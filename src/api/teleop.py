"""Teleoperation realtime qua WebSocket.

Trách nhiệm: đường điều khiển nóng của hệ thống. HTTP dùng để mở/đóng phiên,
còn WebSocket giữ vòng điều khiển: client gửi input, server trả observation.

Giao thức WebSocket trên `/teleop/ws/{session_id}`:

    client -> server (JSON)
        {"type": "input", "seq": n, "client_time_ms": t,
         "dx":..., "dy":..., "dz":..., "drx":..., "dry":..., "drz":..., "grip":...}
        {"type": "record",  "action": "start" | "stop" | "discard"}
        {"type": "scene",   "action": "reset", "seed": 43}
        {"type": "session", "action": "close"}
        {"type": "ping",    "client_time_ms": t}

    server -> client
        JSON   {"type": "obs", "seq": n, "qpos": [...], "ee_pose": [...], ...}
        JSON   {"type": "recording_started" | "recording_saved" | "recording_discarded", ...}
        JSON   {"type": "stats", ...} / {"type": "pong", ...}
        JSON   {"type": "error", "code": "...", "detail": "..."}
        BINARY 1 byte camera + uint32 input seq (big-endian) + JPEG
               b"\\x00" = camera chính (preview_camera)
               b"\\x01" = camera phụ   (preview_camera_secondary, vd cổ tay)

`seq` được phản chiếu trong cả `obs` và header frame để client đo round-trip
và input-to-pixel latency mà không cần đồng bộ đồng hồ hai phía.

Module này KHÔNG chứa logic vật lý. Mọi thao tác chạm MuJoCo đều đẩy sang
worker thread của `ControlLoop` qua `submit_command`, chạy trong executor để
không chặn event loop.

Endpoint:
    POST      /teleop/sessions              -> SessionResponse
    GET       /teleop/sessions/{id}         -> SessionDetailResponse
    GET       /teleop/sessions/{id}/stats   -> LoopStatsResponse
    DELETE    /teleop/sessions/{id}         -> 204
    WEBSOCKET /teleop/ws/{session_id}
"""

import asyncio
import contextlib
import shutil
import struct
import time
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.exc import IntegrityError

from src.config import get_settings
from src.core.control_loop import InvalidSessionState
from src.core.protocol import InvalidControlPayload, parse_command, parse_input
from src.core.session import (
    ControllerAlreadyConnected,
    SessionLimitReached,
    SessionManager,
    SimulationInitFailed,
    get_session_manager,
)
from src.models.db import Episode, Task, User, get_engine, session_factory
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.models.schemas import (
    ActionSpecResponse,
    CreateSessionRequest,
    LoopStatsResponse,
    SessionDetailResponse,
    SessionResponse,
    TaskResponse,
)
from src.services import storage
from src.services.security import current_user, decode_token
from src.sim import tasks as sim_tasks

router = APIRouter(prefix="/teleop", tags=["teleop"])


def _http_error(code: int, error_code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "detail": detail})


def _get_session_or_404(manager: SessionManager, session_id: str):
    try:
        return manager.get(session_id)
    except KeyError:
        raise _http_error(404, "SESSION_NOT_FOUND", f"Phiên '{session_id}' không tồn tại.") from None


def _can_access_session(session, user: User) -> bool:
    return session.user_id == user.id or UserRole(user.role) == UserRole.ADMIN


