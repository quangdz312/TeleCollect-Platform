"""Square nut assembly skill adapter."""

from __future__ import annotations

from typing import Any

import numpy as np

from src.sim.operators.scripted_square import SquareOperatorConfig, SquarePhase, ScriptedSquareOperator
from src.sim.perturbations.variations import SquareVariation


class AssembleSquareTool:
    name = "assemble_square"
    description = "Pick up the square nut and place it over the square peg."
    version = ScriptedSquareOperator.VERSION

    def __init__(self, env: Any, config: SquareOperatorConfig | None = None) -> None:
        self.operator = ScriptedSquareOperator(env, config)

    def reset(self) -> None:
        self.operator.reset()

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        return self.operator.act(observation)

    def set_variation(self, variation: SquareVariation) -> None:
        self.operator.set_variation(variation)

    @property
    def state(self) -> str:
        return self.operator.phase.value

    @property
    def finished(self) -> bool:
        return self.operator.finished

    @property
    def failed(self) -> bool:
        return self.operator.phase == SquarePhase.FAILED

    @property
    def failure_reason(self) -> str | None:
        return self.operator.failure_reason

    @property
    def debug_info(self) -> dict[str, Any]:
        return self.operator.debug_info


def register_square_tools(registry: Any) -> None:
    registry.register(AssembleSquareTool.name, AssembleSquareTool)
