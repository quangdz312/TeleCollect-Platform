"""Cartesian servo primitives on top of robosuite's OSC_POSE controller.

Everything here speaks the controller's own language: a control step commands a
Cartesian *delta* from wherever the end effector currently is, scaled into the
[-1, 1] action range by the controller's output limits. Driving qpos directly
would produce a trajectory with no valid `actions`, which is useless as data.
"""
import numpy as np
from robosuite.utils.control_utils import orientation_error

from .compat import grip_site_id

CLOSE, OPEN = 1.0, -1.0


def body_geom_names(env, body_name):
    m = env.sim.model
    bid = m.body_name2id(body_name)
    return [m.geom_id2name(i) for i in range(m.ngeom)
            if m.geom_bodyid[i] == bid and m.geom_id2name(i) and not m.geom_id2name(i).endswith("_vis")]


class Servo:
    """Position/orientation servo. `on_step` is called after every control step."""

    def __init__(self, env, on_step=None, record=None, prober=None, action_transform=None):
        self.env = env
        self.on_step = on_step
        # `record` is an optional list collecting (state, action) pairs for dataset
        # export. Left as None it costs nothing, so evaluation runs stay fast.
        self.record = record
        # `prober` samples why the arm is or is not moving. Also optional, and
        # also free when absent - but attached, it is what turns "the hand stopped"
        # into "joint 5 is at its stop", which cost hours to work out by hand.
        self.prober = prober
        # Optional collection-only boundary. The solver continues to plan the
        # same action; a perturbation runtime may transform it immediately
        # before recording and env.step.
        self.action_transform = action_transform
        self.phase = ""
        self.goal_pos = None
        self.goal_mat = None
        self.geoms = None
        self.steps = 0
        self.aborted = False
        robot = env.robots[0]
        # robosuite 1.4 exposes a single `controller`; 1.5 uses a composite
        # controller with the arm controller stored under the `right` part.
        c = getattr(robot, "controller", None)
        if c is None:
            c = robot.composite_controller.part_controllers["right"]
        self.pos_scale = np.asarray(c.output_max[:3], dtype=float)
        self.rot_scale = np.asarray(c.output_max[3:], dtype=float)
        self._site = grip_site_id(env.sim.model)

    # ------------------------------------------------------------ state
    @property
    def eef_pos(self):
        return self.env.sim.data.site_xpos[self._site].copy()

    @property
    def eef_mat(self):
        return self.env.sim.data.site_xmat[self._site].reshape(3, 3).copy()

    def grasped(self, geoms):
        return bool(self.env._check_grasp(gripper=self.env.robots[0].gripper, object_geoms=geoms))

    # ------------------------------------------------------------ acting
    def act(self, dpos=None, drot=None, grip=OPEN):
        """One control step. `dpos` in metres, `drot` as an axis-angle vector."""
        a = np.zeros(self.env.action_dim)
        if dpos is not None:
            a[:3] = np.clip(np.asarray(dpos) / self.pos_scale, -1.0, 1.0)
        if drot is not None:
            a[3:6] = np.clip(np.asarray(drot) / self.rot_scale, -1.0, 1.0)
        a[6] = grip
        executed = (
            a
            if self.action_transform is None
            else np.asarray(self.action_transform(a, self.steps, phase=self.phase))
        )
        if executed.shape != (self.env.action_dim,) or not np.isfinite(executed).all():
            raise ValueError(f"Invalid transformed action shape/value: {executed.shape}")
        # Record the state BEFORE stepping and the action that was applied to it,
        # so (states[i], actions[i]) is the pair robomimic expects. Recording the
        # post-step state instead silently shifts the dataset by one control step.
        if self.record is not None:
            self.record.append((
                np.array(self.env.sim.get_state().flatten()), executed.copy(),
            ))
        self.env.step(executed)
        self.steps += 1
        if self.prober is not None:
            self.prober.sample(phase=self.phase, goal_pos=self.goal_pos,
                               goal_mat=self.goal_mat, action=executed,
                               grasped=(self.geoms is not None
                                        and self.grasped(self.geoms)))
        if self.on_step is not None and self.on_step() is False:
            self.aborted = True
        return executed

    def hold(self, n, grip=OPEN):
        for _ in range(n):
            self.act(grip=grip)
            if self.aborted:
                return

    def set_gripper(self, close, n=18):
        """Gripper commands are not instantaneous - they must be held to take effect."""
        self.hold(n, grip=CLOSE if close else OPEN)

    # ------------------------------------------------------------ servo
    def move_to(self, pos=None, mat=None, grip=OPEN, pos_tol=2e-3, rot_tol=0.04,
                max_steps=150, gain=0.8, settle=3):
        """Servo to a Cartesian target. Returns True once inside tolerance."""
        # Publish the goal so each recorded step knows what it was aiming at;
        # without it a probe can say the hand stopped but not whether it stopped
        # short of anything.
        self.goal_pos, self.goal_mat = pos, mat
        ok_for = 0
        for _ in range(max_steps):
            dp = None if pos is None else (np.asarray(pos) - self.eef_pos) * gain
            dr = None if mat is None else orientation_error(np.asarray(mat), self.eef_mat) * gain
            pe = 0.0 if pos is None else np.linalg.norm(np.asarray(pos) - self.eef_pos)
            re = 0.0 if mat is None else np.linalg.norm(orientation_error(np.asarray(mat), self.eef_mat))
            if pe <= pos_tol and re <= rot_tol:
                ok_for += 1
                if ok_for >= settle:
                    return True
            else:
                ok_for = 0
            self.act(dp, dr, grip)
            if self.aborted:
                return False
        return False

    def move_along(self, direction, distance, grip=OPEN, step=0.004, **kw):
        """Straight-line relative move, used for approach and retreat."""
        d = np.asarray(direction, dtype=float)
        d /= np.linalg.norm(d)
        return self.move_to(self.eef_pos + d * distance, self.eef_mat, grip=grip,
                            max_steps=int(abs(distance) / step) + 40, **kw)


def grasp_mat(approach, closing):
    """Gripper orientation matrix from an approach direction and a closing axis.

    Measured on the Panda in robosuite 1.4.1: the grip site's +z points out of
    the palm (approach) and its +x is the finger closing axis. The closing axis
    is +x, not +y -- getting this wrong contorts the arm and misses every grasp.
    """
    z = np.asarray(approach, dtype=float)
    z /= np.linalg.norm(z)
    x = np.asarray(closing, dtype=float)
    x = x - np.dot(x, z) * z  # keep it perpendicular to the approach
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])
