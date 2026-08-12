#!/usr/bin/env python
"""Compare scores against human decisions and report whether a gate is justified.

This never turns anything on. It prints the thresholds the data would support
and the reasons they are not yet trustworthy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.labeling.shadow import DEFAULT_SHADOW, build_report
from src.labeling.workspace import dedupe_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--json", type=Path, help="also write the report as JSON")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _format(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> int:
    args = parse_args()
    scores = load_jsonl(args.scores)
    # The log is append-only, so a re-reviewed episode appears twice; only the
    # latest verdict may vote in the threshold derivation.
    labels = dedupe_labels(load_jsonl(args.labels))
    if not scores:
        print(f"no scored episodes in {args.scores}", file=sys.stderr)
        return 1

    report = build_report(scores, labels, DEFAULT_SHADOW)

    print("shadow mode report")
    print("=" * 60)
    print(f"scored episodes      : {len(scores)}")
    print(f"human decisions      : {len(labels)}  (matched to a score: {report.matched})")
    print(f"  approved / rejected: {report.approved} / {report.rejected}")
    print()
    print(f"AUC                  : {_format(report.auc, 3)}   (need >= {DEFAULT_SHADOW.min_auc})")
    print(f"review yield         : {_format(report.review_yield, 3)}")
    if report.review_yield is not None and not report.review_yield_is_meaningful:
        print(
            f"                       too few reviews to read anything into "
            f"(need {DEFAULT_SHADOW.min_reviews_for_yield})"
        )
    elif report.review_yield is not None:
        if report.review_yield < 0.10:
            verdict = "most reviews change nothing - strong case for automating"
        elif report.review_yield <= 0.30:
            verdict = "humans are adding real value - automate the trim, keep the gate manual"
        else:
            verdict = "the checks are still too weak - fix them before discussing a gate"
        print(f"                       {verdict}")
    print()
    print(f"tau_reject           : {_format(report.tau_reject)}")
    print(f"tau_approve          : {_format(report.tau_approve)}")
    print(f"approve zone         : {report.approve_zone_count} episodes")
    print(f"wrong-approval bound : {_format(report.wrong_approval_bound, 4)}  (rule of three)")
    if report.false_rejections:
        print()
        print("FALSE REJECTIONS (human approved, score at or below tau_reject):")
        for episode_id in report.false_rejections:
            print(f"  {episode_id}")
    print()
    print(f"gate ready           : {'YES' if report.gate_ready else 'NO'}")
    for reason in report.reasons:
        print(f"  - {reason}")

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True), encoding="utf-8",
        )
        print()
        print(f"written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
