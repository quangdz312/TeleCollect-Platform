"""The review workspace: one directory holding a corpus and its verdicts.

```text
<root>/
  datasets/  one .hdf5 per (task, quality, seed) collection run
  videos/    rendered playback, cached; safe to delete, will be rebuilt
  scores.jsonl   derived — rewritten in full on every rescore
  labels.jsonl   append-only — the only thing here that cannot be regenerated
```

Two rules shape this module.

**Scores are derived, labels are not.** ``scores.jsonl`` is a cache of a pure
function of the datasets and the scorer version, so it is rewritten wholesale
and never merged. ``labels.jsonl`` is a record of what a person decided; it is
only ever appended to, and a changed verdict is a new line rather than an edit,
so the history of a disputed episode survives.

**Scoring is a batch operation over the whole corpus.** Three penalties are
defined relative to other episodes of the same task, so adding one collection
run changes the scores of the runs already there. Every collection therefore
rescores everything — which is cheap, since it is numpy over already-collected
arrays with no simulator involved.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .features import load_episodes
from .reasons import validate as validate_reasons
from .score import DEFAULT_SCORER, ScorerConfig, score_episodes

#: Filenames are derived, so they have to survive a round trip through a URL and
#: a filesystem. Anything outside this set is refused rather than sanitised.
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Replace a file atomically so a crash mid-write cannot truncate it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _record_task_slug(task: Any) -> str:
    """Return the stable, display-safe part of a recording name."""

    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(task or "task")).strip("_").lower()
    return value or "task"


def dedupe_labels(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Latest verdict wins, in first-seen order.

    The log is append-only, so a reviewer who changes their mind leaves two
    lines for one episode. Counting both would let a single episode vote twice
    in the threshold derivation.
    """

    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[str(record["episode_id"])] = dict(record)
    return list(latest.values())


