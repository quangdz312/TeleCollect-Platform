"""Auto-trim: drop the idle frames at the head and tail of an episode.

This is the DROID idle filter. It runs independently of the score and is always
only a suggestion, so the risk of getting it slightly wrong is near zero: a
reviewer can move the handles.

DROID's published rule is "keep runs of non-idle actions at least 16 steps
(1 second of wallclock) long that are not interrupted by 8 or more idle
actions". The step counts are expressed here in seconds and converted with the
episode's control rate, because 16 steps is one second only at DROID's rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .features import EpisodeArrays


@dataclass(frozen=True)
class TrimConfig:
    #: Idle speed cut, as a fraction of the episode's own p99 end-effector speed.
    idle_speed_fraction: float = 0.02
    #: Any change in the gripper command counts as motion.
    idle_gripper_delta: float = 1e-6
    min_run_seconds: float = 1.0
    max_gap_seconds: float = 0.5
    #: Frames of success that must survive trimming once the hold is recorded.
    success_hold_steps: int = 0


DEFAULT_TRIM = TrimConfig()


@dataclass(frozen=True)
class TrimSuggestion:
    start: int
    end: int
    idle_ratio_before: float
    idle_ratio_after: float
    kept_runs: int
    trimmed_head: int
    trimmed_tail: int

    @property
    def length(self) -> int:
        return max(0, self.end - self.start + 1)


def idle_mask(episode: EpisodeArrays, config: TrimConfig = DEFAULT_TRIM) -> np.ndarray:
    """Per-frame idle flag from end-effector speed and gripper command change."""

    positions = episode.eef_position
    steps = positions.shape[0]
    if steps == 0:
        return np.zeros(0, dtype=bool)
    deltas = np.diff(positions, axis=0)
    speed = np.zeros(steps, dtype=np.float64)
    if steps > 1:
        speed[1:] = np.linalg.norm(deltas, axis=1) / episode.dt
        speed[0] = speed[1]
    reference = float(np.percentile(speed, 99)) if steps > 1 else 0.0
    speed_cut = config.idle_speed_fraction * reference

    gripper = episode.gripper_command
    gripper_delta = np.zeros(steps, dtype=np.float64)
    if steps > 1:
        gripper_delta[1:] = np.abs(np.diff(gripper))
    return (speed <= speed_cut) & (gripper_delta <= config.idle_gripper_delta)


def _runs(active: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive [start, end] index pairs for each True run."""

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(active):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(active) - 1))
    return runs


def _merge(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """Bridge runs split by a short pause, per the DROID gap clause."""

    if not runs:
        return []
    merged = [runs[0]]
    for start, end in runs[1:]:
        previous_start, previous_end = merged[-1]
        if start - previous_end - 1 < max_gap:
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return merged


def first_success_frame(episode: EpisodeArrays) -> int | None:
    """The frame the success condition first held.

    The executor breaks out of the rollout on the first success, so a successful
    episode's success frame is its last frame. When that changes, this is the
    single place that needs updating.
    """

    return episode.length - 1 if episode.recorded_success else None


def suggest_trim(
    episode: EpisodeArrays,
    config: TrimConfig = DEFAULT_TRIM,
) -> TrimSuggestion:
    steps = episode.length
    if steps == 0:
        return TrimSuggestion(0, -1, 0.0, 0.0, 0, 0, 0)

    idle = idle_mask(episode, config)
    idle_before = float(np.mean(idle))
    min_run = max(1, int(round(config.min_run_seconds * episode.control_hz)))
    max_gap = max(1, int(round(config.max_gap_seconds * episode.control_hz)))

    kept = [run for run in _merge(_runs(~idle), max_gap) if run[1] - run[0] + 1 >= min_run]
    if kept:
        start, end = kept[0][0], kept[-1][1]
    else:
        # Nothing clears the minimum run length; keep the episode whole rather
        # than proposing a cut that deletes everything.
        start, end = 0, steps - 1

    success_frame = first_success_frame(episode)
    if success_frame is not None:
        end = max(end, min(steps - 1, success_frame + config.success_hold_steps))
        start = min(start, end)

    remaining = idle[start : end + 1]
    return TrimSuggestion(
        start=int(start),
        end=int(end),
        idle_ratio_before=idle_before,
        idle_ratio_after=float(np.mean(remaining)) if remaining.size else 0.0,
        kept_runs=len(kept),
        trimmed_head=int(start),
        trimmed_tail=int(steps - 1 - end),
    )
