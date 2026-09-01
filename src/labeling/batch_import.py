"""Ingest a batch folder produced by the desktop app into a review workspace.

The desktop app keeps two views of the same recordings. ``review/datasets`` holds
the collection runs exactly as the collector wrote them — one multi-demo HDF5 per
``(task, quality, seed)`` — and ``batches/<name>/episodes/<episode>`` holds a
file-explorer-friendly copy, one directory per episode, carrying the rendered
``review.mp4`` beside a single-demo ``trajectory.hdf5``.

Only the second view is convenient to zip and hand to someone, so that is what
this module accepts. Rebuilding the first view from it is the whole job:

``meta.json`` records ``original_source`` and ``original_demo`` for every
episode, so the demos can be put back into a file with their original names.
That matters because an episode's identity here is ``<file>::<demo>``: restore
those two strings and a recording keeps the id it had in the app, so verdicts
recorded against it on either side still line up. Inventing new names would
silently fork the corpus instead.

The rendered videos are carried across for the same practical reason the zip is
used at all — the server can replay a recording itself, but only with MuJoCo and
a working offscreen GL context, and a reviewer should not wait on a render that
may not be available.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import h5py

from .features import EpisodeLoadError, load_episodes
from .workspace import Workspace, video_filename

#: Written by :func:`local_app.batch_storage.materialize_scripted`.
META_NAME = "meta.json"
TRAJECTORY_NAME = "trajectory.hdf5"
VIDEO_NAME = "review.mp4"
MANIFEST_NAME = "batch.json"

#: The provenance attribute the review corpus groups episodes by.
BATCH_ATTR = "telecollect_collection_batch_id"

#: Dataset filenames become URLs and filesystem paths, matching `workspace._SAFE`.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class BatchImportError(ValueError):
    """The archive is not a batch folder this workspace can accept."""


@dataclass
class ImportReport:
    """What the archive actually contributed, and what it did not."""

    batch_id: str
    episodes: int = 0
    videos: int = 0
    sources: list[str] = field(default_factory=list)
    #: ``(episode, reason)`` — one line per recording left out, so a partial
    #: import can be explained instead of quietly losing rows.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "episodes": self.episodes,
            "videos": self.videos,
            "sources": sorted(self.sources),
            "skipped": [{"episode": name, "reason": why} for name, why in self.skipped],
        }


def _safe_members(archive: zipfile.ZipFile) -> Iterator[zipfile.ZipInfo]:
    """Yield members that stay inside the extraction directory.

    A zip carries whatever paths the writer put in it, including ``..`` segments
    and absolute paths. Nothing here is trusted enough to hand to ``extractall``.
    """

    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0][1:]:
            continue
        yield info


def _extract(archive_path: Path, target: Path) -> Path:
    """Unpack the archive and return the directory holding ``episodes/``."""

    try:
        handle = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise BatchImportError("The upload is not a readable .zip archive") from exc
    with handle as archive:
        members = list(_safe_members(archive))
        if not members:
            raise BatchImportError("The archive is empty")
        for info in members:
            destination = target / info.filename.replace("\\", "/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink)

    # Zipping a folder from the file explorer usually nests everything one level
    # down, so the batch root is wherever `episodes/` turned up, not the top.
    candidates = sorted(
        (path.parent for path in target.rglob("episodes") if path.is_dir()),
        key=lambda path: len(path.parts),
    )
    if not candidates:
        raise BatchImportError(
            "No `episodes/` directory in the archive — zip a batch folder from the app's "
            "workspace, the one holding batch.json beside episodes/"
        )
    return candidates[0]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def manifest_of(archive_path: Path) -> dict[str, Any]:
    """Return ``batch.json`` from the archive, or an empty mapping."""

    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in _safe_members(archive):
                if Path(info.filename.replace("\\", "/")).name != MANIFEST_NAME:
                    continue
                with archive.open(info) as handle:
                    value = json.loads(handle.read().decode("utf-8"))
                if isinstance(value, dict):
                    return value
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return {}


def _collect(root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, str]]]:
    """Group the archive's episode directories by the file they came from."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    skipped: list[tuple[str, str]] = []
    for directory in sorted((root / "episodes").iterdir()):
        if not directory.is_dir():
            continue
        meta = _read_json(directory / META_NAME)
        if meta is None:
            skipped.append((directory.name, f"no readable {META_NAME}"))
            continue
        episode_id = str(meta.get("episode_id") or directory.name)
        if str(meta.get("source") or "") != "scripted":
            # Manual takes are `actions.parquet` directories on a different
            # ingest path; importing them here would need the teleop pipeline.
            skipped.append((episode_id, "only scripted episodes can be imported"))
            continue
        original_source = str(meta.get("original_source") or "")
        original_demo = str(meta.get("original_demo") or "")
        if not original_source or not original_demo:
            skipped.append((episode_id, "meta.json has no original_source/original_demo"))
            continue
        if not _SAFE_NAME.match(original_source) or not _SAFE_NAME.match(original_demo):
            skipped.append((episode_id, f"unsafe source name {original_source!r}"))
            continue
        if not (directory / TRAJECTORY_NAME).is_file():
            skipped.append((episode_id, f"no {TRAJECTORY_NAME}"))
            continue
        grouped.setdefault(original_source, []).append(
            {"directory": directory, "demo": original_demo, "episode_id": episode_id},
        )
    return grouped, skipped


