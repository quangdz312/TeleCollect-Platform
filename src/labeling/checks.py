"""The E layer: hard checks that return a fact, not an estimate.

Each check reads privileged simulator state and returns 0 or 1. They are
combined by multiplication, so one failure zeroes the score and no penalty can
pull it back up.

A check may also return ``None``, meaning *not evaluable on this data*. Those
are reported in the flags and excluded from the product rather than silently
counted as a pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np

from .features import EpisodeArrays

#: How close the object origin must be to the end effector to count as held.
#:
#: This is geometry, not a tuning knob: the observation reports the object's
#: body origin, which is not where the gripper grips. Square's nut is held by a
#: handle offset from its origin, so a genuine Square grasp sits around 0.06 m
#: away while a Lift grasp sits at 0.02 m. Measured over carried frames in the
#: reference collection: lift 0.007-0.040, can 0.030-0.037, square 0.054-0.087.
GRASP_RADIUS_M: Mapping[str, float] = MappingProxyType({
    "lift": 0.06,
    "can": 0.06,
    "square": 0.12,
})
DEFAULT_GRASP_RADIUS_M = 0.06


@dataclass(frozen=True)
class CheckConfig:
    #: Fallback grasp radius for tasks not in :data:`GRASP_RADIUS_M` (m).
    grasp_radius_m: float = DEFAULT_GRASP_RADIUS_M
    #: How far the object must rise above its resting height to count as lifted (m).
    lift_threshold_m: float = 0.02
    #: How far off the table the object must be for a frame to count as carried (m).
    #: Deliberately smaller than the lift threshold: the carry starts the moment
    #: the object leaves the surface, well before it clears the success margin.
    carry_clearance_m: float = 0.005
    #: Per-frame drift of the hand-to-object offset still counted as "moving together" (m).
    together_tolerance_m: float = 0.006
    #: Consecutive frames of holding+lifted+together required to call it a pick-up.
    pickup_frames: int = 5
    #: Horizontal distance the object must travel on the transport tasks (m).
    transport_distance_m: float = 0.05
    #: Object speed under which the scene counts as settled (m/s).
    stable_speed_mps: float = 0.02
    #: Frames of stillness required at the end of an episode.
    stable_frames: int = 5
    #: How far the object may fall after a release before it counts as dropped (m).
    #: Placing a nut onto a peg costs about 0.10 m of fall on clean Square
    #: episodes, so the band sits above that and below a genuine carry-height
    #: drop.
    drop_fall_m: float = 0.15


DEFAULT_CHECKS = CheckConfig()


@dataclass(frozen=True)
class CheckResult:
    name: str
    value: int | None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.value == 1

    @property
    def evaluable(self) -> bool:
        return self.value is not None


def _resting_height(episode: EpisodeArrays, frames: int = 3) -> float:
    trajectory = episode.object_trajectory
    window = trajectory[: max(1, min(frames, trajectory.shape[0])), 2]
    return float(np.median(window))


def grasp_radius(episode: EpisodeArrays, config: CheckConfig) -> float:
    return GRASP_RADIUS_M.get(episode.task, config.grasp_radius_m)


def _holding(episode: EpisodeArrays, config: CheckConfig) -> np.ndarray:
    """Gripper commanded shut with the object inside grasp range."""

    distance = np.linalg.norm(episode.gripper_to_object, axis=1)
    return (episode.gripper_command_trajectory > 0.0) & (
        distance <= grasp_radius(episode, config)
    )


def _moving_together(episode: EpisodeArrays, config: CheckConfig) -> np.ndarray:
    """The hand-to-object offset holds steady, which is what contact looks like."""

    offsets = episode.gripper_to_object
    steps = offsets.shape[0]
    together = np.zeros(steps, dtype=bool)
    if steps > 1:
        drift = np.linalg.norm(np.diff(offsets, axis=0), axis=1)
        together[1:] = drift <= config.together_tolerance_m
    return together


def _longest_run(mask: np.ndarray) -> int:
    best = current = 0
    for value in mask:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive [start, end] index pairs for each True run."""

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def e_integrity(episode: EpisodeArrays) -> CheckResult:
    """Frame counts line up, actions are well formed, nothing is NaN."""

    problems: list[str] = []
    length = episode.length
    if length == 0:
        problems.append("empty episode")
    if episode.actions.ndim != 2 or episode.actions.shape[1:] != (7,):
        problems.append(f"action shape {episode.actions.shape}")
    for name, array in (
        ("actions", episode.actions),
        ("eef_position", episode.eef_position),
        ("object_position", episode.object_position),
        ("object_orientation", episode.object_orientation),
        ("gripper_qpos", episode.gripper_qpos),
        ("rewards", episode.rewards),
    ):
        if array.shape[0] != length:
            problems.append(f"{name} has {array.shape[0]} frames, expected {length}")
        if array.size and not np.isfinite(array).all():
            problems.append(f"{name} contains non-finite values")
    if episode.actions.size:
        below = np.any(episode.actions < episode.action_low - 1e-9)
        above = np.any(episode.actions > episode.action_high + 1e-9)
        if below or above:
            problems.append("actions out of bounds")
    if episode.num_samples_attr not in (-1, length):
        problems.append(
            f"num_samples attribute {episode.num_samples_attr} does not match {length}",
        )
    return CheckResult("E_integrity", 0 if problems else 1, {"problems": problems})


