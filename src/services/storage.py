"""Bố cục thư mục artifact và đọc/ghi file episode.

Trách nhiệm: gói mọi đường dẫn dưới `settings.storage_dir` vào một chỗ, để
upload, playback (Bước 3b) và export (Bước 4) không tự ghép path bằng tay.
Đổi sang object storage (S3/MinIO) sau này chỉ cần thay implementation ở đây.

Bố cục cố định (đừng đổi — cấu trúc zip ở dataset phụ thuộc đúng tên file):
    <storage_dir>/episodes/<episode_id>/front.mp4
    <storage_dir>/episodes/<episode_id>/wrist.mp4        (optional)
    <storage_dir>/episodes/<episode_id>/trajectory.json  (optional)
    <storage_dir>/episodes/<episode_id>/thumb.jpg         (optional)
    <storage_dir>/tmp/<tmp_id>/...                        (thư mục tạm lúc validate upload)
    <storage_dir>/datasets/<dataset_id>.zip                (Bước 4 — dataset đã đóng gói)

`<episode_id>` là UUID — yêu cầu "tên file dùng UUID" trong plan được thoả
bằng việc THƯ MỤC là UUID, tên file bên trong cố định (`front.mp4`...) để
khớp cấu trúc zip dataset.
"""

import shutil
import uuid
from pathlib import Path

from src.config import get_settings

FRONT_FILENAME = "front.mp4"
WRIST_FILENAME = "wrist.mp4"
TRAJECTORY_FILENAME = "trajectory.json"
THUMBNAIL_FILENAME = "thumb.jpg"
ACTIONS_FILENAME = "actions.parquet"
META_FILENAME = "meta.json"


def episodes_root() -> Path:
    return Path(get_settings().storage_dir) / "episodes"


def tmp_root() -> Path:
    return Path(get_settings().storage_dir) / "tmp"


def datasets_root() -> Path:
    return Path(get_settings().storage_dir) / "datasets"


def dataset_zip_path(dataset_id: str) -> Path:
    """`<storage_dir>/datasets/<dataset_id>.zip` — `dataset_id` là UUID sinh
    nội bộ (không phải input người dùng), không cần resolve chống traversal
    như `episode_dir`."""
    datasets_root().mkdir(parents=True, exist_ok=True)
    return datasets_root() / f"{dataset_id}.zip"


def dataset_hdf5_path(dataset_id: str) -> Path:
    """RoboMimic dataset đã lọc theo quyết định review."""
    datasets_root().mkdir(parents=True, exist_ok=True)
    return datasets_root() / f"{dataset_id}.hdf5"


def _resolve_within(base: Path, name: str) -> Path:
    """Resolve `base/name` và đảm bảo kết quả nằm trong `base` — chặn path
    traversal qua `name` (vd `../../etc/passwd`)."""
    base_resolved = base.resolve()
    candidate = (base_resolved / name).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(f"Đường dẫn không hợp lệ: {name}")
    return candidate


def new_tmp_dir() -> Path:
    """Thư mục tạm mới (uuid riêng, KHÔNG phải episode_id) để ghi/validate file
    upload trước khi biết chắc episode có được tạo hay không."""
    tmp_root().mkdir(parents=True, exist_ok=True)
    tmp_id = uuid.uuid4().hex
    path = _resolve_within(tmp_root(), tmp_id)
    path.mkdir(parents=True, exist_ok=False)
    return path


def episode_dir(episode_id: str) -> Path:
    return _resolve_within(episodes_root(), episode_id)


def video_path(episode_id: str, camera: str) -> Path:
    """Video path for a recorded sim camera inside an episode directory."""
    filename = f"{camera}.mp4"
    if camera == "front":
        filename = FRONT_FILENAME
    elif camera == "wrist":
        filename = WRIST_FILENAME
    return episode_dir(episode_id) / filename


def actions_path(episode_id: str) -> Path:
    return episode_dir(episode_id) / ACTIONS_FILENAME


def meta_path(episode_id: str) -> Path:
    return episode_dir(episode_id) / META_FILENAME


def promote_tmp_to_episode(tmp_dir: Path, episode_id: str) -> Path:
    """Move thư mục tạm đã validate xong sang `episodes/<episode_id>/`. Chỉ
    gọi SAU khi row DB đã insert thành công (thứ tự trong plan: DB trước,
    move file sau, để lỡ move fail thì còn kịp rollback transaction)."""
    episodes_root().mkdir(parents=True, exist_ok=True)
    target = episode_dir(episode_id)
    shutil.move(str(tmp_dir), str(target))
    return target


def delete_dir(path: Path) -> None:
    """Xoá sạch một thư mục (tmp hoặc episode) nếu có — best-effort dọn rác,
    không raise nếu thư mục không tồn tại."""
    shutil.rmtree(path, ignore_errors=True)


def delete_episode(episode_id: str) -> None:
    """Xoá toàn bộ artifact của một episode."""
    delete_dir(episode_dir(episode_id))


def dir_size_bytes(path: Path) -> int:
    """Tổng dung lượng mọi file trong thư mục (đệ quy)."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
