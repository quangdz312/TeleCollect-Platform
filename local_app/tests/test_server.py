from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_app.server import create_app


def test_local_app_lists_only_local_episodes(tmp_path: Path) -> None:
    episode = tmp_path / "episodes" / "lift-local-1"
    episode.mkdir(parents=True)
    (episode / "meta.json").write_text(json.dumps({
        "episode_id": "lift-local-1", "task_name": "lift_cube", "num_steps": 12, "duration_s": 0.4,
    }), encoding="utf-8")
    (episode / "actions.parquet").write_bytes(b"not-read-for-listing")
    client = TestClient(create_app(tmp_path))

    assert client.get("/").status_code == 200
    body = client.get("/api/episodes").json()
    assert body == [{
        "id": "lift-local-1", "task_name": "lift_cube", "num_steps": 12,
        "duration_s": 0.4, "has_video": False, "has_trajectory": True, "status": "active",
    }]
    assert client.patch("/api/episodes/lift-local-1", json={"status": "rejected"}).json()["status"] == "rejected"
    assert client.get("/api/episodes").json()[0]["status"] == "rejected"


def test_signing_in_without_an_address_uses_the_project_server(tmp_path: Path, monkeypatch) -> None:
    """Giao diện gửi đúng username và password, không gửi địa chỉ.

    Ràng buộc `min_length=1` cũ khiến chính thân request mà giao diện gửi bị
    trả 422 trước khi chạm tới `sign_in` — hỏng ngay ở bước đăng nhập.
    """

    from fastapi import FastAPI

    from local_app import local_api, sync

    seen: dict[str, str] = {}

    def fake_sign_in(server: str, username: str, password: str):
        seen.update(server=server, username=username)
        return sync.SyncSession(server=server, username=username, role="reviewer")

    monkeypatch.setattr(local_api.sync, "sign_in", fake_sign_in)
    app = FastAPI()
    local_api.install(app, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/v1/local/sync/login", json={"username": "local", "password": "12345678"},
    )

    assert response.status_code == 200, response.text
    assert seen["server"] == sync.DEFAULT_SERVER
    assert response.json()["username"] == "local"
