from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.schemas import EvaluationJobRequest, TrainingJobRequest


def test_training_request_accepts_bc_rnn_gpu_configuration() -> None:
    request = TrainingJobRequest(
        dataset_id="dataset-123",
        name="lift_bcrnn_v1",
        policy="bc-rnn",
        epochs=100,
        batch_size=64,
        num_workers=0,
        device="cuda",
        sequence_length=20,
        normalize_observations=True,
        observation_profile="minimal",
        rollout_enabled=True,
        rollout_every_n_epochs=10,
        rollout_episodes=5,
        rollout_horizon=500,
    )

    assert request.policy == "bc-rnn"
    assert request.device == "cuda"
    assert request.sequence_length == 20
    assert request.normalize_observations is True
    assert request.observation_profile == "minimal"
    assert request.rollout_horizon == 500


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "../outside"),
        ("policy", "transformer"),
        ("epochs", 0),
        ("batch_size", 0),
        ("num_workers", -1),
        ("device", "gpu"),
        ("learning_rate", 0.0),
        ("observation_profile", "custom"),
        ("rollout_every_n_epochs", 0),
        ("rollout_episodes", 0),
        ("rollout_horizon", 0),
    ],
)
def test_training_request_rejects_unsafe_or_invalid_values(field: str, value: object) -> None:
    payload: dict[str, object] = {"dataset_id": "dataset-123", "name": "safe_run"}
    payload[field] = value

    with pytest.raises(ValidationError):
        TrainingJobRequest.model_validate(payload)


def test_evaluation_request_accepts_headless_video_rollouts() -> None:
    request = EvaluationJobRequest(
        training_run_id="run-123",
        checkpoint_id="best_validation",
        num_rollouts=20,
        horizon=250,
        seed=5000,
        record_videos=3,
    )

    assert request.record_videos == 3
    assert request.horizon == 250


def test_evaluation_request_rejects_more_videos_than_rollouts() -> None:
    with pytest.raises(ValidationError, match="record_videos"):
        EvaluationJobRequest(
            training_run_id="run-123",
            checkpoint_id="best_validation",
            num_rollouts=2,
            record_videos=3,
        )
