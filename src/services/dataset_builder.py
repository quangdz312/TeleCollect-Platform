"""Đóng gói demo `approved` thành file zip dataset — chạy nền (BackgroundTasks).

Trách nhiệm: nơi duy nhất ghi file zip dataset. Gọi từ `POST /api/v1/datasets`
SAU KHI response 202 đã trả (endpoint chỉ ghi snapshot `dataset_episodes`
đồng bộ, việc zip hoá nặng IO/CPU chạy nền ở đây).

Ba bẫy bắt buộc tránh (xem trao đổi trước khi viết module này):
  1. Session của request đã đóng khi response trả về — background task PHẢI
     tự mở session mới qua `session_factory`, không giữ tham chiếu session cũ.
  2. SQLite khoá file khi có transaction ghi mở — zip xong HẲN (đóng handle)
     rồi mới mở session cập nhật status, không giữ session mở suốt lúc zip.
  3. Zip là tác vụ đồng bộ nặng — chạy qua `asyncio.to_thread`, không chặn
     event loop (cùng lý do đã sửa ở `src/services/media.py`: subprocess bất
     đồng bộ trên Windows từng nổ `NotImplementedError`, ở đây tác vụ CPU/IO
     đồng bộ trong event loop sẽ treo mọi request khác đang chờ).

Cấu trúc file zip (ZIP_STORED — video/ảnh đã nén sẵn, nén lại tốn CPU vô ích):
    <name>/meta.json
    <name>/episodes/<episode_id>/front.mp4
    <name>/episodes/<episode_id>/wrist.mp4        (chỉ khi demo có)
    <name>/episodes/<episode_id>/trajectory.json  (chỉ khi demo có)
    <name>/episodes/<episode_id>/meta.json

Episode thiếu file nguồn trên đĩa (bị xoá tay, hỏng...) bị BỎ QUA — không
làm fail cả dataset — và được ghi vào mảng `warnings` trong `meta.json` gốc.
Chỉ khi KHÔNG episode nào build được thì dataset mới chuyển `status=failed`.
"""

import asyncio
import hashlib
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from src.models.db import Dataset, DatasetEpisode, Episode
from src.models.enums import DatasetStatus
from src.services import storage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class _EpisodeSnapshot:
    """Chỉ giữ giá trị cột đơn giản (không phải object ORM) — an toàn dùng
    sau khi session đã đóng, và an toàn truyền qua `asyncio.to_thread`."""

    id: str
    task_name: str
    outcome: str | None
    note: str
    trim_start_s: float | None
    trim_end_s: float | None
    fps: float | None
    duration_s: float | None
    num_frames: int | None
    operator: str
    reviewer: str | None
    created_at: datetime
    has_wrist: bool
    has_trajectory: bool


