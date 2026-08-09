import pytest
from fastapi import HTTPException

from src.models.db import Episode, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services.demo_rules import (
    apply_label,
    apply_reopen,
    apply_review,
    apply_trim,
    ensure_can_modify,
    ensure_status_in,
)


def _episode(status: DemoStatus, outcome: DemoOutcome | None = None, duration_s: float = 10.0) -> Episode:
    return Episode(
        id="demo1",
        task_name="pick_place",
        operator_id="owner1",
        status=status,
        outcome=outcome,
        duration_s=duration_s,
    )


def _user(user_id: str, role: UserRole) -> User:
    return User(id=user_id, username=user_id, password_hash="x", display_name=user_id, role=role)


# --- ensure_can_modify -------------------------------------------------------------


def test_owner_operator_can_modify_own_demo():
    demo = _episode(DemoStatus.RECORDED)
    owner = _user("owner1", UserRole.OPERATOR)
    ensure_can_modify(demo, owner)  # không raise


def test_non_owner_operator_cannot_modify():
    demo = _episode(DemoStatus.RECORDED)
    other = _user("other1", UserRole.OPERATOR)
    with pytest.raises(HTTPException) as exc_info:
        ensure_can_modify(demo, other)
    assert exc_info.value.status_code == 403


def test_reviewer_can_modify_demo_of_others():
    demo = _episode(DemoStatus.RECORDED)
    reviewer = _user("rev1", UserRole.REVIEWER)
    ensure_can_modify(demo, reviewer)  # không raise


def test_admin_can_modify_demo_of_others():
    demo = _episode(DemoStatus.RECORDED)
    admin = _user("admin1", UserRole.ADMIN)
    ensure_can_modify(demo, admin)  # không raise


# --- ensure_status_in ----------------------------------------------------------


def test_ensure_status_in_allowed_does_not_raise():
    demo = _episode(DemoStatus.RECORDED)
    ensure_status_in(demo, {DemoStatus.RECORDED, DemoStatus.LABELED}, "test")


def test_ensure_status_in_not_allowed_raises_409():
    demo = _episode(DemoStatus.APPROVED)
    with pytest.raises(HTTPException) as exc_info:
        ensure_status_in(demo, {DemoStatus.RECORDED, DemoStatus.LABELED}, "test")
    assert exc_info.value.status_code == 409


# --- apply_label -----------------------------------------------------------------


@pytest.mark.parametrize("start_status", [DemoStatus.RECORDED, DemoStatus.LABELED])
def test_apply_label_valid_from_recorded_or_labeled(start_status):
    demo = _episode(start_status)
    apply_label(demo, DemoOutcome.SUCCESS, "note1")
    assert demo.status == DemoStatus.LABELED
    assert demo.outcome == DemoOutcome.SUCCESS
    assert demo.note == "note1"


@pytest.mark.parametrize("start_status", [DemoStatus.APPROVED, DemoStatus.REJECTED])
def test_apply_label_invalid_from_approved_or_rejected(start_status):
    demo = _episode(start_status)
    with pytest.raises(HTTPException) as exc_info:
        apply_label(demo, DemoOutcome.SUCCESS, None)
    assert exc_info.value.status_code == 409


def test_apply_label_note_none_keeps_existing_note():
    demo = _episode(DemoStatus.RECORDED)
    demo.note = "existing"
    apply_label(demo, DemoOutcome.FAILURE, None)
    assert demo.note == "existing"


# --- apply_trim --------------------------------------------------------------------


@pytest.mark.parametrize("start_status", [DemoStatus.RECORDED, DemoStatus.LABELED])
def test_apply_trim_valid_from_recorded_or_labeled(start_status):
    demo = _episode(start_status, duration_s=10.0)
    apply_trim(demo, 1.0, 5.0)
    assert demo.trim_start_s == 1.0
    assert demo.trim_end_s == 5.0


