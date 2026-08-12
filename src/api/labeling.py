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

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from src.models.db import User
from src.models.enums import UserRole
from src.models.schemas import ScriptedLabelRequest, ScriptedRunRequest
from src.services.security import ROLE_RANK, current_user_allow_query_token, require_min_role
from src.services.auto_label import classify_scripted

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

    catalogue = []
    for task in supported_tasks():
        spec = collection_task_spec(task)
        catalogue.append(
            {
                "task": task,
                "tool": spec.tool_name,
                "default_horizon": spec.default_horizon,
            },
        )
    return catalogue


def _public(record: dict[str, Any], *, include_score: bool) -> dict[str, Any]:
    public = dict(record) if include_score else {
        key: value for key, value in record.items() if key not in WITHHELD_WHEN_BLIND
    }
    recommendation = classify_scripted(
        record.get("gate_decision"),
        record.get("recorded_success"),
        record.get("auto_flags"),
        record.get("task"),
    )
    public["auto_label"] = recommendation.label
    public["auto_label_reason"] = recommendation.reason
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
async def overview(_user: User = Depends(reviewer_required)) -> dict[str, Any]:
    return workspace().summary()


# --- thu dữ liệu ------------------------------------------------------------


@router.post("/runs", status_code=202)
async def start_run(
    request: ScriptedRunRequest,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    if request.task not in supported_tasks():
        raise HTTPException(400, f"task không hợp lệ: {request.task}")
    if request.quality not in supported_qualities():
        if request.task == "tool_hang" and request.quality != "clean":
            raise HTTPException(400, "ToolHang hiện chỉ hỗ trợ quality clean")
        raise HTTPException(400, f"quality không hợp lệ: {request.quality}")

    space = workspace()
    from src.labeling.jobs import submit_collection

    seed = (
        space.next_free_seed(request.task, request.quality)
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
    status: str = Query(default="all", pattern="^(all|pending|reviewed)$"),
    include_score: bool = False,
    limit: int = Query(default=500, ge=1, le=5000),
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    space = workspace()
    labels = space.labels_by_id()
    videos = {path.name for path in space.videos_dir.glob("*.mp4")}

    items = []
    for record in space.scores():
        if task is not None and record["task"] != task:
            continue
        if quality is not None and record["requested_quality"] != quality:
            continue
        label = labels.get(record["episode_id"])
        if status == "pending" and label is not None:
            continue
        if status == "reviewed" and label is None:
            continue
        item = _public(dict(record), include_score=include_score)
        item["label"] = label
        item["video_ready"] = space.video_path(record["episode_id"]).name in videos
        items.append(item)
        if len(items) >= limit:
            break

    return {"episodes": items, "count": len(items), "total": len(space.scores())}


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
    labels = space.labels()
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
