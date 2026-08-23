"""Create, inspect and cancel persistent RoboMimic training jobs."""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.db import Dataset, User, get_session
from src.models.enums import DatasetStatus, UserRole
from src.models.schemas import (
    EvalResultResponse,
    EvaluationJobRequest,
    TrainingCheckpointResponse,
    TrainingJobRequest,
    TrainingJobResponse,
)
from src.services import storage
from src.services.security import current_user_allow_query_token, require_min_role
from src.training.evaluation_jobs import EvaluationJobManager
from src.training.jobs import TrainingJobManager

router = APIRouter(prefix="/training", tags=["training"])


def _require_training_enabled() -> None:
    if not get_settings().training_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Training chưa khả dụng trong bản CPU staging",
        )


@lru_cache
def job_manager() -> TrainingJobManager:
    return TrainingJobManager(Path(get_settings().storage_dir) / "training")


@lru_cache
def evaluation_manager() -> EvaluationJobManager:
    root = Path(get_settings().storage_dir) / "training"
    return EvaluationJobManager(root, job_manager())


def _validated_dataset_path(dataset: Dataset) -> Path:
    """Accept only a ready RoboMimic file managed by TeleCollect storage."""
    if dataset.status != DatasetStatus.READY:
        raise HTTPException(status_code=409, detail="Dataset chưa ở trạng thái ready")
    if dataset.format != "robomimic" or not dataset.zip_path:
        raise HTTPException(status_code=422, detail="Training chỉ nhận dataset RoboMimic HDF5")
    expected = storage.dataset_hdf5_path(dataset.id).resolve()
    actual = Path(dataset.zip_path).resolve()
    if actual != expected or actual.suffix.lower() not in {".hdf5", ".h5"}:
        raise HTTPException(
            status_code=422,
            detail="Đường dẫn dataset không thuộc vùng lưu trữ được quản lý",
        )
    if not actual.is_file():
        raise HTTPException(status_code=409, detail="Không tìm thấy file HDF5 của dataset")
    return actual


def _job_or_404(job_id: str) -> TrainingJobResponse:
    job = job_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy training job")
    return job


@router.post("/jobs", response_model=TrainingJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_training_job(
    body: TrainingJobRequest,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> TrainingJobResponse:
    _require_training_enabled()
    dataset = await session.get(Dataset, body.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy dataset")
    return job_manager().submit(body, _validated_dataset_path(dataset))


@router.get("/jobs", response_model=list[TrainingJobResponse])
async def list_training_jobs(
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> list[TrainingJobResponse]:
    return job_manager().list()


@router.get("/jobs/{job_id}", response_model=TrainingJobResponse)
async def get_training_job(
    job_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> TrainingJobResponse:
    return _job_or_404(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=TrainingJobResponse)
async def cancel_training_job(
    job_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> TrainingJobResponse:
    job = job_manager().cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy training job")
    return job


@router.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
async def get_training_log(
    job_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> str:
    log = job_manager().log(job_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy training job")
    return log


@router.get(
    "/jobs/{job_id}/checkpoints/{checkpoint_id}",
    response_model=TrainingCheckpointResponse,
)
async def get_training_checkpoint(
    job_id: str,
    checkpoint_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> TrainingCheckpointResponse:
    if job_manager().get(job_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy training job")
    checkpoint = job_manager().checkpoint(job_id, checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy checkpoint trong training job")
    return checkpoint


@router.post(
    "/jobs/{job_id}/evaluations",
    response_model=EvalResultResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_evaluation(
    job_id: str,
    body: EvaluationJobRequest,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> EvalResultResponse:
    _require_training_enabled()
    if body.training_run_id != job_id:
        raise HTTPException(status_code=422, detail="training_run_id không khớp URL")
    training_job = _job_or_404(job_id)
    if training_job.status != "succeeded":
        raise HTTPException(status_code=409, detail="Training job chưa hoàn thành thành công")
    checkpoint_path = job_manager().checkpoint_path(job_id, body.checkpoint_id)
    if checkpoint_path is None:
        raise HTTPException(
            status_code=404,
            detail="Checkpoint không tồn tại hoặc không thuộc training job này",
        )
    return evaluation_manager().submit(body)


@router.get("/evaluations", response_model=list[EvalResultResponse])
async def list_evaluations(
    training_run_id: str | None = Query(default=None),
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> list[EvalResultResponse]:
    return evaluation_manager().list(training_run_id)


@router.get("/evaluations/{evaluation_id}", response_model=EvalResultResponse)
async def get_evaluation(
    evaluation_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> EvalResultResponse:
    result = evaluation_manager().get(evaluation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy evaluation job")
    return result


@router.post("/evaluations/{evaluation_id}/cancel", response_model=EvalResultResponse)
async def cancel_evaluation(
    evaluation_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> EvalResultResponse:
    result = evaluation_manager().cancel(evaluation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy evaluation job")
    return result


@router.get("/evaluations/{evaluation_id}/log", response_class=PlainTextResponse)
async def get_evaluation_log(
    evaluation_id: str,
    _user: User = Depends(require_min_role(UserRole.REVIEWER)),
) -> str:
    log = evaluation_manager().log(evaluation_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy evaluation job")
    return log


@router.get("/evaluations/{evaluation_id}/videos/{filename}")
async def get_evaluation_video(
    evaluation_id: str,
    filename: str,
    user: User = Depends(current_user_allow_query_token),
) -> FileResponse:
    if UserRole(user.role) not in {UserRole.REVIEWER, UserRole.ADMIN}:
        raise HTTPException(status_code=403, detail="Không đủ quyền")
    path = evaluation_manager().video(evaluation_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy video evaluation")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
