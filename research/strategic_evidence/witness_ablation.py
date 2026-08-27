"""Witness-removal design, causal divergence, and role derivation.

This module is research-only.  Economic removals are specifications consumed by
the production-backed runner; component-only removals are explicitly diagnostic
and cannot support return claims.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from itertools import combinations
from typing import Any

from .contract import StrategicEvidenceContract
from .models import canonical_sha256, require_sha256
from .replay import ReplayResult
from .trace import RouteTraceRow

ECONOMIC = "ECONOMIC"
DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
FULL_REMOVAL = "FULL_REMOVAL"
EVIDENCE_REMOVAL = "EVIDENCE_REMOVAL"
TRADABLE_REMOVAL = "TRADABLE_REMOVAL"
BASELINE = "BASELINE"

_TERMINAL_STATUSES = frozenset({"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"})
_ROLE_ORDER = (
    "owner",
    "qualification witness",
    "ghost witness",
    "decisive-pair member",
    "risk anchor",
)
_ROUTE_LAYERS = (
    "reference_context",
    "leaders",
    "risk",
    "opportunity",
    "targets",
    "orders",
    "fills",
)


@dataclass(frozen=True, slots=True)
class AblationSpec:
    """One immutable removal request and its evidence boundary."""

    scope: str
    subject: str
    removed_symbols: tuple[str, ...]
    axis: str
    evidence_class: str

    def __post_init__(self) -> None:
        if not self.scope or not self.subject:
            raise ValueError("witness ablation scope and subject must be non-empty")
        if len(set(self.removed_symbols)) != len(self.removed_symbols):
            raise ValueError("witness ablation removed symbols contain duplicates")
        if tuple(sorted(self.removed_symbols)) != self.removed_symbols:
            raise ValueError("witness ablation removed symbols must be canonical")
        if self.axis not in {BASELINE, FULL_REMOVAL, EVIDENCE_REMOVAL, TRADABLE_REMOVAL}:
            raise ValueError("witness ablation axis differs")
        expected = ECONOMIC if self.axis in {BASELINE, FULL_REMOVAL} else DIAGNOSTIC_ONLY
        if self.evidence_class != expected:
            raise ValueError("witness ablation economic/diagnostic label differs")
        if self.axis == BASELINE and self.removed_symbols:
            raise ValueError("witness ablation baseline cannot remove symbols")
        if self.axis != BASELINE and not self.removed_symbols:
            raise ValueError("witness ablation removal is empty")

    @property
    def cell_id(self) -> str:
        return f"{self.scope}:{self.subject}:{self.axis}"

    def compact(self) -> dict[str, Any]:
        return {**asdict(self), "cell_id": self.cell_id}


@dataclass(frozen=True, slots=True)
class FirstDivergences:
    """Separate earliest route, durable-state, and economic changes."""

    route: Mapping[str, str] | None
    state: Mapping[str, str] | None
    economic: Mapping[str, str] | None
    comparable: bool
    uncompared_reason: str | None

    def compact(self) -> dict[str, Any]:
        return {
            "route": None if self.route is None else dict(self.route),
            "state": None if self.state is None else dict(self.state),
            "economic": None if self.economic is None else dict(self.economic),
            "comparable": self.comparable,
            "uncompared_reason": self.uncompared_reason,
        }


@dataclass(frozen=True, slots=True)
class AblationCell:
    """Compact retained outcome; full routes remain in external shards."""

    spec: AblationSpec
    status: str
    metrics: Mapping[str, Any] | None
    metric_null_reasons: Mapping[str, str]
    final_account_sha256: str | None
    trace_sha256: str | None
    partial_trace_row_count: int
    intervention_provenance: Mapping[str, Any] | None
    error: str | None

    def __post_init__(self) -> None:
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("witness ablation status is not terminal")
        if self.partial_trace_row_count < 0:
            raise ValueError("witness ablation trace row count is negative")
        if self.status != "SUCCESS" and self.metrics is not None:
            raise ValueError("failed witness ablation cell carries metrics")
        if self.spec.evidence_class == DIAGNOSTIC_ONLY and self.metrics is not None:
            raise ValueError("diagnostic witness ablation cell carries economic metrics")

    @property
    def cell_id(self) -> str:
        return self.spec.cell_id

    def compact(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "spec": self.spec.compact(),
            "status": self.status,
            "metrics": None if self.metrics is None else dict(self.metrics),
            "metric_null_reasons": dict(self.metric_null_reasons),
            "final_account_sha256": self.final_account_sha256,
            "trace_sha256": self.trace_sha256,
            "partial_trace_row_count": self.partial_trace_row_count,
            "intervention_provenance": (
                None if self.intervention_provenance is None else dict(self.intervention_provenance)
            ),
            "error": self.error,
        }


def enumerate_initial_specs(contract: StrategicEvidenceContract) -> tuple[AblationSpec, ...]:
    """Return the exact frozen 117-cell initial matrix.

    The 34 canonical names receive all three axes (102 cells).  The five
    preregistered report controls are already covered there, so the report
    supplement is the eight-symbol outer ring of report-13.  Those eight full
    removals, six industry removals, and one baseline produce the other 15
    economic cells: 49 economic and 68 diagnostic-only in total.
    """

    matrix = contract.raw.get("matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("witness ablation matrix contract is missing")
    symbols = tuple(contract.canonical_universe)
    if len(symbols) != 34:
        raise ValueError("witness ablation requires 34 canonical symbols")
    report13 = _string_sequence(matrix.get("report_universe_13"), label="report universe 13")
    report5 = _string_sequence(matrix.get("report_universe_5"), label="report universe 5")
    if len(report13) != 13 or len(report5) != 5 or not set(report5) < set(report13):
        raise ValueError("witness ablation report universes differ from v1")
    report_outer = tuple(symbol for symbol in report13 if symbol not in set(report5))
    if len(report_outer) != 8:
        raise ValueError("witness ablation report outer ring differs from v1")
    industries = _string_sequence(matrix.get("industry_ablations"), label="industry ablations")
    if len(industries) != 6:
        raise ValueError("witness ablation requires six industry removals")

    specs: list[AblationSpec] = [
        AblationSpec(
            scope="BASELINE",
            subject="baseline",
            removed_symbols=(),
            axis=BASELINE,
            evidence_class=ECONOMIC,
        )
    ]
    specs.extend(
        AblationSpec(
            scope="CANONICAL_LEAVE_ONE_OUT",
            subject=symbol,
            removed_symbols=(symbol,),
            axis=axis,
            evidence_class=ECONOMIC if axis == FULL_REMOVAL else DIAGNOSTIC_ONLY,
        )
        for symbol in symbols
        for axis in (FULL_REMOVAL, EVIDENCE_REMOVAL, TRADABLE_REMOVAL)
    )
    specs.extend(
        AblationSpec(
            scope="REPORT_UNIVERSE_LEAVE_ONE_OUT",
            subject=symbol,
            removed_symbols=(symbol,),
            axis=FULL_REMOVAL,
            evidence_class=ECONOMIC,
        )
        for symbol in report_outer
    )
    specs.extend(
        AblationSpec(
            scope="INDUSTRY_REMOVAL",
            subject=industry,
            removed_symbols=(f"industry:{industry}",),
            axis=FULL_REMOVAL,
            evidence_class=ECONOMIC,
        )
        for industry in industries
    )
    result = tuple(specs)
    if (
        len(result) != 117
        or len({spec.cell_id for spec in result}) != 117
        or sum(spec.evidence_class == ECONOMIC for spec in result) != 49
        or sum(spec.evidence_class == DIAGNOSTIC_ONLY for spec in result) != 68
    ):
        raise ValueError("witness ablation initial coverage differs from exact 117/49/68")
    return result


def _string_sequence(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"witness ablation {label} is malformed")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(f"witness ablation {label} contains duplicates")
    return result


def rank_critical_symbols(
    causal_scores: Mapping[str, float],
    *,
    preregistered: Sequence[str],
    limit: int = 8,
) -> tuple[str, ...]:
    """Put preregistered critical names first, then fill by deterministic score."""

    if limit < 1:
        raise ValueError("witness ablation critical ranking limit must be positive")
    critical = tuple(preregistered)
    if len(critical) != len(set(critical)) or any(not symbol for symbol in critical):
        raise ValueError("witness ablation preregistered critical symbols differ")
    if len(critical) > limit:
        raise ValueError("witness ablation critical symbols exceed ranking limit")
    for symbol, score in causal_scores.items():
        if not symbol or isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("witness ablation causal scores are malformed")
    fill = sorted(
        (symbol for symbol in causal_scores if symbol not in set(critical)),
        key=lambda symbol: (-float(causal_scores[symbol]), symbol),
    )
    return (*critical, *fill[: max(0, limit - len(critical))])


def select_bounded_search(
    ranked_symbols: Sequence[str],
    triple_support: Mapping[tuple[str, str, str], bool],
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str, str], ...]]:
    """Return all top-eight pairs and only explicitly data-supported triples."""

    ranked = tuple(ranked_symbols)
    if len(ranked) != 8 or len(set(ranked)) != 8:
        raise ValueError("witness ablation bounded search requires exactly eight symbols")
    rank = {symbol: index for index, symbol in enumerate(ranked)}
    pairs = tuple(combinations(ranked, 2))
    triples: set[tuple[str, str, str]] = set()
    for raw, supported in triple_support.items():
        if not isinstance(raw, tuple) or len(raw) != 3 or len(set(raw)) != 3:
            raise ValueError("witness ablation triple support identity is malformed")
        if supported and set(raw) <= set(ranked):
            ordered = sorted(raw, key=rank.__getitem__)
            triples.add((ordered[0], ordered[1], ordered[2]))
    return pairs, tuple(sorted(triples, key=lambda item: tuple(rank[symbol] for symbol in item)))


def minimal_witness_sets(removals: Iterable[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """Keep inclusion-minimal successful removal sets for bounded delta debugging."""

    canonical = {tuple(sorted(set(removal))) for removal in removals if removal}
    ordered = sorted(canonical, key=lambda item: (len(item), item))
    minimal: list[tuple[str, ...]] = []
    for candidate in ordered:
        values = set(candidate)
        if not any(set(existing) < values for existing in minimal):
            minimal.append(candidate)
    return tuple(minimal)


def derive_first_divergences(
    baseline: Sequence[RouteTraceRow],
    variant: Sequence[RouteTraceRow],
    *,
    status: str,
) -> FirstDivergences:
    """Derive separate causal dates without aligning terminal failure prefixes."""

    if status not in _TERMINAL_STATUSES:
        raise ValueError("witness ablation divergence status is not terminal")
    if status != "SUCCESS":
        return FirstDivergences(
            route=None,
            state=None,
            economic=None,
            comparable=False,
            uncompared_reason=f"{status} traces are retained but not date-aligned",
        )
    left_dates = _validated_dates(baseline)
    right_dates = _validated_dates(variant)
    if left_dates != right_dates:
        raise ValueError("successful witness ablation traces require aligned dates")
    route: dict[str, str] | None = None
    state: dict[str, str] | None = None
    economic: dict[str, str] | None = None
    for left, right in zip(baseline, variant, strict=True):
        left_payload = left.economic_payload()
        right_payload = right.economic_payload()
        if route is None:
            for layer in _ROUTE_LAYERS:
                if left_payload[layer] != right_payload[layer]:
                    route = {"date": left.date, "layer": layer}
                    break
        if state is None and left.account_sha256 != right.account_sha256:
            state = {"date": left.date, "layer": "account"}
        if economic is None and left.equity != right.equity:
            economic = {"date": left.date, "layer": "equity"}
        if route is not None and state is not None and economic is not None:
            break
    return FirstDivergences(
        route=route,
        state=state,
        economic=economic,
        comparable=True,
        uncompared_reason=None,
    )


def _validated_dates(rows: Sequence[RouteTraceRow]) -> tuple[str, ...]:
    values = tuple(row.date for row in rows)
    try:
        parsed = tuple(date.fromisoformat(value) for value in values)
    except ValueError as exc:
        raise ValueError("witness ablation traces require ISO-8601 dates") from exc
    if parsed != tuple(sorted(set(parsed))):
        raise ValueError("witness ablation traces require sorted unique dates")
    return values


def derive_symbol_roles(
    baseline: Sequence[RouteTraceRow],
    single_divergences: Mapping[str, FirstDivergences],
    *,
    decisive_pairs: Iterable[Sequence[str]] = (),
) -> dict[str, tuple[str, ...]]:
    """Derive evidence-supported multi-label roles from successful comparisons."""

    owners: set[str] = set()
    anchors: set[str] = set()
    observed: set[str] = set(single_divergences)
    for row in baseline:
        for collection in (row.targets, row.orders, row.fills):
            for item in collection:
                symbol = item.get("symbol")
                if isinstance(symbol, str) and symbol:
                    owners.add(symbol)
                    observed.add(symbol)
        raw_anchors = row.risk.get("risk_anchor_symbols", ())
        if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes)):
            anchors.update(str(symbol) for symbol in raw_anchors if str(symbol))
        observed.update(anchors)
    pair_members = {
        symbol for pair in decisive_pairs for symbol in pair if isinstance(symbol, str) and symbol
    }
    observed.update(pair_members)
    result: dict[str, tuple[str, ...]] = {}
    for symbol in sorted(observed):
        divergence = single_divergences.get(symbol)
        qualification = bool(
            divergence is not None and divergence.comparable and divergence.route is not None
        )
        ghost = bool(
            qualification
            and symbol not in owners
            and divergence is not None
            and divergence.state is None
            and divergence.economic is None
        )
        supported = {
            "owner": symbol in owners,
            "qualification witness": qualification,
            "ghost witness": ghost,
            "decisive-pair member": symbol in pair_members,
            "risk anchor": symbol in anchors,
        }
        roles = tuple(role for role in _ROLE_ORDER if supported[role])
        if roles:
            result[symbol] = roles
    return result


def cell_from_replay(spec: AblationSpec, result: ReplayResult) -> AblationCell:
    """Retain terminal status, interventions, and any partial trace already produced."""

    if result.status not in _TERMINAL_STATUSES:
        raise ValueError("witness ablation replay status is not terminal")
    trace_sha = (
        None if not result.trace else canonical_sha256({"trace": [asdict(row) for row in result.trace]})
    )
    metrics = dict(result.metrics) if result.status == "SUCCESS" and spec.evidence_class == ECONOMIC else None
    nulls: dict[str, str] = {}
    if metrics is None:
        nulls["all_economic_metrics"] = spec.evidence_class if result.status == "SUCCESS" else result.status
    final_sha: str | None = None
    if result.status == "SUCCESS" and result.trace:
        final_sha = require_sha256(
            result.trace[-1].account_sha256,
            field="witness ablation final account sha256",
        )
    return AblationCell(
        spec=spec,
        status=result.status,
        metrics=metrics,
        metric_null_reasons=nulls,
        final_account_sha256=final_sha,
        trace_sha256=trace_sha,
        partial_trace_row_count=len(result.trace),
        intervention_provenance=(
            None if result.intervention_provenance is None else dict(result.intervention_provenance)
        ),
        error=result.error,
    )


def diagnostic_removal_trace(
    rows: Sequence[RouteTraceRow],
    *,
    removed_symbols: Sequence[str],
    axis: str,
) -> tuple[RouteTraceRow, ...]:
    """Produce an explicitly non-economic component trace for causal inspection."""

    removed = set(removed_symbols)
    if axis == EVIDENCE_REMOVAL:
        return tuple(
            replace(
                row,
                leaders=tuple(item for item in row.leaders if item.get("symbol") not in removed),
            )
            for row in rows
        )
    if axis == TRADABLE_REMOVAL:
        return tuple(
            replace(
                row,
                targets=tuple(item for item in row.targets if item.get("symbol") not in removed),
                orders=tuple(item for item in row.orders if item.get("symbol") not in removed),
                fills=tuple(item for item in row.fills if item.get("symbol") not in removed),
            )
            for row in rows
        )
    raise ValueError("diagnostic witness removal requires a component-only axis")


def ablation_spec_from_compact(value: object) -> AblationSpec:
    if not isinstance(value, Mapping):
        raise ValueError("witness ablation compact spec is malformed")
    raw = dict(value)
    if set(raw) != {
        "scope",
        "subject",
        "removed_symbols",
        "axis",
        "evidence_class",
        "cell_id",
    }:
        raise ValueError("witness ablation compact spec fields differ")
    symbols = raw["removed_symbols"]
    if not isinstance(symbols, list):
        raise ValueError("witness ablation compact removal symbols are malformed")
    spec = AblationSpec(
        scope=str(raw["scope"]),
        subject=str(raw["subject"]),
        removed_symbols=tuple(str(symbol) for symbol in symbols),
        axis=str(raw["axis"]),
        evidence_class=str(raw["evidence_class"]),
    )
    if raw["cell_id"] != spec.cell_id:
        raise ValueError("witness ablation compact cell identity differs")
    return spec


def ablation_cell_from_compact(value: object) -> AblationCell:
    if not isinstance(value, Mapping):
        raise ValueError("witness ablation compact cell is malformed")
    raw = dict(value)
    expected = {
        "cell_id",
        "spec",
        "status",
        "metrics",
        "metric_null_reasons",
        "final_account_sha256",
        "trace_sha256",
        "partial_trace_row_count",
        "intervention_provenance",
        "error",
    }
    if set(raw) != expected:
        raise ValueError("witness ablation compact cell fields differ")
    metrics = raw["metrics"]
    nulls = raw["metric_null_reasons"]
    intervention = raw["intervention_provenance"]
    if (
        (metrics is not None and not isinstance(metrics, Mapping))
        or not isinstance(nulls, Mapping)
        or (intervention is not None and not isinstance(intervention, Mapping))
    ):
        raise ValueError("witness ablation compact cell shape differs")
    count = raw["partial_trace_row_count"]
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("witness ablation compact trace count is malformed")
    cell = AblationCell(
        spec=ablation_spec_from_compact(raw["spec"]),
        status=str(raw["status"]),
        metrics=None if metrics is None else dict(metrics),
        metric_null_reasons={str(key): str(item) for key, item in nulls.items()},
        final_account_sha256=(
            None if raw["final_account_sha256"] is None else str(raw["final_account_sha256"])
        ),
        trace_sha256=None if raw["trace_sha256"] is None else str(raw["trace_sha256"]),
        partial_trace_row_count=count,
        intervention_provenance=None if intervention is None else dict(intervention),
        error=None if raw["error"] is None else str(raw["error"]),
    )
    if raw["cell_id"] != cell.cell_id:
        raise ValueError("witness ablation compact linkage differs")
    return cell


__all__ = (
    "BASELINE",
    "DIAGNOSTIC_ONLY",
    "ECONOMIC",
    "EVIDENCE_REMOVAL",
    "FULL_REMOVAL",
    "TRADABLE_REMOVAL",
    "AblationCell",
    "AblationSpec",
    "FirstDivergences",
    "ablation_cell_from_compact",
    "ablation_spec_from_compact",
    "cell_from_replay",
    "derive_first_divergences",
    "derive_symbol_roles",
    "diagnostic_removal_trace",
    "enumerate_initial_specs",
    "minimal_witness_sets",
    "rank_critical_symbols",
    "select_bounded_search",
)
