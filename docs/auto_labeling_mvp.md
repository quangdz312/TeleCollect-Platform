# Auto-labeling MVP

An implementation of `auto-labeling.md` for scripted, perturbed collection.

It decides nothing yet. It scores every episode, says *why*, suggests where to
trim, and records what a human decided. Those recorded decisions are the input
the two thresholds are later derived from — which is why this order and not the
other one.

```text
score = (product of hard checks) x (1 - worst penalty)
```

## The loop, in a browser

```bash
make dev             # backend + frontend, one command; Ctrl-C stops both
                     # then open http://localhost:3000/scripted
```

Ports already taken: `make dev API_PORT=8010 WEB_PORT=3010`. Both are checked
before anything starts, because a half-started stack is harder to read than one
clear error.

One page, three sections matching the three steps below: pick a task and a
noise preset and press Start; watch each episode and mark it Đạt/Loại with the
reasons; ask whether the thresholds are justified yet.

It lives in the same frontend as manual teleoperation — `/teleop` is the human
driving, `/scripted` is the machine driving and a human judging. One app, two
collection paths, deliberately separate storage formats.

Collection and video rendering are blocking simulator work, so both run on
their own single-worker threads and the page polls for progress. They get
separate pools deliberately — a video render must not queue behind a
five-minute collection.

`make dev` runs the backend without `--reload` on purpose: an editor save
mid-collection would restart the process and kill the batch. `make run` is the
reloading variant, fine for everything except an active collection.

### The workspace

Everything lands in one directory, `review_dir` (default `data/review/`):

```text
datasets/  one .hdf5 per (task, quality, seed)
videos/    rendered playback, cached, safe to delete
scores.jsonl   derived — rewritten in full on every collection
labels.jsonl   append-only — the only file here that cannot be regenerated
```

The dataset filename is derived from the request rather than a timestamp, so
re-running the same batch collides with itself instead of quietly producing a
second copy of the same deterministic episodes for someone to review twice. The
UI suggests the next unused seed.

Every collection rescores the **whole** corpus, because three penalties are
defined relative to other episodes of the same task — adding a batch changes
the scores of the batches already there.

## The loop, in a terminal

```bash
# 1. Score. No thresholds passed, so everything routes to needs_review.
python scripts/score_episodes.py data/generated --output review/scores.jsonl

# 2. Review. Renders each episode on demand, appends decisions.
python scripts/review_cli.py \
  --scores review/scores.jsonl --labels review/labels.jsonl --video-dir review/videos

# 3. Ask whether a gate is justified yet. It usually is not.
python scripts/shadow_report.py --scores review/scores.jsonl --labels review/labels.jsonl
```

`render_episode.py` renders without reviewing, if you just want the videos.

Step 2 is resumable: episodes already in the labels file are skipped, so it can
be stopped with `q` and picked up later.

Both paths write the same `labels.jsonl` schema and use the same reason
vocabulary, so a corpus reviewed half in each is still one corpus.

## Why a reason and not just a verdict

The verdict is still the only field the thresholds are derived from — nothing
about the maths changed. What the reasons buy is the ability to answer *why* a
threshold came out strange, which a free-text note cannot be aggregated into.

Each reason names the check or penalty meant to catch it. When a reviewer ticks
`bad_grasp` on an episode where `E_skill` returned 1, the check is too lenient,
and the fix belongs in the check rather than in the threshold. The report lists
those cases under **checks that missed**. Penalties are deliberately not
asserted on this way: a penalty is a tuned estimate, and disagreeing with one is
a calibration matter, not a defect.

A rejection requires at least one reason or a note. An approval requires
nothing further.

### Video, without re-collecting anything

Collection runs with the offscreen renderer off, so no images were recorded. The
full MuJoCo state of every frame was, along with the scene XML, so
`src/labeling/playback.py` replays the episode and renders it afterwards.
Restoring a state and rendering reproduces the original frame exactly, which was
verified against a live rollout. Frames the suggested trim would cut are dimmed
and marked `TRIM`.

### Blind by default

A reviewer who sees the machine's opinion first anchors on it, and the labels
stop being usable for calibration. `review_cli.py` hides the score unless
`--show-score` is passed; the web API withholds it **server-side** unless the
client asks for `include_score=true`, because hiding it in JavaScript is not
hiding it — DevTools shows the response either way.