def _collection_fingerprint(path: Path) -> str:
    """Vân tay nội dung của một file collection, không phụ thuộc byte của HDF5.

    Băm chính file thì không dùng được: h5py ghi ra byte khác nhau giữa hai lần
    dựng cùng một dữ liệu (thứ tự thuộc tính, vùng đệm), nên hai bản y hệt vẫn
    ra hai vân tay. Băm tên demo cùng mảng số bên trong thì hai lần upload cùng
    một lần thu luôn khớp.
    """

    digest = hashlib.sha256()
    try:
        with h5py.File(path, "r") as handle:
            data = handle.get("data")
            if data is None:
                return ""
            for demo in sorted(data):
                digest.update(demo.encode("utf-8"))
                group = data[demo]
                for name in sorted(group):
                    value = group[name]
                    if isinstance(value, h5py.Dataset):
                        digest.update(name.encode("utf-8"))
                        digest.update(value[()].tobytes())
    except (OSError, KeyError, ValueError):
        return ""
    return digest.hexdigest()


def _unused_path(path: Path) -> Path:
    """``path``, hoặc ``path`` với ``-1``, ``-2``… chèn trước đuôi file.

    Cách một trình quản lý file đặt tên khi trùng: giữ tên cũ, thêm số đếm.
    Gạch nối chứ không phải ``(1)`` vì tên này đi vào ``episode_id``, mà
    ``_SAFE_NAME`` không nhận dấu ngoặc.
    """

    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise BatchImportError(f"Quá nhiều bản trùng tên với {path.name}")


def _rebuild(entries: list[dict[str, Any]], destination: Path, batch_id: str) -> int:
    """Write one multi-demo collection file from single-demo episode folders.

    Each demo keeps the name it had in the app so episode ids survive the round
    trip. The batch attribute is stamped on both the file and every demo because
    :func:`src.labeling.features._provenance` lets the demo-level value win, and
    a stale id left on a demo would file the recording under the wrong batch.
    """

    written = 0
    with h5py.File(destination, "w") as output:
        data = output.create_group("data")
        for entry in sorted(entries, key=lambda item: str(item["demo"])):
            with h5py.File(entry["directory"] / TRAJECTORY_NAME, "r") as source:
                if "data" not in source or "demo_0" not in source["data"]:
                    raise BatchImportError(
                        f"{entry['episode_id']}: {TRAJECTORY_NAME} has no data/demo_0",
                    )
                if not data.attrs:
                    for key, value in source["data"].attrs.items():
                        data.attrs[key] = value
                source.copy(source["data"]["demo_0"], data, name=str(entry["demo"]))
            data[str(entry["demo"])].attrs[BATCH_ATTR] = batch_id
            written += 1
        data.attrs[BATCH_ATTR] = batch_id
        data.attrs["total"] = sum(
            int(data[name].attrs.get("num_samples", 0)) for name in data
        )
    return written


