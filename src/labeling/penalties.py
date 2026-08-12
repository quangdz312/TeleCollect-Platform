"""The penalty layer: soft warnings in [0, 1] that may only lower a score.

Every penalty uses the same mapping, so adding one is cheap:

```text
penalty = clip((x - x_lo) / (x_hi - x_lo), 0, 1)
```

``x_lo`` is "not worth worrying about yet", ``x_hi`` is "definitely a problem".
The starting values come from ``auto-labeling.md`` and are meant to be adjusted
once the real distribution is visible.

Penalties never approve anything. Because the score multiplies the hard checks
and then subtracts only the worst penalty, a tuned threshold can at most push an
episode down into the human review queue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .features import EpisodeArrays
from .trim import DEFAULT_TRIM, TrimConfig, TrimSuggestion, idle_mask, suggest_trim

#: Gripper open/close events a correct demonstration needs, per task.
EXPECTED_GRIPPER_TOGGLES: Mapping[str, int] = {
    "lift": 1,   # close on the cube and hold
    "can": 2,    # close on the can, open over the bin
    "square": 2,  # close on the nut, open over the peg
}


@dataclass(frozen=True)
class PenaltyBand:
    low: float
    high: float

    def __call__(self, value: float) -> float:
        if self.high == self.low:
            return 0.0
        return float(np.clip((value - self.low) / (self.high - self.low), 0.0, 1.0))


@dataclass(frozen=True)
class PenaltyConfig:
    jerk_percentile: PenaltyBand = PenaltyBand(75.0, 99.0)
    gripper_toggles: PenaltyBand = PenaltyBand(0.0, 3.0)
    path_ratio: PenaltyBand = PenaltyBand(1.3, 2.5)
    length_zscore: PenaltyBand = PenaltyBand(2.5, 4.0)
    #: Widened from the document's 0.02-0.15, which describes human teleoperation.
    #: A scripted operator commands near-full-scale arm deltas by construction, so
    #: even clean episodes saturate: measured 0.03-0.16 on Lift, 0.04-0.45 on Can
    #: and 0.03-0.60 on Square. Provisional until a larger corpus exists.
    saturation: PenaltyBand = PenaltyBand(0.35, 0.75)
    idle_after_trim: PenaltyBand = PenaltyBand(0.10, 0.40)
    #: Episodes of one task needed before a percentile- or MAD-relative penalty
    #: means anything. Below it those penalties report 0 and say why.
    min_corpus_for_relative: int = 30


DEFAULT_PENALTIES = PenaltyConfig()


@dataclass(frozen=True)
class RawPenaltyFeatures:
    """Per-episode quantities; three of them only mean something against a corpus."""

    jerk_rms: float
    gripper_toggles: int
    expected_gripper_toggles: int
    path_length_m: float
    length: int
    saturation_fraction: float
    idle_ratio_after_trim: float


@dataclass(frozen=True)
class TaskStats:
    """Reference distribution for one task, built from the batch being scored."""

    task: str
    jerk_values: np.ndarray
    path_length_median: float
    length_median: float
    length_mad: float
    count: int

    def jerk_percentile(self, value: float) -> float:
        if self.jerk_values.size == 0:
            return 0.0
        return float(np.mean(self.jerk_values <= value) * 100.0)


@dataclass(frozen=True)
class PenaltyResult:
    name: str
    value: float
    raw: float
    detail: dict[str, Any] = field(default_factory=dict)


def _jerk_rms(series: np.ndarray) -> float:
    """RMS of the discrete second difference, the doc's jerk proxy."""

    if series.shape[0] < 3:
        return 0.0
    second = series[2:] - 2.0 * series[1:-1] + series[:-2]
    return float(np.sqrt(np.mean(np.sum(second**2, axis=1))))


def _gripper_toggles(command: np.ndarray) -> int:
    if command.size < 2:
        return 0
    signs = np.sign(command)
    return int(np.count_nonzero(np.diff(signs) != 0))


