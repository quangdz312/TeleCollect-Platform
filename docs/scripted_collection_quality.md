# Scripted collection quality presets

All three collectors (`collect_scripted_lift.py`, `collect_scripted_can.py`,
`collect_scripted_square.py`) are thin wrappers over
`src/sim/scripted_generation.py`. They share one argument contract, one profile
resolution path and one runtime factory, so a dataset collected from the command
line is produced exactly the way the calibration bank was.

## Contract

```text
task:     lift | can | square | tool_hang
quality:  clean | good | medium | poor   default clean
          (tool_hang supports clean only)
episodes: positive integer
seed:     integer 0..2147483647
output:   path to a new file
```

```bash
python scripts/collect_scripted_can.py --quality medium --episodes 100 --seed 42 \
  --output data/generated/can/raw/can_medium.hdf5
```

`--quality clean` keeps the legacy baseline behaviour: the sampled variation is
zero, so every executed action is the operator's plan clipped to the action
bounds and the operator's observation is passed through untouched.

Tool Hang uses the same episode-scoped runtime with an arm-only candidate
profile. Its semantic faults and perception bias remain disabled until a
separate calibration round is complete.

Its difficulty control surface is the three placement knobs
(`--frame-extra`, `--tool-extra`, `--yaw-extra`) instead — see
[`toolhang_integration.md`](toolhang_integration.md).

## Presets

Profile `v1`, published under amendment TC-QP-2026-08-09-02. Doses are the
Phase D2 frozen candidate values, unchanged.

| Task | Clean | Good | Medium | Poor |
|---|---:|---:|---:|---:|
| Lift | 0.00 | 0.25 | 1.70 | 2.50 |
| Can | 0.00 | 0.25 | 1.10 | 1.20 |
| Square | 0.00 | 0.18 | 0.90 | 1.30 |
| Tool Hang (candidate) | 0.00 | 0.20 | 0.60 | 1.00 |

A preset name is a *request*, not an observed quality label. A batch requested
as `medium` whose metrics land in the `good` band is kept as collected and the
report records the mismatch. Nothing is resampled, relabelled or dropped to
reach a success rate, and failed or horizon episodes are written like any other.

## Seeds

The environment seed is unchanged from the legacy contract:

```text
environment_seed = base_seed + episode_index
```

Noise uses a separate stream so quality never changes the environment seed:

```text
SeedSequence([base_seed, task_code, episode_index, 1])
```

Task codes are Lift 1, Can 2, Square 3. The variation is sampled once at reset
and is immutable for the episode; no RNG is called per timestep.

> Known limitation, **lift / can / square only**: the environment seed does not
> reproduce object placement. `reset_*_environment` *rebinds* `env.rng` (e.g.
> `src/sim/lift_env.py:73`), but the Robosuite placement sampler holds the
> generator captured when the environment was built, and `hard_reset=False`
> never rebuilds it. Variations are fully reproducible from provenance; whole
> rollouts are not. See
> `tests/sim/test_scripted_generation_mujoco.py::test_seeded_reset_reproduces_object_placement`.
>
> **ToolHang does not have this defect.** It reseeds the environment's own
> generator *in place* via `src/sim/skillgen/compat.py::seed_env`, so a seed
> reproduces an episode bit-identically. Applying the same fix to the other
> three would invalidate the Phase A baselines and the D2 held-out figures that
> profile v1 was published against, which is why it has not been done — see
> `docs/telecollect_phase_e_report.md` §"Open items".

## Landmarks

Noise biases only the landmarks the operator plans from:

| Task | Position | Orientation |
|---|---|---|
| Lift | `cube_pos` | none |
| Can | `Can_pos` | none |
| Square | `SquareNut_pos` | `SquareNut_quat` |

Robot proprioception is never biased, and the biased view is operator-only: the
core training observations written to HDF5 are the environment's own.

## Provenance in HDF5

The core Robomimic schema is unchanged. `actions` remains the executed action
passed to `env.step()`. Provenance is added as extra attributes.

On `data`:

`telecollect_schema_version`, `telecollect_task`, `telecollect_tool_name`,
`telecollect_requested_quality`, `telecollect_profile_version`,
`telecollect_candidate_profile_version`, `telecollect_acceptance_amendment`,
`telecollect_noise_scale`, `telecollect_base_seed`, `telecollect_task_code`,
`telecollect_stream_code`, `telecollect_coverage`,
`telecollect_position_landmarks`, `telecollect_orientation_landmarks`.

On each `demo_*`, additionally:

`telecollect_episode_index`, `telecollect_environment_seed`,
`telecollect_sampled_variation` (JSON), `telecollect_retry_count`,
`telecollect_outcome`, `telecollect_success`, `telecollect_episode_length`,
`telecollect_terminal_reason`, `telecollect_failure_stage`,
`telecollect_terminal_phase`.

The recorded seeds are enough to rebuild the variation:

```python
from src.sim.perturbations.collection import build_runtime_for

runtime = build_runtime_for(task, quality, env.action_spec,
                            base_seed=base_seed, episode_index=episode_index)
runtime.reset_episode()   # equals telecollect_sampled_variation
```

Datasets written by the legacy path carry no `telecollect_*` attributes and
still validate.
