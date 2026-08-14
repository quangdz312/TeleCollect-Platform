"""Vendored copy of the ToolHang skill generator.

Source: the companion skill repository at `D:\\VIN_AI_TC\\Test`, package
`skillgen/`. Not a git checkout there, so the best available provenance is the
file timestamps of the copied sources, newest 2026-08-12; the handover document
`INTEGRATION.md` in that repo is dated 2026-08-13.

Vendored files: `env_setup.py`, `geometry.py`, `primitives.py`, `telemetry.py`,
`stage1.py`, `stage2.py`, `feasibility.py`, `graspplan.py`.

Omitted: `viewer.py`, `report.py`, `dataset.py` (superseded by this project's own
render and HDF5 pipeline), and `jointmove.py`, `probe.py` (never imported by
stage1 or stage2).

Modifications to the upstream sources, in full:

- Added `compat.py`, which resolves the Panda grip site under both robosuite
  1.4 (`gripper0_grip_site`) and 1.5 (`gripper0_right_grip_site`). Upstream was
  validated on 1.4.1; this project runs 1.5.2. The hardcoded lookups in
  `primitives.py`, `feasibility.py`, `graspplan.py` and `stage2.py` now call
  that helper, and `primitives.GRIP_SITE` plus its inline 1.5 fallback were
  folded into it.

- `env_setup.py` seeds through `compat.seed_env(env, seed)` instead of
  `np.random.seed(seed)`, in `make_env` and in `reset_and_settle`, and no longer
  imports numpy (nothing else in the file used it). Same reason as above:
  upstream was validated on robosuite 1.4, which drew placements and robot
  initialisation noise from numpy's global RNG. 1.5 draws them from a
  per-environment `np.random.default_rng(seed)` that the global seed cannot
  reach, so on 1.5 the seed argument did nothing at all.

  Measured on this project, seed 3, wide placement range: before the change,
  16 settled states across fresh envs, repeated resets and reordered episodes
  were 16 distinct states (max |dqpos| up to 0.38 rad, |dqvel| up to 6.8);
  after, all 16 are bit-identical, while seeds 0-5 still give 6 distinct
  scenes. Full episodes: seed 3 run five times on a fresh env gave 5 different
  step counts before and 1 after; a 10-seed sweep repeated three times gave
  three different results before and three bit-identical ones after. Runtime
  cost is nil -- reseeding a bit generator is not a model rebuild.
  `compat.seed_env` carries the full reasoning and the residual hazard.

Everything else is copied verbatim. These files encode physical tuning that was
measured, not derived; do not reformat or refactor them.
"""
