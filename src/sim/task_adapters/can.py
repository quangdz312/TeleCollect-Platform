"""Can-specific reset, success, and recording schema adapters."""

from __future__ import annotations

from typing import Any, Callable

from src.sim.collection.robomimic_hdf5_writer import RobomimicHDF5Writer
from src.sim.can_env import reset_can_environment
from src.sim.tools.base import ToolContext


def can_observation(observation: dict[str, Any]) -> dict[str, Any]:
    result = dict(observation)
    if "object" not in result and "object-state" in result:
        result["object"] = result["object-state"]
    return {key: result[key] for key in RobomimicHDF5Writer.REQUIRED_OBS_KEYS}


def build_can_tool_context(
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
        reset=reset_can_environment,
        is_success=lambda active_env: bool(active_env._check_success()),
        adapt_observation=can_observation,
        verbose=verbose,
        logger=logger,
    )
