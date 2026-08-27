"""The one switch separating the packaged app from the deployed web build.

Both products are built from this `src/`, and they differ in a single respect:
the app collects data, the web only receives data collected elsewhere. That
difference is `settings.collection_enabled`, applied once in `src/api/routes.py`.

Worth testing because the failure is quiet in both directions. Left on by
accident, the deployment exposes teleop and manual upload it was meant not to
have. Turned off too broadly, the app silently loses the ability to record.
"""

from __future__ import annotations

import importlib

import pytest

import src.api.routes
import src.config
import src.main


def _paths(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> list[str]:
    """Reload the router with the flag set, and return the mounted paths."""

    monkeypatch.setenv("COLLECTION_ENABLED", "true" if enabled else "false")
    src.config.get_settings.cache_clear()
    importlib.reload(src.api.routes)
    app = importlib.reload(src.main).app
    return list(app.openapi()["paths"])


@pytest.fixture(autouse=True)
def _restore_modules():
    """Leave the imported app as the rest of the suite expects to find it."""

    yield
    src.config.get_settings.cache_clear()
    importlib.reload(src.api.routes)
    importlib.reload(src.main)


def test_collection_endpoints_are_absent_when_disabled(monkeypatch):
    paths = _paths(monkeypatch, enabled=False)

    assert not [path for path in paths if "/teleop" in path]
    assert not [path for path in paths if "/demos" in path]


def test_collection_endpoints_are_present_when_enabled(monkeypatch):
    paths = _paths(monkeypatch, enabled=True)

    assert [path for path in paths if "/teleop" in path]
    assert [path for path in paths if "/demos" in path]


def test_review_and_training_survive_the_flag(monkeypatch):
    """The web build keeps everything it exists to do."""

    paths = _paths(monkeypatch, enabled=False)

    for kept in ("/raw", "/datasets", "/labeling", "/training", "/auth"):
        assert [path for path in paths if kept in path], f"{kept} disappeared"


def test_the_upload_routes_built_on_demos_helpers_survive(monkeypatch):
    """`src/api/demos.py` also supplies chunked upload reading.

    Batch import and dataset upload import `_save_upload_chunked` from it, so
    dropping the demos *router* must not drop those two endpoints with it.
    """

    paths = _paths(monkeypatch, enabled=False)

    assert "/api/v1/datasets/uploads" in paths
    assert "/api/v1/raw/batches/{batch_id}/import" in paths


def test_collection_is_enabled_by_default():
    """A developer running the repo gets the whole product."""

    src.config.get_settings.cache_clear()
    assert src.config.get_settings().collection_enabled is True
