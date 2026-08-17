"""Public Hugging Face Hub repository metadata adapter."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Protocol, cast

from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    ManifestFile,
    MonitorError,
    RepoType,
)
from hf_download_live_monitor.security import redact_text
from hf_download_live_monitor.selection import select_manifest


class HubApi(Protocol):
    def model_info(self, repo_id: str, **kwargs: Any) -> Any: ...

    def dataset_info(self, repo_id: str, **kwargs: Any) -> Any: ...

    def space_info(self, repo_id: str, **kwargs: Any) -> Any: ...


def _response_status(exc: object) -> int | None:
    response: Any = getattr(exc, "response", None)
    status: Any = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class HubRepository:
    def __init__(self, api: HubApi | None = None) -> None:
        self._api: HubApi = cast(HubApi, api if api is not None else HfApi())

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        return self.prepare(spec).manifest

    def prepare(self, spec: DownloadSpec) -> DownloadPlan:
        try:
            info = self._repository_info(spec)
        except GatedRepoError as exc:
            raise self._error("gated_repository", exc, ErrorCategory.ACCESS) from exc
        except RepositoryNotFoundError as exc:
            status = _response_status(exc)
            if status == 401:
                raise self._error("authentication_required", exc, ErrorCategory.ACCESS) from exc
            if status == 403:
                raise self._error("access_denied", exc, ErrorCategory.ACCESS) from exc
            raise self._error("repository_not_found", exc, ErrorCategory.REPOSITORY) from exc
        except HfHubHTTPError as exc:
            status = _response_status(exc)
            if status == 401:
                raise self._error("authentication_required", exc, ErrorCategory.ACCESS) from exc
            if status == 403:
                raise self._error("access_denied", exc, ErrorCategory.ACCESS) from exc
            code = "rate_limited" if status == 429 else "hub_error"
            recoverable = status == 429 or (status is not None and status >= 500)
            raise self._error(code, exc, ErrorCategory.REPOSITORY, recoverable) from exc

        resolved_revision = getattr(info, "sha", None)
        if (
            not isinstance(resolved_revision, str)
            or re.fullmatch(r"[0-9a-fA-F]{40}", resolved_revision) is None
        ):
            raise MonitorError(
                "invalid_resolved_revision",
                "the Hub returned no valid immutable repository revision",
                category=ErrorCategory.REPOSITORY,
            )
        resolved_revision = resolved_revision.lower()

        files: list[ManifestFile] = []
        for sibling in getattr(info, "siblings", None) or ():
            filename = _metadata_value(sibling, "rfilename")
            size = _sibling_size(sibling)
            if filename and size is not None:
                files.append(
                    ManifestFile(
                        str(filename).replace("\\", "/"),
                        size,
                        _sibling_sha256(sibling),
                    )
                )
        if not files:
            raise MonitorError(
                "metadata_unavailable",
                "the Hub returned no file-size metadata",
                recoverable=True,
                category=ErrorCategory.REPOSITORY,
            )
        try:
            manifest = select_manifest(
                files, filenames=spec.filenames, includes=spec.includes, excludes=spec.excludes
            )
        except MonitorError as exc:
            raise MonitorError(
                exc.code,
                exc.message,
                exc.recoverable,
                ErrorCategory.REPOSITORY,
            ) from exc
        return DownloadPlan(
            spec=replace(spec, revision=resolved_revision),
            requested_revision=spec.revision,
            manifest=manifest,
        )

    def _repository_info(self, spec: DownloadSpec) -> Any:
        kwargs = {"revision": spec.revision, "files_metadata": True}
        if spec.repo_type is RepoType.DATASET:
            return self._api.dataset_info(spec.repo, **kwargs)
        if spec.repo_type is RepoType.SPACE:
            return self._api.space_info(spec.repo, **kwargs)
        return self._api.model_info(spec.repo, **kwargs)

    @staticmethod
    def _error(
        code: str,
        exc: Exception,
        category: ErrorCategory,
        recoverable: bool = False,
    ) -> MonitorError:
        return MonitorError(code, redact_text(str(exc)), recoverable, category)


def _sibling_size(sibling: object) -> int | None:
    direct = _metadata_value(sibling, "size")
    if isinstance(direct, (int, str)):
        return int(direct)
    lfs = _metadata_value(sibling, "lfs")
    size = _metadata_value(lfs, "size") if lfs is not None else None
    return int(size) if isinstance(size, (int, str)) else None


def _sibling_sha256(sibling: object) -> str | None:
    lfs = _metadata_value(sibling, "lfs")
    digest = _metadata_value(lfs, "sha256") if lfs is not None else None
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return digest.lower()
    return None


def _metadata_value(metadata: object, name: str) -> object | None:
    if isinstance(metadata, dict):
        return cast(dict[str, object], metadata).get(name)
    return getattr(metadata, name, None)
