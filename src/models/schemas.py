"""Schema Pydantic cho biên API.

Trách nhiệm: hợp đồng request/response giữa backend và frontend. Payload
realtime của WebSocket teleop cố tình KHÔNG dùng model ở đây — validate
Pydantic mỗi chu kỳ 30 Hz là chi phí không cần thiết trên đường nóng; giao
thức đó được mô tả trong `src/api/teleop.py`.
"""

from pydantic import BaseModel, Field

from src.models.enums import (
    DatasetFormat,
    DemoOutcome,
    DemoStatus,
    JobStatus,
    UserRole,
)


class UserResponse(BaseModel):
    """Thông tin người dùng trả về sau đăng nhập."""

    id: str
    username: str
    role: UserRole


class TokenResponse(BaseModel):
    """Cặp token cấp cho client."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TaskResponse(BaseModel):
    """Một task demo khả dụng."""

    name: str
    description: str
    action_dim: int = Field(..., description="Số chiều action, frontend dùng để map input")
    max_steps: int


class SessionResponse(BaseModel):
    """Phiên teleop vừa mở."""

    session_id: str
    task_name: str
    ws_url: str = Field(..., description="URL WebSocket để bắt đầu điều khiển")


class LoopStatsResponse(BaseModel):
    """Số đo độ trễ của một phiên — phục vụ tiêu chí tối ưu độ trễ."""

    ticks: int
    dropped_frames: int
    p95_latency_ms: float
    overruns: int


class DemoResponse(BaseModel):
    """Một demonstration trong danh sách."""

    id: str
    task_name: str
    operator_id: str
    status: DemoStatus
    outcome: DemoOutcome | None = None
    num_steps: int
    duration_s: float


class DemoDetailResponse(DemoResponse):
    """Chi tiết một demo khi mở trang xem lại."""

    trim_start: int | None = Field(default=None, description="Bước bắt đầu sau khi cắt")
    trim_end: int | None = Field(default=None, description="Bước kết thúc sau khi cắt")
    label_note: str = ""
    review_note: str = ""
    cameras: list[str] = Field(default_factory=list)


class TrimRequest(BaseModel):
    """Yêu cầu cắt bớt đầu/cuối bản ghi."""

    start_step: int = Field(..., ge=0)
    end_step: int = Field(..., ge=0)


class LabelRequest(BaseModel):
    """Operator gắn nhãn kết quả cho bản ghi."""

    outcome: DemoOutcome
    note: str = Field(default="", max_length=1000)


class ReviewRequest(BaseModel):
    """Reviewer duyệt hoặc từ chối demo."""

    approved: bool
    note: str = Field(default="", max_length=1000)


class DatasetResponse(BaseModel):
    """Một dataset đã đóng băng từ các demo đã duyệt."""

    id: str
    name: str
    task_names: list[str]
    num_episodes: int
    format: DatasetFormat
    dvc_tag: str | None = None


class TrainingJobResponse(BaseModel):
    """Trạng thái một job huấn luyện behavior cloning."""

    id: str
    dataset_id: str
    status: JobStatus
    epoch: int = 0
    train_loss: float | None = None


class EvalResultResponse(BaseModel):
    """Kết quả đánh giá policy trong sim — chỉ số nghiệm thu của dự án."""

    policy_id: str
    task_name: str
    num_episodes: int
    success_rate: float = Field(..., ge=0.0, le=1.0)


class PolicyResponse(BaseModel):
    """Một policy đã huấn luyện xong."""

    id: str
    dataset_id: str
    checkpoint_path: str
    success_rate: float | None = None
