"""Deterministic, episode-scoped perturbation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .profiles import (
    NOISE_STREAM_CODE,
    Quality,
    ResolvedProfile,
)
from .variations import (
    EpisodeVariation,
    EventSchedule,
    variation_type_for_task,
)


MAX_BASE_SEED = 2_147_483_647


@dataclass(frozen=True)
class SeedProvenance:
    base_seed: int
    task_code: int
    episode_index: int
    stream_code: int = NOISE_STREAM_CODE


@dataclass(frozen=True)
class RuntimeStepDiagnostics:
    timestep: int
    phase: str | None
    active_noise_mask: tuple[str, ...]
    planned_action: tuple[float, ...]
    executed_action: tuple[float, ...]
    retry_cap: int


def _validate_seed_inputs(base_seed: int, episode_index: int) -> None:
    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise TypeError("base_seed must be an integer")
    if not 0 <= base_seed <= MAX_BASE_SEED:
        raise ValueError(f"base_seed must be in 0..{MAX_BASE_SEED}")
    if isinstance(episode_index, bool) or not isinstance(episode_index, int):
        raise TypeError("episode_index must be an integer")
    if episode_index < 0:
        raise ValueError("episode_index must be non-negative")


def environment_seed(base_seed: int, episode_index: int) -> int:
    """Return the unchanged legacy reset seed contract."""

    _validate_seed_inputs(base_seed, episode_index)
    return base_seed + episode_index


def _action_bounds(action_spec: Any) -> tuple[np.ndarray, np.ndarray]:
    try:
        low, high = action_spec
    except (TypeError, ValueError) as exc:
        raise ValueError("action_spec must contain low and high bounds") from exc
    low_array = np.asarray(low)
    high_array = np.asarray(high)
    if low_array.shape != (7,) or high_array.shape != (7,):
        raise ValueError("action_spec bounds must both have shape (7,)")
    if not np.isfinite(low_array).all() or not np.isfinite(high_array).all():
        raise ValueError("action_spec bounds must be finite")
    if np.any(low_array > high_array):
        raise ValueError("action_spec lower bounds must not exceed upper bounds")
    return low_array.copy(), high_array.copy()


def validate_and_clip_action(action: Any, action_spec: Any) -> np.ndarray:
    """Validate a seven-dimensional action and clip it to environment bounds."""

    planned = np.asarray(action)
    if planned.shape != (7,):
        raise ValueError(f"Expected action shape (7,), got {planned.shape}")
    if not np.isfinite(planned).all():
        raise ValueError("Action must contain only finite values")
    low, high = _action_bounds(action_spec)
    if np.all(planned >= low) and np.all(planned <= high):
        return planned.copy()
    return np.clip(planned, low, high)


def _event_schedule(
    rng: np.random.Generator,
    profile: ResolvedProfile,
) -> EventSchedule:
    if profile.quality == Quality.CLEAN:
        return EventSchedule()
    event_count = max(1, int(np.ceil(profile.noise_scale * 3.0)))
    has_generic_gripper_events = (
        profile.limits.lift is None
        and profile.limits.can is None
        and profile.limits.square is None
    )
    required = 2 * event_count + (2 if has_generic_gripper_events else 0)
    candidates = np.arange(2, profile.limits.event_window_steps, dtype=np.int64)
    sampled = [int(value) for value in rng.choice(candidates, size=required, replace=False)]
    gripper_pair = sorted(sampled[-2:]) if has_generic_gripper_events else []
    return EventSchedule(
        action_delay_steps=tuple(sorted(sampled[:event_count])),
        pause_steps=tuple(sorted(sampled[event_count : 2 * event_count])),
        gripper_close_steps=(gripper_pair[0],) if gripper_pair else (),
        gripper_open_steps=(gripper_pair[1],) if gripper_pair else (),
    )


def _sample_variation(
    rng: np.random.Generator | None,
    profile: ResolvedProfile,
) -> EpisodeVariation:
    variation_type = variation_type_for_task(profile.task.value)
    if profile.quality == Quality.CLEAN:
        return variation_type(
            quality=profile.quality.value,
            noise_scale=0.0,
            landmark_position_bias=(0.0, 0.0, 0.0),
            landmark_orientation_bias=(0.0, 0.0, 0.0),
            arm_gain=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
            arm_bias=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            schedule=EventSchedule(),
            retry_cap=0,
        )
    if rng is None:
        raise RuntimeError("Non-clean variation sampling requires a noise RNG")

    scale = profile.noise_scale
    position = tuple(
        float(value)
        for value in rng.uniform(-1.0, 1.0, 3) * profile.limits.position_bias_m * scale
    )
    orientation = tuple(
        float(value)
        for value in rng.uniform(-1.0, 1.0, 3)
        * profile.limits.orientation_bias_rad
        * scale
    )
    arm_gain = tuple(
        float(value)
        for value in 1.0
        + rng.uniform(-1.0, 1.0, 6) * profile.limits.arm_gain_delta * scale
    )
    arm_bias = tuple(
        float(value)
        for value in rng.uniform(-1.0, 1.0, 6) * profile.limits.arm_bias * scale
    )
    retry_cap = 0 if profile.quality == Quality.POOR else 1
    semantic_fields: dict[str, Any] = {}
    if profile.limits.lift is not None:
        limits = profile.limits.lift
        grasp_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.grasp_xyz_offset_m)
            * scale
        )
        grasp_yaw_bias = float(
            rng.uniform(-1.0, 1.0) * limits.grasp_yaw_bias_rad * scale,
        )
        close_timing = int(
            np.rint(
                rng.uniform(-1.0, 1.0)
                * limits.gripper_close_timing_steps
                * scale,
            ),
        )
        lift_height_delta = float(
            rng.uniform(-1.0, 1.0) * limits.lift_height_delta_m * scale,
        )
        lateral = rng.uniform(-1.0, 1.0, 2)
        lateral_norm = float(np.linalg.norm(lateral))
        if lateral_norm > 1.0:
            lateral /= lateral_norm
        lift_lateral_offset = tuple(
            float(value) for value in lateral * limits.lift_lateral_offset_m * scale
        )
        retry_cap = limits.regrasp_cap
        semantic_fields = {
            "grasp_xyz_offset": grasp_offset,
            "grasp_yaw_bias": grasp_yaw_bias,
            "gripper_close_timing_offset": close_timing,
            "lift_height_delta": lift_height_delta,
            "lift_lateral_offset": lift_lateral_offset,
            "regrasp_enabled": retry_cap > 0,
        }
    elif profile.limits.can is not None:
        limits = profile.limits.can
        grasp_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.grasp_xyz_offset_m)
            * scale
        )
        grasp_yaw_bias = float(
            rng.uniform(-1.0, 1.0) * limits.grasp_yaw_bias_rad * scale,
        )
        close_timing = max(
            -1,
            int(
                np.rint(
                    rng.uniform(-1.0, 1.0)
                    * limits.gripper_close_timing_steps
                    * scale,
                ),
            ),
        )
        transport_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.transport_waypoint_offset_m)
            * scale
        )
        bin_target_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.bin_target_xyz_offset_m)
            * scale
        )
        release_height_offset = float(
            rng.uniform(-1.0, 1.0) * limits.release_height_offset_m * scale,
        )
        release_timing = max(
            -1,
            int(
                np.rint(
                    rng.uniform(-1.0, 1.0)
                    * limits.release_timing_steps
                    * scale,
                ),
            ),
        )
        retry_cap = limits.retry_cap
        semantic_fields = {
            'can_grasp_xyz_offset': grasp_offset,
            'can_grasp_yaw_bias': grasp_yaw_bias,
            'gripper_close_timing_offset': close_timing,
            'transport_waypoint_offset': transport_offset,
            'bin_target_xyz_offset': bin_target_offset,
            'release_height_offset': release_height_offset,
            'release_timing_offset': release_timing,
            'retry_enabled': retry_cap > 0,
        }
    elif profile.limits.square is not None:
        limits = profile.limits.square
        grasp_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.grasp_xyz_offset_m)
            * scale
        )
        grasp_yaw_bias = float(
            rng.uniform(-1.0, 1.0) * limits.grasp_yaw_bias_rad * scale,
        )
        close_timing = max(
            -1,
            int(
                np.rint(
                    rng.uniform(-1.0, 1.0)
                    * limits.gripper_close_timing_steps
                    * scale,
                ),
            ),
        )
        transport_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.transport_waypoint_offset_m)
            * scale
        )
        peg_target_offset = tuple(
            float(value)
            for value in rng.uniform(-1.0, 1.0, 3)
            * np.asarray(limits.peg_target_xyz_offset_m)
            * scale
        )
        peg_yaw_bias = float(
            rng.uniform(-1.0, 1.0) * limits.peg_alignment_yaw_bias_rad * scale,
        )
        peg_approach_height = float(
            rng.uniform(-1.0, 1.0)
            * limits.peg_approach_height_offset_m
            * scale,
        )
        insert_depth = float(
            rng.uniform(-1.0, 1.0) * limits.insert_depth_offset_m * scale,
        )
        release_timing = max(
            -1,
            int(
                np.rint(
                    rng.uniform(-1.0, 1.0)
                    * limits.release_timing_steps
                    * scale,
                ),
            ),
        )
        retry_cap = limits.retry_cap
        semantic_fields = {
            'square_grasp_xyz_offset': grasp_offset,
            'square_grasp_yaw_bias': grasp_yaw_bias,
            'gripper_close_timing_offset': close_timing,
            'transport_waypoint_offset': transport_offset,
            'peg_target_xyz_offset': peg_target_offset,
            'peg_alignment_yaw_bias': peg_yaw_bias,
            'peg_approach_height_offset': peg_approach_height,
            'insert_depth_offset': insert_depth,
            'release_timing_offset': release_timing,
            'retry_enabled': retry_cap > 0,
        }
    return variation_type(
        quality=profile.quality.value,
        noise_scale=scale,
        landmark_position_bias=position,
        landmark_orientation_bias=orientation,
        arm_gain=arm_gain,
        arm_bias=arm_bias,
        schedule=_event_schedule(rng, profile),
        retry_cap=retry_cap,
        **semantic_fields,
    )


def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(quaternion))
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError("Landmark quaternion must be finite and non-zero")
    return quaternion / norm


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


def _rotation_vector_quaternion(rotation_vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(rotation_vector))
    if angle < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    axis = rotation_vector / angle
    return np.concatenate((axis * np.sin(angle / 2.0), [np.cos(angle / 2.0)]))


class PerturbationRuntime:
    """Apply one immutable variation without timestep RNG calls."""

    def __init__(
        self,
        profile: ResolvedProfile,
        action_spec: Any,
        *,
        base_seed: int,
        episode_index: int,
        position_landmarks: tuple[str, ...] = (),
        orientation_landmarks: tuple[str, ...] = (),
    ) -> None:
        _validate_seed_inputs(base_seed, episode_index)
        self.profile = profile
        self.seed_provenance = SeedProvenance(
            base_seed=base_seed,
            task_code=profile.task_code,
            episode_index=episode_index,
        )
        self._low, self._high = _action_bounds(action_spec)
        self.position_landmarks = tuple(position_landmarks)
        self.orientation_landmarks = tuple(orientation_landmarks)
        self._variation: EpisodeVariation | None = None
        self._previous_executed_arm: np.ndarray | None = None
        self._last_diagnostics: RuntimeStepDiagnostics | None = None

    @property
    def variation(self) -> EpisodeVariation:
        if self._variation is None:
            raise RuntimeError("PerturbationRuntime must be reset before use")
        return self._variation

    @property
    def last_diagnostics(self) -> RuntimeStepDiagnostics | None:
        return self._last_diagnostics

    def reset_episode(self) -> EpisodeVariation:
        """Sample the complete immutable variation and schedule exactly once."""

        provenance = self.seed_provenance
        rng = None
        if self.profile.quality != Quality.CLEAN:
            seed_sequence = np.random.SeedSequence(
                [
                    provenance.base_seed,
                    provenance.task_code,
                    provenance.episode_index,
                    provenance.stream_code,
                ],
            )
            rng = np.random.Generator(np.random.PCG64(seed_sequence))
        self._variation = _sample_variation(rng, self.profile)
        self._previous_executed_arm = None
        self._last_diagnostics = None
        return self._variation

    def policy_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Return a biased operator-only view without mutating or extending core obs."""

        variation = self.variation
        if variation.is_zero:
            return observation
        result = dict(observation)
        position_bias = np.asarray(variation.landmark_position_bias)
        for key in self.position_landmarks:
            if key not in result:
                continue
            value = np.asarray(result[key])
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError(f"Position landmark {key!r} must be finite with shape (3,)")
            result[key] = value.astype(np.float64, copy=True) + position_bias

        orientation_bias = np.asarray(variation.landmark_orientation_bias)
        delta_quaternion = _rotation_vector_quaternion(orientation_bias)
        for key in self.orientation_landmarks:
            if key not in result:
                continue
            value = np.asarray(result[key])
            if value.shape != (4,) or not np.isfinite(value).all():
                raise ValueError(f"Orientation landmark {key!r} must be finite with shape (4,)")
            result[key] = _normalize_quaternion(
                _quaternion_multiply(delta_quaternion, _normalize_quaternion(value)),
            )
        return result

    def apply_action(
        self,
        planned_action: Any,
        timestep: int,
        *,
        phase: str | None = None,
    ) -> np.ndarray:
        """Apply deterministic action/timing events and return the executed action."""

        if isinstance(timestep, bool) or not isinstance(timestep, int) or timestep < 0:
            raise ValueError("timestep must be a non-negative integer")
        planned = np.asarray(planned_action)
        if planned.shape != (7,):
            raise ValueError(f"Expected action shape (7,), got {planned.shape}")
        if not np.isfinite(planned).all():
            raise ValueError("Action must contain only finite values")

        variation = self.variation
        active: list[str] = []
        if variation.is_zero:
            executed = validate_and_clip_action(planned, (self._low, self._high))
        else:
            arm = planned[:6].astype(np.float64) * np.asarray(variation.arm_gain)
            arm += np.asarray(variation.arm_bias)
            active.extend(("arm_gain", "arm_bias"))
            if timestep in variation.schedule.action_delay_steps:
                arm = (
                    np.zeros(6, dtype=np.float64)
                    if self._previous_executed_arm is None
                    else self._previous_executed_arm.copy()
                )
                active.append("action_delay")
            if timestep in variation.schedule.pause_steps:
                arm = np.zeros(6, dtype=np.float64)
                active.append("pause")

            gripper = float(planned[6])
            if timestep in variation.schedule.gripper_close_steps:
                gripper = float(self._high[6])
                active.append("gripper_close")
            elif timestep in variation.schedule.gripper_open_steps:
                gripper = float(self._low[6])
                active.append("gripper_open")
            executed = validate_and_clip_action(
                np.concatenate((arm, [gripper])),
                (self._low, self._high),
            )

        self._previous_executed_arm = executed[:6].astype(np.float64, copy=True)
        self._last_diagnostics = RuntimeStepDiagnostics(
            timestep=timestep,
            phase=phase,
            active_noise_mask=tuple(active),
            planned_action=tuple(float(value) for value in planned),
            executed_action=tuple(float(value) for value in executed),
            retry_cap=variation.retry_cap,
        )
        return executed
