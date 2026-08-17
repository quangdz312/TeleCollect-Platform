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

Mô hình luồng: MuJoCo và render context không an toàn khi truy cập từ nhiều
luồng, nên vòng lặp chạy trong đúng một worker thread và thread đó là chủ sở
hữu duy nhất của `env` lẫn `recorder`. Handler WebSocket (chạy trên event loop
của FastAPI) không bao giờ chạm vào chúng: nó chỉ ghi vào mailbox input, đẩy
lệnh vào hàng đợi command, và đọc cache output. Vì vậy start/stop/discard/
reset đều phải đi qua `submit_command`, không được gọi thẳng trên env.

Command khác input ở chỗ *không* được ghi đè: mất một lệnh "stop" nghĩa là mất
cả episode, nên command đi qua hàng đợi FIFO còn input dùng mailbox depth 1.
"""

import math
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.config import get_settings
from src.core.recorder import EpisodeRecorder
from src.sim import kinematics, tasks

# Trần số mẫu latency giữ lại để tính p50/p95 — đủ dài cho vài chục giây ở
# 30 Hz, đủ ngắn để không phình bộ nhớ trong phiên chạy hàng giờ.
_LATENCY_WINDOW = 600

_NEUTRAL_MOTION = ("dx", "dy", "dz", "drx", "dry", "drz")


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

    p50_latency_ms: float = 0.0
    """Trễ trung vị — đọc cùng p95 để thấy phân bố chứ không chỉ đuôi."""

    jitter_rms_ms: float = 0.0
    """Sai lệch RMS giữa *khoảng cách hai tick liên tiếp* và chu kỳ danh nghĩa.

    Đây là số đo nhịp có đều không. Thời gian xử lý một tick nằm ở
    `p50/p95_latency_ms` — hai đại lượng khác nhau, đừng gộp.
    """

    control_hz_actual: float = 0.0
    """Nhịp thực đo được — so với `control_hz` để biết loop có giữ được nhịp không."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks,
            "dropped_frames": self.dropped_frames,
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "overruns": self.overruns,
            "jitter_rms_ms": round(self.jitter_rms_ms, 2),
            "control_hz_actual": round(self.control_hz_actual, 2),
        }


@dataclass
class LoopOutput:
    """Ảnh chụp trạng thái mới nhất mà WebSocket đọc để gửi về client.

    Đây là ranh giới giữa worker thread và event loop: worker ghi nguyên một
    object mới (thay vì sửa tại chỗ) nên phía đọc luôn thấy một lát cắt nhất
    quán mà không cần khoá.
    """

    seq: int
    sim_time: float
    qpos: list[float]
    qvel: list[float]
    ee_pose: list[float]
    gripper_closed: bool
    task_success: bool
    session_state: str
    episode_id: str | None
    recorded_steps: int

    def as_message(self) -> dict[str, Any]:
        return {
            "type": "obs",
            "seq": self.seq,
            "sim_time": round(self.sim_time, 4),
            "qpos": [round(v, 5) for v in self.qpos],
            "qvel": [round(v, 5) for v in self.qvel],
            "ee_pose": [round(v, 5) for v in self.ee_pose],
            "gripper_closed": self.gripper_closed,
            "task_success": self.task_success,
            "session_state": self.session_state,
            "episode_id": self.episode_id,
            "recorded_steps": self.recorded_steps,
        }


