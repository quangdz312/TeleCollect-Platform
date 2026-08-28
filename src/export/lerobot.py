"""Writer định dạng LeRobot v3.

Trách nhiệm: chuyển episode teleop đã duyệt sang bố cục LeRobot để dùng được
ngay với hệ sinh thái imitation learning sẵn có.

Bố cục đích:
    {root}/
        meta/info.json          # fps, các feature, thống kê
        meta/stats.json         # min/max/mean/std cho state và action
        meta/tasks.parquet      # bảng task_index -> task
        meta/episodes/chunk-000/file-000.parquet
        data/chunk-000/file-000.parquet
        videos/{camera}/chunk-000/file-{n}.mp4

Ba quyết định đáng nói.

**Chỉ nhận teleop.** Episode scripted của web nằm trong workspace nhãn dưới
dạng HDF5 nhiều demo một file, không phải một thư mục một episode như teleop.
Ghép hai đường đọc vào cùng writer sẽ khiến cả hai đều khó đọc, nên bản này
nhận teleop và nói rõ khi bị đưa cái khác — thay vì lặng lẽ bỏ qua.

**Camera là giao của các episode.** LeRobot khai báo feature ở cấp dataset:
mọi episode phải có đúng cùng bộ camera. Một episode thiếu wrist mà vẫn khai
`observation.images.wrist` sẽ tạo dataset hỏng, nên bộ camera lấy theo giao —
episode nào cũng có thì mới ghi.

**FPS lấy theo min và chỉ hạ theo bội số nguyên.** Nội suy giữa hai tần số
không chia hết nhau sẽ bịa ra action chưa từng được ghi. Delta 6 trục cộng
dồn được (đi 2 bước nhỏ bằng đi 1 bước lớn), còn gripper là trạng thái tuyệt
đối nên lấy giá trị cuối của cửa sổ.
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

from src.export.teleop_robomimic import load_teleop_episode

#: Tên từng chiều của observation.state — 7 khớp Panda + 2 ngón kẹp.
STATE_NAMES = [*(f"panda_joint_{number}.pos" for number in range(1, 8)), "left_gripper.pos", "right_gripper.pos"]
#: Action là delta Cartesian tương đối, không phải vị trí tuyệt đối.
ACTION_NAMES = ["delta_x_ee", "delta_y_ee", "delta_z_ee", "delta_rx_ee", "delta_ry_ee", "delta_rz_ee", "gripper"]

#: Camera của web -> video key LeRobot. Thứ tự quyết định thứ tự feature.
CAMERAS = (("front", "observation.images.top"), ("wrist", "observation.images.wrist"))

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480


class LeRobotExportError(ValueError):
    """Điều người dùng cần đọc và xử lý được."""


@dataclass(frozen=True)
class LeRobotEpisodeSource:
    """Một episode teleop đã sẵn sàng ghi, kèm video đã tìm thấy trên đĩa."""

    episode_id: str
    task_name: str
    state: np.ndarray
    action: np.ndarray
    fps: int
    videos: dict[str, Path]


def _stats(values: np.ndarray) -> dict[str, object]:
    return {
        "min": values.min(axis=0).tolist(),
        "max": values.max(axis=0).tolist(),
        "mean": values.mean(axis=0).tolist(),
        "std": values.std(axis=0).tolist(),
        "count": int(values.shape[0]),
    }


def _resample(source: LeRobotEpisodeSource, target_fps: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hạ tần số về `target_fps`, chỉ khi chia hết thành bội số nguyên."""

    state, action = source.state, source.action
    ratio_float = source.fps / target_fps
    ratio = int(round(ratio_float))
    if ratio < 1 or abs(ratio_float - ratio) > 1e-6:
        raise LeRobotExportError(
            f"{source.episode_id}: không hạ an toàn được {source.fps} FPS xuống {target_fps} FPS"
        )
    if ratio > 1:
        indices = np.arange(0, len(state), ratio)
        sampled_state = state[indices]
        sampled_action = np.empty((len(indices), action.shape[1]), dtype=np.float32)
        for output_index, start in enumerate(indices):
            stop = min(int(start) + ratio, len(action))
            # Delta cộng dồn được; gripper là trạng thái nên lấy giá trị cuối.
            sampled_action[output_index, :6] = action[start:stop, :6].sum(axis=0)
            sampled_action[output_index, 6] = action[stop - 1, 6]
        state, action = sampled_state, sampled_action
    timestamps = np.arange(len(state), dtype=np.float32) / float(target_fps)
    return state, action, timestamps


