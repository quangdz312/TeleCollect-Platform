"""Small interface shared by scripted operators."""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np


class ScriptedOperator(Protocol):
    def reset(self) -> None: ...

    def act(self, observation: dict[str, Any]) -> np.ndarray: ...

    @property
    def finished(self) -> bool: ...
