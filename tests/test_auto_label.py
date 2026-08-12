from src.services.auto_label import classify_scripted, classify_teleop_metadata


def test_scripted_pass_is_accept_recommendation():
    result = classify_scripted("suggest_pass", True)
    assert result.label == "accept"


def test_scripted_hard_failure_is_reject_recommendation():
    result = classify_scripted("auto_reject", False)
    assert result.label == "reject"


def test_scripted_incomplete_evidence_needs_review():
    result = classify_scripted("needs_review", None)
    assert result.label == "review"


def test_scripted_success_with_all_hard_checks_pass_is_accept():
    result = classify_scripted(
        "needs_review",
        True,
        {"failed_checks": [], "unavailable_checks": [], "grasp_quality": {"score": 0.9}},
        "other",
    )
    assert result.label == "accept"


def test_scripted_warning_or_unavailable_check_stays_review():
    result = classify_scripted(
        "needs_review",
        True,
        {"failed_checks": [], "unavailable_checks": ["E_other"]},
    )
    assert result.label == "review"


def test_grasp_task_without_grasp_quality_stays_review():
    result = classify_scripted(
        "needs_review",
        True,
        {"failed_checks": [], "unavailable_checks": []},
        "lift",
    )
    assert result.label == "review"


def test_non_lift_scripted_task_can_accept_without_lift_grasp_gate():
    result = classify_scripted(
        "needs_review",
        True,
        {"failed_checks": [], "unavailable_checks": []},
        "can",
    )
    assert result.label == "accept"


def test_teleop_partial_recording_is_reject_recommendation():
    result = classify_teleop_metadata({"partial": True})
    assert result.label == "reject"


def test_teleop_success_stays_in_review_without_independent_verification():
    result = classify_teleop_metadata({"task_success": True})
    assert result.label == "review"


def test_teleop_explicit_failure_is_reject_recommendation():
    result = classify_teleop_metadata({"task_success": False})
    assert result.label == "reject"


def test_teleop_dropped_frames_stay_in_review():
    from src.services.auto_label import _teleop_quality_gate

    result = _teleop_quality_gate(
        {
            "privileged_state": {"recorded": True},
            "dropped_stream_frames": 1,
            "overruns": 0,
        }
    )
    assert result is not None
    assert result.label == "review"
