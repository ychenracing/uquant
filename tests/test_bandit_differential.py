from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "bandit_differential.py"


def _write_report(
    path: Path,
    findings: list[dict[str, Any]],
    *,
    errors: list[dict[str, Any]] | None = None,
) -> None:
    path.write_text(
        json.dumps({"errors": errors or [], "results": findings}),
        encoding="utf-8",
    )


def _finding(
    *,
    filename: Path,
    test_id: str,
    line_number: int,
    code: str,
) -> dict[str, Any]:
    return {
        "filename": str(filename),
        "test_id": test_id,
        "issue_severity": "LOW",
        "issue_confidence": "HIGH",
        "line_number": line_number,
        "code": code,
        "issue_text": "Consider subprocess security implications.",
    }


def test_new_finding_fails_the_security_gate(tmp_path: Path) -> None:
    """Catches a candidate security finding being accepted as baseline debt."""

    baseline_root = candidate_root = tmp_path
    baseline_report = tmp_path / "baseline.json"
    candidate_report = tmp_path / "candidate.json"
    existing = _finding(
        filename=Path("scripts/builder.py"),
        test_id="B404",
        line_number=12,
        code="12 import subprocess\n",
    )
    _write_report(
        baseline_report,
        [existing],
    )
    _write_report(
        candidate_report,
        [
            existing,
            _finding(
                filename=Path("scripts/builder.py"),
                test_id="B603",
                line_number=34,
                code='34 subprocess.run(["git", "status"])\n',
            ),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-report",
            str(baseline_report),
            "--baseline-root",
            str(baseline_root),
            "--candidate-report",
            str(candidate_report),
            "--candidate-root",
            str(candidate_root),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "FAIL"
    assert summary["baseline_count"] == 1
    assert summary["candidate_count"] == 2
    assert summary["added"] == [
        {
            "code": 'subprocess.run(["git", "status"])\n',
            "count": 1,
            "path": "scripts/builder.py",
            "test_id": "B603",
        }
    ]
    assert summary["removed"] == []


def test_equivalent_findings_ignore_checkout_root_and_line_number_churn(
    tmp_path: Path,
) -> None:
    """Catches checkout paths or shifted source lines creating a false regression."""

    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    baseline_report = tmp_path / "baseline.json"
    candidate_report = tmp_path / "candidate.json"
    _write_report(
        baseline_report,
        [
            _finding(
                filename=baseline_root / "scripts" / "builder.py",
                test_id="B404",
                line_number=12,
                code="12 import subprocess\n",
            )
        ],
    )
    _write_report(
        candidate_report,
        [
            _finding(
                filename=candidate_root / "scripts" / "builder.py",
                test_id="B404",
                line_number=99,
                code="99 import subprocess\n",
            )
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-report",
            str(baseline_report),
            "--baseline-root",
            str(baseline_root),
            "--candidate-report",
            str(candidate_report),
            "--candidate-root",
            str(candidate_root),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "PASS"
    assert summary["baseline_count"] == summary["candidate_count"] == 1
    assert summary["added"] == []
    assert summary["removed"] == []


def test_source_indentation_remains_part_of_the_finding_identity(tmp_path: Path) -> None:
    """Catches prefix normalization erasing meaningful source indentation."""

    baseline_report = tmp_path / "baseline.json"
    candidate_report = tmp_path / "candidate.json"
    _write_report(
        baseline_report,
        [
            _finding(
                filename=Path("scripts/builder.py"),
                test_id="B603",
                line_number=12,
                code='12     subprocess.run(["git", "status"])\n',
            )
        ],
    )
    _write_report(
        candidate_report,
        [
            _finding(
                filename=Path("scripts/builder.py"),
                test_id="B603",
                line_number=99,
                code='99 subprocess.run(["git", "status"])\n',
            )
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-report",
            str(baseline_report),
            "--baseline-root",
            str(tmp_path),
            "--candidate-report",
            str(candidate_report),
            "--candidate-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary["added"] == [
        {
            "code": 'subprocess.run(["git", "status"])\n',
            "count": 1,
            "path": "scripts/builder.py",
            "test_id": "B603",
        }
    ]


def test_bandit_scan_errors_fail_closed(tmp_path: Path) -> None:
    """Catches an incomplete Bandit scan being reported as a clean differential."""

    baseline_report = tmp_path / "baseline.json"
    candidate_report = tmp_path / "candidate.json"
    _write_report(
        baseline_report,
        [],
        errors=[{"filename": "scripts/builder.py", "reason": "syntax error"}],
    )
    _write_report(candidate_report, [])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-report",
            str(baseline_report),
            "--baseline-root",
            str(tmp_path),
            "--candidate-report",
            str(candidate_report),
            "--candidate-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "Bandit report contains scan errors" in result.stderr


def test_passing_differential_still_reports_existing_candidate_debt(
    tmp_path: Path,
) -> None:
    """Catches accepted baseline findings disappearing from the CI audit log."""

    baseline_report = tmp_path / "baseline.json"
    candidate_report = tmp_path / "candidate.json"
    finding = _finding(
        filename=Path("scripts/builder.py"),
        test_id="B607",
        line_number=34,
        code='34 subprocess.run(["git", "status"])\n',
    )
    _write_report(baseline_report, [finding])
    _write_report(candidate_report, [finding])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-report",
            str(baseline_report),
            "--baseline-root",
            str(tmp_path),
            "--candidate-report",
            str(candidate_report),
            "--candidate-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["candidate_findings"] == [
        {
            "confidence": "HIGH",
            "issue": "Consider subprocess security implications.",
            "line": 34,
            "path": "scripts/builder.py",
            "severity": "LOW",
            "test_id": "B607",
        }
    ]
