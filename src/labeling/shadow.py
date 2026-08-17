"""Shadow mode: compare the score against human decisions, and only then talk
about thresholds.

Nothing here decides anything. It answers four questions:

1. Is the score separating what humans approve from what they reject at all?
2. Where would the two thresholds sit if they were derived from this data?
3. How many episodes sit in the auto-approve zone, and what wrong-approval rate
   does that support?
4. Was the human decision ever different from what the success flag alone would
   have said? That is the review yield, and it decides whether automating any of
   this is worth the effort.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ShadowConfig:
    #: Pulled away from the observed boundary so a threshold is not set exactly
    #: on the one episode that happened to define it.
    threshold_margin: float = 0.02
    #: Minimum separation before the document allows any gate to be switched on.
    min_auc: float = 0.75
    #: Episodes needed inside the approve zone for a 1% wrong-approval bound.
    target_approve_zone: int = 300
    #: Decisions needed before the review yield says anything about the corpus.
    min_reviews_for_yield: int = 20


DEFAULT_SHADOW = ShadowConfig()


@dataclass(frozen=True)
class ShadowReport:
    matched: int
    approved: int
    rejected: int
    auc: float | None
    tau_reject: float | None
    tau_approve: float | None
    approve_zone_count: int
    wrong_approval_bound: float | None
    false_rejections: list[str]
    review_yield: float | None
    review_yield_is_meaningful: bool
    thresholds_crossed: bool
    gate_ready: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "approved": self.approved,
            "rejected": self.rejected,
            "auc": self.auc,
            "tau_reject": self.tau_reject,
            "tau_approve": self.tau_approve,
            "approve_zone_count": self.approve_zone_count,
            "wrong_approval_bound": self.wrong_approval_bound,
            "false_rejections": self.false_rejections,
            "review_yield": self.review_yield,
            "review_yield_is_meaningful": self.review_yield_is_meaningful,
            "thresholds_crossed": self.thresholds_crossed,
            "gate_ready": self.gate_ready,
            "reasons": self.reasons,
        }


def auc(approved: Sequence[float], rejected: Sequence[float]) -> float | None:
    """Probability a random approved episode outscores a random rejected one.

    Mann-Whitney U with ties counted as half, which is the same number a ROC AUC
    would give without needing a curve.
    """

    if not approved or not rejected:
        return None
    positives = np.asarray(approved, dtype=np.float64)
    negatives = np.asarray(rejected, dtype=np.float64)
    wins = float(np.sum(positives[:, None] > negatives[None, :]))
    ties = float(np.sum(positives[:, None] == negatives[None, :]))
    return (wins + 0.5 * ties) / (positives.size * negatives.size)


def rule_of_three(count: int) -> float | None:
    """Upper bound on the true failure rate after observing zero failures."""

    return None if count <= 0 else 3.0 / count


def derive_thresholds(
    approved: Sequence[float],
    rejected: Sequence[float],
    config: ShadowConfig = DEFAULT_SHADOW,
) -> tuple[float | None, float | None]:
    """tau_reject sits below every approved episode; tau_approve above every rejected one.

    The two are derived independently and may come out in either order.

    When the populations *overlap*, ``tau_reject < tau_approve`` and the span
    between them is the human review queue. When they separate cleanly the pair
    crosses, and the span between them contains no observed episode at all —
    it is extrapolation, not agreement. :func:`src.labeling.score.decide` sends
    that span to a human either way, which is why a crossed pair is reported
    rather than rejected.
    """

    tau_reject = float(min(approved)) - config.threshold_margin if approved else None
    tau_approve = float(max(rejected)) + config.threshold_margin if rejected else None
    return tau_reject, tau_approve


def review_yield(
    scores: Mapping[str, Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
) -> float | None:
    """How often the human disagreed with the success flag alone.

    This is the number that decides whether building the rest is worth it. Under
    10% means most reviews change nothing; above 30% means the checks are still
    too weak to be discussing automation.
    """

    considered = 0
    differed = 0
    for label in labels:
        record = scores.get(label["episode_id"])
        if record is None:
            continue
        considered += 1
        automatic = "approved" if record.get("recorded_success") else "rejected"
        if automatic != label["human_decision"]:
            differed += 1
        elif label.get("note"):
            differed += 1
    return differed / considered if considered else None


def build_report(
    scores: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    config: ShadowConfig = DEFAULT_SHADOW,
) -> ShadowReport:
    by_id = {record["episode_id"]: record for record in scores}
    pairs = [
        (by_id[label["episode_id"]], label)
        for label in labels
        if label["episode_id"] in by_id
    ]
    approved = [record["auto_score"] for record, label in pairs if label["human_decision"] == "approved"]
    rejected = [record["auto_score"] for record, label in pairs if label["human_decision"] == "rejected"]

    separation = auc(approved, rejected)
    tau_reject, tau_approve = derive_thresholds(approved, rejected, config)
    thresholds_crossed = (
        tau_reject is not None and tau_approve is not None and tau_reject >= tau_approve
    )

    approve_zone = 0
    if tau_approve is not None:
        approve_zone = sum(1 for score in approved if score >= tau_approve)
    false_rejections = [
        record["episode_id"]
        for record, label in pairs
        if tau_reject is not None
        and label["human_decision"] == "approved"
        and record["auto_score"] <= tau_reject
    ]

    reasons: list[str] = []
    if not approved or not rejected:
        reasons.append("need both approved and rejected examples before thresholds mean anything")
    if separation is not None and separation < config.min_auc:
        reasons.append(
            f"AUC {separation:.2f} is below {config.min_auc}; fix the checks, not the thresholds",
        )
    if false_rejections:
        reasons.append(f"{len(false_rejections)} approved episodes fall below tau_reject")
    if approve_zone < config.target_approve_zone:
        reasons.append(
            f"only {approve_zone} episodes in the approve zone; "
            f"{config.target_approve_zone} supports a 1% wrong-approval bound",
        )

    return ShadowReport(
        matched=len(pairs),
        approved=len(approved),
        rejected=len(rejected),
        auc=separation,
        tau_reject=tau_reject,
        tau_approve=tau_approve,
        approve_zone_count=approve_zone,
        wrong_approval_bound=rule_of_three(approve_zone),
        false_rejections=false_rejections,
        review_yield=review_yield(by_id, labels),
        review_yield_is_meaningful=len(pairs) >= config.min_reviews_for_yield,
        thresholds_crossed=thresholds_crossed,
        gate_ready=not reasons,
        reasons=reasons,
    )
