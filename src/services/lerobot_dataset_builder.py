"""Dựng một dataset LeRobot v3 từ các episode teleop đã duyệt — chạy nền.

Song song với `robomimic_dataset_builder`, khác ở ba điểm:

  1. **Kết quả là một THƯ MỤC, không phải một file.** `size_bytes` phải duyệt
     cây thay vì `stat()`, và dọn dẹp phải `rmtree` thay vì `unlink`.
  2. **Có chạy ffmpeg cho từng video**, nên nặng và lâu hơn hẳn — bắt buộc
     qua `asyncio.to_thread`, nếu không mọi request khác đều treo theo.
  3. **Chỉ nhận teleop.** Episode scripted nằm trong HDF5 nhiều demo một file
     nên không đi qua đường này; writer sẽ báo rõ khi không có gì để xuất.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.export.lerobot import write_lerobot_dataset
from src.models.db import Dataset
from src.models.enums import DatasetStatus
from src.services import storage


async def build_lerobot_dataset(
    dataset_id: str,
    output: Path,
    episodes: Sequence[dict[str, object]],
    factory: async_sessionmaker,
) -> None:
    try:
        # Blocking: đọc parquet và chạy ffmpeg cho từng episode.
        count, frames = await asyncio.to_thread(
            write_lerobot_dataset, output, list(episodes),
        )
        status = DatasetStatus.READY
        error = None
    except Exception as exc:  # background job must persist its failure
        count, frames = 0, 0
        status = DatasetStatus.FAILED
        error = str(exc)[:2000]
    async with factory() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            shutil.rmtree(output, ignore_errors=True)
            return
        dataset.status = status
        dataset.error_message = error
        if status == DatasetStatus.READY:
            dataset.num_episodes = count
            dataset.num_frames = frames
            dataset.size_bytes = storage.dir_size_bytes(output)
            dataset.zip_path = str(output)
        await session.commit()
