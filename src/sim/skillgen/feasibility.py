"""Constraint-aware pose-feasibility probes for ToolHang's Panda hand motions.

A constrained optimizer can converge to a low-residual pose on a joint boundary.
That boundary solution is not evidence that an interior configuration exists or
that the hand can continue moving in the required direction.
"""

from dataclasses import dataclass

import mujoco
import numpy as np

from .compat import grip_site_id

DEFAULT_LIMIT_MARGIN = 0.05
CONTINUE_FRACTION_THRESHOLD = 0.5


@dataclass
class IKResult:
    q: np.ndarray
    pos_err: float
    rot_err: float
    limit_margin: float
    active_limits: list
    boundary: bool


def _joint_layout(model):
    ids = [
        model.joint_name2id(f"robot0_joint{i}")
        for i in range(1, 8)
    ]
    qadr = [model.jnt_qposadr[j] for j in ids]
    dofs = [model.jnt_dofadr[j] for j in ids]
    lo = np.array([model.jnt_range[j][0] for j in ids])
    hi = np.array([model.jnt_range[j][1] for j in ids])
    return qadr, dofs, lo, hi


def _limit_state(q, lo, hi, margin):
    distances = np.minimum(q - lo, hi - q)
    limit_margin = float(np.min(distances))
    active_limits = [
        index + 1
        for index, distance in enumerate(distances)
        if distance <= margin
    ]
    boundary = bool(limit_margin <= margin)
    return limit_margin, active_limits, boundary


def _pose_errors(model, data, site_id, target_pos, target_mat):
    p = data.site_xpos[site_id].copy()
    R = data.site_xmat[site_id].reshape(3, 3).copy()
    ep = target_pos - p

    quat = np.zeros(4)
    mujoco.mju_mat2Quat(
        quat,
        (target_mat @ R.T).flatten(),
    )
    ang = 2.0 * np.arccos(np.clip(quat[0], -1.0, 1.0))
    s = np.linalg.norm(quat[1:])
    er = np.zeros(3) if s < 1e-9 else quat[1:] / s * ang

    return float(np.linalg.norm(ep)), float(np.linalg.norm(er))


def solve_ik(
    env,
    target_pos,
    target_mat,
    q_seed,
    iters=300,
    margin=DEFAULT_LIMIT_MARGIN,
):
    """Damped least-squares IK on the Panda's grip site.

    Returns an IKResult. This is a PROBE: it writes d.qpos while iterating and
    must leave the simulator exactly as it found it.
    """
    m, d = env.sim.model, env.sim.data
    original_qpos = d.qpos.copy()

    try:
        sid = grip_site_id(m)
        qadr, dofs, lo, hi = _joint_layout(m)

        q = np.asarray(q_seed, dtype=float).copy()
        target_pos = np.asarray(target_pos, dtype=float)
        target_mat = np.asarray(target_mat, dtype=float)

        jacp = np.zeros((3, m.nv))
        jacr = np.zeros((3, m.nv))
        pos_err_m = np.inf
        rot_err_rad = np.inf

        for _ in range(iters):
            for address, value in zip(qadr, q):
                d.qpos[address] = value
            mujoco.mj_forward(m._model, d._data)

            pos_err_m, rot_err_rad = _pose_errors(
                m,
                d,
                sid,
                target_pos,
                target_mat,
            )
            if pos_err_m < 1e-4 and rot_err_rad < 1e-3:
                break

            p = d.site_xpos[sid].copy()
            ep = target_pos - p

            R = d.site_xmat[sid].reshape(3, 3).copy()
            quat = np.zeros(4)
            mujoco.mju_mat2Quat(
                quat,
                (target_mat @ R.T).flatten(),
            )
            ang = 2.0 * np.arccos(np.clip(quat[0], -1.0, 1.0))
            s = np.linalg.norm(quat[1:])
            er = np.zeros(3) if s < 1e-9 else quat[1:] / s * ang

            mujoco.mj_jacSite(
                m._model,
                d._data,
                jacp,
                jacr,
                sid,
            )
            J = np.vstack([jacp[:, dofs], jacr[:, dofs]])
            err = np.concatenate([ep, er])
            dq = J.T @ np.linalg.solve(
                J @ J.T + 1e-4 * np.eye(6),
                err,
            )
            q = np.clip(
                q + np.clip(dq, -0.15, 0.15),
                lo,
                hi,
            )

        for address, value in zip(qadr, q):
            d.qpos[address] = value
        mujoco.mj_forward(m._model, d._data)

        pos_err_m, rot_err_rad = _pose_errors(
            m,
            d,
            sid,
            target_pos,
            target_mat,
        )
        limit_margin, active_limits, boundary = _limit_state(
            q,
            lo,
            hi,
            margin,
        )

        return IKResult(
            q=q.copy(),
            pos_err=pos_err_m,
            rot_err=rot_err_rad,
            limit_margin=limit_margin,
            active_limits=active_limits,
            boundary=boundary,
        )
    finally:
        d.qpos[:] = original_qpos
        mujoco.mj_forward(m._model, d._data)


