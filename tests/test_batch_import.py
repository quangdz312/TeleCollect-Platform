"""Importing a zipped desktop-app batch folder into a review workspace.

The archive is untrusted input from a file explorer, and the ingest has to put
demos back under the names they had in the app — get either wrong and episodes
either land under the wrong batch or quietly fork into new ids.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import h5py
import numpy as np
import pytest

from src.labeling.batch_import import (
    BatchImportError,
    import_batch_archive,
    manifest_of,
)
from src.labeling.workspace import Workspace

FRAMES = 24


def _write_demo(group: h5py.Group, *, quality: str, success: bool) -> None:
    group.attrs["num_samples"] = FRAMES
    group.attrs["success"] = success
    group.attrs["termination_reason"] = "success" if success else "timeout"
    group.attrs["telecollect_task"] = "lift"
    group.attrs["telecollect_requested_quality"] = quality
    group.create_dataset("actions", data=np.zeros((FRAMES, 7)))
    group.create_dataset("rewards", data=np.linspace(0, 1, FRAMES))
    group.create_dataset("dones", data=np.zeros(FRAMES, dtype=np.int64))
    group.create_dataset("states", data=np.zeros((FRAMES, 12)))
    for name in ("obs", "next_obs"):
        observations = group.create_group(name)
        observations.create_dataset("robot0_eef_pos", data=np.zeros((FRAMES, 3)))
        observations.create_dataset("robot0_gripper_qpos", data=np.zeros((FRAMES, 2)))
        observations.create_dataset("object", data=np.zeros((FRAMES, 10)))


def _episode_dir(
    root: Path, episode_id: str, *, source: str, demo: str, video: bool = True,
) -> None:
    """Mirror what `local_app.batch_storage.materialize_scripted` writes out."""

    directory = root / "episodes" / episode_id
    directory.mkdir(parents=True)
    with h5py.File(directory / "trajectory.hdf5", "w") as handle:
        data = handle.create_group("data")
        data.attrs["env_args"] = json.dumps({"env_name": "Lift"})
        data.attrs["telecollect_collection_batch_id"] = "collected-elsewhere"
        _write_demo(data.create_group("demo_0"), quality="clean", success=True)
        data.attrs["total"] = FRAMES
    (directory / "meta.json").write_text(
        json.dumps({
            "format_version": 1,
            "episode_id": episode_id,
            "task_name": "lift",
            "source": "scripted",
            "num_steps": FRAMES,
            "requested_quality": "clean",
            "recorded_success": True,
            "original_source": source,
            "original_demo": demo,
        }),
        encoding="utf-8",
    )
    if video:
        (directory / "review.mp4").write_bytes(b"rendered-playback")


def _archive(tmp_path: Path, *, nested: bool = True) -> Path:
    """Build the zip a person would make from the app's batch folder."""

    staging = tmp_path / "staging"
    root = staging / "Lift v1" if nested else staging
    root.mkdir(parents=True)
    (root / "batch.json").write_text(
        json.dumps({
            "format_version": 1,
            "id": "d1e2f3",
            "name": "Lift v1",
            "task": "lift",
            "description": "two runs",
        }),
        encoding="utf-8",
    )
    _episode_dir(root, "lift_001", source="lift_clean_seed0.hdf5", demo="demo_0")
    _episode_dir(root, "lift_002", source="lift_clean_seed0.hdf5", demo="demo_1")
    _episode_dir(root, "lift_003", source="lift_good_seed4.hdf5", demo="demo_0", video=False)

    archive_path = tmp_path / "batch.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return archive_path


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "review").ensure()


def test_demos_return_to_their_original_files_and_names(space, tmp_path):
    """Episode identity is `<file>::<demo>` — both halves must survive."""

    report = import_batch_archive(space, _archive(tmp_path), batch_id="lift-v1")

    assert report.episodes == 3
    assert sorted(path.name for path in space.datasets()) == [
        "lift_clean_seed0.hdf5",
        "lift_good_seed4.hdf5",
    ]
    with h5py.File(space.datasets_dir / "lift_clean_seed0.hdf5", "r") as handle:
        assert sorted(handle["data"]) == ["demo_0", "demo_1"]
        assert handle["data"].attrs["total"] == FRAMES * 2

    ids = {record["episode_id"] for record in space.scores()}
    assert "lift_clean_seed0.hdf5::demo_1" in ids
    assert "lift_good_seed4.hdf5::demo_0" in ids


