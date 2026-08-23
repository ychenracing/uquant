"""Read-only CLI for Independent Risk Sentinel Shadow Mode."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, cast

import pandas as pd

from uquant.account import load_account
from uquant.atomic_io import atomic_write_text, validate_atomic_output_boundary
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.data import DataContractError, DataStore
from uquant.reference_registry import resolve_reference_symbols
from uquant.validation.ai_era import runtime_environment_provenance
from uquant.validation.universe import canonical_sha256, load_ai_universe

from .models import RISK_FAMILIES
from .provenance import sentinel_source_fingerprint as _sentinel_source_fingerprint
from .service import evaluate_sentinel

_BROAD_INDEX: Final = "sh000300"
_TECH_INDEX: Final = "sh000682"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def sentinel_source_fingerprint(repository_root: str | Path) -> str:
    """Hash the exact Sentinel Python path names and bytes."""

    return _sentinel_source_fingerprint(repository_root)


def _repository_commit(root: Path) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required for Sentinel provenance")
    completed = subprocess.run(  # nosec B603
        [executable, "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("cannot resolve Sentinel repository commit")
    return commit


def _same_day_base_risk(
    *,
    as_of: str,
    risk_state: str,
    events: Sequence[Mapping[str, Any]],
    sentinel_families: tuple[str, ...],
) -> dict[str, object]:
    same_day = tuple(event for event in events if str(event.get("date", "")) == as_of)
    searchable = " ".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True).lower() for event in same_day
    )
    keywords = {
        "market_velocity": ("market", "index", "shock", "velocity"),
        "breadth_structure": ("breadth", "ma20", "structure"),
        "covariance_stress": ("correlation", "covariance", "volatility"),
        "leadership_damage": ("leader", "leadership"),
        "live_book_damage": ("holding", "book", "position"),
        "capital_damage": ("capital", "drawdown", "equity"),
    }
    base_families = tuple(
        sorted(
            family
            for family, terms in keywords.items()
            if any(term in searchable for term in terms)
        )
    )
    return {
        "formal_risk_state": risk_state,
        "same_day_base_events": [dict(event) for event in same_day],
        "base_evidence_families": list(base_families),
        "sentinel_evidence_families": list(sentinel_families),
        "sentinel_only_families": sorted(set(sentinel_families) - set(base_families)),
        "base_only_families": sorted(set(base_families) - set(sentinel_families)),
    }


def _markdown(payload: Mapping[str, Any]) -> str:
    assessment = cast(Mapping[str, Any], payload["assessment"])
    provenance = cast(Mapping[str, Any], payload["provenance"])
    comparison = cast(Mapping[str, Any], payload["base_risk_comparison"])
    coverage = cast(Mapping[str, Any], assessment["coverage"])
    families = cast(list[object], assessment["evidence_families"])
    return "\n".join(
        (
            "# Independent Risk Sentinel Shadow Report",
            "",
            f"- Date: {assessment['date']}",
            f"- Opinion: {assessment['level']}",
            f"- Confidence: {float(assessment['confidence']):.6f}",
            f"- Coverage: {coverage['status']} ({float(coverage['confidence']):.6f})",
            f"- Evidence families: {', '.join(str(item) for item in families) or 'none'}",
            f"- Formal uquant risk state: {comparison['formal_risk_state']}",
            "- Economic effect: none (observation only)",
            "",
            "## Provenance",
            "",
            f"- Repository commit: {provenance['repository_commit']}",
            f"- Sentinel source SHA-256: {provenance['sentinel_source_sha256']}",
            f"- Account SHA-256: {provenance['account_sha256']}",
            f"- Config SHA-256: {provenance['config_sha256']}",
            f"- Universe SHA-256: {provenance['universe_sha256']}",
            f"- Artifact SHA-256: {payload['canonical_sha256']}",
            "",
        )
    )


def _load_panel(
    store: DataStore,
    *,
    symbols: tuple[str, ...],
    as_of: str,
) -> tuple[dict[str, pd.DataFrame], tuple[str, ...]]:
    panel: dict[str, pd.DataFrame] = {}
    loaded: list[str] = []
    for symbol in symbols:
        try:
            panel[symbol] = store.load(symbol, as_of=as_of)
        except DataContractError:
            continue
        loaded.append(symbol)
    return panel, tuple(loaded)


def run_shadow(
    *,
    data_dir: str | Path,
    as_of: str,
    account_path: str | Path,
    output_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate and atomically publish an account-read-only Shadow artifact."""

    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    data_root = Path(data_dir)
    account_source = Path(account_path)
    output = Path(output_path)
    markdown = output.with_suffix(".md")
    latest = output.parent / "latest_success.json"
    identities = {item.resolve(strict=False) for item in (output, markdown, latest)}
    if len(identities) != 3:
        raise ValueError("Shadow output identities must be distinct")
    protected: tuple[Path, ...] = ()
    for destination in (output, markdown, latest):
        protected = validate_atomic_output_boundary(
            destination,
            protected_paths=(account_source,),
            protected_roots=(data_root,),
        )

    account_bytes = account_source.read_bytes()
    account = load_account(account_source)
    universe = load_ai_universe()
    expected = universe.symbols_as_of(as_of)
    registered = resolve_reference_symbols(as_of)
    if registered != expected:
        raise RuntimeError("point-in-time reference registry differs from canonical universe")
    industries = {symbol: universe.industry_of(symbol, as_of) for symbol in expected}
    store = DataStore(data_root)
    broad = store.load(_BROAD_INDEX, as_of=as_of)
    tech = store.load(_TECH_INDEX, as_of=as_of)
    if (
        broad.index[-1].normalize() != pd.Timestamp(as_of).normalize()
        or tech.index[-1].normalize() != pd.Timestamp(as_of).normalize()
    ):
        raise RuntimeError("date is not a contracted frozen-data session")
    panel, loaded = _load_panel(store, symbols=expected, as_of=as_of)
    held = tuple(sorted(account.positions))
    observed_equity = float(account.cash)
    missing_position_price = False
    for symbol, position in account.positions.items():
        frame = panel.get(symbol)
        if frame is None or frame.index[-1].normalize() != pd.Timestamp(as_of).normalize():
            missing_position_price = True
            break
        observed_equity += float(position.shares) * float(frame["close"].iloc[-1])
    capital_drawdown = (
        None
        if missing_position_price
        else max(
            0.0,
            1.0 - observed_equity / max(float(account.capital_peak), 1e-12),
        )
    )
    assessment = evaluate_sentinel(
        as_of=as_of,
        broad_frame=broad,
        tech_frame=tech,
        reference_panel=panel,
        point_in_time_industries=industries,
        held_symbols=held,
        leader_symbols=tuple(sorted(account.active_leaders)),
        capital_drawdown=capital_drawdown,
    )
    manifest = store.manifest(
        (_BROAD_INDEX, _TECH_INDEX, *loaded),
        as_of=as_of,
    )
    base_events = tuple(
        event for event in account.risk_events if isinstance(event, Mapping)
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_id": "risk-sentinel-shadow-v1",
        "assessment": assessment.to_dict(),
        "base_risk_comparison": _same_day_base_risk(
            as_of=as_of,
            risk_state=account.risk,
            events=base_events,
            sentinel_families=assessment.evidence_families,
        ),
        "observation_contract": {
            "account_read_only": True,
            "economic_effect": False,
            "family_coverage": sorted(RISK_FAMILIES),
        },
        "provenance": {
            "account_sha256": hashlib.sha256(account_bytes).hexdigest(),
            "config_sha256": config_fingerprint(DEFAULT_CONFIG),
            "data": {
                "adjustment": manifest.adjustment,
                "digest": manifest.digest,
                "end": manifest.end,
                "files": manifest.files,
                "source": manifest.source,
                "start": manifest.start,
                "symbols": list(manifest.symbols),
            },
            "repository_commit": _repository_commit(root),
            "runtime": runtime_environment_provenance(root),
            "sentinel_source_sha256": sentinel_source_fingerprint(root),
            "universe_sha256": universe.sha256,
        },
    }
    payload["canonical_sha256"] = canonical_sha256(payload)
    if account_source.read_bytes() != account_bytes:
        raise RuntimeError("Shadow evaluation observed an account mutation")
    atomic_write_text(output, _canonical_json(payload), protected_paths=protected)
    atomic_write_text(markdown, _markdown(payload), protected_paths=protected)
    atomic_write_text(
        latest,
        _canonical_json(
            {
                "artifact": output.name,
                "canonical_sha256": payload["canonical_sha256"],
                "date": as_of,
            }
        ),
        protected_paths=protected,
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent Risk Sentinel Shadow Mode")
    parser.add_argument("--validate-contracts", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--account", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one read-only assessment or validate static contracts."""

    parser = _parser()
    args = parser.parse_args(argv)
    if args.validate_contracts:
        from .validation import validate_contracts

        if any(value is not None for value in (args.data_dir, args.date, args.account, args.output)):
            parser.error("--validate-contracts does not accept assessment arguments")
        print(_canonical_json(validate_contracts()), end="")
        return 0
    if any(value is None for value in (args.data_dir, args.date, args.account, args.output)):
        parser.error("--data-dir, --date, --account, and --output are required")
    payload = run_shadow(
        data_dir=args.data_dir,
        as_of=args.date,
        account_path=args.account,
        output_path=args.output,
    )
    print(payload["canonical_sha256"])
    return 0
