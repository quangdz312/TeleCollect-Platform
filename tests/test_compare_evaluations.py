from __future__ import annotations

import json
from pathlib import Path

from scripts.compare_evaluations import compare_files


def _write(path: Path, episodes: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"episodes": episodes}), encoding="utf-8")


def test_compare_reports_first_action_divergence(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    common = {
        "seed": 5000,
        "success": True,
        "steps": 2,
        "initial_state_hash": "same",
        "action_hash": "different-a",
    }
    _write(first, [{**common, "action_prefix": [[0.0, 0.0], [0.1, 0.0]]}])
    _write(second, [{**common, "action_hash": "different-b", "action_prefix": [[1e-8, 0.0], [0.2, 0.0]]}])

    row = compare_files(first, second, tolerance=1e-6)[0]

    assert row["initial_state_same"] is True
    assert row["action_hash_same"] is False
    assert row["first_divergence_step"] == 1
    assert row["prefix_max_abs_error"] == 0.1
