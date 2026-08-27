"""Local exporter for the TeleCollect Panda OSC → LeRobot v3 contract.

The raw recorder remains the source of truth.  This module creates a portable
LeRobot v3 folder without requiring a Hugging Face account or internet access.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import h5py


STATE_NAMES = [*(f"panda_joint_{number}.pos" for number in range(1, 8)), "left_gripper.pos", "right_gripper.pos"]
ACTION_NAMES = ["delta_x_ee", "delta_y_ee", "delta_z_ee", "delta_rx_ee", "delta_ry_ee", "delta_rz_ee", "gripper"]
VIDEO_KEY = "observation.images.top"


@dataclass(frozen=True)
class EpisodeSource:
    episode_id: str
    task_name: str
    root: Path
    source: str
    trajectory_path: Path
    top_video: Path
    rows: int
    fps: int
    width: int
    height: int


def _matrix(table: pa.Table, name: str, width: int) -> np.ndarray:
    if name not in table.column_names:
        raise ValueError(f"{name} is missing")
    values = np.asarray(table[name].to_pylist(), dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != width:
        raise ValueError(f"{name} must have {width} values per frame; got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} contains NaN or infinity")
    return values


def discover(workspace: Path, selected_ids: set[str] | None = None) -> list[EpisodeSource]:
    result: list[EpisodeSource] = []
    roots = list((workspace / "batches").glob("*/episodes/*"))
    roots.extend((workspace / "episodes").glob("*"))
    seen: set[str] = set()
    for root in sorted(roots):
        if not root.is_dir():
            continue
        try:
            meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
            episode_id = str(meta.get("episode_id") or root.name)
            if episode_id in seen:
                continue
            if selected_ids is not None and episode_id not in selected_ids:
                continue
            source = str(meta.get("source") or "manual")
            if source == "scripted":
                trajectory = root / "trajectory.hdf5"
                top_video = root / "review.mp4"
                with h5py.File(trajectory, "r") as handle:
                    demo = handle["data"]["demo_0"]
                    rows = len(demo["actions"])
                    if demo["actions"].shape[1:] != (7,):
                        continue
                    if demo["obs"]["robot0_joint_pos"].shape[1:] != (7,):
                        continue
                    if demo["obs"]["robot0_gripper_qpos"].shape[1:] != (2,):
                        continue
            else:
                trajectory = root / "actions.parquet"
                top_video = root / "birdview.mp4"
                table = pq.read_table(trajectory, columns=["qpos", "action"])
                _matrix(table, "qpos", 9)
                _matrix(table, "action", 7)
                rows = len(table)
            if not top_video.is_file():
                continue
            video = meta.get("video") if isinstance(meta.get("video"), dict) else {}
            result.append(EpisodeSource(
                episode_id=episode_id,
                task_name=str(meta.get("task_name") or "unknown"), root=root,
                source=source, trajectory_path=trajectory, top_video=top_video, rows=rows,
                fps=max(1, int(round(float(meta.get("control_hz") or video.get("fps") or (20 if source == "scripted" else 60))))),
                width=int(video.get("width") or 640), height=int(video.get("height") or 640),
            ))
            seen.add(episode_id)
        except (OSError, ValueError, KeyError, pa.ArrowException, json.JSONDecodeError):
            continue
    return result


def _source_arrays(source: EpisodeSource, target_fps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if source.source == "scripted":
        with h5py.File(source.trajectory_path, "r") as handle:
            demo = handle["data"]["demo_0"]
            state = np.concatenate([
                np.asarray(demo["obs"]["robot0_joint_pos"], dtype=np.float32),
                np.asarray(demo["obs"]["robot0_gripper_qpos"], dtype=np.float32),
            ], axis=1)
            action = np.asarray(demo["actions"], dtype=np.float32)
    else:
        table = pq.read_table(source.trajectory_path, columns=["qpos", "action"])
        state = _matrix(table, "qpos", 9)
        action = _matrix(table, "action", 7)
    if state.shape[0] != action.shape[0] or not np.isfinite(state).all() or not np.isfinite(action).all():
        raise ValueError(f"{source.episode_id}: invalid or misaligned state/action arrays")
    ratio_float = source.fps / target_fps
    ratio = int(round(ratio_float))
    if ratio < 1 or abs(ratio_float - ratio) > 1e-6:
        raise ValueError(
            f"{source.episode_id}: cannot safely resample {source.fps} FPS to {target_fps} FPS"
        )
    if ratio > 1:
        indices = np.arange(0, len(state), ratio)
        sampled_state = state[indices]
        sampled_action = np.empty((len(indices), 7), dtype=np.float32)
        for output_index, start in enumerate(indices):
            stop = min(int(start) + ratio, len(action))
            sampled_action[output_index, :6] = action[start:stop, :6].sum(axis=0)
            sampled_action[output_index, 6] = action[stop - 1, 6]
        state, action = sampled_state, sampled_action
    timestamps = np.arange(len(state), dtype=np.float32) / float(target_fps)
    return state, action, timestamps


def _copy_video(source: EpisodeSource, target: Path, fps: int, width: int, height: int) -> None:
    if source.fps == fps and source.width == width and source.height == height:
        shutil.copy2(source.top_video, target)
        return
    import imageio_ffmpeg

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(source.top_video), "-an",
        "-vf", f"fps={fps},scale={width}:{height}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(f"{source.episode_id}: video conversion failed: {completed.stderr[-500:]}")


def _stats(values: np.ndarray) -> dict[str, object]:
    return {"min": values.min(axis=0).tolist(), "max": values.max(axis=0).tolist(), "mean": values.mean(axis=0).tolist(), "std": values.std(axis=0).tolist(), "count": int(values.shape[0])}


def export(workspace: Path, name: str, selected_ids: set[str] | None = None) -> tuple[Path, int, int]:
    episodes = discover(workspace, selected_ids)
    if not episodes:
        raise ValueError("No complete episode found. LeRobot export requires trajectory data and a top/review video.")
    if selected_ids is not None:
        missing = sorted(selected_ids - {item.episode_id for item in episodes})
        if missing:
            preview = ", ".join(missing[:5])
            suffix = "..." if len(missing) > 5 else ""
            raise ValueError(f"{len(missing)} selected episode(s) are not LeRobot-ready: {preview}{suffix}")
    fps = min(item.fps for item in episodes)
    width = min(item.width for item in episodes)
    height = min(item.height for item in episodes)
    for item in episodes:
        ratio = item.fps / fps
        if abs(ratio - round(ratio)) > 1e-6:
            raise ValueError(f"Cannot combine {item.fps} FPS and {fps} FPS without unsafe interpolation")
    output = workspace / "exports" / f"{name}.lerobot"
    if output.exists():
        raise ValueError(f"Export already exists: {output.name}")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    total_frames = 0
    state_values: list[np.ndarray] = []
    action_values: list[np.ndarray] = []
    from local_app.catalog import canonical_task

    task_indices = {task: index for index, task in enumerate(sorted({canonical_task(item.task_name) for item in episodes}))}
    try:
        for path in (temporary / "data" / "chunk-000", temporary / "meta" / "episodes" / "chunk-000"):
            path.mkdir(parents=True, exist_ok=True)
        (temporary / "videos" / VIDEO_KEY / "chunk-000").mkdir(parents=True, exist_ok=True)
        frames: dict[str, list[object]] = {"observation.state": [], "action": [], "timestamp": [], "frame_index": [], "episode_index": [], "index": [], "task_index": []}
        metadata: dict[str, list[object]] = {"episode_index": [], "tasks": [], "length": [], "dataset_from_index": [], "dataset_to_index": [], "data/chunk_index": [], "data/file_index": [], "meta/episodes/chunk_index": [], "meta/episodes/file_index": []}
        metadata[f"videos/{VIDEO_KEY}/chunk_index"] = []
        metadata[f"videos/{VIDEO_KEY}/file_index"] = []
        metadata[f"videos/{VIDEO_KEY}/from_timestamp"] = []
        metadata[f"videos/{VIDEO_KEY}/to_timestamp"] = []
        for episode_index, source in enumerate(episodes):
            qpos, action, timestamps = _source_arrays(source, fps)
            frame_count = len(qpos)
            start = total_frames
            task_name = canonical_task(source.task_name)
            for frame_index in range(frame_count):
                frames["observation.state"].append(qpos[frame_index].tolist())
                frames["action"].append(action[frame_index].tolist())
                frames["timestamp"].append(float(timestamps[frame_index]))
                frames["frame_index"].append(frame_index)
                frames["episode_index"].append(episode_index)
                frames["index"].append(total_frames + frame_index)
                frames["task_index"].append(task_indices[task_name])
            total_frames += frame_count
            state_values.append(qpos); action_values.append(action)
            _copy_video(
                source,
                temporary / "videos" / VIDEO_KEY / "chunk-000" / f"file-{episode_index:03d}.mp4",
                fps, width, height,
            )
            metadata[f"videos/{VIDEO_KEY}/chunk_index"].append(0)
            metadata[f"videos/{VIDEO_KEY}/file_index"].append(episode_index)
            metadata[f"videos/{VIDEO_KEY}/from_timestamp"].append(0.0)
            metadata[f"videos/{VIDEO_KEY}/to_timestamp"].append(float(timestamps[-1]) if len(timestamps) else 0.0)
            metadata["episode_index"].append(episode_index)
            metadata["tasks"].append([task_name])
            metadata["length"].append(frame_count)
            metadata["dataset_from_index"].append(start)
            metadata["dataset_to_index"].append(total_frames)
            metadata["data/chunk_index"].append(0); metadata["data/file_index"].append(0)
            metadata["meta/episodes/chunk_index"].append(0); metadata["meta/episodes/file_index"].append(0)
        pq.write_table(pa.Table.from_pydict(frames), temporary / "data" / "chunk-000" / "file-000.parquet", compression="snappy")
        pq.write_table(pa.Table.from_pydict(metadata), temporary / "meta" / "episodes" / "chunk-000" / "file-000.parquet", compression="snappy")
        pq.write_table(pa.table({"task_index": list(task_indices.values()), "task": list(task_indices.keys())}), temporary / "meta" / "tasks.parquet", compression="snappy")
        video_features = {VIDEO_KEY: {"dtype": "video", "shape": [3, height, width], "names": ["channels", "height", "width"], "info": {"video.height": height, "video.width": width, "video.codec": "h264", "video.pix_fmt": "yuv420p", "video.is_depth_map": False, "video.fps": fps, "video.channels": 3, "has_audio": False}}}
        info = {"codebase_version": "v3.0", "robot_type": "telecollect_panda_osc", "total_episodes": len(episodes), "total_frames": total_frames, "total_tasks": len(task_indices), "chunks_size": 1000, "data_files_size_in_mb": 100, "video_files_size_in_mb": 500, "fps": fps, "splits": {"train": f"0:{len(episodes)}"}, "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet", "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4", "features": {"observation.state": {"dtype": "float32", "shape": [9], "names": STATE_NAMES}, "action": {"dtype": "float32", "shape": [7], "names": ACTION_NAMES}, **video_features, "timestamp": {"dtype": "float32", "shape": [1], "names": None}, "frame_index": {"dtype": "int64", "shape": [1], "names": None}, "episode_index": {"dtype": "int64", "shape": [1], "names": None}, "index": {"dtype": "int64", "shape": [1], "names": None}, "task_index": {"dtype": "int64", "shape": [1], "names": None}}}
        (temporary / "meta" / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        (temporary / "meta" / "stats.json").write_text(json.dumps({"observation.state": _stats(np.concatenate(state_values)), "action": _stats(np.concatenate(action_values))}, indent=2), encoding="utf-8")
        (temporary / "README.md").write_text("# TeleCollect Panda OSC LeRobot dataset\n\nAction = relative Cartesian delta `[dx, dy, dz, drx, dry, drz, gripper]`.\n", encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output, len(episodes), total_frames
