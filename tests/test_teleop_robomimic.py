from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.export.teleop_robomimic import load_teleop_episode
from src.services.robomimic_dataset_builder import build_robomimic_hdf5
from src.sim.collection.schema_validator import validate_training_dataset


def _teleop(root: Path, *, timestamps: list[float] | None = None) -> Path:
    root.mkdir()
    timestamps = timestamps or [0.0, 0.1, 0.2, 0.3]
    count = len(timestamps)
    qpos = np.arange(count * 9, dtype=np.float64).reshape(count, 9) / 100.0
    qvel = qpos + 0.5
    ee_pose = np.zeros((count, 7), dtype=np.float64)
    ee_pose[:, :3] = np.arange(count * 3).reshape(count, 3) / 10.0
    ee_pose[:, 3] = 1.0  # identity in MuJoCo wxyz order
    actions = np.arange(count * 7, dtype=np.float64).reshape(count, 7) / 10.0
    states = np.arange(count * 12, dtype=np.float64).reshape(count, 12)
    objects = np.arange(count * 10, dtype=np.float64).reshape(count, 10) / 100.0
    pq.write_table(
        pa.table({
            "t": timestamps,
            "qpos": qpos.tolist(),
            "qvel": qvel.tolist(),
            "ee_pose": ee_pose.tolist(),
            "privileged_state": states.tolist(),
            "object": objects.tolist(),
            "action": actions.tolist(),
        }),
        root / "actions.parquet",
    )
    (root / "meta.json").write_text(
        json.dumps({
            "episode_id": root.name,
            "task_name": "lift_cube",
            "operator_id": "operator-1",
            "num_steps": count,
            "control_hz": 10,
            "teleop_schema_version": 2,
            "task_success": True,
        }),
        encoding="utf-8",
    )
    return root


def test_manual_alignment_drops_unpaired_last_action(tmp_path: Path) -> None:
    root = _teleop(tmp_path / "episode-1")
    episode = load_teleop_episode(root)

    assert episode.num_samples == 3
    np.testing.assert_allclose(episode.actions[-1], np.arange(14, 21) / 10.0)
    np.testing.assert_allclose(
        episode.observations["robot0_eef_pos"][1],
        episode.next_observations["robot0_eef_pos"][0],
    )
    np.testing.assert_array_equal(
        episode.observations["robot0_eef_quat"][0], [0.0, 0.0, 0.0, 1.0]
    )
    np.testing.assert_array_equal(
        episode.observations["robot0_eef_quat_site"][0], [1.0, 0.0, 0.0, 0.0]
    )
    assert episode.observations["object"].shape == (3, 10)
    assert episode.rewards.tolist() == [0.0, 0.0, 1.0]
    assert episode.dones.tolist() == [0, 0, 1]


def test_manual_trim_is_applied_before_transition_alignment(tmp_path: Path) -> None:
    episode = load_teleop_episode(
        _teleop(tmp_path / "episode-1"), trim_start_s=0.1, trim_end_s=0.3,
    )

    assert episode.num_samples == 2
    np.testing.assert_allclose(episode.actions[0], np.arange(7, 14) / 10.0)
    assert episode.attrs["source_num_observations"] == 3


def test_manual_rejects_non_monotonic_timestamps(tmp_path: Path) -> None:
    root = _teleop(tmp_path / "episode-1", timestamps=[0.0, 0.1, 0.1])
    with pytest.raises(ValueError, match="tăng nghiêm ngặt"):
        load_teleop_episode(root)


def test_manual_episode_builds_valid_robomimic_hdf5(tmp_path: Path) -> None:
    root = _teleop(tmp_path / "episode-1")
    output = tmp_path / "manual.hdf5"
    items = [{
        "artifact_format": "teleop_dir",
        "episode_dir": str(root),
        "episode_id": "episode-1",
        "decision": "approved",
        "reviewer": "reviewer-1",
        "successful": True,
    }]

    assert build_robomimic_hdf5(output, items) == (1, 3)
    result = validate_training_dataset(output)
    assert result.valid, result.errors
    with h5py.File(output, "r") as handle:
        demo = handle["data/demo_0"]
        assert demo.attrs["source"] == "manual_teleop"
        assert demo.attrs["source_episode_id"] == "episode-1"
        assert demo["actions"].shape == (3, 7)
        assert demo["obs/robot0_joint_pos"].shape == (3, 7)
        assert demo["obs/object"].shape == (3, 10)
        assert set(handle["mask/train"].asstr()[...]) == {"demo_0"}
