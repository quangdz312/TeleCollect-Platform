"""Conservative automatic verdicts for scripted collection episodes."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

AUTO_GATE_VERSION = "telecollect-auto-gate-v2"
AUDIT_SAMPLE_VERSION = "telecollect-auto-gate-v1"
MAX_AUTO_APPROVE_PENALTY = 0.15
IDLE_AFTER_TRIM_AUTO_APPROVE_LIMIT = 0.22
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

    quality = str(record.get("requested_quality", ""))
    if quality not in {"clean", "good"}:
        return GateVerdict("review", f"{quality or 'unknown'} quality stays in human review")
    penalty_name = str(flags.get("worst_penalty") or "unknown")
    penalty = float(flags.get("worst_penalty_value", 1.0) or 0.0)
    penalty_limit = (
        IDLE_AFTER_TRIM_AUTO_APPROVE_LIMIT
        if penalty_name == "idle_after_trim"
        else MAX_AUTO_APPROVE_PENALTY
    )
    if penalty > penalty_limit:
        return GateVerdict(
            "review",
            f"{penalty_name} penalty {penalty:.3f} exceeds {penalty_limit:.2f}",
        )

    task = str(record.get("task", ""))
    audit_rate = TOOLHANG_AUDIT_RATE if task == "tool_hang" else DEFAULT_AUDIT_RATE
    if task == "tool_hang":
        stage1_ok = variation.get("stage1_env_predicate") is True
        stage2_ok = variation.get("stage2_tool_on_frame") is True
        if not (stage1_ok and stage2_ok and provenance.get("terminal_phase") == "done"):
            return GateVerdict("review", "ToolHang stage evidence is incomplete")
    if _audit_selected(str(record.get("episode_id", "")), audit_rate):
        return GateVerdict("audit", "Auto-pass sampled for human audit", audit_rate)
    return GateVerdict("approve", "Success and all strict auto-pass checks passed", audit_rate)


def apply(space: Any) -> dict[str, Any]:
    """Persist automatic labels for currently-unlabelled episodes only."""

    labels = space.labels_by_id()
    scores = space.scores()
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
    for task, task_labels in audit_by_task.items():
        failures = sum(label.get("human_decision") == "rejected" for label in task_labels)
        rate = failures / len(task_labels)
        audit_error_rates[task] = rate
        # One verified false approval is enough to stop that task. Gathering
        # more data must not expose more bad demonstrations while we wait for
        # a statistically convenient sample size.
        if failures > 0 and rate > MAX_AUDIT_ERROR_RATE:
            disabled_tasks.append(task)
    counts: dict[str, Any] = {
        "approved": 0, "rejected": 0, "audit": 0, "review": 0, "skipped": 0,
        "auto_approve_enabled": not disabled_tasks,
        "disabled_tasks": disabled_tasks,
        "audit_error_rates": audit_error_rates,
    }
    for record in scores:
        episode_id = str(record["episode_id"])
        if episode_id in labels:
            counts["skipped"] += 1
            continue
        verdict = evaluate(record)
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
