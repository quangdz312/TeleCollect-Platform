"""Task rules for shadow auto-label suggestions over privileged state traces."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

RuleStatus = Literal["pass", "fail", "cannot_evaluate", "warning"]
Recommendation = Literal["suggest_pass", "auto_reject", "needs_review"]


@dataclass(frozen=True)
class RuleConfig:
    stable_tail_frames: int = 10
    lift_height_m: float = 0.04
    grasp_radius_m: float = 0.08
    release_distance_m: float = 0.6
    can_tail_speed_mps: float = 0.01
    can_divider_clearance_m: float = 0.02
    square_xy_tolerance_m: float = 0.03
    square_height_clearance_m: float = 0.05
    square_angle_tolerance_deg: float = 15.0

    @classmethod
    def from_settings(cls) -> RuleConfig:
        from src.config import get_settings

        settings = get_settings()
        return cls(
            stable_tail_frames=settings.rule_stable_tail_frames,
            lift_height_m=settings.rule_lift_height_m,
            grasp_radius_m=settings.rule_grasp_radius_m,
            release_distance_m=settings.rule_release_distance_m,
            can_tail_speed_mps=settings.rule_can_tail_speed_mps,
            can_divider_clearance_m=settings.rule_can_divider_clearance_m,
            square_xy_tolerance_m=settings.rule_square_xy_tolerance_m,
            square_height_clearance_m=settings.rule_square_height_clearance_m,
            square_angle_tolerance_deg=settings.rule_square_angle_tolerance_deg,
        )


DEFAULT_RULE_CONFIG = RuleConfig()
RULE_ENGINE_NAME = "telecollect-task-rules"


def rule_version(config: RuleConfig = DEFAULT_RULE_CONFIG) -> str:
    payload = json.dumps(asdict(config), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{RULE_ENGINE_NAME}-{digest}"


@dataclass(frozen=True)
class RuleEpisode:
    """Common trace consumed by all task rules, independent of source format."""

    episode_id: str
    task: str
    source: str
    object_position: np.ndarray | None = None
    object_quat: np.ndarray | None = None
    eef_position: np.ndarray | None = None
    target_position: np.ndarray | None = None
    target_bounds: tuple[np.ndarray, np.ndarray] | None = None
    table_height: float | None = None
    divider_x: float | None = None
    control_hz: float = 30.0
    recorded_success: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def length(self) -> int:
        for value in (self.object_position, self.eef_position, self.object_quat):
            if value is not None:
                return int(value.shape[0])
        return 0


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: RuleStatus
    severity: Literal["hard", "quality"] = "hard"
    measured_values: Mapping[str, Any] = field(default_factory=dict)
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class RuleEvaluation:
    episode_id: str
    task: str
    rule_version: str
    final_recommendation: Recommendation
    mode: Literal["shadow"] = "shadow"
    results: list[RuleResult] = field(default_factory=list)

    @property
    def cannot_evaluate(self) -> bool:
        return any(result.status == "cannot_evaluate" for result in self.results)

    def summary(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task": self.task,
            "rule_version": self.rule_version,
            "final_recommendation": self.final_recommendation,
            "mode": self.mode,
            "results": [
                {
                    "rule_id": item.rule_id,
                    "status": item.status,
                    "severity": item.severity,
                    "measured_values": dict(item.measured_values),
                    "thresholds": dict(item.thresholds),
                    "message": item.message,
                }
                for item in self.results
            ],
        }


def evaluate_rules(
    episode: RuleEpisode,
    *,
    config: RuleConfig = DEFAULT_RULE_CONFIG,
) -> RuleEvaluation:
    task = _normalize_task(episode.task)
    if task == "lift":
        results = _lift_rules(episode, config)
    elif task == "can":
        results = _can_rules(episode, config)
    elif task == "square":
        results = _square_rules(episode, config)
    else:
        results = [
            RuleResult(
                "task.supported",
                "cannot_evaluate",
                measured_values={"task": episode.task},
                message="unsupported task",
            )
        ]
    return RuleEvaluation(
        episode_id=episode.episode_id,
        task=task,
        rule_version=rule_version(config),
        final_recommendation=_recommend(results),
        results=results,
    )


def load_manual_rule_episode(episode_dir: str | Path) -> RuleEpisode:
    """Load a teleop directory enough to degrade old recordings cleanly."""

    import pyarrow.parquet as pq

    root = Path(episode_dir)
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    task = str(meta.get("task_name", "unknown"))
    actions_path = root / "actions.parquet"
    if not actions_path.exists():
        return RuleEpisode(str(root.name), task, "manual_teleop", metadata=meta)
    table = pq.read_table(actions_path)
    if "privileged_state" not in table.column_names:
        return RuleEpisode(str(root.name), task, "manual_teleop", metadata=meta)
    # Decoding flat MuJoCo states needs the matching robosuite model. The helper
    # below is used by smoke tests and batch jobs that provide a live env.
    return RuleEpisode(
        str(root.name),
        task,
        "manual_teleop",
        control_hz=float(meta.get("control_hz", 30.0)),
        recorded_success=meta.get("task_success"),
        metadata=meta | {"state_frames": table.num_rows},
    )


def load_manual_rule_episode_with_env(episode_dir: str | Path, env: Any) -> RuleEpisode:
    """Load and decode a teleop directory with a matching live robosuite env."""

    import pyarrow.parquet as pq

    root = Path(episode_dir)
    base = load_manual_rule_episode(root)
    actions_path = root / "actions.parquet"
    if not actions_path.exists():
        return base
    table = pq.read_table(actions_path)
    if "privileged_state" not in table.column_names:
        return base
    states = np.asarray(table["privileged_state"].to_pylist(), dtype=np.float64)
    if states.size == 0:
        return base
    return episode_from_env_states(
        env=env,
        states=states,
        task=base.task,
        episode_id=base.episode_id,
        source=base.source,
        control_hz=base.control_hz,
        recorded_success=base.recorded_success,
    )


def load_scripted_rule_episode(
    hdf5_path: str | Path,
    demo_key: str,
    *,
    task: str,
    env: Any | None = None,
) -> RuleEpisode:
    """Load one robomimic demo; decode states when an env is supplied."""

    import h5py

    source = Path(hdf5_path)
    episode_id = f"{source.name}::{demo_key}"
    with h5py.File(source, "r") as handle:
        demo = handle["data"][demo_key]
        recorded_success = bool(demo.attrs.get("success", False))
        if "states" not in demo:
            return RuleEpisode(
                episode_id,
                task,
                "scripted",
                recorded_success=recorded_success,
                metadata={"missing": "states"},
            )
        states = np.asarray(demo["states"], dtype=np.float64)
    if env is None:
        return RuleEpisode(
            episode_id,
            task,
            "scripted",
            recorded_success=recorded_success,
            metadata={"state_frames": int(states.shape[0])},
        )
    return episode_from_env_states(
        env=env,
        states=states,
        task=task,
        episode_id=episode_id,
        source="scripted",
        control_hz=_env_control_hz(env),
        recorded_success=recorded_success,
    )


def episode_from_env_states(
    *,
    env: Any,
    states: np.ndarray,
    task: str,
    episode_id: str,
    source: str,
    control_hz: float,
    recorded_success: bool | None = None,
) -> RuleEpisode:
    """Decode flat MuJoCo states through one live robosuite env."""

    object_positions: list[np.ndarray] = []
    object_quats: list[np.ndarray] = []
    eef_positions: list[np.ndarray] = []
    for state in np.asarray(states, dtype=np.float64):
        _set_flat_state(env.sim, state)
        env.sim.forward()
        object_positions.append(_body_pos(env, _object_body_candidates(task)))
        object_quats.append(_body_quat(env, _object_body_candidates(task)))
        eef_positions.append(_eef_pos(env))

    return RuleEpisode(
        episode_id=episode_id,
        task=task,
        source=source,
        object_position=np.asarray(object_positions, dtype=np.float64),
        object_quat=np.asarray(object_quats, dtype=np.float64),
        eef_position=np.asarray(eef_positions, dtype=np.float64),
        target_position=_target_position(env, task),
        target_bounds=_target_bounds(env, task),
        table_height=_table_height(env),
        divider_x=_divider_x(env),
        control_hz=control_hz,
        recorded_success=recorded_success,
    )


def _recommend(results: list[RuleResult]) -> Recommendation:
    if any(result.status == "cannot_evaluate" for result in results):
        return "needs_review"
    if any(result.status == "fail" for result in results):
        return "auto_reject"
    if any(result.status == "warning" for result in results):
        return "needs_review"
    return "suggest_pass"


def _normalize_task(task: str) -> str:
    mapping = {
        "lift_cube": "lift",
        "lift": "lift",
        "pick_place_can": "can",
        "can": "can",
        "nut_assembly_square": "square",
        "assemble_square": "square",
        "square": "square",
    }
    return mapping.get(task, task)


def _need(episode: RuleEpisode, *names: str) -> RuleResult | None:
    missing = [name for name in names if getattr(episode, name) is None]
    if missing:
        return RuleResult(
            "state.required",
            "cannot_evaluate",
            measured_values={"missing": missing},
            message="required privileged state is missing",
        )
    if episode.length == 0:
        return RuleResult("state.required", "cannot_evaluate", message="empty episode")
    return None


def _lift_rules(episode: RuleEpisode, config: RuleConfig) -> list[RuleResult]:
    missing = _need(episode, "object_position", "eef_position", "table_height")
    if missing is not None:
        return [missing]
    obj = episode.object_position
    eef = episode.eef_position
    assert obj is not None and eef is not None and episode.table_height is not None

    threshold = episode.table_height + config.lift_height_m
    lifted = obj[:, 2] > threshold
    longest = _longest_run(lifted)
    reached = longest >= config.stable_tail_frames
    first_lift = int(np.argmax(lifted)) if np.any(lifted) else None
    dropped = bool(first_lift is not None and np.any(obj[first_lift:, 2] <= threshold))
    final_distance = float(np.linalg.norm(obj[-1] - eef[-1]))
    grasped_end = final_distance <= config.grasp_radius_m
    passed = reached and not dropped and grasped_end
    return [
        RuleResult(
            "lift.goal_state",
            "pass" if passed else "fail",
            measured_values={
                "max_height_m": float(np.max(obj[:, 2])),
                "longest_lifted_frames": longest,
                "dropped_after_lift": dropped,
                "final_gripper_object_distance_m": final_distance,
            },
            thresholds={
                "height_threshold_m": threshold,
                "hold_frames": config.stable_tail_frames,
                "grasp_radius_m": config.grasp_radius_m,
            },
        )
    ]


def _can_rules(episode: RuleEpisode, config: RuleConfig) -> list[RuleResult]:
    missing = _need(episode, "object_position", "eef_position", "target_bounds")
    if missing is not None:
        return [missing]
    obj = episode.object_position
    eef = episode.eef_position
    bounds = episode.target_bounds
    assert obj is not None and eef is not None and bounds is not None

    low, high = bounds
    final = obj[-1]
    in_target = bool(np.all(final[:2] >= low[:2]) and np.all(final[:2] <= high[:2]))
    tail_speed = _max_tail_speed(obj, episode.control_hz, config.stable_tail_frames)
    released = float(np.linalg.norm(final - eef[-1])) >= config.release_distance_m
    clearance_ok = True
    clearance = None
    if episode.divider_x is not None:
        clearance = abs(float(final[0]) - episode.divider_x)
        clearance_ok = clearance >= config.can_divider_clearance_m
    passed = in_target and tail_speed <= config.can_tail_speed_mps and released and clearance_ok
    return [
        RuleResult(
            "can.goal_state",
            "pass" if passed else "fail",
            measured_values={
                "final_position": final.tolist(),
                "in_target": in_target,
                "max_tail_speed_mps": tail_speed,
                "released": released,
                "divider_clearance_m": clearance,
            },
            thresholds={
                "target_low": low.tolist(),
                "target_high": high.tolist(),
                "tail_speed_mps": config.can_tail_speed_mps,
                "release_distance_m": config.release_distance_m,
                "divider_clearance_m": config.can_divider_clearance_m,
            },
        )
    ]


def _square_rules(episode: RuleEpisode, config: RuleConfig) -> list[RuleResult]:
    missing = _need(episode, "object_position", "eef_position", "target_position", "table_height")
    if missing is not None:
        return [missing]
    obj = episode.object_position
    eef = episode.eef_position
    target = episode.target_position
    assert obj is not None and eef is not None and target is not None and episode.table_height is not None

    final = obj[-1]
    xy_error = np.abs(final[:2] - target[:2])
    height_limit = episode.table_height + config.square_height_clearance_m
    released = float(np.linalg.norm(final - eef[-1])) >= config.release_distance_m
    angle_error = _square_angle_error_deg(episode.object_quat)
    angle_ok = angle_error is None or angle_error <= config.square_angle_tolerance_deg
    passed = (
        bool(np.all(xy_error < config.square_xy_tolerance_m))
        and final[2] < height_limit
        and released
        and angle_ok
    )
    return [
        RuleResult(
            "square.goal_state",
            "pass" if passed else "fail",
            measured_values={
                "final_position": final.tolist(),
                "target_position": target.tolist(),
                "xy_error_m": xy_error.tolist(),
                "height_m": float(final[2]),
                "released": released,
                "angle_error_deg": angle_error,
            },
            thresholds={
                "xy_tolerance_m": config.square_xy_tolerance_m,
                "height_limit_m": height_limit,
                "release_distance_m": config.release_distance_m,
                "angle_tolerance_deg": config.square_angle_tolerance_deg,
            },
        )
    ]


def _longest_run(mask: np.ndarray) -> int:
    best = current = 0
    for value in mask:
        current = current + 1 if bool(value) else 0
        best = max(best, current)
    return best


def _max_tail_speed(position: np.ndarray, control_hz: float, frames: int) -> float:
    if position.shape[0] <= 1:
        return math.inf
    tail = position[-min(position.shape[0], frames + 1) :]
    speed = np.linalg.norm(np.diff(tail, axis=0), axis=1) * control_hz
    return float(np.max(speed)) if speed.size else math.inf


def _square_angle_error_deg(quat: np.ndarray | None) -> float | None:
    if quat is None or quat.shape[0] == 0:
        return None
    # Project yaw from quaternion [w, x, y, z]; square nut is symmetric by 90 deg.
    w, x, y, z = [float(v) for v in quat[-1]]
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    period = math.pi / 2.0
    wrapped = abs((yaw + period / 2.0) % period - period / 2.0)
    return math.degrees(wrapped)


def _set_flat_state(sim: Any, state: np.ndarray) -> None:
    if hasattr(sim, "set_state_from_flattened"):
        sim.set_state_from_flattened(state)
        return
    sim.set_state(sim.get_state().from_flattened(state))


def _env_control_hz(env: Any) -> float:
    for attr in ("control_freq", "_control_freq"):
        value = getattr(env, attr, None)
        if value is not None:
            return float(value)
    return 20.0


def _body_pos(env: Any, candidates: tuple[str, ...]) -> np.ndarray:
    sim = _sim(env)
    for name in candidates:
        try:
            return np.asarray(sim.data.body_xpos[sim.model.body_name2id(name)]).copy()
        except Exception:
            continue
    raise KeyError(f"object body not found; tried {candidates}")


def _body_quat(env: Any, candidates: tuple[str, ...]) -> np.ndarray:
    sim = _sim(env)
    for name in candidates:
        try:
            return np.asarray(sim.data.body_xquat[sim.model.body_name2id(name)]).copy()
        except Exception:
            continue
    raise KeyError(f"object body not found; tried {candidates}")


def _eef_pos(env: Any) -> np.ndarray:
    sim = _sim(env)
    for name in ("gripper0_right_grip_site", "gripper0_grip_site"):
        try:
            return np.asarray(sim.data.site_xpos[sim.model.site_name2id(name)]).copy()
        except Exception:
            continue
    raise KeyError("eef site not found")


def _object_body_candidates(task: str) -> tuple[str, ...]:
    normalized = _normalize_task(task)
    if normalized == "lift":
        return ("cube_main", "cube", "Cube")
    if normalized == "can":
        return ("Can_main", "can_main", "Can", "can")
    if normalized == "square":
        return ("SquareNut_main", "SquareNut", "square_nut", "SquareNutObject")
    return (task,)


def _table_height(env: Any) -> float | None:
    raw = _raw_env(env)
    for attr in ("table_offset", "table_full_size"):
        value = getattr(raw, attr, None)
        if value is not None and attr == "table_offset":
            return float(np.asarray(value)[2])
    return None


def _target_position(env: Any, task: str) -> np.ndarray | None:
    sim = _sim(env)
    if _normalize_task(task) == "square":
        for name in ("peg1", "peg2", "SquareNut_peg"):
            try:
                return np.asarray(sim.data.body_xpos[sim.model.body_name2id(name)]).copy()
            except Exception:
                continue
    return None


def _target_bounds(env: Any, task: str) -> tuple[np.ndarray, np.ndarray] | None:
    if _normalize_task(task) != "can":
        return None
    raw = _raw_env(env)
    for attr in ("target_bin_placements", "target_bin_pos"):
        value = getattr(raw, attr, None)
        if value is not None:
            center = np.asarray(value, dtype=np.float64).reshape(-1)[-3:]
            half = np.asarray([0.12, 0.12, 1.0])
            return center - half, center + half
    return None


def _divider_x(env: Any) -> float | None:
    sim = _sim(env)
    for name in ("bin2", "bin1"):
        try:
            return float(sim.data.body_xpos[sim.model.body_name2id(name)][0])
        except Exception:
            continue
    return None


def _raw_env(env: Any) -> Any:
    return getattr(env, "_env", env)


def _sim(env: Any) -> Any:
    if hasattr(env, "sim"):
        return env.sim
    return _raw_env(env).sim
