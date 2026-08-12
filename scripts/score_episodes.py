#!/usr/bin/env python
"""Score collected episodes. Decides nothing on its own.

With no thresholds passed, every episode is routed to ``needs_review``. That is
the intended starting state: the score and its flags are useful to a reviewer
long before there is enough labelled data to justify letting the machine decide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.labeling import load_episodes, score_episodes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="collected .hdf5 files or directories")
    parser.add_argument("--output", type=Path, help="JSONL to write; defaults to stdout summary only")
    parser.add_argument("--task", help="override the task for datasets without provenance")
    parser.add_argument("--approve-threshold", type=float, default=None)
    parser.add_argument("--reject-threshold", type=float, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.approve_threshold is not None and args.reject_threshold is not None:
        if args.approve_threshold <= args.reject_threshold:
            parser.error("--approve-threshold must be above --reject-threshold")
    return args


def _expand(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        if item.is_dir():
            files.extend(sorted(item.glob("*.hdf5")))
        else:
            files.append(item)
    return files


def main() -> int:
    args = parse_args()
    files = _expand(args.inputs)
    if not files:
        print("no .hdf5 inputs found", file=sys.stderr)
        return 1
    if args.output is not None and args.output.exists() and not args.overwrite:
        print(f"refusing to overwrite {args.output}; pass --overwrite", file=sys.stderr)
        return 1

    episodes = []
    for path in files:
        episodes.extend(load_episodes(path, task=args.task))
    if not episodes:
        print("no demos found in the given files", file=sys.stderr)
        return 1

    scored, stats = score_episodes(
        episodes,
        approve_threshold=args.approve_threshold,
        reject_threshold=args.reject_threshold,
    )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "".join(json.dumps(item.summary(), sort_keys=True) + "\n" for item in scored),
            encoding="utf-8",
        )

    if not args.quiet:
        print(f"scorer_version={scored[0].scorer_version}")
        print(f"corpus per task: {stats.counts}")
        print()
        header = f"{'episode':38s} {'task':7s} {'quality':7s} {'score':>6s}  {'decision':12s} flags"
        print(header)
        print("-" * len(header))
        for item in scored:
            worst = item.worst_penalty
            reasons = list(item.failed_checks)
            if worst is not None and worst.value > 0:
                reasons.append(f"{worst.name}={worst.value:.2f}")
            print(
                f"{item.episode_id[:38]:38s} {item.task:7s} {item.requested_quality:7s} "
                f"{item.auto_score:6.3f}  {item.gate_decision:12s} {', '.join(reasons) or '-'}"
            )
        decisions: dict[str, int] = {}
        for item in scored:
            decisions[item.gate_decision] = decisions.get(item.gate_decision, 0) + 1
        print()
        print(f"{len(scored)} episodes: {decisions}")
        if args.output is not None:
            print(f"written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