def e_success(episode: EpisodeArrays) -> CheckResult:
    """The task's own success condition fired.

    The required hold duration cannot be confirmed on current data: the executor
    breaks out of the rollout on the first success frame, so no frames after it
    were recorded. ``hold_verified`` says so rather than pretending otherwise.
    """

    return CheckResult(
        "E_success",
        1 if episode.recorded_success else 0,
        {
            "termination_reason": episode.termination_reason,
            "hold_verified": False,
            "hold_unverifiable_reason": "episode ends at the first success frame",
        },
    )


def e_skill(episode: EpisodeArrays, config: CheckConfig = DEFAULT_CHECKS) -> CheckResult:
    """The demo used the intended skill, not a lucky shortcut.

    This is the check that matters most here. Lift's success condition is only
    ``cube_height > table + 0.04`` with nothing about grasping, so an episode
    that knocks or scoops the cube upward registers as a success and looks fine
    in the video. Injecting noise deliberately makes that more likely, not less.

    A pick-up is confirmed when, for a run of consecutive frames, the gripper is
    commanded shut with the object in range, the object is above its resting
    height, and the hand-to-object offset stays fixed — an object that follows
    the hand while airborne is being carried.
    """

    if episode.length == 0:
        return CheckResult("E_skill", 0, {"reason": "empty episode"})

    trajectory = episode.object_trajectory
    resting = _resting_height(episode)
    rise = trajectory[:, 2] - resting

    # The three conjuncts are kept separate, as specified.
    #
    # "Moved together with the hand for k consecutive frames" is the contact
    # evidence: a run where the gripper is shut on the object and the
    # hand-to-object offset stays fixed. "Rose above the table" is a condition
    # on the episode, not on every frame of that run — the rollout stops on the
    # frame the lift margin is first cleared, so demanding k frames of clearance
    # would be unsatisfiable on Lift by construction.
    #
    # The run must still contain at least one frame where the object is off the
    # surface. That is what separates carrying from a shut gripper resting next
    # to a motionless object, which would otherwise show a constant offset too.
    contact = _holding(episode, config) & _moving_together(episode, config)
    off_surface = rise > config.carry_clearance_m
    carrying_runs = [
        (start, end)
        for start, end in _runs(contact)
        if end - start + 1 >= config.pickup_frames and np.any(off_surface[start : end + 1])
    ]
    run = max((end - start + 1 for start, end in carrying_runs), default=0)
    longest_contact = _longest_run(contact)
    max_rise = float(np.max(rise))
    rose = max_rise > config.lift_threshold_m
    picked_up = bool(carrying_runs) and rose

    if episode.task == "tool_hang":
        # ToolHang Stage 1 manipulates a long hook frame. Its body origin is
        # intentionally far from the gripper, so the generic grasp-radius
        # test is not meaningful. Use the task's own observable evidence:
        # the frame leaves the table and is transported to the stand.
        horizontal_travel = float(
            np.max(np.linalg.norm(trajectory[:, :2] - trajectory[0, :2], axis=1)),
        )
        toolhang_lifted = max_rise > 0.05
        toolhang_moved = horizontal_travel > 0.05
        detail = {
            "was_picked_up": bool(toolhang_lifted and toolhang_moved),
            "longest_carry_frames": int(0),
            "longest_contact_frames": int(0),
            "required_carry_frames": 0,
            "rose_above_table": toolhang_lifted,
            "max_object_rise_m": max_rise,
            "horizontal_travel_m": horizontal_travel,
            "moved_to_target": toolhang_moved,
            "task_specific": "tool_hang_stage1_frame_transport",
        }
        return CheckResult(
            "E_skill", 1 if toolhang_lifted and toolhang_moved else 0, detail,
        )

    detail = {
        "was_picked_up": bool(picked_up),
        "longest_carry_frames": int(run),
        "longest_contact_frames": int(longest_contact),
        "required_carry_frames": config.pickup_frames,
        "rose_above_table": bool(rose),
        "max_object_rise_m": max_rise,
        "resting_height_m": resting,
    }
    if episode.task == "lift":
        # Lift is complete at the carry: nothing has to be put down.
        return CheckResult("E_skill", 1 if picked_up else 0, detail)

    # Can and Square both require placing the object somewhere, so the object
    # must also have travelled horizontally away from where it started.
    travel = float(
        np.max(np.linalg.norm(trajectory[:, :2] - trajectory[0, :2], axis=1)),
    )
    detail["horizontal_travel_m"] = travel
    moved = travel > config.transport_distance_m
    detail["moved_to_target"] = bool(moved)
    return CheckResult("E_skill", 1 if (picked_up and moved) else 0, detail)


