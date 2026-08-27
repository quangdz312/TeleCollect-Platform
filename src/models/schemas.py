"""Schema Pydantic cho biên API.

Trách nhiệm: hợp đồng request/response giữa backend và frontend. Payload
realtime của WebSocket teleop cố tình KHÔNG dùng model ở đây — validate
Pydantic mỗi chu kỳ 30 Hz là chi phí không cần thiết trên đường nóng; giao
thức đó được mô tả trong `src/api/teleop.py`.
"""

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

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


class ActionSpecResponse(BaseModel):
    """Biên action mà frontend dùng để clamp input trước khi gửi."""

    dim: int
    low: list[float]
    high: list[float]


class CreateSessionRequest(BaseModel):
    """Yêu cầu mở một phiên teleop."""

    task_name: str = "lift_cube"
    operator_id: str = Field(default="", max_length=64)
    seed: int | None = None
    image_size: int = Field(default=640, ge=32, le=1024)
    """Độ phân giải ảnh ghi vào dataset.

    Trần nâng lên 1024 để cho phép 640: đo trên GPU rời, render 3 camera 640px
    tốn 3.4 ms so với 1.9 ms ở 256px, nên độ phân giải gần như miễn phí và
    không đáng đánh đổi lấy tốc độ (xem `src/sim/gpu.py`).
    """


class SessionResponse(BaseModel):
    """Phiên teleop vừa mở."""

    session_id: str
    task_name: str
    state: str = "idle"
    ws_url: str = Field(..., description="URL WebSocket để bắt đầu điều khiển")
    action_spec: ActionSpecResponse | None = None


class SessionDetailResponse(BaseModel):
    """Trạng thái đầy đủ của một phiên teleop."""

    session_id: str
    operator_id: str
    task_name: str
    state: str
    seed: int | None = None
    image_size: int
    started_at: float
    last_seen_at: float | None = None
    episode_id: str | None = None
    last_error: str | None = None
    ws_url: str


class ErrorResponse(BaseModel):
    """Lỗi có mã máy đọc được, khớp với event error trên WebSocket."""

    code: str
    detail: str


class LoopStatsResponse(BaseModel):
    """Số đo độ trễ của một phiên — phục vụ tiêu chí tối ưu độ trễ."""

    ticks: int
    dropped_frames: int
    p95_latency_ms: float
    overruns: int
    p50_latency_ms: float = 0.0
    jitter_rms_ms: float = 0.0
    control_hz_actual: float = 0.0


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

    auto_label: Literal["accept", "review", "reject"] = "review"
    auto_label_reason: str = ""
    auto_label_profile: Literal["scripted_strict", "teleop_tolerant"] = "teleop_tolerant"
    auto_label_profile_version: str = ""

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


class RawEpisodeCameras(BaseModel):
    """Availability of the canonical camera streams for a raw episode."""

    front: bool = False
    birdview: bool = False
    wrist: bool = False


