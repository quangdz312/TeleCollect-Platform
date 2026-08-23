"""Quản lý vòng đời phiên teleop.

Trách nhiệm: một phiên gắn một operator với một instance sim và (nếu đang
ghi) một recorder. Module này cấp phát, tra cứu và dọn phiên, đồng thời thực
thi trần `max_concurrent_sessions` trong config — mỗi phiên tốn một instance
MuJoCo nên không thể mở vô hạn.

Phiên phải được dọn cả khi WebSocket rớt giữa chừng, nếu không instance sim
sẽ rò rỉ; vì vậy có `reap_stale`.

Phân chia trách nhiệm với `control_loop.py`: manager giữ sổ đăng ký và vòng
đời (tạo, tra, đóng, dọn), còn mọi thao tác chạm vào MuJoCo đều uỷ cho
`ControlLoop.submit_command` để env chỉ có đúng một luồng sở hữu. Manager
không bao giờ gọi `env.step()`/`env.reset()` trực tiếp.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.config import get_settings
from src.core.control_loop import ControlLoop, InvalidSessionState
from src.sim import tasks
from src.sim.environment import RobotEnv


class SessionState(StrEnum):
    """Trạng thái của một phiên teleop."""

    IDLE = "idle"
    """Đã kết nối, sim sẵn sàng, chưa ghi."""

    RECORDING = "recording"
    """Đang ghi observation + action vào episode."""

    CLOSED = "closed"
    """Đã đóng, tài nguyên sim đã giải phóng."""


# Tên các lỗi dưới đây khớp 1-1 với error code trong đặc tả (mục 15), nên giữ
# nguyên thay vì thêm hậu tố `Error` theo quy ước N818 — API map thẳng tên lớp
# sang code trả về cho client.
class SessionLimitReached(RuntimeError):  # noqa: N818 - SESSION_LIMIT_REACHED
    """Đã chạm `max_concurrent_sessions` — không mở thêm instance MuJoCo."""


class ControllerAlreadyConnected(RuntimeError):  # noqa: N818 - CONTROLLER_ALREADY_CONNECTED
    """Phiên đã có một controller WebSocket; MVP chỉ cho phép một."""


class SimulationInitFailed(RuntimeError):  # noqa: N818 - SIMULATION_INIT_FAILED
    """Không dựng được RobotEnv (thiếu thư viện render, model lỗi...)."""


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

    seed: int | None = None
    image_size: int = 480
    last_seen_at: float = field(default_factory=time.monotonic)
    last_error: str | None = None
    control_loop: ControlLoop | None = None
    controller_connected: bool = False
    disconnected_at: float | None = None

    @property
    def env(self) -> Any:
        """Env của phiên — thuộc sở hữu của worker thread, chỉ đọc để tra cứu."""
        loop = self.control_loop
        return None if loop is None else loop.env

    def snapshot(self) -> dict[str, Any]:
        """Trạng thái đọc được từ REST — lấy state sống từ control loop nếu có."""
        loop = self.control_loop
        state = self.state
        episode_id = self.episode_id
        if loop is not None and state is not SessionState.CLOSED:
            state = SessionState(loop.session_state())
            episode_id = loop.episode_id()
        return {
            "session_id": self.session_id,
            "operator_id": self.user_id,
            "task_name": self.task_name,
            "state": state.value,
            "seed": self.seed,
            "image_size": self.image_size,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "episode_id": episode_id,
            "last_error": loop.last_error() if loop is not None else self.last_error,
            "ws_url": f"/api/v1/teleop/ws/{self.session_id}",
        }


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

    Thread-safe: REST handler, WebSocket handler và background reaper cùng
    chạm vào registry này từ các luồng/task khác nhau.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TeleopSession] = {}
        self._lock = threading.RLock()

    # ----- vòng đời -----

    def create(
        self,
        user_id: str,
        task_name: str,
        seed: int | None = None,
        image_size: int = 480,
    ) -> TeleopSession:
        """Mở phiên mới; ném lỗi nếu đã chạm `max_concurrent_sessions`."""
        settings = get_settings()
        spec = tasks.get_task(task_name)  # KeyError -> TASK_NOT_FOUND ở tầng API

        with self._lock:
            if self.active_count() >= settings.max_concurrent_sessions:
                raise SessionLimitReached(f"Đã đạt trần {settings.max_concurrent_sessions} phiên đồng thời.")
            session_id = str(uuid.uuid4())
            # Giữ chỗ trước khi dựng sim (mất vài giây) để hai request song song
            # không cùng vượt trần.
            placeholder = TeleopSession(
                session_id=session_id,
                user_id=user_id,
                task_name=task_name,
                state=SessionState.IDLE,
                started_at=time.time(),
                seed=seed,
                image_size=image_size,
            )
            self._sessions[session_id] = placeholder

        def build_env() -> RobotEnv:
            # Chạy trên worker thread của ControlLoop, không phải ở đây: context
            # render EGL gắn với thread tạo nó (xem `ControlLoop._run_forever`).
            env = RobotEnv(
                model_path=spec.model_path,
                task=spec.name,
                control_hz=settings.control_hz,
                cameras=tuple(settings.camera_list()),
                image_size=image_size,
                preview_camera=settings.preview_camera,
                preview_size=settings.preview_size,
            )
            env.reset(seed=seed)
            return env

        loop = ControlLoop(
            session_id=session_id,
            control_hz=settings.control_hz,
            task_name=task_name,
            operator_id=user_id,
            image_size=image_size,
            env_factory=build_env,
        )
        loop.set_seed(seed)
        try:
            loop.start()
        except Exception as exc:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise SimulationInitFailed(str(exc)) from exc

        with self._lock:
            placeholder.control_loop = loop
            placeholder.last_seen_at = time.monotonic()
        return placeholder

    def get(self, session_id: str) -> TeleopSession:
        """Tra phiên theo id; ném KeyError nếu không tồn tại."""
        with self._lock:
            return self._sessions[session_id]

    def list_sessions(self) -> list[TeleopSession]:
        with self._lock:
            return list(self._sessions.values())

    def active_count(self) -> int:
        """Số phiên chưa đóng — dùng để kiểm tra trần đồng thời."""
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.state is not SessionState.CLOSED)

    # ----- điều khiển bản ghi (uỷ cho worker thread) -----

    def start_recording(self, session_id: str) -> str:
        """Bắt đầu ghi, trả về episode_id vừa tạo."""
        session = self.get(session_id)
        loop = self._require_loop(session)
        episode_id = loop.submit_command("start_recording")
        with self._lock:
            session.state = SessionState.RECORDING
            session.episode_id = episode_id
        return episode_id

    def stop_recording(self, session_id: str) -> dict[str, Any]:
        """Dừng ghi và chốt episode, trả về event `recording_saved`."""
        session = self.get(session_id)
        loop = self._require_loop(session)
        event = loop.submit_command("stop_recording", timeout=30.0)
        with self._lock:
            session.state = SessionState.IDLE
            session.episode_id = None
        return event

    def discard_recording(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        loop = self._require_loop(session)
        event = loop.submit_command("discard_recording", timeout=30.0)
        with self._lock:
            session.state = SessionState.IDLE
            session.episode_id = None
        return event

    def reset_scene(self, session_id: str, seed: int | None = None) -> dict[str, Any]:
        session = self.get(session_id)
        loop = self._require_loop(session)
        event = loop.submit_command("reset_scene", timeout=30.0, seed=seed)
        with self._lock:
            session.seed = seed
        return event

    def submit_input(self, session_id: str, input_delta: dict[str, float]) -> None:
        session = self.get(session_id)
        loop = self._require_loop(session)
        loop.submit_input(input_delta)
        session.last_seen_at = time.monotonic()

    def force_neutral(self, session_id: str) -> None:
        """Đưa robot về input trung tính — gọi ngay khi controller rớt."""
        session = self.get(session_id)
        loop = session.control_loop
        if loop is not None and loop.is_alive():
            try:
                loop.submit_command("neutral", timeout=2.0)
            except Exception:
                pass

    # ----- controller attach/detach -----

    def attach_controller(self, session_id: str) -> TeleopSession:
        """Đăng ký controller WebSocket duy nhất của phiên."""
        with self._lock:
            session = self._sessions[session_id]
            if session.state is SessionState.CLOSED:
                raise InvalidSessionState("Phiên đã đóng.")
            if session.controller_connected:
                raise ControllerAlreadyConnected(f"Phiên {session_id} đã có controller.")
            session.controller_connected = True
            session.disconnected_at = None
            session.last_seen_at = time.monotonic()
            return session

    def detach_controller(self, session_id: str) -> None:
        """Controller rớt: robot về neutral, bấm giờ ân hạn reconnect."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.controller_connected = False
            session.disconnected_at = time.monotonic()
        self.force_neutral(session_id)

    def touch(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_seen_at = time.monotonic()

    # ----- dọn dẹp -----

    def close(self, session_id: str) -> None:
        """Đóng phiên và giải phóng instance sim. Idempotent."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.state is SessionState.CLOSED:
                if session is not None:
                    self._sessions.pop(session_id, None)
                return
            session.state = SessionState.CLOSED

        loop = session.control_loop
        if loop is not None:
            try:
                # `stop()` tự chốt bản ghi dở (stop_reason="shutdown") rồi đóng
                # env trên chính worker thread trước khi thoát — đóng phiên
                # không được làm mất dữ liệu, và env không được đóng từ đây
                # (context OpenGL thuộc về thread kia).
                loop.stop(timeout=15.0)
            except TimeoutError:
                session.last_error = "WORKER_STOP_TIMEOUT"

        with self._lock:
            session.control_loop = None
            self._sessions.pop(session_id, None)

    def close_all(self) -> int:
        """Đóng mọi phiên — gọi lúc shutdown ứng dụng."""
        for session_id in [s.session_id for s in self.list_sessions()]:
            self.close(session_id)
        return 0

    def reap_stale(self, timeout_s: float | None = None) -> int:
        """Dọn phiên mất kết nối quá `timeout_s`, trả về số phiên đã dọn.

        Hai mốc thời gian khác nhau:

        - Phiên đang RECORDING mà controller rớt: chờ `reconnect_grace_s` rồi
          chốt phần đã ghi thành episode partial. Đây là dữ liệu operator đã
          bỏ công thu, không vứt đi vì rớt mạng.
        - Phiên IDLE không có controller: chờ `session_idle_timeout_s` rồi
          đóng hẳn, nếu không instance MuJoCo rò rỉ.
        """
        settings = get_settings()
        idle_timeout = timeout_s if timeout_s is not None else settings.session_idle_timeout_s
        grace = settings.reconnect_grace_s
        now = time.monotonic()
        reaped = 0

        for session in self.list_sessions():
            if session.state is SessionState.CLOSED or session.controller_connected:
                continue
            disconnected_at = session.disconnected_at
            if disconnected_at is None:
                disconnected_at = session.last_seen_at

            loop = session.control_loop
            recording = loop is not None and loop.session_state() == SessionState.RECORDING.value
            if recording and now - disconnected_at >= grace:
                # `recording` is only True when loop is not None (see above);
                # mypy doesn't carry that through the stored bool, so it's
                # re-asserted here.
                assert loop is not None
                try:
                    loop.submit_command("stop_recording", timeout=30.0, stop_reason="disconnect")
                except Exception:
                    pass
                with self._lock:
                    session.state = SessionState.IDLE
                    session.episode_id = None

            if now - disconnected_at >= idle_timeout:
                self.close(session.session_id)
                reaped += 1
        return reaped

    def _require_loop(self, session: TeleopSession) -> ControlLoop:
        loop = session.control_loop
        if loop is None or not loop.is_alive():
            raise InvalidSessionState("Phiên không còn vòng điều khiển đang chạy.")
        return loop


_manager: SessionManager | None = None
_manager_lock = threading.Lock()


def get_session_manager() -> SessionManager:
    """Manager dùng chung toàn ứng dụng (một registry cho mọi request)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager()
        return _manager
