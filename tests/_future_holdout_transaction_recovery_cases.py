from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_future_holdout_runtime import (
    _decision_record,
    _install_holdout_contract,
    _valid_replay,
)

from uquant.validation import holdout_runtime as holdout_runtime_module
from uquant.validation.holdout import (
    load_future_holdout_contract,
)
from uquant.validation.holdout_runtime import (
    generate_future_holdout_replay,
)


def test_holdout_bundle_does_not_remove_a_rejected_output_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches rollback treating an unsafe pre-existing carrier as newly created."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    output = tmp_path / "artifacts/replay.json"
    output.parent.mkdir(parents=True)
    output.symlink_to(tmp_path / "missing-target.json")

    with pytest.raises(ValueError, match="symlink"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=tmp_path / "artifacts/decision.json",
        )

    assert output.is_symlink()

@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_holdout_rollback_restores_the_prior_carrier_mode(tmp_path: Path) -> None:
    """Catches recovery replacing prior evidence with a private-mode inode."""

    path = tmp_path / "replay.json"
    prior = b"prior replay evidence\n"
    owned = b"transaction-owned replay evidence\n"
    path.write_bytes(prior)
    path.chmod(0o640)
    snapshots = holdout_runtime_module._artifact_snapshots((path,))

    path.unlink()
    path.write_bytes(owned)
    path.chmod(0o600)
    failures = holdout_runtime_module._restore_artifact_snapshots(
        snapshots,
        {path: owned},
    )

    assert failures == ()
    assert path.read_bytes() == prior
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

def test_holdout_snapshot_mode_and_publish_edges_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises canonical identity, mode inspection, and no-replace publication."""

    with pytest.raises(ValueError, match="path is not canonical"):
        holdout_runtime_module._artifact_snapshots((Path("relative.json"),))

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    carrier = real_parent / "carrier.json"
    carrier.write_bytes(b"prior")
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="contains a symlink"):
        holdout_runtime_module._artifact_snapshots((alias / "carrier.json",))

    real_stat = Path.stat
    carrier_stats = 0

    def fail_mode_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal carrier_stats
        if self == carrier and kwargs.get("follow_symlinks") is False:
            carrier_stats += 1
            if carrier_stats == 3:
                raise OSError("mode unavailable")
        return real_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", fail_mode_stat)
        with pytest.raises(ValueError, match=r"cannot inspect.*mode"):
            holdout_runtime_module._artifact_snapshots((carrier,))

    carrier_stats = 0

    def unsafe_mode_stat(self: Path, *args: object, **kwargs: object) -> object:
        nonlocal carrier_stats
        if self == carrier and kwargs.get("follow_symlinks") is False:
            carrier_stats += 1
            if carrier_stats == 3:
                return SimpleNamespace(st_mode=stat.S_IFDIR)
        return real_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", unsafe_mode_stat)
        with pytest.raises(ValueError, match="artifact is unsafe"):
            holdout_runtime_module._artifact_snapshots((carrier,))

    with monkeypatch.context() as patch:
        patch.setattr(
            holdout_runtime_module,
            "os",
            SimpleNamespace(
                name="nt",
                O_RDONLY=os.O_RDONLY,
                O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
                open=os.open,
                fstat=os.fstat,
                fdopen=os.fdopen,
                dup=os.dup,
                close=os.close,
            ),
        )
        snapshot = holdout_runtime_module._artifact_snapshots((carrier,))[carrier]
    assert snapshot.payload == b"prior"
    assert snapshot.mode is None

    assert holdout_runtime_module._link_bytes_if_absent(carrier, b"successor") is False
    assert carrier.read_bytes() == b"prior"

def test_holdout_restore_preserves_owned_bytes_when_claim_inspection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises recovery when a claimed carrier cannot be safely inspected."""

    carrier = tmp_path / "carrier.json"
    owned = b"owned generation"
    carrier.write_bytes(owned)
    real_read = holdout_runtime_module._read_protected_artifact

    def fail_claim_read(path: str | Path, *, label: str) -> bytes:
        if label == "future holdout rollback artifact":
            raise ValueError("claim inspection failed")
        return real_read(path, label=label)

    monkeypatch.setattr(
        holdout_runtime_module,
        "_read_protected_artifact",
        fail_claim_read,
    )
    holdout_runtime_module._restore_owned_artifact(
        carrier,
        b"prior generation",
        owned,
    )
    assert carrier.read_bytes() == owned

    with monkeypatch.context() as patch:
        patch.setattr(
            holdout_runtime_module.os,
            "replace",
            lambda *_: (_ for _ in ()).throw(PermissionError("claim denied")),
        )
        with pytest.raises(PermissionError, match="claim denied") as caught:
            holdout_runtime_module._restore_owned_artifact(carrier, None, owned)
    assert not getattr(caught.value, "__notes__", ())
    assert carrier.read_bytes() == owned

