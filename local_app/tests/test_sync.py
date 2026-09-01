"""Signing the desktop app into the shared server, and pushing a batch to it.

The app already signs itself into its own database as `local-desktop`. This is a
second, separate session against the team's server, so the tests worth having
are about keeping the two apart and about what is allowed to touch the disk.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from local_app import sync

PASSWORD = "correct-horse-battery-staple"


def _seed_review(workspace: Path, batch_id: str, *, demos: int = 1, name: str = "run.hdf5") -> Path:
    """Dựng kho `review/datasets` như màn hình thu dữ liệu ghi ra.

    Đây mới là chỗ tập của app nằm; `pack_batch` đọc ở đây chứ không đọc
    `batches/<tên>/episodes/`, nên fixture phải dựng đúng cái thật.
    """

    import h5py
    import numpy as np

    datasets = workspace / "review" / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    path = datasets / name
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        data.attrs["telecollect_collection_batch_id"] = batch_id
        data.attrs["telecollect_task"] = "lift"
        for index in range(demos):
            demo = data.create_group(f"demo_{index}")
            demo.attrs["telecollect_collection_batch_id"] = batch_id
            demo.attrs["num_samples"] = 2
            demo.attrs["success"] = True
            demo.create_dataset("actions", data=np.zeros((2, 7), dtype="float32"))
    return path


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Keep every test out of the real app settings file."""

    store: dict[str, object] = {}
    monkeypatch.setattr(sync, "_load_settings", lambda: dict(store))

    def save(values):
        store.clear()
        store.update(values)

    monkeypatch.setattr(sync, "_save_settings", save)
    return store


def _fake_request(monkeypatch, handler):
    monkeypatch.setattr(sync, "_request", handler)


# --- addresses ---------------------------------------------------------------


def test_a_bare_hostname_is_accepted():
    assert sync._normalise("telecollect.io.vn") == "https://telecollect.io.vn"
    assert sync._normalise("https://telecollect.io.vn/") == "https://telecollect.io.vn"
    assert sync._normalise("http://192.168.1.5:8000") == "http://192.168.1.5:8000"


def test_an_empty_address_is_refused():
    with pytest.raises(sync.SyncError, match="địa chỉ máy chủ"):
        sync._normalise("   ")


def test_the_project_server_is_the_default():
    """Giao diện không còn hỏi địa chỉ, nên hằng số này là thứ duy nhất trỏ đường.

    Gõ sai một lần là đăng nhập hỏng mà không rõ vì sao, và người thu dữ liệu
    không có lý do gì phải biết địa chỉ máy chủ của dự án.
    """

    assert sync.DEFAULT_SERVER == "https://telecollect.io.vn"


# --- sign in -----------------------------------------------------------------


def test_signing_in_stores_tokens_and_never_the_password(_isolated_settings, monkeypatch):
    def handler(url, **kwargs):
        if url.endswith("/auth/login"):
            return {"access_token": "access-1", "refresh_token": "refresh-1"}
        return {"username": "tung", "role": "operator"}

    _fake_request(monkeypatch, handler)

    session = sync.sign_in("telecollect.io.vn", "tung", PASSWORD)

    assert session.username == "tung"
    assert session.role == "operator"
    assert _isolated_settings["sync_token"] == "access-1"
    # The whole point: a password on disk works everywhere that account works.
    stored = json.dumps(_isolated_settings)
    assert PASSWORD not in stored


def test_a_rejected_login_leaves_no_session(_isolated_settings, monkeypatch):
    def handler(url, **kwargs):
        raise sync.SyncError("Sai tên đăng nhập hoặc mật khẩu")

    _fake_request(monkeypatch, handler)

    with pytest.raises(sync.SyncError):
        sync.sign_in("telecollect.io.vn", "tung", "wrong")
    assert sync.current_session() is None


def test_signing_out_removes_the_tokens(_isolated_settings, monkeypatch):
    _fake_request(
        monkeypatch,
        lambda url, **kw: {"access_token": "a", "refresh_token": "r"}
        if url.endswith("/auth/login")
        else {"username": "tung", "role": "operator"},
    )
    sync.sign_in("telecollect.io.vn", "tung", PASSWORD)

    sync.sign_out()

    assert sync.current_session() is None
    assert "sync_token" not in _isolated_settings


def test_a_half_written_session_is_treated_as_signed_out(_isolated_settings):
    """A server address alone is not a session."""

    _isolated_settings.update({"sync_server": "https://x", "sync_username": "tung"})

    assert sync.current_session() is None


# --- packing -----------------------------------------------------------------


def test_packing_produces_the_layout_the_import_endpoint_expects(tmp_path):
    workspace = tmp_path / "ws"
    batch = {"id": "lift-v1", "name": "Lift v1", "task": "lift"}
    _seed_review(workspace, "lift-v1")

    archive = sync.pack_batch(workspace, batch, tmp_path / "out.zip")

    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
        meta = json.loads(handle.read("episodes/run__demo_0/meta.json"))
    assert "batch.json" in names
    assert "episodes/run__demo_0/trajectory.hdf5" in names
    # Máy chủ nhóm các tập theo hai trường này; thiếu chúng thì tập bị bỏ qua.
    assert meta["original_source"] == "run.hdf5"
    assert meta["original_demo"] == "demo_0"


