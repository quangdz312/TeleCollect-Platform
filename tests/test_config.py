from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings


def _settings(**overrides) -> Settings:
    """Settings không đọc .env — test phải độc lập với máy đang chạy."""
    return Settings(_env_file=None, **overrides)


def test_ensure_data_dirs_creates_storage_and_sqlite_dir(tmp_path: Path):
    settings = _settings(
        storage_dir=str(tmp_path / "episodes"),
        database_url=f"sqlite+aiosqlite:///{tmp_path}/db/app.db",
    )

    created = settings.ensure_data_dirs()

    assert (tmp_path / "episodes").is_dir()
    assert (tmp_path / "db").is_dir()
    assert set(created) == {tmp_path / "episodes", tmp_path / "db"}


def test_ensure_data_dirs_is_idempotent(tmp_path: Path):
    settings = _settings(storage_dir=str(tmp_path / "episodes"))

    settings.ensure_data_dirs()
    settings.ensure_data_dirs()

    assert (tmp_path / "episodes").is_dir()


def test_real_settings_load_and_database_driver_is_installed():
    """Smoke test: `Settings()` không truyền `_env_file=None` đọc `.env` THẬT
    (giống hệt cách `src.config.get_settings()` chạy lúc server khởi động).

    Toàn bộ 160+ test khác dùng fixture `test_engine` với DB URL hardcode
    trong `conftest.py`, không bao giờ đụng tới `database_url` từ `.env` —
    đó là lý do cả bộ test pass trong khi server thật chết vì driver DB
    (vd asyncpg cho Postgres) chưa được cài. Test này bắt lỗi kiểu đó: tạo
    engine thật từ `settings.database_url` — sẽ raise ModuleNotFoundError
    ngay lập tức (trước khi connect) nếu thiếu driver.
    """
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    engine.sync_engine.dispose()


def test_data_dirs_skips_non_sqlite_database(tmp_path: Path):
    """CSDL qua mạng không có thư mục để tạo."""
    settings = _settings(
        storage_dir=str(tmp_path / "episodes"),
        database_url="postgresql+asyncpg://user:pw@localhost:5432/telecollect",
    )

    assert settings.data_dirs() == [tmp_path / "episodes"]