class RawEpisodeResponse(BaseModel):
    """Source-neutral contract shared by teleop and scripted episodes.

    This model intentionally describes metadata only. Raw arrays and video
    bytes remain behind dedicated endpoints so list responses stay small.
    """

    episode_id: str = Field(..., min_length=1)
    display_name: str = ""
    source: Literal["teleop", "scripted", "unknown"]
    task: str = Field(..., min_length=1)
    created_at: datetime | None = None

    length: int = Field(..., ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    control_hz: float | None = Field(default=None, gt=0)
    size_bytes: int | None = Field(default=None, ge=0)

    recorded_success: bool | None = None
    quality: Literal["clean", "good", "medium", "poor"] | None = None
    review_status: Literal["pending", "approved", "rejected", "archived"]

    operator_id: str | None = None
    collection_batch_id: str | None = None
    cameras: RawEpisodeCameras = Field(default_factory=RawEpisodeCameras)
    artifact_health: Literal["healthy", "warning", "corrupted"] = "healthy"
    management_version: int = Field(default=0, ge=0)


class RawEpisodeSummaryResponse(BaseModel):
    total: int = Field(..., ge=0)
    teleop: int = Field(..., ge=0)
    scripted: int = Field(..., ge=0)
    successes: int = Field(..., ge=0)
    failures: int = Field(..., ge=0)
    pending: int = Field(..., ge=0)
    approved: int = Field(..., ge=0)
    rejected: int = Field(..., ge=0)
    archived: int = Field(..., ge=0)
    by_task: dict[str, int] = Field(default_factory=dict)
    by_quality: dict[str, int] = Field(default_factory=dict)
    by_batch: dict[str, int] = Field(default_factory=dict)
    by_day: dict[str, dict[str, int]] = Field(default_factory=dict)
    undated: int = Field(default=0, ge=0)


class CollectionBatchResponse(BaseModel):
    """Một đợt thu, kèm số liệu đủ để dựng thẻ batch ở trang Raw episodes."""

    id: str
    name: str
    task_name: str | None = None
    description: str = ""
    archived: bool = False
    created_at: datetime | None = None
    # False khi đợt thu chỉ tồn tại trong provenance của episode mà chưa ai tạo
    # bản ghi mô tả. Giao diện dùng cờ này để mời người dùng đặt tên.
    named: bool = True
    episodes: int = Field(default=0, ge=0)
    teleop: int = Field(default=0, ge=0)
    scripted: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)


class CollectionBatchCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(..., min_length=1, max_length=150)
    task_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class CollectionBatchUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    task_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


class CollectionBatchImportSkip(BaseModel):
    episode: str
    reason: str


class CollectionBatchImportResponse(BaseModel):
    """Kết quả nạp một zip thư mục batch từ app vào workspace review."""

    batch: CollectionBatchResponse
    episodes: int = Field(default=0, ge=0)
    videos: int = Field(default=0, ge=0)
    #: Tên các file collection đã dựng lại trong `data/review/datasets`.
    sources: list[str] = Field(default_factory=list)
    # Nạp một phần vẫn là thành công, nhưng phải nói rõ bỏ sót cái gì thay vì
    # im lặng đánh rơi bản ghi.
    skipped: list[CollectionBatchImportSkip] = Field(default_factory=list)


class RawEpisodePageResponse(BaseModel):
    items: list[RawEpisodeResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    total_pages: int = Field(..., ge=0)
    summary: RawEpisodeSummaryResponse
    available_tasks: list[str] = Field(default_factory=list)
    available_batches: list[str] = Field(default_factory=list)


class RawArtifactResponse(BaseModel):
    """One file or container that belongs to a raw episode."""

    name: str
    kind: Literal["metadata", "table", "video", "dataset"]
    exists: bool
    size_bytes: int | None = Field(default=None, ge=0)
    camera: Literal["front", "birdview", "wrist"] | None = None


class RawEpisodeAuditResponse(BaseModel):
    id: str
    action: str
    changes: dict[str, object]
    actor_name: str
    created_at: datetime


class RawEpisodeDetailResponse(RawEpisodeResponse):
    """Raw episode metadata plus a safe manifest without filesystem paths."""

    artifacts: list[RawArtifactResponse] = Field(default_factory=list)
    audit: list[RawEpisodeAuditResponse] = Field(default_factory=list)


class RawEpisodeArchiveUpdate(BaseModel):
    archived: bool
    expected_version: int = Field(..., ge=0)


class RawSignalSeries(BaseModel):
    labels: list[str]
    values: list[list[float]]


class RawSignalsResponse(BaseModel):
    episode_id: str
    source: Literal["teleop", "scripted"]
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)
    total_frames: int = Field(..., ge=0)
    sampled_frames: list[int]
    control_hz: float = Field(..., gt=0)
    video_stride: int = Field(default=1, ge=1)
    time_basis: Literal["recorded", "scripted_playback"]
    t: list[float]
    available_fields: list[str]
    signals: dict[str, RawSignalSeries]


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
    format: str = Field(
        default="raw", pattern="^(raw|robomimic)$",
        description="raw: core ZIP cũ; robomimic: HDF5 từ scripted hoặc manual teleop đã duyệt",
    )
    data_source: Literal["teleop", "scripted", "both"] = Field(
        default="both",
        description="Nguồn episode cho RoboMimic: manual teleop, scripted, hoặc cả hai",
    )
    collection_batch_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$",
        description="Chỉ export scripted episodes thuộc đúng collection batch này",
    )
    episode_ids: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="Nếu có, chỉ export đúng các raw episode ID đã chọn thủ công",
    )


