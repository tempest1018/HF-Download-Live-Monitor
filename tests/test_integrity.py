import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hf_download_live_monitor.integrity import IntegrityVerifier, sha256_file
from hf_download_live_monitor.models import FileState

SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_file_streams_and_validates_chunk_size(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path, 1) == SHA_ABC
    with pytest.raises(ValueError, match="chunk"):
        sha256_file(path, 0)


@pytest.mark.parametrize(
    "expected,state", [(SHA_ABC, FileState.VERIFIED), ("0" * 64, FileState.FAILED)]
)
def test_verify_now_match_and_mismatch(tmp_path: Path, expected: str, state: FileState) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, expected)
    assert result.state is state
    assert result.actual == SHA_ABC


def test_missing_digest_is_complete_unverified_without_reading(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, None)
    assert result.state is FileState.COMPLETE_UNVERIFIED
    assert result.actual is None


def test_missing_file_without_digest_fails(tmp_path: Path) -> None:
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(tmp_path / "missing.bin", None)
    assert result.state is FileState.FAILED


def test_invalid_configuration_and_digest() -> None:
    with pytest.raises(ValueError, match="workers"):
        IntegrityVerifier(0)
    with IntegrityVerifier() as verifier, pytest.raises(ValueError, match="digest"):
        verifier.verify_now(Path("file"), "bad")


def test_request_is_nonblocking_deduplicated_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    import hf_download_live_monitor.integrity as integrity

    real = integrity.sha256_file
    gate = threading.Event()
    calls = 0

    def slow(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        nonlocal calls
        calls += 1
        gate.wait(2)
        return real(candidate, chunk_size)

    monkeypatch.setattr(integrity, "sha256_file", slow)
    with IntegrityVerifier() as verifier:
        started = time.monotonic()
        first = verifier.request(path, SHA_ABC)
        second = verifier.request(path, SHA_ABC)
        assert time.monotonic() - started < 0.5
        assert first.state in {FileState.SIZE_MATCHED, FileState.VERIFYING}
        assert second.state is FileState.VERIFYING
        assert calls == 1
        gate.set()
        assert verifier.verify_now(path, SHA_ABC).state is FileState.VERIFIED
        assert verifier.request(path, SHA_ABC).state is FileState.VERIFIED
        assert calls == 1


def test_changed_identity_invalidates_cache(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    with IntegrityVerifier() as verifier:
        assert verifier.verify_now(path, SHA_ABC).state is FileState.VERIFIED
        path.write_bytes(b"abcd")
        assert verifier.verify_now(path, SHA_ABC).state is FileState.FAILED


def test_file_change_during_hash_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    import hf_download_live_monitor.integrity as integrity

    def changing(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        candidate.write_bytes(b"abcd")
        return SHA_ABC

    monkeypatch.setattr(integrity, "sha256_file", changing)
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, SHA_ABC)
    assert result.state is FileState.FAILED
    assert result.error is not None


def test_read_error_is_safe_failure(tmp_path: Path) -> None:
    path = tmp_path / "hf_secret_token.bin"
    path.mkdir()
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, SHA_ABC)
    assert result.state is FileState.FAILED
    assert "hf_secret" not in (result.error or "")


def test_hash_read_error_is_redacted_and_result_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    import hf_download_live_monitor.integrity as integrity

    def denied(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        raise OSError("denied for Bearer hf_secret_token")

    monkeypatch.setattr(integrity, "sha256_file", denied)
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, SHA_ABC)
    assert result.state is FileState.FAILED
    assert "hf_secret" not in (result.error or "")
    with pytest.raises(FrozenInstanceError):
        result.state = FileState.VERIFIED  # type: ignore[misc]


def test_close_is_idempotent_and_request_after_close_fails(tmp_path: Path) -> None:
    verifier = IntegrityVerifier()
    verifier.close()
    verifier.close()
    with pytest.raises(RuntimeError, match="closed"):
        verifier.request(tmp_path / "file", SHA_ABC)


def test_worker_pool_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = (tmp_path / "one", tmp_path / "two")
    for path in paths:
        path.write_bytes(b"abc")
    import hf_download_live_monitor.integrity as integrity

    gate = threading.Event()
    active = 0
    peak = 0
    lock = threading.Lock()

    def measured(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        gate.wait(2)
        with lock:
            active -= 1
        return SHA_ABC

    monkeypatch.setattr(integrity, "sha256_file", measured)
    with IntegrityVerifier(max_workers=1) as verifier:
        verifier.request(paths[0], SHA_ABC)
        verifier.request(paths[1], SHA_ABC)
        time.sleep(0.02)
        assert peak == 1
        gate.set()


def test_verify_now_survives_reconciliation_after_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    verifier = IntegrityVerifier()
    original_request = verifier._request

    def reconciled_request(candidate: Path, expected: str | None):
        result, key, future = original_request(candidate, expected)
        if key is not None and future is not None:
            with ThreadPoolExecutor(max_workers=1) as reconciler:
                reconciler.submit(verifier._reconcile, key, future).result()
        return result, key, future

    monkeypatch.setattr(verifier, "_request", reconciled_request)
    try:
        result = verifier.verify_now(path, SHA_ABC)
    finally:
        verifier.close()
    assert result.state is FileState.VERIFIED


def test_completed_request_reconciles_without_exact_rerequest(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    with IntegrityVerifier() as verifier:
        verifier.request(path, SHA_ABC)
        deadline = time.monotonic() + 2
        while verifier._futures and time.monotonic() < deadline:
            time.sleep(0.01)
        assert verifier._futures == {}
        assert len(verifier._cache) == 1


def test_replacement_identities_leave_bounded_completed_state(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    with IntegrityVerifier() as verifier:
        for index in range(10):
            path.write_bytes(f"value-{index}".encode())
            verifier.verify_now(path, "0" * 64)
        assert verifier._futures == {}
        assert len(verifier._cache) == 1


def test_queued_replacement_identities_are_bounded_without_cancelling_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"first")
    import hf_download_live_monitor.integrity as integrity

    started = threading.Event()
    release = threading.Event()
    real = integrity.sha256_file

    def blocked(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        started.set()
        release.wait(2)
        return real(candidate, chunk_size)

    monkeypatch.setattr(integrity, "sha256_file", blocked)
    with IntegrityVerifier(max_workers=1) as verifier:
        verifier.request(path, "0" * 64)
        assert started.wait(1)
        for index in range(8):
            path.write_bytes(f"replacement-{index}".encode())
            verifier.request(path, "0" * 64)
            assert len(verifier._futures) <= 2
        assert sum(future.running() for future in verifier._futures.values()) == 1
        release.set()


@pytest.mark.parametrize("error", [OSError("token=hf_secret"), RuntimeError("symlink loop")])
def test_resolve_error_returns_redacted_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path = tmp_path / "token=hf_secret"

    def broken_resolve(self: Path, strict: bool = False) -> Path:
        raise error

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, SHA_ABC)
    assert result.state is FileState.FAILED
    assert "hf_secret" not in str(result.path)
    assert "hf_secret" not in (result.error or "")


def test_verify_now_redacts_unexpected_worker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"abc")
    import hf_download_live_monitor.integrity as integrity

    def broken_hash(candidate: Path, chunk_size: int = 1024 * 1024) -> str:
        raise RuntimeError("worker failed with Bearer hf_secret")

    monkeypatch.setattr(integrity, "sha256_file", broken_hash)
    with IntegrityVerifier() as verifier:
        result = verifier.verify_now(path, SHA_ABC)
    assert result.state is FileState.FAILED
    assert "hf_secret" not in (result.error or "")
