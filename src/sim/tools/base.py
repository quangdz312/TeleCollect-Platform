"""Contracts for reusable robot tools.

Tools operate at skill level (for example ``pick_place_can``), while their
internal operator remains responsible for producing control-rate actions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import numpy as np

from src.sim.collection.robomimic_hdf5_writer import EpisodeData


# enum.StrEnum's str()/format() output differs from this (str, Enum) mixin in
# ways that would change behavior wherever a member is printed or serialized
# implicitly, not just its style — kept as-is rather than "modernized".
class ToolStatus(str, Enum):  # noqa: UP042
    SUCCESS = "success"
    FAILED = "failed"
    ENVIRONMENT_DONE = "environment_done"
    HORIZON = "horizon"


@dataclass(frozen=True)
class ToolContext:
    env: Any
    horizon: int
    seed: int
    reset: Callable[[Any, int], dict[str, Any]]
    is_success: Callable[[Any], bool]
    adapt_observation: Callable[[dict[str, Any]], dict[str, Any]]
    verbose: bool = False
    logger: Callable[[str], None] = print
    post_success_steps: int = 10


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: ToolStatus
    termination_reason: str
    steps: int
    final_state: str
    episode: EpisodeData

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.SUCCESS


class RobotTool(Protocol):
    """Minimal interface implemented by every executable robot skill."""

    name: str
    description: str
    version: str

    def reset(self) -> None: ...

    def act(self, observation: dict[str, Any]) -> np.ndarray: ...

    @property
    def state(self) -> str: ...

    @property
    def finished(self) -> bool: ...

    @property
    def failed(self) -> bool: ...

    @property
    def failure_reason(self) -> str | None: ...

    @property
    def debug_info(self) -> dict[str, Any]: ...
