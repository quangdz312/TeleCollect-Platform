"""ToolHang integration boundary.

The validated ToolHang simulator currently lives in the companion skill project
(`D:\\VIN_AI_TC\\Test`).  Keep that dependency behind this module so the core
task registry can advertise the task without importing robosuite at application
startup.  Stage 1 is production-ready; Stage 2 remains explicitly blocked until
the second tool grasp and image dataset contract are completed.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DEFAULT_TOOLHANG_ROOT = Path(r"D:\VIN_AI_TC\Test")
TOOLHANG_STAGE = "stage1"
TOOLHANG_STATUS = "stage1_ready_stage2_blocked"


def project_root() -> Path:
    """Return the external ToolHang skill root, overridable for deployment."""

    configured = os.environ.get("TELECOLLECT_TOOLHANG_ROOT")
    return Path(configured) if configured else DEFAULT_TOOLHANG_ROOT


def _require_root() -> Path:
    root = project_root()
    if not (root / "skillgen" / "env_setup.py").is_file():
        raise RuntimeError(
            "ToolHang simulator is unavailable. Set TELECOLLECT_TOOLHANG_ROOT "
            "to the folder containing skillgen/env_setup.py."
        )
    return root


@contextlib.contextmanager
def _skill_imports() -> Iterator[None]:
    root = _require_root()
    root_text = str(root)
    sys.path.insert(0, root_text)
    try:
        yield
    finally:
        try:
            sys.path.remove(root_text)
        except ValueError:
            pass


def make_tool_hang_environment(**kwargs: Any) -> Any:
    """Create the validated ToolHang environment from the companion project."""

    with _skill_imports():
        module = importlib.import_module("skillgen.env_setup")
        return module.make_env(**kwargs)


def tool_hang_success(env: Any) -> bool:
    """Use ToolHang's geometric Stage-1 truth, never its loose env predicate."""

    with _skill_imports():
        geometry = importlib.import_module("skillgen.geometry")
        frame_body = env.sim.model.body_name2id("frame_root")
        quat = env.sim.data.body_xquat[frame_body]
        import robosuite.utils.transform_utils as transforms

        rotation = transforms.quat2mat(
            [quat[1], quat[2], quat[3], quat[0]],
        )
        tip = env.sim.data.body_xpos[frame_body] + rotation @ geometry.TIP_LOCAL
        axis = rotation @ geometry.ROD_AXIS_LOCAL
        cavity = geometry.read_cavity(env)
        return bool(geometry.is_seated(tip, axis, cavity))


def tool_hang_metadata() -> dict[str, Any]:
    """Stable metadata for UI/API clients and dataset provenance."""

    return {
        "task": "tool_hang",
        "stage": TOOLHANG_STAGE,
        "status": TOOLHANG_STATUS,
        "source": "Test/skillgen",
        "stage1_success": "rod seated at least 40 mm, lateral and tilt gates pass",
        "stage2": "blocked: tool_hole2/reachability fix and full image dataset pending",
    }
