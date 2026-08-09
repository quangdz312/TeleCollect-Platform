"""Quy tắc chuyển trạng thái demo + kiểm tra quyền sở hữu.

Tách riêng khỏi router `src/api/demos.py` để: (1) test độc lập bằng object
Python thuần, không cần DB/HTTP; (2) tránh copy-paste cùng một rule ở 5
endpoint (trim/label/review/reopen/delete).

Sơ đồ chuyển trạng thái (bản "nới" đã chốt trong plan):

    recorded ─┬─(label)──> labeled ──(review: approve|reject)──┐
              └─────────────(review: approve|reject)───────────┼──> approved | rejected
                                                                 │        │
                                                        (reopen, có outcome) │
                                                                 └── labeled  │
                                                        (reopen, chưa outcome)│
                                                                 └── recorded ┘

`review` KHÔNG bắt buộc phải qua `label` trước (nới) — approve một demo chưa
có nhãn sẽ tự gán `outcome=success`. `reject` không tự gán outcome vì từ chối
không đồng nghĩa với thất bại.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, status

from src.models.db import Episode, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services.security import ROLE_RANK

LABEL_ALLOWED_STATUSES = {DemoStatus.RECORDED, DemoStatus.LABELED}
TRIM_ALLOWED_STATUSES = {DemoStatus.RECORDED, DemoStatus.LABELED}
REVIEW_ALLOWED_STATUSES = {DemoStatus.RECORDED, DemoStatus.LABELED}
REOPEN_ALLOWED_STATUSES = {DemoStatus.APPROVED, DemoStatus.REJECTED}


def ensure_can_modify(demo: Episode, user: User) -> None:
    """operator chỉ sửa được demo CỦA CHÍNH MÌNH; reviewer trở lên sửa được
    mọi demo. Dùng cho trim/label/delete.

    KHÔNG dùng cho review/reopen — 2 hành động đó luôn yêu cầu role reviewer
    trở lên qua `require_min_role` ở router, không xét sở hữu (một operator
    không được tự duyệt demo của chính mình).
    """
    if ROLE_RANK[UserRole(user.role)] >= ROLE_RANK[UserRole.REVIEWER]:
        return
    if demo.operator_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Không có quyền sửa demo này"
        )


def ensure_status_in(demo: Episode, allowed: set[DemoStatus], action: str) -> None:
    """Sai transition -> 409 Conflict (khác 422 — đây là xung đột trạng thái
    nghiệp vụ, không phải input sai định dạng)."""
    if DemoStatus(demo.status) not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Không thể '{action}' khi demo đang ở trạng thái '{demo.status}'",
        )


def apply_label(demo: Episode, outcome: DemoOutcome, note: str | None) -> None:
    ensure_status_in(demo, LABEL_ALLOWED_STATUSES, "gắn nhãn")
    demo.outcome = outcome
    if note is not None:
        demo.note = note
    demo.status = DemoStatus.LABELED


def apply_trim(demo: Episode, trim_start_s: float, trim_end_s: float) -> None:
    """Chỉ ghi metadata, KHÔNG đụng file video gốc."""
    ensure_status_in(demo, TRIM_ALLOWED_STATUSES, "trim")
    duration = demo.duration_s if demo.duration_s is not None else 0.0
    if not (0 <= trim_start_s < trim_end_s <= duration):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"trim_start_s/trim_end_s không hợp lệ: cần 0 <= trim_start_s < "
                f"trim_end_s <= duration_s ({duration})"
            ),
        )
    demo.trim_start_s = trim_start_s
    demo.trim_end_s = trim_end_s


def apply_review(
    demo: Episode, decision: Literal["approve", "reject"], reviewer_id: str, note: str | None
) -> None:
    ensure_status_in(demo, REVIEW_ALLOWED_STATUSES, "duyệt")

    if decision == "approve":
        if demo.outcome is None:
            demo.outcome = DemoOutcome.SUCCESS
        demo.status = DemoStatus.APPROVED
    else:
        # reject KHÔNG tự gán outcome — từ chối không đồng nghĩa thất bại
        # (có thể do quay hỏng, chọn sai task...).
        demo.status = DemoStatus.REJECTED

    demo.reviewer_id = reviewer_id
    demo.reviewed_at = datetime.now(timezone.utc)
    if note is not None:
        demo.note = note


def apply_reopen(demo: Episode) -> None:
    """Giữ bất biến "có outcome ⟺ status >= labeled": có outcome -> quay về
    `labeled` (không phải `recorded`, vì đó là công của operator, không phải
    của reviewer — không được xoá mất). Chưa có outcome -> quay về `recorded`.
    Xoá sạch `reviewer_id`/`reviewed_at`."""
    ensure_status_in(demo, REOPEN_ALLOWED_STATUSES, "mở lại")
    demo.status = DemoStatus.LABELED if demo.outcome is not None else DemoStatus.RECORDED
    demo.reviewer_id = None
    demo.reviewed_at = None
