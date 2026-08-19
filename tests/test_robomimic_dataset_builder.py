from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from src.services.robomimic_dataset_builder import build_robomimic_hdf5
from src.sim.collection.schema_validator import validate_training_dataset
from src.training.robomimic_bc import inspect_training_dataset


def _source(path: Path, count: int = 4) -> None:
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        data.attrs["env_args"] = json.dumps({"env_name": "Lift", "env_kwargs": {"robots": "Panda"}})
        total = 0
        for index in range(count):
            length = index + 2
            demo = data.create_group(f"demo_{index}")
            demo.attrs["num_samples"] = length
            demo.create_dataset("actions", data=np.zeros((length, 7), dtype=np.float32))
            demo.create_dataset("states", data=np.zeros((length, 10), dtype=np.float32))
            demo.create_dataset("rewards", data=np.zeros(length, dtype=np.float32))
            demo.create_dataset("dones", data=np.zeros(length, dtype=np.uint8))
            for group_name in ("obs", "next_obs"):
                group = demo.create_group(group_name)
                group.create_dataset("robot0_eef_pos", data=np.zeros((length, 3), dtype=np.float32))
            total += length
        data.attrs["total"] = total


def _episodes(source: Path, count: int = 4) -> list[dict[str, object]]:
    return [
        {
            "source_path": str(source),
            "demo": f"demo_{index}",
            "episode_id": f"lift-{index}",
            "decision": "approved",
            "reviewer": "tester",
        }
        for index in range(count)
    ]


def test_export_has_deterministic_disjoint_train_valid_masks(tmp_path: Path) -> None:
    source = tmp_path / "raw.hdf5"
    first = tmp_path / "first.hdf5"
    second = tmp_path / "second.hdf5"
    _source(source)
    episodes = _episodes(source)
    assert build_robomimic_hdf5(first, episodes) == (4, 14)
    build_robomimic_hdf5(second, episodes)

    with h5py.File(first, "r") as a, h5py.File(second, "r") as b:
        env_args = json.loads(a["data"].attrs["env_args"])
        assert env_args["type"] == 1
        assert env_args["env_version"]
        assert env_args["env_kwargs"]["controller_configs"]["type"] == "BASIC"
        assert env_args["env_kwargs"]["controller_configs"]["body_parts"]["right"][
            "gripper"
        ] == {"type": "GRIP"}
        train = set(a["mask/train"].asstr()[...])
        valid = set(a["mask/valid"].asstr()[...])
        assert train == set(b["mask/train"].asstr()[...])
        assert valid == set(b["mask/valid"].asstr()[...])
        assert train.isdisjoint(valid)
        assert train | valid == {f"demo_{index}" for index in range(4)}
        assert len(valid) == 1

    result = validate_training_dataset(first)
    assert result.valid, result.errors


def test_training_dry_run_plan_uses_dataset_observations(tmp_path: Path) -> None:
    source = tmp_path / "raw.hdf5"
    output = tmp_path / "export.hdf5"
    _source(source, count=1)
    build_robomimic_hdf5(output, _episodes(source, count=1))

    plan, result = inspect_training_dataset(
        output,
        output_dir=tmp_path / "runs",
        name="smoke",
        epochs=1,
        batch_size=2,
        num_workers=0,
        device="cpu",
    )
    assert result.valid
    assert plan.observation_keys == ("robot0_eef_pos",)
    assert plan.train_demos == 1
    assert plan.valid_demos == 0
    assert not plan.validation_enabled


def test_training_dry_run_accepts_bc_rnn_policy(tmp_path: Path) -> None:
    source = tmp_path / "raw.hdf5"
    output = tmp_path / "export.hdf5"
    _source(source, count=2)
    build_robomimic_hdf5(output, _episodes(source, count=2))

    plan, result = inspect_training_dataset(
        output,
        output_dir=tmp_path / "runs",
        name="bcrnn-smoke",
        epochs=2,
        batch_size=2,
        num_workers=0,
        device="cpu",
        policy="bc-rnn",
    )

    assert result.valid
    assert plan.policy == "bc-rnn"
    assert plan.train_demos == 1
    assert plan.valid_demos == 1
    assert plan.validation_enabled


def test_normalized_minimal_plan_uses_rollout_instead_of_validation(tmp_path: Path) -> None:
    source = tmp_path / "raw.hdf5"
    output = tmp_path / "export.hdf5"
    _source(source, count=2)
    with h5py.File(source, "a") as handle:
        for demo in handle["data"].values():
            length = len(demo["actions"])
            for group_name in ("obs", "next_obs"):
                group = demo[group_name]
                group.create_dataset("object", data=np.zeros((length, 14), dtype=np.float32))
                group.create_dataset("robot0_eef_quat", data=np.zeros((length, 4), dtype=np.float32))
                group.create_dataset("robot0_gripper_qpos", data=np.zeros((length, 2), dtype=np.float32))
    build_robomimic_hdf5(output, _episodes(source, count=2))

    plan, result = inspect_training_dataset(
        output,
        output_dir=tmp_path / "runs",
        name="normalized-square",
        epochs=2,
        batch_size=2,
        num_workers=0,
        device="cpu",
        policy="bc-rnn",
        normalize_observations=True,
        observation_profile="minimal",
        rollout_enabled=True,
    )

    assert result.valid
    assert plan.observation_keys == (
        "object",
        "robot0_eef_pos",
        "robot0_eef_quat",
        "robot0_gripper_qpos",
    )
    assert plan.normalize_observations is True
    assert plan.validation_enabled is False
    assert plan.rollout_enabled is True
