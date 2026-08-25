"""Find episodes that carry no information the corpus does not already have.

A seed is collection metadata, not proof that two demonstrations are equal.
Quality profiles, sampled perturbations, delays, and controller changes can all
produce different trajectories from the same environment seed.  Duplicate
decisions therefore use a content fingerprint of the initial simulator state
and recorded actions.  Missing fingerprints mean "unknown", never "duplicate".

Nothing here judges quality. A duplicate can be a perfect demonstration; it is
still only worth including once.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

FINGERPRINT_VERSION = "telecollect-trajectory-v1"


def trajectory_fingerprint(
    task: str,
    initial_state: np.ndarray,
    actions: np.ndarray,
) -> str | None:
    """Return a stable exact-content fingerprint, or ``None`` without evidence.

    Arrays are normalised to little-endian float64 so the digest does not depend
    on the machine's native byte order or the source HDF5 dtype.  We deliberately
    do not quantise: near-duplicates are still distinct training examples.
    """

    state = np.asarray(initial_state)
    commands = np.asarray(actions)
    if state.size == 0 or commands.size == 0:
        return None
    if not np.isfinite(state).all() or not np.isfinite(commands).all():
        return None

    digest = hashlib.sha256()
    digest.update(FINGERPRINT_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(task).strip().lower().encode("utf-8"))
    for name, value in ((b"initial_state", state), (b"actions", commands)):
        array = np.ascontiguousarray(value, dtype="<f8")
        digest.update(b"\0" + name + b"\0")
        digest.update(np.asarray(array.shape, dtype="<i8").tobytes())
        digest.update(array.tobytes(order="C"))
    return f"{FINGERPRINT_VERSION}:{digest.hexdigest()}"


@dataclass(frozen=True)
class DuplicateGroup:
    """One configuration, and every episode that reproduces it."""

    task: str
    fingerprint: str
    #: Episode ids in corpus order. The first is the one worth keeping; the rest
    #: repeat it. Which one is "first" is stable because the caller passes
    #: records in a stable order, not because any of them is better.
    episode_ids: tuple[str, ...]

    @property
    def keep(self) -> str:
        return self.episode_ids[0]

    @property
    def repeats(self) -> tuple[str, ...]:
        return self.episode_ids[1:]


def _fingerprint_of(record: Mapping[str, Any]) -> str | None:
    value = record.get("trajectory_fingerprint")
    if not isinstance(value, str) or not value.startswith(f"{FINGERPRINT_VERSION}:"):
        return None
    return value


def find_duplicates(records: Sequence[Mapping[str, Any]]) -> list[DuplicateGroup]:
    """Group scored records by the configuration that produced them.

    Only groups with more than one member are returned. Records without a usable
    fingerprint are skipped: missing evidence means unknown, not shared.
    """

    buckets: dict[tuple[str, str], list[str]] = {}
    for record in records:
        fingerprint = _fingerprint_of(record)
        if fingerprint is None:
            continue
        key = (str(record.get("task", "")), fingerprint)
        buckets.setdefault(key, []).append(str(record.get("episode_id", "")))

    return [
        DuplicateGroup(task=task, fingerprint=fingerprint, episode_ids=tuple(ids))
        for (task, fingerprint), ids in buckets.items()
        if len(ids) > 1
    ]


def duplicate_episode_ids(records: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every episode that repeats a configuration already seen earlier."""

    return {
        episode_id
        for group in find_duplicates(records)
        for episode_id in group.repeats
    }
