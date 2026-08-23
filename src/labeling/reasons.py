"""The vocabulary a human reviewer ticks when rejecting an episode.

A bare approve/reject is enough to derive the two thresholds, and nothing here
changes that — the decision is still the only field :mod:`src.labeling.shadow`
reads. What the reasons buy is the ability to answer *why* a threshold came out
strange, which a free-text note cannot be aggregated into.

Every reason names the check or penalty that is supposed to catch it. That
mapping is the point: when a human ticks ``bad_grasp`` on an episode where
``E_skill`` returned 1, the check is too lenient, and the fix belongs in the
check rather than in the threshold. :func:`disagreements` finds those cases.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast


@dataclass(frozen=True)
class Reason:
    """One tickable reason, and the automatic signal that should have caught it."""

    code: str
    label_vi: str
    label_en: str
    #: Hard check expected to return 0 for this reason, if any.
    check: str | None = None
    #: Penalty expected to be high for this reason, if any.
    penalties: tuple[str, ...] = ()


#: Ordered as a reviewer reads them: did it work, was it done properly, was the
#: recording clean. Codes are stable — they are written into ``labels.jsonl``.
REJECTION_REASONS: tuple[Reason, ...] = (
    Reason(
        "task_not_completed",
        "Không hoàn thành task",
        "Task not completed",
        check="E_success",
    ),
    Reason(
        "bad_grasp",
        "Cầm nắm sai / trượt",
        "Bad or slipping grasp",
        check="E_skill",
    ),
    Reason(
        "dropped_object",
        "Làm rơi vật",
        "Dropped the object",
        check="E_no_drop",
    ),
    Reason(
        "jerky_path",
        "Đường đi giật cục, lòng vòng",
        "Jerky or wandering path",
        penalties=("jerkiness", "wandering_path", "gripper_toggles"),
    ),
    Reason(
        "idle_padding",
        "Thừa đoạn đứng yên đầu/cuối",
        "Idle padding at the ends",
        penalties=("idle_after_trim", "unusual_length"),
    ),
    Reason(
        "data_corrupt",
        "Dữ liệu hỏng / thiếu",
        "Corrupt or incomplete data",
        check="E_integrity",
    ),
    Reason(
        "other",
        "Lý do khác (ghi chú)",
        "Other (see note)",
    ),
)

REASONS_BY_CODE: Mapping[str, Reason] = MappingProxyType(
    {reason.code: reason for reason in REJECTION_REASONS},
)


def validate(codes: Iterable[str]) -> list[str]:
    """Keep the given codes in the canonical order, rejecting unknown ones.

    Order is normalised so two reviewers who tick the same boxes produce the
    same record, which keeps the log diffable and the counts honest.
    """

    requested = set(codes)
    unknown = sorted(requested - set(REASONS_BY_CODE))
    if unknown:
        raise ValueError(f"unknown rejection reasons: {', '.join(unknown)}")
    return [reason.code for reason in REJECTION_REASONS if reason.code in requested]


def as_dicts() -> list[dict[str, object]]:
    """Serialise the vocabulary for the web UI, which renders the checkboxes."""

    return [
        {
            "code": reason.code,
            "label_vi": reason.label_vi,
            "label_en": reason.label_en,
            "check": reason.check,
            "penalties": list(reason.penalties),
        }
        for reason in REJECTION_REASONS
    ]


def disagreements(
    score: Mapping[str, object],
    reasons: Sequence[str],
) -> list[dict[str, str]]:
    """Reasons the human ticked that the automatic signal did not flag.

    Each entry is a concrete lead on a check that is too lenient. Penalties are
    deliberately not asserted on here: a penalty is a tuned estimate, and a
    reviewer disagreeing with one is a calibration matter, not a defect.
    """

    # `score` is typed as Mapping[str, object] (an auto-label score dict of
    # mixed value types), but "auto_flags" specifically is always a dict at
    # runtime — cast documents that known shape instead of widening the
    # whole Mapping's value type or silencing the checker.
    flags = cast("dict[str, Any]", score.get("auto_flags") or {})
    failed = set(flags.get("failed_checks", []))
    unavailable = set(flags.get("unavailable_checks", []))

    missed: list[dict[str, str]] = []
    for code in reasons:
        reason = REASONS_BY_CODE.get(code)
        if reason is None or reason.check is None:
            continue
        if reason.check in failed:
            continue
        missed.append(
            {
                "episode_id": str(score.get("episode_id", "")),
                "reason": code,
                "check": reason.check,
                "status": "unavailable" if reason.check in unavailable else "passed",
            },
        )
    return missed
