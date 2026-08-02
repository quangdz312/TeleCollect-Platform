"""Vòng huấn luyện behavior cloning.

Trách nhiệm: chạy huấn luyện từ một dataset đã đóng băng, lưu checkpoint, và
ghi lại đủ thông tin xuất xứ (dataset id, tag DVC, siêu tham số) để tái lập.

Chạy như job nền: API `/training/jobs` chỉ tạo job rồi trả id.
"""

from dataclasses import dataclass


@dataclass
class TrainConfig:
    """Siêu tham số một lần huấn luyện."""

    dataset_root: str
    epochs: int = 50
    batch_size: int = 64
    lr: float = 1e-4
    camera: str = "front"
    device: str = "cuda"
    checkpoint_dir: str = "./data/checkpoints"


def train(config: TrainConfig) -> str:
    """Huấn luyện policy, trả về đường dẫn checkpoint tốt nhất."""
    raise NotImplementedError


def save_checkpoint(policy: object, path: str, meta: dict[str, object]) -> None:
    """Lưu checkpoint kèm metadata xuất xứ."""
    raise NotImplementedError
