# ToolHang integration

ToolHang has been added to the simulator task catalogue as `tool_hang`.
The integration deliberately uses the validated implementation in the
companion folder `D:\VIN_AI_TC\Test` through `src/sim/tool_hang.py`.

## Current scope

- Stage 1 is available: insert the hook frame into the stand.
- Success uses the geometric seated check, including depth, lateral error, and
  tilt. The loose robosuite predicate is not used as the final truth.
- The validated baseline is 20/20 on a fixed stand and 92% with +/-30 mm stand
  jitter, according to the supplied Test reports.
- Stage 2 (hang the wrench) remains blocked. The Test documentation identifies
  the required `tool_hole2` grasp/reachability work; it is not exposed as a
  complete production task yet.
- ToolHang is included in the shared scripted review dataset through a
  Stage-1-specific adapter. It currently supports only the `clean` preset;
  generic perturbation qualities remain disabled until calibration exists.

## Running it

The default companion root is `D:\VIN_AI_TC\Test`. Override it with
`TELECOLLECT_TOOLHANG_ROOT` when moving the skill project:

```powershell
python scripts/run_toolhang_stage1.py --eval 20
python scripts/run_toolhang_stage1.py --dataset --n 25 --stand-jitter 0.03
```

The main project environment must be installed from `requirements.txt`
(`robosuite 1.5.2` in the current lock state). The companion environment is
only the source of the ToolHang skill and its validated reports; do not use its
older robosuite 1.4.x interpreter to launch the backend.
