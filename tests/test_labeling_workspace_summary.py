from __future__ import annotations

import json
from pathlib import Path

from src.labeling.workspace import Workspace


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_summary_breaks_review_progress_down_by_task(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    _write_jsonl(workspace.scores_path, [
        {"episode_id": "lift::0", "task": "lift", "recorded_success": True, "scorer_version": "v"},
        {"episode_id": "lift::1", "task": "lift", "recorded_success": False, "scorer_version": "v"},
        {"episode_id": "can::0", "task": "can", "recorded_success": True, "scorer_version": "v"},
    ])
    _write_jsonl(workspace.labels_path, [
        {"episode_id": "lift::0", "human_decision": "approved"},
        {"episode_id": "lift::1", "human_decision": "rejected"},
    ])

    summary = workspace.summary()

    assert summary["per_task"]["lift"] == {
        "total": 2,
        "reviewed": 2,
        "pending": 0,
        "approved": 1,
        "approved_successes": 1,
        "rejected": 1,
    }
    assert summary["per_task"]["can"]["pending"] == 1
    assert summary["per_task"]["can"]["approved_successes"] == 0
