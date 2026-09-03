"""Đóng gói kho dữ liệu của một máy khác thành gói mang lên máy chủ.

Kho đầy đủ nặng 21 GB, mà 19 GB trong đó là checkpoint trung gian: mỗi lần
huấn luyện lưu lại vài chục bản, trong khi để xem lại kết quả chỉ cần vài bản
đáng kể. Gói này giữ tối đa ba bản mỗi lần chạy — xem `_keep_checkpoints` —
nên 567 tệp còn 35, và cả gói còn khoảng 2.9 GB thay vì 21 GB: vừa với máy chủ
30 GB đang dùng 1.6 GB.

Không đụng tới `app.db`: máy chủ có cơ sở dữ liệu riêng với tài khoản và dữ
liệu thật của nó. Phần ghép vào bảng do `scripts/load_full_data.py` làm ở đầu
kia, sau khi tệp đã nằm đúng chỗ.

Dùng:
    python -m scripts.pack_full_data <thư mục data> <tệp .tar đích>
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

#: Tập không thuộc đợt thu nào được gom theo nhiệm vụ. Một đợt thu là một
#: nhiệm vụ — trang Data diversity lọc theo nó, và một dataset trộn hai nhiệm
#: vụ là dataset hỏng, nên gom tất cả vào một đợt thu chung là không dùng được.
EARLY_BATCHES = {
    "lift": ("lift-early", "lift_cube"),
    "can": ("can-early", "pick_place_can"),
    "square": ("square-early", "nut_assembly_square"),
    "tool_hang": ("toolhang-early", "tool_hang"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _keep_checkpoints(run: Path) -> list[Path]:
    """Bản tốt nhất theo ba tiêu chí: tỉ lệ thành công, validation loss, epoch cuối.

    Tỉ lệ thành công phải có mặt và đứng trước: validation loss thấp không có
    nghĩa cánh tay làm xong được việc, mà rollout trong mô phỏng thì đo đúng
    điều đó. Chọn theo mỗi loss và epoch cuối làm rơi mất bốn bản đạt 1.0 trong
    kho này — tức là bỏ đúng những bản đáng đem ra xem.

    `discover_checkpoints` ở máy chủ đọc lại thư mục đầu ra và suy ra epoch,
    validation loss cùng cờ best/latest từ chính tên tệp, nên chỉ cần chép tệp
    sang là bảng checkpoint dựng lại đúng — không phải mang theo `checkpoints`
    của máy cũ.
    """
    files = list(run.rglob("*.pth"))
    if not files:
        return []

    def epoch_of(path: Path) -> int:
        for part in path.stem.split("_"):
            if part.isdigit():
                return int(part)
        return -1

    def success_of(path: Path) -> float | None:
        match = re.search(r"success_([0-9.]+)$", path.stem)
        return float(match.group(1)) if match else None

    keep = {max(files, key=epoch_of)}

    scored = [item for item in files if success_of(item) is not None]
    if scored:
        # Hoà điểm thì lấy epoch lớn hơn: cùng tỉ lệ thành công thì bản huấn
        # luyện lâu hơn là bản đã ổn định.
        keep.add(max(scored, key=lambda item: (success_of(item), epoch_of(item))))

    best = [item for item in files if "best_validation" in item.name]
    if best:
        keep.add(max(best, key=epoch_of))
    return sorted(keep)


def _batches(review: Path) -> list[dict[str, str]]:
    """Đợt thu cần tạo ở máy chủ, đọc ra từ chính điểm số.

    Đợt thu thu bằng máy chủ không để lại dòng nào trong bảng `collection_batches`
    trước bản vá; kho này thu từ trước đó nên không đợt nào có sẵn. Danh sách
    dựng ở đây để đầu kia tạo lại, giữ nguyên tên như máy gốc hiển thị.
    """
    scores = review / "scores.jsonl"
    if not scores.exists():
        return []
    seen: dict[str, str] = {}
    early: set[str] = set()
    for line in scores.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        task = str(record.get("task") or "")
        batch = (record.get("provenance") or {}).get("collection_batch_id")
        if batch:
            seen.setdefault(str(batch), task)
        elif task in EARLY_BATCHES:
            early.add(task)

    result = [{"id": bid, "name": bid, "task": task} for bid, task in sorted(seen.items())]
    for task in sorted(early):
        batch_id, canonical = EARLY_BATCHES[task]
        result.append({"id": batch_id, "name": batch_id, "task": canonical, "adopts_task": task})
    return result


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)

    source = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve()
    if not source.is_dir():
        raise SystemExit(f"Không có thư mục: {source}")

    staging = destination.parent / f".{destination.stem}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print(f"Nguồn : {source}")
    print(f"Đích  : {destination}\n")

    # --- review: điểm, nhãn, video, dataset thô ---------------------------
    review = source / "review"
    if review.is_dir():
        print("review/ ...", end=" ", flush=True)
        shutil.copytree(review, staging / "review")
        size = sum(f.stat().st_size for f in (staging / "review").rglob("*") if f.is_file())
        print(f"{size / 1e9:.2f} GB")

    # --- dataset đã đóng gói ----------------------------------------------
    datasets = source / "datasets"
    if datasets.is_dir():
        print("datasets/ ...", end=" ", flush=True)
        shutil.copytree(datasets, staging / "datasets")
        size = sum(f.stat().st_size for f in (staging / "datasets").rglob("*") if f.is_file())
        print(f"{size / 1e9:.2f} GB")

    # --- episode teleop ---------------------------------------------------
    episodes = source / "episodes"
    if episodes.is_dir():
        print("episodes/ ...", end=" ", flush=True)
        shutil.copytree(episodes, staging / "episodes")
        print("xong")

    # --- lần huấn luyện: bỏ checkpoint trung gian --------------------------
    training = source / "training"
    kept = dropped = 0
    if training.is_dir():
        print("\ntraining/")
        for run in sorted(p for p in training.iterdir() if p.is_dir()):
            target = staging / "training" / run.name
            keep = set(_keep_checkpoints(run))
            for item in run.rglob("*"):
                if not item.is_file():
                    continue
                if item.suffix == ".pth" and item not in keep:
                    dropped += 1
                    continue
                out = target / item.relative_to(run)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, out)
                if item.suffix == ".pth":
                    kept += 1
            name = json.loads((run / "job.json").read_text(encoding="utf-8")).get("name", run.name) \
                if (run / "job.json").exists() else run.name
            print(f"  {name:32} giữ {len(keep)} checkpoint")

    # --- kê khai ----------------------------------------------------------
    manifest = {
        "format_version": 1,
        "batches": _batches(source / "review"),
        "early_batches": {task: bid for task, (bid, _) in EARLY_BATCHES.items()},
        "checkpoints_kept": kept,
        "checkpoints_dropped": dropped,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )

    print(f"\nCheckpoint: giữ {kept}, bỏ {dropped}")
    print(f"Đợt thu   : {len(manifest['batches'])}")
    for batch in manifest["batches"]:
        print(f"  {batch['id']:28} {batch['task']}")

    # --- đóng gói ---------------------------------------------------------
    print("\nĐang nén ...", end=" ", flush=True)
    with tarfile.open(destination, "w") as archive:
        for item in sorted(staging.rglob("*")):
            if item.is_file():
                archive.add(item, item.relative_to(staging).as_posix())
    shutil.rmtree(staging)
    print(f"{destination.stat().st_size / 1e9:.2f} GB")
    print(f"sha256: {_sha256(destination)}")


if __name__ == "__main__":
    main()
