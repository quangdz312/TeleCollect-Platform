"""Quản lý vòng đời phiên teleop.

Trách nhiệm: một phiên gắn một operator với một instance sim và (nếu đang
ghi) một recorder. Module này cấp phát, tra cứu và dọn phiên, đồng thời thực
thi trần `max_concurrent_sessions` trong config — mỗi phiên tốn một instance
MuJoCo nên không thể mở vô hạn.

Phiên phải được dọn cả khi WebSocket rớt giữa chừng, nếu không instance sim
sẽ rò rỉ; vì vậy có `reap_stale`.
"""

from dataclasses import dataclass
from enum import StrEnum


class SessionState(StrEnum):
    """Trạng thái của một phiên teleop."""

    IDLE = "idle"
    """Đã kết nối, sim sẵn sàng, chưa ghi."""

    RECORDING = "recording"
    """Đang ghi observation + action vào episode."""

    CLOSED = "closed"
    """Đã đóng, tài nguyên sim đã giải phóng."""


@dataclass
class TeleopSession:
    """Một phiên teleop đang sống."""

    session_id: str
    user_id: str
    task_name: str
    state: SessionState
    started_at: float
    episode_id: str | None = None
    """Episode đang ghi, None nếu đang ở IDLE."""


class SessionManager:
    """Sổ đăng ký các phiên teleop đang mở.

    Chữ ký dự kiến:
        def create(self, user_id: str, task_name: str) -> TeleopSession
        def get(self, session_id: str) -> TeleopSession
        def start_recording(self, session_id: str) -> str
        def stop_recording(self, session_id: str) -> str
        def close(self, session_id: str) -> None
        def active_count(self) -> int
        def reap_stale(self, timeout_s: float) -> int
    """

    def create(self, user_id: str, task_name: str) -> TeleopSession:
        """Mở phiên mới; ném lỗi nếu đã chạm `max_concurrent_sessions`."""
        raise NotImplementedError

    def get(self, session_id: str) -> TeleopSession:
        """Tra phiên theo id; ném KeyError nếu không tồn tại."""
        raise NotImplementedError

    def start_recording(self, session_id: str) -> str:
        """Bắt đầu ghi, trả về episode_id vừa tạo."""
        raise NotImplementedError

    def stop_recording(self, session_id: str) -> str:
        """Dừng ghi và chốt episode, trả về episode_id."""
        raise NotImplementedError

    def close(self, session_id: str) -> None:
        """Đóng phiên và giải phóng instance sim."""
        raise NotImplementedError

    def active_count(self) -> int:
        """Số phiên chưa đóng — dùng để kiểm tra trần đồng thời."""
        raise NotImplementedError

    def reap_stale(self, timeout_s: float) -> int:
        """Dọn phiên mất kết nối quá `timeout_s`, trả về số phiên đã dọn."""
        raise NotImplementedError
