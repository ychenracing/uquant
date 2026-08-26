"""Fail only when a Bandit report adds findings over its Git base."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

FindingKey = tuple[str, str, str]
_LINE_NUMBER_PREFIX = re.compile(r"^[ \t]*\d+[ \t]", re.MULTILINE)


class FindingSummary(TypedDict):
    path: str
    test_id: str
    severity: str
    confidence: str
    line: int
    issue: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    return parser


def _relative_filename(filename: str, root: Path) -> str:
    root = root.resolve()
    source = Path(filename)
    if not source.is_absolute():
        source = root / source
    return source.resolve().relative_to(root).as_posix()


def _normalized_code(code: str) -> str:
    return _LINE_NUMBER_PREFIX.sub("", code)


def _required_text(finding: dict[str, Any], name: str) -> str:
    value = finding.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Bandit finding {name} must be non-empty text")
    return value


def _load_findings(
    report: Path,
    root: Path,
) -> tuple[Counter[FindingKey], list[FindingSummary]]:
    payload: Any = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Bandit report must be an object: {report}")
    errors = payload.get("errors")
    if not isinstance(errors, list):
        raise ValueError(f"Bandit report errors must be a list: {report}")
    if errors:
        raise ValueError(f"Bandit report contains scan errors: {report}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Bandit report results must be a list: {report}")
    findings: Counter[FindingKey] = Counter()
    summaries: list[FindingSummary] = []
    for finding in results:
        if not isinstance(finding, dict):
            raise ValueError("Bandit report contains a non-object finding")
        path = _relative_filename(_required_text(finding, "filename"), root)
        test_id = _required_text(finding, "test_id")
        code = _normalized_code(_required_text(finding, "code"))
        line = finding.get("line_number")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError("Bandit finding line_number must be a positive integer")
        findings[(path, test_id, code)] += 1
        summaries.append(
            {
                "path": path,
                "test_id": test_id,
                "severity": _required_text(finding, "issue_severity"),
                "confidence": _required_text(finding, "issue_confidence"),
                "line": line,
                "issue": _required_text(finding, "issue_text"),
            }
        )
    summaries.sort(
        key=lambda finding: (
            finding["path"],
            finding["line"],
            finding["test_id"],
        )
    )
    return findings, summaries


def _render(counter: Counter[FindingKey]) -> list[dict[str, object]]:
    return [
        {"path": path, "test_id": test_id, "code": code, "count": count}
        for (path, test_id, code), count in sorted(counter.items())
    ]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        baseline, _ = _load_findings(arguments.baseline_report, arguments.baseline_root)
        candidate, candidate_findings = _load_findings(
            arguments.candidate_report,
            arguments.candidate_root,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f"Bandit differential failed closed: {exc}", file=sys.stderr)
        return 2
    added = candidate - baseline
    removed = baseline - candidate
    print(
        json.dumps(
            {
                "status": "FAIL" if added else "PASS",
                "baseline_count": baseline.total(),
                "candidate_count": candidate.total(),
                "candidate_findings": candidate_findings,
                "added": _render(added),
                "removed": _render(removed),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if added else 0


if __name__ == "__main__":
    raise SystemExit(main())
