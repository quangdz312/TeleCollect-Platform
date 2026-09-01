"""Băm mật khẩu, ký JWT, và dependency xác thực/phân quyền.

Trách nhiệm: nơi duy nhất biết `settings.jwt_secret`. Router `auth`/`users`
gọi vào đây; không module nào khác đọc secret trực tiếp.

Dùng bcrypt trực tiếp (không qua passlib — passlib đọc `bcrypt.__about__` và
vỡ với bcrypt>=4.1). bcrypt cắt input ở 72 byte nên độ dài mật khẩu được
validate 8–72 ký tự trước khi hash (ở tầng Pydantic schema và lại một lần ở
đây cho phòng thủ kép).
"""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.db import User, get_session
from src.models.enums import UserRole

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 72

TokenType = Literal["access", "refresh"]

MACHINE_TOKEN_TYPE = "machine"

ROLE_RANK: dict[UserRole, int] = {
    UserRole.OPERATOR: 1,
    UserRole.REVIEWER: 2,
    UserRole.ADMIN: 3,
}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)

#: Tài khoản mở sẵn cho người ngoài vào xem hệ thống thật.
#:
#: Nhận theo username chứ không theo một cột riêng trong bảng `users`: dự án
#: chưa dùng Alembic, `init_db` chỉ gọi `create_all`, nên thêm cột là buộc phải
#: dựng lại DB — không làm được với máy chủ đang chứa dữ liệu thật.
DEMO_USERNAME = "demo"

#: Phương thức chỉ đọc. Mọi phương thức còn lại đều đổi dữ liệu.
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_DEMO_READ_ONLY = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Tài khoản dùng thử chỉ xem được, không thay đổi được dữ liệu",
)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Không xác thực được",
    headers={"WWW-Authenticate": "Bearer"},
)


def hash_password(plain: str) -> str:
    """Băm mật khẩu để lưu vào CSDL."""
    if not (MIN_PASSWORD_LENGTH <= len(plain) <= MAX_PASSWORD_LENGTH):
        raise ValueError(
            f"Mật khẩu phải dài {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} ký tự"
        )
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """So khớp mật khẩu người dùng nhập với bản băm đã lưu."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _create_token(user_id: str, role: str, token_type: TokenType, expires_delta: timedelta) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_access_token(user_id: str, role: str) -> str:
    settings = get_settings()
    return _create_token(
        user_id, role, "access", timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(user_id: str, role: str) -> str:
    settings = get_settings()
    return _create_token(
        user_id, role, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str) -> dict[str, Any]:
    """Giải và kiểm tra JWT; ném `jwt.PyJWTError` nếu hết hạn hoặc chữ ký sai."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def _decode_typed_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Giải mã token và bắt buộc đúng `type` — dùng nhầm access/refresh cho nhau -> 401."""
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise _UNAUTHORIZED from exc
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise _UNAUTHORIZED
    return payload


async def get_user_for_refresh(token: str, session: AsyncSession) -> User:
    """Dùng bởi `POST /auth/refresh`: giải mã refresh token + load user còn active."""
    payload = _decode_typed_token(token, "refresh")
    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


async def _load_active_user(
    payload: dict[str, Any], session: AsyncSession, request: Request | None = None,
) -> User:
    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    # Chặn ở đây vì đây là cửa duy nhất mọi request có xác thực đi qua: chặn
    # theo phương thức thay vì liệt kê từng endpoint, nên endpoint ghi thêm về
    # sau được bảo vệ sẵn thay vì phải nhớ bổ sung vào một danh sách.
    if (
        user.username == DEMO_USERNAME
        and request is not None
        and request.method.upper() not in _READ_ONLY_METHODS
    ):
        raise _DEMO_READ_ONLY
    return user


