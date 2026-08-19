import glob

import numpy as np
import pytest

from src.labeling.refine import (
    DEFAULT_POLYORDER,
    DEFAULT_WINDOW,
    MAX_SAFE_DISPLACEMENT_M,
    MIN_WINDOW,
    _odd_window,
    jerk_rms,
    refine_trajectory,
    window_for_rate,
)

DATASETS = sorted(glob.glob("data/review/datasets/*.hdf5"))


@pytest.fixture(scope="module")
def episodes():
    if not DATASETS:
        pytest.skip("no reference corpus on this machine")
    from src.labeling.features import load_many

    return {episode.task: episode for episode in load_many(DATASETS)}


def test_straight_line_has_no_jerk_to_remove():
    line = np.linspace(0, 1, 40)[:, None] * np.array([1.0, 0.0, 0.0])
    result = refine_trajectory(line)

    assert result.applied
    assert result.jerk_before == pytest.approx(0.0, abs=1e-12)
    assert result.max_displacement_m == pytest.approx(0.0, abs=1e-9)


def test_noise_on_a_smooth_path_is_reduced():
    rng = np.random.default_rng(0)
    base = np.stack([np.linspace(0, 1, 200)] * 3, axis=1)
    noisy = base + rng.normal(scale=1e-3, size=base.shape)
    result = refine_trajectory(noisy)

    assert result.applied
    assert result.jerk_after < result.jerk_before


def test_short_episode_is_returned_untouched():
    short = np.zeros((MIN_WINDOW - 1, 3))
    result = refine_trajectory(short)

    assert not result.applied
    assert result.max_displacement_m == 0.0
    assert "below" in result.reason


def test_non_finite_input_is_refused_rather_than_smoothed():
    broken = np.zeros((40, 3))
    broken[10, 1] = np.nan
    result = refine_trajectory(broken)

    assert not result.applied
    assert "non-finite" in result.reason


def test_window_shrinks_to_fit_a_short_but_usable_episode():
    result = refine_trajectory(np.zeros((9, 3)), window=DEFAULT_WINDOW)

    assert result.applied
    assert result.window <= 9
    assert result.window % 2 == 1


def test_input_is_never_mutated():
    original = np.cumsum(np.ones((50, 3)), axis=0)
    copy = original.copy()
    refine_trajectory(original)

    assert np.array_equal(original, copy)


def test_reference_corpus_smooths_without_moving_far(episodes):
    # The cost of smoothing is how far the refined path departs from what the
    # simulator actually executed. Across the reference corpus that stays under
    # 1 cm, an order of magnitude below the 0.06 m grasp radius the checks use,
    # so refinement cannot turn a miss into a grasp or the reverse.
    for episode in episodes.values():
        result = refine_trajectory(episode.eef_position)
        assert result.applied
        assert result.jerk_after < result.jerk_before
        assert result.max_displacement_m < 0.01


def test_jerk_rms_matches_the_penalty_layer_proxy(episodes):
    from src.labeling.penalties import _jerk_rms

    for episode in episodes.values():
        assert jerk_rms(episode.eef_position) == pytest.approx(
            _jerk_rms(episode.eef_position),
        )


def test_defaults_match_the_published_recipe():
    # AXIS: Savitzky-Golay window 15, polynomial order 3, at the 20 Hz this
    # project records at.
    assert DEFAULT_WINDOW == 15
    assert DEFAULT_POLYORDER == 3


def test_window_is_sized_in_seconds_not_frames():
    # Scripted records at 20 Hz and teleoperation at 60 Hz. A fixed frame count
    # would smooth three times as much of one as of the other.
    from src.labeling.refine import DEFAULT_WINDOW_SECONDS, window_for_rate

    assert window_for_rate(20) == DEFAULT_WINDOW
    assert window_for_rate(60) == 45
    for hz in (20, 30, 60):
        assert window_for_rate(hz) / hz == pytest.approx(DEFAULT_WINDOW_SECONDS, abs=0.03)


def test_window_is_always_odd_and_holds_a_cubic():
    from src.labeling.refine import window_for_rate

    for hz in (1, 5, 20, 60, 240):
        window = window_for_rate(hz)
        assert window % 2 == 1
        assert window >= MIN_WINDOW


def test_si_jerk_is_comparable_across_control_rates():
    # The same physical motion sampled at two rates must report the same jerk in
    # m/s^2, even though the per-frame figure differs by the square of the ratio.
    from src.labeling.refine import jerk_rms_si

    seconds = np.linspace(0, 2, 20 * 2)
    slow = np.stack([np.sin(seconds), np.zeros_like(seconds), np.zeros_like(seconds)], axis=1)
    seconds_fast = np.linspace(0, 2, 60 * 2)
    fast = np.stack(
        [np.sin(seconds_fast), np.zeros_like(seconds_fast), np.zeros_like(seconds_fast)], axis=1,
    )

    assert jerk_rms_si(slow, 20) == pytest.approx(jerk_rms_si(fast, 60), rel=0.05)
    # The raw per-frame numbers differ by roughly the square of the rate ratio,
    # which is exactly the distortion the SI conversion undoes.
    assert jerk_rms(slow) / jerk_rms(fast) == pytest.approx((60 / 20) ** 2, rel=0.1)


