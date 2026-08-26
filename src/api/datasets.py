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

import json
import logging
import math
from pathlib import Path

import h5py
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.models.db import Dataset, DatasetEpisode, Episode, User, get_session, session_factory
from src.models.enums import DatasetStatus, DemoOutcome, DemoStatus, UserRole
from src.models.schemas import (
    DatasetCreateRequest,
    DatasetDetailResponse,
    DatasetEpisodeSnapshot,
    DatasetRetryRequest,
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


def _legacy_hdf5_metadata(dataset: Dataset) -> tuple[str, list[dict], dict]:
    """Recover provenance stored inside HDF5 exports created before DB snapshots existed."""
    path = Path(dataset.zip_path) if dataset.zip_path else storage.dataset_hdf5_path(dataset.id)
    if not path.is_file() or path.suffix.lower() not in {".h5", ".hdf5"}:
        return "unknown", [], {}
    sources: set[str] = set()
    inventory: list[dict] = []
    fields: list[dict] = []
    masks: list[str] = []
    try:
        with h5py.File(path, "r") as handle:
            data = handle.get("data")
            demos = sorted(data.keys()) if isinstance(data, h5py.Group) else []
            for name in demos:
                demo = data[name]
                raw_source = str(demo.attrs.get("source", "")).casefold()
                source = "teleop" if raw_source in {"teleop", "manual_teleop"} else "scripted" if raw_source == "scripted" else "unknown"
                if source != "unknown":
                    sources.add(source)
                inventory.append({
                    "episode_id": str(demo.attrs.get("source_episode_id", name)),
                    "source": source,
                    "task": dataset.task_names[0] if dataset.task_names else "unknown",
                    "outcome": "success" if bool(demo.attrs.get("successful", False)) else "unknown",
                    "frames": int(demo.attrs.get("num_samples", len(demo.get("actions", [])))),
                    "review_status": str(demo.attrs.get("review_decision", "approved")),
                })
            if demos:
                def visit(name: str, value: h5py.Dataset | h5py.Group) -> None:
                    if isinstance(value, h5py.Dataset):
                        fields.append({"path": name, "shape": list(value.shape), "dtype": str(value.dtype)})
                data[demos[0]].visititems(visit)
            masks = sorted(handle["mask"].keys()) if "mask" in handle else []
    except (OSError, ValueError, KeyError):
        return "unknown", [], {}
    source_value = next(iter(sources)) if len(sources) == 1 else "both" if len(sources) > 1 else "unknown"
    return source_value, inventory, {"sample_demo": demos[0] if demos else None, "fields": fields, "masks": masks}


async def _backfill_legacy_datasets(session: AsyncSession, datasets: list[Dataset]) -> None:
    changed = False
    for dataset in datasets:
        if dataset.created_by is not None or dataset.episode_inventory:
            continue
        source, inventory, manifest = _legacy_hdf5_metadata(dataset)
        dataset.data_source = source
        dataset.episode_inventory = inventory
        dataset.schema_manifest = manifest
        dataset.exporter_version = "legacy"
        changed = True
    if changed:
        await session.commit()


def _matches_scripted_export(score: dict, body: DatasetCreateRequest) -> bool:
    if score.get("task") != body.task_names[0]:
        return False
    if body.collection_batch_id:
        recorded_batch = score.get("provenance", {}).get("collection_batch_id")
        if body.collection_batch_id == "legacy":
            if recorded_batch not in (None, "", "legacy"):
                return False
        elif recorded_batch != body.collection_batch_id:
            return False
    if not body.include_failures and score.get("recorded_success") is not True:
        return False
    return True


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
    user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> DatasetResponse:
    """Chọn episode TRƯỚC khi đụng tới dataset trùng tên — nếu không có demo
    nào khớp thì trả 422 mà KHÔNG xoá mất dataset cũ (trường hợp overwrite).
    """
    query = select(Episode).where(Episode.status == DemoStatus.APPROVED)
    if body.episode_ids:
        query = query.where(Episode.id.in_(body.episode_ids))
    if body.task_names:
        query = query.where(Episode.task_name.in_(body.task_names))
    if not body.include_failures:
        query = query.where(Episode.outcome == DemoOutcome.SUCCESS)
    episodes = list((await session.scalars(query)).all())
    if body.format == "robomimic" and body.data_source == "scripted":
        episodes = []

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
        if body.data_source != "teleop":
            for score in space.scores():
                if body.episode_ids and str(score["episode_id"]) not in body.episode_ids:
                    continue
                label = labels.get(str(score["episode_id"]))
                if not label or label["human_decision"] != "approved":
                    continue
                if not _matches_scripted_export(score, body):
                    continue
                scripted.append({
                    **label,
                    "decision": label["human_decision"],
                    "source_path": str(space.resolve_source(str(score["source"]))),
                    "demo": score["demo"],
                    "task": score.get("task", body.task_names[0]),
                    "length": score.get("length", 0),
                    "successful": score.get("recorded_success") is True,
                })
        manual = []
        for episode in episodes:
            root = storage.episode_dir(episode.id)
            if not (root / storage.ACTIONS_FILENAME).is_file() or not (
                root / storage.META_FILENAME
            ).is_file():
                continue
            manual.append({
                "artifact_format": "teleop_dir",
                "episode_dir": str(root),
                "episode_id": episode.id,
                "decision": "approved",
                "reviewer": episode.reviewer_id or "unknown",
                "reviewed_at": episode.reviewed_at.isoformat() if episode.reviewed_at else "",
                "note": episode.note,
                "trim_start_s": episode.trim_start_s,
                "trim_end_s": episode.trim_end_s,
                "successful": episode.outcome == DemoOutcome.SUCCESS,
            })
        export_items = [*scripted, *manual]
        if not export_items:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Không có {body.data_source} episode approved có trajectory "
                    "khớp task và collection batch đã chọn"
                ),
            )
    else:
        scripted = []
        manual = []
        export_items = []
    if body.episode_ids:
        eligible_ids = (
            {str(item["episode_id"]) for item in export_items}
            if body.format == "robomimic"
            else {episode.id for episode in episodes}
        )
        invalid_ids = sorted(set(body.episode_ids) - eligible_ids)
        if invalid_ids:
            preview = ", ".join(invalid_ids[:5])
            suffix = "…" if len(invalid_ids) > 5 else ""
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Một số episode đã chọn không tồn tại hoặc không đủ điều kiện "
                    f"(approved, đúng task/source/outcome, có trajectory): {preview}{suffix}"
                ),
            )
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
        data_source=body.data_source,
        collection_batch_id=body.collection_batch_id,
        created_by=user.display_name or user.username,
        exporter_version="1.0",
        build_spec=json.loads(json.dumps(export_items, default=str)),
    )
    session.add(dataset)
    await session.flush()  # cần dataset.id trước khi insert dataset_episodes

    if body.format == "robomimic":
        # Persist the final extension immediately so list/create responses can
        # expose the correct format even while the background build is running.
        dataset.zip_path = str(storage.dataset_hdf5_path(dataset.id))

    linked_episode_ids = (
        {str(item["episode_id"]) for item in manual}
        if body.format == "robomimic" else {episode.id for episode in episodes}
    )
    for episode in episodes:
        if episode.id not in linked_episode_ids:
            continue
        session.add(DatasetEpisode(dataset_id=dataset.id, episode_id=episode.id))

    scripted_by_id = {str(score["episode_id"]): score for score in scripted}
    teleop_by_id = {episode.id: episode for episode in episodes}
    dataset.episode_inventory = [
        {
            "episode_id": str(item["episode_id"]),
            "source": "teleop" if item.get("artifact_format") == "teleop_dir" else "scripted",
            "task": (
                teleop_by_id[str(item["episode_id"])].task_name
                if str(item["episode_id"]) in teleop_by_id
                else str(scripted_by_id[str(item["episode_id"])].get("task", body.task_names[0]))
            ),
            "outcome": (
                teleop_by_id[str(item["episode_id"])].outcome.value
                if str(item["episode_id"]) in teleop_by_id and teleop_by_id[str(item["episode_id"])].outcome
                else ("success" if item.get("successful") else "failure")
            ),
            "frames": int(
                teleop_by_id[str(item["episode_id"])].num_frames or 0
                if str(item["episode_id"]) in teleop_by_id
                else scripted_by_id.get(str(item["episode_id"]), {}).get("length", 0) or 0
            ),
            "review_status": "approved",
        }
        for item in export_items
    ]

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
        # Set unconditionally a few lines above; the ORM column type is
        # Optional because it's nullable for other dataset states, not
        # because it can be missing here.
        assert dataset.zip_path is not None
        background_tasks.add_task(
            build_robomimic_dataset,
            dataset.id,
            Path(dataset.zip_path),
            export_items,
            session_factory(bind),
        )
    else:
        background_tasks.add_task(build_dataset, dataset.id, session_factory(bind))

    return DatasetResponse.model_validate(dataset)