def _sha256_file(path: Path) -> str:
    """Đọc theo chunk 1MB — không load cả file (video) vào RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _episode_meta_dict(ep: _EpisodeSnapshot) -> dict:
    return {
        "task_name": ep.task_name,
        "outcome": ep.outcome,
        "note": ep.note,
        "trim_start_s": ep.trim_start_s,
        "trim_end_s": ep.trim_end_s,
        "fps": ep.fps,
        "duration_s": ep.duration_s,
        "num_frames": ep.num_frames,
        "operator": ep.operator,
        "reviewer": ep.reviewer,
        "created_at": ep.created_at.isoformat(),
    }


def _build_zip_sync(
    zip_path: Path,
    name: str,
    task_names: list[str],
    include_failures: bool,
    episodes: list[_EpisodeSnapshot],
) -> tuple[int, int, list[str]]:
    """Chạy ĐỒNG BỘ trong thread pool (gọi qua `asyncio.to_thread`).

    Trả `(num_episodes_built, total_frames, warnings)`. Ghi ra file `.tmp`
    trước rồi `replace()` sang tên thật khi zip đã đóng hẳn — tránh để lại
    zip dở dang nếu tiến trình chết giữa chừng.
    """
    warnings: list[str] = []
    episode_metas: list[dict] = []
    total_frames = 0

    tmp_path = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for ep in episodes:
            ep_dir = storage.episode_dir(ep.id)
            front_path = ep_dir / storage.FRONT_FILENAME
            if not front_path.exists():
                warnings.append(f"episode {ep.id}: thiếu {storage.FRONT_FILENAME}, đã bỏ qua")
                continue

            files_to_add: list[tuple[Path, str]] = [(front_path, storage.FRONT_FILENAME)]

            if ep.has_wrist:
                wrist_path = ep_dir / storage.WRIST_FILENAME
                if wrist_path.exists():
                    files_to_add.append((wrist_path, storage.WRIST_FILENAME))
                else:
                    warnings.append(f"episode {ep.id}: thiếu {storage.WRIST_FILENAME}, đã bỏ qua file này")

            if ep.has_trajectory:
                trajectory_path = ep_dir / storage.TRAJECTORY_FILENAME
                if trajectory_path.exists():
                    files_to_add.append((trajectory_path, storage.TRAJECTORY_FILENAME))
                else:
                    warnings.append(
                        f"episode {ep.id}: thiếu {storage.TRAJECTORY_FILENAME}, đã bỏ qua file này"
                    )

            arc_prefix = f"{name}/episodes/{ep.id}"
            file_hashes: dict[str, str] = {}
            for src_path, filename in files_to_add:
                zf.write(src_path, f"{arc_prefix}/{filename}")
                file_hashes[filename] = _sha256_file(src_path)

            episode_meta = _episode_meta_dict(ep)
            zf.writestr(f"{arc_prefix}/meta.json", json.dumps(episode_meta, indent=2, ensure_ascii=False))

            episode_metas.append({"episode_id": ep.id, "files": file_hashes, **episode_meta})
            total_frames += ep.num_frames or 0

        if episode_metas:
            dataset_meta = {
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.now(UTC).isoformat(),
                "format": "raw",
                "name": name,
                "task_names": task_names,
                "include_failures": include_failures,
                "num_episodes": len(episode_metas),
                "episodes": episode_metas,
                "warnings": warnings,
                "note": "Snapshot dữ liệu tại thời điểm tạo dataset — không cập nhật khi demo nguồn đổi sau đó.",
            }
            zf.writestr(f"{name}/meta.json", json.dumps(dataset_meta, indent=2, ensure_ascii=False))

    if not episode_metas:
        tmp_path.unlink(missing_ok=True)
        return 0, 0, warnings

    tmp_path.replace(zip_path)  # zip đã đóng handle (ra khỏi `with`) — an toàn rename trên Windows.
    return len(episode_metas), total_frames, warnings


async def build_dataset(dataset_id: str, session_factory: async_sessionmaker) -> None:
    """Entry point gọi từ `BackgroundTasks` sau khi `POST /datasets` trả 202.

    KHÔNG nhận `AsyncSession` — tự mở session riêng qua `session_factory`,
    vì session của request đã đóng lúc response trả về client.
    """
    async with session_factory() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            logger.error("build_dataset: dataset %s không tồn tại", dataset_id)
            return

        name = dataset.name
        task_names = dataset.task_names
        include_failures = dataset.include_failures

        result = await session.execute(
            select(Episode)
            .join(DatasetEpisode, DatasetEpisode.episode_id == Episode.id)
            .where(DatasetEpisode.dataset_id == dataset_id)
            .options(selectinload(Episode.operator), selectinload(Episode.reviewer))
        )
        episodes = list(result.scalars().all())
        snapshots = [
            _EpisodeSnapshot(
                id=ep.id,
                task_name=ep.task_name,
                outcome=ep.outcome,  # cột String — đã là str thô, không phải enum member
                note=ep.note,
                trim_start_s=ep.trim_start_s,
                trim_end_s=ep.trim_end_s,
                fps=ep.fps,
                duration_s=ep.duration_s,
                num_frames=ep.num_frames,
                operator=ep.operator.username,
                reviewer=ep.reviewer.username if ep.reviewer is not None else None,
                created_at=ep.created_at,
                has_wrist=ep.has_wrist,
                has_trajectory=ep.has_trajectory,
            )
            for ep in episodes
        ]
    # Session đóng ở đây — zip hoá bên dưới KHÔNG giữ session mở (SQLite khoá
    # file khi có transaction ghi đang mở).

    zip_path = storage.dataset_zip_path(dataset_id)

    try:
        num_built, total_frames, warnings = await asyncio.to_thread(
            _build_zip_sync, zip_path, name, task_names, include_failures, snapshots
        )
    except Exception as exc:  # noqa: BLE001 — build nền, phải bắt mọi lỗi để cập nhật status=failed
        logger.exception("Build dataset %s thất bại", dataset_id)
        async with session_factory() as session:
            dataset = await session.get(Dataset, dataset_id)
            if dataset is not None:
                dataset.status = DatasetStatus.FAILED
                dataset.error_message = str(exc)[:2000]
                await session.commit()
        return

    if warnings:
        logger.warning("Build dataset %s có %d warning: %s", dataset_id, len(warnings), warnings)

    async with session_factory() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            return
        if num_built == 0:
            dataset.status = DatasetStatus.FAILED
            dataset.error_message = "Không episode nào build được (thiếu toàn bộ file nguồn front.mp4)"
        else:
            dataset.status = DatasetStatus.READY
            dataset.num_episodes = num_built
            dataset.num_frames = total_frames
            dataset.size_bytes = zip_path.stat().st_size
            dataset.zip_path = str(zip_path)
        await session.commit()
