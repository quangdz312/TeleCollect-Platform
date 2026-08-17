"""Measured ToolHang geometry (T1) and pose math for stage 1.

All numbers verified against the live robosuite 1.4.1 model, not the XML comments:
the XML `mount_width` of 12 mm is the OUTER cavity width; the clear inner span is 10 mm.
"""
from dataclasses import dataclass

import numpy as np
import robosuite.utils.transform_utils as T

# ---------------------------------------------------------------- measured
CAVITY_SPAN = 0.010  # nominal clear inner span, both axes

ROD_WIDTH = 0.0075  # square cross-section of the vertical frame
TIP_MIN_DIA = 0.0020  # cone tip
TIP_MAX_DIA = 0.0065
TIP_CONE_LEN = 0.0187

# offsets in the frame body frame (frame_root), from T1
TIP_LOCAL = np.array([0.0438, 0.0, -0.1345])  # frame_tip_site
MOUNT_LOCAL = np.array([0.0438, 0.0, -0.0900])  # frame_mount_site
GRIP_LOCAL = np.array([0.0437, 0.0, 0.0525])  # centre of the Ø25.4 mm ceramic grip
GRIP_RADIUS = 0.0127
GRIP_HALF_LEN = 0.03175
ROD_AXIS_LOCAL = np.array([0.0, 0.0, 1.0])  # tip -> mount direction

# ------------------------------------------------------------- tolerances
# What actually has to land where. The cone means first contact only needs the
# 2 mm tip inside the 10 mm mouth, which is far looser than the 7.5 mm rod fit.
CAPTURE_TOL = (CAVITY_SPAN - TIP_MIN_DIA) / 2.0  # 4.0 mm  - tip entering the mouth
SEATED_TOL = (CAVITY_SPAN - ROD_WIDTH) / 2.0  # 1.25 mm - full rod seated
# rod diagonal 10.6 mm > 10 mm span, so yaw is bounded:
#   ROD_WIDTH * (cos y + sin y) <= CAVITY_SPAN
MAX_YAW = np.arcsin(CAVITY_SPAN / (np.sqrt(2) * ROD_WIDTH)) - np.pi / 4  # ~25.5 deg

# Depth the tip must reach for a genuinely seated insert. The env predicate is
# loose (a 45 deg wedge only 2 mm down still reports assembled), so we require
# real depth instead of trusting it. 40 mm cannot be reached from outside the
# 150 mm bore, so depth is the honest evidence of containment.
SEAT_DEPTH = 0.040
# Lateral is measured at the Ø2 mm cone tip, not at the 7.5 mm rod, so the tip
# can legitimately sit up to (span - tip)/2 off axis while fully inserted.
SEATED_LATERAL_TOL = CAPTURE_TOL + 0.001


@dataclass(frozen=True)
class Cavity:
    """Live world-space geometry of the stand's square bore."""

    centre: np.ndarray
    z_top: float
    z_bottom: float
    span: float
    yaw: float


def _fold_square_yaw(yaw):
    """Fold a yaw into the square bore's 90 degree rotational symmetry."""
    return (yaw + np.pi / 4) % (np.pi / 2) - np.pi / 4


def read_cavity(env):
    """Read the stand cavity directly from its four bounding wall geoms."""
    walls = {}
    for i in range(4):
        gid = env.obj_geom_id[f"stand_wall_{i}"]
        walls[i] = (
            env.sim.data.geom_xpos[gid].copy(),
            env.sim.data.geom_xmat[gid].reshape(3, 3).copy(),
            env.sim.model.geom_size[gid].copy(),
        )

    centre = np.mean([walls[i][0] for i in range(4)], axis=0)
    half_z = (np.abs(walls[0][1]) @ walls[0][2])[2]
    z_bottom = float(walls[0][0][2] - half_z)
    z_top = float(walls[0][0][2] + half_z)

    def clear_span(a, b, axis):
        ea = (np.abs(walls[a][1]) @ walls[a][2])[axis]
        eb = (np.abs(walls[b][1]) @ walls[b][2])[axis]
        return float(abs(walls[a][0][axis] - walls[b][0][axis]) - ea - eb)

    span_y = clear_span(0, 2, 1)
    span_x = clear_span(1, 3, 0)
    span = min(span_x, span_y)

    wall_axis = walls[3][0] - walls[1][0]
    yaw = _fold_square_yaw(np.arctan2(wall_axis[1], wall_axis[0]))

    centre[2] = z_top
    return Cavity(
        centre=centre,
        z_top=z_top,
        z_bottom=z_bottom,
        span=span,
        yaw=float(yaw),
    )


