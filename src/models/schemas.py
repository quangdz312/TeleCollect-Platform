"""Schema Pydantic cho biên API.

Trách nhiệm: hợp đồng request/response giữa backend và frontend. Payload
realtime của WebSocket teleop cố tình KHÔNG dùng model ở đây — validate
Pydantic mỗi chu kỳ 30 Hz là chi phí không cần thiết trên đường nóng; giao
thức đó được mô tả trong `src/api/teleop.py`.
"""

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from src.models.enums import (
    DatasetStatus,
    DemoOutcome,
    DemoStatus,
    JobStatus,
    UserRole,
)

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Bọc chung cho mọi list endpoint có phân trang — `total` lấy bằng COUNT
    query riêng ở tầng router, không phải `len()` cả bảng rồi cắt trong Python."""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72
"""bcrypt cắt input ở 72 byte — validate độ dài ở tầng schema trước khi tới service."""


class UserResponse(BaseModel):
    """Thông tin người dùng trả về qua API. TUYỆT ĐỐI không có `password_hash`."""

    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterRequest(BaseModel):
    """Tự đăng ký — role luôn ép về operator, client không tự chọn được."""

    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(default="", max_length=150)


class RefreshRequest(BaseModel):
    """Đổi refresh token lấy cặp token mới."""

    refresh_token: str


class ChangePasswordRequest(BaseModel):
    """User tự đổi mật khẩu — bắt buộc xác nhận mật khẩu cũ."""

    old_password: str
    new_password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class UserCreateRequest(BaseModel):
    """Admin tạo user mới — được chọn role, khác `RegisterRequest`."""

    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(default="", max_length=150)
    role: UserRole = UserRole.OPERATOR


class UserUpdateRequest(BaseModel):
    """Admin sửa user — mọi field optional, chỉ áp field nào được gửi lên."""

    display_name: str | None = Field(default=None, max_length=150)
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(
        default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH
    )


class TokenResponse(BaseModel):
    """Cặp token cấp cho client."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


TASK_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,49}$"
"""`name` là khoá chính và nằm trong URL path — bắt buộc chữ thường, số, gạch dưới."""


class TaskResponse(BaseModel):
    """Một task demo khả dụng."""

    name: str
    description: str
    instruction: str
    hints: list[str]
    action_dim: int = Field(..., description="Số chiều action, frontend dùng để map input")
    max_steps: int

    model_config = {"from_attributes": True}


class TaskCreateRequest(BaseModel):
    """Admin tạo task mới."""

    name: str = Field(..., pattern=TASK_NAME_PATTERN)
    description: str = Field(default="", max_length=2000)
    instruction: str = Field(default="", max_length=2000)
    hints: list[str] = Field(default_factory=list)
    action_dim: int = Field(..., ge=1)
    max_steps: int = Field(..., ge=1)


class TaskUpdateRequest(BaseModel):
    """Admin sửa task — mọi field optional. `name` được khai báo tường minh
    (không phải bị Pydantic âm thầm bỏ qua) để endpoint có thể trả 400 nếu
    client cố đổi `name` — `name` là PK và `episodes.task_name` đang tham
    chiếu tới, đổi được sẽ làm hỏng dữ liệu demo đã có."""

    name: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    instruction: str | None = Field(default=None, max_length=2000)
    hints: list[str] | None = None
    action_dim: int | None = Field(default=None, ge=1)
    max_steps: int | None = Field(default=None, ge=1)


class TaskStatsResponse(BaseModel):
    """Thống kê demo theo task — `total=0` là trạng thái bình thường (chưa có
    demo nào), không phải lỗi; success_rate/approval_rate phải là 0.0, không
    được chia cho 0."""

    task_name: str
    total: int
    by_status: dict[str, int]
    by_outcome: dict[str, int]
    approved_count: int
    success_count: int
    success_rate: float
    approval_rate: float


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
    """Một demonstration trong danh sách — không có field control-loop
    (latency/jitter/dropped_frames/auto_success) vì bản Core không có sim/
    Teleop đứng sau sinh ra những số liệu đó."""

    id: str
    task_name: str
    operator_id: str
    status: DemoStatus
    outcome: DemoOutcome | None = None
    note: str = ""
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None

    fps: float | None = None
    num_frames: int | None = None
    duration_s: float | None = None
    size_bytes: int | None = None

    trim_start_s: float | None = None
    trim_end_s: float | None = None

    has_wrist: bool = False
    has_trajectory: bool = False

    created_at: datetime

    model_config = {"from_attributes": True}


