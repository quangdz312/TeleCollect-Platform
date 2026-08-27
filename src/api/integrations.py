"""Kết nối tài khoản bên thứ ba của chính người đang đăng nhập.

Hiện chỉ có Weights & Biases. Key W&B là credential vào tài khoản riêng của
từng người — hạn mức, project riêng tư, quyền ghi run đều là của họ — nên mỗi
người tự nhập key của mình thay vì cả server dùng chung một key.

Ba ràng buộc định hình module này:

**Không endpoint nào trả về key.** Chỉ có `key_preview` (bốn ký tự cuối) để
người dùng nhận ra key nào đang lưu. Đọc lại được key qua API thì mã hoá ở tầng
lưu trữ thành vô nghĩa.

**Không ai đọc key của người khác**, kể cả admin. Không có tham số `user_id`;
mọi endpoint chỉ làm việc với `current_user`.

**Không tin key cho tới khi W&B xác nhận.** Có `POST /verify` để thử thật, vì
sai sót thường gặp là dán nhầm hoặc key đã bị thu hồi — không kiểm thì phải đợi
job chạy xong mới biết.

Endpoint:
    GET    /integrations/wandb          -> WandbSettingsResponse
    PUT    /integrations/wandb          -> WandbSettingsResponse
    DELETE /integrations/wandb          -> WandbSettingsResponse
    POST   /integrations/wandb/verify   -> WandbVerifyResponse
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db import User, UserIntegration, get_session
from src.models.schemas import (
    WandbSettingsRequest,
    WandbSettingsResponse,
    WandbVerifyResponse,
)
from src.services import secrets
from src.services.security import current_user

router = APIRouter(prefix="/integrations", tags=["integrations"])

#: W&B trả 401 khá nhanh; đủ dài cho mạng chậm, đủ ngắn để không treo request.
_VERIFY_TIMEOUT_S = 15


def _response(row: UserIntegration | None) -> WandbSettingsResponse:
    if row is None:
        return WandbSettingsResponse()
    return WandbSettingsResponse(
        configured=bool(row.wandb_api_key),
        key_preview=row.wandb_key_preview,
        entity=row.wandb_entity,
    )


@router.get("/wandb", response_model=WandbSettingsResponse)
async def get_wandb_settings(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WandbSettingsResponse:
    return _response(await session.get(UserIntegration, user.id))


@router.put("/wandb", response_model=WandbSettingsResponse)
async def save_wandb_settings(
    payload: WandbSettingsRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WandbSettingsResponse:
    """Lưu key và/hoặc entity.

    `api_key` bỏ trống thì giữ nguyên key cũ — để đổi mỗi entity mà không phải
    dán lại key, vì giao diện không thể hiển thị key ra để dán lại.
    """

    row = await session.get(UserIntegration, user.id)
    if row is None:
        row = UserIntegration(user_id=user.id)
        session.add(row)

    if payload.api_key is not None:
        key = payload.api_key.strip()
        if not key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="API key không được để trống",
            )
        row.wandb_api_key = secrets.encrypt(key)
        row.wandb_key_preview = secrets.preview(key)

    row.wandb_entity = payload.entity.strip()
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return _response(row)


@router.delete("/wandb", response_model=WandbSettingsResponse)
async def clear_wandb_settings(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WandbSettingsResponse:
    row = await session.get(UserIntegration, user.id)
    if row is not None:
        await session.delete(row)
        await session.commit()
    return WandbSettingsResponse()


def _check_key(key: str) -> tuple[bool, str, str]:
    """Hỏi W&B xem key có dùng được không. Chạy trong thread — gọi mạng đồng bộ."""

    try:
        import wandb
    except ImportError:
        return False, "Máy chủ này chưa cài thư viện wandb", ""
    try:
        api = wandb.Api(api_key=key)
        return True, "", str(api.default_entity or "")
    except Exception as exc:  # wandb ném nhiều loại lỗi khác nhau
        message = str(exc).strip() or exc.__class__.__name__
        return False, message[:200], ""


@router.post("/wandb/verify", response_model=WandbVerifyResponse)
async def verify_wandb_key(
    payload: WandbSettingsRequest | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WandbVerifyResponse:
    """Thử key với W&B thật.

    Ưu tiên key vừa gõ trong form (nếu có) để người dùng kiểm tra TRƯỚC khi
    lưu; không có thì thử key đã lưu.
    """

    key = (payload.api_key or "").strip() if payload else ""
    if not key:
        row = await session.get(UserIntegration, user.id)
        stored = secrets.decrypt(row.wandb_api_key) if row else None
        if not stored:
            return WandbVerifyResponse(
                ok=False,
                detail=(
                    "Chưa có API key nào được lưu"
                    if row is None or not row.wandb_api_key
                    # Key mã hoá bằng JWT_SECRET; xoay secret là không giải được nữa.
                    else "Không đọc được key đã lưu — nhập lại key"
                ),
            )
        key = stored

    try:
        ok, detail, entity = await asyncio.wait_for(
            asyncio.to_thread(_check_key, key), timeout=_VERIFY_TIMEOUT_S,
        )
    except TimeoutError:
        return WandbVerifyResponse(ok=False, detail="W&B không phản hồi kịp")
    return WandbVerifyResponse(ok=ok, detail=detail, entity=entity)


async def wandb_credentials(
    session: AsyncSession, user_id: str,
) -> tuple[str | None, str]:
    """Key và entity đã lưu của một người, dùng khi khởi chạy training job.

    Trả `(None, "")` nếu chưa cấu hình hoặc không giải mã được, để phía gọi tự
    quyết định — chứ không ném lỗi, vì training không bật W&B vẫn chạy được.
    """

    row = await session.get(UserIntegration, user_id)
    if row is None:
        return None, ""
    return secrets.decrypt(row.wandb_api_key), row.wandb_entity
