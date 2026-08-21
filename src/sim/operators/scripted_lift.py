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
    SETTLE_BEFORE_GRASP = "settle_before_grasp"
    RECOVER_ALIGN = "recover_align"
    RECOVER_DESCEND = "recover_descend"
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
    final_position_gain: float = 4.0
    pregrasp_xy_tolerance: float = 0.006
    pregrasp_z_tolerance: float = 0.006
    pregrasp_yaw_tolerance: float = 0.05
    settle_duration: int = 5
    settle_position_delta: float = 0.002
    settle_cube_drift: float = 0.003
    settle_realign_xy_error: float = 0.010
    settle_realign_z_error: float = 0.010
    settle_realign_yaw_error: float = 0.12
    settle_correction_timeout: int = 15


class ScriptedLiftOperator:
    """Grasp and lift the cube without consulting reward or task success."""

    VERSION = "1.3"

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
        self._pregrasp_realigns = 0
        self._recovery_active = False
        self._settle_cube_position: np.ndarray | None = None
        self._previous_settle_eef: np.ndarray | None = None
        self._stable_settle_steps = 0
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
            "pregrasp_realigns": self._pregrasp_realigns,
            "recovery_demonstration": self._recovery_active,
            "stable_settle_steps": self._stable_settle_steps,
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

    def _pregrasp_reached(self, current: np.ndarray, target: np.ndarray) -> bool:
        return bool(
            np.linalg.norm(current[:2] - target[:2]) <= self.config.pregrasp_xy_tolerance
            and abs(float(current[2] - target[2])) <= self.config.pregrasp_z_tolerance
        )

    def _begin_pregrasp_realign(self) -> None:
        self._pregrasp_realigns += 1
        self._recovery_active = True
        self._settle_cube_position = None
        self._previous_settle_eef = None
        self._stable_settle_steps = 0
        self._transition(LiftPhase.RECOVER_ALIGN)

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

    @classmethod
    def _cube_yaw_error(cls, target: float, current: float) -> float:
        """Return the shortest yaw error to any equivalent face of a square cube."""
        quarter_turn = np.pi / 2.0
        candidates = (
            cls._wrapped_yaw_error(target + k * quarter_turn, current)
            for k in range(-2, 3)
        )
        return min(candidates, key=abs)

    def _grasp_yaw_error(self, observation: dict[str, Any]) -> float:
        yaw_bias = (
            0.0
            if self._variation is None or self._recovery_active
            else self._variation.grasp_yaw_bias
        )
        for key in ("robot0_eef_quat", "cube_quat"):
            if key not in observation:
                raise ValueError(f"missing observation key: {key}")
        current = np.asarray(observation["robot0_eef_quat"], dtype=np.float64)
        cube = np.asarray(observation["cube_quat"], dtype=np.float64)
        if current.shape != (4,) or cube.shape != (4,):
            raise ValueError("invalid end-effector or cube quaternion shape")
        current = self._normalize_quaternion(
            current,
        )
        cube = self._normalize_quaternion(cube)
        yaw_error = self._cube_yaw_error(
            self._yaw(cube) + yaw_bias,
            self._yaw(current),
        )
        # Preserve the current roll / pitch in diagnostics while showing the
        # nearest equivalent cube-aligned yaw selected by the controller.
        yaw_delta = np.array(
            [0.0, 0.0, np.sin(yaw_error / 2.0), np.cos(yaw_error / 2.0)],
            dtype=np.float64,
        )
        self._desired_grasp_quaternion = self._normalize_quaternion(
            self._quaternion_multiply(yaw_delta, current),
        )
        return yaw_error

    def _move(
        self,
        current: np.ndarray,
        target: np.ndarray,
        gripper: float,
        yaw_error: float = 0.0,
        position_gain: float | None = None,
    ) -> np.ndarray:
        action = np.zeros(7, dtype=np.float64)
        gain = self.config.position_gain if position_gain is None else position_gain
        action[:3] = gain * (target - current)
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
        if self._recovery_active:
            grasp_offset = np.zeros(3, dtype=np.float64)
        close_timing = 0 if semantic is None else semantic.gripper_close_timing_offset
        yaw_error = 0.0
        if self.phase in {
            LiftPhase.APPROACH_CUBE,
            LiftPhase.ALIGN_CUBE,
            LiftPhase.DESCEND,
            LiftPhase.SETTLE_BEFORE_GRASP,
            LiftPhase.RECOVER_ALIGN,
            LiftPhase.RECOVER_DESCEND,
        }:
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
        elif self.phase == LiftPhase.RECOVER_ALIGN:
            target = cube + [0.0, 0.0, c.align_height]
            if self._pregrasp_reached(eef, target) and (
                abs(yaw_error) <= c.pregrasp_yaw_tolerance
            ):
                self._transition(LiftPhase.RECOVER_DESCEND)
        elif self.phase == LiftPhase.DESCEND:
            target = cube + grasp_offset + [0.0, 0.0, c.grasp_height_offset]
            if self._pregrasp_reached(eef, target) and (
                abs(yaw_error) <= c.pregrasp_yaw_tolerance
            ):
                self._settle_cube_position = cube.copy()
                self._previous_settle_eef = None
                self._stable_settle_steps = 0
                self._transition(LiftPhase.SETTLE_BEFORE_GRASP)
        elif self.phase == LiftPhase.RECOVER_DESCEND:
            target = cube + [0.0, 0.0, c.grasp_height_offset]
            if self._pregrasp_reached(eef, target) and (
                abs(yaw_error) <= c.pregrasp_yaw_tolerance
            ):
                self._settle_cube_position = cube.copy()
                self._previous_settle_eef = None
                self._stable_settle_steps = 0
                self._transition(LiftPhase.SETTLE_BEFORE_GRASP)
        elif self.phase == LiftPhase.SETTLE_BEFORE_GRASP:
            # Validate against the current, unbiased cube pose. Perturbed
            # approaches therefore become successful correction trajectories
            # instead of teaching the policy to close from a bad pose.
            target = cube + [0.0, 0.0, c.grasp_height_offset]
            cube_drift = (
                0.0
                if self._settle_cube_position is None
                else float(np.linalg.norm(cube[:2] - self._settle_cube_position[:2]))
            )
            eef_delta = (
                0.0
                if self._previous_settle_eef is None
                else float(np.linalg.norm(eef - self._previous_settle_eef))
            )
            aligned = self._pregrasp_reached(eef, target)
            precisely_oriented = abs(yaw_error) <= c.pregrasp_yaw_tolerance
            xy_error = float(np.linalg.norm(eef[:2] - target[:2]))
            z_error = abs(float(eef[2] - target[2]))
            severe_misalignment = (
                xy_error > c.settle_realign_xy_error
                or z_error > c.settle_realign_z_error
                or abs(yaw_error) > c.settle_realign_yaw_error
            )
            correction_timed_out = (
                self.phase_steps > c.settle_correction_timeout
                and (not aligned or not precisely_oriented)
            )
            if (
                cube_drift > c.settle_cube_drift
                or severe_misalignment
                or correction_timed_out
            ):
                self._begin_pregrasp_realign()
                target = cube + [0.0, 0.0, c.align_height]
            elif not aligned or not precisely_oriented:
                # Small residual errors are corrected at the grasp height.
                # Lifting back to ALIGN here creates the redundant up/down
                # motion that polluted every clean demonstration in v1.2.
                self._previous_settle_eef = eef.copy()
                self._stable_settle_steps = 0
            else:
                self._previous_settle_eef = eef.copy()
                if eef_delta <= c.settle_position_delta:
                    self._stable_settle_steps += 1
                else:
                    self._stable_settle_steps = 0
                if self._stable_settle_steps >= c.settle_duration:
                    self._grasp_cube_position = cube.copy()
                    self._transition(LiftPhase.GRASP)
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
                        self._recovery_active = True
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
            in {
                LiftPhase.APPROACH_CUBE,
                LiftPhase.ALIGN_CUBE,
                LiftPhase.DESCEND,
                LiftPhase.SETTLE_BEFORE_GRASP,
                LiftPhase.RECOVER_ALIGN,
                LiftPhase.RECOVER_DESCEND,
            }
            else 0.0
        )
        self._last_yaw_error = active_yaw_error
        position_gain = (
            c.final_position_gain
            if phase_at_start in {
                LiftPhase.DESCEND,
                LiftPhase.SETTLE_BEFORE_GRASP,
                LiftPhase.RECOVER_DESCEND,
            }
            else None
        )
        return self._move(
            eef,
            np.asarray(target),
            gripper,
            active_yaw_error,
            position_gain,
        )
