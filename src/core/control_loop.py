"""Vòng điều khiển realtime của một phiên teleop.

Trách nhiệm: chạy đúng nhịp `control_hz` — lấy input mới nhất của operator,
dịch thành action, step sim, đẩy observation ngược về client, và (nếu đang
ghi) đưa cặp observation-action cho recorder.

Hai quyết định quan trọng để giữ p95 latency dưới 100 ms:

1. Dùng input *mới nhất* chứ không xếp hàng. Input cũ chưa xử lý bị bỏ; xếp
   hàng sẽ khiến robot chạy trễ so với tay người điều khiển và càng lúc càng
   lệch.
2. Render và gửi frame không được chặn vòng điều khiển. Nếu encode chậm, bỏ
   frame hiển thị — nhưng KHÔNG bao giờ bỏ mẫu ghi, vì dataset phải liên tục.
"""

from dataclasses import dataclass


@dataclass
class LoopStats:
    """Số đo một vòng điều khiển, phục vụ mục tiêu tối ưu độ trễ."""

    ticks: int
    """Số chu kỳ đã chạy."""

    dropped_frames: int
    """Số frame hiển thị bị bỏ do encode không kịp."""

    p95_latency_ms: float
    """Trễ p95 từ lúc nhận input tới lúc gửi observation tương ứng."""

    overruns: int
    """Số chu kỳ chạy quá ngân sách thời gian — tín hiệu tụt nhịp."""


class ControlLoop:
    """Vòng điều khiển của một phiên.

    Chữ ký dự kiến:
        def __init__(self, session_id: str, env: RobotEnv, control_hz: int) -> None
        async def run(self) -> None
        def submit_input(self, input_delta: dict[str, float]) -> None
        async def stop(self) -> None
        def stats(self) -> LoopStats
    """

    def __init__(self, session_id: str, env: object, control_hz: int) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        """Chạy vòng lặp tới khi `stop()` được gọi."""
        raise NotImplementedError

    def submit_input(self, input_delta: dict[str, float]) -> None:
        """Nhận input mới nhất từ WebSocket, ghi đè input chưa dùng."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Dừng vòng lặp và chờ chu kỳ đang chạy kết thúc."""
        raise NotImplementedError

    def stats(self) -> LoopStats:
        """Trả về số đo độ trễ của phiên."""
        raise NotImplementedError