@router.get("", response_model=PaginatedResponse[DatasetResponse])
async def list_datasets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    dataset_status: DatasetStatus | None = Query(default=None, alias="status"),
    task: str | None = Query(default=None, max_length=100),
    source: str | None = Query(default=None, pattern="^(teleop|scripted|both|unknown)$"),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> PaginatedResponse[DatasetResponse]:
    await _backfill_legacy_datasets(session, list((await session.scalars(select(Dataset))).all()))
    query = select(Dataset)
    if search:
        query = query.where(Dataset.name.ilike(f"%{search.strip()}%"))
    if dataset_status:
        query = query.where(Dataset.status == dataset_status)
    if source:
        query = query.where(Dataset.data_source == source)
    if task:
        query = query.where(Dataset.task_names.contains([task]))
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
    await _backfill_legacy_datasets(session, [dataset])

    return DatasetDetailResponse(
        **DatasetResponse.model_validate(dataset).model_dump(),
        error_message=dataset.error_message,
        episodes=[DatasetEpisodeSnapshot.model_validate(e) for e in dataset.episode_inventory],
        schema_manifest=dataset.schema_manifest,
    )


@router.post("/{dataset_id}/retry", response_model=DatasetResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_dataset(
    dataset_id: str,
    _body: DatasetRetryRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> DatasetResponse:
    dataset = await _get_dataset_or_404(dataset_id, session)
    if dataset.status != DatasetStatus.FAILED:
        raise HTTPException(status_code=409, detail="Chỉ có thể retry dataset build thất bại")
    if dataset.format != "robomimic" or not dataset.build_spec:
        raise HTTPException(status_code=409, detail="Dataset cũ không có build specification để retry")
    output = storage.dataset_hdf5_path(dataset.id)
    dataset.status = DatasetStatus.BUILDING
    dataset.error_message = None
    await session.commit()
    bind = session.bind
    assert isinstance(bind, AsyncEngine)
    background_tasks.add_task(
        build_robomimic_dataset, dataset.id, output, dataset.build_spec, session_factory(bind),
    )
    await session.refresh(dataset)
    return DatasetResponse.model_validate(dataset)


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
