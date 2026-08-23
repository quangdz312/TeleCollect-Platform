"""Built-in tool catalog."""

from .assemble_square import register_square_tools
from .lift_cube import register_lift_tools
from .pick_place_can import register_can_tools
from .registry import ToolRegistry


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_can_tools(registry)
    register_lift_tools(registry)
    register_square_tools(registry)
    return registry
