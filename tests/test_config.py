from pathlib import Path

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


def test_data_dirs_skips_non_sqlite_database(tmp_path: Path):
    """CSDL qua mạng không có thư mục để tạo."""
    settings = _settings(
        storage_dir=str(tmp_path / "episodes"),
        database_url="postgresql+asyncpg://user:pw@localhost:5432/telecollect",
    )

    assert settings.data_dirs() == [tmp_path / "episodes"]
