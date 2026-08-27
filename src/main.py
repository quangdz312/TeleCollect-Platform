import asyncio
import contextlib
import logging
import shutil
from contextlib import asynccontextmanager

# Imported for its side effect, before anything can create an OpenGL context:
# it picks the discrete GPU, which is worth ~20x on offscreen rendering.
import src.sim.gpu as gpu  # noqa: F401  isort:skip
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.services import quota
from src.config import get_settings
from src.core.session import get_session_manager
from src.models.db import init_db

logger = logging.getLogger(__name__)
_REAP_INTERVAL_S = 1.0


async def _reaper_task() -> None:
    """Dọn phiên mất controller và chốt bản ghi dở."""
    manager = get_session_manager()
    while True:
        await asyncio.sleep(_REAP_INTERVAL_S)
        try:
            await asyncio.to_thread(manager.reap_stale)
        except Exception as exc:
            logger.warning("teleop reaper ignored error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_data_dirs()
    await init_db()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        logger.warning(
            "ffmpeg/ffprobe not found on PATH - video upload, thumbnail and "
            "playback features will fail. Install ffmpeg "
            "(e.g. `apt-get install ffmpeg` or `choco install ffmpeg`) and "
            "restart the app."
        )
    gpu.log_active_renderer()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    reaper = asyncio.create_task(_reaper_task())
    try:
        yield
    finally:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
        await asyncio.to_thread(get_session_manager().close_all)
        print("Shutting down...")


app = FastAPI(
    title="TeleCollect",
    description="Nền tảng teleoperation & thu thập demonstration cho imitation learning",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    """Liveness, kèm mức sử dụng kho dữ liệu.

    Hạn mức nằm ở đây thay vì một endpoint riêng vì đây là thứ giám sát vốn đã
    hỏi định kỳ — biết đĩa sắp đầy TRƯỚC khi nó đầy mới có ích, lúc đầy rồi thì
    Postgres đã không ghi được nữa.
    """

    # Không `refresh=True`: health bị hỏi liên tục, quét lại cây thư mục mỗi
    # lần sẽ tự nó thành gánh nặng.
    disk = await asyncio.to_thread(quota.status)
    payload: dict[str, object] = {
        "status": "ok",
        "env": settings.app_env,
        "storage_used_bytes": disk.used_bytes,
    }
    if disk.enabled:
        payload["storage_limit_bytes"] = disk.limit_bytes
        payload["storage_percent_used"] = disk.percent_used
    return payload
