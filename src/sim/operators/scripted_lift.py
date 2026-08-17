"""Finite-state scripted operator for Robosuite Lift."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from src.sim.perturbations.variations import LiftVariation


class LiftPhase(str, Enum):
    APPROACH_CUBE = "approach_cube"
    ALIGN_CUBE = "align_cube"
    DESCEND = "descend"
    GRASP = "grasp"
    LIFT = "lift"
    HOLD = "hold"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class LiftOperatorConfig:
    position_gain: float = 10.0
    orientation_gain: float = 2.0
    xy_tolerance: float = 0.015
    z_tolerance: float = 0.012
    yaw_tolerance: float = 0.08
    approach_height: float = 0.14
    align_height: float = 0.065
    grasp_height_offset: float = 0.010
    lift_height: float = 0.16
    grasp_duration: int = 12
    hold_duration: int = 10
    phase_timeout: int = 100
    episode_timeout: int = 300
    grasp_min_lift: float = 0.035
    open_gripper: float = -1.0
    closed_gripper: float = 1.0
    grasp_check_duration: int = 20


class ScriptedLiftOperator:
    """Grasp and lift the cube without consulting reward or task success."""

    VERSION = "1.0"

    def __init__(self, env: Any, config: LiftOperatorConfig | None = None) -> None:
        self.config = config or LiftOperatorConfig()
        low, high = env.action_spec
        self._low = np.asarray(low, dtype=np.float64)
        self._high = np.asarray(high, dtype=np.float64)
        if self._low.shape != (7,) or self._high.shape != (7,):
            raise ValueError(f"OSC_POSE Lift operator requires action shape (7,), got {self._low.shape}")
        self._variation: LiftVariation | None = None
        self.reset()

    def reset(self) -> None:
        self.phase = LiftPhase.APPROACH_CUBE
        self.phase_steps = 0
        self.total_steps = 0
        self._grasp_cube_position: np.ndarray | None = None
        self._desired_grasp_quaternion: np.ndarray | None = None
        self._lift_target_position: np.ndarray | None = None
        self._regrasp_attempts = 0
        self.failure_reason: str | None = None
        self.failure_stage: str | None = None
        self._last_target_position: np.ndarray | None = None
        self._last_yaw_error = 0.0
        self._variation = None

    def set_variation(self, variation: LiftVariation) -> None:
        if not isinstance(variation, LiftVariation):
            raise TypeError("Lift operator requires LiftVariation")
        self._variation = variation

    @property
    def finished(self) -> bool:
        return self.phase in (LiftPhase.DONE, LiftPhase.FAILED)

    @property
    def debug_info(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "phase_steps": self.phase_steps,
            "total_steps": self.total_steps,
            "failure_reason": self.failure_reason,
            "failure_stage": self.failure_stage,
            "regrasp_attempts": self._regrasp_attempts,
            "last_target_position": (
                None if self._last_target_position is None else self._last_target_position.tolist()
            ),
            "desired_grasp_quaternion": (
                None
                if self._desired_grasp_quaternion is None
                else self._desired_grasp_quaternion.tolist()
            ),
            "last_yaw_error": self._last_yaw_error,
        }

    def _transition(self, phase: LiftPhase) -> None:
        self.phase = phase
        self.phase_steps = 0

    def _fail(self, reason: str) -> None:
        self.failure_reason = reason
        self.failure_stage = self.phase.value
        self._transition(LiftPhase.FAILED)

    def _reached(self, current: np.ndarray, target: np.ndarray) -> bool:
        return bool(
            np.linalg.norm(current[:2] - target[:2]) <= self.config.xy_tolerance
            and abs(float(current[2] - target[2])) <= self.config.z_tolerance
        )

    @staticmethod
    def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(quaternion))
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError("invalid end-effector quaternion")
        return quaternion / norm

    @staticmethod
    def _quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
        lx, ly, lz, lw = left
        rx, ry, rz, rw = right
        return np.array(
            [
                lw * rx + lx * rw + ly * rz - lz * ry,
                lw * ry - lx * rz + ly * rw + lz * rx,
                lw * rz + lx * ry - ly * rx + lz * rw,
                lw * rw - lx * rx - ly * ry - lz * rz,
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _yaw(quaternion: np.ndarray) -> float:
        x, y, z, w = quaternion
        return float(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))

    @staticmethod
    def _wrapped_yaw_error(target: float, current: float) -> float:
        return float((target - current + np.pi) % (2 * np.pi) - np.pi)

    def _grasp_yaw_error(self, observation: dict[str, Any]) -> float:
        yaw_bias = 0.0 if self._variation is None else self._variation.grasp_yaw_bias
        if yaw_bias == 0.0:
            return 0.0
        if "robot0_eef_quat" not in observation:
            raise ValueError("missing observation key: robot0_eef_quat")
        current = self._normalize_quaternion(
            np.asarray(observation["robot0_eef_quat"], dtype=np.float64),
        )
        if current.shape != (4,):
            raise ValueError("invalid end-effector quaternion shape")
        if self._desired_grasp_quaternion is None:
            yaw_delta = np.array(
                [0.0, 0.0, np.sin(yaw_bias / 2.0), np.cos(yaw_bias / 2.0)],
                dtype=np.float64,
            )
            self._desired_grasp_quaternion = self._normalize_quaternion(
                self._quaternion_multiply(yaw_delta, current),
            )
        return self._wrapped_yaw_error(
            self._yaw(self._desired_grasp_quaternion),
            self._yaw(current),
        )

    def _move(
        self,
        current: np.ndarray,
        target: np.ndarray,
        gripper: float,
        yaw_error: float = 0.0,
    ) -> np.ndarray:
        action = np.zeros(7, dtype=np.float64)
        action[:3] = self.config.position_gain * (target - current)
        action[5] = self.config.orientation_gain * yaw_error
        action[6] = gripper
        return np.clip(action, self._low, self._high)

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        if self.finished:
            return np.clip(np.array([0, 0, 0, 0, 0, 0, self.config.closed_gripper]), self._low, self._high)
        for key in ("robot0_eef_pos", "cube_pos"):
            if key not in observation:
                self._fail(f"missing observation key: {key}")
                return np.zeros(7, dtype=np.float64)
        eef = np.asarray(observation["robot0_eef_pos"], dtype=np.float64)
        cube = np.asarray(observation["cube_pos"], dtype=np.float64)
        if eef.shape != (3,) or cube.shape != (3,) or not np.all(np.isfinite([eef, cube])):
            self._fail("invalid end-effector or cube pose")
            return np.zeros(7, dtype=np.float64)

        self.total_steps += 1
        self.phase_steps += 1
        if self.total_steps > self.config.episode_timeout:
            self._fail("episode timeout")
        elif self.phase_steps > self.config.phase_timeout:
            self._fail(f"phase timeout: {self.phase.value}")
        if self.finished:
            return self._move(eef, eef, self.config.closed_gripper)

        c = self.config
        phase_at_start = self.phase
        semantic = self._variation
        grasp_offset = np.asarray(
            (0.0, 0.0, 0.0) if semantic is None else semantic.grasp_xyz_offset,
        )
        close_timing = 0 if semantic is None else semantic.gripper_close_timing_offset
        yaw_error = 0.0
        if self.phase in {LiftPhase.APPROACH_CUBE, LiftPhase.ALIGN_CUBE, LiftPhase.DESCEND}:
            try:
                yaw_error = self._grasp_yaw_error(observation)
            except ValueError as exc:
                self._fail(str(exc))
                return self._move(eef, eef, c.open_gripper)
        yaw_reached = abs(yaw_error) <= c.yaw_tolerance
        if self.phase == LiftPhase.GRASP:
            close_step = max(1, 1 + close_timing)
            gripper = c.closed_gripper if self.phase_steps >= close_step else c.open_gripper
        elif self.phase in {LiftPhase.LIFT, LiftPhase.HOLD}:
            gripper = c.closed_gripper
        else:
            gripper = c.open_gripper
        if self.phase == LiftPhase.APPROACH_CUBE:
            target = cube + grasp_offset + [0.0, 0.0, c.approach_height]
            if self._reached(eef, target) and yaw_reached:
                self._transition(LiftPhase.ALIGN_CUBE)
        elif self.phase == LiftPhase.ALIGN_CUBE:
            target = cube + grasp_offset + [0.0, 0.0, c.align_height]
            if self._reached(eef, target) and yaw_reached:
                self._transition(LiftPhase.DESCEND)
        elif self.phase == LiftPhase.DESCEND:
            target = cube + grasp_offset + [0.0, 0.0, c.grasp_height_offset]
            if self._reached(eef, target) and yaw_reached:
                self._grasp_cube_position = cube.copy()
                self._transition(LiftPhase.GRASP)
                if close_timing < 0:
                    gripper = c.closed_gripper
        elif self.phase == LiftPhase.GRASP:
            target = eef.copy()
            if self.phase_steps >= c.grasp_duration:
                self._transition(LiftPhase.LIFT)
        elif self.phase == LiftPhase.LIFT:
            if self._grasp_cube_position is None:
                self._fail("missing grasp position")
                target = eef.copy()
            else:
                lateral = np.asarray(
                    (0.0, 0.0) if semantic is None else semantic.lift_lateral_offset,
                )
                height_delta = 0.0 if semantic is None else semantic.lift_height_delta
                target = self._grasp_cube_position + [
                    lateral[0],
                    lateral[1],
                    c.lift_height + height_delta,
                ]
                self._lift_target_position = np.asarray(target, dtype=np.float64).copy()
                cube_lifted = cube[2] >= self._grasp_cube_position[2] + c.grasp_min_lift
                if self.phase_steps > c.grasp_check_duration and not cube_lifted:
                    can_regrasp = bool(
                        semantic is not None
                        and semantic.regrasp_enabled
                        and self._regrasp_attempts < min(1, semantic.retry_cap)
                    )
                    if can_regrasp:
                        self._regrasp_attempts += 1
                        self._grasp_cube_position = None
                        self._desired_grasp_quaternion = None
                        self._lift_target_position = None
                        self._transition(LiftPhase.APPROACH_CUBE)
                        target = eef.copy()
                        gripper = c.open_gripper
                    else:
                        self._fail("missed grasp: cube did not lift")
                elif not self.finished and cube_lifted and self._reached(eef, target):
                    self._transition(LiftPhase.HOLD)
        elif self.phase == LiftPhase.HOLD:
            target = (
                self._lift_target_position
                if semantic is not None
                and not semantic.is_zero
                and self._lift_target_position is not None
                else eef.copy()
            )
            if self.phase_steps >= c.hold_duration:
                self._transition(LiftPhase.DONE)
        else:
            target = eef.copy()
        self._last_target_position = np.asarray(target, dtype=np.float64).copy()
        active_yaw_error = (
            yaw_error
            if phase_at_start
            in {LiftPhase.APPROACH_CUBE, LiftPhase.ALIGN_CUBE, LiftPhase.DESCEND}
            else 0.0
        )
        self._last_yaw_error = active_yaw_error
        return self._move(eef, np.asarray(target), gripper, active_yaw_error)