def can_continue(env, q, direction, step=0.01, margin=DEFAULT_LIMIT_MARGIN):
    """From configuration `q`, can the hand still move along `direction`?

    Returns (bool, achieved_fraction). Sets the arm to `q`, asks for a small
    Cartesian step along `direction`, and reports how much of it the joint
    limits actually allow.
    """
    if step <= 0.0:
        raise ValueError("step must be positive")
    if margin < 0.0:
        raise ValueError("margin must be non-negative")

    direction = np.asarray(direction, dtype=float).reshape(-1)
    if direction.size != 3:
        raise ValueError("direction must contain three Cartesian components")

    direction_norm = np.linalg.norm(direction)
    if direction_norm < 1e-12:
        return False, 0.0

    m, d = env.sim.model, env.sim.data
    original_qpos = d.qpos.copy()

    try:
        sid = grip_site_id(m)
        qadr, dofs, lo, hi = _joint_layout(m)
        q = np.asarray(q, dtype=float).copy()

        for address, value in zip(qadr, q):
            d.qpos[address] = value
        mujoco.mj_forward(m._model, d._data)

        jacp = np.zeros((3, m.nv))
        jacr = np.zeros((3, m.nv))
        mujoco.mj_jacSite(
            m._model,
            d._data,
            jacp,
            jacr,
            sid,
        )

        J = np.vstack([jacp[:, dofs], jacr[:, dofs]])
        desired_step = np.concatenate(
            [
                direction / direction_norm * step,
                np.zeros(3),
            ]
        )
        dq = J.T @ np.linalg.solve(
            J @ J.T + 1e-4 * np.eye(6),
            desired_step,
        )
        dq = np.clip(dq, -0.15, 0.15)

        safe_lo = lo + margin
        safe_hi = hi - margin
        achieved_fraction = 1.0

        for qi, delta, lower, upper in zip(
            q,
            dq,
            safe_lo,
            safe_hi,
        ):
            if delta > 0.0:
                if qi >= upper:
                    achieved_fraction = 0.0
                else:
                    achieved_fraction = min(
                        achieved_fraction,
                        (upper - qi) / delta,
                    )
            elif delta < 0.0:
                if qi <= lower:
                    achieved_fraction = 0.0
                else:
                    achieved_fraction = min(
                        achieved_fraction,
                        (lower - qi) / delta,
                    )

        achieved_fraction = float(
            np.clip(achieved_fraction, 0.0, 1.0)
        )
        return achieved_fraction > 0.0, achieved_fraction
    finally:
        d.qpos[:] = original_qpos
        mujoco.mj_forward(m._model, d._data)


