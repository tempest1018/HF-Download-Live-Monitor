from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
from requests import Response

from hf_download_live_monitor.errors import ErrorCategory
from hf_download_live_monitor.models import (
    DownloadPlan,
    DownloadSpec,
    ManifestFile,
    MonitorError,
    RepoType,
)
from hf_download_live_monitor.repository import HubRepository


class FakeApi:
    def __init__(self, siblings: list[object], *, sha: str | None = "a" * 40) -> None:
        self.siblings = siblings
        self.sha = sha
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _response(self, method: str, repo_id: str, **kwargs: Any) -> object:
        self.calls.append((method, repo_id, kwargs))
        return SimpleNamespace(siblings=self.siblings, sha=self.sha)

    def model_info(self, repo_id: str, **kwargs: Any) -> object:
        return self._response("model", repo_id, **kwargs)

    def dataset_info(self, repo_id: str, **kwargs: Any) -> object:
        return self._response("dataset", repo_id, **kwargs)

    def space_info(self, repo_id: str, **kwargs: Any) -> object:
        return self._response("space", repo_id, **kwargs)


@pytest.mark.parametrize(
    ("repo_type", "method"),
    [(RepoType.MODEL, "model"), (RepoType.DATASET, "dataset"), (RepoType.SPACE, "space")],
)
def test_manifest_uses_correct_public_api(repo_type: RepoType, method: str) -> None:
    api = FakeApi([SimpleNamespace(rfilename="file.bin", size=12, lfs=None)])
    spec = DownloadSpec("owner/repo", Path("out"), repo_type, revision="v1")

    manifest = HubRepository(api).manifest(spec)

    assert [(item.filename, item.expected_bytes) for item in manifest] == [("file.bin", 12)]
    assert api.calls == [(method, "owner/repo", {"revision": "v1", "files_metadata": True})]


def test_manifest_extracts_direct_and_lfs_sizes_and_skips_unknown() -> None:
    api = FakeApi(
        [
            SimpleNamespace(rfilename="direct", size=1, lfs=None),
            SimpleNamespace(rfilename="dict", size=None, lfs={"size": 2}),
            SimpleNamespace(rfilename="object", size=None, lfs=SimpleNamespace(size=3)),
            SimpleNamespace(rfilename="unknown", size=None, lfs=None),
        ]
    )
    manifest = HubRepository(api).manifest(DownloadSpec("owner/repo", Path("out")))
    assert [(item.filename, item.expected_bytes) for item in manifest] == [
        ("dict", 2),
        ("direct", 1),
        ("object", 3),
    ]


def test_manifest_rejects_response_without_sizes() -> None:
    api = FakeApi([SimpleNamespace(rfilename="unknown", size=None, lfs=None)])
    with pytest.raises(MonitorError) as caught:
        HubRepository(api).manifest(DownloadSpec("owner/repo", Path("out")))
    assert caught.value.code == "metadata_unavailable"


def test_manifest_applies_requested_file_filters() -> None:
    api = FakeApi(
        [
            SimpleNamespace(rfilename="a.bin", size=1, lfs=None),
            SimpleNamespace(rfilename="b.json", size=2, lfs=None),
        ]
    )
    spec = DownloadSpec("owner/repo", Path("out"), includes=("*.json",))
    assert [item.filename for item in HubRepository(api).manifest(spec)] == ["b.json"]


def test_prepare_classifies_missing_requested_file_as_repository_error() -> None:
    api = FakeApi([SimpleNamespace(rfilename="present.bin", size=1, lfs=None)])
    spec = DownloadSpec("owner/repo", Path("out"), filenames=("absent.bin",))

    with pytest.raises(MonitorError) as caught:
        HubRepository(api).prepare(spec)

    assert caught.value.code == "requested_file_missing"
    assert caught.value.category is ErrorCategory.REPOSITORY


def test_prepare_pins_requested_revision_to_resolved_sha() -> None:
    api = FakeApi([SimpleNamespace(rfilename="file.bin", size=12, lfs=None)])
    requested = DownloadSpec("owner/repo", Path("out"), revision="main")

    plan = HubRepository(api).prepare(requested)

    assert isinstance(plan, DownloadPlan)
    assert plan.requested_revision == "main"
    assert plan.spec.revision == "a" * 40
    assert requested.revision == "main"
    assert plan.manifest == (ManifestFile("file.bin", 12),)