def import_batch_archive(
    space: Workspace, archive_path: Path, *, batch_id: str, overwrite: bool = False,
) -> ImportReport:
    """Import a zipped app batch folder into ``space`` under ``batch_id``.

    Every recording in the archive is filed under ``batch_id`` regardless of the
    batch it carried, because the caller picked that batch explicitly and two
    ids for one upload would be a surprise.
    """

    if not _SAFE_NAME.match(batch_id):
        raise BatchImportError(f"Unsafe batch id: {batch_id!r}")

    report = ImportReport(batch_id=batch_id)
    space.ensure()
    with TemporaryDirectory(prefix="batch-import-") as scratch:
        root = _extract(archive_path, Path(scratch))
        grouped, report.skipped = _collect(root)
        if not grouped:
            raise BatchImportError(
                "No importable scripted episodes in the archive"
                + (f" ({report.skipped[0][1]})" if report.skipped else ""),
            )

        staged: list[tuple[Path, Path, list[dict[str, Any]]]] = []
        for source_name, entries in sorted(grouped.items()):
            destination = space.datasets_dir / source_name
            temporary = Path(scratch) / f"staged-{source_name}"
            _rebuild(entries, temporary, batch_id)
            # Fail before touching the corpus if the rebuild is unreadable.
            load_episodes(temporary)

            if destination.exists() and not overwrite:
                # Tên trùng nói lên hai chuyện khác hẳn nhau, nên phải so nội
                # dung mới biết là chuyện nào.
                #
                # Cùng nội dung: người dùng upload lại đúng thứ đã gửi. Nhận
                # thêm một bản là nhân đôi episode trong tập train, khiến model
                # thấy quỹ đạo đó nặng gấp đôi những quỹ đạo khác — làm lệch dữ
                # liệu mà không ai nhận ra. Bỏ qua và nói rõ.
                #
                # Khác nội dung: đây là lần thu mới trùng tên. Đặt cạnh bản cũ
                # với số đếm; bản cũ có thể đã được review nên không đụng tới.
                # Episode id sinh ra từ tên file nên cũng đổi theo, hai bản
                # không giẫm lên nhau trong bảng review.
                if _collection_fingerprint(temporary) == _collection_fingerprint(destination):
                    for entry in entries:
                        report.skipped.append(
                            (
                                str(entry["episode_id"]),
                                f"{source_name} is already in this workspace",
                            ),
                        )
                    continue
                destination = _unused_path(destination)

            staged.append((temporary, destination, entries))

        if not staged:
            raise BatchImportError(
                "Every collection run in this archive is already in the workspace",
            )

        for temporary, destination, entries in staged:
            shutil.move(str(temporary), destination)
            report.sources.append(destination.name)
            report.episodes += len(entries)
            for entry in entries:
                video = entry["directory"] / VIDEO_NAME
                if not video.is_file():
                    continue
                target = space.videos_dir / video_filename(
                    f"{destination.name}::{entry['demo']}",
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(video, target)
                report.videos += 1

    # Scoring is relative to the rest of the corpus, so it has to run over
    # everything — which means one unreadable file already in the workspace can
    # stop it. Say which, instead of surfacing a bare loader error: the imported
    # runs are on disk by now and will be picked up by the next successful
    # rescore.
    try:
        space.rescore()
    except EpisodeLoadError as exc:
        raise BatchImportError(
            f"The batch was imported but the workspace could not be rescored: {exc}",
        ) from exc
    return report
