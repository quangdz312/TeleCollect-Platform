"""Validate a TeleCollect export and train a minimal RoboMimic BC policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="Export .hdf5 từ TeleCollect")
    parser.add_argument("--output-dir", type=Path, default=Path("data/training"))
    parser.add_argument("--name", default="telecollect_bc")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--policy",
        choices=("bc", "bc-rnn"),
        default="bc",
        help="Kiến trúc policy: BC feed-forward hoặc BC-RNN (LSTM)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Chỉ validate và in kế hoạch, không import RoboMimic"
    )
    return parser.parse_args()


def main() -> int:
    from src.training.robomimic_bc import inspect_training_dataset, run_training

    args = parse_args()
    if args.epochs < 1 or args.batch_size < 1 or args.num_workers < 0:
        raise SystemExit("epochs/batch-size phải >= 1 và num-workers phải >= 0")
    try:
        plan, validation = inspect_training_dataset(
            args.dataset,
            output_dir=args.output_dir,
            name=args.name,
            epochs=args.epochs,
            batch_size=args.batch_size,
            num_workers=args.num_workers,
            device=args.device,
            policy=args.policy,
        )
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({**plan.to_dict(), "frames": validation.total_samples}, indent=2))
    if args.dry_run:
        print("Dry run OK: dataset hợp lệ, chưa bắt đầu training.")
        return 0
    run_training(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
