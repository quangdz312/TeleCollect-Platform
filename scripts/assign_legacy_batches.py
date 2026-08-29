"""Gán episode scripted chưa có đợt thu vào batch, và xoá phần dư.

Square và tool_hang chưa từng thuộc đợt thu nào; xoá chúng là mất trọn hai
task. Chúng được gán vào batch riêng. Lift và can thì đã có đợt thu đầy đủ
(440 và 140 episode), nên phần không batch của hai task đó là dư và bị xoá.

Chạy `--dry-run` trước để xem sẽ đụng vào những gì.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.labeling.workspace import _write_jsonl, Workspace  # noqa: E402

#: Task chưa có đợt thu nào -> gán vào batch mới thay vì xoá. Batch tạo ra
#: KHÔNG kèm description: mô tả là chỗ người dùng ghi chú, không phải chỗ
#: script tự giải thích mình.
ADOPT = {
    "square": ("square-scripted-v1", "Square — scripted v1"),
    "tool_hang": ("tool-hang-scripted-v1", "ToolHang — scripted v1"),
}


def batch_of(record: dict) -> str | None:
    return (record.get("provenance") or {}).get("collection_batch_id") or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    space = Workspace.from_settings()
    rows = space.scores()

    adopted: collections.Counter[str] = collections.Counter()
    dropped: collections.Counter[str] = collections.Counter()
    kept: list[dict] = []
    drop_sources: set[str] = set()

    for record in rows:
        if batch_of(record) is not None:
            kept.append(record)
            continue
        task = record.get("task") or ""
        if task in ADOPT:
            batch_id = ADOPT[task][0]
            record.setdefault("provenance", {})["collection_batch_id"] = batch_id
            adopted[batch_id] += 1
            kept.append(record)
        else:
            dropped[task] += 1
            if record.get("source"):
                drop_sources.add(str(record["source"]))

    # Chỉ xoá file HDF5 mà mọi episode trong đó đều bị bỏ — kiểm tra lại thay
    # vì tin vào khảo sát trước đó.
    still_used = {str(r["source"]) for r in kept if r.get("source")}
    removable = sorted(drop_sources - still_used)

    print("ADOPTED INTO A BATCH")
    for batch_id, count in sorted(adopted.items()):
        print(f"  {batch_id}: {count} episodes")
    print("\nDELETED")
    for task, count in sorted(dropped.items()):
        print(f"  {task}: {count} episodes")
    print(f"\nscores.jsonl: {len(rows)} -> {len(kept)}")
    print(f"HDF5 files removable: {len(removable)}")
    if drop_sources & still_used:
        print(f"  (keeping {len(drop_sources & still_used)} still referenced elsewhere)")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    _write_jsonl(space.scores_path, kept)

    # Nhãn của episode đã xoá thành mồ côi -> bỏ luôn cho khớp.
    labels_path = space.root / "labels.jsonl"
    if labels_path.exists():
        from src.labeling.workspace import _read_jsonl

        alive = {str(r.get("episode_id")) for r in kept}
        labels = _read_jsonl(labels_path)
        keep_labels = [x for x in labels if str(x.get("episode_id")) in alive]
        _write_jsonl(labels_path, keep_labels)
        print(f"labels.jsonl: {len(labels)} -> {len(keep_labels)}")

    freed = 0
    for source in removable:
        path = Path(source)
        if path.exists():
            freed += path.stat().st_size
            path.unlink()
    print(f"removed {len(removable)} files, freed {freed / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
