"""Run a RoboMimic checkpoint episode-by-episode and persist structured results."""

from __future__ import annotations

import argparse
import json
import sys
import types
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
        "--success-tail-steps",
        type=int,
        default=30,
        help="Continue the policy for this many steps after first success.",
    )
    return parser.parse_args()


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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


def _rollout_with_success_tail(
    *,
    policy: object,
    env: object,
    horizon: int,
    success_tail_steps: int,
    video_writer: object | None,
    video_skip: int,
    camera_names: list[str],
) -> dict[str, float]:
    """Run one episode and keep recording briefly after its first success.

    ``horizon`` remains the maximum number of steps allowed to *reach* success.
    Tail steps are only added after success, so failed evaluations do not become
    more expensive and the success metric is not changed by the extra footage.
    """
    policy.start_episode()  # type: ignore[attr-defined]
    obs = env.reset()  # type: ignore[attr-defined]
    obs = env.reset_to(env.get_state())  # type: ignore[attr-defined]

    total_reward = 0.0
    total_steps = 0
    video_count = 0
    succeeded = False
    tail_remaining = 0

    try:
        while total_steps < horizon or (succeeded and tail_remaining > 0):
            action = policy(ob=obs)  # type: ignore[operator]
            next_obs, reward, done, _ = env.step(action)  # type: ignore[attr-defined]
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

            first_success = not succeeded and bool(env.is_success()["task"])  # type: ignore[attr-defined]
            if first_success:
                succeeded = True
                tail_remaining = success_tail_steps
            elif succeeded and tail_remaining > 0:
                tail_remaining -= 1

            if done or (not succeeded and total_steps >= horizon) or (
                succeeded and tail_remaining <= 0
            ):
                break
            obs = deepcopy(next_obs)
    except env.rollout_exceptions as exc:  # type: ignore[attr-defined]
        print(f"WARNING: got rollout exception {exc}", flush=True)

    return {
        "Return": total_reward,
        "Horizon": float(total_steps),
        "Success_Rate": float(succeeded),
    }


def main() -> int:
    args = parse_args()
    if (
        args.n_rollouts < 1
        or args.record_videos < 0
        or args.record_videos > args.n_rollouts
        or args.video_skip < 1
        or args.success_tail_steps < 0
    ):
        raise SystemExit("n-rollouts/record-videos không hợp lệ")

    import imageio
    import numpy as np
    import torch
    from robomimic.utils import file_utils, torch_utils

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

    episodes: list[dict[str, object]] = []
    for index in range(args.n_rollouts):
        episode_seed = args.seed + index
        np.random.seed(episode_seed)
        torch.manual_seed(episode_seed)
        writer = None
        temporary_video = args.video_dir / f"episode_{index:03d}.mp4"
        if index < args.record_videos:
            writer = imageio.get_writer(temporary_video, fps=30)
        try:
            stats = _rollout_with_success_tail(
                policy=policy,
                env=env,
                horizon=horizon,
                success_tail_steps=args.success_tail_steps,
                video_writer=writer,
                video_skip=args.video_skip,
                camera_names=[args.camera_name],
            )
        finally:
            if writer is not None:
                writer.close()
        success = bool(stats["Success_Rate"])
        video_name: str | None = None
        if temporary_video.exists():
            final_video = args.video_dir / (
                f"episode_{index:03d}_{'success' if success else 'fail'}.mp4"
            )
            temporary_video.replace(final_video)
            video_name = final_video.name
        episodes.append({
            "seed": episode_seed,
            "success": success,
            "steps": int(stats["Horizon"]),
            "video": video_name,
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
            f"seed={episode_seed} success={success} steps={int(stats['Horizon'])}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
