"""Build the fixed historical cohort from saved, source-verified evidence only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.historical_prices import parse_prices
from uquant.contracts.universe import research_cohort_input
from uquant.validation.manifest import verify_data_manifest

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def build(source_root: Path, cohort_path: Path, cohort_sha: str, output: Path) -> dict[str, Any]:
    frozen = (ROOT / "data/frozen").resolve()
    target = output.resolve()
    if target == frozen or frozen in target.parents or target.exists():
        raise ValueError("research output must be new and separate from frozen data")
    parent = verify_data_manifest(frozen)
    with research_cohort_input(cohort_path, expected_sha256=cohort_sha) as cohort:
        symbols = set(cohort.symbols)
    if len(symbols) != 23:
        raise ValueError("registered historical frame must contain exactly23 supported members")
    cohort_doc = load(cohort_path)
    ledger = load(source_root / "business_ledger.json")
    original = {row["symbol"]: row for row in ledger["rows"] if row["status"] == "supported"}
    if set(original) != symbols or len(ledger["rows"]) != 90:
        raise ValueError("saved original business frame differs from preregistered cohort")
    business = {row["symbol"]: row for row in load(source_root / "business_sources.json")}
    for member in cohort_doc["members"]:
        source = original[member["symbol"]]
        if (source["source_sha256"] != member["source_sha256"]
                or source["candidate_industry"] != member["industry"]
                or source["disclosed_date"] != member["source_disclosed_date"]):
            raise ValueError("cohort business evidence differs from original source review")
        raw = source_root / "business_raw" / Path(business[member["symbol"]]["raw_path"]).name
        if digest(raw) != member["source_sha256"]:
            raise ValueError("original business report hash mismatch")
    price_sources = load(source_root / "prices_full/manifest.json")
    if {row["symbol"] for row in price_sources} != symbols:
        raise ValueError("raw price source frame differs from cohort")
    events = load(source_root / "action_coefficients_verified.json")["events"]
    if len(events) != 109:
        raise ValueError("registered109 original adjustment events are required")
    for event in events:
        pdf = source_root / "corporate_actions" / f"{event['symbol']}_{event['id']}.pdf"
        if (digest(pdf) != event["source_sha256"]
                or event["status"] != "SOURCE_COEFFICIENT_MATCHES_QUOTE_TO_CENT"
                or not event["disclosed"] < event["ex_date"] <= "2026-08-05"):
            raise ValueError("original adjustment evidence failed identity or timing")
    target.mkdir(parents=True)
    audit = []
    for source in sorted(price_sources, key=lambda row: row["symbol"]):
        symbol = source["symbol"]
        if source["status"] != "bounded_rows_verified":
            raise ValueError("raw price source is not verified")
        matches = [path for path in sorted((source_root / "prices_full").glob(symbol + "*.bin"))
                   if digest(path) == source["sha256"]]
        if not matches:
            raise ValueError("original bounded raw price response is unavailable")
        rows = parse_prices(matches[0].read_bytes(), symbol, "2021-01-01", "2026-08-05")
        if rows != load(source_root / "prices_full" / f"{symbol}.json"):
            raise ValueError("normalized prices differ from original response")
        actions = sorted((event for event in events if event["symbol"] == symbol),
                         key=lambda event: event["ex_date"])
        adjusted = []
        for row in rows:
            if row["volume"] and not row["low"] - .01 <= row["amount"] / row["volume"] <= row["high"] + .01:
                raise ValueError("raw volume/amount units fail daily VWAP check")
            values = {key: Decimal(str(row[key])) for key in ("open", "high", "low", "close")}
            for event in actions:
                if event["ex_date"] <= row["date"]:
                    continue
                cash = Decimal(event["cash_adjustment_per_share"])
                split = Decimal(event["share_change_ratio"])
                if "ratio_denominator" in event:
                    cash = Decimal(event["cash_ratio_numerator"]) / Decimal(event["ratio_denominator"])
                    split = Decimal(event["share_ratio_numerator"]) / Decimal(event["ratio_denominator"])
                values = {key: (value - cash) / (1 + split) for key, value in values.items()}
            if min(values.values()) <= 0:
                raise ValueError("non-positive adjusted history requires source review")
            adjusted.append({
                "date": row["date"],
                **{key: format(value.quantize(Decimal(".000001")), "f") for key, value in values.items()},
                "volume": row["volume"], "amount": row["amount"],
            })
        with (target / f"{symbol}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["date", "open", "high", "low", "close", "volume", "amount"],
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(adjusted)
        audit.append({"symbol": symbol, "rows": len(rows), "raw_sha256": source["sha256"],
                      "first": rows[0]["date"], "last": rows[-1]["date"]})
    for symbol in ("sh000300", "sh000682"):
        shutil.copy2(frozen / f"{symbol}.csv", target / f"{symbol}.csv")
    results = [{"symbol": path.stem, "sha256": digest(path)} for path in sorted(target.glob("*.csv"))]
    manifest = {
        "snapshot_id": "uquant-research-historical-cohort-v1-20260909",
        "research_only": True, "production_ready": False,
        "parent_frozen_identity": parent, "cohort_manifest_sha256": cohort_sha,
        "scope": "Fixed23-member cohort; same fixed-end affine diagnostic convention, not causal raw-share corporate-action accounting.",
        "source_action_coefficients_sha256": digest(source_root / "action_coefficients_verified.json"),
        "source_business_ledger_sha256": digest(source_root / "business_ledger.json"),
        "results": results, "source_rows": audit,
    }
    (target / "DATA_MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    (target / "SHA256SUMS").write_text("".join(
        row["sha256"] + "  " + row["symbol"] + ".csv\n" for row in results
    ))
    identity = verify_data_manifest(target)
    if verify_data_manifest(frozen) != parent:
        raise RuntimeError("frozen parent changed while building research input")
    return {"identity": identity, "cohort_sha256": cohort_sha, "source_rows": audit,
            "production_ready": False, "builder_sha256": digest(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--cohort-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build(args.source_root, args.cohort, args.cohort_sha256, args.output)
    (args.output / "BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