class DatasetResponse(BaseModel):
    """Một dataset đã đóng băng từ các demo đã duyệt — snapshot tại thời
    điểm tạo, KHÔNG cập nhật khi demo nguồn đổi trạng thái sau đó."""

    id: str
    name: str
    task_names: list[str]
    include_failures: bool
    format: str
    status: DatasetStatus
    num_episodes: int
    num_frames: int
    size_bytes: int | None = None
    data_source: Literal["teleop", "scripted", "both", "unknown"] = "unknown"
    collection_batch_id: str | None = None
    created_by: str | None = None
    exporter_version: str = "1.0"
    created_at: datetime

    model_config = {"from_attributes": True}


class DatasetEpisodeSnapshot(BaseModel):
    episode_id: str
    source: Literal["teleop", "scripted"]
    task: str
    outcome: Literal["success", "failure", "unknown"] = "unknown"
    frames: int = Field(default=0, ge=0)
    review_status: str = "approved"


class DatasetDetailResponse(DatasetResponse):
    """Chi tiết một dataset — thêm lỗi build (nếu `status=failed`) và danh
    sách demo đã đóng băng vào dataset."""

    error_message: str | None = None
    episodes: list[DatasetEpisodeSnapshot] = Field(default_factory=list)
    schema_manifest: dict[str, object] = Field(default_factory=dict)


class DatasetRetryRequest(BaseModel):
    pass


class ScriptedRunRequest(BaseModel):
    """Yêu cầu thu một mẻ demo scripted để đem đi chấm tay."""

    task: str
    quality: str = "clean"
    episodes: int = Field(default=5, ge=1, le=200)
    seed: int | None = Field(default=None, ge=0)
    horizon: int | None = Field(default=None, ge=1)
    overwrite: bool = False
    collection_batch_id: str = Field(
        default="legacy", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$",
    )


class ScriptedLabelRequest(BaseModel):
    """Quyết định của người chấm cho một episode scripted."""

    episode_id: str
    decision: str
    reasons: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=1000)
    reviewer: str = Field(default="unknown", max_length=64)
    blind: bool = True


class MachineLogRequest(BaseModel):
    """Một lô log máy GPU thuê đẩy về giữa chừng lần train."""

    text: str = Field(max_length=1_000_000)


