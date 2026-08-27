from __future__ import annotations

import json

import h5py
import numpy as np

from local_app import catalog


def test_migrates_existing_data_and_assigns_new_capture_to_active_batch(tmp_path):
    existing = [{"id": "old-teleop", "task": "lift_cube"}]
    scripted = [
        {
            "episode_id": "scripted-1",
            "task": "lift",
            "provenance": {"collection_batch_id": "lift-scripted-v1"},
        }
    ]
    catalog.sync_batches(tmp_path, existing, scripted)
    migrated = catalog.load(tmp_path)

    assert migrated["episodes"]["old-teleop"]["batch_id"]
    scripted_batch_id = migrated["episodes"]["scripted-1"]["batch_id"]
    assert migrated["batches"][scripted_batch_id]["name"] == "lift-scripted-v1"

    active = catalog.create_batch(tmp_path, "lift-aug25", "lift_cube")
    catalog.set_active_batch(tmp_path, active["id"])
    catalog.sync_batches(
        tmp_path,
        [*existing, {"id": "new-teleop", "task": "lift_cube"}],
        scripted,
    )

    updated = catalog.load(tmp_path)
    assert updated["episodes"]["new-teleop"]["batch_id"] == active["id"]


def test_legacy_closed_batch_can_be_active(tmp_path):
    batch = catalog.create_batch(tmp_path, "can-v1", "can")
    data = catalog.load(tmp_path)
    data["batches"][batch["id"]]["status"] = "closed"
    catalog.save(tmp_path, data)

    activated = catalog.set_active_batch(tmp_path, batch["id"])
    assert activated["id"] == batch["id"]
    assert catalog.load(tmp_path)["active_batch_id"] == batch["id"]


def test_manual_episode_can_be_assigned_to_selected_batch(tmp_path):
    batch = catalog.create_batch(tmp_path, "manual-lift", "lift_cube")
    source = tmp_path / "episodes" / "manual-episode-1"
    source.mkdir(parents=True)
    (source / "meta.json").write_text(json.dumps({"episode_id": "manual-episode-1", "task_name": "lift_cube"}))

    catalog.assign_episode_batch(tmp_path, "manual-episode-1", batch["id"])

    entry = catalog.load(tmp_path)["episodes"]["manual-episode-1"]
    assert entry["batch_id"] == batch["id"]
    assert (tmp_path / entry["episode_path"] / "meta.json").is_file()
    assert (tmp_path / "batches" / "manual-lift" / "batch.json").is_file()


def test_new_scripted_episode_uses_active_batch_instead_of_payload_default(tmp_path):
    lift = catalog.create_batch(tmp_path, "lift cube", "lift_cube")
    catalog.create_batch(tmp_path, "can", "pick_place_can")
    catalog.set_active_batch(tmp_path, lift["id"])

    record = {
        "episode_id": "lift-default::demo_0",
        "task": "lift",
        "provenance": {"collection_batch_id": "lift-scripted-v1.2"},
    }
    catalog.sync_batches(tmp_path, [], [record])
    data = catalog.load(tmp_path)

    assert data["episodes"][record["episode_id"]]["batch_id"] == lift["id"]
    assert {batch["name"] for batch in data["batches"].values()} == {"lift cube", "can"}


def test_repairs_old_generated_default_batch_without_deleting_episodes(tmp_path):
    lift = catalog.create_batch(tmp_path, "lift cube", "lift_cube")
    can = catalog.create_batch(tmp_path, "can", "pick_place_can")
    data = catalog.load(tmp_path)
    generated = catalog._ensure_batch(data, "lift-scripted-v1.2", "lift_cube")
    data["episodes"]["lift-old"] = {"batch_id": generated["id"]}
    data["episodes"]["can-old"] = {"batch_id": generated["id"]}
    data["active_batch_id"] = can["id"]
    data["batch_migration_completed"] = True
    catalog.save(tmp_path, data)

    records = [
        {"episode_id": "lift-old", "task": "lift", "provenance": {"collection_batch_id": "lift-scripted-v1.2"}},
        {"episode_id": "can-old", "task": "can", "provenance": {"collection_batch_id": "lift-scripted-v1.2"}},
    ]
    catalog.sync_batches(tmp_path, [], records)
    repaired = catalog.load(tmp_path)

    assert repaired["episodes"]["lift-old"]["batch_id"] == lift["id"]
    assert repaired["episodes"]["can-old"]["batch_id"] == can["id"]
    assert generated["id"] not in repaired["batches"]


def test_scripted_demo_is_materialized_as_one_episode_folder(tmp_path):
    batch = catalog.create_batch(tmp_path, "can batch", "pick_place_can")
    catalog.set_active_batch(tmp_path, batch["id"])
    source = tmp_path / "review" / "datasets" / "can_run.hdf5"
    source.parent.mkdir(parents=True)
    with h5py.File(source, "w") as handle:
        data = handle.create_group("data")
        data.attrs["env_args"] = "{}"
        demo = data.create_group("demo_7")
        demo.attrs["num_samples"] = 2
        demo.create_dataset("actions", data=np.zeros((2, 7), dtype=np.float32))
    record = {
        "episode_id": "can_run.hdf5::demo_7",
        "task": "can",
        "demo": "demo_7",
        "source": str(source),
        "length": 2,
        "provenance": {"collection_batch_id": batch["id"]},
    }

    catalog.sync_batches(tmp_path, [], [record])

    entry = catalog.load(tmp_path)["episodes"][record["episode_id"]]
    episode_dir = tmp_path / entry["episode_path"]
    assert episode_dir.parent.parent.name == "can batch"
    assert json.loads((episode_dir / "meta.json").read_text())["source"] == "scripted"
    with h5py.File(episode_dir / "trajectory.hdf5", "r") as handle:
        assert list(handle["data"]) == ["demo_0"]
