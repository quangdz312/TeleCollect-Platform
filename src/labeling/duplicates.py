"""Find episodes that carry no information the corpus does not already have.

Scripted collection is deterministic: the same task at the same environment seed
replays the same episode. Two such records are not two demonstrations, they are
one demonstration counted twice, and a training set that contains both is
silently reweighted toward whatever that episode happens to show.

Nothing here judges quality. A duplicate can be a perfect demonstration; it is
still only worth including once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DuplicateGroup:
    """One configuration, and every episode that reproduces it."""

    task: str
    environment_seed: int
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


def _seed_of(record: Mapping[str, Any]) -> int | None:
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        return None
    seed = provenance.get("environment_seed")
    if seed is None:
        seed = provenance.get("base_seed")
    try:
        return int(seed)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def find_duplicates(records: Sequence[Mapping[str, Any]]) -> list[DuplicateGroup]:
    """Group scored records by the configuration that produced them.

    Only groups with more than one member are returned. Records without a usable
    seed are skipped rather than grouped together: a missing seed means unknown,
    not shared.
    """

    buckets: dict[tuple[str, int], list[str]] = {}
    for record in records:
        seed = _seed_of(record)
        if seed is None:
            continue
        key = (str(record.get("task", "")), seed)
        buckets.setdefault(key, []).append(str(record.get("episode_id", "")))

    return [
        DuplicateGroup(task=task, environment_seed=seed, episode_ids=tuple(ids))
        for (task, seed), ids in buckets.items()
        if len(ids) > 1
    ]


def duplicate_episode_ids(records: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every episode that repeats a configuration already seen earlier."""

    return {
        episode_id
        for group in find_duplicates(records)
        for episode_id in group.repeats
    }
