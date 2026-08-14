"""ToolHang is labelled by the simulator, and that must not disturb the rest.

Two halves. The first pins the rule engine's ToolHang verdict to the recorded
environment predicates. The second pins the penalty layer: the three measures
that cannot mean anything for this task report zero *for this task only*, and
lift/can/square keep the numbers they had before.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.labeling.features import EpisodeArrays
from src.labeling.penalties import (
    EXPECTED_GRIPPER_TOGGLES,
    PREDICATE_LABELLED_TASKS,
    RawPenaltyFeatures,
    build_task_stats,
    penalties,
)
from src.labeling.rule_engine import RuleEpisode, evaluate_rules

# --- the rule --------------------------------------------------------------

#: Key names copied from a real collected episode's
#: `telecollect_sampled_variation`, not from the collector's source.
def _episode(
    stage1: object = True,
    stage2: object = True,
    *,
    task: str = "tool_hang",
    failure_stage: str = "",
    terminal_phase: str = "done",
    with_variation: bool = True,
) -> RuleEpisode:
    variation = {}
    if with_variation:
        variation = {"stage1_env_predicate": stage1, "stage2_tool_on_frame": stage2}
    return RuleEpisode(
        "th",
        task,
        "scripted",
        metadata={
            "sampled_variation": variation,
            "failure_stage": failure_stage,
            "terminal_phase": terminal_phase,
        },
    )


def test_both_predicates_true_suggests_pass():
    result = evaluate_rules(_episode(True, True))

    assert result.task == "tool_hang"
    assert result.final_recommendation == "suggest_pass"
    assert result.results[0].rule_id == "tool_hang.env_predicate"
    assert result.results[0].status == "pass"


def test_stage1_predicate_false_rejects_with_the_recorded_reason():
    result = evaluate_rules(
        _episode(False, False, failure_stage="grasp_missed", terminal_phase="reach_grip"),
    )

    assert result.final_recommendation == "auto_reject"
    check = result.results[0]
    assert check.status == "fail"
    assert check.measured_values["failed_stage"] == "stage1"
    assert check.measured_values["failure_kind"] == "grasp_missed"
    assert check.measured_values["terminal_phase"] == "reach_grip"
    assert "grasp_missed" in check.message


def test_stage2_predicate_false_rejects_and_names_stage2():
    result = evaluate_rules(
        _episode(True, False, failure_stage="thread_failed", terminal_phase="thread"),
    )

    assert result.final_recommendation == "auto_reject"
    assert result.results[0].measured_values["failed_stage"] == "stage2"
    assert result.results[0].measured_values["failure_kind"] == "thread_failed"


def test_missing_provenance_needs_review():
    """Older datasets have no predicates; a human decides, as they do today."""

    result = evaluate_rules(_episode(with_variation=False))

    assert result.final_recommendation == "needs_review"
    assert result.results[0].status == "cannot_evaluate"
    assert result.cannot_evaluate


def test_predicate_recorded_as_non_boolean_is_not_trusted():
    """A JSON null (an unfinished stage 2) must not read as a passing predicate."""

    result = evaluate_rules(_episode(True, None))

    assert result.final_recommendation == "needs_review"
    assert result.results[0].status == "cannot_evaluate"


def test_collector_tool_name_normalises_to_the_task():
    """The collector writes tool_name `tool_hang_stage1` for the full task."""

    result = evaluate_rules(_episode(True, True, task="tool_hang_stage1"))

    assert result.task == "tool_hang"
    assert result.final_recommendation == "suggest_pass"


def test_an_unrelated_task_still_falls_through_to_cannot_evaluate():
    result = evaluate_rules(RuleEpisode("x", "some_other_task", "scripted"))

    assert result.results[0].rule_id == "task.supported"
    assert result.final_recommendation == "needs_review"


# --- the penalties ---------------------------------------------------------


def _raw(task_toggles: int = 4, idle: float = 0.5661) -> RawPenaltyFeatures:
    """Numbers taken from a real collected full-task ToolHang episode."""

    return RawPenaltyFeatures(
        jerk_rms=0.00107,
        gripper_toggles=task_toggles,
        expected_gripper_toggles=EXPECTED_GRIPPER_TOGGLES.get("tool_hang", 2),
        path_length_m=4.358,
        length=1929,
        saturation_fraction=0.129,
        idle_ratio_after_trim=idle,
    )


def _by_name(results):
    return {item.name: item for item in results}


def _corpus(task: str, raw: RawPenaltyFeatures, count: int = 40):
    """A corpus large enough that the relative penalties would otherwise fire."""

    # The lengths have to actually vary or the MAD is zero and `unusual_length`
    # is undefined rather than merely small.
    spread = [
        RawPenaltyFeatures(
            jerk_rms=raw.jerk_rms * 0.1,
            gripper_toggles=raw.gripper_toggles,
            expected_gripper_toggles=raw.expected_gripper_toggles,
            path_length_m=raw.path_length_m,
            length=raw.length // 4 + (index % 11),
            saturation_fraction=raw.saturation_fraction,
            idle_ratio_after_trim=0.0,
        )
        for index in range(count)
    ]
    return build_task_stats(task, [raw, *spread])


def test_tool_hang_expects_four_gripper_toggles():
    """Stage 1 grasps the frame, stage 2 the wrench: measured 4, not the default 2."""

    assert EXPECTED_GRIPPER_TOGGLES["tool_hang"] == 4

    results = _by_name(penalties(_raw(), _corpus("tool_hang", _raw())))

    assert results["gripper_toggles"].value == 0.0


def test_tool_hang_relative_and_idle_penalties_contribute_zero():
    """The batch-relative pair and the physically mandated stillness are advisory."""

    raw = _raw()
    results = _by_name(penalties(raw, _corpus("tool_hang", raw)))

    for name in ("jerkiness", "unusual_length", "idle_after_trim"):
        assert results[name].value == 0.0, name
        assert results[name].detail["status"] == "advisory_only"

    # Neutralised, not hidden: the measured value stays visible to a reviewer.
    assert results["idle_after_trim"].raw == pytest.approx(raw.idle_ratio_after_trim)
    assert results["jerkiness"].detail["jerk_rms"] == pytest.approx(raw.jerk_rms)
    assert results["unusual_length"].detail["length"] == raw.length


def test_the_worst_tool_hang_penalty_no_longer_zeroes_a_correct_episode():
    """Together these were enough to drive a verified-correct episode to score 0."""

    raw = _raw()
    worst = max(item.value for item in penalties(raw, _corpus("tool_hang", raw)))

    assert worst < 1.0


# --- the regression: nothing above may touch the other three tasks ---------


@pytest.mark.parametrize("task", ["lift", "can", "square"])
def test_other_tasks_are_not_exempted(task):
    assert task not in PREDICATE_LABELLED_TASKS


@pytest.mark.parametrize("task", ["lift", "can", "square"])
def test_other_tasks_still_gate_on_idle_and_the_relative_penalties(task):
    """The same inputs that ToolHang now waives must still be penalised here."""

    raw = _raw(task_toggles=EXPECTED_GRIPPER_TOGGLES[task])
    results = _by_name(penalties(raw, _corpus(task, raw)))

    # idle_after_trim's band is 0.10-0.40, so 0.566 saturates it.
    assert results["idle_after_trim"].value == 1.0
    assert results["idle_after_trim"].detail == {}
    # This episode is the longest and jerkiest of its corpus by construction.
    assert results["jerkiness"].value > 0.0
    assert results["unusual_length"].value > 0.0
    assert "status" not in results["jerkiness"].detail
    assert "status" not in results["unusual_length"].detail


@pytest.mark.parametrize("task", ["lift", "can", "square"])
def test_small_corpus_reason_is_unchanged_for_other_tasks(task):
    """The pre-existing insufficient-corpus path must still report itself."""

    raw = _raw(task_toggles=EXPECTED_GRIPPER_TOGGLES[task])
    results = _by_name(penalties(raw, build_task_stats(task, [raw])))

    assert results["jerkiness"].value == 0.0
    assert results["jerkiness"].detail["status"] == "insufficient_corpus"
    assert results["unusual_length"].detail["status"] == "insufficient_corpus"
    # Still gated: this one was never corpus-relative.
    assert results["idle_after_trim"].value == 1.0


def test_expected_toggles_for_other_tasks_are_untouched():
    assert EXPECTED_GRIPPER_TOGGLES["lift"] == 1
    assert EXPECTED_GRIPPER_TOGGLES["can"] == 2
    assert EXPECTED_GRIPPER_TOGGLES["square"] == 2


def test_lift_scored_end_to_end_still_takes_the_unchanged_path():
    """Drive the real scorer, not just `penalties`, and pin what could leak.

    The exact scores are compared against the pre-change code out of band, on
    the collected datasets in `data/review`. What is pinned here is the thing a
    leak would show up as: a Lift episode picking up ToolHang's exemption.
    """

    from src.labeling.score import score_episodes

    def build(index: int) -> EpisodeArrays:
        frames = 40 + index
        rise = np.linspace(0.0, 0.12, frames + 1)
        obj = np.stack(
            [np.zeros(frames + 1), np.zeros(frames + 1), 0.80 + rise], axis=1,
        )
        eef = obj + np.array([0.005, 0.0, 0.0])
        actions = np.zeros((frames, 7))
        actions[:, 2] = 0.3
        actions[:, 6] = np.where(np.arange(frames) < 3, -1.0, 1.0)
        return EpisodeArrays(
            source=__import__("pathlib").Path("synthetic.hdf5"),
            demo=f"demo_{index}",
            task="lift",
            quality="clean",
            actions=actions,
            eef_position=eef[:-1],
            gripper_qpos=np.tile(np.array([0.02, -0.02]), (frames, 1)),
            object_position=obj[:-1],
            object_orientation=np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (frames, 1)),
            final_eef_position=eef[-1],
            final_gripper_qpos=np.array([0.02, -0.02]),
            final_object_position=obj[-1],
            final_object_orientation=np.array([1.0, 0.0, 0.0, 0.0]),
            rewards=np.zeros(frames),
            dones=np.concatenate((np.zeros(frames - 1), [1.0])),
            recorded_success=True,
            termination_reason="success",
        )

    scored, _ = score_episodes([build(i) for i in range(4)])

    assert len(scored) == 4
    for item in scored:
        assert item.task == "lift"
        flags = item.auto_flags()
        # None of the three ToolHang exemptions may appear on a Lift episode.
        for name in ("jerkiness", "unusual_length", "idle_after_trim"):
            assert flags["penalties"][name].get("status") != "advisory_only", name
        # Lift wants one toggle, and this batch issues exactly one.
        assert flags["penalties"]["gripper_toggles"]["raw"] == 0.0
