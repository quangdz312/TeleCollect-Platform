"""Nạp gói dữ liệu do `scripts.pack_full_data` tạo vào máy chủ này.

Chạy trong container backend, sau khi gói đã nằm trên đĩa máy chủ:

    python -m scripts.load_full_data /tmp/telecollect-full-data.tar

Bốn việc, theo thứ tự:

1. Bung gói vào `STORAGE_DIR`: `review/` thay hẳn, ba thư mục còn lại thêm vào
   cạnh những gì máy chủ đang có — lần huấn luyện cũ của máy chủ giữ nguyên.
2. Viết lại `dataset_path` và `output_dir` trong từng `job.json` — chúng đang
   trỏ vào ổ đĩa của máy đã huấn luyện, và `owner_id` trỏ vào một tài khoản
   không có ở đây.
3. Tạo dòng cho mỗi đợt thu trong `collection_batches`, và đóng dấu đợt thu cho
   những tập chưa có — chúng thu từ trước khi hệ thống có khái niệm đợt thu.
4. Đăng ký các dataset đã đóng gói vào bảng `datasets`.

Không đụng tới tài khoản: máy chủ giữ nguyên người dùng của nó, mọi thứ nạp
vào đều gán cho một quản trị viên đang có.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from src.config import get_settings
from src.models.db import (
    CollectionBatch, Dataset, User, get_engine, init_db, session_factory,
)
from src.models.enums import DatasetStatus, UserRole

#: Đợt thu gán cho tập không có dấu, theo nhiệm vụ ghi trong chính bản ghi.
EARLY_BATCHES = {
    "lift": "lift-early",
    "can": "can-early",
    "square": "square-early",
    "tool_hang": "toolhang-early",
}

TASK_CANONICAL = {
    "lift": "lift_cube",
    "can": "pick_place_can",
    "square": "nut_assembly_square",
    "tool_hang": "tool_hang",
}


def _extract(archive: Path, storage: Path) -> dict[str, Any]:
    """Bung gói, thay thế bốn thư mục dữ liệu.

    Thay chứ không trộn: gói mang theo `scores.jsonl` và `labels.jsonl` của
    riêng nó, mà hai tệp đó là toàn bộ sự thật về kho review — ghép chúng với
    bản đang có sẽ đụng số thứ tự tên tập và sinh ra bản ghi trỏ vào tệp không
    tồn tại.
    """
    print(f"Bung {archive.name} ...")
    staging = storage / ".incoming"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r") as handle:
        handle.extractall(staging, filter="data")

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    arrived: set[str] = set()

    # `review/` thay hẳn: `scores.jsonl` và `labels.jsonl` là toàn bộ sự thật
    # về kho review, ghép hai bản sẽ đụng số thứ tự tên tập và sinh ra bản ghi
    # trỏ vào tệp không có. Ba thư mục còn lại thì mỗi mục là một thư mục hoặc
    # tệp độc lập, nên thêm vào cạnh những gì máy chủ đang có.
    review = staging / "review"
    if review.is_dir():
        target = storage / "review"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(review), str(target))
        print("  review/ (thay hẳn)")

    for name in ("datasets", "episodes", "training"):
        incoming = staging / name
        if not incoming.is_dir():
            continue
        target = storage / name
        target.mkdir(parents=True, exist_ok=True)
        added = skipped = 0
        for item in sorted(incoming.iterdir()):
            destination = target / item.name
            if destination.exists():
                skipped += 1
                continue
            shutil.move(str(item), str(destination))
            added += 1
            if name == "training":
                arrived.add(item.name)
        note = f", giữ nguyên {skipped} mục đã có" if skipped else ""
        print(f"  {name}/ thêm {added} mục{note}")

    shutil.rmtree(staging)
    # Chỉ những lần chạy vừa nạp mới cần viết lại đường dẫn: lần chạy sẵn có
    # của máy chủ đã trỏ đúng chỗ rồi, đụng vào chỉ tổ hỏng.
    manifest["_arrived_runs"] = sorted(arrived)
    return manifest


def _rewrite_runs(storage: Path, owner_id: str, only: set[str]) -> int:
    """Trỏ lại mọi đường dẫn trong `job.json` về máy này.

    `checkpoints` để rỗng: `discover_checkpoints` quét lại thư mục đầu ra mỗi
    lần đọc một run và suy ra epoch, validation loss cùng cờ best/latest từ tên
    tệp — giữ lại danh sách của máy cũ chỉ tổ trỏ vào những tệp gói này đã bỏ.
    """
    training = storage / "training"
    if not training.is_dir():
        return 0
    count = 0
    for path in sorted(training.glob("*/job.json")):
        run = path.parent
        if run.name not in only:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record["output_dir"] = str(run / "output")
        dataset_id = record.get("dataset_id")
        if dataset_id:
            record["dataset_path"] = str(storage / "datasets" / f"{dataset_id}.hdf5")
        record["owner_id"] = owner_id
        record["checkpoints"] = []
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        count += 1
        print(f"  {record.get('name', run.name)}")
    return count


def _stamp_batches(storage: Path) -> dict[str, str]:
    """Đóng dấu đợt thu cho tập chưa có, trả về {mã đợt thu: nhiệm vụ}.

    Tập thu từ trước khi có khái niệm đợt thu không mang dấu nào, nên trang
    Review dồn hết vào nhóm `legacy`. Gán theo nhiệm vụ — một đợt thu là một
    nhiệm vụ, nên gom tất cả vào một đợt thu chung sẽ cho ra thứ không đóng gói
    dataset được.
    """
    scores = storage / "review" / "scores.jsonl"
    if not scores.exists():
        return {}
    lines = [l for l in scores.read_text(encoding="utf-8").splitlines() if l.strip()]
    batches: dict[str, str] = {}
    stamped = 0
    output = []
    for line in lines:
        record = json.loads(line)
        task = str(record.get("task") or "")
        provenance = record.setdefault("provenance", {})
        batch = provenance.get("collection_batch_id")
        if not batch and task in EARLY_BATCHES:
            batch = EARLY_BATCHES[task]
            provenance["collection_batch_id"] = batch
            stamped += 1
        if batch:
            batches.setdefault(str(batch), TASK_CANONICAL.get(task, task))
        output.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    scores.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"  đóng dấu {stamped} tập chưa thuộc đợt thu nào")
    return batches


async def _register(storage: Path, batches: dict[str, str]) -> str:
    engine = get_engine()
    await init_db(engine)
    async with session_factory(engine)() as session:
        admin = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        if admin is None:
            raise SystemExit("Máy chủ chưa có tài khoản quản trị nào để gán dữ liệu")

        created = 0
        for batch_id, task in sorted(batches.items()):
            if await session.get(CollectionBatch, batch_id) is not None:
                continue
            session.add(CollectionBatch(
                id=batch_id, name=batch_id, task_name=task or None,
                description="", created_by=admin.id,
            ))
            created += 1
        print(f"  {created} đợt thu mới, {len(batches) - created} đã có")

        # Dataset: bảng cần một dòng cho mỗi tệp, nếu không trang Training
        # không có gì để chọn dù tệp nằm sẵn trên đĩa.
        registered = 0
        for path in sorted((storage / "datasets").glob("*.hdf5")):
            dataset_id = path.stem
            if await session.get(Dataset, dataset_id) is not None:
                continue
            session.add(Dataset(
                id=dataset_id, name=dataset_id, task_names=[],
                status=DatasetStatus.READY, zip_path=str(path),
                size_bytes=path.stat().st_size, data_source="imported",
                created_by=admin.username,
            ))
            registered += 1
        print(f"  {registered} dataset đăng ký mới")
        await session.commit()
        return admin.id


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    archive = Path(sys.argv[1]).resolve()
    if not archive.is_file():
        raise SystemExit(f"Không có tệp: {archive}")

    storage = Path(get_settings().storage_dir).resolve()
    print(f"Kho: {storage}\n")

    manifest = _extract(archive, storage)

    print("\nĐóng dấu đợt thu:")
    batches = _stamp_batches(storage)
    for entry in manifest.get("batches", []):
        batches.setdefault(entry["id"], entry.get("task", ""))

    print("\nĐăng ký vào cơ sở dữ liệu:")
    owner_id = asyncio.run(_register(storage, batches))

    print("\nViết lại đường dẫn các lần huấn luyện:")
    count = _rewrite_runs(storage, owner_id, set(manifest.get("_arrived_runs", [])))
    print(f"  {count} lần chạy")

    print("\nXong. Khởi động lại backend để nạp các lần huấn luyện:")
    print("  docker compose --env-file /srv/telecollect/secrets/.env.production \\")
    print("    -f docker-compose.prod.yml restart backend")


if __name__ == "__main__":
    main()
