"""Published perturbation profiles for TeleCollect v1.0.

The doses below were frozen before Phase D2 held-out validation and are carried
into profile version 1 unchanged. Amendment TC-QP-2026-08-09-02 accepted them
against the held-out bank; publication changed the version marker only. Retuning
a dose is a new calibration round, not an edit to this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


#: The candidate marker these doses carried through Phase D2. Kept so dataset
#: provenance can trace a published episode back to the frozen candidate run.
CANDIDATE_PROFILE_VERSION = "candidate-phase-d2-frozen"
#: Accepted under amendment TC-QP-2026-08-09-02; see docs/telecollect_profile_v1_decision.md.
PROFILE_VERSION = "v1"
PROFILE_ACCEPTANCE_AMENDMENT = "TC-QP-2026-08-09-02"
TOOLHANG_PROFILE_VERSION = "toolhang-candidate-v1"
TOOLHANG_CANDIDATE_PROFILE_VERSION = "toolhang-phase-decay-calibration"
TOOLHANG_ACCEPTANCE_AMENDMENT = "pending-calibration"


class PerturbationTask(str, Enum):
    LIFT = "lift"
    CAN = "can"
    SQUARE = "square"
    TOOL_HANG = "tool_hang"


class Quality(str, Enum):
    CLEAN = "clean"
    GOOD = "good"
    MEDIUM = "medium"
    POOR = "poor"


TASK_CODES = MappingProxyType({
    PerturbationTask.LIFT: 1,
    PerturbationTask.CAN: 2,
    PerturbationTask.SQUARE: 3,
    PerturbationTask.TOOL_HANG: 4,
})
NOISE_STREAM_CODE = 1

QUALITY_SCALES = MappingProxyType({
    Quality.CLEAN: 0.00,
    Quality.GOOD: 0.25,
    Quality.MEDIUM: 0.55,
    Quality.POOR: 0.85,
})

# Phase D2 frozen candidate doses, published unchanged as profile version 1
# after the untouched held-out bank was assessed under TC-QP-2026-08-09-02.
TASK_QUALITY_SCALES = MappingProxyType({
    PerturbationTask.LIFT: MappingProxyType({
        Quality.CLEAN: 0.00,
        Quality.GOOD: 0.25,
        Quality.MEDIUM: 1.70,
        Quality.POOR: 2.50,
    }),
    PerturbationTask.CAN: MappingProxyType({
        Quality.CLEAN: 0.00,
        Quality.GOOD: 0.25,
        Quality.MEDIUM: 1.10,
        Quality.POOR: 1.20,
    }),
    PerturbationTask.SQUARE: MappingProxyType({
        Quality.CLEAN: 0.00,
        Quality.GOOD: 0.18,
        Quality.MEDIUM: 0.90,
        Quality.POOR: 1.30,
    }),
    PerturbationTask.TOOL_HANG: MappingProxyType({
        Quality.CLEAN: 0.00,
        Quality.GOOD: 0.20,
        Quality.MEDIUM: 0.60,
        Quality.POOR: 1.00,
    }),
})


@dataclass(frozen=True)
class LiftSemanticLimits:
    grasp_xyz_offset_m: tuple[float, float, float]
    grasp_yaw_bias_rad: float
    gripper_close_timing_steps: int
    lift_height_delta_m: float
    lift_lateral_offset_m: float
    regrasp_cap: int = 1


@dataclass(frozen=True)
class CanSemanticLimits:
    grasp_xyz_offset_m: tuple[float, float, float]
    grasp_yaw_bias_rad: float
    gripper_close_timing_steps: int
    transport_waypoint_offset_m: tuple[float, float, float]
    bin_target_xyz_offset_m: tuple[float, float, float]
    release_height_offset_m: float
    release_timing_steps: int
    retry_cap: int = 1


@dataclass(frozen=True)
class SquareSemanticLimits:
    grasp_xyz_offset_m: tuple[float, float, float]
    grasp_yaw_bias_rad: float
    gripper_close_timing_steps: int
    transport_waypoint_offset_m: tuple[float, float, float]
    peg_target_xyz_offset_m: tuple[float, float, float]
    peg_alignment_yaw_bias_rad: float
    peg_approach_height_offset_m: float
    insert_depth_offset_m: float
    release_timing_steps: int
    retry_cap: int = 1


@dataclass(frozen=True)
class CandidateLimits:
    """Conservative construction limits to exercise the Phase B runtime."""

    position_bias_m: float
    orientation_bias_rad: float
    arm_gain_delta: float
    arm_bias: float
    event_window_steps: int = 64
    lift: LiftSemanticLimits | None = None
    can: CanSemanticLimits | None = None
    square: SquareSemanticLimits | None = None


_TASK_LIMITS = {
    PerturbationTask.LIFT: CandidateLimits(
        0.008,
        0.08,
        0.05,
        0.008,
        lift=LiftSemanticLimits(
            grasp_xyz_offset_m=(0.010, 0.010, 0.005),
            grasp_yaw_bias_rad=0.12,
            gripper_close_timing_steps=3,
            lift_height_delta_m=0.035,
            lift_lateral_offset_m=0.020,
        ),
    ),
    PerturbationTask.CAN: CandidateLimits(
        0.012,
        0.10,
        0.07,
        0.010,
        can=CanSemanticLimits(
            grasp_xyz_offset_m=(0.012, 0.012, 0.006),
            grasp_yaw_bias_rad=0.15,
            gripper_close_timing_steps=3,
            transport_waypoint_offset_m=(0.030, 0.030, 0.020),
            bin_target_xyz_offset_m=(0.025, 0.025, 0.010),
            release_height_offset_m=0.025,
            release_timing_steps=3,
        ),
    ),
    PerturbationTask.SQUARE: CandidateLimits(
        0.006,
        0.06,
        0.04,
        0.006,
        square=SquareSemanticLimits(
            grasp_xyz_offset_m=(0.010, 0.010, 0.005),
            grasp_yaw_bias_rad=0.12,
            gripper_close_timing_steps=3,
            transport_waypoint_offset_m=(0.020, 0.020, 0.015),
            peg_target_xyz_offset_m=(0.008, 0.008, 0.006),
            peg_alignment_yaw_bias_rad=0.10,
            peg_approach_height_offset_m=0.015,
            insert_depth_offset_m=0.008,
            release_timing_steps=3,
        ),
    ),
    # Candidate arm-only dose. ToolHang deliberately starts without perception
    # bias or semantic faults; those require their own calibration round.
    PerturbationTask.TOOL_HANG: CandidateLimits(
        0.0,
        0.0,
        0.04,
        0.008,
        event_window_steps=64,
    ),
}

_ALIASES = {
    "lift": PerturbationTask.LIFT,
    "lift_cube": PerturbationTask.LIFT,
    "can": PerturbationTask.CAN,
    "pick_place_can": PerturbationTask.CAN,
    "square": PerturbationTask.SQUARE,
    "assemble_square": PerturbationTask.SQUARE,
    "tool_hang": PerturbationTask.TOOL_HANG,
    "assemble_tool_hang": PerturbationTask.TOOL_HANG,
}

_TOOL_NAMES = {
    PerturbationTask.LIFT: "lift_cube",
    PerturbationTask.CAN: "pick_place_can",
    PerturbationTask.SQUARE: "assemble_square",
    PerturbationTask.TOOL_HANG: "tool_hang_stage1",
}


class PerturbationNotEnabledError(ValueError):
    """Raised for known tasks intentionally excluded from this release."""


@dataclass(frozen=True)
class ResolvedProfile:
    task: PerturbationTask
    tool_name: str
    task_code: int
    quality: Quality
    noise_scale: float
    limits: CandidateLimits
    profile_version: str = PROFILE_VERSION
    candidate_profile_version: str = CANDIDATE_PROFILE_VERSION
    acceptance_amendment: str = PROFILE_ACCEPTANCE_AMENDMENT


def resolve_profile(
    task: str | PerturbationTask,
    quality: str | Quality,
) -> ResolvedProfile:
    """Resolve a v1.0 candidate without importing a simulator backend."""

    task_value = task.value if isinstance(task, PerturbationTask) else str(task).lower()
    try:
        resolved_task = _ALIASES[task_value]
    except KeyError as exc:
        raise ValueError(f"Unsupported perturbation task: {task_value!r}") from exc

    try:
        resolved_quality = quality if isinstance(quality, Quality) else Quality(str(quality).lower())
    except ValueError as exc:
        supported = ", ".join(item.value for item in Quality)
        raise ValueError(
            f"Unsupported perturbation quality: {quality!r}; expected one of {supported}",
        ) from exc

    tool_hang = resolved_task == PerturbationTask.TOOL_HANG
    return ResolvedProfile(
        task=resolved_task,
        tool_name=_TOOL_NAMES[resolved_task],
        task_code=TASK_CODES[resolved_task],
        quality=resolved_quality,
        noise_scale=TASK_QUALITY_SCALES[resolved_task][resolved_quality],
        limits=_TASK_LIMITS[resolved_task],
        profile_version=TOOLHANG_PROFILE_VERSION if tool_hang else PROFILE_VERSION,
        candidate_profile_version=(
            TOOLHANG_CANDIDATE_PROFILE_VERSION if tool_hang else CANDIDATE_PROFILE_VERSION
        ),
        acceptance_amendment=(
            TOOLHANG_ACCEPTANCE_AMENDMENT if tool_hang else PROFILE_ACCEPTANCE_AMENDMENT
        ),
    )
