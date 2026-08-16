"""Create a RoboMimic dataset whose train / valid masks keep selected qualities."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import h5py


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--qualities",
        nargs="+",
        default=["clean", "good"],
        help="Các telecollect_requested_quality cần giữ",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise SystemExit(f"Không tìm thấy dataset: {source}")
    if source == output:
        raise SystemExit("Output phải khác input để giữ nguyên snapshot gốc")
    if output.exists() and not args.overwrite:
        raise SystemExit(f"Output đã tồn tại: {output}; dùng --overwrite nếu muốn ghi đè")

    allowed = {quality.strip().lower() for quality in args.qualities}
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)

    counts: dict[str, int] = {}
    try:
        with h5py.File(output, "r+") as handle:
            kept_names: set[str] = set()
            for split in ("train", "valid"):
                mask_path = f"mask/{split}"
                selected: list[bytes] = []
                for raw_name in handle[mask_path][...]:
                    name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
                    quality = str(
                        handle[f"data/{name}"].attrs.get(
                            "telecollect_requested_quality", ""
                        )
                    ).lower()
                    if quality in allowed:
                        selected.append(name.encode("utf-8"))
                        kept_names.add(name)
                del handle[mask_path]
                handle["mask"].create_dataset(split, data=selected)
                counts[split] = len(selected)
            for name in list(handle["data"].keys()):
                if name not in kept_names:
                    del handle[f"data/{name}"]
            handle["data"].attrs["total"] = sum(
                int(handle[f"data/{name}"].attrs["num_samples"])
                for name in kept_names
            )
    except Exception:
        output.unlink(missing_ok=True)
        raise

    if not counts.get("train"):
        output.unlink(missing_ok=True)
        raise SystemExit("Không có episode phù hợp trong train mask")
    print(f"Created: {output}")
    print(f"Qualities: {', '.join(sorted(allowed))}")
    print(f"Train: {counts['train']} episodes; valid: {counts['valid']} episodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
