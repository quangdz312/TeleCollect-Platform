"""Xác thực và phân quyền.

Trách nhiệm: đăng ký, cấp JWT khi đăng nhập, làm mới token, xem/đổi mật khẩu
của chính mình. Dependency phân quyền dùng chung (`current_user`,
`require_min_role`) nằm ở `src/services/security.py`.

Ba vai trò, có kế thừa quyền: admin ⊇ reviewer ⊇ operator.

Endpoint:
    POST /auth/register      (username, password, display_name) -> UserResponse
                             Tài khoản tạo ra ở trạng thái chờ admin duyệt.
    POST /auth/login         (form: username, password)          -> TokenResponse
    POST /auth/refresh       (refresh_token)                     -> TokenResponse
    GET  /auth/me            ()                                  -> UserResponse
    PATCH /auth/me/password  (old_password, new_password)        -> UserResponse
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.db import User, get_session
from src.models.enums import UserRole
from src.models.schemas import (
    ChangePasswordRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from src.services.security import (
    create_access_token,
    create_refresh_token,
    current_user,
    get_user_for_refresh,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sai username hoặc mật khẩu",
    headers={"WWW-Authenticate": "Bearer"},
)


def _normalize_username(username: str) -> str:
    return username.strip().lower()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)) -> User:
    settings = get_settings()
    if not settings.allow_self_register:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tự đăng ký đang bị tắt"
        )

    username = _normalize_username(body.username)
    existing = await session.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username đã tồn tại")

    # Role luôn ép về operator — RegisterRequest không có field role nên client
    # không thể gửi lên, nhưng ghi rõ ở đây để không ai sau này "tiện tay" thêm field.
    #
    # `is_active=False`: tài khoản tự đăng ký chưa dùng được cho tới khi admin
    # duyệt trong trang Users. Mở đăng ký mà cho vào thẳng thì bất kỳ ai biết
    # địa chỉ đều xem được dữ liệu của cả nhóm.
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or username,
        role=UserRole.OPERATOR,
        is_active=False,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username đã tồn tại") from exc
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    username = _normalize_username(form.username)
    user = await session.scalar(select(User).where(User.username == username))

    # Sai username HOẶC sai password đều trả cùng thông báo — không tiết lộ
    # username nào tồn tại.
    if user is None or not verify_password(form.password, user.password_hash):
        raise _BAD_CREDENTIALS

    # Chỉ tới đây mới nói rõ tài khoản chưa được duyệt: mật khẩu đã đúng nên
    # người gọi vốn đã biết tài khoản này tồn tại, không lộ thêm gì. Nếu gộp
    # vào thông báo trên thì người vừa đăng ký tưởng mình gõ nhầm mật khẩu.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đang chờ admin duyệt",
        )

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = await get_user_for_refresh(body.refresh_token, session)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
    )


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me/password", response_model=UserResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mật khẩu cũ không đúng")

    user.password_hash = hash_password(body.new_password)
    await session.commit()
    await session.refresh(user)
    return user
