from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Storage — artifact nặng của episode (video quan sát, parquet action/state)
    storage_dir: str = "./data/episodes"

    # Teleop
    control_hz: int = Field(default=30, ge=1, le=1000)
    """Tần số vòng điều khiển (Hz) — chu kỳ gửi action và lấy mẫu observation."""

    max_concurrent_sessions: int = Field(default=4, ge=1, le=64)
    """Số phiên teleop chạy đồng thời tối đa; mỗi phiên chiếm một instance sim."""

    # Security
    jwt_secret: str = ""
    """Khoá ký JWT cho hai vai trò operator / reviewer. Bắt buộc đặt ở production."""

    # Data privacy — ràng buộc: ẩn danh khuôn mặt nếu bản ghi có hình ảnh người
    enable_face_anonymization: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
