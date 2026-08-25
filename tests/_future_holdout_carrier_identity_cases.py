from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_future_holdout_runtime import (
    _csv,
    _decision_record,
    _install_holdout_contract,
    _valid_replay,
)

from uquant.validation import holdout_runtime as holdout_runtime_module
from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    load_future_holdout_contract,
)
from uquant.validation.holdout_runtime import (
    generate_future_holdout_replay,
)


@pytest.mark.parametrize("generation", ("owned", "foreign"))
def test_holdout_post_claim_link_failure_preserves_every_carrier_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation: str,
) -> None:
    """Catches post-claim restoration errors deleting the only evidence copies."""

    path = tmp_path / "replay.json"
    expected = b"transaction-owned replay\n"
    prior = b"prior replay\n"
    foreign = b"foreign replay\n"
    path.write_bytes(expected if generation == "owned" else foreign)

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected post-claim link failure")

    monkeypatch.setattr(holdout_runtime_module.os, "link", fail_link)

    with pytest.raises(OSError, match="post-claim link failure"):
        holdout_runtime_module._restore_owned_artifact(
            path,
            prior,
            expected,
        )

    preserved = {item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    if generation == "owned":
        assert preserved == {expected, prior}
    else:
        assert preserved == {foreign}

def test_holdout_lock_cleanup_preserves_primary_and_closes_every_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches one close error masking the primary and skipping later descriptors."""

    original_close = holdout_runtime_module.os.close
    closed: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)
        if len(closed) == 1:
            raise OSError("injected close failure")

    monkeypatch.setattr(holdout_runtime_module.os, "close", close_then_fail_once)

    with (
        pytest.raises(RuntimeError, match="primary transaction failure") as raised,
        holdout_runtime_module._artifact_bundle_lock(
            tmp_path,
            (tmp_path / "replay.json", tmp_path / "decision.json"),
        ),
    ):
        raise RuntimeError("primary transaction failure")

    assert len(closed) == 3
    assert len(set(closed)) == 3
    assert any("injected close failure" in note for note in raised.value.__notes__)

def test_holdout_lock_identity_follows_shared_carriers_across_repositories(
    tmp_path: Path,
) -> None:
    """Catches repository-root locks failing to serialize shared external carriers."""

    shared = tmp_path / "shared" / "replay.json"
    left = holdout_runtime_module._artifact_bundle_lock_paths(
        (shared, tmp_path / "left-decision.json")
    )
    right = holdout_runtime_module._artifact_bundle_lock_paths(
        (shared, tmp_path / "right-decision.json")
    )

    assert set(left) & set(right)

@pytest.mark.parametrize("lock_output", ("replay", "decision"))
def test_holdout_outputs_cannot_replace_the_transaction_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lock_output: str,
) -> None:
    """Catches output replacement invalidating the inode that serializes writers."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    lock_path = holdout_runtime_module._artifact_bundle_lock_path(tmp_path.resolve())
    replay_output = lock_path if lock_output == "replay" else tmp_path / "replay.json"
    decision_output = lock_path if lock_output == "decision" else tmp_path / "decision.json"

    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=replay_output,
            decision_output_path=decision_output,
        )

def test_holdout_checkpoint_prevents_output_carrier_switching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    replay = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: replay,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )

    with pytest.raises(ValueError, match="checkpointed output paths"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "reports/renamed-replay.json",
            decision_output_path=tmp_path / "reports/renamed-decision.json",
        )

def test_holdout_checkpoint_rejects_mutation_of_the_prior_data_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    first_path = (
        tmp_path
        / HOLDOUT_DATA_DIRECTORY
        / contract.review_sessions[0]
        / "sh000300.csv"
    )
    _csv(first_path, contract.review_sessions[0])
    first_snapshot = holdout_runtime_module._capture_holdout_data(
        tmp_path / HOLDOUT_DATA_DIRECTORY
    )
    first = _valid_replay()
    first["holdout_data_sha256"] = first_snapshot.sha256
    first["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in first.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )

    _csv(first_path, contract.review_sessions[0], close=11.0)
    _csv(
        tmp_path
        / HOLDOUT_DATA_DIRECTORY
        / contract.review_sessions[1]
        / "sh000300.csv",
        contract.review_sessions[1],
    )
    current = holdout_runtime_module._capture_holdout_data(
        tmp_path / HOLDOUT_DATA_DIRECTORY
    )
    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    extended = json.loads(json.dumps(first))
    extended["holdout_data_sha256"] = current.sha256
    extended["sessions"] = list(contract.review_sessions[:2])
    extended["decision_digests"].append(second_digest)
    extended["decisions"].append(second_decision)
    extended["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in extended.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: extended,
    )

    with pytest.raises(ValueError, match="checkpointed data prefix"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
        )
