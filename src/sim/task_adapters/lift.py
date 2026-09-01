"""Lift-specific reset, success, and recording adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.sim.collection.robomimic_hdf5_writer import RobomimicHDF5Writer
from src.sim.lift_env import reset_lift_environment
from src.sim.tools.base import ToolContext


def lift_observation(observation: dict[str, Any]) -> dict[str, Any]:
    result = dict(observation)
    if "object" not in result and "object-state" in result:
        result["object"] = result["object-state"]
    return {key: result[key] for key in RobomimicHDF5Writer.REQUIRED_OBS_KEYS}


def build_lift_tool_context(
    env: Any,
    *,
    horizon: int,
    seed: int,
    verbose: bool = False,
    logger: Callable[[str], None] = print,
) -> ToolContext:
    return ToolContext(
        env=env,
        horizon=horizon,
        seed=seed,
        reset=reset_lift_environment,
        is_success=lambda active_env: bool(active_env._check_success()),
        adapt_observation=lift_observation,
        verbose=verbose,
        logger=logger,
        # Keep recording through the operator's complete 30-step HOLD phase.
        # At Lift's 20 Hz control rate this preserves roughly 1.5 seconds of a
        # stable grasp, matching the collection used to export lift-v6.
        post_success_steps=30,
        finish_tool_after_success=True,
    )
