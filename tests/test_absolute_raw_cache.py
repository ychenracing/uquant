"""Raw failures survive the reader without replaying or relabelling evidence."""
import gzip
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from _absolute_generalization_metrics_fixture import complete_replay

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.validation.absolute_generalization import _replay_codec as codec
from uquant.validation.absolute_generalization import runtime


def test_reader_failure_does_not_skip_other_native_cells(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import scripts.run_absolute_generalization_acceptance as runner
    from uquant.validation.absolute_generalization import (
        build_leave_one_out_scenarios,
        load_absolute_generalization_contract,
    )
    contract = load_absolute_generalization_contract()
    scenarios = build_leave_one_out_scenarios(contract)[:2]
    calls = []
    monkeypatch.setattr(runner, "selected_scenarios", lambda *a: scenarios)
    monkeypatch.setattr(runner, "read_cached_cell", lambda *a: None)
    def produce(scenario, *a, **k):
        calls.append(scenario.cell_id)
        if scenario == scenarios[0]:
            raise ValueError("reader failure")
        return SimpleNamespace(status="REPLAY_ERROR")
    monkeypatch.setattr(runner, "run_runtime_cell_artifact", produce)
    options = runner.RunnerOptions(
        shard="loo-a", symbol=None, run_id="unit-test", run_attempt=1,
        output=tmp_path / "manifest.json", cache_dir=tmp_path / "cache",
        data_dir=runner.Path(runner.__file__).resolve().parents[1] / "data/frozen",
        shard_root=None, artifact_prefix=None, upstream_result=None,
    )
    with pytest.raises(ExceptionGroup, match="independent cells completed") as captured:
        runner._run_execution(options, contract)
    assert len(captured.value.exceptions) == 1
    cause = captured.value.exceptions[0]
    assert isinstance(cause, ValueError)
    assert str(cause) == "reader failure"
    assert cause.__traceback__ is not None
    assert cause.__notes__ == [f"Absolute cell: {scenarios[0].cell_id}"]
    assert calls == [scenario.cell_id for scenario in scenarios]
    assert len(list((tmp_path / "cache/reader-errors").glob("*.json"))) == 1
    assert not options.output.exists()


@pytest.fixture
def saved(monkeypatch, tmp_path):
    replay = complete_replay()
    identity = {"scenario": codec.replay_to_raw(replay)["scenario"],
                "head": "head", "source": "source", "config": "config",
                "data": "data", "runtime": "runtime"}
    monkeypatch.setattr(codec, "raw_replay_identity", lambda *_: deepcopy(identity))
    calls = []
    monkeypatch.setattr(codec, "run_absolute_generalization_replay",
                        lambda *a, **k: calls.append(1) or replay)
    args = dict(root=tmp_path, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    return replay, identity, calls, args


def test_reader_failure_retains_raw_and_resume_never_replays(saved, monkeypatch):
    replay, _identity, calls, args = saved
    def reject(*a, **k):
        raise ValueError("strict reader failure")
    monkeypatch.setattr(runtime, "derive_runtime_cell_artifact", reject)
    for _ in range(2):
        with pytest.raises(ValueError, match="strict reader failure"):
            runtime.run_runtime_cell_artifact(replay.scenario, object(), **args)
    assert calls == [1]
    assert codec.cached_removal_replay(replay.scenario, **args) == replay


def test_native_error_prefix_is_retained_and_not_retried(saved, monkeypatch):
    replay, _identity, calls, args = saved
    failed = replace(replay, status="REPLAY_ERROR", replay_error="native failure")
    monkeypatch.setattr(codec, "run_absolute_generalization_replay",
                        lambda *a, **k: calls.append(1) or failed)
    assert codec.cached_removal_replay(replay.scenario, **args) == failed
    assert codec.cached_removal_replay(replay.scenario, **args) == failed
    assert calls == [1]


@pytest.mark.parametrize("field", ["head", "source", "config", "data", "runtime", "scenario"])
def test_wrong_identity_at_expected_key_is_rejected(saved, field):
    replay, identity, calls, args = saved
    codec.cached_removal_replay(replay.scenario, **args)
    original = args["cache_dir"] / "raw" / f"{canonical_json_sha256(identity)}.json.gz"
    identity[field] = "different"
    target = original.with_name(f"{canonical_json_sha256(identity)}.json.gz")
    target.write_bytes(original.read_bytes())
    with pytest.raises(ValueError, match="identity or digest"):
        codec.cached_removal_replay(replay.scenario, **args)
    assert calls == [1]


@pytest.mark.parametrize("fault", ["digest", "duplicate", "symlink"])
def test_corrupt_raw_cache_is_rejected_without_overwrite(saved, fault):
    replay, _identity, calls, args = saved
    codec.cached_removal_replay(replay.scenario, **args)
    path = next((args["cache_dir"] / "raw").glob("*.gz"))
    if fault == "symlink":
        target = path.with_suffix(".original")
        path.rename(target)
        path.symlink_to(target)
    else:
        raw = json.loads(gzip.decompress(path.read_bytes()))
        if fault == "digest":
            raw["sha256"] = "0" * 64
            content = json.dumps(raw)
        else:
            content = '{"identity":{},' + json.dumps(raw)[1:]
        path.write_bytes(gzip.compress(content.encode()))
    before = path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        codec.cached_removal_replay(replay.scenario, **args)
    assert path.read_bytes() == before and calls == [1]


def test_codec_rejects_fake_payload_and_deepcopy_coercion():
    from dataclasses import dataclass

    @dataclass
    class FakePayload:
        canonical_json: bytes
        sha256: str

    class Foreign:
        def __deepcopy__(self, memo):
            pytest.fail("codec must never execute arbitrary deepcopy")

    replay = complete_replay()
    for value in (FakePayload(b"{}", canonical_json_sha256({})), Foreign()):
        malformed = replace(replay, final_account_payload=value)
        with pytest.raises(ValueError):
            codec.replay_to_raw(malformed)