def test_displacement_beyond_contact_tolerance_is_flagged_unsafe():
    from src.labeling.refine import MAX_SAFE_DISPLACEMENT_M
    from src.labeling.checks import DEFAULT_CHECKS

    # The bound is the contact tolerance itself, not a number picked nearby.
    assert MAX_SAFE_DISPLACEMENT_M == DEFAULT_CHECKS.together_tolerance_m

    rng = np.random.default_rng(1)
    rough = np.cumsum(rng.normal(scale=0.02, size=(200, 3)), axis=0)
    result = refine_trajectory(rough, control_hz=60)

    assert result.applied
    assert result.max_displacement_m > MAX_SAFE_DISPLACEMENT_M
    assert not result.safe_to_rescore


def test_refinement_moves_frames_past_the_contact_tolerance(episodes):
    # Recorded because it is the reason refinement cannot quietly replace the
    # recording. Every reference episode -- scripted included, at 0.0045-0.0085 m
    # -- lands near or above the 0.006 m drift that separates "still carrying
    # the object" from "lost it". Rescoring a smoothed path would be scoring a
    # trajectory the simulator never ran.
    displacements = {
        episode.task: refine_trajectory(
            episode.eef_position, control_hz=episode.control_hz,
        ).max_displacement_m
        for episode in episodes.values()
    }

    assert displacements, "no reference episodes loaded"
    assert max(displacements.values()) > MAX_SAFE_DISPLACEMENT_M
    # Still far below the grasp radius, so this is a scoring-fidelity limit
    # rather than a sign the smoothing is destroying the demonstration.
    assert max(displacements.values()) < 0.02


TELEOP_DIRS = sorted(glob.glob("data/episodes/*/"))


@pytest.fixture(scope="module")
def teleop_paths():
    """End-effector paths from the teleoperated recordings, at 60 Hz."""
    import pyarrow.parquet as pq

    paths = []
    for directory in TELEOP_DIRS:
        table = pq.read_table(directory + "actions.parquet", columns=["ee_pose"])
        rows = [row for row in table["ee_pose"].to_pylist() if row]
        if len(rows) < 100:
            continue
        path = np.asarray(rows, dtype=float)[:, :3]
        # An operator who connected but never moved has nothing to smooth.
        if jerk_rms(path) > 1e-6:
            paths.append(path)
    if not paths:
        pytest.skip("no teleoperated recordings on this machine")
    return paths


def test_splicing_filtered_frames_into_unfiltered_ones_adds_jerk(teleop_paths):
    # Documents why refinement is a uniform pass rather than a targeted one.
    #
    # Roughness does concentrate: on these recordings the 99th percentile of
    # per-frame jerk runs 31x to 312x the median, so smoothing only the worst
    # frames looks like the obvious saving. It is not. Filtered frames spliced
    # into unfiltered ones leave a step at every seam, and a step is the
    # quantity being removed.
    #
    # Checked on teleoperation rather than scripted collection because that is
    # where it bites: the same splice on 20 Hz scripted data lands within 3% of
    # the recording, so a scripted-only experiment would have missed it.
    from scipy.signal import savgol_filter

    window = window_for_rate(60)
    for path in teleop_paths:
        filtered = savgol_filter(path, window, DEFAULT_POLYORDER, axis=0)
        second = path[2:] - 2 * path[1:-1] + path[:-2]
        per_frame = np.linalg.norm(second, axis=1)
        rough = np.concatenate(
            ([False], per_frame > np.percentile(per_frame, 90), [False]),
        )

        spliced = path.copy()
        spliced[rough] = filtered[rough]

        assert jerk_rms(filtered) < jerk_rms(path)
        assert jerk_rms(spliced) > jerk_rms(path) * 2


def test_teleoperation_is_rougher_than_scripted_in_physical_units(
    episodes, teleop_paths,
):
    # The comparison that has to be made in m/s^2. On the raw per-frame figure
    # teleoperation reads 4.8x smoother than scripted collection purely because
    # it records at 60 Hz against 20 Hz.
    from src.labeling.refine import jerk_rms_si

    scripted = [
        jerk_rms_si(episode.eef_position, episode.control_hz)
        for episode in episodes.values()
    ]
    teleop = [jerk_rms_si(path, 60) for path in teleop_paths]

    assert max(teleop) > max(scripted)
    # And it is far more variable, which is what human input looks like.
    assert max(teleop) / min(teleop) > max(scripted) / min(scripted)
