from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from uquant.validation.holdout import SCORE_FIELDS, load_future_holdout_contract
from uquant.validation.holdout_lanes import (
    HoldoutLane,
    build_lane_validation_report,
    load_lane_registry,
    validate_lane_registry,
    validate_lane_registry_transition,
)

REGISTRY = Path("benchmarks/future_holdout_lane_registry.json")
VALIDATION = Path("artifacts/holdout/lane_validation.json")
CONTRACT_SHA256 = "f1555d2f5527b83899ade8f934f67de8df6050aa2ebc7453d0d4245c618e2aeb"
STRATEGY_SHA256 = "f9c78557e38342c5a994f19fde63352f635ac37c5d2d7a187ba410b98caa1aed"
CONFIG_SHA256 = "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
LOCK_SHA256 = "4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61"
EMPTY_DATA_SHA256 = "4308b714db46527214f6bbc47f46e904dbdc5f747144da5a67766495934ac17b"
SENTINEL_SOURCE_COMMIT = "e02b0ad5c38aa119b2d21cb3142589b1f3f2fae1"
SENTINEL_SOURCE_SHA256 = "0f26fc5be244a985b20cb426b025a909f85939ee7a5ee8905b9367559093b46e"

_CLI_SPEC = importlib.util.spec_from_file_location(
    "future_holdout_cli_lanes",
    Path(__file__).parents[1] / "scripts/future_holdout.py",
)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
_CLI_MODULE = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(_CLI_MODULE)
future_holdout_main = _CLI_MODULE.main


def _lane(lane_id: str = "champion_pre_sentinel") -> HoldoutLane:
    return HoldoutLane(
        lane_id=lane_id,
        activation_session="2026-08-06",
        source_commit="c47367bba64c827fe18f788c9a3650e13ece306f",
        production_source_sha256=STRATEGY_SHA256,
        sentinel_source_sha256=STRATEGY_SHA256,
        effective_config_sha256=CONFIG_SHA256,
        data_contract_sha256=CONTRACT_SHA256,
        data_directory="data/holdout/phase2-future-v1",
        python_full_version="3.12.13",
        numpy_version="2.5.1",
        pandas_version="3.0.5",
        uv_version="0.11.33",
        uv_lock_sha256=LOCK_SHA256,
        parent_lane=None,
        economic_behavior="IDENTICAL",
        status="OBSERVING",
    )


def _sentinel_lane() -> HoldoutLane:
    return replace(
        _lane("sentinel_shadow"),
        activation_session="2026-08-19",
        source_commit=SENTINEL_SOURCE_COMMIT,
        sentinel_source_sha256=SENTINEL_SOURCE_SHA256,
        parent_lane="champion_pre_sentinel",
    )


def test_tracked_registry_appends_exact_non_backfilled_sentinel_lane() -> None:
    contract = load_future_holdout_contract()
    lanes = load_lane_registry(REGISTRY)

    assert contract.sha256 == CONTRACT_SHA256
    assert contract.last_in_sample_date == "2026-08-05"
    assert contract.first_holdout_date == "2026-08-06"
    assert contract.review_milestones == (20, 40, 60)
    assert contract.parameter_changes_from_observation is False
    assert lanes == (_lane(), _sentinel_lane())
    assert lanes[1].economic_behavior == "IDENTICAL"
    assert lanes[1].status == "OBSERVING"


