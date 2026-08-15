from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hf_live_monitor.models import DownloadSpec, MonitorError, RepoType
from hf_live_monitor.repository import HubRepository


class FakeApi:
    def __init__(self, siblings: list[object]) -> None:
        self.siblings = siblings
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _response(self, method: str, repo_id: str, **kwargs: Any) -> object:
        self.calls.append((method, repo_id, kwargs))
        return SimpleNamespace(siblings=self.siblings)

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