class DemoDetailResponse(DemoResponse):
    """Chi tiết một demo khi mở trang xem lại — thêm `has_thumbnail`, tính
    lúc request (kiểm tra file trên đĩa) chứ không lưu cột riêng trong DB."""

    has_thumbnail: bool = False


class DemoUploadResponse(DemoDetailResponse):
    """Response của `POST /demos/upload`. `warnings` báo các bước phụ (vd
    sinh thumbnail) thất bại nhưng KHÔNG làm fail cả upload."""

    warnings: list[str] = Field(default_factory=list)


class TrimRequest(BaseModel):
    """Yêu cầu cắt bớt đầu/cuối bản ghi — đơn vị GIÂY (bản Core không có
    control loop sinh frame index thật). Chỉ ghi metadata, không đụng file
    video gốc. Validate `0 <= trim_start_s < trim_end_s <= duration_s` thực
    hiện ở router (cần `duration_s` của chính demo, schema không tự biết)."""

    trim_start_s: float = Field(..., ge=0)
    trim_end_s: float = Field(..., ge=0)


class LabelRequest(BaseModel):
    """Operator (hoặc reviewer trở lên) gắn nhãn kết quả cho bản ghi."""

    outcome: DemoOutcome
    note: str | None = Field(default=None, max_length=1000)


class ReviewRequest(BaseModel):
    """Reviewer duyệt hoặc từ chối demo.

    `decision=approve` mà demo chưa có nhãn thì tự gán `outcome=success`
    (quy tắc "nới" đã chốt trong plan — nhóm ít người, review thường do cùng
    1 người bấm). `decision=reject` KHÔNG tự gán outcome: từ chối không đồng
    nghĩa với thất bại (có thể do quay hỏng, chọn sai task...).
    """

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class DemoSummaryResponse(BaseModel):
    """Bảng tổng hợp toàn bộ demo — mẫu số của từng rate được chốt rõ để
    tránh hiểu nhầm (và để frontend biết mẫu số là gì mà không phải đoán):

    - `success_rate`  = success_count / (success_count + failure_count)
      — tỷ lệ trong số demo ĐÃ CÓ NHÃN, không chia cho `total`.
    - `approval_rate` = approved_count / (approved_count + rejected_count)
      — tỷ lệ trong số demo ĐÃ REVIEW, không chia cho `total`.
    - Mẫu số = 0 -> rate = 0.0, không bao giờ ZeroDivisionError.
    """

    total: int
    by_status: dict[str, int]
    by_outcome: dict[str, int]
    by_task: dict[str, int]

    labeled_count: int
    reviewed_count: int
    success_count: int
    approved_count: int

    success_rate: float
    approval_rate: float

    total_frames: int
    total_duration_hours: float
    total_size_bytes: int


DATASET_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{2,63}$"
"""`name` vừa là thư mục gốc trong file zip vừa nằm trong tên file zip trên
đĩa (`<name>.zip`) — bắt buộc chữ thường, số, gạch dưới/gạch ngang, không bắt
đầu bằng ký tự đặc biệt (tránh mọi khả năng path traversal qua tên)."""


class DatasetCreateRequest(BaseModel):
    """Yêu cầu đóng gói một dataset mới từ các demo `approved`."""

    name: str = Field(..., pattern=DATASET_NAME_PATTERN)
    task_names: list[str] = Field(
        default_factory=list, description="Rỗng = mọi task"
    )
    include_failures: bool = Field(
        default=False, description="True: gom cả demo outcome=failure, không chỉ success"
    )
    overwrite: bool = Field(
        default=False, description="True: xoá dataset cùng tên (record + zip cũ) rồi tạo lại"
    )


class DatasetResponse(BaseModel):
    """Một dataset đã đóng băng từ các demo đã duyệt — snapshot tại thời
    điểm tạo, KHÔNG cập nhật khi demo nguồn đổi trạng thái sau đó."""

    id: str
    name: str
    task_names: list[str]
    include_failures: bool
    status: DatasetStatus
    num_episodes: int
    num_frames: int
    size_bytes: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DatasetDetailResponse(DatasetResponse):
    """Chi tiết một dataset — thêm lỗi build (nếu `status=failed`) và danh
    sách demo đã đóng băng vào dataset."""

    error_message: str | None = None
    episodes: list[DemoResponse] = Field(default_factory=list)


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
