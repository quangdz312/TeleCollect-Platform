"""Conservative automatic verdicts for scripted collection episodes.

The three verdicts describe **how far verification got**, not how good the
episode looked:

``reject``
    Verified broken. The simulator predicate failed, a hard check failed, or the
    record repeats a configuration already in the corpus.
``review``
    Not verifiable. Evidence is missing, or two pieces of evidence disagree.
    A human is needed because the machine has no answer, not because it has a
    low opinion.
``approve``
    Verified. Every check that could run, ran and passed.

Soft penalties -- jerk, path ratio, saturation, idle -- deliberately do **not**
appear here. They measure how the scripted policy was written rather than how
well the episode was performed, no published threshold exists for any of them,
and the curation literature reports that action-only scores of this kind do not
predict downstream policy performance. They stay in ``auto_flags`` for export
filters and for refinement, where they can inform without gating.

What replaces them is sampling: a fraction of every auto-pass goes to a human
anyway, and one verified false approval disables auto-approve for that task.
That catches mistakes nobody thought to write a threshold for.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .duplicates import duplicate_episode_ids

AUTO_GATE_VERSION = "telecollect-auto-gate-v3"
AUDIT_SAMPLE_VERSION = "telecollect-auto-gate-v1"
DEFAULT_AUDIT_RATE = 0.10
TOOLHANG_AUDIT_RATE = 0.20
MAX_AUDIT_ERROR_RATE = 0.05

GateAction = Literal["approve", "reject", "review", "audit"]


@dataclass(frozen=True)
class GateVerdict:
    action: GateAction
    reason: str
    audit_rate: float = 0.0


def _audit_selected(episode_id: str, rate: float) -> bool:
    sample = int.from_bytes(
        hashlib.sha256(f"{AUDIT_SAMPLE_VERSION}:{episode_id}".encode()).digest()[:8],
        "big",
    ) / float(2**64)
    return sample < rate


def evaluate(record: Mapping[str, Any]) -> GateVerdict:
    """Route one score using only evidence safe enough to automate."""

    success = record.get("recorded_success")
    flags = record.get("auto_flags")
    flags = flags if isinstance(flags, Mapping) else {}
    failed = list(flags.get("failed_checks") or [])
    unavailable = list(flags.get("unavailable_checks") or [])
    if success is False:
        return GateVerdict("reject", "Simulator task predicate failed")
    if failed:
        return GateVerdict("reject", f"Hard checks failed: {', '.join(map(str, failed))}")
    if success is not True:
        return GateVerdict("review", "Simulator success is not confirmed")
    if unavailable:
        return GateVerdict("review", f"Checks unavailable: {', '.join(map(str, unavailable))}")

    provenance = record.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    variation = provenance.get("sampled_variation")
    variation = variation if isinstance(variation, Mapping) else {}
    fault_type = str(variation.get("fault_type", "none") or "none")
    if fault_type != "none":
        return GateVerdict("review", f"Controlled semantic fault requires review: {fault_type}")
    if str(provenance.get("failure_stage", "") or ""):
        return GateVerdict("review", "Failure-stage provenance conflicts with success")

    task = str(record.get("task", ""))
    audit_rate = TOOLHANG_AUDIT_RATE if task == "tool_hang" else DEFAULT_AUDIT_RATE
    if task == "tool_hang":
        stage1_ok = variation.get("stage1_env_predicate") is True
        stage2_ok = variation.get("stage2_tool_on_frame") is True
        if not (stage1_ok and stage2_ok and provenance.get("terminal_phase") == "done"):
            return GateVerdict("review", "ToolHang stage evidence is incomplete")
        # Stage 1 retries by rotating the frame a quarter turn and trying again,
        # so a late attempt reaches the same predicate by a different route than
        # a first-attempt success. Whether that matters for training is unknown,
        # which is the definition of a review.
        try:
            attempts = int(provenance.get("retry_count", 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts > 0:
            return GateVerdict("review", f"ToolHang succeeded after {attempts} retries")
    if _audit_selected(str(record.get("episode_id", "")), audit_rate):
        return GateVerdict("audit", "Auto-pass sampled for human audit", audit_rate)
    return GateVerdict("approve", "Success and all strict auto-pass checks passed", audit_rate)


def apply(
    space: Any,
    *,
    task: str | None = None,
    collection_batch_id: str | None = None,
) -> dict[str, Any]:
    """Persist automatic labels for currently-unlabelled episodes only."""

    labels = space.labels_by_id()
    scores = space.scores()
    # Only an exact initial-state + action fingerprint proves a repeat. A shared
    # seed is insufficient because quality profiles and sampled perturbations
    # can produce different trajectories from the same environment seed.
    repeats = duplicate_episode_ids(scores)
    audit_by_task: dict[str, list[Mapping[str, Any]]] = {}
    for record in scores:
        if evaluate(record).action != "audit":
            continue
        label = labels.get(str(record["episode_id"]))
        if label is None or label.get("decision_source", "human") == "auto_gate":
            continue
        audit_by_task.setdefault(str(record.get("task", "")), []).append(label)
    disabled_tasks = []
    audit_error_rates: dict[str, float] = {}
    for audit_task, task_labels in audit_by_task.items():
        failures = sum(label.get("human_decision") == "rejected" for label in task_labels)
        rate = failures / len(task_labels)
        audit_error_rates[audit_task] = rate
        # One verified false approval is enough to stop that task. Gathering
        # more data must not expose more bad demonstrations while we wait for
        # a statistically convenient sample size.
        if failures > 0 and rate > MAX_AUDIT_ERROR_RATE:
            disabled_tasks.append(audit_task)
    scoped_disabled = (
        disabled_tasks
        if task is None
        else [disabled for disabled in disabled_tasks if disabled == task]
    )
    counts: dict[str, Any] = {
        "approved": 0, "rejected": 0, "audit": 0, "review": 0, "skipped": 0,
        "duplicates": len(repeats),
        "auto_approve_enabled": not scoped_disabled,
        "disabled_tasks": scoped_disabled,
        "audit_error_rates": audit_error_rates,
    }
    for record in scores:
        if task is not None and str(record.get("task", "")) != task:
            continue
        record_provenance = record.get("provenance")
        record_provenance = (
            record_provenance if isinstance(record_provenance, Mapping) else {}
        )
        if (
            collection_batch_id is not None
            and str(record_provenance.get("collection_batch_id", ""))
            != collection_batch_id
        ):
            continue
        episode_id = str(record["episode_id"])
        if episode_id in labels:
            counts["skipped"] += 1
            continue
        verdict = (
            GateVerdict("reject", "Repeats a configuration already in the corpus")
            if episode_id in repeats
            else evaluate(record)
        )
        if verdict.action == "approve" and str(record.get("task", "")) in disabled_tasks:
            counts["review"] = int(counts["review"] or 0) + 1
            continue
        if verdict.action in {"review", "audit"}:
            counts[verdict.action] = int(counts[verdict.action] or 0) + 1
            continue
        decision = "approved" if verdict.action == "approve" else "rejected"
        space.append_label(
            episode_id,
            decision=decision,
            note=verdict.reason,
            reviewer="auto-gate",
            blind=False,
            decision_source="auto_gate",
            gate_version=AUTO_GATE_VERSION,
            gate_reason=verdict.reason,
        )
        labels[episode_id] = {"human_decision": decision}
        counts[decision] = int(counts[decision] or 0) + 1
    return counts


def dry_run(
    records: list[Mapping[str, Any]],
    *,
    task: str | None = None,
    collection_batch_id: str | None = None,
) -> dict[str, Any]:
    """Preview gate actions without writing labels or changing workspace state."""

    repeats = duplicate_episode_ids(records)
    selected = []
    counts = {"approve": 0, "reject": 0, "review": 0, "audit": 0}
    for record in records:
        if task is not None and str(record.get("task", "")) != task:
            continue
        provenance = record.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        if (
            collection_batch_id is not None
            and str(provenance.get("collection_batch_id", "")) != collection_batch_id
        ):
            continue
        episode_id = str(record.get("episode_id", ""))
        verdict = (
            GateVerdict("reject", "Repeats an exact trajectory already in the corpus")
            if episode_id in repeats
            else evaluate(record)
        )
        counts[verdict.action] += 1
        selected.append({
            "episode_id": episode_id,
            "action": verdict.action,
            "reason": verdict.reason,
        })
    return {"counts": counts, "total": len(selected), "episodes": selected}
