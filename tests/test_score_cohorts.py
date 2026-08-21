from __future__ import annotations

from pathlib import Path

import numpy as np

from src.labeling.features import EpisodeArrays
from src.labeling.score import relative_cohort, score_episodes


def _episode(index: int, *, length: int, batch: str | None) -> EpisodeArrays:
    actions = np.zeros((length, 7), dtype=np.float64)
    actions[length // 2 :, 6] = 1.0
    eef = np.zeros((length, 3), dtype=np.float64)
    eef[:, 2] = np.linspace(0.85, 0.95, length)
    obj = eef.copy()
    obj[:, 2] -= 0.02
    gripper = np.zeros((length, 2), dtype=np.float64)
    provenance = {} if batch is None else {"collection_batch_id": batch}
    return EpisodeArrays(
        source=Path(f"episode-{index}.hdf5"),
        demo=f"demo_{index}", task="lift", quality="clean",
        actions=actions, eef_position=eef, gripper_qpos=gripper,
        object_position=obj, object_orientation=np.zeros((length, 4)),
        final_eef_position=eef[-1], final_gripper_qpos=gripper[-1],
        final_object_position=obj[-1], final_object_orientation=np.zeros(4),
        rewards=np.zeros(length), dones=np.zeros(length), recorded_success=True,
        termination_reason="success", provenance=provenance,
    )


def test_relative_cohort_separates_new_batch_from_legacy() -> None:
    legacy = _episode(0, length=60, batch=None)
    current = _episode(1, length=104, batch="lift-scripted-v1.3")

    assert relative_cohort(legacy) == "lift::legacy"
    assert relative_cohort(current) == "lift::lift-scripted-v1.3"


def test_new_batch_length_is_not_compared_with_legacy_distribution() -> None:
    episodes = [
        *[_episode(i, length=58 + i % 5, batch=None) for i in range(20)],
        *[_episode(100 + i, length=102 + i % 5, batch="lift-scripted-v1.3") for i in range(20)],
    ]

    scored, stats = score_episodes(episodes)
    current = scored[20:]

    assert stats.counts == {
        "lift::legacy": 20,
        "lift::lift-scripted-v1.3": 20,
    }
    assert all(
        item.auto_flags()["penalties"]["unusual_length"]["value"] < 1.0
        for item in current
    )
