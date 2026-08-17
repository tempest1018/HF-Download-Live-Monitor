"""Streaming SHA-256 verification for completed download files."""

from __future__ import annotations

import hashlib
import re
import stat as stat_module
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
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
        self._lock = threading.RLock()
        self._futures: dict[_Key, Future[VerificationResult]] = {}
        self._cache: dict[_Key, VerificationResult] = {}
        self._latest: dict[tuple[Path, str], _Key] = {}
        self._closed = False

    def request(self, path: Path, expected: str | None) -> VerificationResult:
        result, key, future = self._request(path, expected)
        if key is not None and future is not None and future.done():
            return self._reconcile(key, future)
        return result

    def _request(
        self, path: Path, expected: str | None
    ) -> tuple[VerificationResult, _Key | None, Future[VerificationResult] | None]:
        digest = _validate_digest(expected)
        with self._lock:
            if self._closed:
                raise RuntimeError("integrity verifier is closed")
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            return (
                VerificationResult(
                    Path("<unresolved>"),
                    None,
                    digest,
                    None,
                    FileState.FAILED,
                    redact_text(str(exc)),
                ),
                None,
                None,
            )
        identity, failure = _identity_or_failure(resolved, digest)
        if failure is not None:
            return failure, None, None
        assert identity is not None
        if digest is None:
            return (
                VerificationResult(resolved, identity, None, None, FileState.COMPLETE_UNVERIFIED),
                None,
                None,
            )
        key = (resolved, identity, digest)
        created = False
        with self._lock:
            if self._closed:
                raise RuntimeError("integrity verifier is closed")
            cached = self._cache.get(key)
            if cached is not None:
                return cached, None, None
            future = self._futures.get(key)
            if future is None:
                self._latest[(resolved, digest)] = key
                self._evict_superseded_locked(key)
                future = self._executor.submit(self._verify, resolved, identity, digest)
                self._futures[key] = future
                created = True
        if created:
            future.add_done_callback(lambda completed: self._complete(key, completed))
            state = FileState.SIZE_MATCHED
        else:
            state = FileState.VERIFYING
        return VerificationResult(resolved, identity, digest, None, state), key, future

    def verify_now(self, path: Path, expected: str | None) -> VerificationResult:
        result, key, future = self._request(path, expected)
        if key is None or future is None:
            return result
        return self._reconcile(key, future)

    def _reconcile(self, key: _Key, future: Future[VerificationResult]) -> VerificationResult:
        result = future.result()
        self._record_completion(key, future, result)
        return result

    def _complete(self, key: _Key, future: Future[VerificationResult]) -> None:
        try:
            result = future.result()
        except CancelledError:
            with self._lock:
                if self._futures.get(key) is future:
                    self._futures.pop(key, None)
            return
        except Exception as exc:
            result = VerificationResult(
                key[0], key[1], key[2], None, FileState.FAILED, redact_text(str(exc))
            )
        self._record_completion(key, future, result)

    def _record_completion(
        self, key: _Key, future: Future[VerificationResult], result: VerificationResult
    ) -> None:
        with self._lock:
            if self._futures.get(key) is future:
                self._futures.pop(key, None)
            family = (key[0], key[2])
            if self._latest.get(family) == key:
                self._evict_cached_locked(key)
                self._cache[key] = result

    def _evict_superseded_locked(self, current: _Key) -> None:
        self._evict_cached_locked(current)
        for key, future in tuple(self._futures.items()):
            if _same_family(key, current) and key != current and future.cancel():
                self._futures.pop(key, None)

    def _evict_cached_locked(self, current: _Key) -> None:
        for key in tuple(self._cache):
            if _same_family(key, current) and key != current:
                self._cache.pop(key, None)

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


def _same_family(left: _Key, right: _Key) -> bool:
    return left[0] == right[0] and left[2] == right[2]


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
