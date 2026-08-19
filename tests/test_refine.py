import glob

import numpy as np
import pytest

from src.labeling.refine import (
    DEFAULT_POLYORDER,
    DEFAULT_WINDOW,
    MIN_WINDOW,
    jerk_rms,
    refine_trajectory,
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
