"""robosuite version compatibility for the vendored skillgen code.

The upstream skill was validated on robosuite 1.4.1, which names the Panda's
grip site `gripper0_grip_site`. 1.5 names single-arm Panda sites with a
right-arm component suffix, so the same site is `gripper0_right_grip_site`.
That is the only naming difference the skill touches: joint names
(`robot0_joint1..7`) and all body names are unchanged between the two.

The other 1.4 -> 1.5 break is where reset randomness comes from; see
`seed_env`.
"""
import numpy as np

GRIP_SITE_NAMES = ("gripper0_grip_site", "gripper0_right_grip_site")


def grip_site_id(model):
    """Return the grip site id, whichever of the two names this build uses."""
    for name in GRIP_SITE_NAMES:
        if name in model.site_names:
            return model.site_name2id(name)
    raise RuntimeError(
        f"No grip site found; tried {', '.join(GRIP_SITE_NAMES)}. "
        "Unsupported robosuite version or robot."
    )


def seed_env(env, seed):
    """Seed every draw robosuite makes during `env.reset()`, on 1.4 and on 1.5.

    1.4 sampled object placements and the robot's initialisation noise from
    numpy's legacy global RNG, so `np.random.seed(seed)` alone was enough. 1.5
    gives each environment its own generator -- `environments/base.py`:
    `self.rng = np.random.default_rng(seed)` -- and hands *that* to the
    placement samplers (`tool_hang.py`: `UniformRandomSampler(..., rng=self.rng)`)
    and to `robot.reset(..., rng=self.rng)`. `np.random.seed` no longer reaches
    any of them, so on 1.5 the vendored skill's seed argument had no effect at
    all: measured over 16 probes at seed 3, every settled state was different
    (max |dqpos| up to 0.38 rad). After this call, all 16 are bit-identical.

    The generator is reseeded IN PLACE. The samplers are constructed with
    `rng=self.rng` and hold that reference for the life of the environment, so
    rebinding `env.rng` would leave them drawing from the old stream.

    What this does NOT cover: `feasibility.holdable`, `feasibility.best_of` and
    `graspplan.plan_grasp` each fall back to a fresh entropy-seeded
    `np.random.default_rng()` when called with `rng=None`. Stage 2 passes its
    own per-episode generator, and the other two functions are never called, so
    nothing in the stage1+stage2 path is affected today -- but a future caller
    that omits `rng` would reintroduce exactly this bug.
    """
    np.random.seed(seed)
    rng = getattr(env, "rng", None)
    if rng is not None:
        rng.bit_generator.state = np.random.default_rng(seed).bit_generator.state