def test_packing_takes_only_the_episodes_of_that_batch(tmp_path):
    """Một file chung kho không được kéo theo tập của đợt thu khác.

    Mọi đợt thu dùng chung `review/datasets`, nên lọc theo mã đợt thu là thứ duy
    nhất giữ cho gói đúng phạm vi.
    """

    workspace = tmp_path / "ws"
    _seed_review(workspace, "lift-v1", demos=2, name="mine.hdf5")
    _seed_review(workspace, "other-v9", demos=3, name="theirs.hdf5")

    archive = sync.pack_batch(
        workspace, {"id": "lift-v1", "name": "Lift v1", "task": "lift"}, tmp_path / "o.zip",
    )

    with zipfile.ZipFile(archive) as handle:
        episodes = {name.split("/")[1] for name in handle.namelist() if name.startswith("episodes/")}
    assert episodes == {"mine__demo_0", "mine__demo_1"}


def test_packing_a_batch_with_no_episodes_says_so(tmp_path):
    with pytest.raises(sync.SyncError, match="chưa có tập nào"):
        sync.pack_batch(tmp_path, {"id": "x", "name": "Missing"}, tmp_path / "o.zip")


# --- uploading ---------------------------------------------------------------


def _prepare(tmp_path, settings) -> tuple[Path, dict]:
    workspace = tmp_path / "ws"
    batch = {"id": "lift-v1", "name": "Lift v1"}
    _seed_review(workspace, "lift-v1")
    settings.update(
        {
            "sync_server": "https://telecollect.io.vn",
            "sync_username": "tung",
            "sync_role": "operator",
            "sync_token": "expired",
            "sync_refresh": "refresh-1",
        }
    )
    return workspace, batch


def test_uploading_without_a_session_is_refused(tmp_path):
    with pytest.raises(sync.SyncError, match="Chưa đăng nhập"):
        sync.upload_batch(tmp_path, {"id": "lift-v1", "name": "Lift v1"})


def test_an_expired_token_is_refreshed_and_the_upload_retried(
    tmp_path, _isolated_settings, monkeypatch,
):
    workspace, batch = _prepare(tmp_path, _isolated_settings)
    seen: list[str] = []

    def handler(url, *, headers=None, **kwargs):
        if url.endswith("/auth/refresh"):
            return {"access_token": "fresh", "refresh_token": "refresh-2"}
        token = (headers or {}).get("Authorization", "")
        seen.append(token)
        if token == "Bearer expired":
            raise sync.SyncError("Máy chủ từ chối: đăng nhập lại", status=401)
        return {"episodes": 2, "videos": 2, "sources": [], "skipped": []}

    _fake_request(monkeypatch, handler)

    result = sync.upload_batch(workspace, batch)

    assert result["episodes"] == 2
    assert seen == ["Bearer expired", "Bearer fresh"]


def test_a_failed_refresh_reports_the_original_error(
    tmp_path, _isolated_settings, monkeypatch,
):
    """Retrying the same dead token would only fail the same way."""

    workspace, batch = _prepare(tmp_path, _isolated_settings)
    attempts: list[str] = []

    def handler(url, *, headers=None, **kwargs):
        if url.endswith("/auth/refresh"):
            raise sync.SyncError("refresh token hết hạn")
        attempts.append(url)
        raise sync.SyncError("Máy chủ từ chối: đăng nhập lại", status=401)

    _fake_request(monkeypatch, handler)

    with pytest.raises(sync.SyncError, match="từ chối"):
        sync.upload_batch(workspace, batch)
    assert len(attempts) == 1


def test_a_rejected_archive_is_not_uploaded_a_second_time(
    tmp_path, _isolated_settings, monkeypatch,
):
    """422 không phải token hết hạn, nên đừng đẩy lại cả gói."""

    workspace, batch = _prepare(tmp_path, _isolated_settings)
    attempts: list[str] = []

    def handler(url, *, headers=None, **kwargs):
        if url.endswith("/auth/refresh"):
            return {"access_token": "fresh", "refresh_token": "refresh-2"}
        attempts.append(url)
        raise sync.SyncError("Đợt thu này đã có trên máy chủ", status=422)

    _fake_request(monkeypatch, handler)

    with pytest.raises(sync.SyncError, match="đã có trên máy chủ"):
        sync.upload_batch(workspace, batch)
    assert len(attempts) == 1


def test_the_temporary_archive_is_removed_even_when_the_upload_fails(
    tmp_path, _isolated_settings, monkeypatch,
):
    workspace, batch = _prepare(tmp_path, _isolated_settings)
    _fake_request(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(sync.SyncError("nope")))

    with pytest.raises(sync.SyncError):
        sync.upload_batch(workspace, batch)

    assert list((workspace / ".sync").glob("*.zip")) == []
