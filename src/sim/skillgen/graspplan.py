"""Joint grasp-and-face planning for ToolHang stage 2.

Three local decisions failed in succession: a geometry-selected flip, a sign-selected
ring face, and then a holdable face whose post-orient posture could not reach the
threading branch.  This planner therefore evaluates grasp symmetry, handle position,
and ring face together over the full grasp-to-standoff sequence.
"""

from dataclasses import dataclass

import mujoco
import numpy as np

from . import feasibility as F
from .compat import grip_site_id
from .primitives import grasp_mat

IK_POS_TOL = 5e-3
IK_ROT_TOL = np.radians(15.0)
IK_ITERS = 300

SCORE_MARGIN_WEIGHT = 1.0
SCORE_BRANCH_SWING_WEIGHT = 0.1
# Sweep is in metres and ranges 0.28-0.63 across accepted plans, so this weight
# makes a 350 mm difference in travel worth about as much as 0.1 rad of margin.
SCORE_SWEEP_WEIGHT = 0.3

# Set from the candidate distribution, not guessed. Planning grasp and face
# together, every face+1 candidate lands between 2.198 and 3.332 rad, while the
# branch change that broke the old code - planning each phase locally - needed
# 5.005 rad and swung joint 5 through half a revolution. 3.5 admits the whole
# feasible band and still excludes that. An earlier guess of 1.5 rejected all 24
# candidates, which is how a threshold with no measurement behind it fails.
DEFAULT_MAX_BRANCH_SWING = 3.5


# The finger pads are ~15 mm long, so material within this radius of the grasp
# point is what the jaws can actually close on. The handle plate is only 3.2 mm
# thick, so this is about surface proximity, not about enclosing a volume.
GRASP_MATERIAL_REACH = 0.020


def _nearest_tool_geom(model, data, point):
    """Closest collision geom of the wrench to `point`: (name, distance) or None."""
    best = None
    for i in range(model.ngeom):
        name = model.geom_id2name(i)
        if not name or not name.startswith("tool_") or name.endswith("_vis"):
            continue
        # Distance to the geom's surface, not its centre: the grip box is 30 mm
        # across, so centre distance would reject a perfectly good grasp on it.
        half = np.asarray(model.geom_size[i], dtype=float)
        delta = np.abs(np.asarray(data.geom_xpos[i], dtype=float) - np.asarray(point, dtype=float))
        outside = np.maximum(delta - half[:3], 0.0)
        dist = float(np.linalg.norm(outside))
        if best is None or dist < best[1]:
            best = (name, dist)
    return best


def _grasp_quality(model, geom_name, surf_dist):
    """How securely can the jaws close here? Higher is better.

    The wrench offers three kinds of surface: the 30x30x80 mm ceramic grip box,
    the 165x17.5x3.2 mm handle plate, and eight small boxes approximating the
    ring's annulus. Only the first gives the pads a flat face of their own size;
    the annulus boxes are a curved rim the fingers slide off, which is how the
    kinematically-best candidate failed every seed.
    """
    gid = model.geom_name2id(geom_name)
    half = np.asarray(model.geom_size[gid], dtype=float)[:3]
    footprint = 2.0 * float(np.median(half))
    curved = "hc_" in geom_name  # annulus approximation, not a real flat face
    q = footprint * (1.0 if not curved else 0.2)
    return q - surf_dist


def _hand_sweep(env, q_a, q_b, samples=17):
    """Diagonal of the box the grip site sweeps interpolating q_a -> q_b, in metres.

    A probe: it writes qpos and must restore it, or planning would teleport the
    arm it is only supposed to be reasoning about.
    """
    m, d = env.sim.model, env.sim.data
    sid = grip_site_id(m)
    qadr = [m.jnt_qposadr[m.joint_name2id(f"robot0_joint{i}")] for i in range(1, 8)]
    saved = d.qpos.copy()
    try:
        pts = []
        for t in np.linspace(0.0, 1.0, samples):
            q = np.asarray(q_a) + (np.asarray(q_b) - np.asarray(q_a)) * t
            for a, v in zip(qadr, q):
                d.qpos[a] = v
            mujoco.mj_forward(m._model, d._data)
            pts.append(d.site_xpos[sid].copy())
        pts = np.asarray(pts)
        return float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    finally:
        d.qpos[:] = saved
        mujoco.mj_forward(m._model, d._data)


