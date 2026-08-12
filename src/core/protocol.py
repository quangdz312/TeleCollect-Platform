"""Parser cho payload WebSocket teleop.

Vì sao không dùng Pydantic ở đây: `src/models/schemas.py` là hợp đồng REST,
còn đường này nhận ~30 message/giây/phiên. Validate bằng model Pydantic mỗi
tick tốn hơn hẳn vài phép so sánh số học, mà thứ cần kiểm chỉ là "có phải số
hữu hạn trong biên không".

Nguyên tắc: payload sai KHÔNG được làm chết socket hay worker thread. Mọi hàm
ở đây hoặc trả về giá trị đã làm sạch, hoặc ném `InvalidControlPayload` để
handler gửi event `error` rồi tiếp tục phục vụ.
"""

import math
from typing import Any

# Trục chuyển động của action 7-D (xem `kinematics.teleop_input_to_action`);
# `grip` tách riêng vì nó là trạng thái dính chứ không phải vận tốc.
MOTION_AXES = ("dx", "dy", "dz", "drx", "dry", "drz")

# Biên input của người dùng — độc lập với `action_spec` của env. Env clamp lần
# nữa ở control loop; ở đây chặn sớm để số rác không đi xa hơn.
_INPUT_LIMIT = 1.0


class InvalidControlPayload(ValueError):  # noqa: N818 - INVALID_CONTROL_PAYLOAD
    """Message không đúng giao thức — trả error code, không đóng socket."""


def _finite_float(value: Any, field: str) -> float:
    """Ép về float hữu hạn. NaN/Inf bị chặn ở đây.

    NaN lọt xuống MuJoCo làm hỏng trạng thái vật lý của cả phiên (mọi qpos
    thành NaN và không reset được), nên đây là kiểm tra bắt buộc chứ không
    phải phòng xa.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidControlPayload(f"Trường '{field}' phải là số, nhận {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidControlPayload(f"Trường '{field}' không hữu hạn ({value})")
    return number


def _clamp(value: float, limit: float = _INPUT_LIMIT) -> float:
    return min(max(value, -limit), limit)


def parse_input(message: dict[str, Any]) -> dict[str, float]:
    """Làm sạch một message `{"type": "input", ...}` thành input_delta.

    Trường thiếu coi như 0.0 (không chuyển động) thay vì lỗi: client gửi
    trạng thái bàn phím đầy đủ mỗi tick, thiếu trục nghĩa là trục đó không
    được bấm.
    """
    if not isinstance(message, dict):
        raise InvalidControlPayload("Message phải là object JSON")

    delta: dict[str, float] = {}
    for axis in MOTION_AXES:
        raw = message.get(axis, 0.0)
        delta[axis] = _clamp(_finite_float(raw, axis))

    # Gripper là công tắc hai trạng thái: bất kỳ giá trị dương nào cũng là
    # "kẹp", giữ đúng ngữ nghĩa của local UI (`_grip = -grip`).
    grip = _finite_float(message.get("grip", -1.0), "grip")
    delta["grip"] = 1.0 if grip > 0 else -1.0

    seq = message.get("seq", 0)
    delta["seq"] = float(int(_finite_float(seq, "seq")))

    client_time = message.get("client_time_ms")
    if client_time is not None:
        delta["client_time_ms"] = _finite_float(client_time, "client_time_ms")
    return delta


def parse_command(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Đọc message lifecycle, trả về (kind, payload) đã kiểm.

    `kind` là một trong: record_start, record_stop, record_discard,
    scene_reset, session_close, ping.
    """
    if not isinstance(message, dict):
        raise InvalidControlPayload("Message phải là object JSON")

    msg_type = message.get("type")
    if not isinstance(msg_type, str):
        raise InvalidControlPayload("Thiếu trường 'type'")

    if msg_type == "record":
        action = message.get("action")
        if action not in ("start", "stop", "discard"):
            raise InvalidControlPayload(f"record.action không hợp lệ: {action!r}")
        return f"record_{action}", {}

    if msg_type == "scene":
        if message.get("action") != "reset":
            raise InvalidControlPayload(f"scene.action không hợp lệ: {message.get('action')!r}")
        seed = message.get("seed")
        if seed is not None:
            seed = int(_finite_float(seed, "seed"))
        return "scene_reset", {"seed": seed}

    if msg_type == "session":
        if message.get("action") != "close":
            raise InvalidControlPayload(f"session.action không hợp lệ: {message.get('action')!r}")
        return "session_close", {}

    if msg_type == "ping":
        client_time = message.get("client_time_ms")
        payload = {"client_time_ms": _finite_float(client_time, "client_time_ms")} if client_time is not None else {}
        return "ping", payload

    raise InvalidControlPayload(f"Loại message không hỗ trợ: {msg_type!r}")
