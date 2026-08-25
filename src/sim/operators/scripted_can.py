"""Finite-state, rule-based operator for Robosuite PickPlaceCan."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from src.sim.perturbations.variations import CanVariation


# See src/sim/tools/base.py ToolStatus for why this stays (str, Enum) instead
# of enum.StrEnum: their str()/format() output differs, and phase names may
# already be logged/serialized as plain strings.
class CanPhase(str, Enum):  # noqa: UP042
    APPROACH_CAN = "approach_can"
    ALIGN_CAN = "align_can"
    DESCEND = "descend"
    GRASP = "grasp"
    LIFT = "lift"
    APPROACH_BIN = "approach_bin"
    DESCEND_INTO_BIN = "descend_into_bin"
    RELEASE = "release"
    RETREAT = "retreat"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class CanOperatorConfig:
    position_gain: float = 10.0
    orientation_gain: float = 2.0
    xy_tolerance: float = 0.015
    z_tolerance: float = 0.012
    yaw_tolerance: float = 0.08
    approach_height: float = 0.14
    align_height: float = 0.07
    grasp_height_offset: float = 0.025
    lift_height: float = 0.18
    bin_approach_height: float = 0.20
    bin_release_height: float = 0.10
    retreat_height: float = 0.20
    grasp_duration: int = 12
    release_duration: int = 12
    phase_timeout: int = 100
    episode_timeout: int = 400
    grasp_min_lift: float = 0.035
    open_gripper: float = -1.0
    closed_gripper: float = 1.0


class ScriptedCanOperator:
    """Move above the can, grasp it, and place it into its assigned bin.

    Reward and task success are deliberately not inputs to :meth:`act`.
    """

    VERSION = "1.0"

    def __init__(self, env: Any, config: CanOperatorConfig | None = None) -> None:
        self.env = env
        self.config = config or CanOperatorConfig()
        low, high = env.action_spec
        self._low = np.asarray(low, dtype=np.float64)
        self._high = np.asarray(high, dtype=np.float64)
        if self._low.shape != (7,) or self._high.shape != (7,):
            raise ValueError(f"OSC_POSE Can operator requires action shape (7,), got {self._low.shape}")
        self.target_bin_id = int(env.object_id)
        self.target_position = self._read_target_position(env)
        self._target_bin_xy_bounds = self._read_target_bin_xy_bounds(env)
        self._variation: CanVariation | None = None
        self.reset()

    @staticmethod
    def _read_target_position(env: Any) -> np.ndarray:
        placements = np.asarray(env.target_bin_placements, dtype=np.float64)
        object_id = int(env.object_id)
        if placements.ndim != 2 or placements.shape[1] != 3 or object_id >= len(placements):
            raise ValueError("Environment does not expose a valid Can target_bin_placements entry")
        return placements[object_id].copy()

    @staticmethod
    def _read_target_bin_xy_bounds(
        env: Any,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        if not all(hasattr(env, name) for name in ('bin2_pos', 'bin_size', 'object_id')):
            return None
        bin_position = np.asarray(env.bin2_pos, dtype=np.float64)
        bin_size = np.asarray(env.bin_size, dtype=np.float64)
        bin_id = int(env.object_id)
        if bin_position.shape != (3,) or bin_size.shape != (3,):
            raise ValueError('Environment exposes invalid Can bin geometry')
        lower = bin_position[:2].copy()
        if bin_id in (0, 2):
            lower[0] -= bin_size[0] / 2.0
        if bin_id < 2:
            lower[1] -= bin_size[1] / 2.0
        return lower, lower + bin_size[:2] / 2.0

    def reset(self) -> None:
        self.phase = CanPhase.APPROACH_CAN
        self.phase_steps = 0
        self.total_steps = 0
        self._grasp_can_position: np.ndarray | None = None
        self._desired_grasp_quaternion: np.ndarray | None = None
        self._placement_target_position = self.target_position.copy()
        self._retry_attempts = 0
        self._retry_source: str | None = None
        self.failure_reason: str | None = None
        self.failure_stage: str | None = None
        self._last_target_position: np.ndarray | None = None
        self._last_yaw_error = 0.0
        self._active_semantic_mask: tuple[str, ...] = ()
        self._variation = None

    def set_variation(self, variation: CanVariation) -> None:
        if not isinstance(variation, CanVariation):
            raise TypeError('Can operator requires CanVariation')
        placement_target = self.target_position + np.asarray(
            variation.bin_target_xyz_offset,
            dtype=np.float64,
        )
        if self._target_bin_xy_bounds is not None:
            lower, upper = self._target_bin_xy_bounds
            if not np.all((placement_target[:2] > lower) & (placement_target[:2] < upper)):
                raise ValueError('Can bin target offset leaves the assigned target bin')
        self._variation = variation
        self._placement_target_position = placement_target

    @property
    def finished(self) -> bool:
        return self.phase in (CanPhase.DONE, CanPhase.FAILED)

    @property
    def debug_info(self) -> dict[str, Any]:
        return {
            'failure_stage': self.failure_stage,
            'target_bin_id': self.target_bin_id,
            'placement_target_position': self._placement_target_position.tolist(),
            'retry_attempts': self._retry_attempts,
            'retry_source': self._retry_source,
            'last_target_position': (
                None if self._last_target_position is None else self._last_target_position.tolist()
            ),
            'desired_grasp_quaternion': (
                None
                if self._desired_grasp_quaternion is None
                else self._desired_grasp_quaternion.tolist()
            ),
            'last_yaw_error': self._last_yaw_error,
            'active_semantic_mask': self._active_semantic_mask,
            "phase": self.phase.value,
            "phase_steps": self.phase_steps,
            "total_steps": self.total_steps,
            "failure_reason": self.failure_reason,
            "target_position": self.target_position.tolist(),
        }

    def _transition(self, phase: CanPhase) -> None:
        self.phase = phase
        self.phase_steps = 0

    def _fail(self, reason: str) -> None:
        if reason == 'missed grasp: can did not lift' and self._retry_available():
            self._start_retry('grasp_verification')
            return
        self.failure_reason = reason
        self.failure_stage = self.phase.value
        self._transition(CanPhase.FAILED)

    def _retry_available(self) -> bool:
        variation = self._variation
        return bool(
            variation is not None
            and variation.retry_enabled
            and self._retry_attempts < min(1, variation.retry_cap)
        )

    def _start_retry(self, source: str) -> None:
        self._retry_attempts += 1
        self._retry_source = source
        self._grasp_can_position = None
        self._desired_grasp_quaternion = None
        self._transition(CanPhase.APPROACH_CAN)

    def _reached(self, current: np.ndarray, target: np.ndarray) -> bool:
        return bool(
            np.linalg.norm(current[:2] - target[:2]) <= self.config.xy_tolerance
            and abs(float(current[2] - target[2])) <= self.config.z_tolerance
        )

    @staticmethod
    def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(quaternion))
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError('invalid end-effector quaternion')
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
        yaw_bias = 0.0 if self._variation is None else self._variation.can_grasp_yaw_bias
        if yaw_bias == 0.0:
            return 0.0
        if 'robot0_eef_quat' not in observation:
            raise ValueError('missing observation key: robot0_eef_quat')
        current = np.asarray(observation['robot0_eef_quat'], dtype=np.float64)
        if current.shape != (4,):
            raise ValueError('invalid end-effector quaternion shape')
        current = self._normalize_quaternion(current)
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

    def _can_inside_target_bin_xy(self, can_position: np.ndarray) -> bool:
        if self._target_bin_xy_bounds is None:
            return True
        lower, upper = self._target_bin_xy_bounds
        return bool(np.all((can_position[:2] > lower) & (can_position[:2] < upper)))

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
        # Zero rotational delta preserves the reset pose, whose gripper points down.
        action[6] = gripper
        return np.clip(action, self._low, self._high)

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        if self.finished:
            return self._move(np.zeros(3), np.zeros(3), self.config.open_gripper)
        for key in ("robot0_eef_pos", "Can_pos"):
            if key not in observation:
                self._fail(f"missing observation key: {key}")
                return np.zeros(7, dtype=np.float64)

        eef = np.asarray(observation["robot0_eef_pos"], dtype=np.float64)
        can = np.asarray(observation["Can_pos"], dtype=np.float64)
        if eef.shape != (3,) or can.shape != (3,) or not np.all(np.isfinite([eef, can])):
            self._fail("invalid end-effector or can pose")
            return np.zeros(7, dtype=np.float64)

        self.total_steps += 1
        self.phase_steps += 1
        if self.total_steps > self.config.episode_timeout:
            self._fail("episode timeout")
        elif self.phase_steps > self.config.phase_timeout:
            self._fail(f"phase timeout: {self.phase.value}")
        if self.finished:
            return self._move(eef, eef, self.config.open_gripper)

        c = self.config
        phase_at_start = self.phase
        semantic = self._variation
        grasp_offset = np.asarray(
            (0.0, 0.0, 0.0) if semantic is None else semantic.can_grasp_xyz_offset,
        )
        transport_offset = np.asarray(
            (0.0, 0.0, 0.0) if semantic is None else semantic.transport_waypoint_offset,
        )
        close_timing = 0 if semantic is None else semantic.gripper_close_timing_offset
        release_timing = 0 if semantic is None else semantic.release_timing_offset
        release_height = 0.0 if semantic is None else semantic.release_height_offset
        yaw_error = 0.0
        if self.phase in {CanPhase.APPROACH_CAN, CanPhase.ALIGN_CAN, CanPhase.DESCEND}:
            try:
                yaw_error = self._grasp_yaw_error(observation)
            except ValueError as exc:
                self._fail(str(exc))
                return self._move(eef, eef, c.open_gripper)
        yaw_reached = abs(yaw_error) <= c.yaw_tolerance

        if self.phase == CanPhase.GRASP:
            close_step = max(1, 1 + close_timing)
            gripper = c.closed_gripper if self.phase_steps >= close_step else c.open_gripper
        elif self.phase in {
            CanPhase.LIFT,
            CanPhase.APPROACH_BIN,
            CanPhase.DESCEND_INTO_BIN,
        }:
            gripper = c.closed_gripper
        elif self.phase == CanPhase.RELEASE:
            open_step = max(1, 1 + release_timing)
            gripper = c.open_gripper if self.phase_steps >= open_step else c.closed_gripper
        else:
            gripper = c.open_gripper

        if self.phase == CanPhase.APPROACH_CAN:
            target = can + grasp_offset + [0.0, 0.0, c.approach_height]
            if self._reached(eef, target) and yaw_reached:
                self._transition(CanPhase.ALIGN_CAN)
        elif self.phase == CanPhase.ALIGN_CAN:
            target = can + grasp_offset + [0.0, 0.0, c.align_height]
            if self._reached(eef, target) and yaw_reached:
                self._transition(CanPhase.DESCEND)
        elif self.phase == CanPhase.DESCEND:
            target = can + grasp_offset + [0.0, 0.0, c.grasp_height_offset]
            if self._reached(eef, target) and yaw_reached:
                self._grasp_can_position = can.copy()
                self._transition(CanPhase.GRASP)
                if close_timing < 0:
                    gripper = c.closed_gripper
        elif self.phase == CanPhase.GRASP:
            target = eef.copy()
            if self.phase_steps >= c.grasp_duration:
                self._transition(CanPhase.LIFT)
        elif self.phase == CanPhase.LIFT:
            target = self._grasp_can_position + transport_offset + [0.0, 0.0, c.lift_height]
            if self.phase_steps > max(4, c.phase_timeout // 3) and self._grasp_can_position is not None:
                if can[2] < self._grasp_can_position[2] + c.grasp_min_lift:
                    self._fail("missed grasp: can did not lift")
                    if self.phase == CanPhase.APPROACH_CAN:
                        target = eef.copy()
                        gripper = c.open_gripper
            if self.phase == CanPhase.LIFT and self._reached(eef, target):
                self._transition(CanPhase.APPROACH_BIN)
        elif self.phase == CanPhase.APPROACH_BIN:
            target = (
                self._placement_target_position
                + transport_offset
                + [0.0, 0.0, c.bin_approach_height]
            )
            if self._reached(eef, target):
                self._transition(CanPhase.DESCEND_INTO_BIN)
        elif self.phase == CanPhase.DESCEND_INTO_BIN:
            target = self._placement_target_position + [
                0.0,
                0.0,
                c.bin_release_height + release_height,
            ]
            if self._reached(eef, target):
                self._transition(CanPhase.RELEASE)
                if release_timing < 0:
                    gripper = c.open_gripper
        elif self.phase == CanPhase.RELEASE:
            target = eef.copy()
            if self.phase_steps >= c.release_duration:
                placement_missed = bool(
                    semantic is not None
                    and not semantic.is_zero
                    and semantic.retry_enabled
                    and not self._can_inside_target_bin_xy(can)
                )
                if placement_missed and self._retry_available():
                    self._start_retry('placement_verification')
                    gripper = c.open_gripper
                elif placement_missed:
                    self._fail('placement recovery exhausted: can outside target bin')
                else:
                    self._transition(CanPhase.RETREAT)
        elif self.phase == CanPhase.RETREAT:
            target = self.target_position + [0.0, 0.0, c.retreat_height]
            if self._reached(eef, target):
                self._transition(CanPhase.DONE)
        else:
            target = eef.copy()

        if self.phase == CanPhase.FAILED:
            gripper = c.open_gripper
        active: list[str] = []
        if semantic is not None and not semantic.is_zero:
            if phase_at_start in {
                CanPhase.APPROACH_CAN,
                CanPhase.ALIGN_CAN,
                CanPhase.DESCEND,
            }:
                if any(value != 0.0 for value in semantic.can_grasp_xyz_offset):
                    active.append('can_grasp_xyz_offset')
                if semantic.can_grasp_yaw_bias != 0.0:
                    active.append('can_grasp_yaw_bias')
            if phase_at_start == CanPhase.GRASP and close_timing != 0:
                active.append('gripper_close_timing_offset')
            if phase_at_start in {CanPhase.LIFT, CanPhase.APPROACH_BIN} and any(
                value != 0.0 for value in semantic.transport_waypoint_offset
            ):
                active.append('transport_waypoint_offset')
            if phase_at_start in {CanPhase.APPROACH_BIN, CanPhase.DESCEND_INTO_BIN} and any(
                value != 0.0 for value in semantic.bin_target_xyz_offset
            ):
                active.append('bin_target_xyz_offset')
            if phase_at_start == CanPhase.DESCEND_INTO_BIN and release_height != 0.0:
                active.append('release_height_offset')
            if phase_at_start == CanPhase.RELEASE and release_timing != 0:
                active.append('release_timing_offset')
            if self._retry_source is not None and self.phase == CanPhase.APPROACH_CAN:
                active.append('bounded_retry')
        self._active_semantic_mask = tuple(active)
        self._last_target_position = np.asarray(target, dtype=np.float64).copy()
        active_yaw_error = (
            yaw_error
            if phase_at_start
            in {CanPhase.APPROACH_CAN, CanPhase.ALIGN_CAN, CanPhase.DESCEND}
            else 0.0
        )
        self._last_yaw_error = active_yaw_error
        return self._move(eef, np.asarray(target), gripper, active_yaw_error)