@dataclass(frozen=True)
class GraspPlan:
    label: str
    grasp_pos: np.ndarray
    grasp_mat: np.ndarray
    face: float
    orient_mat: np.ndarray
    standoff_pos: np.ndarray
    q_grasp: np.ndarray
    q_standoff: np.ndarray
    branch_swing: float
    min_margin: float
    score: float


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    length = np.linalg.norm(vector)
    if length <= 1e-12:
        raise ValueError("Cannot normalize a zero-length vector.")
    return vector / length


def _rotation_from_to(source, target):
    """Return the proper rotation mapping source onto target."""
    source = _unit(source)
    target = _unit(target)
    cosine = float(np.clip(np.dot(source, target), -1.0, 1.0))

    if cosine >= 1.0 - 1e-12:
        return np.eye(3)

    if cosine <= -1.0 + 1e-12:
        basis = np.zeros(3)
        basis[int(np.argmin(np.abs(source)))] = 1.0
        axis = _unit(np.cross(source, basis))
        return 2.0 * np.outer(axis, axis) - np.eye(3)

    cross = np.cross(source, target)
    skew = np.array(
        [
            [0.0, -cross[2], cross[1]],
            [cross[2], 0.0, -cross[0]],
            [-cross[1], cross[0], 0.0],
        ]
    )
    return np.eye(3) + skew + skew @ skew * ((1.0 - cosine) / np.dot(cross, cross))


def _robot_joint_state(model, data):
    raw_model = model._model
    joint_ids = []
    for number in range(1, 8):
        joint_id = mujoco.mj_name2id(
            raw_model, mujoco.mjtObj.mjOBJ_JOINT, f"robot0_joint{number}"
        )
        if joint_id < 0:
            raise ValueError(f"Missing robot0_joint{number}.")
        joint_ids.append(joint_id)

    joint_ids = np.asarray(joint_ids, dtype=int)
    qpos_addresses = np.asarray(raw_model.jnt_qposadr[joint_ids], dtype=int)
    limits = np.asarray(raw_model.jnt_range[joint_ids], dtype=float).copy()
    current_q = np.asarray(data.qpos[qpos_addresses], dtype=float).copy()
    return current_q, limits


def _margin_value(result):
    value = np.asarray(result.limit_margin, dtype=float)
    if value.size == 0 or not np.all(np.isfinite(value)):
        return float("-inf")
    return float(np.min(value))


def _run_multistart(env, target_pos, target_mat, current_q, limits, rng, n_starts, margin):
    results = []
    errors = []

    for attempt in range(n_starts):
        if attempt == 0:
            seed = current_q.copy()
        else:
            seed = np.asarray(rng.uniform(limits[:, 0], limits[:, 1]), dtype=float)

        try:
            results.append(
                F.solve_ik(
                    env,
                    target_pos,
                    target_mat,
                    seed,
                    iters=IK_ITERS,
                    margin=margin,
                )
            )
        except Exception as exc:
            errors.append(type(exc).__name__)

    return results, errors


def _usable_solutions(results):
    usable = []
    summary = {
        "attempts": len(results),
        "residual_failures": 0,
        "boundary_failures": 0,
        "invalid_results": 0,
    }

    for result in results:
        try:
            q = np.asarray(result.q, dtype=float).copy()
            pos_err = float(result.pos_err)
            rot_err = float(result.rot_err)
            limit_margin = _margin_value(result)
        except (AttributeError, TypeError, ValueError):
            summary["invalid_results"] += 1
            continue

        if (
            q.shape != (7,)
            or not np.all(np.isfinite(q))
            or not np.isfinite(pos_err)
            or not np.isfinite(rot_err)
            or pos_err > IK_POS_TOL
            or rot_err > IK_ROT_TOL
        ):
            summary["residual_failures"] += 1
            continue

        if bool(result.boundary):
            summary["boundary_failures"] += 1
            continue

        usable.append((q, limit_margin, pos_err, rot_err))

    summary["usable"] = len(usable)
    return usable, summary


def _ik_rejection_reason(summary, errors, suffix):
    if summary["boundary_failures"]:
        return f"ik_boundary_{suffix}"
    if errors and summary["attempts"] == 0:
        return f"ik_error_{suffix}"
    return f"ik_residual_{suffix}"


def _label(point_name, axis_name, sign, face):
    closing_sign = "+" if sign > 0.0 else "-"
    face_sign = "+" if face > 0.0 else "-"
    return f"{point_name}/{axis_name}{closing_sign}/face{face_sign}1"


