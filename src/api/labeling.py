"""REST cho công cụ chấm nhãn tay trên dữ liệu scripted.

Luồng đúng một vòng: chọn task + mức nhiễu → Start (thu nền) → xem video →
chấm Đạt/Loại kèm lý do → báo cáo shadow nói ngưỡng đã đủ căn cứ để bật chưa.

Ba điểm cố ý trong thiết kế:

*Blind mặc định.* Danh sách và chi tiết episode **không** trả điểm máy trừ khi
client xin `include_score=true`. Ẩn ở phía JS là ẩn giả — người chấm mở
DevTools ra là thấy, và nhãn thu được hết dùng để hiệu chuẩn. Nên việc lọc nằm
ở server.

*Không có endpoint nào tự bật gate.* Ngưỡng chỉ được *đề xuất* trong báo cáo,
kèm lý do vì sao chưa nên tin.

*Chưa gắn phân quyền.* `src/api/auth.py` vẫn là scaffold nên chưa có dependency
vai trò để dùng. Đây là công cụ chạy cục bộ; khi auth xong thì bọc router này
bằng `require_role(UserRole.REVIEWER)`.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db import CollectionBatch, User, get_session
from src.models.enums import UserRole
from src.models.schemas import ScriptedLabelRequest, ScriptedRunRequest
from src.services.security import ROLE_RANK, current_user_allow_query_token, require_min_role

if TYPE_CHECKING:
    from src.labeling.workspace import Workspace

router = APIRouter(prefix="/labeling", tags=["labeling"])
reviewer_required = require_min_role(UserRole.REVIEWER)

#: Trường bị gỡ khi client không xin điểm — không chỉ điểm máy, mà mọi thứ nói
#: trước kết quả. `recorded_success` là cờ thành công của sim, và review yield
#: chính là "người chấm khác cờ đó bao nhiêu lần"; cho người chấm nhìn thấy nó
#: thì con số ấy chỉ còn đo mức độ chép lại. `provenance` chứa `outcome`,
#: `failure_stage` và `noise_scale`, tức là nói thẳng đáp án.
#:
#: Còn một rò rỉ chưa bịt: `episode_id` vẫn mang tên mẻ (`lift_poor_seed0`),
#: nên mức nhiễu đặt hàng vẫn suy ra được nếu người chấm để ý. Bịt hẳn thì phải
#: cấp id vô danh cho từng episode; để sau, và ghi ra đây để không ai tưởng là
#: đã kín.
WITHHELD_WHEN_BLIND = (
    "auto_score",
    "gate_decision",
    "auto_flags",
    "recorded_success",
    "requested_quality",
    "provenance",
)


def workspace() -> Workspace:
    from src.labeling.workspace import Workspace

    return Workspace.from_settings().ensure()


def supported_tasks() -> tuple[str, ...]:
    from src.sim.scripted_generation import SUPPORTED_TASKS

    return SUPPORTED_TASKS


def supported_qualities() -> tuple[str, ...]:
    from src.sim.scripted_generation import SUPPORTED_QUALITIES

    return SUPPORTED_QUALITIES


@lru_cache(maxsize=1)
def _task_catalogue() -> list[dict[str, Any]]:
    """Horizon mặc định lấy từ chính task spec, không chép tay lại."""

    from src.sim.scripted_generation import collection_task_spec
    from src.sim.tool_hang import TOOLHANG_DISPLAY_NAME, TOOLHANG_TOOL_NAME

    catalogue = []
    for task in supported_tasks():
        spec = collection_task_spec(task)
        # `tool_name` is a stored dataset identifier, and ToolHang's is the
        # historical `tool_hang_stage1` even though the task runs both stages.
        # Send a display name so the picker does not tell the operator they are
        # collecting stage 1 only.
        label = (
            TOOLHANG_DISPLAY_NAME
            if spec.tool_name == TOOLHANG_TOOL_NAME
            else spec.tool_name
        )
        catalogue.append(
            {
                "task": task,
                "tool": spec.tool_name,
                "tool_label": label,
                "default_horizon": spec.default_horizon,
            },
        )
    return catalogue


def _public(record: dict[str, Any], *, include_score: bool) -> dict[str, Any]:
    public = dict(record) if include_score else {
        key: value for key, value in record.items() if key not in WITHHELD_WHEN_BLIND
    }
    from src.labeling.auto_gate import AUTO_GATE_VERSION, evaluate

    gate = evaluate(record)
    public["auto_label"] = {
        "approve": "accept",
        "reject": "reject",
        "review": "review",
        "audit": "review",
    }[gate.action]
    public["auto_label_reason"] = gate.reason
    public["gate_action"] = gate.action
    public["audit_required"] = gate.action == "audit"
    public["auto_gate_version"] = AUTO_GATE_VERSION
    public["auto_gate_reason"] = gate.reason
    return public


# --- cấu hình & tổng quan ---------------------------------------------------


@router.get("/config")
async def config(_user: User = Depends(reviewer_required)) -> dict[str, Any]:
    space = workspace()
    from src.labeling import reasons as reason_vocab

    return {
        "tasks": _task_catalogue(),
        "qualities": list(supported_qualities()),
        "reasons": reason_vocab.as_dicts(),
        "workspace": space.summary(),
        "suggested_seeds": {
            f"{item['task']}:{quality}": space.next_free_seed(str(item["task"]), quality)
            for item in _task_catalogue()
            for quality in supported_qualities()
        },
    }


@router.get("/overview")
async def overview(
    collection_batch_id: str | None = None,
    task: str | None = None,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    return workspace().summary(collection_batch_id=collection_batch_id, task=task)


@router.post("/auto-gate/apply")
async def apply_auto_gate(
    task: str | None = None,
    collection_batch_id: str | None = None,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    """Apply the conservative gate to unlabeled episodes; human labels are immutable."""

    from src.labeling.auto_gate import apply

    space = workspace()
    return {
        "result": apply(
            space,
            task=task,
            collection_batch_id=collection_batch_id,
        ),
        "workspace": space.summary(),
    }


@router.get("/auto-gate/dry-run")
async def dry_run_auto_gate(
    task: str | None = None,
    collection_batch_id: str | None = None,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    """Preview automatic verdicts without persisting any review decision."""

    from src.labeling.auto_gate import dry_run

    return dry_run(
        workspace().scores(),
        task=task,
        collection_batch_id=collection_batch_id,
    )


@router.get("/diversity")
async def diversity(
    task: str = Query(...),
    scope: str = Query("approved", pattern="^(approved|reviewed|all)$"),
    collection_batch_id: str | None = Query(default=None, min_length=1, max_length=64),
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    if task not in supported_tasks():
        raise HTTPException(400, f"task không hợp lệ: {task}")
    from src.labeling.diversity import diversity_report

    # `collection_batch_id` thu hẹp báo cáo về một đợt thu. Phạm vi tham chiếu
    # để tính coverage vẫn là cả task, nên con số trả lời được câu "đợt này phủ
    # bao nhiêu phần vùng đã thu", chứ không phải so đợt với chính nó.
    return diversity_report(
        workspace(), task=task, scope=scope, collection_batch_id=collection_batch_id
    )


# --- thu dữ liệu ------------------------------------------------------------


async def _register_batch(
    session: AsyncSession, batch_id: str, task: str, user_id: str,
) -> None:
    """Ghi đợt thu vào bảng nếu nó chưa có ở đó.

    Mã đợt thu đi vào provenance của episode, nên trang Data thấy đợt thu ngay
    cả khi bảng `collection_batches` không có dòng nào cho nó — và khi thiếu
    dòng đó thì mã hiện ra thay cho tên, còn đổi tên hay xoá đều 404 vì không
    có gì để sửa. Thu scripted là một trong hai đường tạo ra đợt thu mới, nên
    nó phải đăng ký đợt thu giống như lúc nạp gói từ app.

    Tên để bằng mã: yêu cầu thu không mang theo tên nào, và người dùng đổi
    được ở trang Data.
    """
    if batch_id == "legacy" or await session.get(CollectionBatch, batch_id) is not None:
        return
    # Lưu tên task dạng chuẩn, giống đường nạp gói: chỗ chặn trộn hai task khi
    # nạp thêm so sánh sau khi chuẩn hoá, nên hai đường phải ghi cùng một dạng.
    from src.api.raw import _canonical_task

    session.add(
        CollectionBatch(
            id=batch_id, name=batch_id[:150],
            task_name=_canonical_task(task) or None,
            description="", created_by=user_id,
        )
    )
    await session.commit()


@router.post("/runs", status_code=202)
async def start_run(
    request: ScriptedRunRequest,
    user: User = Depends(reviewer_required),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if request.task not in supported_tasks():
        raise HTTPException(400, f"task không hợp lệ: {request.task}")
    if request.quality not in supported_qualities():
        raise HTTPException(400, f"quality không hợp lệ: {request.quality}")
    await _register_batch(session, request.collection_batch_id, request.task, user.id)
    space = workspace()
    from src.labeling.jobs import submit_collection

    seed = (
        space.next_free_seed(
            request.task, request.quality,
            collection_batch_id=request.collection_batch_id,
        )
        if request.seed is None
        else request.seed
    )
    try:
        job = submit_collection(
            space,
            task=request.task,
            quality=request.quality,
            episodes=request.episodes,
            seed=seed,
            horizon=request.horizon,
            overwrite=request.overwrite,
            collection_batch_id=request.collection_batch_id,
        )
    except FileExistsError as error:
        raise HTTPException(409, str(error)) from error
    return job.as_dict()


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=25, ge=1, le=100),
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    from src.labeling.jobs import registry

    jobs = registry().list(kind="collect", limit=limit)
    return {"runs": [job.as_dict() for job in jobs]}


@router.get("/runs/{job_id}")
async def get_run(
    job_id: str,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    from src.labeling.jobs import registry

    job = registry().get(job_id)
    if job is None or job.kind != "collect":
        raise HTTPException(404, "không có run này")
    return job.as_dict()


# --- episode ----------------------------------------------------------------


@router.get("/episodes")
async def list_episodes(
    task: str | None = None,
    quality: str | None = None,
    collection_batch_id: str | None = None,
    status: str = Query(default="all", pattern="^(all|pending|reviewed)$"),
    include_score: bool = False,
    limit: int = Query(default=500, ge=1, le=5000),
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    # Đọc cả workspace và liệt kê thư mục video — khoảng 140 ms với kho hiện
    # tại. Backend chỉ có một worker nên chặn ở đây là chặn tất cả mọi người.
    return await asyncio.to_thread(
        _collect_episodes, task, quality, collection_batch_id, status, include_score, limit
    )


def _collect_episodes(
    task: str | None,
    quality: str | None,
    collection_batch_id: str | None,
    status: str,
    include_score: bool,
    limit: int,
) -> dict[str, Any]:
    space = workspace()
    labels = space.labels_by_id()
    videos = {path.name for path in space.videos_dir.glob("*.mp4")}

    items = []
    matching = []
    for record in space.scores():
        if task is not None and record["task"] != task:
            continue
        if quality is not None and record["requested_quality"] != quality:
            continue
        if collection_batch_id is not None and (
            space._collection_batch(record) != collection_batch_id
        ):
            continue
        label = labels.get(record["episode_id"])
        if status == "pending" and label is not None:
            continue
        if status == "reviewed" and label is None:
            continue
        matching.append(record)
        item = _public(dict(record), include_score=include_score)
        item["label"] = label
        item["video_ready"] = space.video_path(record["episode_id"]).name in videos
        items.append(item)
        if len(items) >= limit:
            break

    return {"episodes": items, "count": len(items), "total": len(matching)}


@router.get("/episodes/detail")
async def episode_detail(
    episode_id: str,
    include_score: bool = False,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    space = workspace()
    record = space.scores_by_id().get(episode_id)
    if record is None:
        raise HTTPException(404, "không có episode này")
    item = _public(dict(record), include_score=include_score)
    item["label"] = space.labels_by_id().get(episode_id)
    item["video_ready"] = space.video_path(episode_id).exists()
    return item


@router.post("/episodes/video", status_code=202)
async def request_video(
    episode_id: str,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    """Xin dựng video. Trả ngay; poll `/labeling/video` cho tới khi có file."""

    space = workspace()
    if episode_id not in space.scores_by_id():
        raise HTTPException(404, "không có episode này")
    if space.video_path(episode_id).exists():
        return {"status": "ready", "episode_id": episode_id}
    from src.labeling.jobs import submit_render

    job = submit_render(space, episode_id)
    return {"status": "rendering", "episode_id": episode_id, "job": job.as_dict()}


@router.get("/video")
async def video(
    episode_id: str,
    user: User = Depends(current_user_allow_query_token),
) -> FileResponse:
    if ROLE_RANK[UserRole(user.role)] < ROLE_RANK[UserRole.REVIEWER]:
        raise HTTPException(403, "Không đủ quyền")
    space = workspace()
    if episode_id not in space.scores_by_id():
        raise HTTPException(404, "không có episode này")
    path = space.video_path(episode_id)
    if not path.exists():
        # 404 sẽ nói dối: episode có thật, chỉ là chưa dựng xong.
        raise HTTPException(425, "video đang dựng, thử lại sau")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


# --- nhãn -------------------------------------------------------------------


@router.post("/labels", status_code=201)
async def submit_label(
    request: ScriptedLabelRequest,
    user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    space = workspace()
    try:
        record = space.append_label(
            request.episode_id,
            decision=request.decision,
            reasons=request.reasons,
            note=request.note,
            reviewer=user.username,
            blind=request.blind,
        )
    except KeyError as error:
        raise HTTPException(404, "không có episode này") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"label": record, "workspace": space.summary()}


@router.get("/labels")
async def list_labels(
    history: bool = False,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    return {"labels": workspace().labels(latest_only=not history)}


# --- báo cáo ----------------------------------------------------------------


@router.get("/report")
async def report(_user: User = Depends(reviewer_required)) -> dict[str, Any]:
    """Ngưỡng mà dữ liệu hiện tại chống đỡ được, và vì sao chưa nên bật."""

    space = workspace()
    scores = space.scores()
    # Automatic verdicts must never be used to calibrate the same gate that
    # produced them. Only independent human decisions are valid shadow truth.
    labels = [
        label for label in space.labels()
        if label.get("decision_source", "human") != "auto_gate"
    ]
    if not scores:
        raise HTTPException(409, "workspace chưa có episode nào")

    from src.labeling import reasons as reason_vocab
    from src.labeling.shadow import DEFAULT_SHADOW, build_report

    shadow = build_report(scores, labels, DEFAULT_SHADOW)
    by_id = {record["episode_id"]: record for record in scores}

    missed: list[dict[str, str]] = []
    reason_counts: dict[str, int] = {}
    for label in labels:
        for code in label.get("reasons", []):
            reason_counts[code] = reason_counts.get(code, 0) + 1
        record = by_id.get(label["episode_id"])
        if record is not None:
            missed.extend(reason_vocab.disagreements(record, label.get("reasons", [])))

    return {
        "shadow": shadow.as_dict(),
        "reason_counts": reason_counts,
        # Người chấm nêu lý do mà check tương ứng vẫn cho qua: đây là danh sách
        # việc phải sửa ở check, không phải ở ngưỡng.
        "checks_that_missed": missed,
        "workspace": space.summary(),
        "targets": {
            "min_auc": DEFAULT_SHADOW.min_auc,
            "target_approve_zone": DEFAULT_SHADOW.target_approve_zone,
            "min_reviews_for_yield": DEFAULT_SHADOW.min_reviews_for_yield,
        },
    }
