"""The ceiling that keeps a deployment from filling its own disk.

What this protects is not room for episodes but the volume itself: a full disk
means Postgres cannot write and the site goes down, so losing an upload is the
cheap outcome. Both directions of error are bad — refusing while there is room
wastes a working deployment, waving through while there is none defeats the
point — so both are tested.
"""

from __future__ import annotations

import pytest

from src.config import get_settings
from src.services import quota


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Point the quota at an empty tree, and never at the real data directory."""

    settings = get_settings()
    storage = tmp_path / "storage"
    review = tmp_path / "review"
    storage.mkdir()
    review.mkdir()
    monkeypatch.setattr(settings, "storage_dir", str(storage))
    monkeypatch.setattr(settings, "review_dir", str(review))
    quota.reset_cache()
    yield storage
    quota.reset_cache()


def _fill(directory, megabytes: int, name: str = "blob.bin") -> None:
    (directory / name).write_bytes(b"\0" * (megabytes * 1024 * 1024))
    quota.reset_cache()


def test_no_limit_by_default(monkeypatch):
    """Dev and test environments must behave as they did before."""

    monkeypatch.setattr(get_settings(), "storage_quota_gb", 0)

    assert quota.status().enabled is False
    quota.check(10**12)  # would be absurd under any real ceiling


def test_usage_counts_both_data_directories(_isolated_data, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_quota_gb", 1)
    from pathlib import Path

    _fill(_isolated_data, 2)
    _fill(Path(settings.review_dir), 3, name="other.bin")

    assert quota.status(refresh=True).used_bytes == 5 * 1024 * 1024


def test_an_ingest_that_would_pass_the_ceiling_is_refused(_isolated_data, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_quota_gb", 10 / 1024)  # 10 MB
    _fill(_isolated_data, 8)

    with pytest.raises(quota.QuotaExceededError):
        quota.check(4 * 1024 * 1024)


def test_an_ingest_that_fits_is_allowed(_isolated_data, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_quota_gb", 10 / 1024)
    _fill(_isolated_data, 8)

    quota.check(1 * 1024 * 1024)


def test_the_refusal_says_what_is_used_and_what_is_left(_isolated_data, monkeypatch):
    """An operator reading this in a log should not need to go measuring."""

    monkeypatch.setattr(get_settings(), "storage_quota_gb", 10 / 1024)
    _fill(_isolated_data, 9)

    with pytest.raises(quota.QuotaExceededError) as caught:
        quota.check(5 * 1024 * 1024)

    message = str(caught.value)
    assert "9.0 MB" in message
    assert "10.0 MB" in message


def test_the_cached_total_is_reused_within_its_window(_isolated_data, monkeypatch):
    """Walking the tree per request would itself become the problem."""

    monkeypatch.setattr(get_settings(), "storage_quota_gb", 1)
    _fill(_isolated_data, 1)
    first = quota.used_bytes()

    (_isolated_data / "extra.bin").write_bytes(b"\0" * (1024 * 1024))

    assert quota.used_bytes() == first
    assert quota.used_bytes(refresh=True) == first + 1024 * 1024


def test_percent_used_is_reported_for_monitoring(_isolated_data, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_quota_gb", 10 / 1024)
    _fill(_isolated_data, 5)

    assert quota.status(refresh=True).percent_used == 50.0


def test_a_missing_data_directory_is_not_an_error(monkeypatch, tmp_path):
    """First boot, before anything has been written."""

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "absent"))
    monkeypatch.setattr(settings, "review_dir", str(tmp_path / "absent-too"))
    quota.reset_cache()

    assert quota.used_bytes(refresh=True) == 0
