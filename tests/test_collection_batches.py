from __future__ import annotations

import h5py

from src.api.datasets import _matches_scripted_export
from src.labeling.features import _provenance
from src.labeling.workspace import Workspace
from src.models.schemas import DatasetCreateRequest


def _request(batch: str | None) -> DatasetCreateRequest:
    return DatasetCreateRequest(
        name="lift-v12", task_names=["lift"], format="robomimic",
        collection_batch_id=batch,
    )


def test_scripted_export_selects_only_requested_batch() -> None:
    matching = {
        "task": "lift", "recorded_success": True,
        "provenance": {"collection_batch_id": "lift-scripted-v1.2"},
    }
    old = {
        "task": "lift", "recorded_success": True,
        "provenance": {"collection_batch_id": "legacy"},
    }

    assert _matches_scripted_export(matching, _request("lift-scripted-v1.2"))
    assert not _matches_scripted_export(old, _request("lift-scripted-v1.2"))
    assert _matches_scripted_export(old, _request(None))
    assert _matches_scripted_export(
        {"task": "lift", "recorded_success": True, "provenance": {}},
        _request("legacy"),
    )


def test_scripted_export_accepts_raw_ui_task_alias() -> None:
    request = _request("lift-scripted-v1.4-hold30").model_copy(
        update={"task_names": ["lift_cube"]},
    )
    score = {
        "task": "lift",
        "recorded_success": True,
        "provenance": {"collection_batch_id": "lift-scripted-v1.4-hold30"},
    }

    assert _matches_scripted_export(score, request)


def test_batch_id_round_trips_from_dataset_metadata(tmp_path) -> None:
    path = tmp_path / "batch.hdf5"
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        data.attrs["telecollect_collection_batch_id"] = "lift-scripted-v1.2"
        data.create_group("demo_0")
    with h5py.File(path, "r") as handle:
        provenance = _provenance(handle["data"], handle["data"]["demo_0"])

    assert provenance["collection_batch_id"] == "lift-scripted-v1.2"


def test_batch_id_separates_collection_filenames(tmp_path) -> None:
    workspace = Workspace(tmp_path)

    old = workspace.dataset_path("lift", "clean", 7)
    new = workspace.dataset_path("lift", "clean", 7, "lift-scripted-v1.2")

    assert old != new
    assert new.name == "lift_clean_seed7_lift-scripted-v1.2.hdf5"
