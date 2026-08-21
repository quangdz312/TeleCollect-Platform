"""Cover E_released, whose threshold is derived from robosuite's own predicate."""

import glob

import pytest

from src.labeling.checks import DEFAULT_CHECKS, RELEASE_REQUIRED_TASKS, e_released
from src.labeling.features import load_many

DATASETS = sorted(glob.glob("data/review/datasets/*.hdf5"))


@pytest.fixture(scope="module")
def episodes():
    if not DATASETS:
        pytest.skip("no reference corpus on this machine")
    return {episode.task: episode for episode in load_many(DATASETS)}


def test_threshold_matches_the_robosuite_reach_term():
    # PickPlace and NutAssembly both AND their placement test with
    # r_reach < 0.6, where r_reach = 1 - tanh(10 * d). Solving gives d > 0.0424.
    import numpy as np

    d = DEFAULT_CHECKS.release_distance_m
    assert 1 - np.tanh(10 * d) == pytest.approx(0.6, abs=1e-3)


def test_release_required_only_where_the_predicate_asks_for_it():
    # Lift's predicate is a height test that holding the cube satisfies, and
    # ToolHang has its own two-stage predicate.
    assert RELEASE_REQUIRED_TASKS == {"can", "square"}


def test_tasks_without_a_release_requirement_pass_rather_than_abstain(episodes):
    # Returning None would put the check in unavailable_checks, which the gate
    # reads as missing evidence and answers with a review.
    for task in ("lift", "tool_hang"):
        if task not in episodes:
            continue
        result = e_released(episodes[task])
        assert result.value == 1
        assert result.evaluable


def test_reference_can_and_square_episodes_show_a_release(episodes):
    for task in ("can", "square"):
        if task not in episodes:
            continue
        result = e_released(episodes[task])
        assert result.value == 1, f"{task} should read as released"
        assert result.detail["max_distance_after_release_m"] > DEFAULT_CHECKS.release_distance_m


def test_lift_threshold_is_robosuite_margin_in_rise_above_resting_terms():
    # robosuite: cube_height > table_height + 0.04, measured from the table.
    # This check measures rise above the cube's resting height, which already
    # sits half a cube above the table (0.819 vs 0.800 on the reference
    # episode), so the same margin is 0.021 here. Copying 0.04 across would
    # demand twice the lift the simulator asks for and reject valid episodes.
    table_height = 0.800
    cube_resting = 0.8191
    robosuite_success_height = table_height + 0.04
    assert DEFAULT_CHECKS.lift_threshold_m == pytest.approx(
        robosuite_success_height - cube_resting, abs=0.002,
    )


def test_reference_lift_episode_reads_as_lifted(episodes):
    # 0.0275 m of rise: above this check's 0.02 and above robosuite's own 0.021
    # equivalent, so both agree the cube was lifted.
    import numpy as np

    if "lift" not in episodes:
        pytest.skip("no lift episode")
    from src.labeling.checks import _resting_height

    episode = episodes["lift"]
    rise = float(np.max(episode.object_trajectory[:, 2] - _resting_height(episode)))
    assert rise > DEFAULT_CHECKS.lift_threshold_m


def test_measured_after_the_last_hold_not_on_the_final_frame(episodes):
    # A scripted episode ends on a step budget, so the recording can stop while
    # the hand is still withdrawing. The reference Can episode clears the
    # threshold by 0.0005 m and is still moving away on its last frame.
    if "can" not in episodes:
        pytest.skip("no can episode")
    detail = e_released(episodes["can"]).detail
    assert detail["max_distance_after_release_m"] >= detail["final_distance_m"]
