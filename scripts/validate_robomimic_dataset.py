#!/usr/bin/env python
"""Validate technical schema only; this does not approve episode quality."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sim.collection.schema_validator import validate_robomimic_dataset
from src.sim.can_env import REFERENCE_DATASET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--reference", type=Path, default=REFERENCE_DATASET)
    args = parser.parse_args()
    result = validate_robomimic_dataset(args.dataset, reference_path=args.reference)
    if result.valid:
        print(f"VALID: demos={result.demos} total_samples={result.total_samples}")
        return 0
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
