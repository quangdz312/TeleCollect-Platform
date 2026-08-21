from src.labeling.auto_gate import apply, evaluate


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


def test_requested_quality_does_not_decide_anything():
    # The quality label is an input to generation, not a finding about the
    # episode. Asking for a rougher run and then holding that against the result
    # would make the gate answer a question it was never asked.
    for quality in ("clean", "good", "medium", "poor"):
        assert evaluate(_record(requested_quality=quality)).action in {"approve", "audit"}


def test_soft_penalties_do_not_gate():
    # Penalties describe how the scripted policy was written, no published
    # threshold exists for them, and action-only scores of this kind are not
    # known to predict policy performance. They are reported, never gated on.
    for name in ("idle_after_trim", "jerkiness", "wandering_path", "saturation"):
        flags = {
            "failed_checks": [],
            "unavailable_checks": [],
            "worst_penalty": name,
            "worst_penalty_value": 0.95,
        }
        assert evaluate(_record(auto_flags=flags)).action in {"approve", "audit"}


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


def _toolhang(**provenance):
    base = {
        "terminal_phase": "done",
        "sampled_variation": {
            "stage1_env_predicate": True,
            "stage2_tool_on_frame": True,
        },
    }
    base.update(provenance)
    return _record(task="tool_hang", provenance=base)


def test_toolhang_retry_goes_to_review():
    assert evaluate(_toolhang(retry_count=0)).action in {"approve", "audit"}
    assert evaluate(_toolhang(retry_count=1)).action == "review"


def test_repeated_seed_is_rejected_without_touching_the_first_one():
    class Space:
        def __init__(self):
            self.appended = []

        def labels_by_id(self):
            return {}

        def scores(self):
            return [
                {**_record(episode_id="lift::0"), "provenance": {"environment_seed": 4}},
                {**_record(episode_id="lift::1"), "provenance": {"environment_seed": 4}},
            ]

        def append_label(self, episode_id, **kwargs):
            self.appended.append((episode_id, kwargs))

    space = Space()
    counts = apply(space)

    assert counts["duplicates"] == 1
    rejected = [item for item in space.appended if item[1]["decision"] == "rejected"]
    assert [item[0] for item in rejected] == ["lift::1"]


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


def test_review_api_auto_label_matches_auto_gate_verdict():
    from src.api.labeling import _public

    approved = _public(_record(), include_score=False)
    rejected = _public(_record(recorded_success=False), include_score=False)
    medium = _public(_record(requested_quality="medium"), include_score=False)

    expected = "accept" if evaluate(_record()).action == "approve" else "review"
    assert approved["auto_label"] == expected
    assert rejected["auto_label"] == "reject"
    assert medium["auto_label"] == "review"
