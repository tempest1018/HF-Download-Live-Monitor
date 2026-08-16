"""Public Hugging Face Hub repository metadata adapter."""

from __future__ import annotations

from typing import Any, Protocol, cast

from huggingface_hub import HfApi
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from hf_live_monitor.models import DownloadSpec, ManifestFile, MonitorError, RepoType
from hf_live_monitor.security import redact_text
from hf_live_monitor.selection import select_manifest


class HubApi(Protocol):
    def model_info(self, repo_id: str, **kwargs: Any) -> Any: ...

    def dataset_info(self, repo_id: str, **kwargs: Any) -> Any: ...

    def space_info(self, repo_id: str, **kwargs: Any) -> Any: ...


class HubRepository:
    def __init__(self, api: HubApi | None = None) -> None:
        self._api: HubApi = cast(HubApi, api if api is not None else HfApi())

    def manifest(self, spec: DownloadSpec) -> tuple[ManifestFile, ...]:
        try:
            info = self._repository_info(spec)
        except GatedRepoError as exc:
            raise self._error("authentication_required", exc) from exc
        except RepositoryNotFoundError as exc:
            raise self._error("repository_not_found", exc) from exc
        except HfHubHTTPError as exc:
            status = exc.response.status_code
            code = "rate_limited" if status == 429 else "hub_error"
            raise self._error(code, exc, recoverable=status == 429 or (status or 0) >= 500) from exc

        files: list[ManifestFile] = []
        for sibling in getattr(info, "siblings", None) or ():
            filename = getattr(sibling, "rfilename", None)
            size = _sibling_size(sibling)
            if filename and size is not None:
                files.append(ManifestFile(str(filename).replace("\\", "/"), size))
        if not files:
            raise MonitorError(
                "metadata_unavailable",
                "the Hub returned no file-size metadata",
                recoverable=True,
            )
        return select_manifest(
            files,
            filenames=spec.filenames,
            includes=spec.includes,
            excludes=spec.excludes,
        )

    def _repository_info(self, spec: DownloadSpec) -> Any:
        kwargs = {"revision": spec.revision, "files_metadata": True}
        if spec.repo_type is RepoType.DATASET:
            return self._api.dataset_info(spec.repo, **kwargs)
        if spec.repo_type is RepoType.SPACE:
            return self._api.space_info(spec.repo, **kwargs)
        return self._api.model_info(spec.repo, **kwargs)

    @staticmethod
    def _error(code: str, exc: Exception, recoverable: bool = False) -> MonitorError:
        return MonitorError(code, redact_text(str(exc)), recoverable)


def _sibling_size(sibling: object) -> int | None:
    direct = getattr(sibling, "size", None)
    if direct is not None:
        return int(direct)
    lfs = getattr(sibling, "lfs", None)
    size: object | None = (
        cast(dict[str, object], lfs).get("size")
        if isinstance(lfs, dict)
        else getattr(lfs, "size", None)
    )
    return int(size) if isinstance(size, (int, str)) else None
