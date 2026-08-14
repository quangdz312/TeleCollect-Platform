# TeleCollect Phase E — completion report

Date: 2026-08-09
Branch: `feat/robosuite-panda`
Scope: Lift, Can, Square. Tool Hang remains out of v1.0.

> **Historical record — read the date.** This is a point-in-time completion
> report for Phase E and is kept as it was written; it is not a description of
> the system today. Two statements above have since been overtaken:
>
> - *"Tool Hang remains out of v1.0"* is **no longer true**. ToolHang is
>   integrated as the full two-stage task — see
>   [`toolhang_integration.md`](toolhang_integration.md). Its perturbation
>   *profile* is still pending, which is the part that remains accurate.
> - Open item 1 below ("seeded reset does not reproduce object placement")
>   **has been fixed for ToolHang** via `src/sim/skillgen/compat.py::seed_env`.
>   It is still open for Lift, Can and Square, for the reason the item gives.
>
> Current state lives in the repo `README.md` and in `docs/`.

Phase E takes the Phase D perturbation runtime and makes it reachable from the
user-facing collectors: pick a task and a quality, get a raw dataset whose
provenance says how it was made.

## What was delivered

### 1. Profile v1 published

- `docs/telecollect_quality_protocol_amendments.md` gains amendment
  TC-QP-2026-08-09-02 (poor band `0-55%`, minimum adjacent gap 5 points),
  appended below the existing amendment. Nothing earlier was edited.
- `docs/telecollect_profile_v1_decision.md` records the publication decision
  against the existing held-out evidence.
- `PROFILE_VERSION` is now `v1`. Doses are unchanged; the frozen candidate
  marker survives as `ResolvedProfile.candidate_profile_version` so any dataset
  can be traced back to the run that produced its evidence.
- `assess_v1_quality_ordering()` implements the new gate.
  `assess_d2_quality_ordering()` is untouched and still reproduces the recorded
  D2 FAIL, so publishing v1 did not rewrite history. Both behaviours are
  pinned by `tests/sim/test_profile_v1_gate.py`.

### 2. One collection runner behind three CLIs

`src/sim/scripted_generation.py` is the shared runner. The three collector
scripts are now ~15-line wrappers that pass a task name. They gained
`--quality {clean,good,medium,poor}` with `clean` as default; every other
argument is unchanged.

`src/sim/perturbations/collection.py` holds the landmark table, the retry debug
keys and the runtime factory. `calibration.py` now imports them instead of
keeping its own copies, so calibration and collection cannot drift apart — a
test asserts the two agree.

### 3. Provenance in HDF5

The core Robomimic schema is unchanged: `actions` is still the executed action
passed to `env.step()`, and no observation key was added or renamed. Provenance
is additive `telecollect_*` attributes on `data` (task, tool, requested quality,
profile version, candidate version, amendment, noise scale, base seed, task
code, stream code, coverage, landmarks) and on each `demo_*` (episode index,
environment seed, sampled variation as JSON, retry count, outcome, success,
length, terminal reason, failure stage, terminal phase).

Datasets written by the legacy path carry no `telecollect_*` attributes and
still validate.

### 4. Documentation

`docs/scripted_collection_quality.md` is the shared contract page; the three
per-task collection docs link to it and show a `--quality` example.

## Verification

`.venv/bin/python -m pytest tests/ -q` — 121 passed, 2 xfailed, 0 failed
(80 of those were the pre-existing sim suite, unchanged).
`ruff check src/ tests/` — 44 findings, the same count as before this work.

New coverage:

- `tests/sim/test_scripted_generation.py` (24 tests) — shared configuration,
  Tool Hang rejection, CLI contract, seed contract per quality, variation
  reproducibility from provenance alone, retention of failed and horizon
  episodes, refusal to overwrite, HDF5 provenance round-trip, legacy writer
  behaviour.
- `tests/sim/test_scripted_generation_mujoco.py` (5 passed, 2 xfailed) — real
  Robosuite Lift: clean is an exact action and observation passthrough, noisy
  actions stay finite and in bounds, arm noise never reaches the gripper
  dimension, `next_obs[t] == obs[t+1]` holds under noise, and the biased
  landmark reaches the operator while proprioception does not.