def raw_penalty_features(
    episode: EpisodeArrays,
    *,
    trim: TrimSuggestion | None = None,
    trim_config: TrimConfig = DEFAULT_TRIM,
) -> RawPenaltyFeatures:
    suggestion = suggest_trim(episode, trim_config) if trim is None else trim
    positions = episode.eef_position

    path_length = 0.0
    if positions.shape[0] > 1:
        path_length = float(np.sum(np.linalg.norm(np.diff(positions, axis=0), axis=1)))

    # The gripper dimension is bang-bang on this action contract, so it sits at a
    # bound every frame; counting it would report full saturation for every
    # episode. Only the six arm dimensions are meaningful here.
    arm = episode.actions[:, :6]
    if arm.size:
        at_bound = np.isclose(arm, episode.action_low[:6], atol=1e-6) | np.isclose(
            arm, episode.action_high[:6], atol=1e-6,
        )
        saturation = float(np.mean(np.any(at_bound, axis=1)))
    else:
        saturation = 0.0

    idle = idle_mask(episode, trim_config)
    kept = idle[suggestion.start : suggestion.end + 1]
    return RawPenaltyFeatures(
        jerk_rms=_jerk_rms(positions),
        gripper_toggles=_gripper_toggles(episode.gripper_command),
        expected_gripper_toggles=EXPECTED_GRIPPER_TOGGLES.get(episode.task, 2),
        path_length_m=path_length,
        length=episode.length,
        saturation_fraction=saturation,
        idle_ratio_after_trim=float(np.mean(kept)) if kept.size else 0.0,
    )


def build_task_stats(
    task: str,
    features: Sequence[RawPenaltyFeatures],
) -> TaskStats:
    """Normalise within the same task; every task has its own natural scale."""

    jerks = np.sort(np.asarray([item.jerk_rms for item in features], dtype=np.float64))
    paths = np.asarray([item.path_length_m for item in features], dtype=np.float64)
    lengths = np.asarray([item.length for item in features], dtype=np.float64)
    length_median = float(np.median(lengths)) if lengths.size else 0.0
    return TaskStats(
        task=task,
        jerk_values=jerks,
        path_length_median=float(np.median(paths)) if paths.size else 0.0,
        length_median=length_median,
        length_mad=float(np.median(np.abs(lengths - length_median))) if lengths.size else 0.0,
        count=len(features),
    )


def penalties(
    raw: RawPenaltyFeatures,
    stats: TaskStats,
    config: PenaltyConfig = DEFAULT_PENALTIES,
) -> list[PenaltyResult]:
    results: list[PenaltyResult] = []

    # A percentile or a MAD z-score is a statement about a distribution. On a
    # handful of episodes it is not one: whichever episode happens to be the
    # jerkiest lands at the 100th percentile and would be penalised out of
    # existence for being the worst of eight. Report 0 until the corpus is big
    # enough to mean something. Under-penalising is the safe direction, because
    # a penalty can only ever push an episode toward human review.
    relative_ready = stats.count >= config.min_corpus_for_relative
    insufficient = {
        "corpus_count": stats.count,
        "corpus_required": config.min_corpus_for_relative,
        "status": "insufficient_corpus",
    }

    percentile = stats.jerk_percentile(raw.jerk_rms)
    results.append(
        PenaltyResult(
            "jerkiness",
            config.jerk_percentile(percentile) if relative_ready else 0.0,
            percentile,
            {"jerk_rms": raw.jerk_rms, "corpus_count": stats.count}
            if relative_ready
            else {"jerk_rms": raw.jerk_rms, **insufficient},
        ),
    )

    excess = abs(raw.gripper_toggles - raw.expected_gripper_toggles)
    results.append(
        PenaltyResult(
            "gripper_toggles",
            config.gripper_toggles(excess),
            float(excess),
            {"observed": raw.gripper_toggles, "expected": raw.expected_gripper_toggles},
        ),
    )

    # Divide by the task median, never by straight-line distance: in pick-place
    # the arm returns near where it started and the ratio would explode.
    ratio = (
        raw.path_length_m / stats.path_length_median
        if stats.path_length_median > 0
        else 0.0
    )
    results.append(
        PenaltyResult(
            "wandering_path",
            config.path_ratio(ratio),
            ratio,
            {"path_length_m": raw.path_length_m, "task_median_m": stats.path_length_median},
        ),
    )

    zscore = (
        abs(0.6745 * (raw.length - stats.length_median) / stats.length_mad)
        if stats.length_mad > 0
        else 0.0
    )
    results.append(
        PenaltyResult(
            "unusual_length",
            config.length_zscore(zscore) if relative_ready else 0.0,
            zscore,
            {"length": raw.length, "task_median": stats.length_median, "mad": stats.length_mad}
            | ({} if relative_ready else insufficient),
        ),
    )

    results.append(
        PenaltyResult(
            "hitting_limits",
            config.saturation(raw.saturation_fraction),
            raw.saturation_fraction,
            {"note": "arm dimensions only; the gripper is bang-bang by contract"},
        ),
    )

    results.append(
        PenaltyResult(
            "idle_after_trim",
            config.idle_after_trim(raw.idle_ratio_after_trim),
            raw.idle_ratio_after_trim,
            {},
        ),
    )
    return results