def test_download_plan_rejects_blank_requested_revision() -> None:
    spec = DownloadSpec("owner/repo", Path("out"), revision="a" * 40)
    with pytest.raises(ValueError, match="requested revision"):
        DownloadPlan(spec, " ", ())


def test_manifest_file_normalizes_valid_sha256() -> None:
    file = ManifestFile("weights.bin", 1, "AB" * 32)
    assert file.sha256 == "ab" * 32


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64])
def test_manifest_file_rejects_invalid_sha256(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ManifestFile("weights.bin", 1, digest)


def test_prepare_extracts_lfs_sha256_from_dict_and_object() -> None:
    api = FakeApi(
        [
            SimpleNamespace(rfilename="dict", size=None, lfs={"size": 2, "sha256": "B" * 64}),
            SimpleNamespace(
                rfilename="object",
                size=None,
                lfs=SimpleNamespace(size=3, sha256="c" * 64),
            ),
        ]
    )
    manifest = HubRepository(api).prepare(DownloadSpec("owner/repo", Path("out"))).manifest
    assert [(item.filename, item.sha256) for item in manifest] == [
        ("dict", "b" * 64),
        ("object", "c" * 64),
    ]


def test_prepare_treats_invalid_adapter_lfs_digest_as_absent() -> None:
    api = FakeApi(
        [SimpleNamespace(rfilename="file", size=None, lfs={"size": 2, "sha256": "invalid"})]
    )
    manifest = HubRepository(api).prepare(DownloadSpec("owner/repo", Path("out"))).manifest
    assert manifest[0].sha256 is None


@pytest.mark.parametrize("sha", [None, "", "a" * 39, "g" * 40])
def test_prepare_rejects_missing_or_invalid_resolved_sha(sha: str | None) -> None:
    api = FakeApi([SimpleNamespace(rfilename="file", size=1, lfs=None)], sha=sha)
    with pytest.raises(MonitorError) as caught:
        HubRepository(api).prepare(DownloadSpec("owner/repo", Path("out")))
    assert caught.value.category is ErrorCategory.REPOSITORY
    assert caught.value.code == "invalid_resolved_revision"


class RaisingApi(FakeApi):
    def __init__(self, error: Exception) -> None:
        super().__init__([])
        self.error = error

    def model_info(self, repo_id: str, **kwargs: Any) -> object:
        raise self.error


def _http_error(error_type: type[HfHubHTTPError], status: int) -> HfHubHTTPError:
    response = Response()
    response.status_code = status
    response.url = "https://huggingface.co/owner/repo?token=hf_secret"
    return error_type("failed token=hf_secret", response=response)


@pytest.mark.parametrize(
    ("error", "category", "code", "recoverable"),
    [
        (_http_error(GatedRepoError, 403), ErrorCategory.ACCESS, "gated_repository", False),
        (
            _http_error(RepositoryNotFoundError, 401),
            ErrorCategory.ACCESS,
            "authentication_required",
            False,
        ),
        (
            _http_error(RepositoryNotFoundError, 403),
            ErrorCategory.ACCESS,
            "access_denied",
            False,
        ),
        (
            _http_error(RepositoryNotFoundError, 404),
            ErrorCategory.REPOSITORY,
            "repository_not_found",
            False,
        ),
        (_http_error(HfHubHTTPError, 401), ErrorCategory.ACCESS, "authentication_required", False),
        (_http_error(HfHubHTTPError, 403), ErrorCategory.ACCESS, "access_denied", False),
        (_http_error(HfHubHTTPError, 429), ErrorCategory.REPOSITORY, "rate_limited", True),
        (_http_error(HfHubHTTPError, 503), ErrorCategory.REPOSITORY, "hub_error", True),
    ],
)
def test_prepare_maps_hub_errors(
    error: Exception,
    category: ErrorCategory,
    code: str,
    recoverable: bool,
) -> None:
    with pytest.raises(MonitorError) as caught:
        HubRepository(RaisingApi(error)).prepare(DownloadSpec("owner/repo", Path("out")))
    assert caught.value.category is category
    assert caught.value.code == code
    assert caught.value.recoverable is recoverable
    assert "hf_secret" not in caught.value.message


def test_missing_file_metadata_is_repository_error() -> None:
    api = FakeApi([SimpleNamespace(rfilename="unknown", size=None, lfs=None)])
    with pytest.raises(MonitorError) as caught:
        HubRepository(api).prepare(DownloadSpec("owner/repo", Path("out")))
    assert caught.value.category is ErrorCategory.REPOSITORY
    assert caught.value.code == "metadata_unavailable"
