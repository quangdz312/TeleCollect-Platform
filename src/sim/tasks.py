"""Định nghĩa task và điều kiện thành công.

Trách nhiệm: mô tả các task mà operator sẽ demo (tiêu chí nâng cao yêu cầu
thu dataset nhiều task), gồm model MJCF dùng, cách sinh trạng thái đầu, và
hàm chấm thành công. Registry ở đây là nguồn sự thật cho cả lúc thu demo lẫn
lúc đánh giá policy, để success rate hai bên so sánh được với nhau.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    """Đặc tả một task demo."""

    name: str
    """Định danh dùng trong dataset và URL, vd: "pick_place_cube"."""

    description: str
    """Mô tả hiển thị cho operator trước khi bắt đầu ghi."""

    model_path: str
    """Đường dẫn file MJCF của scene."""

    max_steps: int
    """Trần số bước một episode, tránh phiên treo vô hạn."""

    success_fn: Callable[..., bool]
    """Hàm chấm thành công, nhận state sim và trả về True/False."""


def register(spec: TaskSpec) -> None:
    """Thêm một task vào registry."""
    raise NotImplementedError


def get_task(name: str) -> TaskSpec:
    """Lấy đặc tả task theo tên; ném KeyError nếu chưa đăng ký."""
    raise NotImplementedError


def list_tasks() -> list[TaskSpec]:
    """Liệt kê mọi task khả dụng — API `/tasks` dùng hàm này."""
    raise NotImplementedError
