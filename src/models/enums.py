"""Trạng thái và vai trò dùng chung giữa lớp API và lớp CSDL."""

from enum import StrEnum


class UserRole(StrEnum):
    """Ba vai trò, có kế thừa quyền: admin ⊇ reviewer ⊇ operator."""

    OPERATOR = "operator"
    """Thu demo: upload, ghi, gắn nhãn bản ghi của mình."""

    REVIEWER = "reviewer"
    """Mọi quyền operator + duyệt/mở lại demo của bất kỳ ai, tạo/xoá dataset."""

    ADMIN = "admin"
    """Mọi quyền reviewer + CRUD user + CRUD task."""


class DatasetStatus(StrEnum):
    """Trạng thái đóng gói zip của một dataset (chạy nền qua BackgroundTasks)."""

    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class DemoStatus(StrEnum):
    """Vòng đời một demonstration trong quy trình human-in-the-loop."""

    RECORDING = "recording"
    """Đang ghi, chưa chốt."""

    RECORDED = "recorded"
    """Đã chốt, chờ operator gắn nhãn."""

    LABELED = "labeled"
    """Đã gắn nhãn thành công/thất bại, chờ reviewer duyệt."""

    APPROVED = "approved"
    """Reviewer đã duyệt — đủ điều kiện vào dataset huấn luyện."""

    REJECTED = "rejected"
    """Reviewer từ chối; không vào dataset."""


class DemoOutcome(StrEnum):
    """Nhãn kết quả operator gán cho bản ghi."""

    SUCCESS = "success"
    FAILURE = "failure"


class DatasetFormat(StrEnum):
    """Định dạng export."""

    LEROBOT = "lerobot"
    RLDS = "rlds"


class JobStatus(StrEnum):
    """Trạng thái job chạy nền (export, huấn luyện, đánh giá)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
