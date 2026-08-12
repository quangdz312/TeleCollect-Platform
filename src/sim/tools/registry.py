"""Explicit registry used by CLIs today and an LLM planner in the future."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import RobotTool


ToolFactory = Callable[[Any], RobotTool]


class ToolRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ToolFactory] = {}

    def register(self, name: str, factory: ToolFactory) -> None:
        if not name or name in self._factories:
            raise ValueError(f"Robot tool already registered or invalid: {name!r}")
        self._factories[name] = factory

    def create(self, name: str, env: Any) -> RobotTool:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(f"Unknown robot tool {name!r}; available={self.names()}") from exc
        return factory(env)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def catalog(self) -> tuple[dict[str, str], ...]:
        """Return planner-friendly metadata without constructing environments."""
        return tuple(
            {
                "name": name,
                "description": str(getattr(factory, "description", "")),
                "version": str(getattr(factory, "version", "unknown")),
            }
            for name, factory in sorted(self._factories.items())
        )
