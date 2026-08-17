# ToolHang integration

ToolHang has been added to the simulator task catalogue as `tool_hang`.
The skill implementation is vendored at `src/sim/skillgen/` (copied from the
companion folder `D:\VIN_AI_TC\Test`, see that package's `__init__.py` for
provenance) and reached through `src/sim/tool_hang.py`.

## Current scope

- The task is both stages: stage 1 inserts the hook frame into the stand,
  stage 2 threads the wrench onto the hook. Stage 2 runs only when stage 1
  succeeded.
- Success is robosuite's own physical check, not the skill's geometric one:
  `_check_frame_assembled()` for stage 1, `_check_tool_on_frame()` for stage 2,
  both for the full task. The skill's `EpisodeResult.success` gates seating
  depth at 40 mm where correct assembly needs ~130 mm, so it reports success on
  a rod that jammed part-way; it is recorded as a diagnostic only.
- Measured on robosuite 1.5.2, seeds 0-9, `max_attempts=1`, wide placement range
  (`frame_extra=0.04`, `tool_extra=0.04`, `yaw_extra=0.35`): stage 1 10/10,
  stage 2 9/10 (seed 0 fails), ~13 s per episode. This is now a fact about
  those ten seeds rather than a sample: the same sweep repeated three times is
  bit-identical. The earlier 8/10 was one draw from an unseeded distribution --
  see below.
- Failed episodes are collected and written like any other, carrying their
  failure kind and terminal phase. Knowing where and how an episode failed is
  the thing simulated collection provides and human teleoperation cannot.
- Per-step telemetry is written to a JSONL sidecar next to the HDF5
  (`<output>.trace.jsonl`), one line per episode. This is provisional; the
  trace belongs in the HDF5, but that means changing the writer lift/can/square
  share, and the scoring work that will consume it is deferred.
- ToolHang currently supports only the `clean` preset; generic perturbation
  qualities remain disabled until calibration exists.

## Naming: `tool_hang` vs `tool_hang_stage1`

Two different strings, deliberately, and they are not interchangeable:

| Constant | Value | Role |
|---|---|---|
| task name | `tool_hang` | The task in the registry and in the API |
| `TOOLHANG_TOOL_NAME` | `tool_hang_stage1` | **Stored dataset identifier.** Written into HDF5 attrs and `scores.jsonl` |
| `TOOLHANG_DISPLAY_NAME` | `ToolHang (stage 1 + stage 2)` | **What a human reads.** Use this in any UI or report |
| `TOOLHANG_PROFILE` | `tool-hang-stage1-baseline` | Recorded as `profile_version` on every episode |

`tool_hang_stage1` is historical — it dates from when only stage 1 existed. It is
kept because it identifies data already collected, and because
`src/labeling/features.py` and `src/labeling/rule_engine.py` map it back to the
`tool_hang` task. It is **not** a registry key: ToolHang has its own collection
path and is not in `build_default_registry()`.

Never show `tool_hang_stage1` to a user. The API sends `tool_label` alongside the
raw `tool` for exactly this reason (`src/api/labeling.py`).

One string that is *correctly* stage-specific and should not be changed:
`checks.py`'s `task_specific: "tool_hang_stage1_frame_transport"` really does
only measure stage-1 frame transport.

## Review cameras

Three cameras, and the same framing is used in Teleop and in review, so what the
operator saw is what the reviewer sees:

| Camera | Source |
|---|---|
| `review_front` | Installed into the scene by `src/sim/review_camera.py` |
| `birdview` | Stock robosuite |
| `robot0_eye_in_hand` | Stock robosuite wrist camera |

`review_front` exists because ToolHang's stock `frontview` looks along the table
and hides the frame, the stand and the wrench. The angle (azimuth 180,
elevation -15, distance 1.2 about `stand_root`) was chosen by eye from a sweep
and is installed as a real named camera so both the recorder and the playback
path can ask for it by name.

The mp4 is written **at collection time**, so opening an episode for review does
not wait for a render.

