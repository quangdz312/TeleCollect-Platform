# ToolHang integration

ToolHang has been added to the simulator task catalogue as `tool_hang`.
The integration uses the vendored implementation under `src/sim/skillgen`
through `src/sim/tool_hang.py`.

## Current scope

- Stage 1 inserts the hook frame into the stand and Stage 2 hangs the wrench.
- Full success requires both RoboSuite environment predicates.
- Training observations use RoboSuite's native 44-D `object-state`.
- ToolHang is included in the shared scripted review dataset through a
  full-task adapter. It supports `clean`, `good`, `medium`, and `poor` through
  the shared phase-decayed perturbation runtime. The non-clean doses remain a
  candidate profile until calibration is complete.

## Running it

The project environment must be installed from `requirements.txt`
(`robosuite 1.5.2` in the current lock state). Collection is available from the
Collect data page or through the shared scripted collection runner.
