import json
from pathlib import Path

from local_app.workspace import LocalWorkspace


def test_workspace_reads_episode_and_keeps_review_state_outside_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    workspace_root = tmp_path / "recordings"
    episode = workspace_root / "episodes" / "episode-a"
    episode.mkdir(parents=True)
    (episode / "meta.json").write_text(json.dumps({
        "episode_id": "episode-a", "task_name": "lift_cube", "task_success": True,
        "num_steps": 12, "duration_s": 0.4,
    }), encoding="utf-8")

    workspace = LocalWorkspace(workspace_root)
    records = workspace.episodes()

    assert len(records) == 1
    assert records[0].id == "episode-a"
    assert records[0].source == "manual"
    workspace.review(["episode-a"], "accepted", "looks good")

    assert workspace.decision_for("episode-a") == "accepted"
    assert workspace.note_for("episode-a") == "looks good"
    assert not (workspace_root / "app.db").exists()
    assert workspace.annotation_path.is_file()
    assert workspace.annotation_path.parent != workspace_root
