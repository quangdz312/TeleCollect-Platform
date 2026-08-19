"""Smooth a recorded trajectory instead of judging it.

Jerk, path efficiency and idle stretches say something real about a
demonstration, but no published threshold says where to cut them, and curation
audits report that action-only scores of this kind do not predict downstream
policy performance. Rejecting on them trades data away for a number nobody can
justify.

Refinement is the other option that literature actually supports: leave the
episode in the corpus and reduce the roughness. AXIS reports 63.9% less
acceleration and 80.8% less jerk this way while 86.2% of refined trajectories
still replay successfully.

Nothing here mutates a recording. It returns a new array and the measurements
that go with it, so the caller decides what to keep.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import savgol_filter

#: Savitzky-Golay window, in frames. AXIS uses 15 at 20 Hz, which is the rate
#: this project records at, so the filter spans the same 0.75 s of motion.
DEFAULT_WINDOW = 15
#: Polynomial order fitted inside each window. Cubic preserves an accelerating
#: reach; going lower flattens it into a straight line.
DEFAULT_POLYORDER = 3
#: Below this, a window cannot hold a cubic fit.
MIN_WINDOW = DEFAULT_POLYORDER + 2


@dataclass(frozen=True)
class RefinementResult:
    """A smoothed trajectory next to what smoothing cost and bought."""

    trajectory: np.ndarray
    window: int
    polyorder: int
    jerk_before: float
    jerk_after: float
    #: Largest distance any single frame moved. This is the honest cost: it is
    #: how far the refined trajectory departs from what the simulator actually
    #: executed.
    max_displacement_m: float
    applied: bool
    reason: str

    @property
    def jerk_reduction(self) -> float:
        if self.jerk_before <= 0.0:
            return 0.0
        return 1.0 - self.jerk_after / self.jerk_before


def jerk_rms(series: np.ndarray) -> float:
    """RMS of the discrete second difference, matching the penalty layer's proxy."""

    if series.shape[0] < 3:
        return 0.0
    second = series[2:] - 2.0 * series[1:-1] + series[:-2]
    return float(np.sqrt(np.mean(np.sum(second**2, axis=1))))


def _odd_window(requested: int, length: int) -> int:
    """savgol needs an odd window no longer than the series."""

    window = min(requested, length if length % 2 else length - 1)
    return window if window % 2 else window - 1


def refine_trajectory(
    trajectory: np.ndarray,
    *,
    window: int = DEFAULT_WINDOW,
    polyorder: int = DEFAULT_POLYORDER,
) -> RefinementResult:
    """Smooth one position series, or explain why it was left alone.

    A short episode is returned untouched rather than smoothed with a window
    that would span most of it: at that point the filter is not removing noise,
    it is replacing the trajectory with its own trend line.
    """

    trajectory = np.asarray(trajectory, dtype=np.float64)
    before = jerk_rms(trajectory)
    length = trajectory.shape[0]

    if length < MIN_WINDOW:
        return RefinementResult(
            trajectory.copy(), 0, polyorder, before, before, 0.0, False,
            f"episode is {length} frames, below the {MIN_WINDOW} a cubic fit needs",
        )
    if not np.isfinite(trajectory).all():
        return RefinementResult(
            trajectory.copy(), 0, polyorder, before, before, 0.0, False,
            "trajectory contains non-finite values",
        )

    effective = _odd_window(window, length)
    if effective <= polyorder:
        return RefinementResult(
            trajectory.copy(), 0, polyorder, before, before, 0.0, False,
            f"window {effective} cannot hold a degree-{polyorder} fit",
        )

    smoothed = savgol_filter(trajectory, effective, polyorder, axis=0)
    displacement = float(np.max(np.linalg.norm(smoothed - trajectory, axis=1)))
    return RefinementResult(
        smoothed,
        effective,
        polyorder,
        before,
        jerk_rms(smoothed),
        displacement,
        True,
        f"savgol window={effective} polyorder={polyorder}",
    )