@pytest.mark.parametrize("start_status", [DemoStatus.APPROVED, DemoStatus.REJECTED])
def test_apply_trim_invalid_status_raises_409(start_status):
    demo = _episode(start_status, duration_s=10.0)
    with pytest.raises(HTTPException) as exc_info:
        apply_trim(demo, 1.0, 5.0)
    assert exc_info.value.status_code == 409


def test_apply_trim_start_greater_than_end_raises_422():
    demo = _episode(DemoStatus.RECORDED, duration_s=10.0)
    with pytest.raises(HTTPException) as exc_info:
        apply_trim(demo, 5.0, 1.0)
    assert exc_info.value.status_code == 422


def test_apply_trim_end_beyond_duration_raises_422():
    demo = _episode(DemoStatus.RECORDED, duration_s=10.0)
    with pytest.raises(HTTPException) as exc_info:
        apply_trim(demo, 0.0, 10.1)
    assert exc_info.value.status_code == 422


def test_apply_trim_start_equals_end_raises_422():
    demo = _episode(DemoStatus.RECORDED, duration_s=10.0)
    with pytest.raises(HTTPException):
        apply_trim(demo, 5.0, 5.0)


# --- apply_review ------------------------------------------------------------------


def test_apply_review_approve_from_recorded_auto_assigns_success_outcome():
    """Quy tắc 'nới': review thẳng từ recorded (chưa label) -> approve tự gán outcome=success."""
    demo = _episode(DemoStatus.RECORDED, outcome=None)
    apply_review(demo, "approve", reviewer_id="rev1", note=None)
    assert demo.status == DemoStatus.APPROVED
    assert demo.outcome == DemoOutcome.SUCCESS
    assert demo.reviewer_id == "rev1"
    assert demo.reviewed_at is not None


def test_apply_review_approve_keeps_existing_outcome():
    demo = _episode(DemoStatus.LABELED, outcome=DemoOutcome.FAILURE)
    apply_review(demo, "approve", reviewer_id="rev1", note=None)
    assert demo.outcome == DemoOutcome.FAILURE  # không bị ghi đè
    assert demo.status == DemoStatus.APPROVED


def test_apply_review_reject_does_not_auto_assign_outcome():
    demo = _episode(DemoStatus.RECORDED, outcome=None)
    apply_review(demo, "reject", reviewer_id="rev1", note=None)
    assert demo.status == DemoStatus.REJECTED
    assert demo.outcome is None  # KHÔNG tự gán


@pytest.mark.parametrize("start_status", [DemoStatus.APPROVED, DemoStatus.REJECTED])
def test_apply_review_invalid_from_approved_or_rejected_raises_409(start_status):
    demo = _episode(start_status)
    with pytest.raises(HTTPException) as exc_info:
        apply_review(demo, "approve", reviewer_id="rev1", note=None)
    assert exc_info.value.status_code == 409


# --- apply_reopen ------------------------------------------------------------------


def test_apply_reopen_with_outcome_goes_to_labeled():
    demo = _episode(DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)
    demo.reviewer_id = "rev1"
    demo.reviewed_at = "sometime"
    apply_reopen(demo)
    assert demo.status == DemoStatus.LABELED
    assert demo.outcome == DemoOutcome.SUCCESS  # giữ nguyên
    assert demo.reviewer_id is None
    assert demo.reviewed_at is None


def test_apply_reopen_without_outcome_goes_to_recorded():
    demo = _episode(DemoStatus.REJECTED, outcome=None)
    apply_reopen(demo)
    assert demo.status == DemoStatus.RECORDED
    assert demo.outcome is None


@pytest.mark.parametrize("start_status", [DemoStatus.RECORDED, DemoStatus.LABELED])
def test_apply_reopen_invalid_from_recorded_or_labeled_raises_409(start_status):
    demo = _episode(start_status)
    with pytest.raises(HTTPException) as exc_info:
        apply_reopen(demo)
    assert exc_info.value.status_code == 409


def test_apply_reopen_keeps_note():
    demo = _episode(DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)
    demo.note = "important note from operator"
    apply_reopen(demo)
    assert demo.note == "important note from operator"
