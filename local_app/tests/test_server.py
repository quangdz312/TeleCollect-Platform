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
