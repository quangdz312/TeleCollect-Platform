"""Minimal offline Behavior Cloning runner backed by RoboMimic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import h5py

from src.sim.collection.schema_validator import ValidationResult, validate_training_dataset

MINIMAL_OBSERVATION_KEYS = (
    "object",
    "robot0_eef_pos",
    "robot0_eef_quat",
    "robot0_gripper_qpos",
)


@dataclass(frozen=True)
class BCTrainingPlan:
    dataset: str
    output_dir: str
    name: str
    epochs: int
    batch_size: int
    num_workers: int
    device: str
    observation_keys: tuple[str, ...]
    train_demos: int
    valid_demos: int
    validation_enabled: bool
    policy: str = "bc"
    learning_rate: float = 1e-4
    seed: int = 1
    save_every_n_epochs: int | None = None
    sequence_length: int = 50
    rnn_hidden_dim: int = 400
    rnn_layers: int = 2
    normalize_observations: bool = False
    observation_profile: str = "all"
    rollout_enabled: bool = False
    rollout_every_n_epochs: int = 20
    rollout_episodes: int = 5
    rollout_horizon: int = 500

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_training_dataset(
    dataset: str | Path,
    *,
    output_dir: str | Path,
    name: str,
    epochs: int,
    batch_size: int,
    num_workers: int,
    device: str,
    policy: str = "bc",
    learning_rate: float = 1e-4,
    seed: int = 1,
    save_every_n_epochs: int | None = None,
    sequence_length: int = 50,
    rnn_hidden_dim: int = 400,
    rnn_layers: int = 2,
    normalize_observations: bool = False,
    observation_profile: str = "all",
    rollout_enabled: bool = False,
    rollout_every_n_epochs: int = 20,
    rollout_episodes: int = 5,
    rollout_horizon: int = 500,
) -> tuple[BCTrainingPlan, ValidationResult]:
    if policy not in {"bc", "bc-rnn"}:
        raise ValueError(f"Policy không được hỗ trợ: {policy}")
    path = Path(dataset).resolve()
    result = validate_training_dataset(path)
    if not result.valid:
        raise ValueError("Dataset không hợp lệ: " + "; ".join(result.errors))
    with h5py.File(path, "r") as handle:
        first_name = sorted(handle["data"], key=lambda key: int(key.split("_")[1]))[0]
        available_keys = tuple(sorted(handle["data"][first_name]["obs"].keys()))
        if observation_profile == "minimal":
            missing = [key for key in MINIMAL_OBSERVATION_KEYS if key not in available_keys]
            if missing:
                raise ValueError("Dataset thiếu observation tối giản: " + ", ".join(missing))
            observation_keys = MINIMAL_OBSERVATION_KEYS
        elif observation_profile == "all":
            observation_keys = available_keys
        else:
            raise ValueError(f"Observation profile không được hỗ trợ: {observation_profile}")
        train_demos = len(handle["mask/train"])
        valid_demos = len(handle["mask/valid"])
    return BCTrainingPlan(
        dataset=str(path),
        output_dir=str(Path(output_dir).resolve()),
        name=name,
        epochs=epochs,
        batch_size=batch_size,
        num_workers=num_workers,
        device=device,
        observation_keys=observation_keys,
        train_demos=train_demos,
        valid_demos=valid_demos,
        # RoboMimic v1.5 asserts when observation normalization and validation
        # are enabled together. Normalized runs use simulator rollout as their
        # model-selection signal instead of silently crashing before epoch 1.
        validation_enabled=valid_demos > 0 and not normalize_observations,
        policy=policy,
        learning_rate=learning_rate,
        seed=seed,
        save_every_n_epochs=save_every_n_epochs,
        sequence_length=sequence_length,
        rnn_hidden_dim=rnn_hidden_dim,
        rnn_layers=rnn_layers,
        normalize_observations=normalize_observations,
        observation_profile=observation_profile,
        rollout_enabled=rollout_enabled,
        rollout_every_n_epochs=rollout_every_n_epochs,
        rollout_episodes=rollout_episodes,
        rollout_horizon=rollout_horizon,
    ), result


def make_robomimic_config(plan: BCTrainingPlan):
    """Create a RoboMimic v0.5 BC config; import lazily for API-only installs."""
    try:
        from robomimic.config import config_factory
    except ImportError as exc:
        raise RuntimeError(
            "Chưa cài RoboMimic. Chạy: pip install -r requirements-train.txt"
        ) from exc

    config = config_factory(algo_name="bc")
    config.experiment.name = plan.name
    config.experiment.validate = plan.validation_enabled
    config.experiment.rollout.enabled = plan.rollout_enabled
    config.experiment.rollout.rate = plan.rollout_every_n_epochs
    config.experiment.rollout.n = plan.rollout_episodes
    config.experiment.rollout.horizon = plan.rollout_horizon
    config.experiment.rollout.terminate_on_success = True
    config.experiment.render_video = False
    config.experiment.logging.log_tb = True
    config.experiment.logging.log_wandb = False
    config.experiment.save.enabled = True
    config.experiment.save.every_n_epochs = (
        plan.save_every_n_epochs or max(1, plan.epochs // 5)
    )
    config.experiment.save.on_best_validation = plan.validation_enabled
    config.experiment.save.on_best_rollout_success_rate = plan.rollout_enabled
    config.train.data = [{"path": plan.dataset}]
    config.train.output_dir = plan.output_dir
    config.train.num_epochs = plan.epochs
    config.train.batch_size = plan.batch_size
    config.train.num_data_workers = plan.num_workers
    config.train.seed = plan.seed
    config.train.hdf5_filter_key = "train"
    config.train.hdf5_validation_filter_key = "valid" if plan.validation_enabled else None
    config.train.hdf5_normalize_obs = plan.normalize_observations
    config.observation.modalities.obs.low_dim = list(plan.observation_keys)
    config.observation.modalities.obs.rgb = []
    config.observation.modalities.obs.depth = []
    config.observation.modalities.obs.scan = []
    config.observation.modalities.goal.low_dim = []
    config.observation.modalities.goal.rgb = []
    config.observation.modalities.goal.depth = []
    config.observation.modalities.goal.scan = []
    config.algo.rnn.enabled = plan.policy == "bc-rnn"
    if config.algo.rnn.enabled:
        config.algo.rnn.rnn_type = "LSTM"
        config.algo.rnn.horizon = plan.sequence_length
        config.algo.rnn.hidden_dim = plan.rnn_hidden_dim
        config.algo.rnn.num_layers = plan.rnn_layers
        config.algo.rnn.open_loop = False
        config.algo.rnn.kwargs.bidirectional = False
        config.train.seq_length = plan.sequence_length
    config.algo.optim_params.policy.learning_rate.initial = plan.learning_rate
    config.algo.gmm.enabled = False
    return config


def run_training(plan: BCTrainingPlan) -> None:
    config = make_robomimic_config(plan)
    try:
        import torch
        from robomimic.scripts.train import train
        from robomimic.utils import torch_utils
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu dependency training. Chạy: pip install -r requirements-train.txt"
        ) from exc

    if plan.device == "cpu":
        device = torch.device("cpu")
    elif plan.device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("Đã chọn CUDA nhưng PyTorch không thấy GPU CUDA")
        device = torch.device("cuda")
    else:
        device = torch_utils.get_torch_device(try_to_use_cuda=True)
    train(config, device=device)