async def current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Dependency FastAPI: giải mã access token -> load user thật từ DB theo `sub`.

    BẮT BUỘC query DB (không tin payload token) — user vừa bị soft-delete
    (`is_active=False`) phải mất quyền ngay lập tức, không đợi token hết hạn.
    """
    if not token:
        raise _UNAUTHORIZED
    payload = _decode_typed_token(token, "access")
    return await _load_active_user(payload, session, request)


async def current_user_allow_query_token(
    request: Request,
    token: str | None = Query(default=None, description="Chỉ dùng cho endpoint media"),
    header_token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Biến thể của `current_user` CHỈ dùng cho các endpoint tải file trực
    tiếp qua URL (`GET /demos/{id}/playback`, `GET /demos/{id}/thumbnail`,
    `GET /datasets/{id}/download`).

    Thẻ `<video src="...">`/`<img src="...">` của trình duyệt, hoặc một link
    tải file trực tiếp, KHÔNG gửi được header `Authorization`, nên nếu chỉ
    nhận Bearer token thì frontend buộc phải fetch nguyên file thành blob rồi
    mới dùng — với video mất luôn khả năng tua, với dataset zip mất luôn khả
    năng tải qua trình duyệt/thanh địa chỉ. Các endpoint này vì vậy chấp nhận
    thêm token qua query param `?token=`.

    ĐÁNH ĐỔI CÓ CHỦ Ý: token nằm trong URL sẽ lọt vào access log và lịch sử
    trình duyệt. Đây là quyết định chấp nhận được ở bản Core; giai đoạn sau
    nên đổi sang signed URL ngắn hạn. KHÔNG dùng dependency này cho bất kỳ
    endpoint nào khác ngoài các endpoint tải file kể trên.
    """
    resolved_token = header_token or token
    if not resolved_token:
        raise _UNAUTHORIZED
    payload = _decode_typed_token(resolved_token, "access")
    return await _load_active_user(payload, session, request)


def _machine_secret() -> str:
    """Khóa ký token máy. Rỗng thì dùng chung `jwt_secret`."""
    settings = get_settings()
    return settings.machine_token_secret or settings.jwt_secret


def create_machine_token(job_id: str, expires_in: timedelta) -> str:
    """Ký token cho MỘT training job chạy trên máy GPU thuê.

    Token này KHÔNG gắn với user nào và không có `sub`, nên `current_user` từ
    chối nó — máy thuê không mượn được quyền của người bấm Train. Chiều ngược
    lại cũng chặn: access token của user không có `job_id` nên
    `current_machine_job` từ chối.

    `expires_in` phải dài hơn thời gian train tối đa (`runpod_max_hours`):
    token chết giữa chừng thì máy GPU train xong nhưng không đẩy được
    checkpoint về, mất trắng cả lần chạy.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "job_id": job_id,
        "type": MACHINE_TOKEN_TYPE,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, _machine_secret(), algorithm="HS256")


def decode_machine_token(token: str) -> str:
    """Trả `job_id` trong token máy; ném 401 nếu sai chữ ký, hết hạn, hoặc sai loại."""
    try:
        payload = jwt.decode(token, _machine_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise _UNAUTHORIZED from exc
    job_id = payload.get("job_id")
    if payload.get("type") != MACHINE_TOKEN_TYPE or not job_id:
        raise _UNAUTHORIZED
    return str(job_id)


def current_machine_job(
    job_id: str,
    token: str | None = Depends(oauth2_scheme),
) -> str:
    """Dependency FastAPI cho ba endpoint mà máy GPU thuê gọi ngược về server:
    tải dataset, đẩy log, đẩy checkpoint.

    CHỈ dùng cho ba endpoint đó. Token máy không tra CSDL và không có user, nên
    gắn nhầm dependency này lên một endpoint người dùng sẽ bỏ qua toàn bộ kiểm
    tra vai trò.

    Token phải mang đúng `job_id` của đường dẫn: token của job A gọi sang job B
    bị từ chối, nên một máy thuê bị chiếm quyền cũng chỉ chạm được đúng job của
    nó.
    """
    if not token:
        raise _UNAUTHORIZED
    if decode_machine_token(token) != job_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token máy không khớp training job",
        )
    return job_id


def require_min_role(
    minimum: UserRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """So sánh theo `ROLE_RANK`, KHÔNG so khớp tên role — admin luôn thoả mọi
    `require_min_role` thấp hơn (kế thừa quyền)."""

    async def _dependency(user: User = Depends(current_user)) -> User:
        if ROLE_RANK[UserRole(user.role)] < ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Không đủ quyền"
            )
        return user

    return _dependency
