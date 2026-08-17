from src.labeling.auto_gate import (
    IDLE_AFTER_TRIM_AUTO_APPROVE_LIMIT,
    MAX_AUTO_APPROVE_PENALTY,
    apply,
    evaluate,
)


def _record(**overrides):
    record = {
        "episode_id": "lift_clean_seed100::demo_0",
        "task": "lift",
        "requested_quality": "clean",
        "recorded_success": True,
        "auto_flags": {
            "failed_checks": [],
            "unavailable_checks": [],
            "worst_penalty_value": 0.05,
        },
        "provenance": {"sampled_variation": {"fault_type": "none"}},
    }
    record.update(overrides)
    return record


def test_failed_simulator_predicate_is_auto_rejected():
    assert evaluate(_record(recorded_success=False)).action == "reject"


def test_failed_hard_check_is_auto_rejected():
    flags = {"failed_checks": ["E_no_drop"], "unavailable_checks": []}
    assert evaluate(_record(auto_flags=flags)).action == "reject"


def test_unavailable_check_stays_in_review():
    flags = {"failed_checks": [], "unavailable_checks": ["E_skill"]}
    assert evaluate(_record(auto_flags=flags)).action == "review"


def test_medium_and_poor_stay_in_review_even_when_successful():
    assert evaluate(_record(requested_quality="medium")).action == "review"
    assert evaluate(_record(requested_quality="poor")).action == "review"


def test_large_soft_penalty_stays_in_review():
    flags = {
        "failed_checks": [], "unavailable_checks": [],
        "worst_penalty_value": MAX_AUTO_APPROVE_PENALTY + 0.01,
    }
    assert evaluate(_record(auto_flags=flags)).action == "review"


def test_idle_after_trim_has_a_separate_conservative_limit():
    flags = {
        "failed_checks": [],
        "unavailable_checks": [],
        "worst_penalty": "idle_after_trim",
        "worst_penalty_value": 0.20,
    }
    assert evaluate(_record(auto_flags=flags)).action in {"approve", "audit"}
    flags["worst_penalty_value"] = IDLE_AFTER_TRIM_AUTO_APPROVE_LIMIT + 0.01
    assert evaluate(_record(auto_flags=flags)).action == "review"


def test_same_value_for_jerkiness_remains_in_review():
    flags = {
        "failed_checks": [],
        "unavailable_checks": [],
        "worst_penalty": "jerkiness",
        "worst_penalty_value": 0.20,
    }
    assert evaluate(_record(auto_flags=flags)).action == "review"


def test_strict_pass_is_approved_or_sampled_for_audit():
    assert evaluate(_record()).action in {"approve", "audit"}


def test_toolhang_requires_both_stages_and_done_terminal_phase():
    incomplete = _record(task="tool_hang")
    assert evaluate(incomplete).action == "review"
    complete = _record(
        task="tool_hang",
        provenance={
            "terminal_phase": "done",
            "sampled_variation": {
                "stage1_env_predicate": True,
                "stage2_tool_on_frame": True,
            },
        },
    )
    assert evaluate(complete).action in {"approve", "audit"}


def test_apply_never_overwrites_existing_human_label():
    class Space:
        appended = []

        def labels_by_id(self):
            return {"human::0": {"human_decision": "rejected", "decision_source": "human"}}

        def scores(self):
            return [_record(episode_id="human::0"), _record(episode_id="failed::0", recorded_success=False)]

        def append_label(self, episode_id, **kwargs):
            self.appended.append((episode_id, kwargs))

    space = Space()
    counts = apply(space)
    assert counts["skipped"] == 1
    assert counts["rejected"] == 1
    assert [item[0] for item in space.appended] == ["failed::0"]
    assert space.appended[0][1]["decision_source"] == "auto_gate"
