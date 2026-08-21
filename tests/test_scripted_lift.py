from __future__ import annotations

import numpy as np
import pytest

from src.sim.operators.scripted_lift import LiftPhase, ScriptedLiftOperator


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
