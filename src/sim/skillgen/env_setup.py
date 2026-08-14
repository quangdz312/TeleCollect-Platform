"""ToolHang environment construction.

The three Windows patches this depends on (mujoco.dll copy, macros_private.py,
mujoco==3.1.6 pin) are documented in SETUP.md and are applied to the venv, not here.
"""
import robosuite as suite

from .compat import seed_env

try:
    # robosuite <= 1.4 exposed this helper under the generic name. In 1.5.x
    # the equivalent single-arm API is load_part_controller_config.
    from robosuite.controllers import load_controller_config
except ImportError:
    from robosuite.controllers import load_part_controller_config

    def load_controller_config(*, default_controller=None, **kwargs):
        return load_part_controller_config(
            default_controller=default_controller,
            **kwargs,
        )

try:
    from robosuite.controllers import load_composite_controller_config
except ImportError:
    load_composite_controller_config = None


def _widen(sampler, extra_xy, extra_yaw):
    """Grow a placement sampler's ranges symmetrically about their own centre.

    Widening rather than replacing keeps whatever the task authors chose as the
    sensible middle of the workspace - the ranges are not symmetric about the
    origin, and re-centring them on zero would move objects somewhere the task
    was never designed around.
    """
    if extra_xy:
        cx = 0.5 * (sampler.x_range[0] + sampler.x_range[1])
        cy = 0.5 * (sampler.y_range[0] + sampler.y_range[1])
        hx = 0.5 * (sampler.x_range[1] - sampler.x_range[0]) + extra_xy
        hy = 0.5 * (sampler.y_range[1] - sampler.y_range[0]) + extra_xy
        sampler.x_range = [cx - hx, cx + hx]
        sampler.y_range = [cy - hy, cy + hy]
    if extra_yaw and getattr(sampler, "rotation", None) is not None:
        r = sampler.rotation
        cr = 0.5 * (r[0] + r[1])
        hr = 0.5 * (r[1] - r[0]) + extra_yaw
        sampler.rotation = [cr - hr, cr + hr]


def make_env(render=False, control_freq=20, horizon=1500, seed=None,
             offscreen=False, stand_jitter=0.0, stand_yaw_jitter=0.0,
             frame_extra=0.0, tool_extra=0.0, yaw_extra=0.0):
    """ToolHang with OSC_POSE. `render=True` opens the on-screen MuJoCo window.

    `frame_extra` / `tool_extra` widen the frame and wrench placement ranges by
    that many metres on each side; `yaw_extra` widens both rotations by that many
    radians each way. The defaults leave robosuite's own ranges untouched.

    Measured defaults, for calibration: the stand is pinned (x, y and rotation
    all zero-width), while the frame and tool each get a 40 mm x 40 mm box and
    ~20 deg of yaw - which across 20 seeds moved them only +-18 mm. A success
    rate over those seeds therefore measures robustness to small jitter, not
    generalisation.
    """
    if load_composite_controller_config is not None:
        cfg = load_composite_controller_config(controller="BASIC")
        arm_cfg = cfg["body_parts"]["right"]
    else:
        cfg = load_controller_config(default_controller="OSC_POSE")
        arm_cfg = cfg
    # Absolute-ish tracking is easier to script than raw deltas; keep deltas
    # (robomimic-native) but widen the per-step range so a servo step is not clipped.
    arm_cfg["control_delta"] = True
    arm_cfg["kp"] = 150
    arm_cfg["input_max"] = 1.0
    arm_cfg["input_min"] = -1.0
    arm_cfg["output_max"] = [0.05, 0.05, 0.05, 0.5, 0.5, 0.5]
    arm_cfg["output_min"] = [-0.05, -0.05, -0.05, -0.5, -0.5, -0.5]

    env = suite.make(
        "ToolHang",
        robots="Panda",
        controller_configs=cfg,
        has_renderer=render,
        has_offscreen_renderer=offscreen,
        use_camera_obs=False,
        render_camera="sideview" if render else None,
        control_freq=control_freq,
        horizon=horizon,
        ignore_done=True,
        hard_reset=False,
    )

    samplers = getattr(getattr(env, "placement_initializer", None), "samplers", {})
    sampler = samplers.get("standObjectSampler")
    if sampler is not None:
        if stand_jitter != 0.0:
            x0 = 0.5 * (sampler.x_range[0] + sampler.x_range[1])
            y0 = 0.5 * (sampler.y_range[0] + sampler.y_range[1])
            sampler.x_range = [x0 - stand_jitter, x0 + stand_jitter]
            sampler.y_range = [y0 - stand_jitter, y0 + stand_jitter]
        if stand_yaw_jitter != 0.0:
            sampler.rotation = [-stand_yaw_jitter, stand_yaw_jitter]

    for key, extra in (("frameObjectSampler", frame_extra),
                       ("toolObjectSampler", tool_extra)):
        s = samplers.get(key)
        if s is not None and (extra or yaw_extra):
            _widen(s, extra, yaw_extra)

    if seed is not None:
        seed_env(env, seed)
        env.reset()
    return env


def reset_and_settle(env, seed=None, n=80):
    """Reset, then let the scene fall and come to rest before anything reads a pose.

    Objects spawn a few centimetres in the air. Reading an object pose straight
    after reset gives a target that is ~30 mm too high by the time the arm
    arrives, which silently misses every grasp.
    """
    if seed is not None:
        seed_env(env, seed)
    env.reset()
    for _ in range(n):
        env.sim.step()
    env.sim.forward()
    return env
