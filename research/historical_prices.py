"""Bounded, unadjusted source audit; never a production data replacement."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from research.https_input import read_https


def request_url(symbol: str, start: str, end: str) -> str:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or last >= date(2026, 8, 6):
        raise ValueError("invalid or protected date range")
    if not re.fullmatch(r"(?:sh|sz)[0-9]{6}", symbol):
        raise ValueError("invalid symbol")
    params = {"code": "cn_" + symbol[2:], "start": first.strftime("%Y%m%d"),
              "end": last.strftime("%Y%m%d"), "stat": "1", "order": "D", "period": "d",
              "callback": "historySearchHandler", "rt": "jsonp"}
    return "https://q.stock.sohu.com/hisHq?" + urlencode(params)


def parse_prices(raw: bytes, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    request_url(symbol, start, end)
    text = raw.decode("gb18030").strip().removesuffix(";")
    callback = "historySearchHandler("
    if text.startswith(callback) and text.endswith(")"):
        text = text[len(callback):-1]
    data = json.loads(text)
    if not isinstance(data, list) or len(data) != 1 or data[0].get("status") != 0:
        raise ValueError("unavailable source response")
    if data[0].get("code") != "cn_" + symbol[2:]:
        raise ValueError("response symbol mismatch")
    source_rows = data[0]["hq"]
    if len(source_rows) > 1 and source_rows[0][0] > source_rows[-1][0]:
        source_rows = list(reversed(source_rows))
    rows: list[dict[str, Any]] = []
    previous = ""
    for row in source_rows:
        if len(row) not in (10, 11):
            raise ValueError("unknown source schema")
        day = date.fromisoformat(row[0]).isoformat()
        if not start <= day <= end or day <= previous:
            raise ValueError("response date outside range or not strictly ordered")
        opening, closing, change, low, high, lots, amount = (
            float(row[i]) for i in (1, 2, 3, 5, 6, 7, 8)
        )
        if not all(math.isfinite(v) for v in (opening, closing, change, low, high, lots, amount)):
            raise ValueError("nonfinite source value")
        if not 0 < low <= min(opening, closing) <= max(opening, closing) <= high:
            raise ValueError("invalid price geometry")
        if lots < 0 or amount < 0 or (lots == 0) != (amount == 0):
            raise ValueError("invalid reported volume or amount")
        rows.append({"date": day, "open": opening, "close": closing, "low": low,
                     "high": high, "volume": float(Decimal(row[7]) * 100),
                     "amount": float(Decimal(row[8]) * 10000),
                     "reported_change": change})
        if len(row) == 11:
            post_close_volume = float(Decimal(row[10]) * 100)
            if not math.isfinite(post_close_volume) or post_close_volume < 0:
                raise ValueError("invalid post-close volume")
            rows[-1]["post_close_volume"] = post_close_volume
        previous = day
    if not rows:
        raise ValueError("no historical observations")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    frame = json.loads(args.frame.read_text())
    urls = {row["symbol"]: request_url(row["symbol"], args.start, args.end) for row in frame}
    if len(urls) != len(frame):
        raise ValueError("duplicate source frame symbol")
    args.output.mkdir(parents=True, exist_ok=True)
    identity = {"source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "frame_sha256": hashlib.sha256(args.frame.read_bytes()).hexdigest(),
                "start": args.start, "end": args.end, "research_only": True}
    (args.output / "acquisition_identity.json").write_text(json.dumps(identity, indent=2))

    def collect(symbol: str) -> dict[str, Any]:
        request_id = hashlib.sha256(urls[symbol].encode()).hexdigest()[:16]
        raw_path = args.output / (symbol + "_" + request_id + ".bin")
        result: dict[str, Any] = {"symbol": symbol, "url": urls[symbol], "research_only": True}
        try:
            if not raw_path.exists():
                raw = read_https(urls[symbol], allowed_hosts=("q.stock.sohu.com",))
                raw_path.write_bytes(raw)
            raw = raw_path.read_bytes()
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            rows = parse_prices(raw, symbol, args.start, args.end)
            (args.output / (symbol + ".json")).write_text(json.dumps(rows))
            result.update(status="bounded_rows_verified", rows=len(rows),
                          first=rows[0]["date"], last=rows[-1]["date"])
        except Exception as exc:
            result.update(status="unavailable", error=str(exc))
        return result

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(collect, urls):
            results.append(result)
            (args.output / "manifest.json").write_text(json.dumps(results, indent=2))
            if len(results) % 10 == 0:
                print("source audit", len(results), "/", len(urls), flush=True)
    print("verified", sum(r["status"] == "bounded_rows_verified" for r in results),
          "/", len(results), flush=True)


if __name__ == "__main__":
    main()
