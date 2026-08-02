"""Wrapper môi trường MuJoCo cho một robot mô phỏng.

Trách nhiệm: nạp model MJCF, giữ trạng thái vật lý của một phiên teleop, và
cung cấp vòng `reset` / `step` / `observe` cho control loop. Mỗi phiên teleop
sở hữu riêng một instance để các phiên không giẫm lên state của nhau.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class Observation:
    """Một lát cắt quan sát tại thời điểm `t`.

    Đây là đơn vị dữ liệu được ghi lại đồng bộ cùng action, nên mọi trường
    phải serialise được để đẩy qua WebSocket và lưu xuống dataset.
    """

    t: float
    """Thời gian mô phỏng (giây) kể từ lúc reset — dùng để đồng bộ với action."""

    qpos: list[float]
    """Vị trí các khớp."""

    qvel: list[float]
    """Vận tốc các khớp."""

    ee_pose: list[float]
    """Pose end-effector dạng [x, y, z, qw, qx, qy, qz]."""

    images: dict[str, bytes]
    """Frame từ từng camera, khoá là tên camera."""


class RobotEnv:
    """Môi trường một robot trong MuJoCo.

    Chữ ký dự kiến:
        def __init__(self, model_path: str, task: str, control_hz: int) -> None
        def reset(self, seed: int | None = None) -> Observation
        def step(self, action: list[float]) -> Observation
        def observe(self) -> Observation
        def is_success(self) -> bool
        def close(self) -> None
    """

    def __init__(self, model_path: str, task: str, control_hz: int) -> None:
        raise NotImplementedError

    def reset(self, seed: int | None = None) -> Observation:
        """Đưa robot về trạng thái đầu của task, trả về quan sát đầu tiên."""
        raise NotImplementedError

    def step(self, action: list[float]) -> Observation:
        """Áp một action trong đúng một chu kỳ điều khiển, trả về quan sát mới."""
        raise NotImplementedError

    def observe(self) -> Observation:
        """Lấy quan sát hiện tại mà không tiến thời gian mô phỏng."""
        raise NotImplementedError

    def is_success(self) -> bool:
        """Kiểm tra điều kiện thành công của task — dùng để gợi ý nhãn tự động."""
        raise NotImplementedError

    def close(self) -> None:
        """Giải phóng tài nguyên mô phỏng và render."""
        raise NotImplementedError

    @property
    def action_spec(self) -> dict[str, Any]:
        """Số chiều và biên của action, để frontend biết cách map input."""
        raise NotImplementedError
