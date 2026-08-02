"""Teleoperation realtime qua WebSocket.

Trách nhiệm: đường điều khiển nóng của hệ thống. HTTP dùng để mở/đóng phiên,
còn WebSocket giữ vòng điều khiển: client gửi input, server trả observation.

Giao thức WebSocket dự kiến trên `/teleop/ws/{session_id}`:

    client -> server
        {"type": "input",  "dx":..., "dy":..., "dz":..., "grip":..., "seq": n}
        {"type": "record", "action": "start" | "stop"}

    server -> client
        {"type": "obs",    "t":..., "qpos":[...], "ee_pose":[...], "seq": n}
        {"type": "frame",  "camera": "...", "jpeg": <binary>}
        {"type": "error",  "detail": "..."}

`seq` được phản chiếu lại trong `obs` để client đo round-trip latency mà
không cần đồng bộ đồng hồ hai phía.

Endpoint dự kiến:
    POST      /teleop/sessions              (task_name: str) -> SessionResponse
    DELETE    /teleop/sessions/{id}         (id: str)        -> None
    GET       /teleop/sessions/{id}/stats   (id: str)        -> LoopStatsResponse
    WEBSOCKET /teleop/ws/{session_id}
"""

from fastapi import APIRouter

router = APIRouter(prefix="/teleop", tags=["teleop"])
