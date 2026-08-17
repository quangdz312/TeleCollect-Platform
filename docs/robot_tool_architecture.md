# Robot tool architecture

The simulation code separates task mechanics from reusable execution:

```text
CLI or future planner
        -> ToolRegistry
        -> RobotTool
        -> generic execute_tool loop
        -> task adapter
        -> environment and raw recorder
```

Core extension points:

- `src/sim/tools/base.py` defines the stable tool context and result contracts.
- `src/sim/tools/executor.py` runs any tool and captures raw transitions.
- `src/sim/tools/registry.py` provides explicit discovery by tool name.
- `src/sim/task_adapters/` owns reset, success, and observation differences.
- `src/sim/operators/` contains control-rate task logic such as finite-state machines.

The built-in implementations are registered as `lift_cube`, `pick_place_can`,
and `assemble_square`. To add another task, create its operator, expose it
through a `RobotTool` adapter, add a task context adapter, and register the
factory in `src/sim/tools/defaults.py`.

> **ToolHang is the exception and does not go through this registry.** Its skill
> is vendored wholesale at `src/sim/skillgen/` and driven by its own collection
> path, `src/sim/tool_hang_collection.py`, reached through the boundary module
> `src/sim/tool_hang.py`. Nothing named `tool_hang_stage1` is registered in
> `build_default_registry()`; that string is a stored dataset identifier, not a
> lookup key. So the pipeline above describes three of the four collectable
> tasks. See [`toolhang_integration.md`](toolhang_integration.md).
Neither the generic executor nor a future LLM planner should contain task
branches. A planner calls skill-level tools and never emits OSC actions itself.

Tools return structured status, termination reason, final state, steps, and
episode data. The HDF5 writer remains outside the tool, so the same tool can be
used for dry runs, raw collection, evaluation, or planner execution.
