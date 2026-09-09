"""Collect original dated issuer reports; do not join returns or infer missing values."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen


class ReportHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.links, self.text = [], [], []
        self._rows, self._cells = [], []
        self._link = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._rows.append([])
        if tag in {"td", "th"}:
            self._cells.append([])
        if tag == "a":
            self._link = [dict(attrs).get("href", ""), [], "".join(self.text[-8:])]

    def handle_data(self, data):
        self.text.append(data)
        for cell in self._cells:
            cell.append(data)
        if self._link is not None:
            self._link[1].append(data)

    def handle_endtag(self, tag):
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


def parse_document(raw):
    match = re.search(br"charset\s*=\s*['\"]?([\w-]+)", raw[:8000], re.I)
    encoding = match.group(1).decode() if match else "gb18030"
    doc = ReportHTML()
    doc.feed(raw.decode(encoding, errors="replace"))
    return doc


def number(value):
    value = re.sub(r"[\s,%\uff05]", "", value).replace("\u2212", "-")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    return float(value)


def extract_revenue(raw, period):
    doc = parse_document(raw)
    text = " ".join(doc.text)
    date = re.search(r"公告日期\s*[:\uff1a]\s*(\d{4}-\d{2}-\d{2})", text)
    rows = [r for r in doc.rows if 2 <= len(r) <= 7 and
            re.fullmatch(r"营业收入(?:[\uff08(]元[\uff09)])?", re.sub(r"\s", "", r[0]))]
    result = {"disclosed_date": date.group(1) if date else None,
              "revenue_rows": rows[:4], "status": "unverified_layout"}
    if not rows or date is None:
        return result
    row = rows[0]
    # Accept only simple unadjusted main-metrics layouts; retain other layouts.
    if period.endswith("Q3") and len(row) == 5:
        revenue, growth, prior = number(row[3]), number(row[4]), None
    elif period.endswith("Q1") and len(row) == 3:
        revenue, growth, prior = number(row[1]), number(row[2]), None
    elif (len(row) == 4 and not period.endswith("Q3")) or (period.endswith("FY") and len(row) == 5):
        revenue, prior, growth = number(row[1]), number(row[2]), number(row[3])
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


def collect(output, manifest, workers):
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir(exist_ok=True)

    def fetch(url):
        path = raw_dir / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        if not path.exists():
            with urlopen(url, timeout=25) as response:
                raw = response.read()
            if len(raw) < 1000:
                raise ValueError("empty or truncated source")
            path.write_bytes(raw)
        raw = path.read_bytes()
        return raw, str(path), hashlib.sha256(raw).hexdigest()

    jobs = [(m["symbol"], kind) for m in json.loads(manifest.read_text())["members"]
            for kind in ("sjdbg", "yjdbg", "zqbg", "ndbg")]
    failures, reports = [], {}

    def discover(job):
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

    def read(row):
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
