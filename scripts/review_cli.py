#!/usr/bin/env python
"""Review episodes one at a time and record the decision.

Decisions land in a JSONL append log. That log is the input the thresholds are
later derived from, so this is the step that has to happen before any automatic
approve or reject can be switched on.

The score and its flags are hidden by default. A reviewer who sees the machine's
opinion first anchors on it, and the agreement rate stops meaning anything —
which would defeat the point of collecting these labels. Pass ``--show-score``
when you want the assisted view rather than calibration data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.labeling.playback import DEFAULT_PLAYBACK, PlaybackConfig, render_demo
from src.labeling.reasons import REJECTION_REASONS, validate


PROMPT = "[a]pprove  [r]eject  [s]kip  re[p]lay  [q]uit > "


def ask_reasons() -> list[str]:
    """Same vocabulary the web UI ticks, so the two logs stay comparable."""

    print("  lý do (số, cách nhau bởi dấu cách; Enter để bỏ qua):")
    for index, reason in enumerate(REJECTION_REASONS, start=1):
        print(f"    {index}. {reason.code:20s} {reason.label_vi}")
    while True:
        answer = input("  > ").strip()
        if not answer:
            return []
        try:
            picked = [REJECTION_REASONS[int(token) - 1].code for token in answer.split()]
            return validate(picked)
        except (ValueError, IndexError):
            print("  chỉ nhập số trong danh sách trên")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True, help="JSONL from score_episodes.py")
    parser.add_argument("--labels", type=Path, required=True, help="JSONL to append decisions to")
    parser.add_argument("--video-dir", type=Path, required=True)
    parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    parser.add_argument("--show-score", action="store_true", help="disable the blind default")
    parser.add_argument("--task", help="override for datasets without provenance")
    parser.add_argument("--limit", type=int, default=0, help="stop after N decisions")
    parser.add_argument("--no-open", action="store_true", help="only print the video path")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def open_video(path: Path) -> None:
    for player in ("xdg-open", "mpv", "vlc", "ffplay"):
        if shutil.which(player):
            subprocess.Popen(  # noqa: S603 - local playback of a file we just wrote
                [player, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    print("  (no video player found; open the file manually)")


def describe(record: dict, *, show_score: bool) -> None:
    print()
    print("=" * 72)
    print(f"episode : {record['episode_id']}")
    print(f"task    : {record['task']}   requested quality: {record['requested_quality']}")
    print(f"length  : {record['length']} frames")
    if show_score:
        flags = record["auto_flags"]
        print(f"score   : {record['auto_score']:.3f}   decision: {record['gate_decision']}")
        if flags["failed_checks"]:
            print(f"failed  : {', '.join(flags['failed_checks'])}")
        if flags["worst_penalty"]:
            print(f"worst   : {flags['worst_penalty']} = {flags['worst_penalty_value']:.2f}")
    else:
        print("score   : hidden (blind review; pass --show-score to see it)")
    print(
        f"trim    : suggested [{record['suggested_trim_start']}, "
        f"{record['suggested_trim_end']}] "
        f"(cuts {record['trim_head_frames']} head, {record['trim_tail_frames']} tail)"
    )


def main() -> int:
    args = parse_args()
    scores = load_jsonl(args.scores)
    if not scores:
        print(f"no scored episodes in {args.scores}", file=sys.stderr)
        return 1
    already = {record["episode_id"] for record in load_jsonl(args.labels)}
    pending = [record for record in scores if record["episode_id"] not in already]
    print(f"{len(scores)} scored, {len(already)} already reviewed, {len(pending)} to go")
    if not pending:
        return 0

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    args.video_dir.mkdir(parents=True, exist_ok=True)
    config: PlaybackConfig = DEFAULT_PLAYBACK
    decided = 0

    for record in pending:
        source = Path(record["source"])
        demo = record["demo"]
        video = args.video_dir / f"{source.stem}__{demo}.mp4"
        if not video.exists():
            print(f"rendering {record['episode_id']} ...")
            render_demo(
                source,
                demo,
                video,
                task=args.task or record.get("task"),
                trim=(record["suggested_trim_start"], record["suggested_trim_end"]),
                config=config,
            )

        describe(record, show_score=args.show_score)
        print(f"video   : {video}")
        if not args.no_open:
            open_video(video)

        while True:
            try:
                answer = input(PROMPT).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nstopped")
                return 0
            if answer in {"a", "r", "s", "q"}:
                break
            if answer == "p":
                open_video(video)
                continue
            print("  please answer a, r, s, p or q")

        if answer == "q":
            print("stopped")
            break
        if answer == "s":
            continue

        reasons = ask_reasons() if answer == "r" else []
        note = input("note (optional) > ").strip()
        decision = {
            "episode_id": record["episode_id"],
            "source": record["source"],
            "demo": demo,
            "task": record["task"],
            "requested_quality": record["requested_quality"],
            "human_decision": "approved" if answer == "a" else "rejected",
            "reasons": reasons,
            "note": note,
            "reviewer": args.reviewer,
            "blind": not args.show_score,
            "scorer_version": record["scorer_version"],
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with args.labels.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision, sort_keys=True) + "\n")
        decided += 1
        print(f"  recorded: {decision['human_decision']}")
        if args.limit and decided >= args.limit:
            print(f"reached --limit {args.limit}")
            break

    print(f"\n{decided} decisions appended to {args.labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