- `tests/sim/test_profile_v1_gate.py` (8 tests) — the held-out figures pass the
  v1 gate and still fail the D2 gate; no dose was retuned.

### 3 x 4 smoke

Run through the collector CLIs themselves, two episodes each at base seed 900,
and validated with `scripts/validate_robomimic_dataset.py` against the genuine
robomimic v1.5 reference dataset for each task.

```bash
python scripts/collect_scripted_<task>.py --quality <q> --episodes 2 --seed 900 --output <path>
python scripts/validate_robomimic_dataset.py <path> --reference data/datasets/<task>/ph/low_dim_v15.hdf5
```

| Task | Quality | Episodes | Success | Samples | Schema |
|---|---|---:|---:|---:|---|
| lift | clean | 2 | 2 | 116 | VALID |
| lift | good | 2 | 2 | 112 | VALID |
| lift | medium | 2 | 1 | 214 | VALID |
| lift | poor | 2 | 1 | 214 | VALID |
| can | clean | 2 | 2 | 336 | VALID |
| can | good | 2 | 2 | 342 | VALID |
| can | medium | 2 | 1 | 546 | VALID |
| can | poor | 2 | 1 | 455 | VALID |
| square | clean | 2 | 1 | 431 | VALID |
| square | good | 2 | 2 | 330 | VALID |
| square | medium | 2 | 0 | 358 | VALID |
| square | poor | 2 | 2 | 440 | VALID |

12 of 12 valid, no errors. `profile_version=v1` appeared in every run.

An earlier pass over the same twelve combinations, run before the reference
datasets were available, additionally checked per-episode that every action was
finite, shape `(7,)` and in bounds, that `next_obs[t] == obs[t+1]` held
throughout, and that every sampled variation could be rebuilt exactly from its
recorded provenance. All twelve passed.

Two episodes per cell is a plumbing check, not a quality measurement. The
success column is far too small a sample to say anything about the bands —
`square/medium` at 0/2 and `square/poor` at 2/2 is exactly the inversion a
2-episode cell produces, and `square/clean` at 1/2 is consistent with the
recorded 90/100 clean baseline. Failures were kept as collected.

No dataset from the smoke was committed.

### Reference datasets

`data/datasets/{lift,can,square}/ph/low_dim_v15.hdf5` were missing from this
checkout — gitignored payload with no DVC pointer. They are the official
robomimic v1.5 proficient-human datasets and were downloaded from the upstream
HuggingFace repo `robomimic/robomimic_datasets`:

```bash
for t in lift can square; do
  mkdir -p "data/datasets/$t/ph"
  curl -L -o "data/datasets/$t/ph/low_dim_v15.hdf5" \
    "https://huggingface.co/datasets/robomimic/robomimic_datasets/resolve/main/v1.5/$t/ph/low_dim_v15.hdf5"
done
```

Sizes: lift 21 MB, can 47 MB, square 51 MB. They stay gitignored. Worth adding
this command to the setup docs so the next person does not hit the same wall.

## Open items for the project owner

1. **Seeded reset does not reproduce object placement.** `reset_*_environment()`
   rebinds `env.rng`, but the Robosuite placement sampler holds the generator
   captured when the environment was built, and `hard_reset=False` never
   rebuilds it. Calling the same seed three times gives three different cube
   positions. Consequences:
   - the sampled variation is reproducible from provenance, but a whole rollout
     is not;
   - Phase D calibration compared good/medium/poor across *different* initial
     states, not matched ones, which is not what the protocol assumed.

   The fix is one line per environment module — reseed in place so every holder
   of the reference sees it:

   ```python
   env.rng.bit_generator.state = np.random.default_rng(seed).bit_generator.state
   ```

   This was verified to work. It was **not applied**, because it changes every
   clean trajectory and therefore invalidates the Phase A baselines and the D2
   held-out figures that profile v1 was just published against. Applying it is a
   project decision: it means re-running the clean baselines and the calibration
   bank. Two tests are marked `xfail(strict=True)` against this defect, so
   whoever fixes it will be told immediately.

2. **Phase D2 raw artifacts are absent.** `tmp/telecollect_phase_d2_20260809_run01/`
   was untracked working-tree output on the collecting operator's machine. The
   v1 decision cites the held-out figures as transcribed in the handover
   document; the raw metrics should be obtained from that operator if anyone
   needs to re-derive the assessment.
