"""torch Dataset đọc dataset đã export.

Trách nhiệm: nạp cặp (observation, action) từ định dạng LeRobot thành tensor.
Đọc từ dataset đã đóng băng chứ không từ storage episode thô — như vậy lần
huấn luyện nào cũng tái lập được từ một tag DVC cụ thể.

Frame video giải mã lazy theo từng batch; giải mã trước toàn bộ sẽ không vừa
RAM khi dataset lên tới hàng trăm episode.
"""


class DemonstrationDataset:
    """Tập cặp observation-action phẳng hoá từ nhiều episode.

    Chữ ký dự kiến:
        def __init__(self, root: str, camera: str, action_horizon: int = 1) -> None
        def __len__(self) -> int
        def __getitem__(self, idx: int) -> tuple[Tensor, Tensor]
        def normalization_stats(self) -> dict[str, Tensor]
    """

    def __init__(self, root: str, camera: str, action_horizon: int = 1) -> None:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    def __getitem__(self, idx: int) -> tuple[object, object]:
        """Trả về (observation, action) đã chuẩn hoá tại chỉ số `idx`."""
        raise NotImplementedError

    def normalization_stats(self) -> dict[str, object]:
        """Mean/std của action và state — phải lưu kèm checkpoint.

        Thiếu thống kê này thì lúc suy luận không khôi phục được thang đo
        action, và policy sẽ hành xử sai hoàn toàn.
        """
        raise NotImplementedError