def test_registry_rejects_duplicate_ids_missing_or_forward_parents() -> None:
    contract = load_future_holdout_contract()
    champion = _lane()
    duplicate = replace(champion)
    with pytest.raises(ValueError, match="unique"):
        validate_lane_registry((champion, duplicate), contract)

    missing_parent = replace(
        champion,
        lane_id="sentinel_shadow",
        activation_session="2026-08-19",
        parent_lane="missing",
    )
    with pytest.raises(ValueError, match="parent"):
        validate_lane_registry((missing_parent,), contract)

    forward_parent = replace(missing_parent, parent_lane="future")
    future = replace(
        champion,
        lane_id="future",
        activation_session="2026-08-20",
        parent_lane=None,
    )
    with pytest.raises(ValueError, match="parent"):
        validate_lane_registry((forward_parent, future), contract)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("activation_session", "2026-08-05", "activation"),
        ("source_commit", "abc", "commit"),
        ("production_source_sha256", "0" * 63, "SHA-256"),
        ("effective_config_sha256", "0" * 63, "SHA-256"),
        ("data_contract_sha256", "0" * 64, "contract"),
        ("data_directory", "data/frozen", "data directory"),
        ("economic_behavior", "BACKFILL", "behavior"),
        ("status", "PASSED", "status"),
        ("python_full_version", "", "runtime"),
        ("uv_lock_sha256", "x" * 64, "SHA-256"),
    ],
)
def test_registry_rejects_invalid_lane_identity(
    field: str,
    value: object,
    message: str,
) -> None:
    contract = load_future_holdout_contract()
    with pytest.raises(ValueError, match=message):
        validate_lane_registry((replace(_lane(), **{field: value}),), contract)


def test_registry_transition_is_append_only_and_forbids_backfill() -> None:
    contract = load_future_holdout_contract()
    champion = _lane()
    shadow = replace(
        champion,
        lane_id="sentinel_shadow",
        activation_session="2026-08-19",
        parent_lane="champion_pre_sentinel",
    )
    validate_lane_registry_transition(
        (champion,),
        (champion, shadow),
        contract,
        observed_sessions=contract.review_sessions[:9],
    )

    with pytest.raises(ValueError, match="deleted"):
        validate_lane_registry_transition((champion, shadow), (champion,), contract)
    with pytest.raises(ValueError, match="identity"):
        validate_lane_registry_transition(
            (champion,),
            (replace(champion, source_commit="0" * 40),),
            contract,
        )
    with pytest.raises(ValueError, match="backfill"):
        validate_lane_registry_transition(
            (champion,),
            (
                champion,
                replace(
                    shadow,
                    activation_session="2026-08-18",
                ),
            ),
            contract,
            observed_sessions=contract.review_sessions[:9],
        )


def test_registry_transition_cannot_move_activation_or_hide_behavior_change() -> None:
    contract = load_future_holdout_contract()
    champion = _lane()
    changed = replace(
        champion,
        activation_session="2026-08-07",
        economic_behavior="FREEZE_ONLY",
    )
    with pytest.raises(ValueError, match="identity"):
        validate_lane_registry_transition((champion,), (changed,), contract)

    behavioral = replace(
        champion,
        lane_id="sentinel_limited_gross_cap",
        activation_session="2026-08-19",
        parent_lane="champion_pre_sentinel",
        economic_behavior="GROSS_CAP",
    )
    with pytest.raises(ValueError, match="identity"):
        validate_lane_registry_transition(
            (champion, behavioral),
            (champion, replace(behavioral, economic_behavior="IDENTICAL")),
            contract,
        )


def test_lane_report_is_non_reviewable_and_scores_are_null_before_20_sessions() -> None:
    contract = load_future_holdout_contract()
    report = build_lane_validation_report(
        lanes=(_lane(),),
        contract=contract,
        observed_sessions=contract.review_sessions[:9],
        holdout_data_sha256="1" * 64,
    )

    lane = report["lanes"][0]
    assert lane["observed_sessions"] == 9
    assert lane["next_milestone"] == 20
    assert lane["score_status"] == "NON_REVIEWABLE"
    assert lane["formal_reviewable"] is False
    assert lane["scores"] == {field: None for field in SCORE_FIELDS}