def _convert_video(source: Path, target: Path, fps: int, width: int, height: int) -> None:
    """Chuẩn hoá một video về đúng fps và kích thước của dataset."""

    import imageio_ffmpeg

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(source), "-an",
        "-vf", f"fps={fps},scale={width}:{height}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise LeRobotExportError(f"Chuyển đổi video thất bại: {completed.stderr[-500:]}")


def load_sources(episodes: list[dict[str, object]]) -> list[LeRobotEpisodeSource]:
    """Đọc từng episode teleop thành mảng state/action, bỏ qua cái không đọc được.

    `episodes` dùng chung shape với `build_robomimic_dataset`: episode teleop
    mang `artifact_format == "teleop_dir"` và `episode_dir`, kèm tuỳ chọn
    `trim_start_s`/`trim_end_s`. Phần tử scripted bị bỏ qua.
    """

    result: list[LeRobotEpisodeSource] = []
    for item in episodes:
        if item.get("artifact_format") != "teleop_dir":
            continue
        directory = Path(str(item["episode_dir"]))
        try:
            loaded = load_teleop_episode(
                directory,
                trim_start_s=item.get("trim_start_s"),  # type: ignore[arg-type]
                trim_end_s=item.get("trim_end_s"),  # type: ignore[arg-type]
            )
        except (ValueError, OSError):
            continue
        videos = {
            key: directory / f"{camera}.mp4"
            for camera, key in CAMERAS
            if (directory / f"{camera}.mp4").is_file()
        }
        if not videos:
            continue
        # observation.state của LeRobot là 7 khớp + 2 ngón, ghép lại từ hai key
        # RoboMimic rời — cùng thứ tự với STATE_NAMES.
        state = np.concatenate(
            [loaded.observations["robot0_joint_pos"], loaded.observations["robot0_gripper_qpos"]],
            axis=1,
        ).astype(np.float32)
        result.append(LeRobotEpisodeSource(
            episode_id=loaded.episode_id,
            task_name=loaded.task_name,
            state=state,
            action=loaded.actions.astype(np.float32),
            fps=int(loaded.attrs["control_hz"]),
            videos=videos,
        ))
    return result