def video_filename(episode_id: str) -> str:
    """``lift_poor_seed0.hdf5::demo_3`` -> ``lift_poor_seed0__demo_3.mp4``."""

    source, _, demo = episode_id.partition("::")
    return f"{Path(source).stem}__{demo}.mp4"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def from_settings(cls) -> Workspace:
        from src.config import get_settings

        return cls(Path(get_settings().review_dir))

    @property
    def datasets_dir(self) -> Path:
        return self.root / "datasets"

    @property
    def videos_dir(self) -> Path:
        return self.root / "videos"

    @property
    def scores_path(self) -> Path:
        return self.root / "scores.jsonl"

    @property
    def labels_path(self) -> Path:
        return self.root / "labels.jsonl"

    @property
    def record_counters_path(self) -> Path:
        """Persistent names for recordings, independent of HDF5 demo keys."""

        return self.root / "record_counters.json"

    def ensure(self) -> Workspace:
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        return self

    # --- datasets -----------------------------------------------------------

    def datasets(self) -> list[Path]:
        return sorted(self.datasets_dir.glob("*.hdf5"))

    def dataset_path(
        self, task: str, quality: str, seed: int, collection_batch_id: str = '',
    ) -> Path:
        """One file per (task, quality, seed).

        The name is derived from the inputs rather than a timestamp so that
        re-running a batch collides with itself instead of silently producing a
        second copy of the same deterministic episodes for someone to review
        twice.
        """

        batch_suffix = f"_{collection_batch_id}" if collection_batch_id else ""
        name = f"{task}_{quality}_seed{seed}{batch_suffix}.hdf5"
        if not _SAFE.match(name):
            raise ValueError(f"unsafe dataset name: {name!r}")
        return self.datasets_dir / name

    def next_free_seed(
        self, task: str, quality: str, *, start: int = 0,
        collection_batch_id: str = '',
    ) -> int:
        seed = start
        while self.dataset_path(task, quality, seed, collection_batch_id).exists():
            seed += 1
        return seed

    def video_path(self, episode_id: str) -> Path:
        name = video_filename(episode_id)
        if not _SAFE.match(name):
            raise ValueError(f"unsafe video name: {name!r}")
        return self.videos_dir / name

    def resolve_source(self, source: str) -> Path:
        """Map a score record's ``source`` back into this workspace.

        Scores may have been produced elsewhere, and a path out of a JSON file
        is untrusted input. Only the basename is used, and only if it names a
        dataset that actually lives here.
        """

        candidate = self.datasets_dir / Path(source).name
        if not candidate.exists():
            raise FileNotFoundError(f"{source} is not in {self.datasets_dir}")
        return candidate

    # --- scores -------------------------------------------------------------

    def scores(self) -> list[dict[str, Any]]:
        records = _read_jsonl(self.scores_path)
        return self._ensure_record_names(records)

    def _ensure_record_names(self, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Assign names once, preserving them when data is rescored or removed.

        The HDF5 key remains ``demo_0``/``demo_1`` for robomimic compatibility;
        this metadata is only the human-facing name shown in the web review.
        """

        if not records:
            return []
        metadata: dict[str, Any] = {}
        if self.record_counters_path.exists():
            try:
                metadata = json.loads(self.record_counters_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        names = dict(metadata.get("names", {}))
        next_by_task = {str(key): int(value) for key, value in metadata.get("next_by_task", {}).items()}
        changed = False
        result: list[dict[str, Any]] = []
        for raw in records:
            record = dict(raw)
            episode_id = str(record.get("episode_id", ""))
            display_name = names.get(episode_id)
            if not display_name:
                task = _record_task_slug(record.get("task"))
                number = next_by_task.get(task, 1)
                display_name = f"{task}_{number:03d}"
                names[episode_id] = display_name
                next_by_task[task] = number + 1
                changed = True
            if record.get("display_name") != display_name:
                record["display_name"] = display_name
                changed = True
            result.append(record)
        if changed:
            self.record_counters_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.record_counters_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"version": 1, "next_by_task": next_by_task, "names": names}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.record_counters_path)
            _write_jsonl(self.scores_path, result)
        return result

    def scores_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(record["episode_id"]): record for record in self.scores()}

    def rescore(self, *, config: ScorerConfig = DEFAULT_SCORER) -> list[dict[str, Any]]:
        """Rebuild ``scores.jsonl`` from every dataset in the workspace."""

        episodes = []
        for path in self.datasets():
            episodes.extend(load_episodes(path))
        summaries = []
        if episodes:
            scored, _ = score_episodes(episodes, config=config)
            summaries = [item.summary() for item in scored]
        summaries = self._ensure_record_names(summaries)
        _write_jsonl(self.scores_path, summaries)
        return summaries

    # --- labels -------------------------------------------------------------

    def labels(self, *, latest_only: bool = True) -> list[dict[str, Any]]:
        records = _read_jsonl(self.labels_path)
        return dedupe_labels(records) if latest_only else records

    def labels_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(record["episode_id"]): record for record in self.labels()}

    def append_label(
        self,
        episode_id: str,
        *,
        decision: str,
        reasons: Sequence[str] = (),
        note: str = "",
        reviewer: str = "unknown",
        blind: bool = True,
        decision_source: str = "human",
        gate_version: str | None = None,
        gate_reason: str = "",
    ) -> dict[str, Any]:
        """Record one verdict. Raises if the episode is not in this workspace."""

        if decision not in {"approved", "rejected"}:
            raise ValueError(f"decision must be approved or rejected, got {decision!r}")
        score = self.scores_by_id().get(episode_id)
        if score is None:
            raise KeyError(episode_id)

        normalised = validate_reasons(reasons)
        if decision == "rejected" and not normalised and not note.strip():
            raise ValueError("a rejection needs at least one reason or a note")

        record = {
            "episode_id": episode_id,
            "source": score.get("source", ""),
            "demo": score.get("demo", ""),
            "task": score.get("task", ""),
            "requested_quality": score.get("requested_quality", ""),
            "human_decision": decision,
            "reasons": normalised,
            "note": note.strip(),
            "reviewer": reviewer,
            "blind": blind,
            "scorer_version": score.get("scorer_version", ""),
            "reviewed_at": _now(),
            "decision_source": decision_source,
            "gate_version": gate_version,
            "gate_reason": gate_reason,
        }
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        with self.labels_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    # --- xoá ------------------------------------------------------------------

    def delete_batch(self, collection_batch_id: str) -> int:
        """Xoá hẳn một đợt thu khỏi workspace. Trả về số tập đã xoá.

        Bảng `collection_batches` chỉ giữ tên và mô tả, không có khoá ngoại
        sang dữ liệu, nên xoá dòng ở đó chỉ làm đợt thu mất tên chứ không mất
        đi: `scores.jsonl` vẫn còn tập của nó và trang Review dựng lại đợt thu
        từ chính chúng, lần này hiện mã thay cho tên. Muốn đợt thu biến mất
        thật thì phải xoá ở đây, nơi dữ liệu thật sự nằm.

        Một file HDF5 chứa nhiều demo và không phải demo nào cũng cùng một đợt
        thu, nên xoá theo từng demo chứ không theo file. File nào hết demo thì
        bỏ luôn — giữ lại một cái vỏ rỗng chỉ làm `rescore` phải mở ra rồi bỏ
        qua mỗi lần chạy.
        """
        import h5py

        doomed: dict[Path, list[str]] = {}
        for record in self.scores():
            if self._collection_batch(record) != collection_batch_id:
                continue
            episode_id = str(record["episode_id"])
            source, _, demo = episode_id.partition("::")
            try:
                path = self.resolve_source(source)
            except FileNotFoundError:
                continue
            doomed.setdefault(path, []).append(demo)

        removed = 0
        for path, demos in doomed.items():
            with h5py.File(path, "a") as handle:
                data = handle["data"]
                for demo in demos:
                    if demo in data:
                        del data[demo]
                        removed += 1
                empty = not any(key.startswith("demo_") for key in data)
            # Đóng file trước khi xoá: Windows khoá file đang mở.
            if empty:
                path.unlink()

        for record in self.scores():
            if self._collection_batch(record) != collection_batch_id:
                continue
            video = self.video_path(str(record["episode_id"]))
            video.unlink(missing_ok=True)

        if removed:
            # `rescore` đọc lại từ các file còn trên đĩa, nên nó tự dọn
            # `scores.jsonl`. Nhãn thì không, phải gỡ tay theo tập đã mất.
            gone = {
                str(record["episode_id"]) for record in self.scores()
                if self._collection_batch(record) == collection_batch_id
            }
            self.rescore()
            alive = {str(record["episode_id"]) for record in self.scores()}
            labels = [
                row for row in self.labels(latest_only=False)
                if str(row.get("episode_id")) in alive
            ]
            _write_jsonl(self.labels_path, labels)
            del gone
        return removed

    # --- overview -----------------------------------------------------------

    @staticmethod
    def _collection_batch(record: Mapping[str, Any]) -> str:
        value = record.get("provenance", {}).get("collection_batch_id")
        return str(value) if value else "legacy"

    def collection_batches(self) -> list[str]:
        """Return stable batch IDs found in the corpus, including legacy data."""

        return sorted({self._collection_batch(record) for record in self.scores()})

    def summary(
        self,
        *,
        collection_batch_id: str | None = None,
        task: str | None = None,
    ) -> dict[str, Any]:
        all_scores = self.scores()
        scores = [
            record for record in all_scores
            if (collection_batch_id is None or self._collection_batch(record) == collection_batch_id)
            and (task is None or str(record.get("task")) == task)
        ]
        scores_by_id = {str(item["episode_id"]): item for item in scores}
        all_labels = self.labels_by_id()
        labels = {
            episode_id: label for episode_id, label in all_labels.items()
            if episode_id in scores_by_id
        }
        approved = sum(1 for item in labels.values() if item["human_decision"] == "approved")
        approved_successes = sum(
            1
            for episode_id, item in labels.items()
            if item["human_decision"] == "approved"
            and scores_by_id.get(episode_id, {}).get("recorded_success") is True
        )
        auto_approved = sum(
            1 for item in labels.values()
            if item.get("human_decision") == "approved" and item.get("decision_source") == "auto_gate"
        )
        auto_rejected = sum(
            1 for item in labels.values()
            if item.get("human_decision") == "rejected" and item.get("decision_source") == "auto_gate"
        )
        from .auto_gate import evaluate

        audited = [
            (record, labels.get(str(record["episode_id"])))
            for record in scores
            if evaluate(record).action == "audit"
        ]
        audit_pending = sum(1 for _, label in audited if label is None)
        audit_reviewed = sum(
            1 for _, label in audited
            if label is not None and label.get("decision_source", "human") != "auto_gate"
        )
        audit_failed = sum(
            1 for _, label in audited
            if label is not None
            and label.get("decision_source", "human") != "auto_gate"
            and label.get("human_decision") == "rejected"
        )
        per_task: dict[str, dict[str, int]] = {}
        per_quality: dict[str, dict[str, int]] = {}
        for record in scores:
            bucket = per_task.setdefault(
                str(record["task"]), {
                    "total": 0,
                    "reviewed": 0,
                    "pending": 0,
                    "approved": 0,
                    "approved_successes": 0,
                    "rejected": 0,
                },
            )
            bucket["total"] += 1
            label = labels.get(str(record["episode_id"]))
            if label is None:
                bucket["pending"] += 1
                continue
            bucket["reviewed"] += 1
            decision = str(label["human_decision"])
            bucket[decision] += 1
            if decision == "approved" and record.get("recorded_success") is True:
                bucket["approved_successes"] += 1
        for record in scores:
            quality_bucket = per_quality.setdefault(
                str(record.get("requested_quality") or "unknown"), {
                    "total": 0, "reviewed": 0, "pending": 0,
                    "approved": 0, "approved_successes": 0, "rejected": 0,
                    "recovery": 0,
                },
            )
            quality_bucket["total"] += 1
            label = labels.get(str(record["episode_id"]))
            if label is None:
                quality_bucket["pending"] += 1
                continue
            quality_bucket["reviewed"] += 1
            decision = str(label["human_decision"])
            quality_bucket[decision] += 1
            if decision == "approved" and record.get("recorded_success") is True:
                quality_bucket["approved_successes"] += 1
                provenance = record.get("provenance", {})
                if bool(provenance.get("recovery_demonstration")) or int(
                    provenance.get("pregrasp_realign_count", 0) or 0
                ) > 0 or int(provenance.get("retry_count", 0) or 0) > 0:
                    quality_bucket["recovery"] += 1
        return {
            "root": str(self.root),
            "datasets": len(self.datasets()),
            "episodes": len(scores),
            "reviewed": len(labels),
            "approved": approved,
            "approved_successes": approved_successes,
            "rejected": len(labels) - approved,
            "pending": len(scores) - len(labels),
            "auto_approved": auto_approved,
            "auto_rejected": auto_rejected,
            "human_reviewed": len(labels) - auto_approved - auto_rejected,
            "audit_pending": audit_pending,
            "audit_reviewed": audit_reviewed,
            "audit_failed": audit_failed,
            "audit_error_rate": (
                round(audit_failed / audit_reviewed, 4) if audit_reviewed else None
            ),
            "per_task": per_task,
            "per_quality": per_quality,
            "collection_batch_id": collection_batch_id,
            "task_filter": task,
            "available_batches": sorted({self._collection_batch(record) for record in all_scores}),
            "scorer_version": scores[0]["scorer_version"] if scores else None,
        }
