from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.schemas import RawEpisodeResponse


def test_raw_episode_contract_accepts_teleop_metadata() -> None:
    episode = RawEpisodeResponse(
        episode_id="lift_cube_a1b2c3d4",
        display_name="lift_cube_a1b2c3d4",
        source="teleop",
        task="lift_cube",
        created_at="2026-08-24T03:00:00Z",
        length=300,
        duration_s=10.0,
        control_hz=30.0,
        size_bytes=1024,
        recorded_success=True,
        quality=None,
        review_status="pending",
        operator_id="operator-1",
        collection_batch_id=None,
        cameras={"front": True, "birdview": True, "wrist": True},
        artifact_health="healthy",
    )

    assert episode.source == "teleop"
    assert episode.cameras.wrist is True
    assert episode.created_at is not None


def test_raw_episode_contract_accepts_scripted_metadata() -> None:
    episode = RawEpisodeResponse(
        episode_id="lift_clean_seed0.hdf5::demo_0",
        display_name="lift_001",
        source="scripted",
        task="lift",
        length=93,
        duration_s=3.1,
        control_hz=30.0,
        recorded_success=True,
        quality="clean",
        review_status="approved",
        collection_batch_id="batch-001",
        cameras={"front": True, "birdview": True, "wrist": True},
    )

    assert episode.source == "scripted"
    assert episode.quality == "clean"
    assert episode.operator_id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "upload"),
        ("quality", "excellent"),
        ("review_status", "deleted"),
        ("artifact_health", "missing"),
        ("length", -1),
        ("duration_s", -0.1),
        ("control_hz", 0),
        ("size_bytes", -1),
    ],
)
def test_raw_episode_contract_rejects_invalid_metadata(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "episode_id": "episode-1",
        "source": "teleop",
        "task": "lift",
        "length": 10,
        "review_status": "pending",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        RawEpisodeResponse.model_validate(payload)