def capture_tol(cavity):
    """Cone-tip lateral capture tolerance for a live cavity."""
    return (cavity.span - TIP_MIN_DIA) / 2.0


def seated_lateral_tol(cavity):
    """Cone-tip lateral seating tolerance for a live cavity."""
    return capture_tol(cavity) + 0.001


def upright_frame_quat(yaw):
    """Body quaternion (xyzw) that stands the rod vertical, tip down, at `yaw`.

    ROD_AXIS_LOCAL is +z in the body frame and must map to +z in the world for
    the tip (at local -z) to point down, so only the yaw term is needed.
    """
    return T.axisangle2quat(np.array([0.0, 0.0, yaw]))


def frame_pose_for_tip(tip_world, yaw):
    """Body (pos, quat_xyzw) placing the rod tip at `tip_world` with rod vertical."""
    quat = upright_frame_quat(yaw)
    pos = tip_world - T.quat2mat(quat) @ TIP_LOCAL
    return pos, quat


def tip_from_body(body_pos, body_quat_xyzw):
    """Forward: where the tip is, given the body pose."""
    return body_pos + T.quat2mat(body_quat_xyzw) @ TIP_LOCAL


def insertion_error(tip_world, rod_axis_world, yaw, cavity):
    """Errors that decide stage-1 success.

    Returns lateral offset from the cavity axis (m), tilt of the rod off vertical
    (rad), yaw folded into the rod's 90 deg symmetry (rad), and how far the tip
    has descended past the cavity mouth (m, negative while still above).
    """
    lateral = np.linalg.norm(tip_world[:2] - cavity.centre[:2])
    tilt = np.arccos(np.clip(abs(rod_axis_world[2]), -1.0, 1.0))
    yaw_folded = _fold_square_yaw(yaw)
    depth = cavity.z_top - tip_world[2]
    return lateral, tilt, yaw_folded, depth


def is_seated(tip_world, rod_axis_world, cavity):
    """Geometric truth for 'the rod is really in the hole', independent of the env predicate."""
    lateral, tilt, _, depth = insertion_error(tip_world, rod_axis_world, 0.0, cavity)
    return depth >= SEAT_DEPTH and lateral <= seated_lateral_tol(cavity) and tilt < np.radians(8)

# ------------------------------------------------------- stage 2: hang the wrench
# Measured live, same as everything above. The wrench threads its Ø21 mm hole onto
# the 7.5 mm square hook bar, so the clearance is far wider than stage 1's bore.
TOOL_HOLE_RADIUS = 0.0105        # inner_radius_1
HOOK_THICKNESS = 0.0075          # frame_thickness
# What the env predicate actually tests: distance from the hole centre to the hook
# axis must stay under this. 6.75 mm, versus 1.25 mm for the stage-1 rod fit.
HANG_TOL = TOOL_HOLE_RADIUS - HOOK_THICKNESS / 2.0
# Fraction along the hook bar to aim for. Measured by teleport: 0.50 hangs and
# passes the full task predicate, 0.35 falls off, 0.20 hangs but fails the
# "inserted far enough" check. Aim for the middle of the bar.
HANG_ALONG = 0.50
TOOL_GRIP_HALF = np.array([0.015, 0.015, 0.040])   # tool_grip_g0, a 30x30x80 mm box


@dataclass(frozen=True)
class Hook:
    """Live world geometry of the hook bar the wrench must be threaded onto."""

    origin: np.ndarray      # frame_hang_site, the free end of the bar
    axis: np.ndarray        # unit vector pointing from the free end inward
    length: float
    target: np.ndarray      # where the hole centre should end up


def read_hook(env):
    """Read the hook bar from the frame's own sites, after stage 1 has stood it up."""
    d = env.sim.data
    origin = d.site_xpos[env.obj_site_id["frame_hang_site"]].copy()
    inner = d.site_xpos[env.obj_site_id["frame_intersection_site"]].copy()
    vec = inner - origin
    length = float(np.linalg.norm(vec))
    axis = vec / length
    return Hook(origin=origin, axis=axis, length=length,
                target=origin + axis * (HANG_ALONG * length))