def holdable(
    env,
    target_pos,
    target_mat,
    n_starts=40,
    pos_tol=5e-3,
    rot_tol=np.radians(15),
    margin=DEFAULT_LIMIT_MARGIN,
    continue_dir=None,
    rng=None,
):
    """Returns (ok, hits, detail_dict)."""
    if rng is None:
        rng = np.random.default_rng()

    m, d = env.sim.model, env.sim.data
    original_qpos = d.qpos.copy()

    try:
        qadr, _, lo, hi = _joint_layout(m)
        current_q = np.array(
            [d.qpos[address] for address in qadr],
            dtype=float,
        )

        hits = 0
        boundary_rejects = 0
        continue_rejects = 0
        residual_rejects = 0
        best_result = None
        best_feasible_result = None

        for start_index in range(n_starts):
            if start_index == 0:
                seed = current_q
            else:
                seed = rng.uniform(
                    lo + margin,
                    hi - margin,
                )

            result = solve_ik(
                env,
                target_pos,
                target_mat,
                seed,
                margin=margin,
            )

            if (
                best_result is None
                or result.pos_err < best_result.pos_err
                or (
                    result.pos_err == best_result.pos_err
                    and result.rot_err < best_result.rot_err
                )
            ):
                best_result = result

            residual_ok = (
                result.pos_err <= pos_tol
                and result.rot_err <= rot_tol
            )

            if not residual_ok:
                residual_rejects += 1
                continue

            if result.boundary:
                boundary_rejects += 1
                continue

            continuation_ok = True
            continuation_fraction = 1.0
            if continue_dir is not None:
                continuation_ok, continuation_fraction = can_continue(
                    env,
                    result.q,
                    continue_dir,
                    margin=margin,
                )
                if (
                    not continuation_ok
                    or continuation_fraction
                    < CONTINUE_FRACTION_THRESHOLD
                ):
                    continue_rejects += 1
                    continue

            hits += 1
            if (
                best_feasible_result is None
                or result.limit_margin
                > best_feasible_result.limit_margin
            ):
                best_feasible_result = result

        if best_result is None:
            best_pos_err = np.inf
            best_rot_err = np.inf
            best_limit_margin = -np.inf
            active_limits = []
            best_q = None
        else:
            best_pos_err = best_result.pos_err
            best_rot_err = best_result.rot_err
            best_limit_margin = best_result.limit_margin
            active_limits = list(best_result.active_limits)
            best_q = best_result.q.copy()

        detail = {
            "best_pos_err": float(best_pos_err),
            "best_rot_err": float(best_rot_err),
            "best_limit_margin": float(best_limit_margin),
            "active_limits": active_limits,
            "best_q": best_q,
            "boundary_rejects": int(boundary_rejects),
            "continue_rejects": int(continue_rejects),
            "residual_rejects": int(residual_rejects),
            "continue_fraction_threshold": (
                CONTINUE_FRACTION_THRESHOLD
            ),
            "best_feasible_limit_margin": (
                None
                if best_feasible_result is None
                else float(best_feasible_result.limit_margin)
            ),
            "best_feasible_q": (
                None
                if best_feasible_result is None
                else best_feasible_result.q.copy()
            ),
        }

        return hits > 0, hits, detail
    finally:
        d.qpos[:] = original_qpos
        mujoco.mj_forward(m._model, d._data)


def best_of(
    env,
    candidates,
    n_starts=40,
    rng=None,
    continue_dir=None,
):
    """Pick the feasible candidate with the most IK hits."""
    if rng is None:
        rng = np.random.default_rng()

    winner = None
    winner_margin = -np.inf

    for label, pos, mat in candidates:
        holdable_pose, hits, detail = holdable(
            env,
            pos,
            mat,
            n_starts=n_starts,
            continue_dir=continue_dir,
            rng=rng,
        )
        if not holdable_pose:
            continue

        margin = detail["best_feasible_limit_margin"]
        if margin is None:
            margin = detail["best_limit_margin"]

        if (
            winner is None
            or hits > winner[3]
            or (hits == winner[3] and margin > winner_margin)
        ):
            winner = (label, pos, mat, hits, detail)
            winner_margin = margin

    return winner
