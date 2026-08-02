"""Đóng băng demo đã duyệt thành một phiên bản dataset.

Trách nhiệm: chọn episode theo bộ lọc (task, trạng thái `approved`), áp
khoảng cắt mà reviewer đã đặt, gọi writer tương ứng, rồi tag phiên bản bằng
DVC.

Chỉ lấy demo `APPROVED` — đây là chỗ ràng buộc human-in-the-loop được thực
thi bằng code, không phải bằng quy ước.
"""

from dataclasses import dataclass

from src.models.enums import DatasetFormat


@dataclass
class BuildResult:
    """Kết quả một lần build dataset."""

    dataset_id: str
    num_episodes: int
    total_steps: int
    size_bytes: int
    """Dung lượng dataset — theo dõi chi phí lưu trữ."""

    dvc_tag: str | None


def select_episodes(task_names: list[str]) -> list[str]:
    """Lấy id các episode đã duyệt của những task đã cho."""
    raise NotImplementedError


def build(
    name: str,
    task_names: list[str],
    fmt: DatasetFormat,
) -> BuildResult:
    """Build một phiên bản dataset mới từ demo đã duyệt."""
    raise NotImplementedError


def track_with_dvc(dataset_path: str, tag: str) -> str:
    """Đưa dataset vào DVC và tạo tag; trả về revision."""
    raise NotImplementedError
