"""Immutable episode-level variation records used by the shared runtime."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import TypeAlias


Vector3: TypeAlias = tuple[float, float, float]
ArmVector: TypeAlias = tuple[float, float, float, float, float, float]
Vector2: TypeAlias = tuple[float, float]


@dataclass(frozen=True)
class EventSchedule:
    """Absolute zero-based timestep events sampled once per episode."""

    action_delay_steps: tuple[int, ...] = ()
    pause_steps: tuple[int, ...] = ()
    gripper_close_steps: tuple[int, ...] = ()
    gripper_open_steps: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        groups = (
            self.action_delay_steps,
            self.pause_steps,
            self.gripper_close_steps,
            self.gripper_open_steps,
        )
        if any(
            any(
                isinstance(step, bool) or not isinstance(step, Integral) or step < 0
                for step in group
            )
            for group in groups
        ):
            raise ValueError("Scheduled event steps must be non-negative integers")
        if any(tuple(sorted(set(group))) != group for group in groups):
            raise ValueError("Scheduled event steps must be sorted and unique")
        if set(self.gripper_close_steps) & set(self.gripper_open_steps):
            raise ValueError("Gripper close and open events cannot share a timestep")

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.action_delay_steps,
                self.pause_steps,
                self.gripper_close_steps,
                self.gripper_open_steps,
            ),
        )


@dataclass(frozen=True)
class EpisodeVariation:
    quality: str
    noise_scale: float
    landmark_position_bias: Vector3
    landmark_orientation_bias: Vector3
    arm_gain: ArmVector
    arm_bias: ArmVector
    schedule: EventSchedule
    retry_cap: int

    @property
    def is_zero(self) -> bool:
        return (
            self.noise_scale == 0.0
            and self.landmark_position_bias == (0.0, 0.0, 0.0)
            and self.landmark_orientation_bias == (0.0, 0.0, 0.0)
            and self.arm_gain == (1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
            and self.arm_bias == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            and self.schedule.empty
            and self.retry_cap == 0
        )


@dataclass(frozen=True)
class LiftVariation(EpisodeVariation):
    grasp_xyz_offset: Vector3 = (0.0, 0.0, 0.0)
    grasp_yaw_bias: float = 0.0
    gripper_close_timing_offset: int = 0
    lift_height_delta: float = 0.0
    lift_lateral_offset: Vector2 = (0.0, 0.0)
    regrasp_enabled: bool = False

    @property
    def is_zero(self) -> bool:
        return (
            super().is_zero
            and self.grasp_xyz_offset == (0.0, 0.0, 0.0)
            and self.grasp_yaw_bias == 0.0
            and self.gripper_close_timing_offset == 0
            and self.lift_height_delta == 0.0
            and self.lift_lateral_offset == (0.0, 0.0)
            and not self.regrasp_enabled
        )


@dataclass(frozen=True)
class CanVariation(EpisodeVariation):
    can_grasp_xyz_offset: Vector3 = (0.0, 0.0, 0.0)
    can_grasp_yaw_bias: float = 0.0
    gripper_close_timing_offset: int = 0
    transport_waypoint_offset: Vector3 = (0.0, 0.0, 0.0)
    bin_target_xyz_offset: Vector3 = (0.0, 0.0, 0.0)
    release_height_offset: float = 0.0
    release_timing_offset: int = 0
    retry_enabled: bool = False

    @property
    def is_zero(self) -> bool:
        return (
            super().is_zero
            and self.can_grasp_xyz_offset == (0.0, 0.0, 0.0)
            and self.can_grasp_yaw_bias == 0.0
            and self.gripper_close_timing_offset == 0
            and self.transport_waypoint_offset == (0.0, 0.0, 0.0)
            and self.bin_target_xyz_offset == (0.0, 0.0, 0.0)
            and self.release_height_offset == 0.0
            and self.release_timing_offset == 0
            and not self.retry_enabled
        )


@dataclass(frozen=True)
class SquareVariation(EpisodeVariation):
    square_grasp_xyz_offset: Vector3 = (0.0, 0.0, 0.0)
    square_grasp_yaw_bias: float = 0.0
    gripper_close_timing_offset: int = 0
    transport_waypoint_offset: Vector3 = (0.0, 0.0, 0.0)
    peg_target_xyz_offset: Vector3 = (0.0, 0.0, 0.0)
    peg_alignment_yaw_bias: float = 0.0
    peg_approach_height_offset: float = 0.0
    insert_depth_offset: float = 0.0
    release_timing_offset: int = 0
    retry_enabled: bool = False

    @property
    def is_zero(self) -> bool:
        return (
            super().is_zero
            and self.square_grasp_xyz_offset == (0.0, 0.0, 0.0)
            and self.square_grasp_yaw_bias == 0.0
            and self.gripper_close_timing_offset == 0
            and self.transport_waypoint_offset == (0.0, 0.0, 0.0)
            and self.peg_target_xyz_offset == (0.0, 0.0, 0.0)
            and self.peg_alignment_yaw_bias == 0.0
            and self.peg_approach_height_offset == 0.0
            and self.insert_depth_offset == 0.0
            and self.release_timing_offset == 0
            and not self.retry_enabled
        )


TaskVariation: TypeAlias = LiftVariation | CanVariation | SquareVariation

VariationType: TypeAlias = type[EpisodeVariation]
_VARIATION_TYPES: dict[str, VariationType] = {}


def register_variation_type(task: str, variation_type: VariationType) -> None:
    """Register a typed episode record without changing runtime core."""

    task_name = str(task).lower()
    if not task_name:
        raise ValueError("Variation task name must not be empty")
    if not issubclass(variation_type, EpisodeVariation):
        raise TypeError("Variation type must inherit EpisodeVariation")
    if task_name in _VARIATION_TYPES:
        raise ValueError(f"Variation type already registered for task {task_name!r}")
    _VARIATION_TYPES[task_name] = variation_type


def variation_type_for_task(task: str) -> VariationType:
    try:
        return _VARIATION_TYPES[str(task).lower()]
    except KeyError as exc:
        raise ValueError(f"No variation type registered for task {task!r}") from exc


register_variation_type("lift", LiftVariation)
register_variation_type("can", CanVariation)
register_variation_type("square", SquareVariation)