async def _ws_user(token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access" or not payload.get("sub"):
        return None
    async with session_factory(get_engine())() as db:
        user = await db.get(User, payload["sub"])
        if user is None or not user.is_active:
            return None
        return user


def _ensure_playback_aliases(episode_id: str) -> tuple[bool, int]:
    settings = get_settings()
    cameras = settings.camera_list()
    episode_dir = storage.episode_dir(episode_id)

    if cameras:
        source = storage.video_path(episode_id, cameras[0])
        target = episode_dir / storage.FRONT_FILENAME
        if source.exists() and source.resolve() != target.resolve():
            shutil.copyfile(source, target)

    # The wrist view is the tertiary preview: secondary is the overhead
    # `birdview`, which is not what `wrist.mp4` is supposed to hold.
    wrist_source = None
    wrist_camera = settings.preview_camera_tertiary.strip()
    if wrist_camera:
        wrist_source = storage.video_path(episode_id, wrist_camera)
    if (wrist_source is None or not wrist_source.exists()) and len(cameras) > 1:
        wrist_source = storage.video_path(episode_id, cameras[1])

    has_wrist = False
    if wrist_source is not None and wrist_source.exists():
        target = episode_dir / storage.WRIST_FILENAME
        if wrist_source.resolve() != target.resolve():
            shutil.copyfile(wrist_source, target)
        has_wrist = True

    return has_wrist, storage.dir_size_bytes(episode_dir)


async def _register_saved_episode(session_snapshot: dict[str, Any], event: dict[str, Any]) -> None:
    episode_id = str(event["episode_id"])
    task_name = str(session_snapshot["task_name"])
    operator_id = str(session_snapshot["operator_id"])
    task_success = bool(event.get("task_success"))
    duration_s = float(event.get("duration_s") or 0.0)
    num_steps = int(event.get("num_steps") or 0)
    has_wrist, size_bytes = _ensure_playback_aliases(episode_id)

    async with session_factory(get_engine())() as db:
        task = await db.get(Task, task_name)
        if task is None:
            try:
                spec = sim_tasks.get_task(task_name)
                task = Task(
                    name=spec.name,
                    description=spec.description,
                    instruction=spec.description,
                    hints=[],
                    action_dim=7,
                    max_steps=spec.max_steps,
                )
            except KeyError:
                task = Task(
                    name=task_name,
                    description=task_name,
                    instruction="",
                    hints=[],
                    action_dim=7,
                    max_steps=max(num_steps, 1),
                )
            db.add(task)
            await db.flush()

        if await db.get(Episode, episode_id) is not None:
            return

        db.add(
            Episode(
                id=episode_id,
                task_name=task_name,
                operator_id=operator_id,
                status=DemoStatus.LABELED,
                outcome=DemoOutcome.SUCCESS if task_success else DemoOutcome.FAILURE,
                note="Generated by web teleop",
                fps=float(get_settings().control_hz),
                num_frames=num_steps,
                duration_s=duration_s,
                size_bytes=size_bytes,
                trim_start_s=0.0,
                trim_end_s=duration_s if duration_s > 0 else None,
                has_wrist=has_wrist,
                has_trajectory=False,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


# ----- REST -----


@router.get("/tasks", response_model=list[TaskResponse])
async def list_sim_tasks(_user: User = Depends(current_user)) -> list[TaskResponse]:
    return [
        TaskResponse(
            name=spec.name,
            description=spec.description,
            instruction=spec.description,
            hints=[],
            action_dim=7,
            max_steps=spec.max_steps,
        )
        for spec in sim_tasks.list_tasks()
    ]


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    user: User = Depends(current_user),
) -> SessionResponse:
    """Mở phiên teleop mới và khởi động vòng điều khiển.

    Dựng MuJoCo mất vài giây nên chạy trong executor — chặn event loop ở đây
    sẽ làm đứng mọi WebSocket đang phục vụ phiên khác.
    """
    manager = get_session_manager()
    try:
        session = await asyncio.to_thread(
            manager.create,
            user_id=user.id,
            task_name=request.task_name,
            seed=request.seed,
            image_size=request.image_size,
        )
    except KeyError:
        raise _http_error(404, "TASK_NOT_FOUND", f"Task '{request.task_name}' chưa đăng ký.") from None
    except SessionLimitReached as exc:
        raise _http_error(409, "SESSION_LIMIT_REACHED", str(exc)) from None
    except SimulationInitFailed as exc:
        raise _http_error(500, "SIMULATION_INIT_FAILED", str(exc)) from None

    action_spec = None
    env = session.env
    if env is not None:
        # Biên thật từ controller đang chạy, không hard-code: nếu đổi
        # controller thì frontend phải clamp theo biên mới.
        spec = env.action_spec
        action_spec = ActionSpecResponse(dim=spec["dim"], low=spec["low"], high=spec["high"])

    return SessionResponse(
        session_id=session.session_id,
        task_name=session.task_name,
        state=session.snapshot()["state"],
        ws_url=f"/api/v1/teleop/ws/{session.session_id}",
        action_spec=action_spec,
    )


@router.get("/sessions", response_model=list[SessionDetailResponse])
async def list_sessions(user: User = Depends(current_user)) -> list[SessionDetailResponse]:
    manager = get_session_manager()
    sessions = manager.list_sessions()
    if UserRole(user.role) != UserRole.ADMIN:
        sessions = [session for session in sessions if session.user_id == user.id]
    return [SessionDetailResponse(**s.snapshot()) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    user: User = Depends(current_user),
) -> SessionDetailResponse:
    manager = get_session_manager()
    session = _get_session_or_404(manager, session_id)
    if not _can_access_session(session, user):
        raise _http_error(403, "FORBIDDEN", "Không có quyền xem phiên này.")
    return SessionDetailResponse(**session.snapshot())


@router.get("/sessions/{session_id}/stats", response_model=LoopStatsResponse)
async def get_session_stats(
    session_id: str,
    user: User = Depends(current_user),
) -> LoopStatsResponse:
    manager = get_session_manager()
    session = _get_session_or_404(manager, session_id)
    if not _can_access_session(session, user):
        raise _http_error(403, "FORBIDDEN", "Không có quyền xem phiên này.")
    loop = session.control_loop
    if loop is None:
        raise _http_error(409, "INVALID_SESSION_STATE", "Phiên không có vòng điều khiển đang chạy.")
    return LoopStatsResponse(**loop.stats().as_dict())


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_session(
    session_id: str,
    user: User = Depends(current_user),
) -> None:
    """Đóng phiên. Idempotent: đóng phiên đã đóng vẫn trả 204."""
    manager = get_session_manager()
    with contextlib.suppress(KeyError):
        session = manager.get(session_id)
        if not _can_access_session(session, user):
            raise _http_error(403, "FORBIDDEN", "Không có quyền đóng phiên này.")
    await asyncio.to_thread(manager.close, session_id)


# ----- WebSocket -----


class _Controller:
    """Cầu nối giữa một WebSocket và worker thread của phiên.

    Ba dòng chảy chạy song song trên event loop: nhận message từ client, đẩy
    obs/stats theo `telemetry_hz`, và đẩy frame JPEG theo `stream_fps`. Tách
    ra để frame chậm không chặn obs và ngược lại.
    """

    def __init__(self, websocket: WebSocket, manager: SessionManager, session_id: str) -> None:
        self.ws = websocket
        self.manager = manager
        self.session_id = session_id
        self.settings = get_settings()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._closing = asyncio.Event()

    # --- gửi ---

    async def _send_json(self, payload: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send_json(payload)

    async def telemetry_pump(self) -> None:
        """Đẩy obs + event lifecycle + stats định kỳ."""
        loop = self.manager.get(self.session_id).control_loop
        period = 1.0 / self.settings.telemetry_hz
        stats_every = max(1, int(self.settings.telemetry_hz))  # ~1 Hz
        tick = 0

        while not self._closing.is_set():
            await asyncio.sleep(period)
            if loop is None or not loop.is_alive():
                await self._send_json(
                    {"type": "error", "code": "INVALID_SESSION_STATE", "detail": "Vòng điều khiển đã dừng."}
                )
                self._closing.set()
                return

            while not self._events.empty():
                event = self._events.get_nowait()
                if event.get("type") == "recording_saved":
                    with contextlib.suppress(Exception):
                        await _register_saved_episode(
                            self.manager.get(self.session_id).snapshot(),
                            event,
                        )
                await self._send_json(event)

            output = loop.latest_output()
            if output is not None:
                await self._send_json(output.as_message())

            tick += 1
            if tick % stats_every == 0:
                stats = loop.stats().as_dict()
                await self._send_json({"type": "stats", **stats})

    async def frame_pump(self) -> None:
        """Đẩy JPEG mới nhất; không có frame mới thì bỏ qua chu kỳ này.

        Định dạng khung binary: 1 byte camera + uint32 seq big-endian + JPEG.

            b"\\x00" + seq + jpeg -> camera chính
            b"\\x01" + seq + jpeg -> camera phụ

        Một byte header rẻ hơn nhiều so với bọc JSON + base64 (phình 33% trên
        đường nóng), mà vẫn cho client biết khung hình thuộc camera nào.
        """
        loop = self.manager.get(self.session_id).control_loop
        period = 1.0 / self.settings.stream_fps

        while not self._closing.is_set():
            await asyncio.sleep(period)
            if loop is None or not loop.is_alive():
                return
            primary_seq, primary, secondary_seq, secondary, tertiary_seq, tertiary = loop.take_frames()
            try:
                if primary is not None:
                    await self.ws.send_bytes(b"\x00" + struct.pack(">I", primary_seq & 0xFFFFFFFF) + primary)
                if secondary is not None:
                    await self.ws.send_bytes(b"\x01" + struct.pack(">I", secondary_seq & 0xFFFFFFFF) + secondary)
                if tertiary is not None:
                    await self.ws.send_bytes(b"\x02" + struct.pack(">I", tertiary_seq & 0xFFFFFFFF) + tertiary)
            except Exception:
                self._closing.set()
                return

    # --- nhận ---

    async def receive_loop(self) -> None:
        while not self._closing.is_set():
            try:
                message = await self.ws.receive_json()
            except WebSocketDisconnect:
                self._closing.set()
                return
            except Exception:
                # JSON hỏng: báo lỗi rồi phục vụ tiếp. Đóng socket vì một
                # message rác là phạt operator cho lỗi của mạng.
                await self._send_json(
                    {
                        "type": "error",
                        "code": "INVALID_CONTROL_PAYLOAD",
                        "detail": "Message không phải JSON hợp lệ.",
                    }
                )
                continue

            try:
                await self._dispatch(message)
            except InvalidControlPayload as exc:
                await self._send_json({"type": "error", "code": "INVALID_CONTROL_PAYLOAD", "detail": str(exc)})
            except InvalidSessionState as exc:
                await self._send_json({"type": "error", "code": "INVALID_SESSION_STATE", "detail": str(exc)})
            except KeyError:
                await self._send_json({"type": "error", "code": "SESSION_NOT_FOUND", "detail": self.session_id})
                self._closing.set()
                return
            except Exception as exc:  # không để một lệnh hỏng giết cả phiên
                await self._send_json({"type": "error", "code": "SIMULATION_STEP_FAILED", "detail": str(exc)})

    async def _dispatch(self, message: Any) -> None:
        if not isinstance(message, dict):
            raise InvalidControlPayload("Message phải là object JSON")

        if message.get("type") == "input":
            delta = parse_input(message)
            # Đường nóng: chỉ ghi mailbox, O(1), không chạm sim.
            self.manager.submit_input(self.session_id, delta)
            return

        kind, payload = parse_command(message)
        if kind == "ping":
            await self._send_json(
                {"type": "pong", "client_time_ms": payload.get("client_time_ms"), "server_time_ms": time.time() * 1000}
            )
            self.manager.touch(self.session_id)
            return

        if kind == "record_start":
            episode_id = await asyncio.to_thread(self.manager.start_recording, self.session_id)
            await self._send_json({"type": "recording_started", "episode_id": episode_id})
            return
        if kind == "record_stop":
            snapshot = self.manager.get(self.session_id).snapshot()
            event = await asyncio.to_thread(self.manager.stop_recording, self.session_id)
            await _register_saved_episode(snapshot, event)
            await self._send_json(event)
            return
        if kind == "record_discard":
            event = await asyncio.to_thread(self.manager.discard_recording, self.session_id)
            await self._send_json(event)
            return
        if kind == "scene_reset":
            event = await asyncio.to_thread(self.manager.reset_scene, self.session_id, payload.get("seed"))
            await self._send_json(event)
            return
        if kind == "session_close":
            self._closing.set()
            await asyncio.to_thread(self.manager.close, self.session_id)
            return

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self.receive_loop()),
            asyncio.create_task(self.telemetry_pump()),
            asyncio.create_task(self.frame_pump()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._closing.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/ws/{session_id}")
async def teleop_ws(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    """Kênh điều khiển của một phiên. MVP: một controller cho mỗi phiên."""
    manager = get_session_manager()
    await websocket.accept()

    try:
        session = manager.get(session_id)
    except KeyError:
        await websocket.send_json(
            {"type": "error", "code": "SESSION_NOT_FOUND", "detail": f"Phiên '{session_id}' không tồn tại."}
        )
        await websocket.close(code=4404)
        return

    user = await _ws_user(token)
    if user is None or not _can_access_session(session, user):
        await websocket.send_json({"type": "error", "code": "UNAUTHORIZED", "detail": "Không xác thực được WebSocket."})
        await websocket.close(code=4401)
        return

    try:
        manager.attach_controller(session_id)
    except ControllerAlreadyConnected as exc:
        await websocket.send_json({"type": "error", "code": "CONTROLLER_ALREADY_CONNECTED", "detail": str(exc)})
        await websocket.close(code=4409)
        return
    except InvalidSessionState as exc:
        await websocket.send_json({"type": "error", "code": "INVALID_SESSION_STATE", "detail": str(exc)})
        await websocket.close(code=4409)
        return

    controller = _Controller(websocket, manager, session_id)
    try:
        await controller.run()
    finally:
        # Rớt kết nối: robot dừng ngay, đồng hồ ân hạn bắt đầu chạy. Bản ghi
        # dở KHÔNG bị xoá ở đây — `reap_stale` chốt nó thành episode partial
        # sau `reconnect_grace_s` (xem `SessionManager.reap_stale`).
        manager.detach_controller(session_id)
        with contextlib.suppress(Exception):
            await websocket.close()