> Replay renders with `geomgroup[0] = 0` / `geomgroup[1] = 1`
> (`src/labeling/playback.py`). Robosuite sets this itself; a bare MuJoCo
> renderer defaults to drawing both groups, which paints the Panda's green
> collision capsules over its visual shells and makes the robot replay green.

## Seeds and reproducibility

A seed now reproduces an episode exactly, with no conditions: same seed and same
`*_extra` knobs give the same scene, the same step count and the same outcome,
on a fresh environment or on one that has already run other episodes.

That was not true before, and the handover document's promise
(`INTEGRATION.md` §4, §5.1) did not hold on this project's robosuite. robosuite
1.5 moved every reset draw off numpy's global RNG onto a per-environment
`np.random.default_rng(seed)` passed to the placement samplers and to
`robot.reset()`. The vendored skill seeds with `np.random.seed()`, which on 1.5
reaches none of them, so the seed argument had no effect at all — every reset
rolled a fresh scene. `src/sim/skillgen/compat.py::seed_env` reseeds the
environment's own generator in place; `env_setup.py` calls it from both
`make_env` and `reset_and_settle`.

Measured on robosuite 1.5.2, seed 3, wide range, before → after:

| Probe | Before | After |
|---|---|---|
| settled scene, 16 probes (fresh env / repeated resets / reordered episodes / after 600 random actions) | 16 distinct states, up to 0.38 rad apart | 1 state, bit-identical |
| full episode, fresh env ×5 | 5 step counts (1911–2392), 2 outcomes | 969→1723 steps every time |
| seed 3 preceded by nothing / by seed 2 / by seeds 2,5 | 3 step counts, one `grasp_missed` at 204 steps | identical to the isolated run |
| 10-seed sweep repeated ×3 | stage 1 9/10, 9/10, 10/10, failing seed [7], [6], [] | bit-identical, stage 1 10/10, stage 2 9/10 |

Seeds still select distinct scenes (0–5 give six different layouts), so the
seeding did not collapse the placement range.

Runtime cost is nil: 601 s before and 584 s after for the same 41-episode
experiment. Reseeding a bit generator is not a model rebuild.

Two consequences worth knowing. Any dataset collected before this change has
episodes whose recorded `seed` does not reproduce them, and a per-seed success
rate measured before it was a sample of a distribution rather than a fact.
`tests/test_tool_hang.py::test_same_seed_reproduces_the_same_settled_scene`
pins the property (opt-in, `TELECOLLECT_RUN_SIM_TESTS=1`).

Two things that were suspected and are *not* involved:

- The `hard_reset=False` that `make_env` passes to `suite.make` does not leak
  state between episodes. After 600 steps of random actions the next seeded
  reset is bit-identical to an isolated one. It should also stay `False`:
  `hard_reset=True` re-runs `_load_model`, which rebuilds
  `placement_initializer` and silently discards the widening the `*_extra`
  knobs apply (measured: frame `x_range` reverts from `[-0.10, 0.02]` to
  `[-0.06, -0.02]`).
- The three unseeded `np.random.default_rng()` fallbacks in `feasibility.py`
  (`holdable`, `best_of`) and `graspplan.py` (`plan_grasp`) are never reached.
  Instrumented over 41 real episodes, all three saw zero calls: `plan_grasp`
  and `best_of` have no caller at all, and `holdable`'s only caller
  (`Stage2._ring_upright_mat`) is itself unreachable. They are left verbatim
  and recorded as a hazard in `compat.seed_env`'s docstring.

## Running it

```powershell
python scripts/run_toolhang.py --output data/toolhang.hdf5 --n 10
python scripts/run_toolhang.py --output data/toolhang_wide.hdf5 --n 10 `
    --frame-extra 0.04 --tool-extra 0.04 --yaw-extra 0.35
```

The three `*_extra` knobs are the difficulty control surface and default to 0,
which is robosuite's own placement range. That range is narrow enough that every
seed converges to the same solution, so episode count at the defaults is not a
measure of data diversity; use the wide range above when diversity matters.

The main project environment must be installed from `requirements.txt`
(`robosuite 1.5.2` in the current lock state). The companion project is only the
upstream source of the vendored skill; do not use its older robosuite 1.4.x
interpreter to launch the backend.
