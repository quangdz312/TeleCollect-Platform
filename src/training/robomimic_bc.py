"""Minimal offline Behavior Cloning runner backed by RoboMimic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import h5py

from src.sim.collection.schema_validator import ValidationResult, validate_training_dataset


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
) -> tuple[BCTrainingPlan, ValidationResult]:
    if policy not in {"bc", "bc-rnn"}:
        raise ValueError(f"Policy không được hỗ trợ: {policy}")
    path = Path(dataset).resolve()
    result = validate_training_dataset(path)
    if not result.valid:
        raise ValueError("Dataset không hợp lệ: " + "; ".join(result.errors))
    with h5py.File(path, "r") as handle:
        first_name = sorted(handle["data"], key=lambda key: int(key.split("_")[1]))[0]
        observation_keys = tuple(sorted(handle["data"][first_name]["obs"].keys()))
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
        validation_enabled=valid_demos > 0,
        policy=policy,
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
    config.experiment.rollout.enabled = False
    config.experiment.logging.log_tb = True
    config.experiment.logging.log_wandb = False
    config.experiment.save.enabled = True
    config.experiment.save.every_n_epochs = max(1, plan.epochs // 5)
    config.experiment.save.on_best_validation = plan.validation_enabled
    config.train.data = [{"path": plan.dataset}]
    config.train.output_dir = plan.output_dir
    config.train.num_epochs = plan.epochs
    config.train.batch_size = plan.batch_size
    config.train.num_data_workers = plan.num_workers
    config.train.hdf5_filter_key = "train"
    config.train.hdf5_validation_filter_key = "valid"
    # RoboMimic v0.5 rejects observation normalization whenever a validation
    # filter is configured (TrainUtils asserts before the first epoch). Keep
    # the deterministic train/valid split and disable normalization in this
    # minimal runner; action and observation tensors remain unchanged.
    config.train.hdf5_normalize_obs = False
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
        config.algo.rnn.horizon = 10
        config.algo.rnn.hidden_dim = 400
        config.algo.rnn.num_layers = 2
        config.algo.rnn.open_loop = False
        config.algo.rnn.kwargs.bidirectional = False
        config.train.seq_length = 10
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
