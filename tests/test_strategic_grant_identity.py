from __future__ import annotations

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
    Target,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _account_with_grant() -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.data_hash = "data"
    account.code_hash = "code:production"
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol="sz300308",
        qualification_signature="qualification:optical",
        qualification_route="persistent_industry",
        qualification_evidence_sha256="a" * 64,
        created_session="2026-01-05",
        previous_grant_id="",
        production_source_identity=account.code_hash,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol="sz300308",
        qualification_signature="qualification:optical",
        qualification_route="persistent_industry",
        qualification_evidence_sha256="a" * 64,
        created_session="2026-01-05",
        last_eligible_session="2026-01-05",
        target_weight=0.05,
        status=StrategicGrantStatus.PENDING_EXECUTION.value,
        account_identity=account.account_identity,
        production_source_identity=account.code_hash,
    )
    return account


def _strategic_target(grant_id: str) -> Target:
    return Target(
        symbol="sz300308",
        weight=0.05,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=0.9,
        confidence=0.95,
        reason="prequalified strategic leader cohort",
        reason_code="strategic_cohort",
        origin_subsystem=OriginSubsystem.STRATEGIC.value,
        mechanism=AttributionMechanism.STRATEGIC_COHORT.value,
        origin_lifecycle=Lifecycle.CORE.value,
        grant_id=grant_id,
    )


def test_grant_identity_flows_from_target_through_fill_and_position() -> None:
    account = _account_with_grant()
    grant = account.strategic_grant
    assert grant is not None
    targets = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-05",
        targets=(_strategic_target(grant.grant_id),),
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={"sz300308": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    reconciled = reconcile_account_orders(
        account=account,
        previous=[],
        current=planned,
        submitted_date="2026-01-05",
    )
    account.pending_orders = list(reconciled)
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    panel = {
        "sz300308": pd.DataFrame(
            {
                "open": [10.0, 10.0],
                "close": [10.0, 10.0],
                "volume": [10_000_000.0, 10_000_000.0],
                "amount": [100_000_000.0, 100_000_000.0],
            },
            index=dates,
        )
    }

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1],
        account=account,
        panel=panel,
    )

    assert len(fills) == 1
    order = account.order_ledger[0]
    tranche = account.positions["sz300308"].tranches[0]
    assert targets[0].grant_id == grant.grant_id
    assert planned[0].grant_id == grant.grant_id
    assert order.grant_id == grant.grant_id
    assert fills[0].grant_id == grant.grant_id
    assert tranche.grant_id == grant.grant_id
    assert account.positions["sz300308"].grant_id == grant.grant_id
    assert fills[0].event_id == order.event_id == tranche.event_id
    assert fills[0].symbol == order.symbol == account.positions["sz300308"].symbol


def test_grant_identity_is_attached_only_to_its_candidate_target() -> None:
    account = _account_with_grant()
    grant = account.strategic_grant
    assert grant is not None
    symbols = ("sz300308", "sz300394", "sz300502")
    targets = PortfolioAllocator(DEFAULT_CONFIG)._targets(
        proposed={symbol: 0.20 for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90, industry="optical") for symbol in symbols},
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="prequalified strategic leader cohort",
        origin_subsystem=OriginSubsystem.STRATEGIC,
        mechanism=AttributionMechanism.STRATEGIC_COHORT,
    )

    by_symbol = {target.symbol: target.grant_id for target in targets}
    assert by_symbol == {
        "sz300308": grant.grant_id,
        "sz300394": "",
        "sz300502": "",
    }


def test_grant_metadata_mismatch_fails_closed() -> None:
    account = _account_with_grant()
    grant = account.strategic_grant
    assert grant is not None
    targets = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-05",
        targets=(_strategic_target(grant.grant_id),),
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={"sz300308": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    reconciled = reconcile_account_orders(
        account=account,
        previous=[],
        current=planned,
        submitted_date="2026-01-05",
    )
    account.pending_orders = list(reconciled)
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.0],
            "close": [10.0, 10.0],
            "volume": [10_000_000.0, 10_000_000.0],
            "amount": [100_000_000.0, 100_000_000.0],
        },
        index=dates,
    )
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1], account=account, panel={"sz300308": frame}
    )
    payload = account.to_dict()
    payload["fills"][0]["grant_id"] = "grant_" + "f" * 64

    with pytest.raises(RuntimeError, match="fill metadata differs from account order: grant_id"):
        account_from_dict(payload)