def test_holdout_rollback_does_not_overwrite_a_foreign_carrier_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches rollback clobbering bytes no longer owned by its transaction."""

    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    first = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    checkpoint = tmp_path / "artifacts/future_holdout_checkpoint.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )
    before_decision = decision_output.read_bytes()
    before_checkpoint = checkpoint.read_bytes()

    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    extended = json.loads(json.dumps(first))
    extended["sessions"].append(contract.review_sessions[1])
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
    original_write = holdout_runtime_module.atomic_write_text
    foreign = b"foreign carrier generation\n"

    def replace_then_fail(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == decision_output:
            output.write_bytes(foreign)
            raise OSError("injected foreign replacement")
        original_write(destination, text, **kwargs)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", replace_then_fail)

    with pytest.raises(OSError, match="foreign replacement"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
        )

    assert output.read_bytes() == foreign
    assert decision_output.read_bytes() == before_decision
    assert checkpoint.read_bytes() == before_checkpoint

def test_holdout_canonicalizes_carrier_paths_before_snapshot_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a missing ``component/..`` making rollback delete existing evidence."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    canonical = tmp_path / "replay.json"
    canonical.write_bytes(b"prior replay evidence\n")
    decision = tmp_path / "decision.json"
    original_write = holdout_runtime_module.atomic_write_text

    def fail_decision(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == decision:
            raise OSError("injected decision failure")
        original_write(destination, text, **kwargs)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_decision)

    with pytest.raises(OSError, match="injected decision failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "missing" / ".." / "replay.json",
            decision_output_path=decision,
        )

    assert canonical.read_bytes() == b"prior replay evidence\n"
    assert not (tmp_path / "missing").exists()

def test_holdout_uses_one_canonical_carrier_identity_after_lock_acquisition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches relative outputs resolving to different lock and write paths."""

    repository = tmp_path / "repository"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    _install_holdout_contract(repository)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    original_lock = holdout_runtime_module._artifact_bundle_lock
    locked: tuple[Path, ...] = ()

    @contextmanager
    def change_directory_after_lock(
        root: Path,
        carriers: tuple[Path, ...],
    ) -> Iterator[None]:
        nonlocal locked
        with original_lock(root, carriers):
            locked = carriers
            monkeypatch.chdir(second_cwd)
            try:
                yield
            finally:
                monkeypatch.chdir(first_cwd)

    monkeypatch.chdir(first_cwd)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_artifact_bundle_lock",
        change_directory_after_lock,
    )

    generate_future_holdout_replay(
        repository_root=repository,
        account_path=repository / "account.json",
        output_path="replay.json",
        decision_output_path="decision.json",
    )

    assert locked[:1] == (first_cwd / "replay.json",)
    assert (first_cwd / "replay.json").is_file()
    assert (first_cwd / "decision.json").is_file()
    assert not (second_cwd / "replay.json").exists()
    assert not (second_cwd / "decision.json").exists()

def test_holdout_rollback_does_not_overwrite_a_foreign_toctou_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches foreign bytes installed after rollback's ownership read."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    output = tmp_path / "replay.json"
    decision = tmp_path / "decision.json"
    original_read = holdout_runtime_module._read_protected_artifact
    original_write = holdout_runtime_module.atomic_write_text
    armed = False
    foreign = b"foreign carrier generation\n"

    def fail_decision(destination: str | Path, text: str, **kwargs: object) -> None:
        nonlocal armed
        if Path(destination) == decision:
            armed = True
            raise OSError("injected decision failure")
        original_write(destination, text, **kwargs)

    def replace_after_ownership_read(path: Path, *, label: str) -> bytes:
        current = original_read(path, label=label)
        if (
            armed
            and label == "future holdout rollback artifact"
            and (path == output or path.name.startswith(f".{output.name}.claimed-"))
        ):
            output.write_bytes(foreign)
        return current

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_decision)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_read_protected_artifact",
        replace_after_ownership_read,
    )

    with pytest.raises(OSError, match="injected decision failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision,
        )

    assert output.read_bytes() == foreign

def test_holdout_cleanup_preserves_primary_failure_and_continues_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches one rollback failure masking the write error and stopping later restores."""

    _install_holdout_contract(tmp_path)
    first = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision = tmp_path / "artifacts/decision.json"
    checkpoint = tmp_path / "artifacts/future_holdout_checkpoint.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision,
    )
    before_decision = decision.read_bytes()
    contract = load_future_holdout_contract()
    digest, record = _decision_record(contract.review_sessions[1])
    second = json.loads(json.dumps(first))
    second["sessions"].append(contract.review_sessions[1])
    second["decision_digests"].append(digest)
    second["decisions"].append(record)
    second["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in second.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: second,
    )
    original_write = holdout_runtime_module.atomic_write_text
    original_restore = holdout_runtime_module._restore_owned_artifact

    def fail_checkpoint(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == checkpoint:
            raise OSError("primary checkpoint failure")
        original_write(destination, text, **kwargs)

    def fail_first_restore(
        destination: Path,
        payload: bytes | None,
        expected: bytes,
        *,
        mode: int | None = None,
    ) -> None:
        if destination == output:
            raise OSError("secondary rollback failure")
        original_restore(destination, payload, expected, mode=mode)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_checkpoint)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_restore_owned_artifact",
        fail_first_restore,
    )

    with pytest.raises(OSError, match="primary checkpoint failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision,
        )

    assert decision.read_bytes() == before_decision
