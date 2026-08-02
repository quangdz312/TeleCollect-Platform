"""Writer định dạng RLDS.

Trách nhiệm: xuất cùng tập episode sang RLDS (TFDS) cho các pipeline yêu cầu
định dạng này. Một episode RLDS là chuỗi step, mỗi step gồm observation,
action và các cờ `is_first` / `is_last` / `is_terminal`.
"""


class RLDSWriter:
    """Ghi dataset theo bố cục RLDS.

    Chữ ký dự kiến:
        def __init__(self, root: str) -> None
        def add_episode(self, episode_id: str) -> None
        def finalize(self) -> str
    """

    def __init__(self, root: str) -> None:
        raise NotImplementedError

    def add_episode(self, episode_id: str) -> None:
        """Thêm một episode dưới dạng chuỗi step RLDS."""
        raise NotImplementedError

    def finalize(self) -> str:
        """Ghi `dataset_info.json` và đóng dataset; trả về đường dẫn gốc."""
        raise NotImplementedError
