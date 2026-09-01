"""Nạp một gói chuyển giao training run vào máy này.

Gói được dựng trên máy khác nên mọi đường dẫn bên trong `job.json` trỏ tới ổ
đĩa của máy đó. Script chép hiện vật vào đúng chỗ máy này dùng, rồi viết lại ba
đường dẫn: `dataset_path`, `output_dir`, và bản ghi dataset trong cơ sở dữ liệu.

Danh sách checkpoint trong `job.json` không được giữ: backend quét lại thư mục
đầu ra mỗi lần đọc một run (`discover_checkpoints`), nên 35 mục của máy cũ —
trong đó 33 mục không có file đi kèm — sẽ tự rụng còn đúng những file thật sự
được chép sang.

Chạy:
    python -m scripts.import_transfer_bundle <thư mục gói> [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console Windows mặc định cp1252, không in nổi tiếng Việt và ném
# UnicodeEncodeError giữa chừng — sau khi đã chép xong vài file.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import get_settings
from src.models.db import Dataset, DatasetStatus, get_engine, session_factory
from src.services import storage

#: Tên hai checkpoint trong gói, và chỗ chúng phải nằm dưới `output_dir`.
#: Đường dẫn tương đối phải khớp `filename` mà `discover_checkpoints` sinh ra,
#: vì tên file là nơi epoch và validation loss được đọc ra.
CHECKPOINTS = {
    "checkpoint_epoch_180_last.pth": "lift-v9-rnn/20260831112320/last.pth",
    "checkpoint_epoch_174_best_validation.pth": (
        "lift-v9-rnn/20260831112320/models/"
        "model_epoch_174_best_validation_0.006789102731272578.pth"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(bundle: Path) -> None:
    """Đối chiếu với SHA256SUMS.txt trước khi chép bất cứ thứ gì."""

    sums = bundle / "SHA256SUMS.txt"
    if not sums.is_file():
        raise SystemExit(f"Thiếu {sums}")
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, name = line.split(maxsplit=1)
        path = bundle / name.strip()
        if not path.is_file():
            raise SystemExit(f"Gói thiếu file: {name.strip()}")
        actual = _sha256(path)
        if actual != expected:
            raise SystemExit(f"Sai vân tay: {name.strip()}\n  cho: {expected}\n  thật: {actual}")
        print(f"  OK {name.strip()}")


async def _upsert_dataset(dataset_id: str, name: str, size: int, episodes: int) -> str:
    """Dựng bản ghi dataset để trang Training thấy nó ở trạng thái ready."""

    async with session_factory(get_engine())() as session:
        existing = await session.get(Dataset, dataset_id)
        if existing is not None:
            existing.status = DatasetStatus.READY
            existing.size_bytes = size
            await session.commit()
            return "cập nhật"
        session.add(
            Dataset(
                id=dataset_id,
                name=name,
                task_names=["lift"],
                status=DatasetStatus.READY,
                size_bytes=size,
                num_episodes=episodes,
                data_source="transfer",
                exporter_version="1.0",
            ),
        )
        await session.commit()
        return "tạo mới"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không ghi gì")
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    if not bundle.is_dir():
        raise SystemExit(f"Không phải thư mục: {bundle}")

    print(f"Gói: {bundle}")
    print("Kiểm tra vân tay:")
    _verify(bundle)

    job = json.loads((bundle / "job.json").read_text(encoding="utf-8"))
    job_id = job["id"]
    dataset_id = job["dataset_id"]

    settings = get_settings()
    training_root = Path(settings.storage_dir) / "training"
    output_dir = training_root / job_id / "output"
    dataset_target = storage.dataset_hdf5_path(dataset_id)

    print()
    print(f"  run:        {job['name']} ({job_id})")
    print(f"  dataset:    {dataset_id}")
    print(f"  dataset ->  {dataset_target}")
    print(f"  output  ->  {output_dir}")

    if args.dry_run:
        print("\n--dry-run: dừng ở đây, không ghi gì.")
        return

    print("\nChép hiện vật:")
    dataset_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle / "dataset.hdf5", dataset_target)
    print(f"  dataset.hdf5 -> {dataset_target.name}")

    for source_name, relative in CHECKPOINTS.items():
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / source_name, target)
        print(f"  {source_name} -> {relative}")

    config_target = output_dir / "config.json"
    shutil.copyfile(bundle / "config.json", config_target)

    # Viết lại bản ghi run cho máy này. `checkpoints` để rỗng: backend quét lại
    # thư mục đầu ra và điền, nên giữ danh sách của máy cũ chỉ tạo ra 33 mục trỏ
    # vào những file không tồn tại ở đây.
    job["dataset_path"] = str(dataset_target)
    job["output_dir"] = str(output_dir)
    job["checkpoints"] = []
    job["config"] = {**job["config"], "device": "cpu"}

    job_dir = training_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"  job.json -> {job_dir / 'job.json'}")

    action = asyncio.run(
        _upsert_dataset(
            dataset_id,
            f"{job['name']}-transfer",
            dataset_target.stat().st_size,
            len(job.get("config", {}).get("episode_inventory", [])) or 0,
        ),
    )
    print(f"  bản ghi dataset: {action}")

    print("\nXong. Khởi động lại backend để nó đọc run mới.")


if __name__ == "__main__":
    main()
