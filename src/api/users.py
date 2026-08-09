"""Quản lý user — chỉ admin.

Trách nhiệm: CRUD tài khoản/vai trò. Toàn bộ router yêu cầu
`require_min_role(UserRole.ADMIN)` — admin kế thừa mọi quyền reviewer/operator
nhưng ở đây đi ngược lại thì không: reviewer/operator không được vào đây.

Chốt an toàn: admin không được tự hạ role hoặc tự vô hiệu hoá chính mình,
tránh trường hợp khoá sạch tài khoản admin mà hệ thống không cứu được (không
có ai còn quyền mở lại).

Endpoint:
    GET    /users            (?role=&is_active=) -> list[UserResponse]
    POST   /users             (admin chọn role)   -> UserResponse
    PATCH  /users/{id}                            -> UserResponse
    DELETE /users/{id}        (soft delete)        -> UserResponse
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db import User, get_session
from src.models.enums import UserRole
from src.models.schemas import UserCreateRequest, UserResponse, UserUpdateRequest
from src.services.security import hash_password, require_min_role

router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_min_role(UserRole.ADMIN))]
)


def _self_lockout_guard(target: User, current_admin: User, body: UserUpdateRequest) -> None:
    """Chặn admin tự hạ role hoặc tự tắt is_active của chính mình."""
    if target.id != current_admin.id:
        return
    if body.role is not None and body.role != UserRole(target.role):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự hạ role của chính mình",
        )
    if body.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự vô hiệu hoá chính mình",
        )


@router.get("", response_model=list[UserResponse])
async def list_users(
    role: UserRole | None = None,
    is_active: bool | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    query = select(User)
    if role is not None:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    result = await session.scalars(query)
    return list(result.all())


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest, session: AsyncSession = Depends(get_session)
) -> User:
    username = body.username.strip().lower()
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or username,
        role=body.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username đã tồn tại") from exc
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    current_admin: User = Depends(require_min_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy user")

    _self_lockout_guard(target, current_admin, body)

    if body.display_name is not None:
        target.display_name = body.display_name
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.password is not None:
        target.password_hash = hash_password(body.password)

    await session.commit()
    await session.refresh(target)
    return target


@router.delete("/{user_id}", response_model=UserResponse)
async def delete_user(
    user_id: str,
    current_admin: User = Depends(require_min_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Soft delete (`is_active=False`) — không xoá cứng vì `episodes` còn tham
    chiếu tới `operator_id`/`reviewer_id`."""
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy user")

    if target.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự vô hiệu hoá chính mình",
        )

    target.is_active = False
    await session.commit()
    await session.refresh(target)
    return target
