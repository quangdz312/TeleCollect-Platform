"""Xem lại, cắt, gắn nhãn và duyệt demonstration.

Trách nhiệm: vòng human-in-the-loop. Một episode đi qua các trạng thái
`recorded → labeled → approved | rejected`, và chỉ demo `approved` mới được
gom vào dataset huấn luyện.

Video demo KHÔNG sinh ra từ Teleop (bản Core không có robot/sim) — được đưa
vào hệ thống qua upload thủ công (multipart). `front.mp4` bắt buộc, `wrist.mp4`
và `trajectory.json` optional.

Thứ tự xử lý upload (tránh rác khi có lỗi giữa chừng):
    1. task_name không tồn tại -> 404, CHƯA ghi gì cả.
    2. Ghi file vào thư mục TẠM (`storage_dir/tmp/<uuid>/`), không ghi thẳng
       vào `episodes/`.
    3. Validate (magic bytes, ffprobe, trajectory).
    4. Chỉ khi mọi thứ pass: tạo row DB rồi mới move thư mục tạm sang
       `episodes/<episode_id>/`.
    5. Bất kỳ lỗi nào ở bước 2-4 -> xoá sạch thư mục tạm (và row DB nếu đã
       trót tạo), không để lại rác — dùng try/finally.

Endpoint (Bước 3a):
    POST /demos/upload  (multipart)                     -> DemoUploadResponse
    GET  /demos         (task, status, outcome, operator_id, mine, page, page_size)
                                                          -> PaginatedResponse[DemoResponse]
    GET  /demos/{id}                                     -> DemoDetailResponse

Endpoint (Bước 3b — playback/thumbnail):
    GET /demos/{id}/playback?camera=front|wrist  -> video/mp4, hỗ trợ HTTP Range
    GET /demos/{id}/thumbnail                    -> image/jpeg

Cả 2 endpoint media dùng `current_user_allow_query_token` thay vì `current_user`
— chấp nhận token qua `Authorization: Bearer` HOẶC query `?token=` vì thẻ
`<video src="...">` của trình duyệt không gửi được header tuỳ ý (xem docstring
của dependency đó trong `src/services/security.py` để biết đánh đổi).

Giới hạn Range có chủ ý: chỉ hỗ trợ đơn-range. Header multi-range
(`bytes=0-99,200-299`) bị coi như không có Range, trả full file — không hỗ
trợ `multipart/byteranges` (xem `src/services/ranges.py`).

Endpoint (Bước 3c — review flow + summary):
    GET   /demos/summary        (KHAI BÁO TRƯỚC /demos/{id} — xem cảnh báo dưới)
    PATCH /demos/{id}/trim      (trim_start_s, trim_end_s)   -> DemoResponse
    PATCH /demos/{id}/label     (outcome, note)              -> DemoResponse
    POST  /demos/{id}/review    (decision, note)              -> DemoResponse
    POST  /demos/{id}/reopen    ()                             -> DemoResponse
    DELETE /demos/{id}          ()                             -> 204

CẢNH BÁO THỨ TỰ ROUTE: FastAPI khớp route theo thứ tự khai báo. `/demos/summary`
và `/demos/{id}` đều là 1 segment sau prefix — nếu `/{id}` được khai báo trước,
request tới `/demos/summary` sẽ bị route đó "nuốt" (path param `demo_id="summary"`)
và trả 404 (không tìm thấy demo tên "summary"). `/demos/summary` PHẢI đứng trước
`/demos/{demo_id}` trong file này.

Quy tắc chuyển trạng thái, kiểm tra sở hữu (`ensure_can_modify`) nằm ở
`src/services/demo_rules.py` — không lặp lại logic ở từng endpoint. `review`/
`reopen` luôn yêu cầu role reviewer trở lên (không xét sở hữu — operator
không được tự duyệt demo của chính mình); `trim`/`label`/`delete` xét sở hữu
(operator chỉ động vào demo của mình, reviewer trở lên động vào mọi demo).
"""

import json
import logging
import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.db import DatasetEpisode, Episode, Task, User, get_session
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.models.schemas import (
    DemoDetailResponse,
    DemoResponse,
    DemoSummaryResponse,
    DemoUploadResponse,
    LabelRequest,
    PaginatedResponse,
    ReviewRequest,
    TrimRequest,
)
from src.services import demo_rules, storage
from src.services.media import MediaProbeError, generate_thumbnail, has_mp4_magic_bytes, probe_video
from src.services.security import current_user, current_user_allow_query_token, require_min_role
from src.services.streaming import stream_file_range

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demos", tags=["demos"])