def test_lane_report_does_not_backfill_pre_activation_sessions() -> None:
    contract = load_future_holdout_contract()
    lane = replace(
        _lane("sentinel_shadow"),
        activation_session="2026-08-19",
        parent_lane="champion_pre_sentinel",
    )
    report = build_lane_validation_report(
        lanes=(_lane(), lane),
        contract=contract,
        observed_sessions=contract.review_sessions[:12],
        holdout_data_sha256="2" * 64,
    )

    assert report["lanes"][0]["observed_sessions"] == 12
    assert report["lanes"][1]["observed_sessions"] == 3
    assert report["lanes"][1]["first_observed_session"] == "2026-08-19"


def test_registry_loader_rejects_duplicate_unknown_and_resealed_edits(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_lane_registry(duplicate)

    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    raw["unknown"] = True
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_lane_registry(unknown)

    raw.pop("unknown")
    raw["lanes"][0]["activation_session"] = "2026-08-07"
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_lane_registry(edited)


def test_empty_holdout_report_remains_null_and_bound_to_empty_data_identity() -> None:
    contract = load_future_holdout_contract()
    report = build_lane_validation_report(
        lanes=(_lane(),),
        contract=contract,
        observed_sessions=(),
        holdout_data_sha256=EMPTY_DATA_SHA256,
    )

    assert report["observed_sessions"] == 0
    assert report["holdout_data_sha256"] == EMPTY_DATA_SHA256
    assert all(value is None for value in report["lanes"][0]["scores"].values())


def test_tracked_sentinel_lane_has_no_observations_or_formal_scores() -> None:
    report = json.loads(VALIDATION.read_text(encoding="utf-8"))
    lane = report["lanes"][1]

    assert lane["lane_id"] == "sentinel_shadow"
    assert lane["activation_session"] == "2026-08-19"
    assert lane["observed_sessions"] == 0
    assert lane["next_milestone"] == 20
    assert lane["formal_reviewable"] is False
    assert lane["score_status"] == "NON_REVIEWABLE"
    assert lane["scores"] == {field: None for field in SCORE_FIELDS}


def test_tracked_validation_is_exact_empty_observation_report() -> None:
    expected = build_lane_validation_report(
        lanes=load_lane_registry(REGISTRY),
        contract=load_future_holdout_contract(),
        observed_sessions=(),
        holdout_data_sha256=EMPTY_DATA_SHA256,
    )

    assert json.loads(VALIDATION.read_text(encoding="utf-8")) == expected


def test_validation_cli_recomputes_tracked_lane_evidence(capsys: pytest.CaptureFixture[str]) -> None:
    assert future_holdout_main(["validate-lanes"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["observed_sessions"] == 0
    assert output["lanes"][0]["next_milestone"] == 20
    assert all(
        value is None
        for lane in output["lanes"]
        for value in lane["scores"].values()
    )


def test_static_lane_validation_is_independent_from_local_observation_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repository"
    (root / "benchmarks").mkdir(parents=True)
    (root / "artifacts/holdout").mkdir(parents=True)
    for source in (
        Path("benchmarks/future_holdout_contract.json"),
        REGISTRY,
        VALIDATION,
    ):
        destination = root / source
        destination.write_bytes(source.read_bytes())

    observed = root / "data/holdout/phase2-future-v1/2026-08-06/market.csv"
    observed.parent.mkdir(parents=True)
    observed.write_text(
        "date,open,high,low,close,volume\n"
        "2026-08-06,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    local_report = root / "future_holdout_lane_report.json"

    assert future_holdout_main(
        [
            "report-lanes",
            "--repository-root",
            str(root),
            "--output",
            str(local_report),
        ]
    ) == 0
    reported = json.loads(capsys.readouterr().out)
    assert reported["observed_sessions"] == 1
    assert reported["lanes"][0]["first_observed_session"] == "2026-08-06"
    assert json.loads(local_report.read_text(encoding="utf-8")) == reported

    assert future_holdout_main(
        [
            "validate-static-lanes",
            "--repository-root",
            str(root),
        ]
    ) == 0
    static = json.loads(capsys.readouterr().out)
    assert static["observed_sessions"] == 0
    assert static == json.loads(
        (root / "artifacts/holdout/lane_validation.json").read_text(encoding="utf-8")
    )
