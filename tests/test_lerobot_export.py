"""Xuất episode teleop đã duyệt sang bố cục LeRobot v3.

Điều đáng test không phải là "có ghi ra file không" mà là dataset ghi ra có
đọc lại đúng không: chỉ số frame phải liên tục qua các episode, camera khai
báo phải khớp video thật sự có, và việc hạ tần số không được bịa ra chuyển
động chưa từng xảy ra.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.export.lerobot import (
    LeRobotExportError,
    load_sources,
    write_lerobot_dataset,
)


def _teleop(root: Path, *, control_hz: int = 10, frames: int = 5, wrist: bool = True) -> Path:
    """Một bản ghi teleop schema-v2 tối thiểu nhưng hợp lệ."""

    root.mkdir(parents=True)
    timestamps = [index / control_hz for index in range(frames)]
    qpos = np.arange(frames * 9, dtype=np.float64).reshape(frames, 9) / 100.0
    qvel = qpos + 0.5
    ee_pose = np.zeros((frames, 7), dtype=np.float64)
    ee_pose[:, :3] = np.arange(frames * 3).reshape(frames, 3) / 10.0
    ee_pose[:, 3] = 1.0  # identity, MuJoCo wxyz
    actions = np.arange(frames * 7, dtype=np.float64).reshape(frames, 7) / 10.0
    states = np.arange(frames * 12, dtype=np.float64).reshape(frames, 12)
    objects = np.arange(frames * 10, dtype=np.float64).reshape(frames, 10) / 100.0
    pq.write_table(
        pa.table({
            "t": timestamps,
            "qpos": qpos.tolist(),
            "qvel": qvel.tolist(),
            "ee_pose": ee_pose.tolist(),
            "privileged_state": states.tolist(),
            "object": objects.tolist(),
            "action": actions.tolist(),
        }),
        root / "actions.parquet",
    )
    (root / "meta.json").write_text(
        json.dumps({
            "episode_id": root.name,
            "task_name": "lift_cube",
            "operator_id": "operator-1",
            "num_steps": frames,
            "control_hz": control_hz,
            "teleop_schema_version": 2,
            "task_success": True,
        }),
        encoding="utf-8",
    )
    _fake_video(root / "front.mp4")
    if wrist:
        _fake_video(root / "wrist.mp4")
    return root


def _fake_video(path: Path) -> None:
    """Một mp4 thật, đủ ngắn để ffmpeg đọc được trong test."""

    import imageio_ffmpeg
    import subprocess

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi",
            "-i", "testsrc=duration=1:size=64x64:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, check=True,
    )


def _item(directory: Path, **overrides: object) -> dict[str, object]:
    return {
        "episode_id": directory.name,
        "artifact_format": "teleop_dir",
        "episode_dir": str(directory),
        **overrides,
    }


# --- đọc nguồn ---------------------------------------------------------------


def test_only_teleop_episodes_are_read(tmp_path: Path) -> None:
    """Episode scripted nằm trong HDF5 nhiều demo, writer này không đọc được."""

    directory = _teleop(tmp_path / "ep1")

    assert load_sources([_item(directory, artifact_format="hdf5")]) == []


def test_an_episode_without_any_video_is_skipped(tmp_path: Path) -> None:
    """LeRobot khai báo feature video ở cấp dataset; không video thì vô nghĩa."""

    directory = _teleop(tmp_path / "ep1")
    (directory / "front.mp4").unlink()
    (directory / "wrist.mp4").unlink()

    assert load_sources([_item(directory)]) == []


def test_state_is_seven_joints_then_two_fingers(tmp_path: Path) -> None:
    """Thứ tự phải khớp STATE_NAMES, nếu không mọi chiều đều lệch nhãn."""

    directory = _teleop(tmp_path / "ep1")

    source = load_sources([_item(directory)])[0]

    assert source.state.shape[1] == 9
    assert source.action.shape[1] == 7
    assert source.fps == 10


# --- ghi dataset -------------------------------------------------------------


def test_writes_a_readable_v3_dataset(tmp_path: Path) -> None:
    directory = _teleop(tmp_path / "ep1")
    root = tmp_path / "out.lerobot"

    episodes, frames = write_lerobot_dataset(root, [_item(directory)])

    assert episodes == 1
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["codebase_version"] == "v3.0"
    assert info["total_frames"] == frames
    assert info["fps"] == 10
    table = pq.read_table(root / "data" / "chunk-000" / "file-000.parquet")
    assert table.num_rows == frames
    assert (root / "meta" / "stats.json").is_file()
    assert (root / "meta" / "tasks.parquet").is_file()


def test_frame_index_is_continuous_across_episodes(tmp_path: Path) -> None:
    """`index` là con trỏ toàn dataset — đứt quãng là hỏng khi train."""

    first = _teleop(tmp_path / "ep1")
    second = _teleop(tmp_path / "ep2")
    root = tmp_path / "out.lerobot"

    _, frames = write_lerobot_dataset(root, [_item(first), _item(second)])

    table = pq.read_table(root / "data" / "chunk-000" / "file-000.parquet")
    assert table["index"].to_pylist() == list(range(frames))
    assert sorted(set(table["episode_index"].to_pylist())) == [0, 1]


def test_only_cameras_every_episode_has_are_declared(tmp_path: Path) -> None:
    """Khai báo wrist khi một episode thiếu sẽ tạo dataset hỏng."""

    both = _teleop(tmp_path / "ep1")
    front_only = _teleop(tmp_path / "ep2", wrist=False)
    root = tmp_path / "out.lerobot"

    write_lerobot_dataset(root, [_item(both), _item(front_only)])

    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert "observation.images.top" in info["features"]
    assert "observation.images.wrist" not in info["features"]
    assert not (root / "videos" / "observation.images.wrist").exists()


def test_a_slower_episode_sets_the_dataset_rate(tmp_path: Path) -> None:
    """Lấy min và hạ theo bội số nguyên; 20 Hz xuống 10 Hz là gộp 2 frame."""

    fast = _teleop(tmp_path / "ep1", control_hz=20, frames=9)
    slow = _teleop(tmp_path / "ep2", control_hz=10, frames=5)
    root = tmp_path / "out.lerobot"

    write_lerobot_dataset(root, [_item(fast), _item(slow)])

    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["fps"] == 10


def test_a_rate_that_cannot_be_halved_is_refused(tmp_path: Path) -> None:
    """Nội suy 15 Hz xuống 10 Hz sẽ bịa ra action chưa từng được ghi."""

    odd = _teleop(tmp_path / "ep1", control_hz=15, frames=7)
    even = _teleop(tmp_path / "ep2", control_hz=10, frames=5)

    with pytest.raises(LeRobotExportError, match="FPS"):
        write_lerobot_dataset(tmp_path / "out.lerobot", [_item(odd), _item(even)])


def test_nothing_exportable_says_so(tmp_path: Path) -> None:
    with pytest.raises(LeRobotExportError, match="teleop"):
        write_lerobot_dataset(tmp_path / "out.lerobot", [])


def test_a_failed_export_leaves_no_half_written_folder(tmp_path: Path) -> None:
    """Một dataset dở dang trông y hệt dataset thật cho tới lúc train."""

    odd = _teleop(tmp_path / "ep1", control_hz=15, frames=7)
    even = _teleop(tmp_path / "ep2", control_hz=10, frames=5)
    root = tmp_path / "out.lerobot"

    with pytest.raises(LeRobotExportError):
        write_lerobot_dataset(root, [_item(odd), _item(even)])

    assert not root.exists()
    assert list(tmp_path.glob(".*tmp")) == []
