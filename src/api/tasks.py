"""Danh mục task demo.

Trách nhiệm: cho frontend biết có những task nào để chọn trước khi upload
demo, kèm thông tin cần để dựng giao diện (mô tả, hướng dẫn, gợi ý, số chiều
action). Đọc thẳng từ bảng `tasks` — bản Core không có registry sim đứng sau.

Endpoint:
    GET  /tasks              ()            -> list[TaskResponse]   (mọi user đã đăng nhập)
    GET  /tasks/{name}       (name: str)   -> TaskResponse          (404 nếu không có)
    GET  /tasks/{name}/stats (name: str)   -> TaskStatsResponse     (404 nếu task không có)
    POST /tasks               (admin)      -> TaskResponse          (409 nếu trùng name)
    PATCH /tasks/{name}       (admin)      -> TaskResponse          (400 nếu cố đổi name)

KHÔNG có DELETE /tasks trong bản Core: `episodes.task_name` tham chiếu tới
`tasks.name`, xoá sẽ làm hỏng dữ liệu demo đã có. Cần vô hiệu hoá task thì
thêm cột `is_active` + soft delete ở giai đoạn sau, không xoá cứng.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db import Episode, Task, User, get_session
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.models.schemas import (
    TaskCreateRequest,
    TaskResponse,
    TaskStatsResponse,
    TaskUpdateRequest,
)
from src.services.security import current_user, require_min_role

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _get_task_or_404(name: str, session: AsyncSession) -> Task:
    task = await session.get(Task, name)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy task")
    return task


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[Task]:
    result = await session.scalars(select(Task).order_by(Task.name))
    return list(result.all())


@router.get("/{name}", response_model=TaskResponse)
async def get_task(
    name: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> Task:
    return await _get_task_or_404(name, session)


@router.get("/{name}/stats", response_model=TaskStatsResponse)
async def get_task_stats(
    name: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> TaskStatsResponse:
    await _get_task_or_404(name, session)

    # Một query duy nhất, đếm bằng conditional aggregation (SUM(CASE WHEN...))
    # thay vì lặp COUNT(*) theo từng status/outcome -> tránh N+1.
    status_columns = [
        func.coalesce(
            func.sum(case((Episode.status == s, 1), else_=0)), 0
        ).label(f"status_{s.value}")
        for s in DemoStatus
    ]
    outcome_columns = [
        func.coalesce(
            func.sum(case((Episode.outcome == o, 1), else_=0)), 0
        ).label(f"outcome_{o.value}")
        for o in DemoOutcome
    ]
    stmt = select(
        func.count().label("total"), *status_columns, *outcome_columns
    ).where(Episode.task_name == name)
    row = (await session.execute(stmt)).one()

    total = row.total
    by_status = {s.value: getattr(row, f"status_{s.value}") for s in DemoStatus}
    by_outcome = {o.value: getattr(row, f"outcome_{o.value}") for o in DemoOutcome}
    approved_count = by_status[DemoStatus.APPROVED.value]
    success_count = by_outcome[DemoOutcome.SUCCESS.value]

    return TaskStatsResponse(
        task_name=name,
        total=total,
        by_status=by_status,
        by_outcome=by_outcome,
        approved_count=approved_count,
        success_count=success_count,
        success_rate=(success_count / total) if total else 0.0,
        approval_rate=(approved_count / total) if total else 0.0,
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreateRequest,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_min_role(UserRole.ADMIN)),
) -> Task:
    task = Task(
        name=body.name,
        description=body.description,
        instruction=body.instruction,
        hints=body.hints,
        action_dim=body.action_dim,
        max_steps=body.max_steps,
    )
    session.add(task)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task đã tồn tại") from exc
    await session.refresh(task)
    return task


@router.patch("/{name}", response_model=TaskResponse)
async def update_task(
    name: str,
    body: TaskUpdateRequest,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_min_role(UserRole.ADMIN)),
) -> Task:
    if body.name is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể đổi tên task — name là khoá chính và episodes.task_name đang tham chiếu tới",
        )

    task = await _get_task_or_404(name, session)

    if body.description is not None:
        task.description = body.description
    if body.instruction is not None:
        task.instruction = body.instruction
    if body.hints is not None:
        task.hints = body.hints
    if body.action_dim is not None:
        task.action_dim = body.action_dim
    if body.max_steps is not None:
        task.max_steps = body.max_steps

    await session.commit()
    await session.refresh(task)
    return task
