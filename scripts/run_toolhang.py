"""Collect ToolHang episodes (stage 1 + stage 2) into a Robomimic HDF5.

Examples:
    python scripts/run_toolhang.py --output data/toolhang.hdf5 --n 10
    python scripts/run_toolhang.py --output data/toolhang_wide.hdf5 --n 10 \
        --frame-extra 0.04 --tool-extra 0.04 --yaw-extra 0.35
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repository package importable when this file is invoked directly
# from the scripts directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sim.tool_hang_collection import collect


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--frame-extra", type=float, default=0.0)
    parser.add_argument("--tool-extra", type=float, default=0.0)
    parser.add_argument("--yaw-extra", type=float, default=0.0)
    args = parser.parse_args()

    result = collect(
        args.output,
        episodes=args.n,
        seed=args.seed,
        overwrite=args.overwrite,
        max_attempts=args.max_attempts,
        frame_extra=args.frame_extra,
        tool_extra=args.tool_extra,
        yaw_extra=args.yaw_extra,
    )
    print(
        f"episodes={result['episodes']} successes={result['successes']} "
        f"failed={result['failed']}\nhdf5={result['output']}\n"
        f"trace={result['trace_output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
