"""Run a RoboMimic checkpoint episode-by-episode and persist structured results."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import os
import random
import sys
import types
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Evaluation reproducibility is more important than parallel CPU throughput.
# Set these before importing NumPy, Torch, or MuJoCo through RoboMimic.
for _thread_env_var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[_thread_env_var] = "1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, required=True)
    parser.add_argument("--n-rollouts", type=int, default=20)
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--seed", type=int, default=5000)
    parser.add_argument("--record-videos", type=int, default=3)
    parser.add_argument("--camera-name", default="agentview")
    parser.add_argument("--video-skip", type=int, default=1)
    parser.add_argument(
        "--state-bank",
        type=Path,
        help="Persistent NPZ mapping evaluation seeds to initial simulator states.",
    )
    parser.add_argument(
        "--prepare-state-bank-only",
        action="store_true",
        help="Build the complete state bank, then exit before running rollouts.",
    )
    parser.add_argument(
        "--success-hold-steps",
        type=int,
        default=10,
        help="Require task success for this many consecutive steps.",
    )
    parser.add_argument(
        "--success-tail-steps",
        type=int,
        default=0,
        help="Optional extra video steps after strict success is confirmed.",
    )
    return parser.parse_args()


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows can briefly lock result.json while the API polls it. Retry the
    # atomic replace instead of aborting an otherwise healthy evaluation.
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def _install_egl_probe_fallback() -> bool:
    """Let RoboMimic use the platform-default offscreen device if egl_probe is absent.

    RoboMimic imports egl_probe unconditionally whenever video rendering is
    requested, although an empty result simply means "do not set an explicit
    render_gpu_device_id". Windows uses the project's Optimus / WGL selection
    instead, so this fallback preserves exactly that supported branch.
    """
    try:
        import egl_probe  # noqa: F401
    except ModuleNotFoundError:
        fallback = types.ModuleType("egl_probe")
        fallback.get_available_devices = lambda: []  # type: ignore[attr-defined]
        sys.modules["egl_probe"] = fallback
        return True
    return False


def _environment_chain(env: object) -> list[object]:
    """Return every distinct wrapper down to the underlying RoboSuite env."""
    chain: list[object] = []
    current = env
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        child = getattr(current, "env", None)
        if child is None:
            break
        current = child
    return chain


def _synchronize_controllers_after_reset_to(env: object) -> None:
    """Align RoboSuite controller goals with a state restored by reset_to.

    RoboMimic's RoboSuite wrapper restores the flattened MuJoCo state, but it
    does not restore controller goals or interpolators. Those values otherwise
    remain tied to the random reset that happened immediately beforehand.
    """
    raw_env = _environment_chain(env)[-1]
    sim = getattr(raw_env, "sim", None)
    if sim is not None:
        # MjSimState only contains time, qpos, and qvel. Clear transient
        # dynamics left by the random reset that preceded reset_to; otherwise
        # the same restored state and action can produce a different first
        # solver step (especially through qacc_warmstart).
        for name in ("ctrl", "qacc_warmstart", "qfrc_applied", "xfrc_applied"):
            value = getattr(sim.data, name, None)
            if value is not None:
                value[...] = 0
        sim.forward()

    for robot in getattr(raw_env, "robots", ()):
        composite = getattr(robot, "composite_controller", None)
        if composite is None:
            continue
        composite.update_state()
        for controller in getattr(composite, "part_controllers", {}).values():
            update_initial_joints = getattr(controller, "update_initial_joints", None)
            qpos_index = getattr(controller, "qpos_index", None)
            sim = getattr(controller, "sim", None)
            if update_initial_joints is not None and qpos_index is not None and sim is not None:
                # OSC uses this value for its null-space torque. Leaving it
                # attached to the preceding random reset makes the very first
                # simulation step differ despite an identical policy action.
                import numpy as np

                restored_joint_positions = np.asarray(
                    sim.data.qpos[qpos_index], dtype=np.float64
                ).copy()
                update_initial_joints(restored_joint_positions)
            else:
                update = getattr(controller, "update", None)
                if update is not None:
                    update(force=True)
        composite.reset()

    # Controller synchronization performs additional forward calls. Keep the
    # first policy step independent of any acceleration estimate they created.
    if sim is not None:
        warmstart = getattr(sim.data, "qacc_warmstart", None)
        if warmstart is not None:
            warmstart[...] = 0


def _seed_episode(seed: int, env: object) -> None:
    """Seed global RNGs and every environment wrapper before reset."""
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    for candidate in _environment_chain(env):
        if hasattr(candidate, "seed"):
            try:
                candidate.seed = seed
            except (AttributeError, TypeError):
                pass
        if hasattr(candidate, "rng"):
            try:
                rng = candidate.rng
                if isinstance(rng, np.random.Generator):
                    # Placement samplers keep a reference to the Generator
                    # created with the environment. Mutate that Generator in
                    # place; replacing env.rng would leave those samplers using
                    # the old, unseeded object.
                    rng.bit_generator.state = np.random.default_rng(seed).bit_generator.state
                elif isinstance(rng, np.random.RandomState):
                    rng.seed(seed)
                else:
                    candidate.rng = np.random.default_rng(seed)
            except (AttributeError, TypeError):
                pass


def _array_fingerprint(value: object) -> str:
    """Hash numeric data using a stable dtype and byte order."""
    import numpy as np

    array = np.asarray(value, dtype="<f8")
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def _environment_fingerprint(env: object) -> str:
    render_only_keys = {
        "camera_depths",
        "camera_heights",
        "camera_names",
        "camera_segmentations",
        "camera_widths",
        "has_offscreen_renderer",
        "has_renderer",
        "render_camera",
        "use_camera_obs",
    }

    def without_render_settings(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: without_render_settings(item)
                for key, item in value.items()
                if key not in render_only_keys
            }
        if isinstance(value, list):
            return [without_render_settings(item) for item in value]
        return value

    serialized = without_render_settings(env.serialize())  # type: ignore[attr-defined]
    encoded = json.dumps(serialized, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_bank_metadata_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _state_bank_model_path(path: Path) -> Path:
    return path.with_suffix(".xml")


def _prepare_state_bank_model(path: Path, env: object, seed: int) -> str:
    """Persist one canonical MuJoCo model shared by every evaluation run."""
    model_path = _state_bank_model_path(path.resolve())
    if model_path.is_file():
        return model_path.read_text(encoding="utf-8")

    _seed_episode(seed, env)
    env.reset()  # type: ignore[attr-defined]
    model = str(env.get_state()["model"])  # type: ignore[attr-defined]
    temporary = model_path.with_name(f".{model_path.name}.tmp")
    try:
        temporary.write_text(model, encoding="utf-8")
        temporary.replace(model_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"created evaluation state-bank model: {model_path}", flush=True)
    return model


def _prepare_state_bank(
    path: Path,
    env: object,
    seeds: list[int],
    task_name: str,
) -> dict[int, object]:
    """Load a compatible state bank, or atomically create it once."""
    import numpy as np

    path = path.resolve()
    metadata_path = _state_bank_metadata_path(path)
    environment_hash = _environment_fingerprint(env)
    expected_keys = {f"seed_{seed}" for seed in seeds}

    if path.exists() or metadata_path.exists():
        if not path.is_file() or not metadata_path.is_file():
            raise ValueError("State bank thiếu file NPZ hoặc metadata JSON")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        schema_version = metadata.get("schema_version")
        if schema_version not in {1, 2}:
            raise ValueError("State bank có schema_version không được hỗ trợ")
        if metadata.get("task_name") != task_name:
            raise ValueError("State bank không thuộc đúng task evaluation")
        if schema_version == 2 and metadata.get("environment_hash") != environment_hash:
            raise ValueError("State bank không tương thích với environment của checkpoint")
        # The warm-up process creates the complete bank, while each parallel
        # rollout worker asks for only its own contiguous subset.
        stored_seeds = metadata.get("seeds") or []
        if not set(seeds).issubset(set(stored_seeds)):
            raise ValueError("State bank không chứa đủ dải seed được yêu cầu")
        with np.load(path, allow_pickle=False) as bank:
            if not expected_keys.issubset(set(bank.files)):
                raise ValueError("State bank thiếu seed so với metadata")
            states = {
                seed: np.asarray(bank[f"seed_{seed}"], dtype=np.float64).copy()
                for seed in seeds
            }
        expected_shape = metadata.get("state_shape")
        if expected_shape is not None and any(
            list(np.asarray(state).shape) != expected_shape for state in states.values()
        ):
            raise ValueError("State bank không khớp kích thước state trong metadata")
        if schema_version == 1:
            metadata["schema_version"] = 2
            metadata["environment_hash"] = environment_hash
            temporary = metadata_path.with_name(f".{metadata_path.name}.tmp")
            try:
                temporary.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(metadata_path)
            finally:
                temporary.unlink(missing_ok=True)
            print(f"upgraded evaluation state-bank metadata: {metadata_path}", flush=True)
        return states

    states: dict[int, object] = {}
    for seed in seeds:
        _seed_episode(seed, env)
        env.reset()  # type: ignore[attr-defined]
        state = env.get_state()  # type: ignore[attr-defined]
        states[seed] = np.asarray(state["states"], dtype=np.float64).copy()

    dimensions = {np.asarray(state).shape for state in states.values()}
    if len(dimensions) != 1:
        raise ValueError("Các state trong bank không cùng kích thước")
    metadata = {
        "schema_version": 2,
        "task_name": task_name,
        "environment_hash": environment_hash,
        "seeds": seeds,
        "state_shape": list(next(iter(dimensions))),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_npz = path.with_name(f".{path.name}.tmp")
    temporary_json = metadata_path.with_name(f".{metadata_path.name}.tmp")
    try:
        with temporary_npz.open("wb") as handle:
            np.savez_compressed(handle, **{f"seed_{seed}": state for seed, state in states.items()})
        temporary_json.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_npz.replace(path)
        temporary_json.replace(metadata_path)
    finally:
        temporary_npz.unlink(missing_ok=True)
        temporary_json.unlink(missing_ok=True)
    print(f"created evaluation state bank: {path}", flush=True)
    return states


def _should_keep_video(index: int, requested_videos: int, success: bool) -> bool:
    """Keep requested leading rollouts plus every failed rollout."""
    return index < requested_videos or not success


def _rollout_with_success_tail(
    *,
    policy: object,
    env: object,
    horizon: int,
    success_hold_steps: int = 10,
    success_tail_steps: int,
    video_writer: object | None,
    video_skip: int,
    camera_names: list[str],
    episode_seed: int | None = None,
    initial_state_vector: object | None = None,
    initial_model_xml: str | None = None,
) -> dict[str, object]:
    """Run one episode and require sustained task success.

    A one-step threshold crossing is not success. The task condition must stay
    true for ``success_hold_steps`` consecutive simulator steps; a false step
    resets the streak. Hold confirmation and any optional video tail both fit
    inside ``horizon``, keeping checkpoint comparisons equally budgeted.
    """
    if episode_seed is not None:
        _seed_episode(episode_seed, env)
    if initial_state_vector is not None and initial_model_xml is not None:
        import numpy as np

        initial_state = {
            "model": initial_model_xml,
            "states": np.asarray(initial_state_vector, dtype=np.float64).copy(),
        }
    else:
        obs = env.reset()  # type: ignore[attr-defined]
        initial_state = env.get_state()  # type: ignore[attr-defined]
        if initial_state_vector is not None:
            import numpy as np

            initial_state["states"] = np.asarray(initial_state_vector, dtype=np.float64).copy()
    obs = env.reset_to(initial_state)  # type: ignore[attr-defined]
    _synchronize_controllers_after_reset_to(env)
    policy.start_episode()  # type: ignore[attr-defined]
    initial_state_hash = _array_fingerprint(initial_state["states"])

    total_reward = 0.0
    total_steps = 0
    video_count = 0
    succeeded = False
    tail_remaining = 0
    consecutive_success = 0
    best_held_steps = 0
    actions: list[object] = []
    action_prefix: list[list[float]] = []
    state_prefix_hashes: list[str] = []

    try:
        while total_steps < horizon:
            action = policy(ob=obs)  # type: ignore[operator]
            import numpy as np

            action_values = np.asarray(action, dtype=np.float64).copy()
            actions.append(action_values)
            if len(action_prefix) < 20:
                action_prefix.append(action_values.tolist())
            next_obs, reward, done, _ = env.step(action)  # type: ignore[attr-defined]
            if len(state_prefix_hashes) < 20:
                state_after_step = env.get_state()  # type: ignore[attr-defined]
                state_prefix_hashes.append(_array_fingerprint(state_after_step["states"]))
            total_reward += float(reward)
            total_steps += 1

            if video_writer is not None and video_count % video_skip == 0:
                frames = [
                    env.render(  # type: ignore[attr-defined]
                        mode="rgb_array", height=512, width=512, camera_name=camera
                    )
                    for camera in camera_names
                ]
                import numpy as np

                video_writer.append_data(np.concatenate(frames, axis=1))  # type: ignore[attr-defined]
            video_count += 1

            task_success = bool(env.is_success()["task"])  # type: ignore[attr-defined]
            if not succeeded:
                consecutive_success = consecutive_success + 1 if task_success else 0
                best_held_steps = max(best_held_steps, consecutive_success)
                if consecutive_success >= success_hold_steps:
                    succeeded = True
                    tail_remaining = success_tail_steps
            elif tail_remaining > 0:
                tail_remaining -= 1

            if done or (succeeded and tail_remaining <= 0):
                break
            obs = deepcopy(next_obs)
    except env.rollout_exceptions as exc:  # type: ignore[attr-defined]
        print(f"WARNING: got rollout exception {exc}", flush=True)

    return {
        "Return": total_reward,
        "Horizon": float(total_steps),
        "Success_Rate": float(succeeded),
        "Held_Steps": int(best_held_steps),
        "Required_Hold_Steps": int(success_hold_steps),
        "initial_state_hash": initial_state_hash,
        "action_hash": _array_fingerprint(actions),
        "first_action": action_prefix[0] if action_prefix else [],
        "action_prefix": action_prefix,
        "state_prefix_hashes": state_prefix_hashes,
    }


def main() -> int:
    args = parse_args()
    if (
        args.n_rollouts < 1
        or args.record_videos < 0
        or args.record_videos > args.n_rollouts
        or args.video_skip < 1
        or args.success_hold_steps < 1
        or args.success_tail_steps < 0
    ):
        raise SystemExit("n-rollouts/record-videos không hợp lệ")

    import imageio
    import numpy as np
    import torch
    from robomimic.utils import file_utils, torch_utils

    torch.use_deterministic_algorithms(True, warn_only=True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    device = torch_utils.get_torch_device(try_to_use_cuda=True)
    policy, checkpoint = file_utils.policy_from_checkpoint(
        ckpt_path=str(args.agent.resolve()), device=device, verbose=True
    )
    if args.horizon is None:
        config, _ = file_utils.config_from_checkpoint(ckpt_dict=checkpoint)
        horizon = int(config.experiment.rollout.horizon)
    else:
        horizon = args.horizon
    if args.record_videos > 0 and _install_egl_probe_fallback():
        print(
            "egl_probe is not installed; using the platform-default offscreen renderer.",
            flush=True,
        )
    env, _ = file_utils.env_from_checkpoint(
        ckpt_dict=checkpoint,
        env_name=None,
        render=False,
        render_offscreen=args.record_videos > 0,
        verbose=True,
    )
    serialized = env.serialize()
    task_name = str(serialized.get("env_name") or getattr(env, "name", "unknown"))
    args.video_dir.mkdir(parents=True, exist_ok=True)
    seeds = list(range(args.seed, args.seed + args.n_rollouts))
    state_bank = (
        _prepare_state_bank(args.state_bank, env, seeds, task_name)
        if args.state_bank is not None
        else None
    )
    state_bank_model = (
        _prepare_state_bank_model(args.state_bank, env, seeds[0])
        if args.state_bank is not None
        else None
    )
    if args.prepare_state_bank_only:
        return 0

    episodes: list[dict[str, object]] = []
    for index in range(args.n_rollouts):
        episode_seed = args.seed + index
        writer = None
        # Parallel workers all start at local index zero. Seed-based names keep
        # their temporary and final videos from overwriting one another.
        temporary_video = args.video_dir / f"seed_{episode_seed}.tmp.mp4"
        if args.record_videos > 0:
            writer = imageio.get_writer(temporary_video, fps=30)
        try:
            stats = _rollout_with_success_tail(
                policy=policy,
                env=env,
                horizon=horizon,
                success_hold_steps=args.success_hold_steps,
                success_tail_steps=args.success_tail_steps,
                video_writer=writer,
                video_skip=args.video_skip,
                camera_names=[args.camera_name],
                episode_seed=episode_seed,
                initial_state_vector=(
                    state_bank[episode_seed] if state_bank is not None else None
                ),
                initial_model_xml=state_bank_model,
            )
        finally:
            if writer is not None:
                writer.close()
        success = bool(stats["Success_Rate"])
        video_name: str | None = None
        if temporary_video.exists() and _should_keep_video(
            index, args.record_videos, success
        ):
            final_video = args.video_dir / (
                f"seed_{episode_seed}_{'success' if success else 'fail'}.mp4"
            )
            temporary_video.replace(final_video)
            video_name = final_video.name
        else:
            temporary_video.unlink(missing_ok=True)
        episodes.append({
            "seed": episode_seed,
            "success": success,
            "steps": int(stats["Horizon"]),
            "video": video_name,
            "held_steps": int(stats["Held_Steps"]),
            "required_hold_steps": int(stats["Required_Hold_Steps"]),
            "initial_state_hash": str(stats["initial_state_hash"]),
            "action_hash": str(stats["action_hash"]),
            "first_action": stats["first_action"],
            "action_prefix": stats["action_prefix"],
            "state_prefix_hashes": stats["state_prefix_hashes"],
        })
        successes = sum(bool(item["success"]) for item in episodes)
        payload: dict[str, object] = {
            "task_name": task_name,
            "num_episodes": args.n_rollouts,
            "completed_episodes": len(episodes),
            "success_rate": successes / len(episodes),
            "mean_episode_length": sum(int(item["steps"]) for item in episodes) / len(episodes),
            "episodes": episodes,
        }
        _write_result(args.result, payload)
        print(
            f"evaluation episode {index + 1}/{args.n_rollouts} "
            f"seed={episode_seed} success={success} steps={int(stats['Horizon'])} "
            f"held={int(stats['Held_Steps'])}/{int(stats['Required_Hold_Steps'])}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
