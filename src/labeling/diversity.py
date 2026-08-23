"""Read-only diversity diagnostics for the scripted review corpus."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import h5py
import numpy as np

from src.sim.perturbations.profiles import PerturbationTask
from src.sim.perturbations.runtime import phase_noise_policies

QUALITY_ORDER = ("clean", "good", "medium", "poor")


def _selected(record: Mapping[str, Any], labels: Mapping[str, Mapping[str, Any]], scope: str) -> bool:
    label = labels.get(str(record.get("episode_id", "")))
    if scope == "approved":
        return bool(label and label.get("human_decision") == "approved")
    if scope == "reviewed":
        return label is not None
    return True


def _pose_slices(task: str, width: int) -> list[tuple[str, slice]]:
    if task == "tool_hang":
        if width >= 42:
            return [("frame", slice(21, 24)), ("tool", slice(35, 38))]
        return [("frame", slice(0, 3)), ("tool", slice(7, 10))]
    if task == "lift":
        return [("object", slice(0, 3))]
    return [("object", slice(7, 10))]


def load_initial_positions(space: Any, records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, list[float]]]:
    """Load initial object poses, opening every source HDF5 only once."""

    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        by_source[str(record.get("source", ""))].append(record)
    positions: dict[str, dict[str, list[float]]] = {}
    for source, source_records in by_source.items():
        try:
            path: Path = space.resolve_source(source)
            with h5py.File(path, "r") as handle:
                for record in source_records:
                    demo = str(record.get("demo", ""))
                    dataset = handle.get(f"data/{demo}/obs/object")
                    if dataset is None or len(dataset) == 0:
                        continue
                    vector = np.asarray(dataset[0], dtype=float).reshape(-1)
                    values: dict[str, list[float]] = {}
                    for key, indices in _pose_slices(str(record.get("task", "")), vector.size):
                        xyz = vector[indices]
                        if xyz.size == 3 and np.all(np.isfinite(xyz)):
                            values[key] = xyz.tolist()
                    if values:
                        positions[str(record["episode_id"])] = values
        except (FileNotFoundError, OSError, KeyError, ValueError):
            continue
    return positions


def _histogram(lengths: list[int]) -> dict[str, list[float] | list[int]]:
    if not lengths:
        return {"edges": [], "counts": []}
    bins = min(10, max(1, int(np.sqrt(len(lengths)))))
    counts, edges = np.histogram(lengths, bins=bins)
    return {"edges": edges.round(2).tolist(), "counts": counts.astype(int).tolist()}


def _coverage(selected_ids: set[str], positions: Mapping[str, Mapping[str, list[float]]]) -> dict[str, Any]:
    all_xy = np.asarray([next(iter(value.values()))[:2] for value in positions.values()], dtype=float)
    chosen_xy = np.asarray(
        [next(iter(value.values()))[:2] for key, value in positions.items() if key in selected_ids],
        dtype=float,
    )
    if len(all_xy) == 0 or len(chosen_xy) == 0:
        return {"overall": None, "x": None, "y": None, "reference_episodes": len(all_xy)}
    reference_span = np.ptp(all_xy, axis=0)
    selected_span = np.ptp(chosen_xy, axis=0)
    ratios = np.where(reference_span > 1e-9, selected_span / reference_span, 1.0)
    ratios = np.clip(ratios, 0.0, 1.0) * 100.0
    return {
        "overall": round(float(np.sqrt(ratios[0] * ratios[1])), 1),
        "x": round(float(ratios[0]), 1),
        "y": round(float(ratios[1]), 1),
        "reference_episodes": len(all_xy),
    }


def build_report(
    records: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, list[float]]],
    *,
    task: str,
    scope: str,
) -> dict[str, Any]:
    task_records = [record for record in records if record.get("task") == task]
    selected = [record for record in task_records if _selected(record, labels, scope)]
    selected_ids = {str(record["episode_id"]) for record in selected}
    quality_rows = []
    for quality in QUALITY_ORDER:
        subset = [record for record in selected if record.get("requested_quality") == quality]
        decisions = Counter(
            str(labels.get(str(record["episode_id"]), {}).get("human_decision", "pending"))
            for record in subset
        )
        quality_rows.append({
            "quality": quality,
            "total": len(subset),
            "success": sum(bool(record.get("recorded_success")) for record in subset),
            "failure": sum(not bool(record.get("recorded_success")) for record in subset),
            "approved": decisions["approved"],
            "rejected": decisions["rejected"],
            "pending": decisions["pending"],
        })
    coverage = _coverage(selected_ids, positions)
    # quality_rows entries are dict[str, object] (mixed str/int/bool values in
    # the literal above collapse to that common type); "total" is always the
    # int from len(subset) a few lines up.
    represented = sum(cast(int, row["total"]) > 0 for row in quality_rows)
    if not selected:
        status = {"code": "no_data", "label": "No data", "detail": "No episodes match this scope."}
    elif len(selected) < 20:
        status = {"code": "needs_data", "label": "Needs data", "detail": "Fewer than 20 episodes are available."}
    elif represented < 2:
        status = {"code": "imbalanced", "label": "Imbalanced", "detail": "Only one requested quality is represented."}
    elif coverage["overall"] is not None and coverage["overall"] < 50:
        status = {"code": "low_coverage", "label": "Low coverage", "detail": "Reviewed data covers less than half of the collected XY range."}
    else:
        status = {"code": "healthy", "label": "Healthy", "detail": "Episode count, quality mix and reviewed XY coverage pass MVP checks."}
    point_sets: list[dict[str, Any]] = []
    keys = sorted({key for episode in positions.values() for key in episode})
    for key in keys:
        points = []
        for record in selected:
            episode_id = str(record["episode_id"])
            xyz = positions.get(episode_id, {}).get(key)
            if xyz:
                points.append({
                    "episode_id": episode_id,
                    "quality": record.get("requested_quality", "unknown"),
                    "decision": labels.get(episode_id, {}).get("human_decision", "pending"),
                    "success": bool(record.get("recorded_success")),
                    "x": xyz[0], "y": xyz[1], "z": xyz[2],
                })
        if points:
            point_sets.append({"key": key, "label": key.replace("_", " ").title(), "points": points})
    failures = Counter(
        str(record.get("provenance", {}).get("failure_stage") or record.get("provenance", {}).get("terminal_phase") or "unknown")
        for record in selected if not record.get("recorded_success")
    )
    policies = phase_noise_policies(PerturbationTask(task))
    phases = [{"phase": name, "action_scale": policy.action_scale, "failures": failures[name]} for name, policy in policies.items()]
    unmatched_failures = sum(count for name, count in failures.items() if name not in policies)
    if unmatched_failures:
        phases.append({"phase": "other / unassigned", "action_scale": 0.0, "failures": unmatched_failures})
    success_count = sum(bool(record.get("recorded_success")) for record in selected)
    return {
        "task": task, "scope": scope, "episodes": len(selected),
        "success_rate": round(100 * success_count / len(selected), 1) if selected else None,
        "coverage": coverage, "status": status, "quality": quality_rows,
        "position_sets": point_sets,
        "length_histogram": _histogram([int(record.get("length", 0)) for record in selected]),
        "phases": phases,
    }


def diversity_report(space: Any, *, task: str, scope: str) -> dict[str, Any]:
    records = space.scores()
    task_records = [record for record in records if record.get("task") == task]
    return build_report(
        records, space.labels_by_id(), load_initial_positions(space, task_records), task=task, scope=scope,
    )
