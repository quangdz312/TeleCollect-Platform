"""Finite-state scripted operator for NutAssemblySquare."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from src.sim.perturbations.variations import SquareVariation


class SquarePhase(str, Enum):
    APPROACH_NUT = "approach_nut"
    ALIGN_NUT = "align_nut"
    DESCEND = "descend"
    GRASP = "grasp"
    LIFT = "lift"
    APPROACH_PEG = "approach_peg"
    DESCEND_ON_PEG = "descend_on_peg"
    RELEASE = "release"
    RETREAT = "retreat"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class SquareOperatorConfig:
    position_gain: float = 10.0
    orientation_gain: float = 2.0
    xy_tolerance: float = 0.012
    z_tolerance: float = 0.012
    yaw_tolerance: float = 0.08
    approach_height: float = 0.14
    align_height: float = 0.065
    grasp_height_offset: float = 0.0
    lift_height: float = 0.18
    peg_approach_height: float = 0.20
    peg_release_height: float = 0.075
    retreat_height: float = 0.20
    grasp_duration: int = 15
    release_duration: int = 15
    phase_timeout: int = 120
    episode_timeout: int = 500
    grasp_min_lift: float = 0.035
    grasp_check_duration: int = 25
    max_grasp_attempts: int = 2
    square_handle_local_offset: tuple[float, float, float] = (0.054, 0.0, 0.0)
    placement_xy_tolerance: float = 0.03
    placement_height_above_table: float = 0.05
    open_gripper: float = -1.0
    closed_gripper: float = 1.0


class ScriptedSquareOperator:
    VERSION = "1.2"

    def __init__(self, env: Any, config: SquareOperatorConfig | None = None) -> None:
        self.env = env
        self.config = config or SquareOperatorConfig()
        low, high = env.action_spec
        self._low = np.asarray(low, dtype=np.float64)
        self._high = np.asarray(high, dtype=np.float64)
        if self._low.shape != (7,) or self._high.shape != (7,):
            raise ValueError(f"OSC_POSE Square operator requires action shape (7,), got {self._low.shape}")
        self._handle_local_offset = np.asarray(
            self.config.square_handle_local_offset,
            dtype=np.float64,
        )
        if self._handle_local_offset.shape != (3,) or not np.isfinite(
            self._handle_local_offset,
        ).all():
            raise ValueError('Square handle local offset must be a finite vector of shape (3,)')
        self.target_peg_id = int(env.peg1_body_id)
        table_offset = np.asarray(getattr(env, 'table_offset', [0.0, 0.0, 0.82]))
        if table_offset.shape != (3,) or not np.isfinite(table_offset).all():
            raise ValueError('Square table offset must be a finite vector of shape (3,)')
        self._placement_max_z = float(
            table_offset[2] + self.config.placement_height_above_table,
        )
        self._variation: SquareVariation | None = None
        self.reset()

    def reset(self) -> None:
        self.phase = SquarePhase.APPROACH_NUT
        self.phase_steps = 0
        self.total_steps = 0
        self._grasp_nut_position: np.ndarray | None = None
        self.failure_reason: str | None = None
        self._grasp_eef_position: np.ndarray | None = None
        self._eef_to_nut_offset: np.ndarray | None = None
        self._grasp_attempts = 0
        self._retry_count = 0
        self._retry_source: str | None = None
        self._desired_grasp_quaternion: np.ndarray | None = None
        self._desired_peg_quaternion: np.ndarray | None = None
        self.peg_position = np.asarray(
            self.env.sim.data.body_xpos[self.env.peg1_body_id], dtype=np.float64,
        ).copy()
        self.failure_stage: str | None = None
        self._last_target_position: np.ndarray | None = None
        self._last_yaw_error = 0.0
        self._active_semantic_mask: tuple[str, ...] = ()
        self._variation = None

    def set_variation(self, variation: SquareVariation) -> None:
        if not isinstance(variation, SquareVariation):
            raise TypeError('Square operator requires SquareVariation')
        numeric_fields = (
            variation.square_grasp_xyz_offset,
            variation.transport_waypoint_offset,
            variation.peg_target_xyz_offset,
            (
                variation.square_grasp_yaw_bias,
                variation.peg_alignment_yaw_bias,
                variation.peg_approach_height_offset,
                variation.insert_depth_offset,
            ),
        )
        if not all(np.isfinite(np.asarray(field, dtype=np.float64)).all() for field in numeric_fields):
            raise ValueError('Square semantic variation must contain only finite values')
        self._variation = variation

    @property
    def finished(self) -> bool:
        return self.phase in (SquarePhase.DONE, SquarePhase.FAILED)

    @property
    def debug_info(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value, "phase_steps": self.phase_steps,
            "total_steps": self.total_steps, "failure_reason": self.failure_reason,
            'failure_stage': self.failure_stage,
            "grasp_attempts": self._grasp_attempts,
            'retry_count': self._retry_count,
            'retry_source': self._retry_source,
            'target_peg_id': self.target_peg_id,
            "peg_position": self.peg_position.tolist(),
            'last_target_position': (
                None if self._last_target_position is None else self._last_target_position.tolist()
            ),
            'desired_grasp_quaternion': (
                None
                if self._desired_grasp_quaternion is None
                else self._desired_grasp_quaternion.tolist()
            ),
            'desired_peg_quaternion': (
                None
                if self._desired_peg_quaternion is None
                else self._desired_peg_quaternion.tolist()
            ),
            'last_yaw_error': self._last_yaw_error,
            'active_semantic_mask': self._active_semantic_mask,
        }

    def _transition(self, phase: SquarePhase) -> None:
        self.phase = phase
        self.phase_steps = 0

    def _fail(self, reason: str) -> None:
        self.failure_reason = reason
        self.failure_stage = self.phase.value
        self._transition(SquarePhase.FAILED)

    def _retry_available(self) -> bool:
        variation = self._variation
        return bool(
            variation is not None
            and not variation.is_zero
            and variation.retry_enabled
            and self._retry_count < min(1, variation.retry_cap)
        )

    def _start_retry(self, source: str) -> None:
        self._retry_count += 1
        self._retry_source = source
        self._grasp_nut_position = None
        self._grasp_eef_position = None
        self._eef_to_nut_offset = None
        self._desired_grasp_quaternion = None
        self._desired_peg_quaternion = None
        self._transition(SquarePhase.APPROACH_NUT)

    def _reached(self, current: np.ndarray, target: np.ndarray) -> bool:
        return bool(
            np.linalg.norm(current[:2] - target[:2]) <= self.config.xy_tolerance
            and abs(float(current[2] - target[2])) <= self.config.z_tolerance
        )

    @staticmethod
    def _yaw(quaternion: np.ndarray) -> float:
        x, y, z, w = quaternion
        return float(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))

    @staticmethod
    def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(quaternion))
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError('invalid Square quaternion')
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

    @classmethod
    def _rotation_matrix(cls, quaternion: np.ndarray) -> np.ndarray:
        x, y, z, w = cls._normalize_quaternion(quaternion)
        return np.array(
            [
                [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
                [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
                [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
            ],
            dtype=np.float64,
        )

    def _handle_from_observation(
        self,
        nut_position: np.ndarray,
        nut_quaternion: np.ndarray,
    ) -> np.ndarray:
        return nut_position + self._rotation_matrix(nut_quaternion) @ self._handle_local_offset

    def _grasp_yaw_error(
        self,
        nut_quaternion: np.ndarray,
        eef_quaternion: np.ndarray,
        *,
        biased: bool,
    ) -> float:
        bias = (
            0.0
            if not biased or self._variation is None
            else self._variation.square_grasp_yaw_bias
        )
        if bias == 0.0:
            return self._symmetric_yaw_error(
                self._yaw(nut_quaternion),
                self._yaw(eef_quaternion),
            )
        if self._desired_grasp_quaternion is None:
            yaw_delta = np.array(
                [0.0, 0.0, np.sin(bias / 2.0), np.cos(bias / 2.0)],
                dtype=np.float64,
            )
            self._desired_grasp_quaternion = self._normalize_quaternion(
                self._quaternion_multiply(
                    yaw_delta,
                    self._normalize_quaternion(nut_quaternion),
                ),
            )
        return self._symmetric_yaw_error(
            self._yaw(self._desired_grasp_quaternion),
            self._yaw(eef_quaternion),
        )

    def _peg_yaw_error(self, eef_quaternion: np.ndarray, *, biased: bool) -> float:
        bias = (
            0.0
            if not biased or self._variation is None
            else self._variation.peg_alignment_yaw_bias
        )
        if bias != 0.0 and self._desired_peg_quaternion is None:
            self._desired_peg_quaternion = self._normalize_quaternion(
                np.array(
                    [0.0, 0.0, np.sin(bias / 2.0), np.cos(bias / 2.0)],
                    dtype=np.float64,
                ),
            )
        target_yaw = 0.0 if bias == 0.0 else self._yaw(self._desired_peg_quaternion)
        return self._symmetric_yaw_error(target_yaw, self._yaw(eef_quaternion))

    def _nut_on_target(self, nut_position: np.ndarray) -> bool:
        return bool(
            abs(float(nut_position[0] - self.peg_position[0])) < self.config.placement_xy_tolerance
            and abs(float(nut_position[1] - self.peg_position[1])) < self.config.placement_xy_tolerance
            and float(nut_position[2]) < self._placement_max_z
        )

    @staticmethod
    def _symmetric_yaw_error(target: float, current: float) -> float:
        candidates = [target + k * np.pi - current for k in range(-2, 3)]
        wrapped = [float((value + np.pi) % (2 * np.pi) - np.pi) for value in candidates]
        return min(wrapped, key=abs)

    def _move(self, current: np.ndarray, target: np.ndarray, gripper: float, yaw_error: float = 0.0) -> np.ndarray:
        action = np.zeros(7, dtype=np.float64)
        action[:3] = self.config.position_gain * (target - current)
        action[5] = self.config.orientation_gain * yaw_error
        action[6] = gripper
        return np.clip(action, self._low, self._high)

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        if self.finished:
            return np.clip(np.array([0, 0, 0, 0, 0, 0, self.config.open_gripper]), self._low, self._high)
        for key in ("robot0_eef_pos", "robot0_eef_quat", "SquareNut_pos", "SquareNut_quat"):
            if key not in observation:
                self._fail(f"missing observation key: {key}")
                return np.zeros(7, dtype=np.float64)
        eef = np.asarray(observation["robot0_eef_pos"], dtype=np.float64)
        eef_quat = np.asarray(observation["robot0_eef_quat"], dtype=np.float64)
        nut = np.asarray(observation["SquareNut_pos"], dtype=np.float64)
        nut_quat = np.asarray(observation["SquareNut_quat"], dtype=np.float64)
        if (
            eef.shape != (3,) or nut.shape != (3,) or eef_quat.shape != (4,)
            or nut_quat.shape != (4,)
            or not all(np.all(np.isfinite(value)) for value in (eef, nut, eef_quat, nut_quat))
        ):
            self._fail("invalid end-effector or square nut pose")
            return np.zeros(7, dtype=np.float64)
        try:
            handle = self._handle_from_observation(nut, nut_quat)
        except ValueError as exc:
            self._fail(str(exc))
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
            (0.0, 0.0, 0.0) if semantic is None else semantic.square_grasp_xyz_offset,
        )
        transport_offset = np.asarray(
            (0.0, 0.0, 0.0) if semantic is None else semantic.transport_waypoint_offset,
        )
        peg_offset = np.asarray(
            (0.0, 0.0, 0.0) if semantic is None else semantic.peg_target_xyz_offset,
        )
        close_timing = 0 if semantic is None else semantic.gripper_close_timing_offset
        release_timing = 0 if semantic is None else semantic.release_timing_offset
        approach_height_offset = (
            0.0 if semantic is None else semantic.peg_approach_height_offset
        )
        insert_depth = 0.0 if semantic is None else semantic.insert_depth_offset
        eef_yaw = self._yaw(eef_quat)
        try:
            grasp_yaw_error = self._grasp_yaw_error(
                nut_quat,
                eef_quat,
                biased=self.phase
                in {SquarePhase.APPROACH_NUT, SquarePhase.ALIGN_NUT, SquarePhase.DESCEND},
            )
            peg_yaw_error = self._peg_yaw_error(
                eef_quat,
                biased=self.phase in {SquarePhase.APPROACH_PEG, SquarePhase.DESCEND_ON_PEG},
            )
        except ValueError as exc:
            self._fail(str(exc))
            return self._move(eef, eef, c.open_gripper)
        if self.phase == SquarePhase.GRASP:
            close_step = max(1, 1 + close_timing)
            gripper = c.closed_gripper if self.phase_steps >= close_step else c.open_gripper
        elif self.phase in {
            SquarePhase.LIFT,
            SquarePhase.APPROACH_PEG,
            SquarePhase.DESCEND_ON_PEG,
        }:
            gripper = c.closed_gripper
        elif self.phase == SquarePhase.RELEASE:
            open_step = max(1, 1 + release_timing)
            gripper = c.open_gripper if self.phase_steps >= open_step else c.closed_gripper
        else:
            gripper = c.open_gripper
        if self.phase == SquarePhase.APPROACH_NUT:
            target = handle + grasp_offset + [0.0, 0.0, c.approach_height]
            if self._reached(eef, target) and abs(grasp_yaw_error) <= c.yaw_tolerance:
                self._transition(SquarePhase.ALIGN_NUT)
        elif self.phase == SquarePhase.ALIGN_NUT:
            target = handle + grasp_offset + [0.0, 0.0, c.align_height]
            if self._reached(eef, target) and abs(grasp_yaw_error) <= c.yaw_tolerance:
                self._transition(SquarePhase.DESCEND)
        elif self.phase == SquarePhase.DESCEND:
            target = handle + grasp_offset + [0.0, 0.0, c.grasp_height_offset]
            if self._reached(eef, target) and abs(grasp_yaw_error) <= c.yaw_tolerance:
                self._grasp_nut_position = nut.copy()
                self._grasp_eef_position = eef.copy()
                self._eef_to_nut_offset = eef - nut
                self._grasp_attempts += 1
                self._transition(SquarePhase.GRASP)
                if close_timing < 0:
                    gripper = c.closed_gripper
        elif self.phase == SquarePhase.GRASP:
            target = eef.copy()
            if self.phase_steps >= c.grasp_duration:
                self._transition(SquarePhase.LIFT)
        elif self.phase == SquarePhase.LIFT:
            if self._grasp_nut_position is None or self._grasp_eef_position is None:
                self._fail("missing grasp position")
                target = eef.copy()
            else:
                target = self._grasp_eef_position + transport_offset + [0.0, 0.0, c.lift_height]
                lifted = nut[2] >= self._grasp_nut_position[2] + c.grasp_min_lift
                if self.phase_steps > c.grasp_check_duration and not lifted:
                    if self._grasp_attempts < c.max_grasp_attempts:
                        # A marginal grasp can nudge the nut. Reacquire its
                        # live handle pose and retry instead of replaying the
                        # stale trajectory or terminating the episode.
                        if semantic is not None and not semantic.is_zero:
                            if self._retry_available():
                                self._start_retry('grasp_verification')
                            else:
                                self._fail('perturbation retry exhausted: square nut did not lift')
                        else:
                            self._grasp_nut_position = None
                            self._grasp_eef_position = None
                            self._eef_to_nut_offset = None
                            self._transition(SquarePhase.APPROACH_NUT)
                    else:
                        self._fail(
                            f"missed grasp after {self._grasp_attempts} attempts: "
                            "square nut did not lift"
                        )
                if not self.finished and lifted and self._reached(eef, target):
                    self._transition(SquarePhase.APPROACH_PEG)
        elif self.phase == SquarePhase.APPROACH_PEG:
            if self._eef_to_nut_offset is None:
                self._fail("missing grasp offset")
                self._eef_to_nut_offset = np.zeros(3)
            current_offset = eef - nut
            target = (
                self.peg_position
                + peg_offset
                + transport_offset
                + [0.0, 0.0, c.peg_approach_height + approach_height_offset]
                + current_offset
            )
            if self._reached(eef, target) and abs(peg_yaw_error) <= c.yaw_tolerance:
                self._transition(SquarePhase.DESCEND_ON_PEG)
        elif self.phase == SquarePhase.DESCEND_ON_PEG:
            current_offset = eef - nut
            target = (
                self.peg_position
                + peg_offset
                + [0.0, 0.0, c.peg_release_height - insert_depth]
                + current_offset
            )
            if self._reached(eef, target) and abs(peg_yaw_error) <= c.yaw_tolerance:
                self._transition(SquarePhase.RELEASE)
                if release_timing < 0:
                    gripper = c.open_gripper
        elif self.phase == SquarePhase.RELEASE:
            target = eef.copy()
            if self.phase_steps >= c.release_duration:
                placement_missed = bool(
                    semantic is not None
                    and not semantic.is_zero
                    and semantic.retry_enabled
                    and not self._nut_on_target(nut)
                )
                if placement_missed and self._retry_available():
                    self._start_retry('placement_verification')
                    gripper = c.open_gripper
                elif placement_missed:
                    self._fail('placement recovery exhausted: square nut is not on target peg')
                else:
                    self._transition(SquarePhase.RETREAT)
        elif self.phase == SquarePhase.RETREAT:
            target = self.peg_position + [0.0, 0.0, c.retreat_height]
            if self._reached(eef, target):
                self._transition(SquarePhase.DONE)
        else:
            target = eef.copy()
        if self.phase == SquarePhase.FAILED:
            gripper = c.open_gripper
        active: list[str] = []
        if semantic is not None and not semantic.is_zero:
            if phase_at_start in {
                SquarePhase.APPROACH_NUT,
                SquarePhase.ALIGN_NUT,
                SquarePhase.DESCEND,
            }:
                if any(value != 0.0 for value in semantic.square_grasp_xyz_offset):
                    active.append('square_grasp_xyz_offset')
                if semantic.square_grasp_yaw_bias != 0.0:
                    active.append('square_grasp_yaw_bias')
            if phase_at_start == SquarePhase.GRASP and close_timing != 0:
                active.append('gripper_close_timing_offset')
            if phase_at_start in {SquarePhase.LIFT, SquarePhase.APPROACH_PEG} and any(
                value != 0.0 for value in semantic.transport_waypoint_offset
            ):
                active.append('transport_waypoint_offset')
            if phase_at_start in {SquarePhase.APPROACH_PEG, SquarePhase.DESCEND_ON_PEG} and any(
                value != 0.0 for value in semantic.peg_target_xyz_offset
            ):
                active.append('peg_target_xyz_offset')
            if phase_at_start in {SquarePhase.APPROACH_PEG, SquarePhase.DESCEND_ON_PEG} and (
                semantic.peg_alignment_yaw_bias != 0.0
            ):
                active.append('peg_alignment_yaw_bias')
            if phase_at_start == SquarePhase.APPROACH_PEG and approach_height_offset != 0.0:
                active.append('peg_approach_height_offset')
            if phase_at_start == SquarePhase.DESCEND_ON_PEG and insert_depth != 0.0:
                active.append('insert_depth_offset')
            if phase_at_start == SquarePhase.RELEASE and release_timing != 0:
                active.append('release_timing_offset')
            if self._retry_source is not None and self.phase == SquarePhase.APPROACH_NUT:
                active.append('bounded_retry')
        self._active_semantic_mask = tuple(active)
        self._last_target_position = np.asarray(target, dtype=np.float64).copy()
        yaw_error = grasp_yaw_error if self.phase in {
            SquarePhase.APPROACH_NUT, SquarePhase.ALIGN_NUT, SquarePhase.DESCEND,
            SquarePhase.GRASP, SquarePhase.LIFT,
        } else peg_yaw_error
        self._last_yaw_error = yaw_error
        return self._move(eef, np.asarray(target), gripper, yaw_error)