def test_every_imported_episode_is_filed_under_the_chosen_batch(space, tmp_path):
    """The archive carried a different batch id; the caller's choice wins."""

    import_batch_archive(space, _archive(tmp_path), batch_id="lift-v1")

    batches = {
        str(record.get("provenance", {}).get("collection_batch_id"))
        for record in space.scores()
    }
    assert batches == {"lift-v1"}


def test_rendered_videos_are_carried_across(space, tmp_path):
    """The server can re-render, but only with a working GL context."""

    report = import_batch_archive(space, _archive(tmp_path), batch_id="lift-v1")

    assert report.videos == 2
    assert (space.videos_dir / "lift_clean_seed0__demo_0.mp4").is_file()
    assert (space.videos_dir / "lift_clean_seed0__demo_1.mp4").is_file()
    # The third episode had no render; that must not invent an empty file.
    assert not (space.videos_dir / "lift_good_seed4__demo_0.mp4").exists()


def test_a_flat_archive_without_a_wrapping_folder_also_works(space, tmp_path):
    report = import_batch_archive(
        space, _archive(tmp_path, nested=False), batch_id="lift-v1",
    )

    assert report.episodes == 3


def test_an_existing_collection_run_is_never_overwritten(space, tmp_path):
    """Re-importing must not rewrite recordings someone may have reviewed."""

    archive_path = _archive(tmp_path)
    import_batch_archive(space, archive_path, batch_id="lift-v1")
    before = (space.datasets_dir / "lift_clean_seed0.hdf5").read_bytes()

    with pytest.raises(BatchImportError, match="already in the workspace"):
        import_batch_archive(space, archive_path, batch_id="lift-v2")

    assert (space.datasets_dir / "lift_clean_seed0.hdf5").read_bytes() == before
    assert {
        str(record.get("provenance", {}).get("collection_batch_id"))
        for record in space.scores()
    } == {"lift-v1"}


def test_a_re_collected_run_lands_beside_the_old_one(space, tmp_path):
    """Same name, different recording: keep both.

    Refusing it would lose a genuine second take, and overwriting would rewrite
    episodes someone may have reviewed. A counter on the name -- what a file
    manager does with a duplicate -- keeps the old run untouched and the new one
    under its own episode ids.
    """

    import numpy as np

    import_batch_archive(space, _archive(tmp_path), batch_id="lift-v1")
    before = (space.datasets_dir / "lift_clean_seed0.hdf5").read_bytes()

    # Same file name, different trajectory.
    staging = tmp_path / "retake"
    root = staging / "Lift retake"
    _episode_dir(root, "lift_001", source="lift_clean_seed0.hdf5", demo="demo_0")
    with h5py.File(root / "episodes" / "lift_001" / "trajectory.hdf5", "r+") as handle:
        actions = handle["data"]["demo_0"]["actions"]
        actions[...] = np.ones_like(actions[()])
    retake = tmp_path / "retake.zip"
    with zipfile.ZipFile(retake, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())

    report = import_batch_archive(space, retake, batch_id="lift-v2")

    assert report.sources == ["lift_clean_seed0-1.hdf5"]
    assert report.skipped == []
    # The reviewed run is byte-for-byte what it was.
    assert (space.datasets_dir / "lift_clean_seed0.hdf5").read_bytes() == before
    assert {
        str(record.get("provenance", {}).get("collection_batch_id"))
        for record in space.scores()
    } == {"lift-v1", "lift-v2"}


def test_manual_episodes_are_reported_rather_than_dropped(space, tmp_path):
    staging = tmp_path / "manual"
    root = staging / "Mixed"
    _episode_dir(root, "lift_001", source="lift_clean_seed0.hdf5", demo="demo_0")
    manual = root / "episodes" / "manual_001"
    manual.mkdir(parents=True)
    (manual / "meta.json").write_text(
        json.dumps({"episode_id": "manual_001", "source": "manual"}), encoding="utf-8",
    )
    archive_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())

    report = import_batch_archive(space, archive_path, batch_id="lift-v1")

    assert report.episodes == 1
    assert report.skipped == [("manual_001", "only scripted episodes can be imported")]


def test_a_zip_cannot_write_outside_the_extraction_directory(space, tmp_path):
    """`..` members are dropped, so a traversal reads as an empty archive."""

    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../escaped.txt", "nope")

    with pytest.raises(BatchImportError, match="empty"):
        import_batch_archive(space, archive_path, batch_id="lift-v1")

    assert not (tmp_path.parent / "escaped.txt").exists()


