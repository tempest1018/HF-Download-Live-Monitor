"""Streaming SHA-256 verification for completed download files."""

from __future__ import annotations

import hashlib
import re
import stat as stat_module
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from hf_download_live_monitor.models import FileIdentity, FileState
from hf_download_live_monitor.security import redact_text

_DEFAULT_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Return the lowercase SHA-256 digest while reading bounded chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationResult:
    path: Path
    identity: FileIdentity | None
    expected: str | None
    actual: str | None
    state: FileState
    error: str | None = None


_Key = tuple[Path, FileIdentity, str]


class IntegrityVerifier:
    """Bounded, deduplicating background verifier."""

    def __init__(self, max_workers: int = 1) -> None:
        if max_workers <= 0:
            raise ValueError("max workers must be positive")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="integrity")
        self._lock = threading.Lock()
        self._futures: dict[_Key, Future[VerificationResult]] = {}
        self._cache: dict[_Key, VerificationResult] = {}
        self._closed = False

    def request(self, path: Path, expected: str | None) -> VerificationResult:
        resolved = path.resolve()
        digest = _validate_digest(expected)
        with self._lock:
            if self._closed:
                raise RuntimeError("integrity verifier is closed")
        identity, failure = _identity_or_failure(resolved, digest)
        if failure is not None:
            return failure
        assert identity is not None
        if digest is None:
            return VerificationResult(resolved, identity, None, None, FileState.COMPLETE_UNVERIFIED)
        key = (resolved, identity, digest)
        with self._lock:
            if self._closed:
                raise RuntimeError("integrity verifier is closed")
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            future = self._futures.get(key)
            if future is None:
                future = self._executor.submit(self._verify, resolved, identity, digest)
                self._futures[key] = future
                return VerificationResult(resolved, identity, digest, None, FileState.SIZE_MATCHED)
            if not future.done():
                return VerificationResult(resolved, identity, digest, None, FileState.VERIFYING)
        return self._reconcile(key, future)

    def verify_now(self, path: Path, expected: str | None) -> VerificationResult:
        result = self.request(path, expected)
        if result.state not in {FileState.SIZE_MATCHED, FileState.VERIFYING}:
            return result
        assert result.identity is not None and result.expected is not None
        key = (result.path, result.identity, result.expected)
        with self._lock:
            future = self._futures[key]
        return self._reconcile(key, future)

    def _reconcile(self, key: _Key, future: Future[VerificationResult]) -> VerificationResult:
        result = future.result()
        with self._lock:
            self._futures.pop(key, None)
            self._cache[key] = result
        return result

    @staticmethod
    def _verify(path: Path, before: FileIdentity, expected: str) -> VerificationResult:
        try:
            actual = sha256_file(path)
            after = _identity(path)
            if after != before:
                return VerificationResult(
                    path,
                    after,
                    expected,
                    actual,
                    FileState.FAILED,
                    "file changed during verification",
                )
            state = FileState.VERIFIED if actual == expected else FileState.FAILED
            return VerificationResult(path, before, expected, actual, state)
        except (OSError, ValueError) as exc:
            return VerificationResult(
                path, before, expected, None, FileState.FAILED, redact_text(str(exc))
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True)

    def __enter__(self) -> IntegrityVerifier:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _validate_digest(expected: str | None) -> str | None:
    if expected is None:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
        raise ValueError("expected digest must be exactly 64 hexadecimal characters")
    return expected.lower()


def _identity(path: Path) -> FileIdentity:
    result = path.stat()
    if not stat_module.S_ISREG(result.st_mode):
        raise OSError("verification target is not a regular file")
    return FileIdentity(result.st_size, result.st_mtime_ns)


def _identity_or_failure(
    path: Path, expected: str | None
) -> tuple[FileIdentity | None, VerificationResult | None]:
    try:
        return _identity(path), None
    except (OSError, ValueError) as exc:
        return None, VerificationResult(
            path, None, expected, None, FileState.FAILED, redact_text(str(exc))
        )
