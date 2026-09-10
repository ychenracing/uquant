"""Collect original dated issuer reports; do not join returns or infer missing values."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from research.https_input import read_https


class ReportHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.links: list[tuple[str, str, str]] = []
        self.text: list[str] = []
        self._rows: list[list[str]] = []
        self._cells: list[list[str]] = []
        self._link: tuple[str, list[str], str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._rows.append([])
        if tag in {"td", "th"}:
            self._cells.append([])
        if tag == "a":
            self._link = (dict(attrs).get("href") or "", [], "".join(self.text[-8:]))

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        for cell in self._cells:
            cell.append(data)
        if self._link is not None:
            self._link[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cells:
            cell = "".join(self._cells.pop()).strip()
            if self._rows:
                self._rows[-1].append(cell)
        if tag == "tr" and self._rows:
            self.rows.append(self._rows.pop())
        if tag == "a" and self._link is not None:
            href, text, prefix = self._link
            self.links.append((href, "".join(text).strip(), prefix))
            self._link = None


def parse_document(raw: bytes) -> ReportHTML:
    match = re.search(br"charset\s*=\s*['\"]?([\w-]+)", raw[:8000], re.I)
    encoding = match.group(1).decode() if match else "gb18030"
    doc = ReportHTML()
    doc.feed(raw.decode(encoding, errors="replace"))
    return doc


def number(value: str) -> float | None:
    value = re.sub(r"[\s,%\uff05]", "", value).replace("\u2212", "-")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    return float(value)


def extract_revenue(raw: bytes, period: str) -> dict[str, Any]:
    doc = parse_document(raw)
    text = " ".join(doc.text)
    date = re.search(r"公告日期\s*[:\uff1a]\s*(\d{4}-\d{2}-\d{2})", text)
    indexed = [(i, r) for i, r in enumerate(doc.rows) if 2 <= len(r) <= 9 and
            re.fullmatch(r"营业收入(?:[\uff08(]元[\uff09)])?", re.sub(r"\s", "", r[0]))]
    rows = [r for _, r in indexed]
    result: dict[str, Any] = {"disclosed_date": date.group(1) if date else None,
              "revenue_rows": rows[:4], "status": "unverified_layout"}
    if not rows or date is None:
        return result
    row = rows[0]
    fiscal_values = tuple(number(value) for value in row[1:4]) if len(row) >= 4 else (None, None, None)
    fiscal_revenue, fiscal_prior, fiscal_growth = fiscal_values
    headers = " ".join(" ".join(r) for r in doc.rows[max(0, indexed[0][0] - 2):indexed[0][0]])
    # Adjusted comparisons must be explicit and reconcile to the stated growth.
    if period.endswith("Q3") and len(row) == 5:
        revenue, growth, prior = number(row[3]), number(row[4]), None
    elif period.endswith("Q3") and len(row) == 9 and "调整前" in headers and "调整后" in headers:
        revenue, growth, prior = number(row[5]), number(row[8]), number(row[7])
    elif period.endswith("Q1") and len(row) == 3:
        revenue, growth, prior = number(row[1]), number(row[2]), None
    elif (len(row) == 4 and not period.endswith("Q3")) or (period.endswith("FY") and len(row) == 5):
        revenue, prior, growth = number(row[1]), number(row[2]), number(row[3])
    elif (period.endswith("FY") and len(row) == 6 and fiscal_prior
          and fiscal_growth is not None and fiscal_revenue is not None
          and abs((fiscal_revenue / fiscal_prior - 1) * 100 - fiscal_growth) <= .03):
        # Only the older third fiscal year has an adjusted/before pair.
        revenue, prior, growth = fiscal_revenue, fiscal_prior, fiscal_growth
    elif not period.endswith("Q3") and len(row) in {5, 6, 7} and "调整后" in headers and "调整前" in headers:
        prior_column = 2 if headers.index("调整后") < headers.index("调整前") else 3
        revenue, prior, growth = number(row[1]), number(row[prior_column]), number(row[4])
    else:
        return result
    if revenue is None or growth is None or revenue < 0:
        return result
    if prior is not None and (prior <= 0 or abs((revenue / prior - 1) * 100 - growth) > .03):
        result["status"] = "arithmetic_mismatch"
        return result
    result.update(status="parsed_pending_audit", cumulative_revenue_reported=revenue,
                  cumulative_revenue_yoy_percent=growth, prior_revenue_reported=prior)
    return result


def collect(output: Path, manifest: Path, workers: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir(exist_ok=True)

    def fetch(url: str) -> tuple[bytes, str, str]:
        path = raw_dir / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        if not path.exists():
            raw = read_https(url, allowed_hosts=("money.finance.sina.com.cn", "vip.stock.finance.sina.com.cn"))
            if len(raw) < 1000:
                raise ValueError("empty or truncated source")
            path.write_bytes(raw)
        raw = path.read_bytes()
        return raw, str(path), hashlib.sha256(raw).hexdigest()

    jobs = [(m["symbol"], kind) for m in json.loads(manifest.read_text())["members"]
            for kind in ("sjdbg", "yjdbg", "zqbg", "ndbg")]
    failures: list[dict[str, Any]] = []
    reports: dict[tuple[str, str, str], dict[str, str]] = {}

    def discover(job: tuple[str, str]) -> list[dict[str, str]]:
        symbol, kind = job
        url = f"https://money.finance.sina.com.cn/corp/go.php/vCB_Bulletin/stockid/{symbol[2:]}/page_type/{kind}.phtml"
        raw, _, _ = fetch(url)
        found = []
        for href, title, prefix in parse_document(raw).links:
            if "vCB_AllBulletinDetail.php" not in href or any(x in title for x in ("摘要", "更正", "修订", "取消")):
                continue
            year = re.search(r"(20\d{2})\s*年", title)
            if not year or not 2022 <= int(year.group(1)) <= 2026:
                continue
            period = year.group(1) + {"sjdbg": "Q3", "yjdbg": "Q1", "zqbg": "H1", "ndbg": "FY"}[kind]
            dates = re.findall(r"20\d{2}-\d{2}-\d{2}", prefix)
            if not dates or not "2022-09-01" <= dates[-1] <= "2026-08-05":
                continue
            found.append({"symbol": symbol, "period": period, "title": title,
                          "listing_date": dates[-1], "source_url": urljoin(url, href)})
        return found

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_jobs = {pool.submit(discover, job): job for job in jobs}
        for count, future in enumerate(concurrent.futures.as_completed(future_jobs), 1):
            try:
                for row in future.result():
                    reports[(row["symbol"], row["period"], row["source_url"])] = row
            except Exception as exc:
                failures.append({"job": future_jobs[future], "error": str(exc)[:200]})
            if count % 20 == 0:
                print(f"discovery {count}/{len(jobs)} reports={len(reports)} errors={len(failures)}", flush=True)
    (output / "discovery.json").write_text(json.dumps({"reports": list(reports.values()), "failures": failures}, ensure_ascii=False, indent=2))

    def read(row: dict[str, str]) -> dict[str, Any]:
        try:
            raw, path, sha = fetch(row["source_url"])
            result = {**row, **extract_revenue(raw, row["period"]), "raw_path": path, "sha256": sha}
            if result["disclosed_date"] != row["listing_date"]:
                result["status"] = "date_mismatch"
            return result
        except Exception as exc:
            return {**row, "status": "fetch_failed", "error": str(exc)[:200]}

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for count, row in enumerate(pool.map(read, reports.values()), 1):
            rows.append(row)
            if count % 20 == 0:
                print(f"reports {count}/{len(reports)}", flush=True)
    rows.sort(key=lambda r: (r["symbol"], r["period"], r["listing_date"]))
    (output / "panel_unverified.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"DONE reports={len(rows)} parsed={sum(r['status']=='parsed_pending_audit' for r in rows)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    collect(args.output, args.manifest, args.workers)
