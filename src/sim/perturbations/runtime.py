"""Deterministic, episode-scoped perturbation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .profiles import (
    NOISE_STREAM_CODE,
    PerturbationTask,
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
    phase_scale: float
    fault_type: str


@dataclass(frozen=True)
class PhaseNoisePolicy:
    action_scale: float
    perception_scale: float
    allow_delay: bool = True
    allow_pause: bool = True


_DEFAULT_PHASE_POLICY = PhaseNoisePolicy(0.0, 0.0, False, False)
_PHASE_POLICIES: dict[PerturbationTask, dict[str, PhaseNoisePolicy]] = {
    PerturbationTask.LIFT: {
        "approach_cube": PhaseNoisePolicy(1.00, 1.00),
        "align_cube": PhaseNoisePolicy(0.70, 0.70),
        "descend": PhaseNoisePolicy(0.45, 0.45, False, False),
        "settle_before_grasp": PhaseNoisePolicy(0.0, 0.0, False, False),
        "recover_align": PhaseNoisePolicy(0.0, 0.0, False, False),
        "recover_descend": PhaseNoisePolicy(0.0, 0.0, False, False),
        "grasp": PhaseNoisePolicy(0.35, 0.35, False, False),
        "lift": PhaseNoisePolicy(0.55, 0.35, True, False),
        "hold": PhaseNoisePolicy(0.10, 0.10, False, False),
    },
    PerturbationTask.CAN: {
        "approach_can": PhaseNoisePolicy(1.00, 1.00),
        "align_can": PhaseNoisePolicy(0.70, 0.70),
        "descend": PhaseNoisePolicy(0.45, 0.45, False, False),
        "grasp": PhaseNoisePolicy(0.35, 0.35, False, False),
        "lift": PhaseNoisePolicy(0.55, 0.40, True, False),
        "approach_bin": PhaseNoisePolicy(0.70, 0.50),
        "descend_into_bin": PhaseNoisePolicy(0.25, 0.20, False, False),
        "release": PhaseNoisePolicy(0.10, 0.10, False, False),
        "retreat": PhaseNoisePolicy(0.00, 0.00, False, False),
    },
    PerturbationTask.SQUARE: {
        "approach_nut": PhaseNoisePolicy(1.00, 1.00),
        "align_nut": PhaseNoisePolicy(0.65, 0.65),
        "descend": PhaseNoisePolicy(0.40, 0.40, False, False),
        "grasp": PhaseNoisePolicy(0.30, 0.30, False, False),
        "lift": PhaseNoisePolicy(0.55, 0.40, True, False),
        "approach_peg": PhaseNoisePolicy(0.25, 0.20, False, False),
        "descend_on_peg": PhaseNoisePolicy(0.08, 0.05, False, False),
        "release": PhaseNoisePolicy(0.00, 0.00, False, False),
        "retreat": PhaseNoisePolicy(0.00, 0.00, False, False),
    },
    PerturbationTask.TOOL_HANG: {
        # Stage 1: preserve diversity while approaching and transporting the
        # frame, then remove perturbation before contact-critical insertion.
        "reach_grip": PhaseNoisePolicy(1.00, 0.00),
        "descend_grip": PhaseNoisePolicy(0.45, 0.00, False, False),
        "close_gripper": PhaseNoisePolicy(0.20, 0.00, False, False),
        "lift": PhaseNoisePolicy(0.55, 0.00, True, False),
        "upright": PhaseNoisePolicy(0.50, 0.00),
        "transport": PhaseNoisePolicy(0.65, 0.00),
        "align": PhaseNoisePolicy(0.15, 0.00, False, False),
        "insert": PhaseNoisePolicy(0.00, 0.00, False, False),
        "search": PhaseNoisePolicy(0.00, 0.00, False, False),
        "release": PhaseNoisePolicy(0.00, 0.00, False, False),
        "retreat": PhaseNoisePolicy(0.00, 0.00, False, False),
        # Stage 2 restarts at a high-noise approach and decays independently
        # toward the hole / thread operation.
        "home": PhaseNoisePolicy(1.00, 0.00),
        "descend_tool": PhaseNoisePolicy(0.45, 0.00, False, False),
        "close_tool": PhaseNoisePolicy(0.20, 0.00, False, False),
        "lift_tool": PhaseNoisePolicy(0.55, 0.00, True, False),
        "orient_tool": PhaseNoisePolicy(0.50, 0.00),
        "align_handle": PhaseNoisePolicy(0.30, 0.00, False, False),
        "transport_tool": PhaseNoisePolicy(0.65, 0.00),
        "align_hole": PhaseNoisePolicy(0.12, 0.00, False, False),
        "lower_ring": PhaseNoisePolicy(0.00, 0.00, False, False),
        "slide_inboard": PhaseNoisePolicy(0.00, 0.00, False, False),
        "thread": PhaseNoisePolicy(0.00, 0.00, False, False),
        "release_tool": PhaseNoisePolicy(0.00, 0.00, False, False),
        "retreat_tool": PhaseNoisePolicy(0.00, 0.00, False, False),
        "done": PhaseNoisePolicy(0.00, 0.00, False, False),
    },
}


def phase_noise_policy(task: PerturbationTask, phase: str | None) -> PhaseNoisePolicy:
    if phase is None:
        return _DEFAULT_PHASE_POLICY
    return _PHASE_POLICIES[task].get(str(phase), _DEFAULT_PHASE_POLICY)


def phase_noise_policies(task: PerturbationTask) -> dict[str, PhaseNoisePolicy]:
    """Return the configured phase policy without exposing mutable internals."""

    return dict(_PHASE_POLICIES[task])


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
        profile.task != PerturbationTask.TOOL_HANG
        and
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
            fault_type="none",
            fault_phase="",
            fault_magnitude=0.0,
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
    _decay_semantic_fields(profile.task, semantic_fields)
    fault_type, fault_phase, fault_magnitude = _inject_controlled_fault(
        rng, profile, semantic_fields,
    )
    return variation_type(
        quality=profile.quality.value,
        noise_scale=scale,
        landmark_position_bias=position,
        landmark_orientation_bias=orientation,
        arm_gain=arm_gain,
        arm_bias=arm_bias,
        schedule=_event_schedule(rng, profile),
        retry_cap=retry_cap,
        fault_type=fault_type,
        fault_phase=fault_phase,
        fault_magnitude=fault_magnitude,
        **semantic_fields,
    )


_FAULT_PROBABILITY = {
    Quality.GOOD: 0.10,
    Quality.MEDIUM: 0.18,
    Quality.POOR: 0.25,
}


def _scaled_tuple(value: Any, scale: float) -> tuple[float, ...]:
    return tuple(float(item) * scale for item in value)


def _decay_semantic_fields(task: PerturbationTask, fields: dict[str, Any]) -> None:
    """Attenuate ordinary semantic jitter as precision requirements increase."""
    if task == PerturbationTask.LIFT:
        fields["grasp_xyz_offset"] = _scaled_tuple(fields["grasp_xyz_offset"], 0.70)
        fields["grasp_yaw_bias"] *= 0.70
        fields["gripper_close_timing_offset"] = int(
            np.rint(fields["gripper_close_timing_offset"] * 0.40)
        )
        fields["lift_height_delta"] *= 0.25
        fields["lift_lateral_offset"] = _scaled_tuple(fields["lift_lateral_offset"], 0.30)
    elif task == PerturbationTask.CAN:
        fields["can_grasp_xyz_offset"] = _scaled_tuple(fields["can_grasp_xyz_offset"], 0.70)
        fields["can_grasp_yaw_bias"] *= 0.70
        fields["gripper_close_timing_offset"] = int(
            np.rint(fields["gripper_close_timing_offset"] * 0.40)
        )
        fields["transport_waypoint_offset"] = _scaled_tuple(
            fields["transport_waypoint_offset"], 0.60,
        )
        fields["bin_target_xyz_offset"] = _scaled_tuple(fields["bin_target_xyz_offset"], 0.25)
        fields["release_height_offset"] *= 0.20
        fields["release_timing_offset"] = int(
            np.rint(fields["release_timing_offset"] * 0.10)
        )
    elif task == PerturbationTask.SQUARE:
        fields["square_grasp_xyz_offset"] = _scaled_tuple(
            fields["square_grasp_xyz_offset"], 0.65,
        )
        fields["square_grasp_yaw_bias"] *= 0.65
        fields["gripper_close_timing_offset"] = int(
            np.rint(fields["gripper_close_timing_offset"] * 0.35)
        )
        fields["transport_waypoint_offset"] = _scaled_tuple(
            fields["transport_waypoint_offset"], 0.50,
        )
        fields["peg_target_xyz_offset"] = _scaled_tuple(fields["peg_target_xyz_offset"], 0.20)
        fields["peg_alignment_yaw_bias"] *= 0.10
        fields["peg_approach_height_offset"] *= 0.15
        fields["insert_depth_offset"] *= 0.10
        fields["release_timing_offset"] = int(
            np.rint(fields["release_timing_offset"] * 0.05)
        )
    elif task == PerturbationTask.TOOL_HANG:
        # The first ToolHang candidate is arm-only by design.
        return


def _add_vector_fault(fields: dict[str, Any], key: str, axis: int, amount: float) -> None:
    values = list(fields[key])
    values[axis] += amount
    fields[key] = tuple(values)


def _inject_controlled_fault(
    rng: np.random.Generator,
    profile: ResolvedProfile,
    fields: dict[str, Any],
) -> tuple[str, str, float]:
    """Inject at most one named, reproducible task-level fault per episode."""
    if profile.task == PerturbationTask.TOOL_HANG:
        return "none", "", 0.0
    probability = _FAULT_PROBABILITY.get(profile.quality, 0.0)
    if rng.random() >= probability:
        return "none", "", 0.0

    sign = -1.0 if rng.random() < 0.5 else 1.0
    if profile.task == PerturbationTask.LIFT:
        fault = str(rng.choice(("grasp_offset", "insufficient_lift", "lateral_drift")))
        if fault == "grasp_offset":
            magnitude = float(rng.uniform(0.006, 0.012))
            _add_vector_fault(fields, "grasp_xyz_offset", int(rng.integers(0, 2)), sign * magnitude)
            return fault, "align_cube", magnitude
        if fault == "insufficient_lift":
            magnitude = float(rng.uniform(0.025, 0.050))
            fields["lift_height_delta"] -= magnitude
            return fault, "lift", magnitude
        magnitude = float(rng.uniform(0.012, 0.025))
        values = list(fields["lift_lateral_offset"])
        values[int(rng.integers(0, 2))] += sign * magnitude
        fields["lift_lateral_offset"] = tuple(values)
        return fault, "lift", magnitude

    if profile.task == PerturbationTask.CAN:
        fault = str(rng.choice(("grasp_offset", "target_offset", "premature_release")))
        if fault == "grasp_offset":
            magnitude = float(rng.uniform(0.007, 0.014))
            _add_vector_fault(fields, "can_grasp_xyz_offset", int(rng.integers(0, 2)), sign * magnitude)
            return fault, "align_can", magnitude
        if fault == "target_offset":
            magnitude = float(rng.uniform(0.004, 0.008))
            _add_vector_fault(fields, "bin_target_xyz_offset", int(rng.integers(0, 2)), sign * magnitude)
            return fault, "descend_into_bin", magnitude
        magnitude = float(rng.integers(1, 4))
        fields["release_timing_offset"] = int(fields["release_timing_offset"]) - int(magnitude)
        return fault, "release", magnitude

    if profile.task != PerturbationTask.SQUARE:
        raise ValueError(f"No controlled-fault policy for task {profile.task.value!r}")
    fault = str(rng.choice(("grasp_offset", "target_offset", "yaw_error", "shallow_insert")))
    if fault == "grasp_offset":
        magnitude = float(rng.uniform(0.005, 0.010))
        _add_vector_fault(fields, "square_grasp_xyz_offset", int(rng.integers(0, 2)), sign * magnitude)
        return fault, "align_nut", magnitude
    if fault == "target_offset":
        magnitude = float(rng.uniform(0.003, 0.007))
        _add_vector_fault(fields, "peg_target_xyz_offset", int(rng.integers(0, 2)), sign * magnitude)
        return fault, "approach_peg", magnitude
    if fault == "yaw_error":
        magnitude = float(rng.uniform(0.04, 0.10))
        fields["peg_alignment_yaw_bias"] += sign * magnitude
        return fault, "descend_on_peg", magnitude
    magnitude = float(rng.uniform(0.004, 0.010))
    fields["insert_depth_offset"] += magnitude
    return fault, "descend_on_peg", magnitude


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

    def policy_observation(
        self, observation: dict[str, Any], *, phase: str | None = None,
    ) -> dict[str, Any]:
        """Return a biased operator-only view without mutating or extending core obs."""

        variation = self.variation
        if variation.is_zero:
            return observation
        policy = phase_noise_policy(self.profile.task, phase)
        if policy.perception_scale == 0.0:
            return observation
        result = dict(observation)
        position_bias = np.asarray(variation.landmark_position_bias) * policy.perception_scale
        for key in self.position_landmarks:
            if key not in result:
                continue
            value = np.asarray(result[key])
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError(f"Position landmark {key!r} must be finite with shape (3,)")
            result[key] = value.astype(np.float64, copy=True) + position_bias

        orientation_bias = (
            np.asarray(variation.landmark_orientation_bias) * policy.perception_scale
        )
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
        policy = phase_noise_policy(self.profile.task, phase)
        active: list[str] = []
        if variation.is_zero:
            executed = validate_and_clip_action(planned, (self._low, self._high))
        else:
            gain = 1.0 + (np.asarray(variation.arm_gain) - 1.0) * policy.action_scale
            arm = planned[:6].astype(np.float64) * gain
            arm += np.asarray(variation.arm_bias) * policy.action_scale
            active.extend(("arm_gain", "arm_bias"))
            if policy.allow_delay and timestep in variation.schedule.action_delay_steps:
                arm = (
                    np.zeros(6, dtype=np.float64)
                    if self._previous_executed_arm is None
                    else self._previous_executed_arm.copy()
                )
                active.append("action_delay")
            if policy.allow_pause and timestep in variation.schedule.pause_steps:
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
            phase_scale=policy.action_scale,
            fault_type=variation.fault_type,
        )
        return executed
