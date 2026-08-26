from types import SimpleNamespace

import numpy as np

from src.core.recorder import TELEOP_SCHEMA_VERSION, EpisodeRecorder
from src.labeling.rule_engine import (
    RuleConfig,
    RuleEpisode,
    evaluate_rules,
    load_manual_rule_episode,
)
from src.services import storage

CONFIG = RuleConfig(stable_tail_frames=3)


def _eef_like(obj: np.ndarray, distance: float = 0.02) -> np.ndarray:
    eef = obj.copy()
    eef[:, 0] += distance
    return eef


def test_lift_rule_passes():
    obj = np.array(
        [
            [0.0, 0.0, 0.80],
            [0.0, 0.0, 0.85],
            [0.0, 0.0, 0.85],
            [0.0, 0.0, 0.85],
        ]
    )
    episode = RuleEpisode(
        "lift-pass",
        "lift_cube",
        "test",
        object_position=obj,
        eef_position=_eef_like(obj),
        table_height=0.80,
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "suggest_pass"
    assert result.results[0].status == "pass"


def test_lift_rule_fails_when_object_drops():
    obj = np.array(
        [
            [0.0, 0.0, 0.80],
            [0.0, 0.0, 0.85],
            [0.0, 0.0, 0.85],
            [0.0, 0.0, 0.80],
        ]
    )
    episode = RuleEpisode(
        "lift-fail",
        "lift",
        "test",
        object_position=obj,
        eef_position=_eef_like(obj),
        table_height=0.80,
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "auto_reject"
    assert result.results[0].status == "fail"


def test_lift_rule_cannot_evaluate_without_privileged_state():
    result = evaluate_rules(RuleEpisode("old", "lift_cube", "manual_teleop"), config=CONFIG)

    assert result.final_recommendation == "needs_review"
    assert result.results[0].status == "cannot_evaluate"


def test_can_rule_passes():
    obj = np.array(
        [
            [0.0, 0.0, 0.82],
            [0.2, 0.2, 0.82],
            [0.2, 0.2, 0.82],
            [0.2, 0.2, 0.82],
            [0.2, 0.2, 0.82],
        ]
    )
    eef = obj.copy()
    eef[-1] = [1.0, 1.0, 1.0]
    episode = RuleEpisode(
        "can-pass",
        "pick_place_can",
        "test",
        object_position=obj,
        eef_position=eef,
        target_bounds=(np.array([0.1, 0.1, 0.0]), np.array([0.3, 0.3, 2.0])),
        control_hz=30.0,
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "suggest_pass"
    assert result.results[0].status == "pass"


def test_can_rule_fails_outside_target():
    obj = np.tile(np.array([[0.6, 0.6, 0.82]]), (4, 1))
    episode = RuleEpisode(
        "can-fail",
        "can",
        "test",
        object_position=obj,
        eef_position=np.tile(np.array([[1.4, 1.4, 1.0]]), (4, 1)),
        target_bounds=(np.array([0.1, 0.1, 0.0]), np.array([0.3, 0.3, 2.0])),
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "auto_reject"
    assert result.results[0].status == "fail"


def test_can_rule_cannot_evaluate_without_target_bounds():
    obj = np.tile(np.array([[0.2, 0.2, 0.82]]), (4, 1))
    result = evaluate_rules(
        RuleEpisode("can-old", "can", "manual_teleop", object_position=obj, eef_position=obj),
        config=CONFIG,
    )

    assert result.results[0].status == "cannot_evaluate"


def test_square_rule_passes():
    obj = np.tile(np.array([[0.2, 0.2, 0.83]]), (4, 1))
    eef = np.tile(np.array([[1.0, 1.0, 1.0]]), (4, 1))
    quat = np.tile(np.array([[1.0, 0.0, 0.0, 0.0]]), (4, 1))
    episode = RuleEpisode(
        "square-pass",
        "nut_assembly_square",
        "test",
        object_position=obj,
        object_quat=quat,
        eef_position=eef,
        target_position=np.array([0.2, 0.2, 0.8]),
        table_height=0.80,
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "suggest_pass"
    assert result.results[0].status == "pass"


def test_square_rule_fails_on_position_error():
    obj = np.tile(np.array([[0.3, 0.3, 0.83]]), (4, 1))
    episode = RuleEpisode(
        "square-fail",
        "square",
        "test",
        object_position=obj,
        eef_position=np.tile(np.array([[1.0, 1.0, 1.0]]), (4, 1)),
        target_position=np.array([0.2, 0.2, 0.8]),
        table_height=0.80,
    )

    result = evaluate_rules(episode, config=CONFIG)

    assert result.final_recommendation == "auto_reject"
    assert result.results[0].status == "fail"


def test_square_rule_cannot_evaluate_without_target():
    obj = np.tile(np.array([[0.2, 0.2, 0.83]]), (4, 1))
    result = evaluate_rules(
        RuleEpisode("square-old", "square", "manual_teleop", object_position=obj, eef_position=obj),
        config=CONFIG,
    )

    assert result.results[0].status == "cannot_evaluate"


def test_teleop_profile_relaxes_square_orientation_quality_not_goal_state():
    angle = np.radians(18.0)
    quat = np.tile(
        np.array([[np.cos(angle / 2.0), 0.0, 0.0, np.sin(angle / 2.0)]]),
        (4, 1),
    )
    common = {
        "object_position": np.tile(np.array([[0.2, 0.2, 0.83]]), (4, 1)),
        "object_quat": quat,
        "eef_position": np.tile(np.array([[1.0, 1.0, 1.0]]), (4, 1)),
        "target_position": np.array([0.2, 0.2, 0.8]),
        "table_height": 0.80,
    }

    strict = evaluate_rules(RuleEpisode("strict", "square", "scripted", **common), config=CONFIG)
    tolerant = evaluate_rules(
        RuleEpisode("tolerant", "square", "manual_teleop", **common), config=CONFIG,
    )

    assert strict.results[0].status == "pass"
    assert strict.results[1].status == "warning"
    assert strict.final_recommendation == "needs_review"
    assert tolerant.results[0].status == "pass"
    assert tolerant.results[1].status == "pass"
    assert tolerant.final_recommendation == "suggest_pass"


def test_recorder_writes_privileged_state_schema(storage_dir):
    recorder = EpisodeRecorder(
        episode_id="episode_with_state",
        task_name="lift_cube",
        operator_id="operator",
        control_hz=30,
        image_size=2,
    )
    recorder.append(
        SimpleNamespace(
            t=0.0,
            qpos=[0.0],
            qvel=[0.0],
            ee_pose=[0.0] * 7,
            privileged_state=[1.0, 2.0, 3.0],
            images={},
        ),
        [0.0] * 7,
    )
    recorder.finalize()

    import json

    import pyarrow.parquet as pq

    meta = json.loads(storage.meta_path("episode_with_state").read_text(encoding="utf-8"))
    table = pq.read_table(storage.actions_path("episode_with_state"))

    assert meta["teleop_schema_version"] == TELEOP_SCHEMA_VERSION
    assert meta["privileged_state"]["format"] == "mujoco_flat_state"
    assert meta["privileged_state"]["bytes_per_frame"] == 24
    assert "privileged_state" in table.column_names
    assert table["privileged_state"].to_pylist() == [[1.0, 2.0, 3.0]]


def test_old_manual_episode_without_privileged_state_degrades_to_cannot_evaluate(storage_dir):
    episode_dir = storage.episode_dir("old_episode")
    episode_dir.mkdir(parents=True)

    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    (episode_dir / "meta.json").write_text(
        json.dumps({"episode_id": "old_episode", "task_name": "lift_cube"}),
        encoding="utf-8",
    )
    pq.write_table(
        pa.table(
            {
                "t": [0.0],
                "qpos": [[0.0]],
                "qvel": [[0.0]],
                "ee_pose": [[0.0] * 7],
                "action": [[0.0] * 7],
            }
        ),
        episode_dir / "actions.parquet",
    )

    episode = load_manual_rule_episode(episode_dir)
    result = evaluate_rules(episode, config=CONFIG)

    assert episode.metadata["task_name"] == "lift_cube"
    assert result.results[0].status == "cannot_evaluate"
