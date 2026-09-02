"""Xoá một đợt thu scripted: dữ liệu phải đi hẳn, không chỉ mất cái tên.

Trước đây `DELETE /raw/batches/{id}` chỉ xoá dòng mô tả trong bảng. Episode
scripted nằm trong workspace dạng file nên ở lại, và trang Review dựng lại đợt
thu đó từ chính chúng — lần này hiện mã thay cho tên. Người dùng bấm xoá, thấy
đợt thu vẫn còn, bấm lại, vẫn còn.
"""

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from src.labeling.workspace import Workspace

FRAMES = 40


def _demo(group: h5py.Group, *, batch: str) -> None:
    group.attrs["telecollect_task"] = "lift"
    group.attrs["telecollect_requested_quality"] = "clean"
    group.attrs["telecollect_collection_batch_id"] = batch
    group.attrs["num_samples"] = FRAMES
    group.attrs["success"] = True
    # Quỹ đạo khác nhau giữa các demo, nếu không bộ chấm coi là trùng lặp.
    offset = abs(hash(batch)) % 100 / 1000
    group.create_dataset("actions", data=np.linspace(offset, offset + 1, FRAMES * 7).reshape(FRAMES, 7))
    group.create_dataset("rewards", data=np.linspace(0, 1, FRAMES))
    group.create_dataset("dones", data=np.zeros(FRAMES, dtype=np.int64))
    group.create_dataset("states", data=np.zeros((FRAMES, 12)))
    for name in ("obs", "next_obs"):
        obs = group.create_group(name)
        obs.create_dataset("robot0_eef_pos", data=np.zeros((FRAMES, 3)) + offset)
        obs.create_dataset("robot0_eef_quat", data=np.zeros((FRAMES, 4)))
        obs.create_dataset("robot0_joint_pos", data=np.zeros((FRAMES, 7)))
        obs.create_dataset("robot0_joint_vel", data=np.zeros((FRAMES, 7)))
        obs.create_dataset("robot0_gripper_qpos", data=np.zeros((FRAMES, 2)))
        obs.create_dataset("robot0_gripper_qvel", data=np.zeros((FRAMES, 2)))
        obs.create_dataset("object", data=np.zeros((FRAMES, 14)))


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    """Một workspace có hai đợt thu nằm chung trong một file HDF5.

    Chung file là trường hợp khó: xoá đợt thu này không được đụng đợt kia.
    """
    workspace = Workspace(tmp_path / "review").ensure()
    path = workspace.datasets_dir / "lift_clean_seed0.hdf5"
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        data.attrs["env_args"] = json.dumps({"env_name": "Lift"})
        _demo(data.create_group("demo_0"), batch="doomed")
        _demo(data.create_group("demo_1"), batch="doomed")
        _demo(data.create_group("demo_2"), batch="keeper")
    for demo in ("demo_0", "demo_1", "demo_2"):
        (workspace.videos_dir / f"lift_clean_seed0__{demo}.mp4").write_bytes(b"video")
    workspace.rescore()
    return workspace


def test_deleting_a_batch_removes_its_episodes(space: Workspace):
    removed = space.delete_batch("doomed")

    assert removed == 2
    assert "doomed" not in space.collection_batches()


def test_deleting_a_batch_keeps_the_others(space: Workspace):
    """Hai đợt thu chung một file: xoá cái này không được chạm cái kia."""
    space.delete_batch("doomed")

    remaining = space.scores()
    assert len(remaining) == 1
    # `keeper` nằm ở demo_2 và giữ nguyên khoá đó: robomimic đọc theo tên khoá,
    # đánh số lại sẽ làm mọi thứ đang trỏ tới nó hỏng theo.
    assert remaining[0]["episode_id"].endswith("::demo_2")


def test_deleting_a_batch_removes_its_videos(space: Workspace):
    space.delete_batch("doomed")

    left = sorted(path.name for path in space.videos_dir.glob("*.mp4"))
    assert left == ["lift_clean_seed0__demo_2.mp4"]


def test_deleting_the_last_batch_removes_the_file(space: Workspace):
    """File rỗng không còn demo nào thì bỏ luôn, đừng để lại vỏ."""
    space.delete_batch("doomed")
    space.delete_batch("keeper")

    assert space.datasets() == []
    assert space.scores() == []


def test_deleting_an_unknown_batch_changes_nothing(space: Workspace):
    assert space.delete_batch("khong-co-that") == 0
    assert len(space.scores()) == 3
