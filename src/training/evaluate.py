"""Đánh giá policy trong sim — chỉ số nghiệm thu của dự án.

Trách nhiệm: chạy policy đã huấn luyện trên chính task đã thu demo, đếm tỷ lệ
episode thành công theo `TaskSpec.success_fn`.

Điều kiện đầu phải được sinh bằng seed cố định và khác với seed lúc thu demo:
đánh giá trên đúng cấu hình đã thấy khi huấn luyện sẽ cho success rate ảo cao
hơn thực tế.
"""

from dataclasses import dataclass


@dataclass
class EvalResult:
    """Kết quả một đợt đánh giá."""

    task_name: str
    num_episodes: int
    num_success: int
    success_rate: float
    mean_steps: float
    """Số bước trung bình tới lúc thành công — policy chậm cũng là tín hiệu xấu."""


def evaluate(
    checkpoint_path: str,
    task_name: str,
    num_episodes: int = 50,
    seed: int = 0,
) -> EvalResult:
    """Chạy policy trong sim và tính success rate."""
    raise NotImplementedError


def rollout(policy: object, env: object, max_steps: int) -> bool:
    """Chạy một episode do policy điều khiển; trả về có thành công không."""
    raise NotImplementedError
