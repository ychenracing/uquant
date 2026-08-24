"""Research-owned Future Holdout CLI and risk-differential observation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from typing import Any

from research.risk_differential import (
    BOOLEAN_AXES,
    append_observation,
    classify_boolean_axis,
    classify_normalized_scalar,
)
from research.risk_differential_models import (
    canonical_bytes,
    canonical_sha256,
    validate_registry_checkout,
)
from research.risk_replay_runtime import ReplayCell, run_trade_cell, run_uquant_cell
from uquant.validation.execution_journal import (
    append_filled,
    append_planned,
    append_skipped,
    execution_journal_checkpoint,
    read_execution_journal,
    record_to_dict,
)
from uquant.validation.holdout import holdout_data_identity, load_future_holdout_contract
from uquant.validation.holdout.cli_operations import (
    CANONICAL_DIFFERENTIAL_JOURNAL_PATH,
    CANONICAL_JOURNAL_CHECKPOINT_PATH,
    CANONICAL_JOURNAL_PATH,
    CANONICAL_LOCAL_LANE_REPORT_PATH,
    read_trusted_execution_journal,
    render_execution_journal,
    write_journal_checkpoint,
)
from uquant.validation.holdout.cli_operations import (
    build_local_lane_report as build_local_lane_report,
)
from uquant.validation.holdout.cli_operations import (
    load_journal_checkpoint as load_journal_checkpoint,
)
from uquant.validation.holdout.cli_operations import (
    summarize_execution_journal as summarize_execution_journal,
)
from uquant.validation.holdout.cli_operations import (
    validate_static_lanes as _validate_static_lanes,
)
from uquant.validation.holdout.cli_operations import (
    write_local_lane_report as _write_local_lane_report,
)
from uquant.validation.holdout_lanes import load_lane_registry

type _ReplayCellRunner = Callable[..., dict[str, Any]]
future_holdout_trade_replay: _ReplayCellRunner = run_trade_cell
future_holdout_uquant_replay: _ReplayCellRunner = run_uquant_cell

_DIFFERENTIAL_AXIS_FIELDS = (
    "trade_only_axes",
    "sentinel_only_axes",
    "base_only_axes",
    "all_agree_axes",
    "trade_and_sentinel_not_base_axes",
)


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/future_holdout.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate-lanes", "validate-static-lanes"):
        lanes = sub.add_parser(command)
        lanes.add_argument("--repository-root", default=".")
        lanes.add_argument("--registry", default="benchmarks/future_holdout_lane_registry.json")
        lanes.add_argument("--evidence", default="artifacts/holdout/lane_validation.json")
    local = sub.add_parser("report-lanes")
    local.add_argument("--repository-root", default=".")
    local.add_argument("--registry", default="benchmarks/future_holdout_lane_registry.json")
    local.add_argument("--output", default=CANONICAL_LOCAL_LANE_REPORT_PATH)
    differential = sub.add_parser("append-risk-differential")
    differential.add_argument("--repository-root", default=".")
    differential.add_argument("--trade-root", required=True)
    differential.add_argument("--date", required=True)
    differential.add_argument("--journal", default=CANONICAL_DIFFERENTIAL_JOURNAL_PATH)

    journal = sub.add_parser("journal")
    journal_sub = journal.add_subparsers(dest="journal_action", required=True)
    planned = journal_sub.add_parser("planned")
    planned.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    planned.add_argument("--plan-id", required=True)
    planned.add_argument("--decision-date", required=True)
    planned.add_argument("--recorded-at", required=True)
    planned.add_argument("--symbol", required=True)
    planned.add_argument("--side", choices=("BUY", "SELL"), required=True)
    planned.add_argument("--planned-weight", type=float, required=True)
    planned.add_argument("--planned-price", type=float, required=True)
    planned.add_argument("--planned-shares", type=int, required=True)
    filled = journal_sub.add_parser("filled")
    filled.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    filled.add_argument("--plan-id", required=True)
    filled.add_argument("--recorded-at", required=True)
    filled.add_argument("--next-open", type=float, required=True)
    filled.add_argument("--actual-time", required=True)
    filled.add_argument("--actual-price", type=float, required=True)
    filled.add_argument("--actual-shares", type=int, required=True)
    filled.add_argument("--broker-order-id", required=True)
    skipped = journal_sub.add_parser("skipped")
    skipped.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    skipped.add_argument("--plan-id", required=True)
    skipped.add_argument("--recorded-at", required=True)
    skipped.add_argument("--next-open", type=float, required=True)
    skipped.add_argument("--manual-skip", required=True)
    report = journal_sub.add_parser("report")
    report.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    checkpoint = journal_sub.add_parser("checkpoint")
    checkpoint.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    checkpoint.add_argument("--output", default=CANONICAL_JOURNAL_CHECKPOINT_PATH)
    verify = journal_sub.add_parser("verify")
    verify.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    verify.add_argument("--checkpoint", default=CANONICAL_JOURNAL_CHECKPOINT_PATH)
    return parser


def _append_risk_differential(args: argparse.Namespace) -> dict[str, Any]:
    """Append one source-bound observation without touching production state."""

    root = Path(args.repository_root).resolve()
    identity = json.loads(
        (root / "benchmarks/risk_differential_holdout_identity.json").read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if identity.get("payload_sha256") != canonical_sha256(identity):
        raise ValueError("risk differential holdout identity is not sealed")
    source_registry = json.loads(
        (root / "benchmarks/risk_differential_source_registry.json").read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    validate_registry_checkout(Path(args.trade_root), source_registry["trade"])
    lane = next(
        item
        for item in load_lane_registry(root / "benchmarks/future_holdout_lane_registry.json")
        if item.lane_id == "risk_differential_shadow"
    )
    if lane.sentinel_source_sha256 != identity["payload_sha256"]:
        raise ValueError("risk differential lane source identity changed")
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    observed_sessions, data_sha256 = holdout_data_identity(root / contract.data_directory)
    _validate_differential_session(
        args.date,
        activation=lane.activation_session,
        reviewed_sessions=contract.review_sessions,
        observed_sessions=observed_sessions,
    )
    raw, replay_envelope = _compute_risk_differential_payload(
        root=root,
        trade_root=Path(args.trade_root),
        data_directory=root / contract.data_directory,
        date=args.date,
        data_sha256=data_sha256,
        source_registry=source_registry,
    )
    required = {
        *_DIFFERENTIAL_AXIS_FIELDS,
        "trade_risk_level",
        "base_risk_level",
        "sentinel_risk_level",
        "trade_block_new_entries",
        "base_freeze_new_risk",
        "sentinel_freeze_authorized",
        "actionable_buy_intents",
        "actionable_pyramid_intents",
    }
    differential_contract = json.loads(
        (root / "benchmarks/risk_differential_contract.json").read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    _validate_risk_differential_payload(
        raw,
        required=required,
        allowed_axes=frozenset(differential_contract["axes"]),
    )
    journal = Path(args.journal)
    if not journal.is_absolute():
        journal = root / journal
    prior = _read_risk_differential_journal(journal)
    for item in prior:
        try:
            prior_payload = {key: item[key] for key in required}
        except KeyError as exc:
            raise ValueError("risk differential observation journal payload is malformed") from exc
        _validate_risk_differential_payload(
            prior_payload,
            required=required,
            allowed_axes=frozenset(differential_contract["axes"]),
        )
        _validate_differential_session(
            str(item.get("date", "")),
            activation=lane.activation_session,
            reviewed_sessions=contract.review_sessions,
            observed_sessions=observed_sessions,
        )
        _validate_prior_differential_source_identity(
            item,
            lane_id=lane.lane_id,
            lane_identity_sha256=identity["payload_sha256"],
            trade_commit=source_registry["trade"]["commit"],
            trade_source_sha256=source_registry["trade"]["python_source_sha256"],
        )
    observation_payload = {"date": args.date, **raw}
    formal_scores = _differential_formal_scores([*prior, observation_payload])
    record = {
        **observation_payload,
        "lane_id": lane.lane_id,
        "lane_identity_sha256": identity["payload_sha256"],
        "trade_source_commit": source_registry["trade"]["commit"],
        "trade_source_sha256": source_registry["trade"]["python_source_sha256"],
        "holdout_data_sha256": data_sha256,
        "replay_envelope": replay_envelope,
        "parameter_changes_from_observation": False,
        "production_authority_changes_from_observation": False,
        "formal_scores": formal_scores,
        "review_status": "NON_REVIEWABLE" if formal_scores is None else "REVIEWABLE",
    }
    return append_observation(journal, record, activation=lane.activation_session)


def _validate_prior_differential_source_identity(
    item: dict[str, Any],
    *,
    lane_id: str,
    lane_identity_sha256: str,
    trade_commit: str,
    trade_source_sha256: str,
) -> None:
    """Reject any source or lane identity drift after the first observation."""

    if (
        item.get("lane_id") != lane_id
        or item.get("lane_identity_sha256") != lane_identity_sha256
        or item.get("trade_source_commit") != trade_commit
        or item.get("trade_source_sha256") != trade_source_sha256
    ):
        raise ValueError("risk differential observation journal source identity changed")


def _compute_risk_differential_payload(
    *,
    root: Path,
    trade_root: Path,
    data_directory: Path,
    date: str,
    data_sha256: str,
    source_registry: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run both locked engines and derive, never accept, one holdout observation."""

    differential_contract = json.loads(
        (root / "benchmarks/risk_differential_contract.json").read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    symbols = tuple(differential_contract["official_pools"]["e"])
    cell = ReplayCell(
        cell_id=f"future_holdout/{date}/e",
        axis="future_holdout",
        window="risk_differential_shadow",
        universe="e",
        family="official",
        symbols=symbols,
        start="2023-01-03",
        end=date,
    )
    uquant = future_holdout_uquant_replay(cell, data_directory)
    trade = future_holdout_trade_replay(cell, trade_root, data_directory)
    if uquant["dates"] != trade["dates"] or not uquant["dates"] or uquant["dates"][-1] != date:
        raise ValueError("holdout replay does not end on the requested source-bound session")
    trade_row = trade["trade"][-1]
    base_row = uquant["base"][-1]
    sentinel_row = uquant["sentinel"][-1]
    classified: dict[str, list[str]] = {
        "trade_only_axes": [],
        "sentinel_only_axes": [],
        "base_only_axes": [],
        "all_agree_axes": [],
        "trade_and_sentinel_not_base_axes": [],
    }
    field_for_class = {
        "TRADE_ONLY": "trade_only_axes",
        "SENTINEL_ONLY": "sentinel_only_axes",
        "BASE_ONLY": "base_only_axes",
        "AGREE_ALL": "all_agree_axes",
        "TRADE_AND_SENTINEL_NOT_BASE": "trade_and_sentinel_not_base_axes",
    }
    allowed_axes = frozenset(differential_contract["axes"])
    for axis in sorted(set(BOOLEAN_AXES) & allowed_axes):
        classification = classify_boolean_axis(
            trade=trade_row[axis],
            base=base_row[axis],
            sentinel=sentinel_row[axis],
        )
        destination = field_for_class.get(classification)
        if destination is not None:
            classified[destination].append(axis)
    gross_class = classify_normalized_scalar(
        trade=trade_row["recommended_gross_cap"],
        base=base_row["recommended_gross_cap"],
        sentinel=sentinel_row["recommended_gross_cap"],
        higher_is_riskier=False,
    )
    gross_destination = field_for_class.get(gross_class)
    if gross_destination is not None:
        classified[gross_destination].append("recommended_gross_cap")
    actionability = uquant["actionability"].get(date)
    if actionability is None:
        raise ValueError("uquant holdout replay has no actionability for requested session")
    raw = {
        **{key: sorted(value) for key, value in classified.items()},
        "trade_risk_level": int(trade_row["severity_rank"]),
        "base_risk_level": int(base_row["severity_rank"]),
        "sentinel_risk_level": int(sentinel_row["severity_rank"]),
        "trade_block_new_entries": bool(trade_row["block_new_entries"]),
        "base_freeze_new_risk": bool(base_row["block_new_entries"]),
        "sentinel_freeze_authorized": bool(sentinel_row["block_new_entries"]),
        "actionable_buy_intents": int(actionability["buy"]),
        "actionable_pyramid_intents": int(actionability["pyramid"]),
    }
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "cell_id": cell.cell_id,
        "date": date,
        "data_sha256": data_sha256,
        "trade_commit": source_registry["trade"]["commit"],
        "trade_python_source_sha256": source_registry["trade"]["python_source_sha256"],
        "uquant_decision_digest_sha256": uquant["decision_digest_sha256"],
        "derived_payload_sha256": canonical_sha256(raw),
    }
    envelope["payload_sha256"] = canonical_sha256(envelope)
    return raw, envelope


