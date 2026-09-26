"""Fixed C3 account budgets shared by native economic acceptance paths.

Suite checks do not replace the separately required complete six-family budget,
unchanged absolute gates, event attribution, or cumulative V2/main comparisons.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, cast

CONTRACT_PATH: Final = Path(__file__).resolve().parents[2] / (
    "artifacts/remediation-validation-20260926/industry-v2-research/continuous/CONTRACT_V1.json"
)
CONTRACT_SHA256: Final = "ff0c41218bca9810f97dfd294900417a000acab4e6e6d48cd0619727ad917b3d"
NATIVE_SHA256: Final = "1b7a527012ddd1d464fd4d244029990bf74ed5d87069a97b50424a70af58d765"


def _original_c3_aliases() -> dict[str, dict[str, Any]]:
    """Read the preregistered requests; altered, missing or duplicate input fails."""
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise ValueError("fixed C3 contract is missing or not a regular file")
    raw = CONTRACT_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise ValueError("fixed C3 contract differs from preregistration")
    contract = json.loads(raw)
    aliases: dict[str, dict[str, Any]] = {}
    identifiers: set[str] = set()
    for resource in contract["reference_files"]:
        path = CONTRACT_PATH.parent / resource["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("fixed C3 resource is missing or not a regular file")
        data = path.read_bytes()
        if len(data) != resource["bytes"] or hashlib.sha256(data).hexdigest() != resource["sha256"]:
            raise ValueError("fixed C3 resource identity differs")
        if not resource["path"].startswith("requests-"):
            continue
        for row in json.loads(data):
            request = json.dumps(row["request"], sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False).encode()
            if row["request_id"] != hashlib.sha256(request).hexdigest() or row["request_id"] in identifiers:
                raise ValueError("fixed C3 request identity is invalid or duplicated")
            identifiers.add(row["request_id"])
            for alias in row["aliases"]:
                if alias in aliases:
                    raise ValueError("fixed C3 alias is duplicated")
                aliases[alias] = row
    if len(identifiers) != contract["coverage"]["registered_unique_requests"]:
        raise ValueError("fixed C3 reference coverage differs")
    return aliases


def load_fixed_c3() -> dict[str, dict[str, Any]]:
    """Authenticate and deduplicate the complete frozen native request set."""
    aliases = _original_c3_aliases()
    native_path = CONTRACT_PATH.parent / "C3_NATIVE_REQUESTS.json"
    if native_path.is_symlink() or not native_path.is_file():
        raise ValueError("fixed native C3 requests are missing")
    native_bytes = native_path.read_bytes()
    if hashlib.sha256(native_bytes).hexdigest() != NATIVE_SHA256:
        raise ValueError("fixed native C3 requests differ")
    native = json.loads(native_bytes)
    by_id = {row["request_id"]: row for row in aliases.values()}
    for row in native["economic_requests"]:
        request = json.dumps(row["request"], sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        if row["request_id"] != hashlib.sha256(request).hexdigest():
            raise ValueError("native C3 request identity differs")
        prior = by_id.setdefault(row["request_id"], row)
        if prior["family"] != row["family"] or any(
            abs(prior["metrics"][key] - row["metrics"][key]) > 1e-12
            for key in ("final_wealth_multiple", "max_drawdown")
        ):
            raise ValueError("duplicate native C3 request disagrees")
        for alias in row["aliases"]:
            if alias in aliases and aliases[alias]["request_id"] != row["request_id"]:
                raise ValueError("native C3 alias identifies different requests")
            aliases[alias] = prior
    if len(by_id) != 272 or len(aliases) != 307:
        raise ValueError("complete native C3 request coverage differs")
    return aliases


def historical_crowning_basis(source_cell_id: str) -> dict[str, Any] | None:
    """Use the independent C3 opportunity audit only for its frozen history.

    Candidate qualification cannot narrow this audit. Controlled actual successor
    execution and every non-count historical obligation remain separate gates.
    """
    if source_cell_id != "remove-sz300502":
        return None
    path = CONTRACT_PATH.parent / "C3_OPPORTUNITY_AUDIT.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("independent C3 opportunity audit is missing")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != "7965922219d0eb8d39fd515ebfd1fbeb675390a89c434d0fa6a477427d3bd829":
        raise ValueError("independent C3 opportunity audit differs")
    audit = json.loads(raw)
    observed = next(row for row in audit["observations"] if row["scenario"] == source_cell_id)
    if (observed["observed_sessions"] != 869 or not observed["all_equity_and_metrics_equal"]
            or observed["native_successor_observed_sessions"] != 869
            or observed["legal_confirmed_opportunities"] != 0):
        raise ValueError("independent historical opportunity coverage is incomplete")
    return {"audit_sha256": digest, "source_economic_sha256": audit["source_economic_sha256"],
            "source_cell_id": source_cell_id, "minimum_observed_epochs": 1,
            "minimum_observed_owners": 1, "historical_two_owner_coverage": "INSUFFICIENT",
            "capability": audit["required_controlled_capability"]}


def compare_c3_account(
    alias: str, metrics: Mapping[str, Any], *, reference: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate the fixed cumulative account budget, preserving its real anchor."""
    if alias not in reference:
        raise ValueError(f"account has no fixed C3 reference: {alias}")
    row = reference[alias]
    wealth = metrics.get("final_wealth_multiple", metrics.get("final_wealth"))
    drawdown = metrics.get("max_drawdown")
    if any(isinstance(value, bool) or not isinstance(value, (float, int))
           or not math.isfinite(value) for value in (wealth, drawdown)):
        raise ValueError(f"invalid C3 comparison metrics: {alias}")
    wealth, drawdown = cast(float, wealth), cast(float, drawdown)
    if wealth <= 0 or not 0 <= drawdown <= 1:
        raise ValueError(f"C3 comparison metrics outside valid range: {alias}")
    before = row["metrics"]
    ratio = wealth / before["final_wealth_multiple"]
    delta = drawdown - before["max_drawdown"]
    wealth_delta = wealth - before["final_wealth_multiple"]
    ws = (wealth_delta > 1e-12) - (wealth_delta < -1e-12)
    ds = (delta > 1e-12) - (delta < -1e-12)
    category = (
        "unchanged" if ws == ds == 0 else "improvement" if ws >= 0 and ds <= 0
        else "pure_degradation" if ws <= 0 and ds >= 0 else "controlled_exchange"
    )
    failures = []
    if ratio < .95:
        failures.append(f"{alias}: wealth ratio below fixed C3 0.95 budget")
    if delta > .01:
        failures.append(f"{alias}: drawdown increase above fixed C3 0.01 budget")
    return {
        "alias": alias, "request_id": row["request_id"], "family": row["family"],
        "anchor_raw_sha256": row["raw_sha256"], "anchor_producer": row["producer"],
        "wealth_ratio": ratio, "drawdown_increase": delta, "classification": category,
        "wealth_decreased": ws < 0, "drawdown_increased": ds > 0,
        "failures": failures, "per_account_budget_passed": not failures,
        "final_acceptance": False,
    }


