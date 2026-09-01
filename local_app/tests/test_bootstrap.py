"""Nạp workspace vào SQLite của app."""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from local_app.bootstrap import index_workspace


def _batch(root: Path, batch_id: str, name: str, task: str) -> Path:
    folder = root / "batches" / name
    (folder / "episodes").mkdir(parents=True)
    (folder / "batch.json").write_text(
        json.dumps({"format_version": 1, "id": batch_id, "name": name, "task": task}),
        encoding="utf-8",
    )
    return folder


@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    """Engine riêng cho mỗi test.

    `get_engine` cache theo tiến trình và đọc `get_settings()`, nên đặt biến môi
    trường là vô ích — phải vá thẳng vào settings rồi xoá cache, đúng cách
    `tests/conftest.py` cô lập storage_dir.
    """

    from src.config import get_settings
    from src.models.db import get_engine, init_db

    monkeypatch.setattr(
        get_settings(), "database_url", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
    )
    get_engine.cache_clear()
    instance = get_engine()
    await init_db(instance)
    yield instance
    await instance.dispose()
    get_engine.cache_clear()


@pytest.mark.asyncio
async def test_a_batch_folder_becomes_a_named_record(tmp_path, engine) -> None:
    """Tên và task đến từ `batch.json`, không phải từ mã đợt thu.

    Thiếu bản ghi thì trang Review hiện chính mã ở chỗ đáng lẽ là tên — người
    dùng thấy `can-73d85272` trong khi màn hình Collect hiện `can` — và
    `task_name` rỗng khiến hàng rào chặn nạp khác task lặng lẽ ngừng hoạt động.
    """

    from src.models.db import CollectionBatch, session_factory

    _batch(tmp_path, "can-73d85272", "can", "pick_place_can")
    await index_workspace(tmp_path, "operator-1")

    async with session_factory(engine)() as session:
        record = await session.get(CollectionBatch, "can-73d85272")

    assert record is not None
    assert record.name == "can"
    assert record.task_name == "pick_place_can"


@pytest.mark.asyncio
async def test_a_renamed_batch_updates_its_record(tmp_path, engine) -> None:
    """`batch.json` trên đĩa là bản gốc: đổi tên trong app phải thấy ở Review."""

    from src.models.db import CollectionBatch, session_factory

    folder = _batch(tmp_path, "can-73d85272", "can", "pick_place_can")
    await index_workspace(tmp_path, "operator-1")

    (folder / "batch.json").write_text(
        json.dumps({"id": "can-73d85272", "name": "can round 2", "task": "pick_place_can"}),
        encoding="utf-8",
    )
    await index_workspace(tmp_path, "operator-1")

    async with session_factory(engine)() as session:
        record = await session.get(CollectionBatch, "can-73d85272")

    assert record is not None
    assert record.name == "can round 2"