def write_lerobot_dataset(
    root: Path,
    episodes: list[dict[str, object]],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> tuple[int, int]:
    """Ghi một dataset LeRobot v3 vào `root`. Trả về (số episode, số frame).

    Ghi vào thư mục tạm rồi `os.replace` — một lần export hỏng giữa chừng
    không để lại dataset dở dang mà người khác tưởng là dùng được.
    """

    sources = load_sources(episodes)
    if not sources:
        raise LeRobotExportError(
            "Không có episode teleop nào xuất được sang LeRobot. "
            "LeRobot cần bản ghi teleop có actions.parquet và ít nhất một video."
        )

    # Feature khai báo ở cấp dataset, nên chỉ giữ camera mà MỌI episode đều có.
    shared_keys = set(sources[0].videos)
    for source in sources[1:]:
        shared_keys &= set(source.videos)
    video_keys = [key for _, key in CAMERAS if key in shared_keys]
    if not video_keys:
        raise LeRobotExportError(
            "Các episode được chọn không có chung camera nào; "
            "hãy chọn nhóm episode cùng bộ camera."
        )

    fps = min(source.fps for source in sources)
    task_indices = {task: index for index, task in enumerate(sorted({source.task_name for source in sources}))}

    temporary = root.with_name(f".{root.name}.{uuid.uuid4().hex}.tmp")
    total_frames = 0
    state_values: list[np.ndarray] = []
    action_values: list[np.ndarray] = []
    try:
        (temporary / "data" / "chunk-000").mkdir(parents=True, exist_ok=True)
        (temporary / "meta" / "episodes" / "chunk-000").mkdir(parents=True, exist_ok=True)
        for key in video_keys:
            (temporary / "videos" / key / "chunk-000").mkdir(parents=True, exist_ok=True)

        frames: dict[str, list[object]] = {
            "observation.state": [], "action": [], "timestamp": [],
            "frame_index": [], "episode_index": [], "index": [], "task_index": [],
        }
        metadata: dict[str, list[object]] = {
            "episode_index": [], "tasks": [], "length": [],
            "dataset_from_index": [], "dataset_to_index": [],
            "data/chunk_index": [], "data/file_index": [],
            "meta/episodes/chunk_index": [], "meta/episodes/file_index": [],
        }
        for key in video_keys:
            metadata[f"videos/{key}/chunk_index"] = []
            metadata[f"videos/{key}/file_index"] = []
            metadata[f"videos/{key}/from_timestamp"] = []
            metadata[f"videos/{key}/to_timestamp"] = []

        for episode_index, source in enumerate(sources):
            state, action, timestamps = _resample(source, fps)
            frame_count = len(state)
            start = total_frames
            for frame_index in range(frame_count):
                frames["observation.state"].append(state[frame_index].tolist())
                frames["action"].append(action[frame_index].tolist())
                frames["timestamp"].append(float(timestamps[frame_index]))
                frames["frame_index"].append(frame_index)
                frames["episode_index"].append(episode_index)
                frames["index"].append(total_frames + frame_index)
                frames["task_index"].append(task_indices[source.task_name])
            total_frames += frame_count
            state_values.append(state)
            action_values.append(action)

            for key in video_keys:
                _convert_video(
                    source.videos[key],
                    temporary / "videos" / key / "chunk-000" / f"file-{episode_index:03d}.mp4",
                    fps, width, height,
                )
                metadata[f"videos/{key}/chunk_index"].append(0)
                metadata[f"videos/{key}/file_index"].append(episode_index)
                metadata[f"videos/{key}/from_timestamp"].append(0.0)
                metadata[f"videos/{key}/to_timestamp"].append(
                    float(timestamps[-1]) if len(timestamps) else 0.0
                )

            metadata["episode_index"].append(episode_index)
            metadata["tasks"].append([source.task_name])
            metadata["length"].append(frame_count)
            metadata["dataset_from_index"].append(start)
            metadata["dataset_to_index"].append(total_frames)
            metadata["data/chunk_index"].append(0)
            metadata["data/file_index"].append(0)
            metadata["meta/episodes/chunk_index"].append(0)
            metadata["meta/episodes/file_index"].append(0)

        pq.write_table(
            pa.Table.from_pydict(frames),
            temporary / "data" / "chunk-000" / "file-000.parquet", compression="snappy",
        )
        pq.write_table(
            pa.Table.from_pydict(metadata),
            temporary / "meta" / "episodes" / "chunk-000" / "file-000.parquet", compression="snappy",
        )
        pq.write_table(
            pa.table({"task_index": list(task_indices.values()), "task": list(task_indices.keys())}),
            temporary / "meta" / "tasks.parquet", compression="snappy",
        )

        video_features = {
            key: {
                "dtype": "video",
                "shape": [3, height, width],
                "names": ["channels", "height", "width"],
                "info": {
                    "video.height": height, "video.width": width, "video.codec": "h264",
                    "video.pix_fmt": "yuv420p", "video.is_depth_map": False,
                    "video.fps": fps, "video.channels": 3, "has_audio": False,
                },
            }
            for key in video_keys
        }
        info = {
            "codebase_version": "v3.0",
            "robot_type": "telecollect_panda_osc",
            "total_episodes": len(sources),
            "total_frames": total_frames,
            "total_tasks": len(task_indices),
            "chunks_size": 1000,
            "data_files_size_in_mb": 100,
            "video_files_size_in_mb": 500,
            "fps": fps,
            "splits": {"train": f"0:{len(sources)}"},
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": {
                "observation.state": {"dtype": "float32", "shape": [9], "names": STATE_NAMES},
                "action": {"dtype": "float32", "shape": [7], "names": ACTION_NAMES},
                **video_features,
                "timestamp": {"dtype": "float32", "shape": [1], "names": None},
                "frame_index": {"dtype": "int64", "shape": [1], "names": None},
                "episode_index": {"dtype": "int64", "shape": [1], "names": None},
                "index": {"dtype": "int64", "shape": [1], "names": None},
                "task_index": {"dtype": "int64", "shape": [1], "names": None},
            },
        }
        (temporary / "meta" / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        (temporary / "meta" / "stats.json").write_text(
            json.dumps({
                "observation.state": _stats(np.concatenate(state_values)),
                "action": _stats(np.concatenate(action_values)),
            }, indent=2),
            encoding="utf-8",
        )
        (temporary / "README.md").write_text(
            "# TeleCollect Panda OSC LeRobot dataset\n\n"
            "Action = relative Cartesian delta `[dx, dy, dz, drx, dry, drz, gripper]`.\n",
            encoding="utf-8",
        )
        root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return len(sources), total_frames
