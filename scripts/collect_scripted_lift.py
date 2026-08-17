#!/usr/bin/env python
"""Collect raw Lift demonstrations with the registered scripted tool."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sim.scripted_generation import main_for_task


if __name__ == "__main__":
    raise SystemExit(main_for_task("lift"))
