"""Combine the two layers into one score, and nothing else.

```text
score = (product of evaluable hard checks) x (1 - worst penalty)
```

Multiplying the checks means one failure gives exactly zero and no
good-looking metric can pull it back. Taking the max of the penalties instead of
a sum or an average means nothing dilutes a serious problem and nothing
accumulates ten trivial ones into a fake one — and it keeps the number readable:
``score = 0.7`` means the worst single problem with this episode is worth 0.3,
and ``worst_penalty`` names it.

Scoring is a batch operation because three penalties are defined relative to
other episodes of the same task.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .checks import DEFAULT_CHECKS, CheckConfig, CheckResult, hard_checks
from .duplicates import FINGERPRINT_VERSION, trajectory_fingerprint
from .features import EpisodeArrays
from .penalties import (
    DEFAULT_PENALTIES,
    PenaltyBand,
    PenaltyConfig,
    PenaltyResult,
    RawPenaltyFeatures,
    TaskStats,
    build_task_stats,
    penalties,
    raw_penalty_features,
)
from .trim import DEFAULT_TRIM, TrimConfig, TrimSuggestion, suggest_trim

SCORER_NAME = "telecollect-autolabel"
RELATIVE_COHORT_VERSION = "task+collection_batch_id-v1"


@dataclass(frozen=True)
class ScorerConfig:
    checks: CheckConfig = DEFAULT_CHECKS
    penalties: PenaltyConfig = DEFAULT_PENALTIES
    trim: TrimConfig = DEFAULT_TRIM

    def version(self) -> str:
        """Hash the thresholds so a re-score is traceable to its settings."""

        payload = json.dumps(
            {
                "checks": asdict(self.checks),
                "penalties": {
                    name: [value.low, value.high]
                    if isinstance(value, PenaltyBand)
                    else value
                    for name, value in vars(self.penalties).items()
                },
                "trim": asdict(self.trim),
                "relative_cohort": RELATIVE_COHORT_VERSION,
                "trajectory_fingerprint": FINGERPRINT_VERSION,
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"{SCORER_NAME}-{digest}"


DEFAULT_SCORER = ScorerConfig()
SCORER_VERSION = DEFAULT_SCORER.version()


@dataclass(frozen=True)
class CorpusStats:
    """Per-task reference distributions used by the relative penalties."""

    tasks: Mapping[str, TaskStats]

    @property
    def counts(self) -> dict[str, int]:
        return {task: stats.count for task, stats in self.tasks.items()}


def relative_cohort(episode: EpisodeArrays) -> str:
    """Group relative penalties by task and compatible collection generation.

    Length, jerk and path distributions changed intentionally when Lift added
    settle/recovery phases. Comparing that controller with legacy trajectories
    makes every valid new episode look anomalous. Batch-less files retain one
    shared ``legacy`` cohort for backward compatibility.
    """

    batch = episode.provenance.get("collection_batch_id")
    batch_id = str(batch) if batch else "legacy"
    return f"{episode.task}::{batch_id}"


@dataclass(frozen=True)
class EpisodeScore:
    episode_id: str
    source: str
    demo: str
    task: str
    requested_quality: str
    length: int
    recorded_success: bool
    auto_score: float
    gate_decision: str
    checks: list[CheckResult]
    penalty_results: list[PenaltyResult]
    raw_features: RawPenaltyFeatures
    trim: TrimSuggestion
    scorer_version: str
    trajectory_fingerprint: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failed_checks(self) -> list[str]:
        return [check.name for check in self.checks if check.value == 0]

    @property
    def unavailable_checks(self) -> list[str]:
        return [check.name for check in self.checks if check.value is None]

    @property
    def worst_penalty(self) -> PenaltyResult | None:
        if not self.penalty_results:
            return None
        return max(self.penalty_results, key=lambda item: item.value)

    def auto_flags(self) -> dict[str, Any]:
        """The reasons behind the number. This is what a reviewer reads."""

        worst = self.worst_penalty
        return {
            "checks": {
                check.name: {"value": check.value, **check.detail} for check in self.checks
            },
            "penalties": {
                item.name: {"value": round(item.value, 6), "raw": round(item.raw, 6), **item.detail}
                for item in self.penalty_results
            },
            "failed_checks": self.failed_checks,
            "unavailable_checks": self.unavailable_checks,
            "worst_penalty": None if worst is None else worst.name,
            "worst_penalty_value": 0.0 if worst is None else round(worst.value, 6),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "source": self.source,
            "demo": self.demo,
            "task": self.task,
            "requested_quality": self.requested_quality,
            "length": self.length,
            "recorded_success": self.recorded_success,
            "auto_score": round(self.auto_score, 6),
            "gate_decision": self.gate_decision,
            "auto_flags": self.auto_flags(),
            "suggested_trim_start": self.trim.start,
            "suggested_trim_end": self.trim.end,
            "trim_head_frames": self.trim.trimmed_head,
            "trim_tail_frames": self.trim.trimmed_tail,
            "scorer_version": self.scorer_version,
            "trajectory_fingerprint": self.trajectory_fingerprint,
            "provenance": dict(self.provenance),
        }


def combine(checks: Sequence[CheckResult], penalty_results: Sequence[PenaltyResult]) -> float:
    """Product of the evaluable checks, reduced by the single worst penalty."""

    product = 1.0
    for check in checks:
        if check.value is None:
            continue  # Not evaluable is not the same as failed.
        product *= float(check.value)
    if product == 0.0:
        return 0.0
    worst = max((item.value for item in penalty_results), default=0.0)
    return float(product * (1.0 - worst))


def decide(
    score: float,
    *,
    approve_threshold: float | None = None,
    reject_threshold: float | None = None,
) -> str:
    """Route an episode.

    With no thresholds set, everything routes to a human. That is the correct
    behaviour until shadow mode has produced enough labelled episodes to derive
    them, and it is why the scorer is useful before any gate is switched on.

    The two thresholds are derived independently and can come out in either
    order. When they cross — which is what clean separation between approved and
    rejected episodes produces — a score can satisfy both rules at once. Each
    rule therefore also has to clear the other threshold, so the span between
    them, which contains no observed episode either way, goes to a human rather
    than to whichever rule happened to be tested first.
    """

    rejects = reject_threshold is not None and score <= reject_threshold
    approves = approve_threshold is not None and score >= approve_threshold
    if rejects and not approves:
        return "rejected"
    if approves and not rejects:
        return "approved"
    return "needs_review"


def score_episodes(
    episodes: Sequence[EpisodeArrays],
    *,
    config: ScorerConfig = DEFAULT_SCORER,
    approve_threshold: float | None = None,
    reject_threshold: float | None = None,
) -> tuple[list[EpisodeScore], CorpusStats]:
    """Score a batch. Deterministic: same inputs and version, same output."""

    trims: list[TrimSuggestion] = []
    raws: list[RawPenaltyFeatures] = []
    for episode in episodes:
        trim = suggest_trim(episode, config.trim)
        trims.append(trim)
        raws.append(raw_penalty_features(episode, trim=trim, trim_config=config.trim))

    grouped: dict[str, tuple[str, list[RawPenaltyFeatures]]] = {}
    for episode, raw in zip(episodes, raws):
        cohort = relative_cohort(episode)
        if cohort not in grouped:
            grouped[cohort] = (episode.task, [])
        grouped[cohort][1].append(raw)
    stats = CorpusStats(
        tasks={
            cohort: build_task_stats(task, items)
            for cohort, (task, items) in grouped.items()
        },
    )

    version = config.version()
    scored: list[EpisodeScore] = []
    for episode, raw, trim in zip(episodes, raws, trims):
        checks = hard_checks(episode, config.checks)
        penalty_results = penalties(
            raw, stats.tasks[relative_cohort(episode)], config.penalties,
        )
        value = combine(checks, penalty_results)
        scored.append(
            EpisodeScore(
                episode_id=episode.episode_id,
                source=str(episode.source),
                demo=episode.demo,
                task=episode.task,
                requested_quality=episode.quality,
                length=episode.length,
                recorded_success=episode.recorded_success,
                auto_score=value,
                gate_decision=decide(
                    value,
                    approve_threshold=approve_threshold,
                    reject_threshold=reject_threshold,
                ),
                checks=checks,
                penalty_results=penalty_results,
                raw_features=raw,
                trim=trim,
                scorer_version=version,
                trajectory_fingerprint=trajectory_fingerprint(
                    episode.task,
                    episode.initial_state,
                    episode.actions,
                ),
                provenance=episode.provenance,
            ),
        )
    return scored, stats
