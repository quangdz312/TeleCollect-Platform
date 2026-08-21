"""Explainable shadow recommendations for the review queue.

These recommendations never mutate the persisted human-review status.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AutoLabel = Literal["accept", "review", "reject"]


@dataclass(frozen=True)
class AutoLabelResult:
    label: AutoLabel
    reason: str


#: An auto-pass that the gate sampled for human audit is still an auto-pass. The
#: distinction matters to the audit bookkeeping, not to a reviewer reading the
#: queue, so both collapse to "accept" here.
_GATE_TO_LABEL: dict[str, AutoLabel] = {
    "approve": "accept",
    "audit": "accept",
    "review": "review",
    "reject": "reject",
}


def classify_scripted(
    gate_decision: str | None,
    recorded_success: bool | None,
    auto_flags: dict | None = None,
    task: str | None = None,
    provenance: dict | None = None,
) -> AutoLabelResult:
    """Advisory label for the review queue, delegating to the real gate.

    This used to carry a second, older rule set that could disagree with
    ``auto_gate`` -- the queue showed one verdict while the gate would have
    written another. There is one set of rules now; this wrapper only adapts the
    record shape and folds ``audit`` back into ``accept``.

    ``gate_decision`` is kept for callers that already resolved a verdict
    upstream, and still short-circuits an explicit rejection.
    """

    from src.labeling.auto_gate import evaluate

    if gate_decision in {"rejected", "auto_reject"}:
        return AutoLabelResult("reject", "Hard failure or failed task predicate")

    verdict = evaluate({
        "recorded_success": recorded_success,
        "auto_flags": auto_flags or {},
        "task": task or "",
        "provenance": provenance or {},
        "episode_id": "",
    })
    return AutoLabelResult(_GATE_TO_LABEL[verdict.action], verdict.reason)


def classify_teleop_metadata(metadata: dict) -> AutoLabelResult:
    if metadata.get("partial") or metadata.get("interrupted"):
        return AutoLabelResult("reject", "Recording is partial or interrupted")
    if metadata.get("task_success") is False:
        return AutoLabelResult("reject", "Task was marked unsuccessful")
    if metadata.get("task_success") is True:
        return AutoLabelResult("review", "Success recorded; independent verification is unavailable")
    return AutoLabelResult("review", "No complete task verdict is available")


def _teleop_quality_gate(metadata: dict) -> AutoLabelResult | None:
    """Return a conservative result for recording-quality failures."""

    privileged = metadata.get("privileged_state")
    if not isinstance(privileged, dict) or not privileged.get("recorded", False):
        return AutoLabelResult("review", "Privileged state is missing")
    if int(metadata.get("dropped_stream_frames", 0) or 0) > 0:
        return AutoLabelResult("review", "Recording contains dropped stream frames")
    if int(metadata.get("overruns", 0) or 0) > 0:
        return AutoLabelResult("review", "Control loop contains timing overruns")
    return None


def _teleop_rule_evaluation(episode_dir: Path, metadata: dict):
    """Evaluate a Teleop trace with the same task rules used by Scripted."""

    import numpy as np
    import pyarrow.parquet as pq

    from src.labeling.rule_engine import episode_from_env_states, evaluate_rules
    from src.sim.can_env import make_can_environment
    from src.sim.lift_env import make_lift_environment
    from src.sim.square_env import make_square_environment

    actions_path = episode_dir / "actions.parquet"
    if not actions_path.exists():
        return None
    table = pq.read_table(actions_path, columns=["privileged_state"])
    states = [row for row in table["privileged_state"].to_pylist() if row is not None]
    if not states:
        return None

    task = str(metadata.get("task_name", ""))
    factories = {
        "lift_cube": make_lift_environment,
        "pick_place_can": make_can_environment,
        "nut_assembly_square": make_square_environment,
    }
    factory = factories.get(task)
    if factory is None:
        return None

    env = factory(render=False, seed=metadata.get("seed"))
    try:
        env.reset()
        episode = episode_from_env_states(
            env=env,
            states=np.asarray(states, dtype=np.float64),
            task=task,
            episode_id=str(metadata.get("episode_id", episode_dir.name)),
            source="manual_teleop",
            control_hz=float(metadata.get("control_hz", 30)),
            recorded_success=metadata.get("task_success"),
        )
        return evaluate_rules(episode)
    finally:
        try:
            env.close()
        except Exception:
            pass


def classify_teleop_episode(episode_dir: Path) -> AutoLabelResult:
    meta_path = episode_dir / "meta.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return AutoLabelResult("review", "Recording metadata is missing or unreadable")
    if not isinstance(metadata, dict):
        return AutoLabelResult("review", "Recording metadata has an invalid format")
    basic = classify_teleop_metadata(metadata)
    if basic.label != "review" or metadata.get("task_success") is not True:
        return basic

    quality = _teleop_quality_gate(metadata)
    if quality is not None:
        return quality

    try:
        evaluation = _teleop_rule_evaluation(episode_dir, metadata)
    except Exception:
        return AutoLabelResult("review", "Teleop state could not be evaluated by task rules")
    if evaluation is None:
        return AutoLabelResult("review", "Teleop state trace is incomplete")
    if evaluation.final_recommendation == "auto_reject":
        return AutoLabelResult("reject", "Shared task rule detected a hard failure")
    if evaluation.final_recommendation == "needs_review":
        return AutoLabelResult("review", "Shared task rules require human review")
    return AutoLabelResult("accept", "Task rules and recording quality checks passed")