def test_a_file_that_is_not_a_zip_is_refused(space, tmp_path):
    broken = tmp_path / "notes.zip"
    broken.write_bytes(b"this is not a zip")

    with pytest.raises(BatchImportError, match="not a readable .zip"):
        import_batch_archive(space, broken, batch_id="lift-v1")


def test_manifest_is_read_for_the_batch_name(tmp_path):
    assert manifest_of(_archive(tmp_path))["name"] == "Lift v1"
    assert manifest_of(tmp_path / "missing.zip") == {}


def _distinct_archive(tmp_path: Path, *, tag: str) -> Path:
    """Zip có các tập khác nhau thật, nên auto-gate không coi là trùng lặp.

    `_archive` ghi mọi tập bằng cùng một mảng số, nên gate từ chối chúng như
    bản sao — đúng với nó, nhưng không kiểm được việc gate có gán nhãn không.
    """

    staging = tmp_path / f"staging-{tag}"
    root = staging / "Lift v1"
    (root / "episodes").mkdir(parents=True)
    (root / "batch.json").write_text(
        json.dumps({"format_version": 1, "id": tag, "name": "Lift v1", "task": "lift"}),
        encoding="utf-8",
    )
    for index in range(2):
        directory = root / "episodes" / f"lift_{index}"
        directory.mkdir()
        with h5py.File(directory / "trajectory.hdf5", "w") as handle:
            data = handle.create_group("data")
            data.attrs["env_args"] = json.dumps({"env_name": "Lift"})
            demo = data.create_group("demo_0")
            _write_demo(demo, quality="clean", success=True)
            # Quỹ đạo riêng cho từng tập: gate băm hành động cùng trạng thái đầu
            # để tìm bản sao, nên hai tập toàn số 0 là một bản sao.
            del demo["actions"], demo["states"]
            demo.create_dataset("actions", data=np.full((FRAMES, 7), index + 1.0))
            demo.create_dataset("states", data=np.full((FRAMES, 12), index + 1.0))
            data.attrs["total"] = FRAMES
        (directory / "meta.json").write_text(
            json.dumps({
                "format_version": 1,
                "episode_id": f"lift_{index}",
                "task_name": "lift",
                "source": "scripted",
                "num_steps": FRAMES,
                "requested_quality": "clean",
                "recorded_success": True,
                "original_source": f"lift_clean_seed{index}.hdf5",
                "original_demo": "demo_0",
            }),
            encoding="utf-8",
        )
    archive_path = tmp_path / f"batch-{tag}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return archive_path


def test_import_auto_labels_instead_of_leaving_everything_pending(space, tmp_path):
    """Tập nhập vào phải mang luôn quyết định của auto-gate, không nằm chờ.

    Không có bước này thì cùng một tập được gate quyết ở nơi gửi đi lại thành
    `pending` trên máy chủ, và người dùng phải duyệt lại bằng tay đúng những gì
    máy đã quyết — auto-gate coi như không tồn tại với dữ liệu đi qua đường nhập.

    Gate duyệt hay loại là tuỳ chất lượng quỹ đạo; điều phải đúng ở đây là mọi
    tập đều được nó xử lý và có nhãn, chứ không phải nhãn nào.
    """

    report = import_batch_archive(
        space, _distinct_archive(tmp_path, tag="a"), batch_id="lift-v1",
    )

    counts = report.auto_gate
    assert counts["approved"] + counts["rejected"] == report.episodes
    records = space.labels()
    assert len(records) == report.episodes
    for record in records:
        assert record["decision_source"] == "auto_gate"
        assert record["reviewer"] == "auto-gate"


def test_import_leaves_a_human_verdict_alone(space, tmp_path):
    """Nhập lại không được ghi đè quyết định của người bằng quyết định của máy."""

    import_batch_archive(space, _distinct_archive(tmp_path, tag="a"), batch_id="lift-v1")
    episode_id = space.labels()[0]["episode_id"]
    space.append_label(
        episode_id, decision="approved", note="người xem thấy được", reviewer="tung",
    )

    # Cùng zip đó: mọi lần thu đã có trong kho nên máy chủ từ chối trọn gói,
    # đúng như thiết kế. Điều cần kiểm là nhãn của người vẫn nguyên sau đó.
    with pytest.raises(BatchImportError):
        import_batch_archive(space, _distinct_archive(tmp_path, tag="b"), batch_id="lift-v2")

    kept = {record["episode_id"]: record for record in space.labels()}[episode_id]
    assert kept["human_decision"] == "approved"
    assert kept["reviewer"] == "tung"
