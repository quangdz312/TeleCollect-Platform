from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "TeleCollect"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # Database — metadata episode, nhãn demo, tài khoản/vai trò.
    # Driver bất đồng bộ (aiosqlite): backend chạy trên event loop của FastAPI,
    # driver đồng bộ sẽ chặn loop và làm vòng điều khiển 30 Hz trượt nhịp.
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    # Storage — artifact nặng của episode (video quan sát, parquet action/state).
    # Cấu trúc con cố định: <storage_dir>/episodes/<id>/... và <storage_dir>/tmp/<id>/
    # (thư mục tạm lúc validate upload, xem src/services/storage.py).
    storage_dir: str = "./data"

    max_upload_mb: int = Field(default=200, ge=1)
    """Giới hạn dung lượng mỗi file upload (front/wrist/trajectory), tính bằng MB."""

    review_dir: str = "./data/review"
    """Workspace cho scripted collection, scoring và nhãn người chấm."""

    # Auto-label rule thresholds. Defaults mirror robosuite success checks where available.
    rule_stable_tail_frames: int = Field(default=10, ge=1)
    rule_lift_height_m: float = Field(default=0.04, ge=0.0)
    rule_grasp_radius_m: float = Field(default=0.08, ge=0.0)
    rule_release_distance_m: float = Field(default=0.6, ge=0.0)
    rule_can_tail_speed_mps: float = Field(default=0.01, ge=0.0)
    rule_can_divider_clearance_m: float = Field(default=0.02, ge=0.0)
    rule_square_xy_tolerance_m: float = Field(default=0.03, ge=0.0)
    rule_square_height_clearance_m: float = Field(default=0.05, ge=0.0)
    rule_square_angle_tolerance_deg: float = Field(default=15.0, ge=0.0)

    # Teleop
    control_hz: int = Field(default=30, ge=1, le=1000)
    """Tần số vòng điều khiển (Hz) — chu kỳ gửi action và lấy mẫu observation."""

    max_concurrent_sessions: int = Field(default=4, ge=1, le=64)
    """Số phiên teleop chạy đồng thời tối đa; mỗi phiên chiếm một instance sim."""

    max_record_steps: int = Field(default=0, ge=0)
    """Trần số bước một episode web; 0 = không giới hạn."""

    command_timeout_ms: int = Field(default=250, ge=10, le=5000)
    """Input cũ hơn ngưỡng này bị coi là stale và robot nhận neutral motion."""

    reconnect_grace_s: float = Field(default=10.0, ge=0.0)
    """Thời gian chờ reconnect trước khi chốt bản ghi dở thành partial episode."""

    session_idle_timeout_s: float = Field(default=300.0, ge=1.0)
    """Phiên idle không còn controller quá lâu sẽ bị background reaper đóng."""

    telemetry_hz: int = Field(default=15, ge=1, le=120)
    """Nhịp gửi observation/stats JSON về browser."""

    stream_fps: int = Field(default=15, ge=1, le=120)
    """Nhịp gửi frame JPEG preview về browser."""

    jpeg_quality: int = Field(default=75, ge=1, le=100)
    """Chất lượng JPEG của stream preview."""

    teleop_cameras: str = "agentview,robot0_eye_in_hand"
    """Camera ghi vào episode, phân tách bằng dấu phẩy."""

    preview_camera: str = "frontview"
    """Camera chính stream về browser."""

    preview_camera_secondary: str = "robot0_eye_in_hand"
    """Camera phụ stream về browser; để rỗng nếu không dùng."""

    preview_size: int = Field(default=384, ge=0, le=1024)
    """Độ phân giải camera preview chính; 0 = dùng lại ảnh ghi."""

    # Security
    jwt_secret: str = ""
    """Khoá ký JWT cho ba vai trò operator / reviewer / admin. Bắt buộc đặt ở production."""

    access_token_expire_minutes: int = Field(default=30, ge=1)
    refresh_token_expire_days: int = Field(default=7, ge=1)

    allow_self_register: bool = True
    """Bật/tắt POST /auth/register. Register luôn tạo role operator, không bao giờ tạo admin."""

    allow_self_review: bool = False
    """Cho phép reviewer tự duyệt (approve/reject) demo do chính mình upload.
    Mặc định tắt — bật khi cần demo bằng một tài khoản duy nhất."""

    # Data privacy — ràng buộc: ẩn danh khuôn mặt nếu bản ghi có hình ảnh người
    enable_face_anonymization: bool = True

    def data_dirs(self) -> list[Path]:
        """Những thư mục ứng dụng cần có sẵn để ghi được dữ liệu.

        Gồm `storage_dir` và — nếu CSDL là SQLite trên đĩa — thư mục chứa file
        .db. SQLite không tự tạo thư mục cha, còn CSDL qua mạng (PostgreSQL)
        thì không cần thư mục nào.
        """
        dirs = [Path(self.storage_dir)]
        db_path = make_url(self.database_url).database
        if self.database_url.startswith("sqlite") and db_path and db_path != ":memory:":
            dirs.append(Path(db_path).parent)
        return dirs

    def camera_list(self) -> list[str]:
        """Danh sách camera ghi vào episode."""
        return [name.strip() for name in self.teleop_cameras.split(",") if name.strip()]

    def ensure_data_dirs(self) -> list[Path]:
        """Tạo sẵn các thư mục ở `data_dirs()`. Idempotent, gọi lúc khởi động."""
        dirs = self.data_dirs()
        for path in dirs:
            path.mkdir(parents=True, exist_ok=True)
        return dirs


@lru_cache
def get_settings() -> Settings:
    return Settings()
