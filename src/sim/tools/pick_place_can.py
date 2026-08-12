"""Can skill adapter around the task-specific finite-state operator."""

from __future__ import annotations

from typing import Any

import numpy as np

from src.sim.operators.scripted_can import CanOperatorConfig, CanPhase, ScriptedCanOperator
from src.sim.perturbations.variations import CanVariation


class PickPlaceCanTool:
    name = "pick_place_can"
    description = "Pick up the Can and place it in its assigned bin."
    version = ScriptedCanOperator.VERSION

    def __init__(self, env: Any, config: CanOperatorConfig | None = None) -> None:
        self.operator = ScriptedCanOperator(env, config)

    def reset(self) -> None:
        self.operator.reset()

    def set_variation(self, variation: CanVariation) -> None:
        self.operator.set_variation(variation)

    def act(self, observation: dict[str, Any]) -> np.ndarray:
        return self.operator.act(observation)

    @property
    def state(self) -> str:
        return self.operator.phase.value

    @property
    def finished(self) -> bool:
        return self.operator.finished

    @property
    def failed(self) -> bool:
        return self.operator.phase == CanPhase.FAILED

    @property
    def failure_reason(self) -> str | None:
        return self.operator.failure_reason

    @property
    def debug_info(self) -> dict[str, Any]:
        return self.operator.debug_info


def register_can_tools(registry: Any) -> None:
    registry.register(PickPlaceCanTool.name, PickPlaceCanTool)
