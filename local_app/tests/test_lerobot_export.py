from __future__ import annotations

import json

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from local_app.lerobot_export import EpisodeSource, _source_arrays, export


def test_exports_panda_osc_episode_as_lerobot_v3(tmp_path):
    episode = tmp_path / "episodes" / "lift_cube_example"
    episode.mkdir(parents=True)
    (episode / "meta.json").write_text(json.dumps({"episode_id": "lift_cube_example", "task_name": "lift_cube", "control_hz": 60, "video": {"width": 640, "height": 640}}))
    pq.write_table(pa.table({"t": [0.0, 1 / 60], "qpos": [[0.0] * 9, [0.1] * 9], "action": [[0.0] * 7, [0.2] * 7]}), episode / "actions.parquet")
    (episode / "birdview.mp4").write_bytes(b"video")
    (episode / "robot0_eye_in_hand.mp4").write_bytes(b"video")

    output, episode_count, frame_count = export(tmp_path, "panda_osc")

    assert (episode_count, frame_count) == (1, 2)
    info = json.loads((output / "meta" / "info.json").read_text())
    assert info["codebase_version"] == "v3.0"
    assert info["features"]["observation.state"]["shape"] == [9]
    assert info["features"]["action"]["names"][-1] == "gripper"
    assert (output / "data" / "chunk-000" / "file-000.parquet").is_file()
    assert (output / "videos" / "observation.images.top" / "chunk-000" / "file-000.mp4").is_file()


def test_exports_manual_and_scripted_as_one_top_camera_dataset(tmp_path):
    manual = tmp_path / "batches" / "mixed" / "episodes" / "manual"
    manual.mkdir(parents=True)
    (manual / "meta.json").write_text(json.dumps({"episode_id": "manual", "task_name": "lift_cube", "control_hz": 20, "video": {"fps": 20, "width": 640, "height": 640}}))
    pq.write_table(pa.table({"qpos": [[0.0] * 9, [0.1] * 9], "action": [[0.0] * 7, [0.2] * 7]}), manual / "actions.parquet")
    (manual / "birdview.mp4").write_bytes(b"manual-video")

    scripted = tmp_path / "batches" / "mixed" / "episodes" / "scripted"
    scripted.mkdir()
    (scripted / "meta.json").write_text(json.dumps({"episode_id": "scripted", "task_name": "lift", "source": "scripted", "video": {"fps": 20, "width": 640, "height": 640}}))
    (scripted / "review.mp4").write_bytes(b"scripted-video")
    with h5py.File(scripted / "trajectory.hdf5", "w") as handle:
        demo = handle.create_group("data").create_group("demo_0")
        demo.create_dataset("actions", data=np.ones((3, 7), dtype=np.float32))
        obs = demo.create_group("obs")
        obs.create_dataset("robot0_joint_pos", data=np.ones((3, 7), dtype=np.float32))
        obs.create_dataset("robot0_gripper_qpos", data=np.ones((3, 2), dtype=np.float32))

    output, episode_count, frame_count = export(tmp_path, "mixed")

    assert (episode_count, frame_count) == (2, 5)
    info = json.loads((output / "meta" / "info.json").read_text())
    assert info["total_tasks"] == 1
    assert list((output / "videos" / "observation.images.top" / "chunk-000").glob("*.mp4")) == [
        output / "videos" / "observation.images.top" / "chunk-000" / "file-000.mp4",
        output / "videos" / "observation.images.top" / "chunk-000" / "file-001.mp4",
    ]


def test_downsamples_manual_delta_actions_without_losing_motion(tmp_path):
    trajectory = tmp_path / "actions.parquet"
    pq.write_table(pa.table({"qpos": [[float(i)] * 9 for i in range(6)], "action": [[1.0] * 6 + [float(i)] for i in range(6)]}), trajectory)
    source = EpisodeSource("ep", "lift_cube", tmp_path, "manual", trajectory, tmp_path / "video.mp4", 6, 60, 640, 640)

    state, action, timestamps = _source_arrays(source, 20)

    assert state[:, 0].tolist() == [0.0, 3.0]
    assert action[:, 0].tolist() == [3.0, 3.0]
    assert action[:, 6].tolist() == [2.0, 5.0]
    assert timestamps.tolist() == [0.0, 0.05000000074505806]