CAMERA_FILENAMES = {"front": storage.FRONT_FILENAME, "wrist": storage.WRIST_FILENAME}
"""Ánh xạ tường minh camera -> tên file cố định — tránh mọi khả năng ghép
chuỗi từ query param thành đường dẫn (dù `episode_dir()` đã tự resolve an
toàn trong `storage_dir`, ánh xạ cứng này loại bỏ luôn khả năng đó ngay từ đầu)."""

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB — đọc UploadFile theo chunk, không await .read() cả file.


class UploadTooLargeError(Exception):
    """Vượt `settings.max_upload_mb` — không tin `Content-Length` của client,
    đếm byte thật trong lúc ghi."""


async def _save_upload_chunked(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    total = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UploadTooLargeError(
                    f"{upload.filename or dest.name} vượt quá {max_bytes} byte"
                )
            f.write(chunk)
    await upload.close()


def _validate_trajectory(path: Path, action_dim: int) -> None:
    """Parse JSON; nếu có field `action`, mỗi phần tử phải có độ dài đúng
    bằng `task.action_dim`. Sai cú pháp hoặc sai chiều -> 422."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"trajectory.json không hợp lệ: {exc}",
        ) from exc

    actions = data.get("action") if isinstance(data, dict) else None
    if actions is None:
        return
    if not isinstance(actions, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="trajectory.action phải là danh sách",
        )
    for i, row in enumerate(actions):
        if not isinstance(row, list) or len(row) != action_dim:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"trajectory.action[{i}] phải có độ dài {action_dim}",
            )


async def _get_episode_or_404(episode_id: str, session: AsyncSession) -> Episode:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy demo")
    return episode


@router.post("/upload", response_model=DemoUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_demo(
    task_name: str = Form(...),
    front: UploadFile = File(...),
    wrist: UploadFile | None = File(default=None),
    trajectory: UploadFile | None = File(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> DemoUploadResponse:
    task = await session.get(Task, task_name)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy task")

    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    tmp_dir = storage.new_tmp_dir()
    episode: Episode | None = None
    moved = False
    warnings: list[str] = []

    try:
        front_path = tmp_dir / storage.FRONT_FILENAME
        try:
            await _save_upload_chunked(front, front_path, max_bytes)
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
        if not has_mp4_magic_bytes(front_path):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="front không phải file mp4 hợp lệ (thiếu magic bytes 'ftyp')",
            )

        has_wrist = wrist is not None
        if wrist is not None:
            wrist_path = tmp_dir / storage.WRIST_FILENAME
            try:
                await _save_upload_chunked(wrist, wrist_path, max_bytes)
            except UploadTooLargeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
                ) from exc
            if not has_mp4_magic_bytes(wrist_path):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="wrist không phải file mp4 hợp lệ (thiếu magic bytes 'ftyp')",
                )

        has_trajectory = trajectory is not None
        if trajectory is not None:
            trajectory_path = tmp_dir / storage.TRAJECTORY_FILENAME
            try:
                await _save_upload_chunked(trajectory, trajectory_path, max_bytes)
            except UploadTooLargeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
                ) from exc
            _validate_trajectory(trajectory_path, task.action_dim)

        try:
            probe = await probe_video(front_path)
        except MediaProbeError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

        thumb_path = tmp_dir / storage.THUMBNAIL_FILENAME
        has_thumbnail = await generate_thumbnail(front_path, thumb_path)
        if not has_thumbnail:
            warnings.append("Không sinh được thumbnail — video vẫn được chấp nhận")
            thumb_path.unlink(missing_ok=True)

        size_bytes = storage.dir_size_bytes(tmp_dir)

        episode = Episode(
            task_name=task.name,
            operator_id=user.id,  # LUÔN lấy từ current_user, bỏ qua nếu client cố gửi khác
            status=DemoStatus.RECORDED,
            fps=probe.fps,
            num_frames=probe.num_frames,
            duration_s=probe.duration_s,
            size_bytes=size_bytes,
            has_wrist=has_wrist,
            has_trajectory=has_trajectory,
        )
        session.add(episode)
        await session.commit()
        await session.refresh(episode)

        storage.promote_tmp_to_episode(tmp_dir, episode.id)
        moved = True

        return DemoUploadResponse(
            **DemoResponse.model_validate(episode).model_dump(),
            has_thumbnail=has_thumbnail,
            warnings=warnings,
        )
    finally:
        if not moved:
            storage.delete_dir(tmp_dir)
            if episode is not None:
                await session.delete(episode)
                await session.commit()


@router.get("", response_model=PaginatedResponse[DemoResponse])
async def list_demos(
    task: str | None = None,
    demo_status: DemoStatus | None = Query(default=None, alias="status"),
    outcome: DemoOutcome | None = None,
    operator_id: str | None = None,
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PaginatedResponse[DemoResponse]:
    query = select(Episode)
    if task is not None:
        query = query.where(Episode.task_name == task)
    if demo_status is not None:
        query = query.where(Episode.status == demo_status)
    if outcome is not None:
        query = query.where(Episode.outcome == outcome)

    effective_operator_id = user.id if mine else operator_id
    if effective_operator_id is not None:
        query = query.where(Episode.operator_id == effective_operator_id)

    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()

    items_query = (
        query.order_by(Episode.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = list((await session.scalars(items_query)).all())

    total_pages = math.ceil(total / page_size) if total else 0

    return PaginatedResponse[DemoResponse](
        items=[DemoResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/summary", response_model=DemoSummaryResponse)
async def get_demos_summary(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> DemoSummaryResponse:
    """PHẢI đứng trước `GET /demos/{demo_id}` — xem cảnh báo thứ tự route ở
    đầu file. Tối đa 2 query: 1 gộp theo status/outcome + tổng frames/duration/
    size (conditional aggregation, không loop theo từng status), 1 gộp theo
    task_name."""
    status_columns = [
        func.coalesce(func.sum(case((Episode.status == s, 1), else_=0)), 0).label(f"status_{s.value}")
        for s in DemoStatus
    ]
    outcome_columns = [
        func.coalesce(func.sum(case((Episode.outcome == o, 1), else_=0)), 0).label(f"outcome_{o.value}")
        for o in DemoOutcome
    ]
    agg_stmt = select(
        func.count().label("total"),
        func.coalesce(func.sum(Episode.num_frames), 0).label("total_frames"),
        func.coalesce(func.sum(Episode.duration_s), 0.0).label("total_duration_s"),
        func.coalesce(func.sum(Episode.size_bytes), 0).label("total_size_bytes"),
        *status_columns,
        *outcome_columns,
    )
    row = (await session.execute(agg_stmt)).one()

    by_status = {s.value: getattr(row, f"status_{s.value}") for s in DemoStatus}
    by_outcome = {o.value: getattr(row, f"outcome_{o.value}") for o in DemoOutcome}

    task_rows = (
        await session.execute(select(Episode.task_name, func.count()).group_by(Episode.task_name))
    ).all()
    by_task = {task_name: count for task_name, count in task_rows}

    success_count = by_outcome[DemoOutcome.SUCCESS.value]
    failure_count = by_outcome[DemoOutcome.FAILURE.value]
    approved_count = by_status[DemoStatus.APPROVED.value]
    rejected_count = by_status[DemoStatus.REJECTED.value]

    # Mẫu số CHỦ Ý không dùng by_status["labeled"]/["approved"+"rejected"]
    # trực tiếp cho labeled_count: một demo đã gán outcome rồi được duyệt sẽ
    # rời khỏi status "labeled", nhưng vẫn phải tính là "đã có nhãn" — nên
    # labeled_count = success+failure theo OUTCOME (bền qua mọi status sau đó).
    labeled_count = success_count + failure_count
    reviewed_count = approved_count + rejected_count

    return DemoSummaryResponse(
        total=row.total,
        by_status=by_status,
        by_outcome=by_outcome,
        by_task=by_task,
        labeled_count=labeled_count,
        reviewed_count=reviewed_count,
        success_count=success_count,
        approved_count=approved_count,
        success_rate=(success_count / labeled_count) if labeled_count else 0.0,
        approval_rate=(approved_count / reviewed_count) if reviewed_count else 0.0,
        total_frames=row.total_frames,
        total_duration_hours=row.total_duration_s / 3600,
        total_size_bytes=row.total_size_bytes,
    )


@router.get("/{demo_id}", response_model=DemoDetailResponse)
async def get_demo(
    demo_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user),
) -> DemoDetailResponse:
    episode = await _get_episode_or_404(demo_id, session)
    thumb_path = storage.episode_dir(episode.id) / storage.THUMBNAIL_FILENAME
    return DemoDetailResponse(
        **DemoResponse.model_validate(episode).model_dump(),
        has_thumbnail=thumb_path.exists(),
    )


@router.get("/{demo_id}/playback", operation_id="playback_demo")
@router.head("/{demo_id}/playback", operation_id="playback_demo_head")
async def playback_demo(
    demo_id: str,
    request: Request,
    camera: str = Query(default="front", pattern="^(front|wrist)$"),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user_allow_query_token),
) -> Response:
    episode = await _get_episode_or_404(demo_id, session)
    if camera == "wrist" and not episode.has_wrist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Demo này không có camera wrist"
        )

    video_path = storage.episode_dir(episode.id) / CAMERA_FILENAMES[camera]
    if not video_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy file video")

    return stream_file_range(video_path, request, media_type="video/mp4")


@router.get("/{demo_id}/thumbnail")
async def get_thumbnail(
    demo_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(current_user_allow_query_token),
) -> FileResponse:
    episode = await _get_episode_or_404(demo_id, session)
    thumb_path = storage.episode_dir(episode.id) / storage.THUMBNAIL_FILENAME
    if not thumb_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Demo này không có thumbnail"
        )
    return FileResponse(thumb_path, media_type="image/jpeg")


@router.patch("/{demo_id}/trim", response_model=DemoResponse)
async def trim_demo(
    demo_id: str,
    body: TrimRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DemoResponse:
    episode = await _get_episode_or_404(demo_id, session)
    demo_rules.ensure_can_modify(episode, user)
    demo_rules.apply_trim(episode, body.trim_start_s, body.trim_end_s)
    await session.commit()
    await session.refresh(episode)
    return DemoResponse.model_validate(episode)


@router.patch("/{demo_id}/label", response_model=DemoResponse)
async def label_demo(
    demo_id: str,
    body: LabelRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DemoResponse:
    episode = await _get_episode_or_404(demo_id, session)
    demo_rules.ensure_can_modify(episode, user)
    demo_rules.apply_label(episode, body.outcome, body.note)
    await session.commit()
    await session.refresh(episode)
    return DemoResponse.model_validate(episode)


@router.post("/{demo_id}/review", response_model=DemoResponse)
async def review_demo(
    demo_id: str,
    body: ReviewRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> DemoResponse:
    """Luôn yêu cầu role reviewer trở lên — KHÔNG xét sở hữu (một operator
    không được tự duyệt demo của chính mình, kể cả admin cũng đi qua đây nhờ
    kế thừa role, không phải nhờ bỏ qua check)."""
    episode = await _get_episode_or_404(demo_id, session)
    demo_rules.apply_review(episode, body.decision, reviewer_id=user.id, note=body.note)
    await session.commit()
    await session.refresh(episode)
    return DemoResponse.model_validate(episode)


@router.post("/{demo_id}/reopen", response_model=DemoResponse)
async def reopen_demo(
    demo_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> DemoResponse:
    episode = await _get_episode_or_404(demo_id, session)
    demo_rules.apply_reopen(episode)
    await session.commit()
    await session.refresh(episode)
    return DemoResponse.model_validate(episode)


@router.delete("/{demo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_demo(
    demo_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """Thứ tự: xoá row DB (kèm mọi row `dataset_episodes` tham chiếu tới demo
    này — KHÔNG đụng file zip của dataset, zip là snapshot độc lập đã copy
    file vào trong lúc build) và commit TRƯỚC, rồi mới xoá thư mục file.

    Nếu xoá thư mục lỗi (Windows còn giữ handle mở) -> log error kèm đường
    dẫn nhưng VẪN trả 204: file mồ côi trên đĩa vô hại, còn row DB trỏ tới
    file đã mất sẽ làm `playback` (Bước 3b) nổ 500 — ưu tiên tránh cái sau.
    """
    episode = await _get_episode_or_404(demo_id, session)
    demo_rules.ensure_can_modify(episode, user)

    await session.execute(delete(DatasetEpisode).where(DatasetEpisode.episode_id == episode.id))
    await session.delete(episode)
    await session.commit()

    episode_path = storage.episode_dir(demo_id)
    try:
        shutil.rmtree(episode_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("Không xoá được thư mục episode %s (%s): %s", demo_id, episode_path, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