class TrainingJobRequest(BaseModel):
    """Cấu hình một lần chạy RoboMimic BC hoặc BC-RNN.

    Dataset và checkpoint luôn được tham chiếu bằng ID, không nhận đường dẫn từ
    client. Backend sẽ tự resolve chúng bên trong các thư mục được quản lý.
    """

    dataset_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(
        ...,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    policy: Literal["bc", "bc-rnn"] = "bc"
    epochs: int = Field(default=200, ge=1, le=10_000)
    batch_size: int = Field(default=32, ge=1, le=4096)
    num_workers: int = Field(default=0, ge=0, le=64)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    learning_rate: float = Field(default=1e-4, gt=0.0, le=1.0)
    seed: int = Field(default=1, ge=0, le=2_147_483_647)
    save_every_n_epochs: int | None = Field(default=None, ge=1, le=10_000)
    sequence_length: int = Field(default=50, ge=1, le=1024)
    rnn_hidden_dim: int = Field(default=400, ge=1, le=8192)
    rnn_layers: int = Field(default=2, ge=1, le=32)
    normalize_observations: bool = True
    observation_profile: Literal["minimal", "all"] = "minimal"
    rollout_enabled: bool = True
    rollout_every_n_epochs: int = Field(default=20, ge=1, le=10_000)
    rollout_episodes: int = Field(default=5, ge=1, le=200)
    rollout_horizon: int = Field(default=500, ge=1, le=20_000)
    wandb_enabled: bool = False
    wandb_project: str = Field(
        default="telecollect-robot-learning",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    wandb_entity: str | None = Field(default=None, min_length=1, max_length=128)


class WandbSettingsResponse(BaseModel):
    """Trạng thái tích hợp W&B của người đang đăng nhập.

    KHÔNG có trường nào chứa API key. `key_preview` là bốn ký tự cuối, đủ để
    nhận ra key nào đang lưu chứ không dùng lại được.
    """

    configured: bool = False
    key_preview: str = ""
    entity: str = ""


class WandbSettingsRequest(BaseModel):
    """Lưu key W&B. `api_key` bỏ trống nghĩa là chỉ đổi entity."""

    # W&B key là chuỗi hex 40 ký tự; chặn ở đây để người dùng biết ngay là dán
    # nhầm, thay vì phải đợi tới lúc job chạy mới báo lỗi xác thực.
    api_key: str | None = Field(default=None, min_length=8, max_length=200)
    entity: str = Field(default="", max_length=128)


class WandbVerifyResponse(BaseModel):
    """Kết quả thử key với W&B thật."""

    ok: bool
    detail: str = ""
    entity: str = ""


class TrainingCheckpointResponse(BaseModel):
    """Checkpoint do backend phát hiện trong đúng thư mục của training job."""

    id: str
    filename: str
    epoch: int = Field(..., ge=0)
    validation_loss: float | None = Field(default=None, ge=0.0)
    size_bytes: int = Field(..., ge=0)
    created_at: datetime
    is_best_validation: bool = False
    is_latest: bool = False


class TrainingJobResponse(BaseModel):
    """Trạng thái và kết quả có thể khôi phục của một training job."""

    id: str
    dataset_id: str
    name: str
    status: JobStatus
    config: TrainingJobRequest
    output_dir: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    epoch: int = Field(default=0, ge=0)
    train_loss: float | None = Field(default=None, ge=0.0)
    validation_loss: float | None = Field(default=None, ge=0.0)
    error: str | None = None
    checkpoints: list[TrainingCheckpointResponse] = Field(default_factory=list)


class EvaluationJobRequest(BaseModel):
    """Cấu hình rollout một checkpoint trong simulator ở chế độ headless."""

    training_run_id: str = Field(..., min_length=1, max_length=64)
    checkpoint_id: str = Field(..., min_length=1, max_length=255)
    num_rollouts: int = Field(default=20, ge=1, le=200)
    horizon: int | None = Field(default=None, ge=1, le=20_000)
    seed: int = Field(default=5000, ge=0, le=2_147_483_647)
    record_videos: int = Field(default=3, ge=0, le=20)

    @model_validator(mode="after")
    def video_count_fits_rollouts(self) -> "EvaluationJobRequest":
        if self.record_videos > self.num_rollouts:
            raise ValueError("record_videos không được lớn hơn num_rollouts")
        return self


class EvaluationEpisodeResponse(BaseModel):
    seed: int
    success: bool
    steps: int = Field(..., ge=0)
    video: str | None = None


class EvalResultResponse(BaseModel):
    """Trạng thái và kết quả rollout policy trong simulator."""

    id: str
    training_run_id: str
    checkpoint_id: str
    task_name: str
    status: JobStatus
    num_episodes: int = Field(..., ge=1)
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_episode_length: float | None = Field(default=None, ge=0.0)
    episodes: list[EvaluationEpisodeResponse] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class PolicyResponse(BaseModel):
    """Một policy đã huấn luyện xong."""

    id: str
    dataset_id: str
    checkpoint_path: str
    success_rate: float | None = None
