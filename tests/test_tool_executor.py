from __future__ import annotations

import numpy as np
import pytest

from src.sim.tools.base import ToolContext, ToolStatus
from src.sim.tools.executor import execute_tool


class _State:
    def flatten(self) -> np.ndarray:
        return np.zeros(1)


class _Model:
    @staticmethod
    def get_xml() -> str:
        return "<mujoco/>"


class _Sim:
    model = _Model()

    @staticmethod
    def get_state() -> _State:
        return _State()


class _Env:
    sim = _Sim()
    action_spec = (-np.ones(7), np.ones(7))

    def __init__(self) -> None:
        self.steps = 0

    def step(self, action: np.ndarray):
        self.steps += 1
        return {"step": self.steps}, 0.0, False, {}


class _Tool:
    name = "test_tool"
    version = "1.0"
    state = "holding"
    finished = False
    failed = False
    failure_reason = None

    @staticmethod
    def reset() -> None:
        return None

    @staticmethod
    def act(_observation: dict) -> np.ndarray:
        return np.zeros(7)


def _context(env: _Env, *, success_at: int, horizon: int, tail: int) -> ToolContext:
    return ToolContext(
        env=env,
        horizon=horizon,
        seed=0,
        reset=lambda _env, _seed: {"step": 0},
        is_success=lambda current_env: current_env.steps >= success_at,
        adapt_observation=lambda observation: observation,
        post_success_steps=tail,
    )


def test_executor_records_requested_tail_after_success() -> None:
    env = _Env()

    result = execute_tool(_Tool(), _context(env, success_at=2, horizon=5, tail=3))

    assert result.status == ToolStatus.SUCCESS
    assert result.steps == 5
    assert result.episode.dones == [False, False, False, False, True]


def test_executor_does_not_extend_unsuccessful_horizon() -> None:
    env = _Env()

    result = execute_tool(_Tool(), _context(env, success_at=99, horizon=4, tail=3))

    assert result.status == ToolStatus.HORIZON
    assert result.steps == 4


def test_executor_can_disable_success_tail() -> None:
    env = _Env()

    result = execute_tool(_Tool(), _context(env, success_at=2, horizon=5, tail=0))

    assert result.status == ToolStatus.SUCCESS
    assert result.steps == 2


def test_executor_rejects_negative_success_tail() -> None:
    env = _Env()

    with pytest.raises(ValueError, match="post_success_steps"):
        execute_tool(_Tool(), _context(env, success_at=2, horizon=5, tail=-1))
