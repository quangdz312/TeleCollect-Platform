"""Lift skill adapter."""

from __future__ import annotations

from typing import Any

import numpy as np

from src.sim.operators.scripted_lift import LiftOperatorConfig, LiftPhase, ScriptedLiftOperator
from src.sim.perturbations.variations import LiftVariation


class LiftCubeTool:
    name = "lift_cube"
    description = "Grasp the cube and lift it above the table."
    version = ScriptedLiftOperator.VERSION

    def __init__(self, env: Any, config: LiftOperatorConfig | None = None) -> None:
        self.operator = ScriptedLiftOperator(env, config)

    def reset(self) -> None:
        self.operator.reset()

    def set_variation(self, variation: LiftVariation) -> None:
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
        return self.operator.phase == LiftPhase.FAILED

    @property
    def failure_reason(self) -> str | None:
        return self.operator.failure_reason

    @property
    def debug_info(self) -> dict[str, Any]:
        return self.operator.debug_info


def register_lift_tools(registry: Any) -> None:
    registry.register(LiftCubeTool.name, LiftCubeTool)