def acceptance_basis() -> dict[str, Any]:
    return {
        "contract_id": "pr92-v2-continuous-20260926-v1",
        "contract_sha256": CONTRACT_SHA256,
        "native_requests_sha256": NATIVE_SHA256,
        "anchor": "fixed C3",
        "per_account_wealth_ratio_min": .95,
        "per_account_drawdown_increase_max": .01,
        "aggregate_required": "All frozen requests, equal weights within and across six families",
        "other_obligations": "Retained absolute gates, causal events, V2 cumulative and main comparison",
    }


def compare_c3_subset(accounts: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the same fixed per-account budget after native evidence validation."""
    reference = load_fixed_c3()
    return [compare_c3_account(alias, metrics, reference=reference)
            for alias, metrics in sorted(accounts.items())]


def _compare_account_exports(
    accounts: list[Mapping[str, Any]], reference: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    rows: dict[str, dict[str, Any]] = {}
    producers: set[str] = set()
    failures: list[str] = []
    for account in accounts:
        alias = account["alias"]
        compared = compare_c3_account(alias, account["metrics"], reference=reference)
        source = account.get("economic_source_sha256")
        raw_sha = account.get("raw_sha256")
        if any(not isinstance(value, str) or len(value) != 64
               or any(c not in "0123456789abcdef" for c in value) for value in (source, raw_sha)):
            raise ValueError("aggregate account lacks source/raw identity")
        producers.add(cast(str, source))
        key = compared["request_id"]
        if key in rows:
            prior = rows[key]
            if any(prior[name] != compared[name] for name in (
                "wealth_ratio", "drawdown_increase", "classification",
            )):
                raise ValueError("duplicate account aliases disagree")
            prior["aliases"].append(alias)
            continue
        compared["aliases"] = [alias]
        compared["raw_sha256"] = raw_sha
        rows[key] = compared
        failures.extend(compared["failures"])
        if compared["classification"] == "improvement" and not account.get("causal_evidence"):
            failures.append(f"{alias}: improvement lacks event evidence")
        if compared["classification"] == "controlled_exchange" and not account.get("preregistered_purpose"):
            failures.append(f"{alias}: exchange lacks preregistered purpose")
    return rows, producers, failures


def evaluate_c3_matrix(accounts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate already validated native account metrics; a subset cannot pass.

    Callers remain responsible for native raw-account and provenance validation.
    This budget result never replaces the independent absolute/engineering gates.
    """
    reference = load_fixed_c3()
    required = {row["request_id"] for row in reference.values()}
    rows, producers, failures = _compare_account_exports(accounts, reference)
    if len(producers) != 1:
        failures.append("aggregate requires one economic producer")
    missing = sorted(required - rows.keys())
    if missing:
        failures.append("aggregate is missing frozen requests")
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows.values():
        families[row["family"]].append(row)
    aggregate = None
    if not missing:
        if set(families) != {"full", "full_removal", "random", "tradable_subset", "short_start", "stress"}:
            raise ValueError("aggregate family membership differs")
        weights = {family: 1 / 6 / len(group) for family, group in families.items()}
        geometric = math.exp(math.fsum(weights[r["family"]] * math.log(r["wealth_ratio"]) for r in rows.values()))
        mean_dd = math.fsum(weights[r["family"]] * r["drawdown_increase"] for r in rows.values())
        degraded = math.fsum(weights[r["family"]] for r in rows.values() if r["classification"] == "pure_degradation")
        aggregate = {"geometric_wealth_ratio": geometric, "mean_drawdown_increase": mean_dd,
                     "pure_degradation_weight": degraded}
        if geometric < .98:
            failures.append("aggregate geometric wealth ratio below 0.98")
        if mean_dd > 1e-12:
            failures.append("aggregate mean drawdown increased")
        if degraded > .2 + 1e-12:
            failures.append("aggregate pure degradation weight exceeds 0.20")
    return {"acceptance_basis": acceptance_basis(), "relative_budget_passed": not failures,
            "final_acceptance": False, "unique_accounts": len(rows), "input_accounts": len(accounts),
            "family_counts": {family: len(group) for family, group in sorted(families.items())},
            "counts": dict(Counter(r["classification"] for r in rows.values())),
            "wealth_decreased": sum(r["wealth_decreased"] for r in rows.values()),
            "drawdown_increased": sum(r["drawdown_increased"] for r in rows.values()),
            "worst_wealth_ratio": min((r["wealth_ratio"] for r in rows.values()), default=None),
            "worst_drawdown_increase": max((r["drawdown_increase"] for r in rows.values()), default=None),
            "missing_requests": missing, "aggregate": aggregate, "failures": failures,
            "accounts": list(rows.values())}
