"""Read-only unified view over teleop and scripted raw episodes."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.labeling import workspace
from src.models.db import Episode, RawEpisodeAudit, RawEpisodeManagement, User, get_session
from src.models.enums import DemoStatus, UserRole
from src.models.schemas import (
    RawArtifactResponse,
    RawEpisodeArchiveUpdate,
    RawEpisodeAuditResponse,
    RawEpisodeCameras,
    RawEpisodeDetailResponse,
    RawEpisodePageResponse,
    RawEpisodeResponse,
    RawEpisodeSummaryResponse,
    RawSignalSeries,
    RawSignalsResponse,
)
from src.services import storage
from src.services.security import (
    ROLE_RANK,
    current_user_allow_query_token,
    require_min_role,
)
from src.services.streaming import stream_file_range

router = APIRouter(prefix="/raw", tags=["raw"])
reviewer_required = require_min_role(UserRole.REVIEWER)

RawSource = Literal["teleop", "scripted"]
RawQuality = Literal["clean", "good", "medium", "poor"]
RawReviewStatus = Literal["pending", "approved", "rejected", "archived"]
RawOutcome = Literal["success", "failure"]
RawCamera = Literal["front", "birdview", "wrist", "composite"]
SIGNAL_FIELDS = {"action", "ee_pose", "qpos", "qvel", "reward", "object", "privileged_state"}
TASK_ALIASES = {
    "lift": "lift_cube",
    "lift_cube": "lift_cube",
}


def _canonical_task(value: str) -> str:
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    return TASK_ALIASES.get(normalized, normalized)


def _managed_episode(
    episode: RawEpisodeResponse, management: RawEpisodeManagement | None,
) -> RawEpisodeResponse:
    if management is None:
        return episode
    updates: dict[str, Any] = {"management_version": management.version}
    if management.archived:
        updates["review_status"] = "archived"
    return episode.model_copy(update=updates)


async def _management(session: AsyncSession, episode_id: str) -> RawEpisodeManagement | None:
    return await session.get(RawEpisodeManagement, episode_id)


async def _detail_management(
    session: AsyncSession, detail: RawEpisodeDetailResponse,
) -> RawEpisodeDetailResponse:
    management = await _management(session, detail.episode_id)
    managed = _managed_episode(detail, management)
    audits = list((await session.scalars(
        select(RawEpisodeAudit)
        .where(RawEpisodeAudit.episode_id == detail.episode_id)
        .order_by(RawEpisodeAudit.created_at.desc())
        .limit(50)
    )).all())
    return RawEpisodeDetailResponse(
        **managed.model_dump(exclude={"audit"}),
        audit=[
            RawEpisodeAuditResponse(
                id=item.id,
                action=item.action,
                changes=item.changes,
                actor_name=item.actor_name,
                created_at=item.created_at,
            )
            for item in audits
        ],
    )


async def _resolve_source(
    episode_id: str, session: AsyncSession,
) -> tuple[RawSource, Episode | dict[str, Any]]:
    teleop = await session.get(Episode, episode_id)
    if teleop is not None:
        return "teleop", teleop
    record = workspace().scores_by_id().get(episode_id)
    if record is not None:
        return "scripted", record
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy episode")


async def _locked_management(
    session: AsyncSession,
    episode_id: str,
    source: RawSource,
    expected_version: int,
    user: User,
) -> RawEpisodeManagement:
    management = await _management(session, episode_id)
    current_version = management.version if management else 0
    if current_version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Episode đã thay đổi (version hiện tại: {current_version}). Hãy tải lại.",
        )
    if management is None:
        management = RawEpisodeManagement(
            episode_id=episode_id,
            source=source,
            updated_by=user.id,
            version=1,
        )
        session.add(management)
    else:
        management.version += 1
        management.updated_by = user.id
        management.updated_at = datetime.now(UTC)
    return management


def _audit(
    *, episode_id: str, source: RawSource, action: str, changes: dict[str, Any], user: User,
) -> RawEpisodeAudit:
    return RawEpisodeAudit(
        episode_id=episode_id,
        source=source,
        action=action,
        changes=changes,
        actor_id=user.id,
        actor_name=user.display_name or user.username,
    )


def _read_teleop_meta(episode_id: str) -> dict[str, Any]:
    path = storage.meta_path(episode_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _teleop_review_status(value: DemoStatus | str) -> RawReviewStatus:
    status_value = value.value if isinstance(value, DemoStatus) else str(value)
    if status_value == DemoStatus.APPROVED.value:
        return "approved"
    if status_value == DemoStatus.REJECTED.value:
        return "rejected"
    return "pending"


def _teleop_episode(episode: Episode) -> RawEpisodeResponse:
    directory = storage.episode_dir(episode.id)
    meta = _read_teleop_meta(episode.id)
    cameras = set(meta.get("cameras", [])) if isinstance(meta.get("cameras"), list) else set()
    control_hz = meta.get("control_hz")
    outcome = episode.outcome.value if hasattr(episode.outcome, "value") else episode.outcome

    return RawEpisodeResponse(
        episode_id=episode.id,
        display_name=episode.id,
        source="teleop",
        task=_canonical_task(episode.task_name),
        created_at=episode.created_at,
        length=episode.num_frames or 0,
        duration_s=episode.duration_s,
        control_hz=float(control_hz) if isinstance(control_hz, (int, float)) else None,
        size_bytes=episode.size_bytes,
        recorded_success=(outcome == "success") if outcome else None,
        quality=None,
        review_status=_teleop_review_status(episode.status),
        operator_id=episode.operator_id,
        collection_batch_id=(
            str(meta["collection_batch_id"]) if meta.get("collection_batch_id") else None
        ),
        cameras=RawEpisodeCameras(
            front=(directory / storage.FRONT_FILENAME).exists()
            or (directory / "review_front.mp4").exists()
            or "review_front" in cameras,
            birdview=(directory / "birdview.mp4").exists() or "birdview" in cameras,
            wrist=(directory / storage.WRIST_FILENAME).exists()
            or (directory / "robot0_eye_in_hand.mp4").exists()
            or "robot0_eye_in_hand" in cameras,
        ),
        artifact_health="healthy",
    )


def _scripted_review_status(label: dict[str, Any] | None) -> RawReviewStatus:
    if label and label.get("human_decision") == "approved":
        return "approved"
    if label and label.get("human_decision") == "rejected":
        return "rejected"
    return "pending"


def _scripted_episode(
    record: dict[str, Any], label: dict[str, Any] | None,
) -> RawEpisodeResponse:
    provenance = record.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    control_hz_value = provenance.get("control_hz", 20.0)
    control_hz = float(control_hz_value) if isinstance(control_hz_value, (int, float)) else 20.0
    length = int(record.get("length") or 0)
    created_at = None
    try:
        source_path = workspace().resolve_source(str(record.get("source") or ""))
        created_at = datetime.fromtimestamp(source_path.stat().st_mtime, UTC)
    except (FileNotFoundError, OSError, ValueError):
        # Legacy score records do not carry a timestamp. Keeping it unknown is
        # safer than inventing a collection date when the source artifact is gone.
        pass

    return RawEpisodeResponse(
        episode_id=str(record["episode_id"]),
        display_name=str(record.get("display_name") or record.get("demo") or record["episode_id"]),
        source="scripted",
        task=_canonical_task(str(record["task"])),
        created_at=created_at,
        length=length,
        duration_s=length / control_hz,
        control_hz=control_hz,
        size_bytes=None,
        recorded_success=(
            bool(record["recorded_success"]) if record.get("recorded_success") is not None else None
        ),
        quality=record.get("requested_quality"),
        review_status=_scripted_review_status(label),
        operator_id=None,
        collection_batch_id=(
            str(provenance["collection_batch_id"])
            if provenance.get("collection_batch_id")
            else None
        ),
        cameras=RawEpisodeCameras(front=True, birdview=True, wrist=True),
        artifact_health="healthy",
    )


def _artifact(path, *, kind: str, camera: str | None = None) -> RawArtifactResponse:
    exists = path.exists() and path.is_file()
    return RawArtifactResponse(
        name=path.name,
        kind=kind,
        exists=exists,
        size_bytes=path.stat().st_size if exists else None,
        camera=camera,
    )


def _first_artifact(directory, names: tuple[str, ...], *, camera: str) -> RawArtifactResponse:
    for name in names:
        path = directory / name
        if path.exists():
            return _artifact(path, kind="video", camera=camera)
    return _artifact(directory / names[0], kind="video", camera=camera)


def _teleop_video_path(episode_id: str, camera: RawCamera):
    directory = storage.episode_dir(episode_id)
    names = {
        "front": ("review_front.mp4", storage.FRONT_FILENAME),
        "birdview": ("birdview.mp4",),
        "wrist": ("robot0_eye_in_hand.mp4", storage.WRIST_FILENAME),
    }.get(camera)
    if names is None:
        return None
    return next((directory / name for name in names if (directory / name).exists()), None)


def _teleop_detail(episode: Episode) -> RawEpisodeDetailResponse:
    directory = storage.episode_dir(episode.id)
    artifacts = [
        _artifact(storage.meta_path(episode.id), kind="metadata"),
        _artifact(storage.actions_path(episode.id), kind="table"),
        _first_artifact(directory, ("review_front.mp4", storage.FRONT_FILENAME), camera="front"),
        _first_artifact(directory, ("birdview.mp4",), camera="birdview"),
        _first_artifact(
            directory,
            ("robot0_eye_in_hand.mp4", storage.WRIST_FILENAME),
            camera="wrist",
        ),
    ]
    return RawEpisodeDetailResponse(**_teleop_episode(episode).model_dump(), artifacts=artifacts)


def _scripted_detail(record: dict[str, Any], label: dict[str, Any] | None) -> RawEpisodeDetailResponse:
    space = workspace()
    source_name = str(record.get("source") or "dataset.hdf5")
    try:
        dataset_path = space.resolve_source(source_name)
    except FileNotFoundError:
        dataset_path = space.datasets_dir / source_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    artifacts = [
        _artifact(dataset_path, kind="dataset"),
        _artifact(space.video_path(str(record["episode_id"])), kind="video"),
    ]
    return RawEpisodeDetailResponse(
        **_scripted_episode(record, label).model_dump(),
        artifacts=artifacts,
    )


def _sampled_indices(start: int, end: int, max_points: int) -> list[int]:
    length = end - start
    if length <= max_points:
        return list(range(start, end))
    if max_points == 1:
        return [start]
    return [
        start + round(index * (length - 1) / (max_points - 1))
        for index in range(max_points)
    ]


def _labels(prefix: str, width: int) -> list[str]:
    known = {
        "action": ["x", "y", "z", "rx", "ry", "rz", "gripper"],
        "ee_pose": ["x", "y", "z", "qx", "qy", "qz", "qw"],
    }.get(prefix, [])
    return [known[index] if index < len(known) else f"{prefix}_{index}" for index in range(width)]


def _float_row(value: Any) -> list[float]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [float(item) for item in value]


def _teleop_signals(
    episode_id: str,
    requested: set[str],
    start: int,
    end: int | None,
    max_points: int,
) -> RawSignalsResponse:
    import pyarrow.parquet as pq

    path = storage.actions_path(episode_id)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không có actions.parquet")
    schema_names = set(pq.read_schema(path).names)
    available = sorted((schema_names - {"t"}) & SIGNAL_FIELDS)
    selected = sorted(requested & set(available))
    table = pq.read_table(path, columns=["t", *selected])
    total = table.num_rows
    bounded_start = min(start, total)
    bounded_end = total if end is None else min(end, total)
    if bounded_end <= bounded_start:
        raise HTTPException(status_code=422, detail="Khoảng frame không hợp lệ")
    indices = _sampled_indices(bounded_start, bounded_end, max_points)
    times = [float(table["t"][index].as_py()) for index in indices]
    series: dict[str, RawSignalSeries] = {}
    for name in selected:
        values = [_float_row(table[name][index].as_py()) for index in indices]
        width = len(values[0]) if values else 0
        series[name] = RawSignalSeries(labels=_labels(name, width), values=values)
    meta = _read_teleop_meta(episode_id)
    control_hz = float(meta.get("control_hz") or 30.0)
    return RawSignalsResponse(
        episode_id=episode_id,
        source="teleop",
        start=bounded_start,
        end=bounded_end,
        total_frames=total,
        sampled_frames=indices,
        control_hz=control_hz,
        video_stride=1,
        time_basis="recorded",
        t=times,
        available_fields=available,
        signals=series,
    )


def _scripted_signals(
    record: dict[str, Any],
    requested: set[str],
    start: int,
    end: int | None,
    max_points: int,
) -> RawSignalsResponse:
    import h5py
    import numpy as np

    from src.labeling.playback import PlaybackConfig, frame_stride

    space = workspace()
    source = space.resolve_source(str(record["source"]))
    with h5py.File(source, "r") as handle:
        data = handle["data"]
        group = data[str(record["demo"])]
        total = int(group["actions"].shape[0])
        bounded_start = min(start, total)
        bounded_end = total if end is None else min(end, total)
        if bounded_end <= bounded_start:
            raise HTTPException(status_code=422, detail="Khoảng frame không hợp lệ")
        indices = _sampled_indices(bounded_start, bounded_end, max_points)
        index_array = np.asarray(indices, dtype=np.int64)
        obs = group["obs"]
        sources: dict[str, Any] = {
            "action": group.get("actions"),
            "reward": group.get("rewards"),
            "object": obs.get("object"),
            "privileged_state": group.get("states"),
        }
        if "robot0_eef_pos" in obs and "robot0_eef_quat" in obs:
            sources["ee_pose"] = np.concatenate(
                (np.asarray(obs["robot0_eef_pos"]), np.asarray(obs["robot0_eef_quat"])), axis=1,
            )
        if "robot0_joint_pos" in obs:
            parts = [np.asarray(obs["robot0_joint_pos"])]
            if "robot0_gripper_qpos" in obs:
                parts.append(np.asarray(obs["robot0_gripper_qpos"]))
            sources["qpos"] = np.concatenate(parts, axis=1)
        if "robot0_joint_vel" in obs:
            parts = [np.asarray(obs["robot0_joint_vel"])]
            if "robot0_gripper_qvel" in obs:
                parts.append(np.asarray(obs["robot0_gripper_qvel"]))
            sources["qvel"] = np.concatenate(parts, axis=1)
        available = sorted(name for name, dataset in sources.items() if dataset is not None)
        selected = sorted(requested & set(available))
        series: dict[str, RawSignalSeries] = {}
        for name in selected:
            values = [_float_row(row) for row in np.asarray(sources[name])[index_array]]
            width = len(values[0]) if values else 0
            series[name] = RawSignalSeries(labels=_labels(name, width), values=values)

    control_hz = 20.0
    stride = frame_stride(total, PlaybackConfig())
    times = [index / (control_hz * stride) for index in indices]
    return RawSignalsResponse(
        episode_id=str(record["episode_id"]),
        source="scripted",
        start=bounded_start,
        end=bounded_end,
        total_frames=total,
        sampled_frames=indices,
        control_hz=control_hz,
        video_stride=stride,
        time_basis="scripted_playback",
        t=times,
        available_fields=available,
        signals=series,
    )


def _matches(
    episode: RawEpisodeResponse,
    *,
    task: str | None,
    quality: RawQuality | None,
    outcome: RawOutcome | None,
    review_status: RawReviewStatus | None,
    collection_batch_id: str | None,
    search: str | None,
) -> bool:
    if task is not None and episode.task != _canonical_task(task):
        return False
    if quality is not None and episode.quality != quality:
        return False
    if outcome is not None and episode.recorded_success != (outcome == "success"):
        return False
    if review_status is not None and episode.review_status != review_status:
        return False
    if collection_batch_id is not None and episode.collection_batch_id != collection_batch_id:
        return False
    if search is not None:
        needle = search.casefold()
        if needle not in episode.episode_id.casefold() and needle not in episode.display_name.casefold():
            return False
    return True


async def _all_episodes(
    session: AsyncSession, source: RawSource | None,
) -> list[RawEpisodeResponse]:
    items: list[RawEpisodeResponse] = []
    if source in (None, "teleop"):
        teleop = list((await session.scalars(select(Episode).order_by(Episode.created_at.desc()))).all())
        items.extend(_teleop_episode(episode) for episode in teleop)

    if source in (None, "scripted"):
        space = workspace()
        labels = space.labels_by_id()
        items.extend(
            _scripted_episode(record, labels.get(str(record["episode_id"])))
            for record in space.scores()
        )
    management_rows = list((await session.scalars(select(RawEpisodeManagement))).all())
    management_by_id = {item.episode_id: item for item in management_rows}
    return [_managed_episode(item, management_by_id.get(item.episode_id)) for item in items]


@router.get("/episodes", response_model=RawEpisodePageResponse)
async def list_raw_episodes(
    source: RawSource | None = None,
    task: str | None = None,
    quality: RawQuality | None = None,
    outcome: RawOutcome | None = None,
    review_status: RawReviewStatus | None = None,
    collection_batch_id: str | None = Query(default=None, min_length=1, max_length=64),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _user: User = Depends(reviewer_required),
    session: AsyncSession = Depends(get_session),
) -> RawEpisodePageResponse:
    all_items = await _all_episodes(session, source)
    available_tasks = sorted({episode.task for episode in all_items})
    available_batches = sorted({
        episode.collection_batch_id
        for episode in all_items
        if episode.collection_batch_id is not None
    })
    items = [
        episode
        for episode in all_items
        if _matches(
            episode,
            task=task,
            quality=quality,
            outcome=outcome,
            review_status=review_status,
            collection_batch_id=collection_batch_id,
            search=search,
        )
    ]
    total = len(items)
    start = (page - 1) * page_size
    summary = RawEpisodeSummaryResponse(
        total=total,
        teleop=sum(item.source == "teleop" for item in items),
        scripted=sum(item.source == "scripted" for item in items),
        successes=sum(item.recorded_success is True for item in items),
        failures=sum(item.recorded_success is False for item in items),
        pending=sum(item.review_status == "pending" for item in items),
        approved=sum(item.review_status == "approved" for item in items),
        rejected=sum(item.review_status == "rejected" for item in items),
        archived=sum(item.review_status == "archived" for item in items),
        by_task={
            task_name: sum(item.task == task_name for item in items)
            for task_name in sorted({item.task for item in items})
        },
        by_quality={
            quality: sum(item.quality == quality for item in items)
            for quality in ("clean", "good", "medium", "poor")
        },
        by_batch={
            batch: sum(item.collection_batch_id == batch for item in items)
            for batch in sorted({
                item.collection_batch_id
                for item in items
                if item.collection_batch_id is not None
            })
        },
        by_day={
            day: {
                source: sum(
                    item.created_at is not None
                    and item.created_at.date().isoformat() == day
                    and item.source == source
                    for item in items
                )
                for source in ("teleop", "scripted")
            }
            for day in sorted({
                item.created_at.date().isoformat()
                for item in items
                if item.created_at is not None
            })
        },
        undated=sum(item.created_at is None for item in items),
    )
    return RawEpisodePageResponse(
        items=items[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
        summary=summary,
        available_tasks=available_tasks,
        available_batches=available_batches,
    )


@router.get("/episodes/{episode_id}/video/{camera}", operation_id="raw_episode_video")
@router.head("/episodes/{episode_id}/video/{camera}", operation_id="raw_episode_video_head")
async def raw_episode_video(
    episode_id: str,
    camera: RawCamera,
    request: Request,
    user: User = Depends(current_user_allow_query_token),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if ROLE_RANK[UserRole(user.role)] < ROLE_RANK[UserRole.REVIEWER]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Không đủ quyền")

    teleop = await session.get(Episode, episode_id)
    if teleop is not None:
        path = _teleop_video_path(episode_id, camera)
        if path is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không có camera này")
        return stream_file_range(path, request, media_type="video/mp4")

    space = workspace()
    if episode_id not in space.scores_by_id():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy episode")
    if camera != "composite":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scripted episode dùng video composite ba góc nhìn",
        )
    path = space.video_path(episode_id)
    if not path.exists():
        raise HTTPException(status_code=425, detail="Video scripted chưa render xong")
    return stream_file_range(path, request, media_type="video/mp4")


@router.post("/episodes/{episode_id}/video/prepare", status_code=status.HTTP_202_ACCEPTED)
async def prepare_raw_episode_video(
    episode_id: str,
    _user: User = Depends(reviewer_required),
) -> dict[str, Any]:
    space = workspace()
    if episode_id not in space.scores_by_id():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy scripted episode")
    if space.video_path(episode_id).exists():
        return {"status": "ready", "episode_id": episode_id}
    from src.labeling.jobs import submit_render

    job = submit_render(space, episode_id)
    return {"status": "rendering", "episode_id": episode_id, "job": job.as_dict()}


@router.get("/episodes/{episode_id}/signals", response_model=RawSignalsResponse)
async def raw_episode_signals(
    episode_id: str,
    fields: str = Query(default="action,ee_pose,qpos,qvel,reward", min_length=1),
    start: int = Query(default=0, ge=0),
    end: int | None = Query(default=None, ge=1),
    max_points: int = Query(default=600, ge=2, le=2000),
    _user: User = Depends(reviewer_required),
    session: AsyncSession = Depends(get_session),
) -> RawSignalsResponse:
    requested = {name.strip() for name in fields.split(",") if name.strip()}
    unknown = requested - SIGNAL_FIELDS
    if unknown:
        raise HTTPException(status_code=422, detail=f"Signal không hỗ trợ: {', '.join(sorted(unknown))}")

    teleop = await session.get(Episode, episode_id)
    if teleop is not None:
        return _teleop_signals(episode_id, requested, start, end, max_points)

    record = workspace().scores_by_id().get(episode_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy episode")
    return _scripted_signals(record, requested, start, end, max_points)


@router.get("/episodes/{episode_id}", response_model=RawEpisodeDetailResponse)
async def raw_episode_detail(
    episode_id: str,
    _user: User = Depends(reviewer_required),
    session: AsyncSession = Depends(get_session),
) -> RawEpisodeDetailResponse:
    teleop = await session.get(Episode, episode_id)
    if teleop is not None:
        return await _detail_management(session, _teleop_detail(teleop))

    space = workspace()
    record = space.scores_by_id().get(episode_id)
    if record is not None:
        return await _detail_management(
            session, _scripted_detail(record, space.labels_by_id().get(episode_id)),
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy episode")


@router.post("/episodes/{episode_id}/archive", response_model=RawEpisodeDetailResponse)
async def archive_raw_episode(
    episode_id: str,
    payload: RawEpisodeArchiveUpdate,
    user: User = Depends(require_min_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> RawEpisodeDetailResponse:
    source, _record = await _resolve_source(episode_id, session)
    management = await _locked_management(
        session, episode_id, source, payload.expected_version, user,
    )
    previous = management.archived
    management.archived = payload.archived
    session.add(_audit(
        episode_id=episode_id, source=source,
        action="archived" if payload.archived else "restored",
        changes={"before": previous, "after": payload.archived}, user=user,
    ))
    await session.commit()
    return await raw_episode_detail(episode_id, user, session)
