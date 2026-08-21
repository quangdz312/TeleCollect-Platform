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

**Why the whole trajectory, when roughness is not spread evenly?** It is a fair
question -- per-frame jerk in the reference teleoperated episodes puts 31x to
312x the median in its worst 1% of frames -- and smoothing only those frames was
tried. It does not work, for two reasons that showed up immediately:

* Splicing filtered frames into unfiltered ones puts a step at every seam, and a
  step is jerk. Swapping the worst 10% of frames on a 1102-frame recording made
  the episode *seven times rougher* than leaving it alone. Feathering the
  handover across the filter window fixes that, but only by widening the mask
  until it covers 58-82% of the episode, at which point it is the uniform pass
  with extra machinery.
* It saves nothing anyway. The frame that moves furthest is the roughest frame,
  which is inside the mask under every setting, so ``max_displacement_m`` came
  out identical to five decimal places at every dilation from 0 to 22.

The uniform pass stays. What actually limits the damage is not smoothing less of
the episode but knowing when the result stops being safe to score, which is what
:attr:`RefinementResult.safe_to_rescore` reports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import savgol_filter

#: Savitzky-Golay window, in seconds of motion. AXIS uses 15 frames at 20 Hz;
#: expressing it as a duration keeps the filter spanning the same movement at
#: any rate. Scripted collection records at 20 Hz and teleoperation at 60 Hz, so
#: a fixed frame count would smooth three times as much of one as of the other.
DEFAULT_WINDOW_SECONDS = 0.75
#: Frame-count fallback for callers that have no rate to hand, matching the
#: published recipe at the rate it was published for.
DEFAULT_WINDOW = 15
#: Polynomial order fitted inside each window. Cubic preserves an accelerating
#: reach; going lower flattens it into a straight line.
DEFAULT_POLYORDER = 3
#: Below this, a window cannot hold a cubic fit.
MIN_WINDOW = DEFAULT_POLYORDER + 2

#: How far a refined path may depart from the recorded one before the result is
#: no longer safe to score (m).
#:
#: This is not a taste threshold. ``checks.together_tolerance_m`` is 0.006 m --
#: the per-frame drift that separates "carrying the object" from "lost it" --
#: so a smoothing pass that moves a frame further than that can flip a hard
#: check and make the refined episode disagree with what the simulator ran.
#: Measured on the reference corpora: scripted peaks at 0.0085 m, teleoperation
#: at 0.0163 m, so teleoperated recordings do cross it.
#:
#: Crossing it does not make an episode bad. It means the smoothed path is a
#: derived artefact for training, and the recorded path stays the one the checks
#: read.
MAX_SAFE_DISPLACEMENT_M = 0.006


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

    @property
    def safe_to_rescore(self) -> bool:
        """Whether the hard checks would read the same story off the refined path.

        False does not mean the refinement is wrong or the episode is bad. It
        means this smoothed path moved frames further than the tolerance the
        contact checks work at, so it is a training artefact rather than a
        replacement for the record: score the recording, train on the refinement.
        """

        return self.max_displacement_m <= MAX_SAFE_DISPLACEMENT_M


def jerk_rms(series: np.ndarray) -> float:
    """RMS of the discrete second difference, matching the penalty layer's proxy.

    Per *frame*, so it is only comparable between episodes recorded at the same
    rate. Use :func:`jerk_rms_si` to compare across sources.
    """

    if series.shape[0] < 3:
        return 0.0
    second = series[2:] - 2.0 * series[1:-1] + series[:-2]
    return float(np.sqrt(np.mean(np.sum(second**2, axis=1))))


def jerk_rms_si(series: np.ndarray, control_hz: float) -> float:
    """The same measure in m/s^2, which is comparable across control rates.

    The frame-based figure is not: a second difference shrinks with the square
    of the timestep, so the same motion recorded at 60 Hz reports roughly a
    ninth of what it reports at 20 Hz. Comparing scripted collection at 20 Hz
    against teleoperation at 60 Hz on the raw number makes the rougher source
    look five times smoother than the other.
    """

    return jerk_rms(series) * float(control_hz) ** 2


def _odd_window(requested: int, length: int) -> int:
    """savgol needs an odd window no longer than the series."""

    window = min(requested, length if length % 2 else length - 1)
    return window if window % 2 else window - 1


def window_for_rate(control_hz: float, seconds: float = DEFAULT_WINDOW_SECONDS) -> int:
    """Frames spanning ``seconds`` at this rate, rounded up to an odd number."""

    frames = max(MIN_WINDOW, int(round(seconds * float(control_hz))))
    return frames if frames % 2 else frames + 1


def refine_trajectory(
    trajectory: np.ndarray,
    *,
    control_hz: float | None = None,
    window: int | None = None,
    polyorder: int = DEFAULT_POLYORDER,
) -> RefinementResult:
    """Smooth one position series, or explain why it was left alone.

    Pass ``control_hz`` and the window is sized in seconds of motion, which is
    what keeps a 60 Hz teleoperated recording and a 20 Hz scripted one smoothed
    by the same amount. An explicit ``window`` overrides it.

    A short episode is returned untouched rather than smoothed with a window
    that would span most of it: at that point the filter is not removing noise,
    it is replacing the trajectory with its own trend line.
    """

    if window is None:
        window = DEFAULT_WINDOW if control_hz is None else window_for_rate(control_hz)
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
