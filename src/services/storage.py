"""Bố cục thư mục artifact và đọc/ghi file episode.

Trách nhiệm: gói mọi đường dẫn dưới `settings.storage_dir` vào một chỗ, để
recorder, export và playback không tự ghép path bằng tay. Đổi sang object
storage (S3/MinIO) sau này chỉ cần thay implementation ở đây.
"""


def episode_dir(episode_id: str) -> str:
    """Thư mục chứa artifact của một episode."""
    raise NotImplementedError


def video_path(episode_id: str, camera: str) -> str:
    """Đường dẫn file video của một camera trong episode."""
    raise NotImplementedError


def actions_path(episode_id: str) -> str:
    """Đường dẫn file parquet chứa action + state cấp thấp."""
    raise NotImplementedError


def meta_path(episode_id: str) -> str:
    """Đường dẫn file meta.json của episode."""
    raise NotImplementedError


def delete_episode(episode_id: str) -> None:
    """Xoá toàn bộ artifact của một episode (demo bị từ chối, dọn dẹp)."""
    raise NotImplementedError


def disk_usage(episode_id: str) -> int:
    """Dung lượng episode tính bằng byte — theo dõi chi phí lưu trữ."""
    raise NotImplementedError