What is withheld is more than the score. `recorded_success` is the simulator's
own success flag, and the review yield is defined as how often the human
differed from exactly that flag; show it and the number collapses into a measure
of how often the reviewer copied it. The `provenance` block is worse still — it
names the outcome, the failure stage and the noise scale outright.

One leak is left open: `episode_id` still carries the batch name
(`lift_poor_seed0`), so the requested quality is inferable by anyone who looks.
Closing it needs an opaque per-episode review id, which is post-MVP. It is
recorded here so nobody assumes the blind mode is airtight.

## What is implemented

| Hard check | Status |
|---|---|
| `E_integrity` | frame counts, finite values, action shape and bounds, `num_samples` |
| `E_success` | the recorded success flag; the hold duration is **not** verifiable, see below |
| `E_skill` | gripper shut on the object, offset stable for k frames, object off the surface, and for Can/Square transported |
| `E_no_drop` | measures how far the object falls after each release |
| `E_stable_end` | reports *unavailable* on successful episodes, see below |
| `E_no_stray` | not implemented; needs a per-task definition of "outside the task" |
| `E_teleop` | not applicable to scripted collection |

All six applicable penalties are implemented (jerkiness, gripper toggles,
wandering path, unusual length, hitting limits, idle after trim). The choppy
teleop penalty and the per-operator penalty do not apply to a deterministic
scripted operator.

An unavailable check returns `None` and is **excluded from the product** rather
than counted as a pass or a failure.

## Calibration decisions, and why

The document's starting values are for human teleoperation. Three needed
changing once the real distribution was visible, which is what it says to do.

**Grasp radius is per task, and it is geometry.** The observation reports the
object's body origin, which is not where the gripper grips. Measured over frames
where an object was genuinely being carried: Lift 0.007-0.040 m, Can
0.030-0.037 m, Square **0.054-0.087 m** — Square's nut is held by a handle
offset from its origin. A single 0.06 m radius silently failed `E_skill` on
Square episodes that were fine.

**Saturation band widened from 0.02-0.15 to 0.35-0.75.** A scripted operator
commands near-full-scale arm deltas by construction, so even clean episodes
saturate: 0.03-0.16 on Lift, 0.04-0.45 on Can, 0.03-0.60 on Square. The original
band scored clean episodes at zero.

**Percentile and MAD penalties abstain below 30 episodes per task.** A
percentile is a statement about a distribution; on eight episodes it is not one,
and whichever episode happens to be the jerkiest lands at the 100th percentile
and gets penalised out of existence for being the worst of eight. They report
`insufficient_corpus` and contribute 0. Under-penalising is the safe direction —
a penalty can only ever push an episode toward review.

**Drop threshold is 0.15 m of fall after release.** Placing a nut on a peg costs
about 0.10 m on clean Square episodes; a release from carry height costs more.

## Two limitations worth knowing

**The success hold is not recorded.** `src/sim/tools/executor.py` breaks out of
the rollout on the first success frame, so no frames after it exist. `E_success`
therefore confirms that success fired but reports `hold_verified: false`, and
`E_stable_end` reports itself unavailable on every successful episode. Both
start working on their own once episodes record a hold; nothing here needs
changing. Adding the hold changes episode lengths and therefore the recorded
baselines, so it is a project decision, not a silent fix.

**The state after the final action lives only in `next_obs`.** Because success is
evaluated after stepping, the frame the task actually succeeded on is the one
that never appears in `obs`. `EpisodeArrays` exposes a `*_trajectory` view of
length `T + 1` for exactly this reason; reading `obs` alone makes every
successful Lift look like the cube barely moved. There is a regression test.

## Thresholds

None are set, and none should be until `shadow_report.py` says otherwise. It
requires, per the document: no approved episode below `tau_reject`, AUC at least
0.75, and enough episodes in the approve zone — 300 for a 1% wrong-approval
bound under the rule of three.

The two thresholds are one-sided and derived independently, so they can come out
in either order. When the populations overlap, `tau_reject < tau_approve` and the
span between them is the review queue. When the score separates them cleanly the
pair **crosses**, and the span between them contains no observed episode at all.
`decide()` routes that span to a human in both cases rather than letting
whichever rule is tested first win.

## Re-scoring

`scorer_version` is a hash of every threshold in the config. Change any of them
and the version changes, which is the signal to re-score. Scoring is a batch
operation and deterministic: same episodes, same version, same output.

If the robot, gripper, action scale or camera changes, the hard checks still
measure ground truth but the penalty bands and any thresholds do not. Bump the
version, re-score, re-derive, re-run shadow mode.
