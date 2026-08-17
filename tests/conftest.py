import os
import shutil
import subprocess

# `Settings` cache qua `lru_cache` và `src.main` đọc `jwt_secret` ngay lúc
# import module (dòng `settings = get_settings()` ở top-level) — biến môi
# trường phải được set TRƯỚC import đó, nếu không mọi test ký JWT sẽ vỡ vì
# key rỗng (đúng ý đồ: production bắt buộc tự đặt, không có default ngầm).
os.environ.setdefault("JWT_SECRET", "test-secret-key-do-not-use-in-prod")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import get_settings
from src.main import app
from src.models.db import enable_sqlite_foreign_keys, get_session, init_db, session_factory


@pytest_asyncio.fixture
async def test_engine() -> AsyncEngine:
    """SQLite in-memory riêng cho mỗi test — StaticPool giữ 1 connection duy nhất
    để dữ liệu :memory: không biến mất giữa các session/request."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_foreign_keys(engine)
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine):
    """Session thô để test tự chèn dữ liệu (vd: tạo sẵn user role admin/reviewer)
    mà không phải đi qua API."""
    factory = session_factory(test_engine)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_engine: AsyncEngine):
    """Async HTTP client chạy trên app thật, DB được override sang `test_engine`."""
    factory = session_factory(test_engine)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    """Cô lập `settings.storage_dir` về thư mục tạm riêng của test — không
    đụng tới `./data/episodes` thật, không rò rỉ file giữa các test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="session")
def sample_mp4_bytes(tmp_path_factory) -> bytes:
    """Video mp4 thật (2s, testsrc) sinh 1 lần bằng ffmpeg, dùng lại cho mọi
    test upload — tránh gọi ffmpeg lặp lại tốn thời gian."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not found on PATH - install ffmpeg to run this test")
    directory = tmp_path_factory.mktemp("fixtures")
    path = directory / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=10",
            "-t",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path.read_bytes()
