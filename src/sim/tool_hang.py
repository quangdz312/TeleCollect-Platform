"""ToolHang integration boundary.

The ToolHang simulator is vendored at `src.sim.skillgen`.  Keep it behind this
module so the core task registry can advertise the task without importing
robosuite at application startup.  The task is the full two-stage skill: stage 1
stands the hook frame up in the stand, stage 2 threads the wrench onto the hook.
"""

from __future__ import annotations

from typing import Any

TOOLHANG_STAGE = "stage1+stage2"
TOOLHANG_STATUS = "full_task_ready"

#: Stored provenance identifier, written into dataset HDF5 attrs and into
#: `data/review/scores.jsonl`. It is historical: the name dates from when only
#: stage 1 existed, and the task now runs both stages. It is kept anyway because
#: it identifies already-collected data, and `src/labeling/features.py` and
#: `src/labeling/rule_engine.py` map it back to the `tool_hang` task. Never show
#: it to a human -- use `TOOLHANG_DISPLAY_NAME` for that.
TOOLHANG_TOOL_NAME = "tool_hang_stage1"

#: What a human reads. The task is the full two-stage ToolHang, so anything the
#: UI or a report renders must say so rather than repeating the stage-1 name.
TOOLHANG_DISPLAY_NAME = "ToolHang (stage 1 + stage 2)"

TOOLHANG_TASK_CODE = 4

#: Same story as `TOOLHANG_TOOL_NAME`: this string is recorded in every episode
#: as `profile_version`, so it stays stable to keep collected data comparable.
TOOLHANG_PROFILE = "tool-hang-stage1-baseline"


def make_tool_hang_environment(**kwargs: Any) -> Any:
    """Create the validated ToolHang environment from the vendored skill.

    The review camera is installed here rather than by each caller, because
    every path that renders a ToolHang review pane -- the live teleop session,
    the mp4 written while a scripted episode is collected, and the replay --
    goes through this function, and all three must produce the same shot. The
    scripted collector used to build its environment without it and its
    recorder then quietly fell back to `agentview`, so the review video showed a
    different angle from the one the operator sees.

    ToolHang's stand is pinned by its placement sampler (zero-width x, y and
    rotation ranges), so the angle stays correct across the resets the skill
    does per attempt and this costs nothing per control step.
    """

    from src.sim.review_camera import install_into_env
    from src.sim.skillgen.env_setup import make_env

    env = make_env(**kwargs)
    install_into_env(env)
    return env


def tool_hang_stage1_success(env: Any) -> bool:
    """Stage-1 truth: robosuite says the hook frame is assembled into the stand.

    This is the simulator's own physical check.  The skill's geometric
    `EpisodeResult.success` is *looser* -- it gates seating depth at 40 mm where
    correct assembly needs ~130 mm -- so it reports success on a rod that jammed
    part-way.  Never use it as the label.
    """

    return bool(env._check_frame_assembled())


def tool_hang_stage2_success(env: Any) -> bool:
    """Stage-2 truth: robosuite says the wrench is hanging on the hook."""

    return bool(env._check_tool_on_frame())


def tool_hang_success(env: Any) -> bool:
    """Full-task truth: both stages, as judged by robosuite."""

    return tool_hang_stage1_success(env) and tool_hang_stage2_success(env)


def tool_hang_metadata() -> dict[str, Any]:
    """Stable metadata for UI/API clients and dataset provenance."""

    return {
        "task": "tool_hang",
        "stage": TOOLHANG_STAGE,
        "status": TOOLHANG_STATUS,
        "source": "src/sim/skillgen (vendored from Test/skillgen)",
        "stage1_success": "robosuite _check_frame_assembled: hook frame seated in the stand",
        "stage2_success": "robosuite _check_tool_on_frame: wrench threaded onto the hook",
    }
