from __future__ import annotations

import numpy as np
import pytest

from src.sim.operators.scripted_lift import LiftOperatorConfig, LiftPhase, ScriptedLiftOperator


class _FakeLiftEnv:
    action_spec = (-np.ones(7, dtype=np.float64), np.ones(7, dtype=np.float64))


def _yaw_quaternion(yaw: float) -> np.ndarray:
    return np.array([0.0, 0.0, np.sin(yaw / 2.0), np.cos(yaw / 2.0)])


def _observation(*, cube_yaw: float, eef_yaw: float = 0.0) -> dict[str, np.ndarray]:
    cube = np.array([0.1, -0.1, 0.82])
    return {
        "cube_pos": cube,
        "cube_quat": _yaw_quaternion(cube_yaw),
        "robot0_eef_pos": cube + [0.0, 0.0, 0.14],
        "robot0_eef_quat": _yaw_quaternion(eef_yaw),
    }


def test_lift_rotates_toward_cube_before_leaving_approach() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())

    action = operator.act(_observation(cube_yaw=0.35))

    assert action[5] == pytest.approx(0.7)
    assert operator.phase == LiftPhase.APPROACH_CUBE


def test_lift_accepts_an_equivalent_cube_edge() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())

    action = operator.act(_observation(cube_yaw=0.3, eef_yaw=0.3 + np.pi / 2.0))

    assert action[5] == pytest.approx(0.0, abs=1e-12)
    assert operator.phase == LiftPhase.ALIGN_CUBE


def test_lift_selects_shortest_of_four_cube_edges() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())

    action = operator.act(_observation(cube_yaw=1.45))

    expected_error = 1.45 - np.pi / 2.0
    assert action[5] == pytest.approx(2.0 * expected_error)
    assert abs(action[5]) < 0.3


def test_lift_fails_safely_without_cube_orientation() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())
    observation = _observation(cube_yaw=0.3)
    observation.pop("cube_quat")

    action = operator.act(observation)

    assert operator.phase == LiftPhase.FAILED
    assert operator.failure_reason == "missing observation key: cube_quat"
    assert action[6] == operator.config.open_gripper


def test_lift_settles_before_closing_gripper() -> None:
    operator = ScriptedLiftOperator(
        _FakeLiftEnv(), LiftOperatorConfig(settle_duration=3),
    )
    observation = _observation(cube_yaw=0.0)
    cube = observation["cube_pos"]
    observation["robot0_eef_pos"] = cube + [0.0, 0.0, operator.config.grasp_height_offset]
    operator.phase = LiftPhase.DESCEND

    action = operator.act(observation)
    assert operator.phase == LiftPhase.SETTLE_BEFORE_GRASP
    assert action[6] == operator.config.open_gripper

    for _ in range(2):
        action = operator.act(observation)
        assert operator.phase == LiftPhase.SETTLE_BEFORE_GRASP
        assert action[6] == operator.config.open_gripper
    operator.act(observation)
    assert operator.phase == LiftPhase.GRASP


def test_lift_descend_uses_strict_pregrasp_threshold() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())
    observation = _observation(cube_yaw=0.0)
    cube = observation["cube_pos"]
    # This passed the old 15 mm DESCEND threshold but must not enter SETTLE.
    observation["robot0_eef_pos"] = cube + [0.010, 0.0, operator.config.grasp_height_offset]
    operator.phase = LiftPhase.DESCEND

    action = operator.act(observation)

    assert operator.phase == LiftPhase.DESCEND
    assert action[0] < 0.0
    assert action[2] == pytest.approx(0.0)
    assert action[6] == operator.config.open_gripper


def test_lift_corrects_small_settle_error_without_lifting() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())
    observation = _observation(cube_yaw=0.0)
    cube = observation["cube_pos"]
    observation["robot0_eef_pos"] = cube + [0.007, 0.0, operator.config.grasp_height_offset]
    operator.phase = LiftPhase.SETTLE_BEFORE_GRASP
    operator._settle_cube_position = cube.copy()

    action = operator.act(observation)

    assert operator.phase == LiftPhase.SETTLE_BEFORE_GRASP
    assert operator.debug_info["pregrasp_realigns"] == 0
    assert action[0] < 0.0
    assert action[2] == pytest.approx(0.0)
    assert action[6] == operator.config.open_gripper


def test_lift_realigns_if_cube_moves_during_settle() -> None:
    operator = ScriptedLiftOperator(_FakeLiftEnv())
    observation = _observation(cube_yaw=0.0)
    cube = observation["cube_pos"]
    observation["robot0_eef_pos"] = cube + [0.0, 0.0, operator.config.grasp_height_offset]
    operator.phase = LiftPhase.DESCEND
    operator.act(observation)

    moved = {key: value.copy() for key, value in observation.items()}
    moved["cube_pos"][0] += operator.config.settle_cube_drift * 2
    action = operator.act(moved)

    assert operator.phase == LiftPhase.RECOVER_ALIGN
    assert operator.debug_info["pregrasp_realigns"] == 1
    assert operator.debug_info["recovery_demonstration"] is True
    assert action[6] == operator.config.open_gripper


def test_lift_realigns_to_unbiased_cube_after_perturbed_approach() -> None:
    from src.sim.perturbations.variations import EventSchedule, LiftVariation

    operator = ScriptedLiftOperator(_FakeLiftEnv())
    operator.set_variation(LiftVariation(
        quality="good", noise_scale=0.25,
        landmark_position_bias=(0.0, 0.0, 0.0),
        landmark_orientation_bias=(0.0, 0.0, 0.0),
        arm_gain=(1.0,) * 6, arm_bias=(0.0,) * 6,
        schedule=EventSchedule(), retry_cap=1,
        fault_type="none", fault_phase="", fault_magnitude=0.0,
        grasp_xyz_offset=(0.012, 0.0, 0.0), regrasp_enabled=True,
    ))
    observation = _observation(cube_yaw=0.0)
    cube = observation["cube_pos"]
    observation["robot0_eef_pos"] = cube + [0.012, 0.0, operator.config.grasp_height_offset]
    operator.phase = LiftPhase.DESCEND
    operator.act(observation)
    operator.act(observation)

    assert operator.phase == LiftPhase.RECOVER_ALIGN
    assert operator.debug_info["recovery_demonstration"] is True


def test_the_lift_context_records_through_the_hold_phase() -> None:
    """Lift ghi tiếp 30 bước sau khi thành công, và dựng được.

    Không test nào chạm `build_lift_tool_context`, nên một tham số sai ở đây đi
    lọt qua cả bộ test rồi mới vỡ lúc thu dữ liệu thật — đúng điều đã xảy ra
    với `finish_tool_after_success`, một tên mà `ToolContext` không có.
    """

    from src.sim.task_adapters.lift import build_lift_tool_context

    context = build_lift_tool_context(None, horizon=100, seed=1)

    # 30 bước ở 20 Hz là khoảng 1.5 giây giữ vật — pha HOLD mà người thao tác
    # thực hiện, chứ không cắt ngay lúc chạm ngưỡng.
    assert context.post_success_steps == 30