def _validate_differential_session(
    date: str,
    *,
    activation: str,
    reviewed_sessions: tuple[str, ...],
    observed_sessions: tuple[str, ...],
) -> None:
    if date < activation:
        raise ValueError("risk differential date predates immutable activation")
    if date not in reviewed_sessions:
        raise ValueError("risk differential date is not a reviewed market session")
    if date not in observed_sessions:
        raise ValueError("risk differential date has no source-bound holdout data")


def _validate_risk_differential_payload(
    raw: object,
    *,
    required: set[str],
    allowed_axes: frozenset[str],
) -> None:
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("risk differential observation payload schema is malformed")
    observed_axes: set[str] = set()
    for field in _DIFFERENTIAL_AXIS_FIELDS:
        value = raw[field]
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
            or value != sorted(set(value))
            or not set(value) <= allowed_axes
            or observed_axes.intersection(value)
        ):
            raise ValueError("risk differential axes must be disjoint sorted contract-axis lists")
        observed_axes.update(value)
    for field in ("trade_risk_level", "base_risk_level", "sentinel_risk_level"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
            raise ValueError("risk differential levels must be integer ranks in [0, 3]")
    for field in (
        "trade_block_new_entries",
        "base_freeze_new_risk",
        "sentinel_freeze_authorized",
    ):
        if not isinstance(raw[field], bool):
            raise ValueError("risk differential authority fields must be booleans")
    for field in ("actionable_buy_intents", "actionable_pyramid_intents"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("risk differential actionability counts must be nonnegative integers")


def _read_risk_differential_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [
        json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        for line in path.read_text().splitlines()
        if line
    ]
    previous = "0" * 64
    for row in rows:
        record_sha256 = row.get("record_sha256")
        if row.get("previous_sha256") != previous or not isinstance(record_sha256, str):
            raise ValueError("risk differential observation journal chain is invalid")
        unsealed = {key: value for key, value in row.items() if key != "record_sha256"}
        if hashlib.sha256(canonical_bytes(unsealed)).hexdigest() != record_sha256:
            raise ValueError("risk differential observation journal record seal is invalid")
        previous = record_sha256
    return rows


def _differential_formal_scores(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    sessions = len(rows)
    if sessions < 20:
        return None
    milestone = max(item for item in (20, 40, 60) if item <= min(sessions, 60))
    return {
        "observed_sessions": sessions,
        "review_milestone": milestone,
        "trade_only_axis_observations": sum(len(item["trade_only_axes"]) for item in rows),
        "actionable_buy_intents": sum(int(item["actionable_buy_intents"]) for item in rows),
        "actionable_pyramid_intents": sum(int(item["actionable_pyramid_intents"]) for item in rows),
        "trade_risk_session_rate": sum(int(item["trade_risk_level"]) > 0 for item in rows) / sessions,
        "base_risk_session_rate": sum(int(item["base_risk_level"]) > 0 for item in rows) / sessions,
        "sentinel_risk_session_rate": sum(int(item["sentinel_risk_level"]) > 0 for item in rows) / sessions,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"validate-lanes", "validate-static-lanes"}:
        print(json.dumps(_validate_static_lanes(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "report-lanes":
        print(json.dumps(_write_local_lane_report(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "append-risk-differential":
        print(json.dumps(_append_risk_differential(args), ensure_ascii=False, sort_keys=True))
        return 0
    if args.journal_action == "planned":
        record = append_planned(
            args.journal,
            plan_id=args.plan_id,
            decision_date=args.decision_date,
            recorded_at=args.recorded_at,
            symbol=args.symbol,
            side=args.side,
            planned_weight=args.planned_weight,
            planned_price=args.planned_price,
            planned_shares=args.planned_shares,
        )
    elif args.journal_action == "filled":
        record = append_filled(
            args.journal,
            plan_id=args.plan_id,
            recorded_at=args.recorded_at,
            next_open=args.next_open,
            actual_time=args.actual_time,
            actual_price=args.actual_price,
            actual_shares=args.actual_shares,
            broker_order_id=args.broker_order_id,
        )
    elif args.journal_action == "skipped":
        record = append_skipped(
            args.journal,
            plan_id=args.plan_id,
            recorded_at=args.recorded_at,
            next_open=args.next_open,
            manual_skip=args.manual_skip,
        )
    elif args.journal_action == "checkpoint":
        rendered = json.dumps(asdict(write_journal_checkpoint(args.journal, args.output)), sort_keys=True)
        print(rendered)
        return 0
    elif args.journal_action == "verify":
        records = read_trusted_execution_journal(args.journal, args.checkpoint)
        current = execution_journal_checkpoint(records)
        print(
            json.dumps(
                {
                    "checkpoint": asdict(current),
                    "records": len(records),
                    "status": "VALID",
                },
                sort_keys=True,
            )
        )
        return 0
    else:
        print(render_execution_journal(read_execution_journal(args.journal)))
        return 0
    print(json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True))
    return 0


_CLI_SEAM_LOCK = RLock()


@contextmanager
def future_holdout_cli_seams(
    *,
    trade_replay: _ReplayCellRunner,
    uquant_replay: _ReplayCellRunner,
) -> Iterator[None]:
    """Install the two frozen replay seams for one bounded CLI call."""

    global future_holdout_trade_replay, future_holdout_uquant_replay
    with _CLI_SEAM_LOCK:
        originals = (future_holdout_trade_replay, future_holdout_uquant_replay)
        future_holdout_trade_replay = trade_replay
        future_holdout_uquant_replay = uquant_replay
        try:
            yield
        finally:
            future_holdout_trade_replay, future_holdout_uquant_replay = originals


compute_risk_differential_payload = _compute_risk_differential_payload
differential_formal_scores = _differential_formal_scores
future_holdout_parser = _parser
validate_differential_session = _validate_differential_session
validate_prior_differential_source_identity = _validate_prior_differential_source_identity
validate_risk_differential_payload = _validate_risk_differential_payload


if __name__ == "__main__":
    raise SystemExit(main())
