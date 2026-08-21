"""Đóng băng demo `approved` thành dataset zip — snapshot dữ liệu cho huấn luyện.

Trách nhiệm: gom một tập demo `approved` (lọc theo task/outcome) thành file
zip bất biến. Dataset KHÔNG cập nhật lại sau khi build xong — thu thêm demo
thì tạo dataset MỚI (tên khác hoặc `overwrite`), không sửa dataset cũ, để
kết quả huấn luyện giữa các lần so sánh được với nhau.

Endpoint:
    POST   /datasets              (name, task_names, include_failures, overwrite)
                                                          -> 202 DatasetResponse (status=building)
    GET    /datasets              (page, page_size)       -> PaginatedResponse[DatasetResponse]
    GET    /datasets/{id}                                 -> DatasetDetailResponse
    GET    /datasets/{id}/download                        -> application/zip
    DELETE /datasets/{id}                                 -> 204

Việc zip hoá thật sự (nặng IO/CPU) chạy NỀN qua `BackgroundTasks` sau khi
response 202 đã trả — xem `src/services/dataset_builder.py` để biết các bẫy
bắt buộc tránh (session mới, không giữ session lúc zip, chạy trong thread).

`GET /datasets/{id}/download` dùng `current_user_allow_query_token` — CÙNG
cơ chế `?token=` như `GET /demos/{id}/playback` (client tải file trực tiếp
qua link, không phải lúc nào cũng gắn được header `Authorization`).
"""

import logging
import math
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.models.db import Dataset, DatasetEpisode, Episode, User, get_session, session_factory
from src.models.enums import DatasetStatus, DemoOutcome, DemoStatus, UserRole
from src.models.schemas import (
    DatasetCreateRequest,
    DatasetDetailResponse,
    DatasetResponse,
    DemoResponse,
    PaginatedResponse,
)
from src.services import storage
from src.services.dataset_builder import build_dataset
from src.services.robomimic_dataset_builder import build_robomimic_dataset
from src.services.security import current_user, current_user_allow_query_token, require_min_role
from src.services.streaming import stream_file_range

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasets", tags=["datasets"])


async def _get_dataset_or_404(dataset_id: str, session: AsyncSession) -> Dataset:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy dataset")
    return dataset


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_dataset(
    body: DatasetCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> DatasetResponse:
    """Chọn episode TRƯỚC khi đụng tới dataset trùng tên — nếu không có demo
    nào khớp thì trả 422 mà KHÔNG xoá mất dataset cũ (trường hợp overwrite).
    """
    if body.format == "robomimic":
        if len(body.task_names) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="RoboMimic BC cần đúng một task/environment cho mỗi dataset",
            )
        from src.api.labeling import workspace

        space = workspace()
        labels = space.labels_by_id()
        scripted = []
        for score in space.scores():
            label = labels.get(str(score["episode_id"]))
            if not label or label["human_decision"] != "approved":
                continue
            if score.get("task") != body.task_names[0]:
                continue
            if not body.include_failures and score.get("recorded_success") is not True:
                continue
            scripted.append({
                **label,
                "decision": label["human_decision"],
                "source_path": str(space.resolve_source(str(score["source"]))),
                "demo": score["demo"],
            })
        if not scripted:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Không có scripted episode approved cho task đã chọn",
            )
    else:
        scripted = []

    query = select(Episode).where(Episode.status == DemoStatus.APPROVED)
    if body.task_names:
        query = query.where(Episode.task_name.in_(body.task_names))
    if not body.include_failures:
        query = query.where(Episode.outcome == DemoOutcome.SUCCESS)

    episodes = list((await session.scalars(query)).all())
    if not episodes and not scripted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Không có demo nào khớp điều kiện (status=approved, task_names, include_failures)",
        )

    existing = await session.scalar(select(Dataset).where(Dataset.name == body.name))
    if existing is not None:
        if not body.overwrite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Dataset '{body.name}' đã tồn tại"
            )
        old_zip = Path(existing.zip_path) if existing.zip_path else storage.dataset_zip_path(existing.id)
        await session.delete(existing)
        await session.commit()
        old_zip.unlink(missing_ok=True)

    dataset = Dataset(
        name=body.name,
        task_names=body.task_names,
        include_failures=body.include_failures,
        status=DatasetStatus.BUILDING,
    )
    session.add(dataset)
    await session.flush()  # cần dataset.id trước khi insert dataset_episodes

    if body.format == "robomimic":
        # Persist the final extension immediately so list/create responses can
        # expose the correct format even while the background build is running.
        dataset.zip_path = str(storage.dataset_hdf5_path(dataset.id))

    for episode in episodes if body.format == "raw" else []:
        session.add(DatasetEpisode(dataset_id=dataset.id, episode_id=episode.id))

    await session.commit()
    await session.refresh(dataset)

    # LẤY ENGINE TỪ CHÍNH SESSION CỦA REQUEST (`session.bind`), KHÔNG gọi
    # `get_engine()` — request có thể chạy trên engine đã bị override (test
    # dùng SQLite in-memory riêng qua dependency `get_session`), gọi thẳng
    # `get_engine()` sẽ tạo session trên engine THẬT khác (DB khác schema/dữ
    # liệu), background task tự y như đang chạy nhầm CSDL.
    bind = session.bind
    assert isinstance(bind, AsyncEngine)  # session_factory() luôn bind theo engine, không phải connection
    if body.format == "robomimic":
        background_tasks.add_task(
            build_robomimic_dataset,
            dataset.id,
            Path(dataset.zip_path),
            scripted,
            session_factory(bind),
        )
    else:
        background_tasks.add_task(build_dataset, dataset.id, session_factory(bind))

    return DatasetResponse.model_validate(dataset)


