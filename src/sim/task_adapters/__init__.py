"""Bindings between generic tool execution and concrete simulator tasks."""

from .can import build_can_tool_context, can_observation

__all__ = ["build_can_tool_context", "can_observation"]
