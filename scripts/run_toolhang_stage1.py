"""Run the validated ToolHang Stage-1 collector from the companion project.

Examples:
    python scripts/run_toolhang_stage1.py --eval 20
    python scripts/run_toolhang_stage1.py --dataset --n 25
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Make the repository package importable when this file is invoked directly
# from the scripts directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sim.tool_hang import project_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", type=int, metavar="N")
    parser.add_argument("--dataset", action="store_true")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stand-jitter", type=float, default=0.0)
    args = parser.parse_args()

    root = project_root()
    # Use the project's pinned robosuite 1.5.2. The companion venv is kept for
    # reference/data, but its robosuite 1.4.x runtime is not compatible with
    # the main application.
    python = sys.executable
    if args.dataset:
        command = [python, "generate_dataset.py", "--n", str(args.n),
                   "--seed", str(args.seed), "--stand-jitter", str(args.stand_jitter)]
    else:
        command = [python, "run_stage1.py", "--eval", str(args.eval or 1),
                   "--seed", str(args.seed), "--stand-jitter", str(args.stand_jitter)]
    return subprocess.call(command, cwd=Path(root))


if __name__ == "__main__":
    raise SystemExit(main())