@router.get("", response_model=PaginatedResponse[DatasetResponse])
async def list_datasets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> PaginatedResponse[DatasetResponse]:
    query = select(Dataset)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()

    items_query = query.order_by(Dataset.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = list((await session.scalars(items_query)).all())

    total_pages = math.ceil(total / page_size) if total else 0

    return PaginatedResponse[DatasetResponse](
        items=[DatasetResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{dataset_id}", response_model=DatasetDetailResponse)
async def get_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> DatasetDetailResponse:
    dataset = await _get_dataset_or_404(dataset_id, session)

    episodes = list(
        (
            await session.scalars(
                select(Episode)
                .join(DatasetEpisode, DatasetEpisode.episode_id == Episode.id)
                .where(DatasetEpisode.dataset_id == dataset_id)
            )
        ).all()
    )

    return DatasetDetailResponse(
        **DatasetResponse.model_validate(dataset).model_dump(),
        error_message=dataset.error_message,
        episodes=[DemoResponse.model_validate(e) for e in episodes],
    )


@router.get("/{dataset_id}/download")
async def download_dataset(
    dataset_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user_allow_query_token),
) -> Response:
    dataset = await _get_dataset_or_404(dataset_id, session)
    if dataset.status != DatasetStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset đang ở trạng thái '{dataset.status}', chưa sẵn sàng tải",
        )

    zip_path = Path(dataset.zip_path) if dataset.zip_path else storage.dataset_zip_path(dataset_id)
    if not zip_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy file dataset")

    is_hdf5 = zip_path.suffix == ".hdf5"

    return stream_file_range(
        zip_path,
        request,
        media_type="application/x-hdf5" if is_hdf5 else "application/zip",
        extra_headers={"Content-Disposition": f'attachment; filename="{dataset.name}{zip_path.suffix}"'},
    )


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> Response:
    """Thứ tự: xoá row DB (cascade `dataset_episodes`) và commit TRƯỚC, rồi
    mới xoá file zip — lỗi xoá file (hiếm, nhưng vẫn log) không được chặn
    204, tương tự `DELETE /demos/{id}`."""
    dataset = await _get_dataset_or_404(dataset_id, session)
    zip_path = Path(dataset.zip_path) if dataset.zip_path else storage.dataset_zip_path(dataset_id)

    await session.delete(dataset)
    await session.commit()

    try:
        zip_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Không xoá được file zip dataset %s (%s): %s", dataset_id, zip_path, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