@dataclass
class _Command:
    """Một lệnh lifecycle chờ worker thread thực thi."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


class ControlLoop:
    """Vòng điều khiển của một phiên.

    Chữ ký dự kiến:
        def __init__(self, session_id: str, env: RobotEnv, control_hz: int) -> None
        async def run(self) -> None
        def submit_input(self, input_delta: dict[str, float]) -> None
        async def stop(self) -> None
        def stats(self) -> LoopStats

    Khác chữ ký gốc: `run()` không còn là coroutine. Vòng lặp phải sở hữu
    MuJoCo trên một thread riêng (xem docstring module), nên nó chạy bằng
    `start()`/`stop()` trên `threading.Thread`; giữ `run()` async sẽ kéo
    `env.step()` — thao tác blocking hàng chục ms — vào event loop của FastAPI
    và làm trễ mọi WebSocket khác.
    """

    def __init__(
        self,
        session_id: str,
        env: Any = None,
        control_hz: int = 30,
        task_name: str = "lift_cube",
        operator_id: str = "local",
        image_size: int = 480,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        env_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self.env = env
        self._env_factory = env_factory
        self._owns_env = env_factory is not None
        self.control_hz = control_hz
        self.task_name = task_name
        self.operator_id = operator_id
        self.image_size = image_size
        self._on_event = on_event

        settings = get_settings()
        self._period = 1.0 / control_hz
        self._input_timeout_s = settings.command_timeout_ms / 1000.0
        self._jpeg_quality = settings.jpeg_quality
        self._stream_period = 1.0 / settings.stream_fps
        self._preview_camera = settings.preview_camera
        self._preview_camera_secondary = settings.preview_camera_secondary
        self._preview_camera_tertiary = settings.preview_camera_tertiary
        # 0 = không giới hạn: operator tự quyết lúc nào dừng, giống local UI.
        # Trần cũ lấy từ `TaskSpec.max_steps` (500 bước ≈ 16 giây) làm episode
        # tự chốt giữa chừng khi thao tác dài hơn.
        self._max_record_steps = settings.max_record_steps

        # Ném KeyError ngay nếu task chưa đăng ký, thay vì để lỗi lộ ra giữa
        # phiên. Không còn dùng `max_steps` ở đây — xem `max_record_steps`.
        self._spec = tasks.get_task(task_name)

        # Mailbox depth 1: input mới ghi đè input chưa dùng.
        self._input_lock = threading.Lock()
        self._latest_input: dict[str, float] | None = None
        self._latest_input_at = 0.0
        self._latest_seq = 0
        self._last_applied_seq = 0

        # Gripper là trạng thái *dính*: khi input stale, tay gắp phải giữ
        # nguyên (đang kẹp vật thì không được nhả) trong khi chuyển động về 0.
        self._grip = -1.0

        self._commands: queue.Queue[_Command] = queue.Queue()

        self._state_lock = threading.Lock()
        self._output: LoopOutput | None = None
        self._frame: bytes | None = None
        self._frame_secondary: bytes | None = None
        self._frame_tertiary: bytes | None = None
        self._frame_seq = 0
        self._session_state = "idle"
        self._episode_id: str | None = None
        self._seed: int | None = None
        self._last_error: str | None = None

        self._recorder: EpisodeRecorder | None = None
        self._recording_started_at = 0.0

        self._stats_lock = threading.Lock()
        self._latencies: list[float] = []
        self._jitters: list[float] = []
        self._ticks = 0
        self._dropped_frames = 0
        self._overruns = 0
        self._loop_started_at = 0.0
        self._loop_elapsed = 0.0
        self._last_tick_at: float | None = None

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_stream_at = 0.0
        self._ready = threading.Event()
        self._init_error: Exception | None = None

    # ----- vòng đời thread -----

    def start(self, timeout: float = 120.0) -> None:
        """Khởi động worker thread và chờ nó dựng xong env; idempotent.

        Chặn tới khi env sẵn sàng vì nơi gọi cần biết ngay sim có dựng được
        không (`SIMULATION_INIT_FAILED` phải trả về ở REST, không phải lộ ra
        sau đó dưới dạng phiên chết).
        """
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever,
            name=f"control-loop-{self.session_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self._stop_event.set()
            raise TimeoutError("Vòng điều khiển không khởi động kịp")
        if self._init_error is not None:
            self._thread = None
            raise self._init_error

    def run(self) -> None:
        """Chạy vòng lặp tới khi `stop()` được gọi (blocking, dùng trong test)."""
        self._run_forever()

    def stop(self, timeout: float = 5.0) -> None:
        """Dừng vòng lặp, chốt bản ghi dở nếu có, và join thread.

        Bản ghi dở được finalize chứ không bỏ: operator có thể đã thao tác
        xong phần quan trọng, mất kết nối không phải lý do để xoá dữ liệu.
        Xem `stop_reason` trong meta.json để phân biệt với episode bấm dừng
        bình thường.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise TimeoutError("WORKER_STOP_TIMEOUT")
        self._thread = None

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ----- API cho event loop (thread-safe, O(1)) -----

    def submit_input(self, input_delta: dict[str, float]) -> None:
        """Nhận input mới nhất từ WebSocket, ghi đè input chưa dùng."""
        seq = int(input_delta.get("seq", 0))
        with self._input_lock:
            # Gói UDP-style tới sai thứ tự: input cũ hơn cái đang giữ là thông
            # tin đã lỗi thời, áp vào chỉ làm robot giật ngược.
            if seq and seq < self._latest_seq:
                return
            self._latest_input = dict(input_delta)
            self._latest_input_at = time.monotonic()
            if seq:
                self._latest_seq = seq

    def submit_command(self, kind: str, timeout: float = 10.0, **payload: Any) -> Any:
        """Xếp một lệnh lifecycle cho worker thread và chờ kết quả.

        Chặn tới khi worker xử lý xong vì nơi gọi cần biết episode_id / lỗi.
        Chạy trong executor khi gọi từ event loop (xem `src/api/teleop.py`).
        """
        if not self.is_alive():
            raise RuntimeError("SESSION_NOT_RUNNING")
        command = _Command(kind=kind, payload=payload)
        self._commands.put(command)
        if not command.done.wait(timeout=timeout):
            raise TimeoutError(f"Lệnh '{kind}' quá hạn {timeout}s")
        if command.error is not None:
            raise command.error
        return command.result

    def latest_output(self) -> LoopOutput | None:
        with self._state_lock:
            return self._output

    def take_frame(self) -> tuple[int, bytes] | None:
        """Lấy frame JPEG camera chính mới nhất và xoá khỏi cache.

        Cache depth 1: nếu client chậm hơn sim, frame chưa gửi bị frame mới
        ghi đè (đếm vào `dropped_frames`). Stream xem trực tiếp chấp nhận mất
        frame; video ghi xuống dataset thì không (nó đi đường `recorder`).
        """
        with self._state_lock:
            frame, self._frame = self._frame, None
            seq = self._frame_seq
        return None if frame is None else (seq, frame)

    def take_frames(self) -> tuple[int, bytes | None, bytes | None, bytes | None]:
        """Lấy cả ba camera preview trong một lần khoá.

        Lấy chung một lần để ba khung hình trên browser thuộc cùng một tick —
        ba lời gọi riêng có thể rơi vào ba tick khác nhau và khiến ảnh cổ tay
        lệch pha với ảnh tổng thể.
        """
        with self._state_lock:
            primary, self._frame = self._frame, None
            secondary, self._frame_secondary = self._frame_secondary, None
            tertiary, self._frame_tertiary = self._frame_tertiary, None
            seq = self._frame_seq
        return seq, primary, secondary, tertiary

    def session_state(self) -> str:
        with self._state_lock:
            return self._session_state

    def episode_id(self) -> str | None:
        with self._state_lock:
            return self._episode_id

    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def stats(self) -> LoopStats:
        """Trả về số đo độ trễ của phiên."""
        with self._stats_lock:
            latencies = sorted(self._latencies)
            jitters = list(self._jitters)
            ticks = self._ticks
            elapsed = self._loop_elapsed
            return LoopStats(
                ticks=ticks,
                dropped_frames=self._dropped_frames,
                p50_latency_ms=_percentile(latencies, 0.50),
                p95_latency_ms=_percentile(latencies, 0.95),
                overruns=self._overruns,
                jitter_rms_ms=_rms(jitters),
                control_hz_actual=(ticks / elapsed) if elapsed > 0 else 0.0,
            )

    # ----- worker thread -----

    def _run_forever(self) -> None:
        # Dựng env NGAY TRONG worker thread, không nhận từ ngoài. Context render
        # offscreen của MuJoCo (EGL) gắn với thread tạo ra nó: tạo ở thread A rồi
        # `eglMakeCurrent` ở thread B trả về EGL_BAD_ACCESS và mọi `step()` ném lỗi.
        # Vì vậy "một owner thread cho mỗi sim" phải tính từ lúc khởi tạo.
        try:
            if self._env_factory is not None:
                self.env = self._env_factory()
        except Exception as exc:
            self._init_error = exc
            self._ready.set()
            return
        self._ready.set()

        self._loop_started_at = time.perf_counter()
        deadline = time.perf_counter()
        try:
            while not self._stop_event.is_set():
                tick_start = time.perf_counter()
                self._drain_commands()
                self._tick(tick_start)

                deadline += self._period
                now = time.perf_counter()
                if now < deadline:
                    self._stop_event.wait(deadline - now)
                else:
                    # Quá hạn: resync mốc thay vì chạy bù. Chạy bù dồn nhiều
                    # tick liên tiếp làm robot nhảy vọt và jitter càng tệ.
                    with self._stats_lock:
                        self._overruns += 1
                    deadline = now
        finally:
            self._shutdown_recorder()
            # Đóng env ở đây chứ không ở SessionManager: giải phóng context
            # OpenGL cũng phải diễn ra trên thread đã tạo ra nó.
            if self._owns_env and self.env is not None:
                try:
                    self.env.close()
                except Exception:
                    pass
                self.env = None

    def _tick(self, tick_start: float) -> None:
        input_delta, seq = self._read_input()
        action = kinematics.teleop_input_to_action(input_delta, self._current_qpos())
        action = self._clamp_action(action)

        try:
            obs = self.env.step(action)
        except Exception as exc:  # sim hỏng: dừng loop, giữ nguyên dữ liệu đã ghi
            self._set_error("SIMULATION_STEP_FAILED", str(exc))
            self._stop_event.set()
            return

        recorded_steps = 0
        if self._recorder is not None:
            # Bản ghi phải liên tục: append trước mọi việc có thể bỏ qua được
            # (stream frame, telemetry) để lỗi ở tầng hiển thị không làm thủng
            # dataset.
            self._recorder.append(obs, action)
            recorded_steps = self._recorder.num_steps

        try:
            success = self.env.is_success()
        except Exception:
            success = False

        self._publish(obs, seq, success, recorded_steps)
        self._maybe_publish_frame(obs, seq, tick_start)

        if self._max_record_steps > 0 and self._recorder is not None and recorded_steps >= self._max_record_steps:
            self._finalize_recording(save=True, stop_reason="max_steps")

        elapsed_ms = (time.perf_counter() - tick_start) * 1000.0

        # Jitter = sai lệch của *khoảng cách giữa hai tick* so với chu kỳ danh
        # định, không phải thời gian xử lý một tick. Đo theo thời gian xử lý sẽ
        # báo jitter ≈ (period − thời gian xử lý) ngay cả khi loop chạy đúng
        # nhịp tuyệt đối — càng chạy nhanh lại càng bị chấm là tệ.
        interval_ms: float | None = None
        if self._last_tick_at is not None:
            interval_ms = (tick_start - self._last_tick_at) * 1000.0
        self._last_tick_at = tick_start

        with self._stats_lock:
            self._ticks += 1
            self._loop_elapsed = time.perf_counter() - self._loop_started_at
            self._latencies.append(elapsed_ms)
            if len(self._latencies) > _LATENCY_WINDOW:
                del self._latencies[: len(self._latencies) - _LATENCY_WINDOW]
            if interval_ms is not None:
                self._jitters.append(interval_ms - self._period * 1000.0)
                if len(self._jitters) > _LATENCY_WINDOW:
                    del self._jitters[: len(self._jitters) - _LATENCY_WINDOW]

    def _read_input(self) -> tuple[dict[str, float], int]:
        """Lấy input mới nhất; quá hạn thì về neutral nhưng giữ trạng thái gắp."""
        now = time.monotonic()
        with self._input_lock:
            latest = self._latest_input
            age = now - self._latest_input_at
            seq = self._latest_seq

        if latest is None or age > self._input_timeout_s:
            return {"grip": self._grip}, seq

        self._grip = float(latest.get("grip", self._grip))
        delta = {axis: float(latest.get(axis, 0.0)) for axis in _NEUTRAL_MOTION}
        delta["grip"] = self._grip
        self._last_applied_seq = seq
        return delta, seq

    def _current_qpos(self) -> list[float]:
        output = self._output
        return output.qpos if output is not None else []

    def _clamp_action(self, action: list[float]) -> list[float]:
        spec = self.env.action_spec
        low, high = spec["low"], spec["high"]
        return [min(max(value, low[i]), high[i]) for i, value in enumerate(action[: spec["dim"]])]

    def _publish(self, obs: Any, seq: int, success: bool, recorded_steps: int) -> None:
        with self._state_lock:
            self._output = LoopOutput(
                seq=seq,
                sim_time=obs.t,
                qpos=list(obs.qpos),
                qvel=list(obs.qvel),
                ee_pose=list(obs.ee_pose),
                gripper_closed=self._grip > 0,
                task_success=success,
                session_state=self._session_state,
                episode_id=self._episode_id,
                recorded_steps=recorded_steps,
            )

    def _maybe_publish_frame(self, obs: Any, seq: int, tick_start: float) -> None:
        """Encode JPEG theo nhịp `stream_fps`, không phải mỗi tick.

        `obs.images` đã có sẵn từ `env.step()` (xem `RobotEnv._observation`),
        nên ở đây chỉ encode — không render lại, không đụng vào render context
        ngoài luồng.
        """
        if tick_start - self._last_stream_at < self._stream_period:
            return

        try:
            # Renderer xem trực tiếp (độ phân giải cao) nếu phiên có bật; nếu
            # không thì tái dùng chính ảnh đã render cho dataset — không render
            # lại lần nữa chỉ để hiển thị.
            frame = self.env.render_preview() if hasattr(self.env, "render_preview") else None
            if frame is not None:
                renderer = self.env._preview_renderer
            else:
                # Không có renderer xem riêng: dùng lại ảnh đã render cho
                # dataset. `preview_camera` có thể là camera KHÔNG ghi (vd
                # `frontview`), nên lùi về camera ghi đầu tiên thay vì bỏ hẳn
                # frame — thà xem góc khác còn hơn màn hình đen.
                frame = obs.images.get(self._preview_camera)
                if frame is None:
                    frame = next(iter(obs.images.values()), None)
                if frame is None:
                    return
                renderer = self.env._renderer
            jpeg = renderer.encode_jpeg(frame, quality=self._jpeg_quality)

            # Hai camera phụ (tổng quan trên cao + cổ tay) lấy thẳng ảnh đã
            # render cho dataset — khung nhỏ nên không cần độ phân giải cao, và
            # không tốn thêm lần render nào mỗi tick.
            def side(name: str) -> bytes | None:
                if not name:
                    return None
                raw = obs.images.get(name)
                if raw is None:
                    return None
                return self.env._renderer.encode_jpeg(raw, quality=self._jpeg_quality)

            jpeg_secondary = side(self._preview_camera_secondary)
            jpeg_tertiary = side(self._preview_camera_tertiary)
        except Exception as exc:
            self._set_error("RENDER_FAILED", str(exc))
            return

        self._last_stream_at = tick_start
        with self._state_lock:
            if self._frame is not None:
                # Client chưa kịp lấy frame trước — bỏ nó, giữ frame mới nhất.
                with self._stats_lock:
                    self._dropped_frames += 1
            self._frame = jpeg
            self._frame_secondary = jpeg_secondary
            self._frame_tertiary = jpeg_tertiary
            self._frame_seq = seq

    # ----- command handlers (chỉ chạy trên worker thread) -----

    def _drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                command.result = self._handle_command(command)
            except Exception as exc:
                command.error = exc
            finally:
                command.done.set()

    def _handle_command(self, command: _Command) -> Any:
        if command.kind == "start_recording":
            return self._start_recording()
        if command.kind == "stop_recording":
            return self._finalize_recording(
                save=True,
                stop_reason=command.payload.get("stop_reason", "operator"),
            )
        if command.kind == "discard_recording":
            return self._discard_recording()
        if command.kind == "reset_scene":
            return self._reset_scene(command.payload.get("seed"))
        if command.kind == "neutral":
            return self._force_neutral()
        raise ValueError(f"Lệnh không hợp lệ: {command.kind}")

    def _start_recording(self) -> str:
        if self._recorder is not None:
            raise InvalidSessionState("Đang ghi rồi — không mở recorder thứ hai.")
        episode_id = f"{self.task_name}_{uuid.uuid4().hex[:8]}"
        self._recorder = EpisodeRecorder(
            episode_id=episode_id,
            task_name=self.task_name,
            operator_id=self.operator_id,
            control_hz=self.control_hz,
            image_size=self.image_size,
        )
        self._recording_started_at = time.monotonic()
        with self._state_lock:
            self._episode_id = episode_id
            self._session_state = "recording"
        self._emit({"type": "recording_started", "episode_id": episode_id})
        return episode_id

    def _finalize_recording(self, save: bool, stop_reason: str) -> dict[str, Any]:
        if self._recorder is None:
            raise InvalidSessionState("Không có bản ghi nào đang chạy.")
        if not save:
            return self._discard_recording()

        recorder = self._recorder
        try:
            success = self.env.is_success()
        except Exception:
            success = False

        stats = self.stats()
        interrupted = stop_reason in ("disconnect", "shutdown")
        try:
            meta = recorder.finalize(
                extra_meta={
                    "seed": self._seed,
                    "task_success": success,
                    "partial": interrupted,
                    "interrupted": interrupted,
                    "stop_reason": stop_reason,
                    "p50_server_latency_ms": stats.p50_latency_ms,
                    "p95_server_latency_ms": stats.p95_latency_ms,
                    "jitter_rms_ms": stats.jitter_rms_ms,
                    "overruns": stats.overruns,
                    "dropped_stream_frames": stats.dropped_frames,
                }
            )
        except Exception as exc:
            self._recorder = None
            with self._state_lock:
                self._session_state = "idle"
                self._episode_id = None
            self._set_error("RECORDING_FINALIZE_FAILED", str(exc))
            raise

        self._recorder = None
        with self._state_lock:
            self._session_state = "idle"
            self._episode_id = None

        event = {
            "type": "recording_saved",
            "episode_id": meta.episode_id,
            "num_steps": meta.num_steps,
            "duration_s": round(meta.duration_s, 3),
            "task_success": success,
            "partial": interrupted,
            "stop_reason": stop_reason,
        }
        self._emit(event)
        return event

    def _discard_recording(self) -> dict[str, Any]:
        if self._recorder is None:
            raise InvalidSessionState("Không có bản ghi nào đang chạy.")
        episode_id = self._recorder.episode_id
        self._recorder.abort()
        self._recorder = None
        with self._state_lock:
            self._session_state = "idle"
            self._episode_id = None
        event = {"type": "recording_discarded", "episode_id": episode_id}
        self._emit(event)
        return event

    def _reset_scene(self, seed: int | None = None) -> dict[str, Any]:
        if self._recorder is not None:
            raise InvalidSessionState("Không reset scene khi đang ghi.")
        self.env.reset(seed=seed)
        self._seed = seed
        self._grip = -1.0
        with self._input_lock:
            self._latest_input = None
        return {"type": "scene_reset", "seed": seed}

    def _force_neutral(self) -> None:
        """Xoá input đang giữ — robot dừng chuyển động ngay tick sau."""
        with self._input_lock:
            self._latest_input = None
            self._latest_input_at = 0.0

    def _shutdown_recorder(self) -> None:
        """Chốt bản ghi dở khi worker thread kết thúc."""
        if self._recorder is None:
            return
        try:
            self._finalize_recording(save=True, stop_reason="shutdown")
        except Exception:
            # Không để lỗi finalize nuốt mất việc dọn thread; artifact dở nằm
            # lại trên đĩa còn hơn worker treo.
            self._recorder = None

    # ----- tiện ích -----

    def _emit(self, event: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                pass

    def _set_error(self, code: str, detail: str) -> None:
        with self._state_lock:
            self._last_error = f"{code}: {detail}"
        self._emit({"type": "error", "code": code, "detail": detail})

    def set_seed(self, seed: int | None) -> None:
        self._seed = seed


class InvalidSessionState(RuntimeError):  # noqa: N818 - tên khớp error code INVALID_SESSION_STATE của đặc tả
    """Lệnh không hợp lệ với trạng thái hiện tại của phiên (map sang HTTP 409)."""


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(math.ceil(fraction * len(sorted_values))) - 1)
    return sorted_values[max(0, index)]


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))