def hole_axis_distance(hole_world, hook):
    """Distance from the wrench hole centre to the hook's axis line.

    This is exactly the quantity the env predicate thresholds, so scripting
    against it means scripting against the real success condition.
    """
    v = hole_world - hook.origin
    return float(np.linalg.norm(v - np.dot(v, hook.axis) * hook.axis))


def hole_along_hook(hole_world, hook):
    """Normalised position of the hole along the bar; the predicate wants 0.05..1."""
    return float(np.dot(hole_world - hook.origin, hook.axis) / hook.length)


def threaded(env, hook):
    """Is the bar through the ring? Mirrors the env's own geometric test.

    This used to carry an extra condition of my own - that the ring be within
    ~25 deg of square to the bar - added after a bug where the ring-centre-to-bar
    distance read 0.1 mm while the bar ran *alongside* the ring. That condition
    does block the bug, but robosuite blocks it a cheaper way: it takes two
    opposite geoms of the annulus and requires them on opposite sides of the bar
    axis, which is impossible unless the bar passes between them. Demanding
    squareness on top of that was mine, not the task's, and it is what forced a
    90 deg wrist rotation and the joint-limit exhaustion that followed.

    So the test here is the env's: the bar passes between opposite sides of the
    ring, the centre is within the aperture, and it is far enough along the bar.
    Tilt is left free, because the task leaves it free.
    """
    d, m = env.sim.data, env.sim.model
    origin = d.site_xpos[env.obj_site_id["frame_hang_site"]].copy()
    vec = d.site_xpos[env.obj_site_id["frame_intersection_site"]] - origin
    length = float(np.linalg.norm(vec))
    axis = vec / length

    centre = d.site_xpos[env.obj_site_id["tool_hole1_center"]].copy()
    rel = centre - origin
    along = float(np.dot(rel, axis))
    radial = float(np.linalg.norm(rel - along * axis))

    n = env.tool_args["ngeoms"]
    g1 = d.geom_xpos[env.obj_geom_id["tool_hole1_hc_0"]] - origin
    g2 = d.geom_xpos[env.obj_geom_id["tool_hole1_hc_%d" % (n // 2)]] - origin
    # Opposite sides of the bar axis: the sign of the cross products disagrees
    # only when the axis runs between the two geoms.
    between = float(np.dot(np.cross(g1, axis), np.cross(g2, axis))) < 0.0

    within = radial < HANG_TOL
    far_enough = 0.05 < (along / length) < 1.0
    detail = {
        "radial_mm": radial * 1000.0,
        "clearance_mm": (HANG_TOL - radial) * 1000.0,
        "along_frac": along / length,
        "between": between,
        "within_aperture": within,
        "far_enough": far_enough,
    }
    ok = between and within and far_enough
    if not ok:
        detail["reason"] = ("bar not between opposite sides of the ring" if not between
                            else "centre outside the aperture" if not within
                            else "not far enough along the bar")
    return ok, detail


def through_ring(ring_centre, ring_normal, hook, inner_radius=TOOL_HOLE_RADIUS,
                 bar_half_thickness=HOOK_THICKNESS / 2.0):
    """Pose-only threading test, for planning where no env is at hand.

    Weaker than `threaded`, which reads the actual annulus geoms. Kept because
    the planner reasons about hypothetical poses that do not exist in the
    simulator yet. Note it assumes the ring is roughly square to the bar; use
    `threaded` whenever a live env is available.
    """
    n = np.asarray(ring_normal, float)
    n = n / np.linalg.norm(n)
    a = np.asarray(hook.axis, float)
    a = a / np.linalg.norm(a)

    denom = float(np.dot(a, n))
    detail = {"parallelism": abs(denom)}
    if abs(denom) < 1e-6:
        detail["reason"] = "bar parallel to ring plane"
        return False, detail

    t = float(np.dot(ring_centre - hook.origin, n) / denom)
    pierce = hook.origin + a * t
    radial = float(np.linalg.norm(pierce - ring_centre))
    detail["t_along_bar"] = t
    detail["radial_mm"] = radial * 1000.0
    detail["clearance_mm"] = (inner_radius - bar_half_thickness - radial) * 1000.0

    inside_bar = -1e-9 <= t <= hook.length + 1e-9
    inside_hole = radial + bar_half_thickness <= inner_radius
    detail["inside_bar"] = inside_bar
    detail["inside_hole"] = inside_hole
    ok = inside_bar and inside_hole
    if not ok:
        detail["reason"] = ("pierces outside the bar" if not inside_bar
                            else "pierces outside the aperture")
    return ok, detail