def plan_grasp(
    env,
    hook,
    rng=None,
    n_starts=40,
    margin=0.05,
    max_branch_swing=DEFAULT_MAX_BRANCH_SWING,
    only_axis=None,
):
    """Choose grasp and ring face jointly. Returns ``(GraspPlan | None, report)``.

    `only_axis` restricts the closing direction to "along" or "across" the
    wrench's long axis. Left None, both are scored and the best wins - which in
    practice has meant "along", because closing along the handle gives the jaws
    the flattest face and the shortest reach.

    That choice is what strands the orient phase. Held along its length, the
    wrench's ring can only be stood up by rotating about the handle's own axis,
    which is nearly collinear with the final wrist axis - so the whole rotation
    falls on j6/j7 and j6 reaches its stop before the pose is reached. Held
    across, the same rotation is a motion of the arm, shared among joints that
    have room for it.
    """
    if int(n_starts) < 1:
        raise ValueError("n_starts must be at least one.")
    if max_branch_swing <= 0.0:
        raise ValueError("max_branch_swing must be positive.")

    n_starts = int(n_starts)
    rng = np.random.default_rng() if rng is None else rng

    model = env.sim.model
    data = env.sim.data
    raw_model = model._model

    hole = np.asarray(
        data.site_xpos[env.obj_site_id["tool_hole1_center"]], dtype=float
    ).copy()
    grip_body_id = mujoco.mj_name2id(
        raw_model, mujoco.mjtObj.mjOBJ_BODY, "tool_grip_main"
    )
    if grip_body_id < 0:
        raise ValueError("Missing tool_grip_main body.")
    grip_w = np.asarray(data.body_xpos[grip_body_id], dtype=float).copy()

    long_axis = hole - grip_w
    long_axis[2] = 0.0
    long_axis = _unit(long_axis)
    across = _unit(np.cross(np.array([0.0, 0.0, 1.0]), long_axis))

    current_q, limits = _robot_joint_state(model, data)
    standoff = np.asarray(hook.origin, dtype=float) - np.asarray(
        hook.axis, dtype=float
    ) * 0.045

    point_specs = (
        ("grip", 0.0),
        ("toward-ring50", 0.5),
        ("toward-ring75", 0.75),
    )
    axis_specs = (
        ("along", long_axis),
        ("across", across),
    )

    report = {
        "candidates": [],
        "n_candidates": 0,
        "n_feasible": 0,
        "best_label": None,
    }
    best_plan = None
    best_key = None
    grasp_cache = {}

    for point_name, fraction in point_specs:
        grasp_pos = grip_w + fraction * (hole - grip_w)

        # A pose the arm can hold is not a grasp. Measured: the interpolated
        # handle points rank best on kinematics and sit 34 mm from the nearest
        # tool geom - the gap between the ring and the grip box - so the fingers
        # close on air. Require solid material at the grasp point before the
        # candidate is worth scoring at all.
        near = _nearest_tool_geom(model, data, grasp_pos)
        if near is None or near[1] > GRASP_MATERIAL_REACH:
            report["candidates"].append({
                "label": f"{point_name}/*",
                "reason": "no_material_at_grasp",
                "nearest_geom_mm": None if near is None else near[1] * 1000.0,
            })
            report["n_candidates"] += 1
            continue
        # Material nearby is necessary but not sufficient. Measured: the point
        # 75% toward the ring sits 3 mm off the annulus, passes the reach test,
        # scores best on kinematics because its lever arm is short - and the
        # fingers slide off the curved rim every time. Prefer a flat, thick
        # feature, and treat that preference as a tier above kinematics: a grasp
        # that slips makes every downstream number meaningless.
        grasp_quality = _grasp_quality(model, near[0], near[1])

        for axis_name, base_axis in axis_specs:
            if only_axis is not None and axis_name != only_axis:
                continue
            for sign in (1.0, -1.0):
                closing = sign * base_axis
                candidate_grasp_mat = np.asarray(
                    grasp_mat(
                        approach=np.array([0.0, 0.0, -1.0]),
                        closing=closing,
                    ),
                    dtype=float,
                ).copy()

                cache_key = (point_name, axis_name, sign)
                if cache_key not in grasp_cache:
                    grasp_results, grasp_errors = _run_multistart(
                        env,
                        grasp_pos,
                        candidate_grasp_mat,
                        current_q,
                        limits,
                        rng,
                        n_starts,
                        margin,
                    )
                    grasp_cache[cache_key] = (
                        *_usable_solutions(grasp_results),
                        grasp_errors,
                    )

                grasp_solutions, grasp_summary, grasp_errors = grasp_cache[cache_key]

                for face in (1.0, -1.0):
                    label = _label(point_name, axis_name, sign, face)
                    entry = {
                        "label": label,
                        "grasp_fraction": fraction,
                        "face": face,
                        "grasp_ik": dict(grasp_summary),
                    }
                    report["candidates"].append(entry)
                    report["n_candidates"] += 1

                    if not grasp_solutions:
                        entry["reason"] = _ik_rejection_reason(
                            grasp_summary, grasp_errors, "grasp"
                        )
                        if grasp_errors:
                            entry["grasp_ik_errors"] = list(grasp_errors)
                        continue

                    orient_delta = _rotation_from_to(
                        np.array([0.0, 0.0, 1.0]),
                        face * np.asarray(hook.axis, dtype=float),
                    )
                    orient_mat = orient_delta @ candidate_grasp_mat
                    standoff_hand = standoff - orient_delta @ (hole - grasp_pos)

                    standoff_results, standoff_errors = _run_multistart(
                        env,
                        standoff_hand,
                        orient_mat,
                        current_q,
                        limits,
                        rng,
                        n_starts,
                        margin,
                    )
                    standoff_solutions, standoff_summary = _usable_solutions(
                        standoff_results
                    )
                    entry["standoff_ik"] = standoff_summary

                    if not standoff_solutions:
                        entry["reason"] = _ik_rejection_reason(
                            standoff_summary, standoff_errors, "standoff"
                        )
                        if standoff_errors:
                            entry["standoff_ik_errors"] = list(standoff_errors)
                        continue

                    best_pair = None
                    for q_grasp, grasp_margin, _, _ in grasp_solutions:
                        for q_standoff, standoff_margin, _, _ in standoff_solutions:
                            branch_swing = float(
                                np.max(np.abs(q_standoff - q_grasp))
                            )
                            min_margin = min(grasp_margin, standoff_margin)
                            pair_key = (branch_swing, -min_margin)

                            if best_pair is None or pair_key < best_pair[0]:
                                best_pair = (
                                    pair_key,
                                    q_grasp,
                                    q_standoff,
                                    branch_swing,
                                    min_margin,
                                )

                    _, q_grasp, q_standoff, branch_swing, min_margin = best_pair
                    entry["branch_swing"] = branch_swing
                    entry["min_margin"] = min_margin

                    if branch_swing > max_branch_swing:
                        entry["reason"] = "branch_swing"
                        continue

                    # Margin preserves room for the remaining insertion motion;
                    # swing penalizes avoiding it by a costly wrist-branch change.
                    # Sweep penalises legal-but-wild paths: measured, one accepted
                    # plan carried the hand through 628 mm of vertical travel while
                    # holding the wrench, versus 281 mm for the plans that chose a
                    # shorter lever arm. Both satisfy the joint limits; only one is
                    # a sane thing to do with a tool in the gripper.
                    sweep = _hand_sweep(env, q_grasp, q_standoff)
                    entry["hand_sweep"] = sweep
                    score = (
                        SCORE_MARGIN_WEIGHT * min_margin
                        - SCORE_BRANCH_SWING_WEIGHT * branch_swing
                        - SCORE_SWEEP_WEIGHT * sweep
                    )
                    entry["score"] = score
                    entry["grasp_quality"] = grasp_quality
                    entry["status"] = "accepted"
                    report["n_feasible"] += 1

                    plan = GraspPlan(
                        label=label,
                        grasp_pos=grasp_pos.copy(),
                        grasp_mat=candidate_grasp_mat.copy(),
                        face=face,
                        orient_mat=orient_mat.copy(),
                        standoff_pos=standoff_hand.copy(),
                        q_grasp=q_grasp.copy(),
                        q_standoff=q_standoff.copy(),
                        branch_swing=branch_swing,
                        min_margin=min_margin,
                        score=score,
                    )

                    # Tiered, not weighted. Grasp security is categorically prior
                    # to kinematic comfort: a candidate whose fingers slip makes
                    # its margin and swing numbers meaningless, so no amount of
                    # kinematic advantage should outrank a solid grasp. Within a
                    # quality tier, the weighted kinematic score decides.
                    key = (round(grasp_quality, 3), score)
                    if best_key is None or key > best_key:
                        best_key, best_plan = key, plan

    report["best_label"] = None if best_plan is None else best_plan.label
    return best_plan, report
