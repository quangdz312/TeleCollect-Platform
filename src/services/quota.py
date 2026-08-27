"""Refuse large ingests before the disk fills.

The failure this guards against is not "we ran out of room for episodes". It is
a full VPS volume, where Postgres cannot write, Docker cannot pull, and the site
goes down — losing whatever was being uploaded is the least of it. So the quota
sits below the real disk and turns a silent, total failure into a clear refusal
of one upload.

Two decisions worth stating.

**Measured, not tracked.** The total is computed by walking the data directories
rather than kept in a counter. A counter drifts the moment anything is written
or deleted outside the API — a dropped file, a manual cleanup, a failed import
leaving a partial — and a drifted counter either blocks an empty disk or waves
through a full one. Walking is slower but cannot be wrong.

**Cached briefly.** Walking tens of thousands of files on every request would be
its own problem, so the result is held for a few seconds. That is safe because
the quota is a floor with headroom under the real disk, not an exact accountant:
a few concurrent uploads slipping past the line together cannot fill a volume
that still has room to spare.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import get_settings

#: Long enough to absorb a burst of requests, short enough that a big import is
#: reflected before the next one is decided.
_CACHE_TTL_S = 5.0

_cache: tuple[float, int] | None = None


class QuotaExceededError(RuntimeError):
    """The configured storage ceiling would be passed by this write."""


@dataclass(frozen=True)
class QuotaStatus:
    used_bytes: int
    limit_bytes: int

    @property
    def enabled(self) -> bool:
        return self.limit_bytes > 0

    @property
    def available_bytes(self) -> int:
        if not self.enabled:
            return -1
        return max(0, self.limit_bytes - self.used_bytes)

    @property
    def percent_used(self) -> float:
        if not self.enabled:
            return 0.0
        return round(min(100.0, self.used_bytes / self.limit_bytes * 100), 1)


def _tree_size(root: Path) -> int:
    total = 0
    for directory, _, files in os.walk(root, onerror=lambda _: None):
        for name in files:
            try:
                total += os.stat(os.path.join(directory, name)).st_size
            except OSError:
                # A file removed mid-walk is normal; skipping it under-counts by
                # its size for one cycle, which the headroom absorbs.
                continue
    return total


def used_bytes(*, refresh: bool = False) -> int:
    """Bytes currently held under the configured data directories."""

    global _cache
    now = time.monotonic()
    if not refresh and _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]

    settings = get_settings()
    roots = {Path(settings.storage_dir), Path(settings.review_dir)}
    total = sum(_tree_size(root) for root in roots if root.is_dir())
    _cache = (now, total)
    return total


def reset_cache() -> None:
    """Drop the cached total — used by tests and right after a large write."""

    global _cache
    _cache = None


def status(*, refresh: bool = False) -> QuotaStatus:
    limit_gb = get_settings().storage_quota_gb
    return QuotaStatus(
        used_bytes=used_bytes(refresh=refresh),
        limit_bytes=int(limit_gb * 1024**3),
    )


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} B"
        size /= 1024
    return f"{size:.1f} GB"


def check(incoming_bytes: int = 0) -> QuotaStatus:
    """Raise :class:`QuotaExceededError` if `incoming_bytes` would not fit.

    Call before accepting an ingest, not after: the point is to refuse the
    upload rather than to notice afterwards that the disk is full.
    """

    current = status()
    if not current.enabled:
        return current
    if current.used_bytes + incoming_bytes > current.limit_bytes:
        raise QuotaExceededError(
            f"Kho dữ liệu đã dùng {_human(current.used_bytes)} trên hạn mức "
            f"{_human(current.limit_bytes)}; còn trống "
            f"{_human(current.available_bytes)}. Xoá bớt dataset hoặc video "
            f"render (video dựng lại được từ HDF5) rồi thử lại."
        )
    return current
