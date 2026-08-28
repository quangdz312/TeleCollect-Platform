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
    root = workspace / "batches" / "Lift v1"
    (root / "episodes" / "ep1").mkdir(parents=True)
    (root / "batch.json").write_text(json.dumps(batch), encoding="utf-8")
    (root / "episodes" / "ep1" / "meta.json").write_text("{}", encoding="utf-8")

    archive = sync.pack_batch(workspace, batch, tmp_path / "out.zip")

    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert "batch.json" in names
    assert "episodes/ep1/meta.json" in names


def test_packing_a_missing_batch_folder_says_so(tmp_path):
    with pytest.raises(sync.SyncError, match="Không tìm thấy thư mục"):
        sync.pack_batch(tmp_path, {"id": "x", "name": "Missing"}, tmp_path / "o.zip")


# --- uploading ---------------------------------------------------------------


def _prepare(tmp_path, settings) -> tuple[Path, dict]:
    workspace = tmp_path / "ws"
    batch = {"id": "lift-v1", "name": "Lift v1"}
    root = workspace / "batches" / "Lift v1" / "episodes" / "ep1"
    root.mkdir(parents=True)
    (root / "meta.json").write_text("{}", encoding="utf-8")
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
            raise sync.SyncError("Máy chủ từ chối: đăng nhập lại")
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
        raise sync.SyncError("Máy chủ từ chối: đăng nhập lại")

    _fake_request(monkeypatch, handler)

    with pytest.raises(sync.SyncError, match="từ chối"):
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
