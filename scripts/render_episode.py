#!/usr/bin/env python
"""Render collected episodes to mp4 so a person can watch them.

Replays the recorded MuJoCo states, so nothing needs re-collecting and the same
input always produces the same video. Frames the suggested trim would cut are
dimmed and marked, which is the command-line equivalent of opening the trim
timeline pre-set at the suggested cut points.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import h5py

from src.labeling.playback import DEFAULT_PLAYBACK, PlaybackConfig, render_demo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="a collected .hdf5")
    parser.add_argument("--demo", help="demo name; default renders every demo in the file")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task", help="override for datasets without provenance")
    parser.add_argument("--scores", type=Path, help="scores JSONL, used for the trim overlay")
    parser.add_argument("--camera", default=DEFAULT_PLAYBACK.camera)
    parser.add_argument("--height", type=int, default=DEFAULT_PLAYBACK.height)
    parser.add_argument("--width", type=int, default=DEFAULT_PLAYBACK.width)
    parser.add_argument("--fps", type=int, default=DEFAULT_PLAYBACK.fps)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_trims(path: Path | None) -> dict[str, tuple[int, int]]:
    if path is None or not path.exists():
        return {}
    trims: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        start = record.get("suggested_trim_start")
        end = record.get("suggested_trim_end")
        if start is not None and end is not None:
            trims[record["episode_id"]] = (int(start), int(end))
    return trims


def main() -> int:
    args = parse_args()
    config = PlaybackConfig(
        camera=args.camera, height=args.height, width=args.width, fps=args.fps,
    )
    with h5py.File(args.dataset, "r") as handle:
        names = sorted(
            (key for key in handle["data"] if key.startswith("demo_")),
            key=lambda name: int(name.split("_")[1]),
        )
    if args.demo:
        if args.demo not in names:
            print(f"{args.demo} not in {args.dataset}", file=sys.stderr)
            return 1
        names = [args.demo]

    trims = load_trims(args.scores)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        output = args.output_dir / f"{args.dataset.stem}__{name}.mp4"
        if output.exists() and not args.overwrite:
            print(f"skip (exists): {output}")
            continue
        trim = trims.get(f"{args.dataset.name}::{name}")
        render_demo(args.dataset, name, output, task=args.task, trim=trim, config=config)
        print(f"wrote: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