def e_no_drop(episode: EpisodeArrays, config: CheckConfig = DEFAULT_CHECKS) -> CheckResult:
    """The object was never let go mid-air in a way that made it fall.

    A release is not automatically a drop. Can and Square both *have* to release
    the object over a target, and it settles a short distance below the hand.
    What separates the two is how far the object falls afterwards, so that is
    what is measured — no target geometry required.
    """

    if episode.length == 0:
        return CheckResult("E_no_drop", 0, {"reason": "empty episode"})

    trajectory = episode.object_trajectory
    heights = trajectory[:, 2]
    resting = _resting_height(episode)
    airborne = heights - resting > config.lift_threshold_m
    holding = _holding(episode, config)

    falls: list[float] = []
    for index in range(1, trajectory.shape[0]):
        released = holding[index - 1] and not holding[index]
        if not (released and airborne[index]):
            continue
        falls.append(float(heights[index] - np.min(heights[index:])))

    worst_fall = max(falls) if falls else 0.0
    dropped = worst_fall > config.drop_fall_m
    return CheckResult(
        "E_no_drop",
        0 if dropped else 1,
        {
            "airborne_frames": int(np.count_nonzero(airborne)),
            "release_events": len(falls),
            "worst_fall_after_release_m": worst_fall,
            "drop_fall_threshold_m": config.drop_fall_m,
        },
    )


def hard_checks(
    episode: EpisodeArrays,
    config: CheckConfig = DEFAULT_CHECKS,
) -> list[CheckResult]:
    return [
        e_integrity(episode),
        e_success(episode),
        e_skill(episode, config),
        e_no_drop(episode, config),
    ]
