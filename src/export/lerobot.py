"""Writer định dạng LeRobot.

Trách nhiệm: chuyển episode nội bộ sang bố cục LeRobot để dùng được ngay với
hệ sinh thái imitation learning sẵn có.

Bố cục đích:
    {root}/
        meta/info.json          # fps, các feature, thống kê
        meta/episodes.jsonl     # một dòng mỗi episode
        data/chunk-000/*.parquet
        videos/chunk-000/{camera}/*.mp4
"""


class LeRobotWriter:
    """Ghi dataset theo bố cục LeRobot.

    Chữ ký dự kiến:
        def __init__(self, root: str, fps: int) -> None
        def add_episode(self, episode_id: str) -> None
        def finalize(self) -> str
    """

    def __init__(self, root: str, fps: int) -> None:
        raise NotImplementedError

    def add_episode(self, episode_id: str) -> None:
        """Thêm một episode (đã áp khoảng cắt) vào dataset."""
        raise NotImplementedError

    def finalize(self) -> str:
        """Ghi metadata tổng và đóng dataset; trả về đường dẫn gốc."""
        raise NotImplementedError
