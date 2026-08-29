"""Persistent subprocess jobs for RoboMimic training."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.models.enums import JobStatus
from src.models.schemas import (
    TrainingCheckpointResponse,
    TrainingJobRequest,
    TrainingJobResponse,
)
from src.training.runpod_runner import RunPodError

# Số lần hỏi trạng thái hỏng liên tiếp trước khi coi là mất liên lạc thật.
# Với `runpod_poll_interval_s` mặc định 10 giây thì đây là khoảng 5 phút —
# đủ dài để đi qua một lần rớt mạng, đủ ngắn để không treo job hàng giờ.
_MAX_POLL_FAILURES = 30

_EPOCH_BLOCK = re.compile(
    r"(?P<kind>Train|Validation) Epoch (?P<epoch>\d+)\s*\n(?P<body>\{.*?\})",
    re.DOTALL,
)
_CHECKPOINT_EPOCH = re.compile(r"^model_epoch_(?P<epoch>\d+)(?=_|\.pth$)")
_CHECKPOINT_VALIDATION = re.compile(
    r"_best_validation_(?P<loss>[0-9.eE+-]+)(?=_|\.pth$)"
)
ACCELERATOR_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def parse_training_progress(text: str) -> dict[str, int | float | None]:
    """Extract the latest complete RoboMimic epoch blocks from a text log."""
    epoch = 0
    train_loss: float | None = None
    validation_loss: float | None = None
    for match in _EPOCH_BLOCK.finditer(text.replace("\r\n", "\n")):
        try:
            values = json.loads(match.group("body"))
            loss = float(values["Loss"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        block_epoch = int(match.group("epoch"))
        epoch = max(epoch, block_epoch)
        if match.group("kind") == "Train":
            train_loss = loss
        else:
            validation_loss = loss
    return {"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss}


def discover_checkpoints(output_dir: Path, current_epoch: int = 0) -> list[dict[str, Any]]:
    """Return stable metadata for checkpoint files below one managed job directory."""
    found: list[dict[str, Any]] = []
    if not output_dir.exists():
        return found
    for path in output_dir.rglob("*.pth"):
        if not path.is_file() or path.name == "last_bak.pth":
            continue
        relative = path.relative_to(output_dir).as_posix()
        epoch_match = _CHECKPOINT_EPOCH.search(path.name)
        validation_match = _CHECKPOINT_VALIDATION.search(path.name)
        loss = float(validation_match.group("loss")) if validation_match else None
        # ``last.pth`` has no epoch in its filename and represents the current
        # run state. Model checkpoints must retain the epoch encoded at the
        # start of their filename even when RoboMimic appends rollout tags.
        epoch = int(epoch_match.group("epoch")) if epoch_match else current_epoch
        stat = path.stat()
        found.append({
            "id": hashlib.sha256(relative.encode()).hexdigest()[:20],
            "filename": relative,
            "epoch": epoch,
            "validation_loss": loss,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "is_best_validation": False,
            "is_latest": False,
        })
    if found:
        latest = next(
            (item for item in found if Path(item["filename"]).name == "last.pth"),
            max(found, key=lambda item: (item["created_at"], item["filename"])),
        )
        latest["is_latest"] = True
        with_loss = [item for item in found if item["validation_loss"] is not None]
        if with_loss:
            min(with_loss, key=lambda item: item["validation_loss"])["is_best_validation"] = True
    return sorted(found, key=lambda item: (item["epoch"], item["filename"]), reverse=True)


def build_training_command(
    config: dict[str, Any],
    *,
    python_executable: str,
    training_script: str,
    dataset_path: str,
    output_dir: str,
) -> list[str]:
    """Dựng dòng lệnh gọi `train_robomimic_bc.py`.

    Hàm thuần, tách khỏi `TrainingJobManager` vì handler chạy trên máy GPU thuê
    (`runpod_handler.py`) phải dựng ĐÚNG dòng lệnh này. Viết lại lần hai ở đó
    thì thêm một cờ mới vào `TrainingJobRequest` là chắc chắn quên một chỗ.
    """
    command = [
        python_executable,
        training_script,
        "--dataset", dataset_path,
        "--output-dir", output_dir,
        "--name", str(config["name"]),
        "--policy", str(config["policy"]),
        "--epochs", str(config["epochs"]),
        "--batch-size", str(config["batch_size"]),
        "--num-workers", str(config["num_workers"]),
        "--device", str(config["device"]),
        "--learning-rate", str(config["learning_rate"]),
        "--seed", str(config["seed"]),
        "--sequence-length", str(config["sequence_length"]),
        "--rnn-hidden-dim", str(config["rnn_hidden_dim"]),
        "--rnn-layers", str(config["rnn_layers"]),
        "--observation-profile", str(config["observation_profile"]),
        (
            "--normalize-observations"
            if config.get("normalize_observations")
            else "--no-normalize-observations"
        ),
        "--rollout-enabled" if config.get("rollout_enabled") else "--no-rollout-enabled",
        "--rollout-every-n-epochs", str(config["rollout_every_n_epochs"]),
        "--rollout-episodes", str(config["rollout_episodes"]),
        "--rollout-horizon", str(config["rollout_horizon"]),
        "--wandb-enabled" if config.get("wandb_enabled") else "--no-wandb-enabled",
        "--wandb-project", str(config.get("wandb_project", "telecollect-robot-learning")),
    ]
    if config.get("wandb_entity"):
        command.extend(["--wandb-entity", str(config["wandb_entity"])])
    if config.get("save_every_n_epochs") is not None:
        command.extend(["--save-every-n-epochs", str(config["save_every_n_epochs"])])
    return command


class TrainingJobManager:
    """Run training outside the API event loop and persist every state change.

    The single-worker executor deliberately serializes jobs. This guarantees
    that two requests cannot compete for the same GPU and is a safe MVP for
    CPU jobs as well.
    """

    def __init__(
        self,
        root: Path,
        *,
        repo_root: Path | None = None,
        python_executable: str | None = None,
        training_script: Path | None = None,
        runner: Any | None = None,
    ) -> None:
        # `runner=None` giữ nguyên đường subprocess cũ. Truyền `RunPodRunner`
        # vào để huấn luyện trên GPU thuê thay vì trên máy chạy backend.
        self.runner = runner
        self.root = root.resolve()
        self.repo_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.python_executable = python_executable or sys.executable
        self.training_script = (
            training_script or self.repo_root / "scripts" / "train_robomimic_bc.py"
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="training-job")
        self._load_existing()

    def _job_dir(self, job_id: str) -> Path:
        path = (self.root / job_id).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Training job id không hợp lệ")
        return path

    #: Kept in the in-memory record so the subprocess can be given it, and
    #: stripped before the record reaches disk. Encrypting the key in the
    #: database only to write it out here in clear text would undo the point.
    _UNPERSISTED_KEYS = ("wandb_api_key",)

    def _write(self, record: dict[str, Any]) -> None:
        job_dir = self._job_dir(record["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / "job.json"
        temporary = job_dir / "job.json.tmp"
        persisted = {
            key: value
            for key, value in record.items()
            if key not in self._UNPERSISTED_KEYS
        }
        temporary.write_text(
            json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, target)

    def _load_existing(self) -> None:
        for path in self.root.glob("*/job.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["status"] in {JobStatus.PENDING, JobStatus.RUNNING}:
                    record["status"] = JobStatus.FAILED
                    record["finished_at"] = _now()
                    record["error"] = (
                        "Backend đã khởi động lại khi job đang chạy; "
                        "tiến trình cũ không thể được quản lý an toàn."
                    )
                    self._write(record)
                self._jobs[record["id"]] = record
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

    @staticmethod
    def _environment(record: dict[str, Any]) -> dict[str, str]:
        """Environment for one training subprocess.

        The W&B key travels here rather than on the command line: arguments are
        visible to anyone who can list processes, and they end up in logs. It is
        also per-job, not per-server — each job carries the key of the person
        who started it.
        """

        environment = dict(os.environ)
        key = record.get("wandb_api_key")
        if key:
            environment["WANDB_API_KEY"] = str(key)
        else:
            # Never let a job silently log to whoever ran the server: an
            # inherited key would send someone's run to a stranger's account.
            environment.pop("WANDB_API_KEY", None)
        return environment

    def submit(
        self,
        config: TrainingJobRequest,
        dataset_path: Path,
        *,
        wandb_api_key: str | None = None,
        owner_id: str | None = None,
    ) -> TrainingJobResponse:
        job_id = uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=False)
        record: dict[str, Any] = {
            "id": job_id,
            # Ai bấm Train — cần để cộng giờ GPU vào đúng tài khoản khi job
            # kết thúc. `None` với job cũ và với runner local (không tốn giờ thuê).
            "owner_id": owner_id,
            "dataset_id": config.dataset_id,
            "dataset_path": str(dataset_path.resolve()),
            "name": config.name,
            "status": JobStatus.PENDING,
            "config": config.model_dump(mode="json"),
            "output_dir": str(output_dir),
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "epoch": 0,
            "train_loss": None,
            "validation_loss": None,
            "error": None,
            "checkpoints": [],
            "cancel_requested": False,
            "runner_state": {},
            # Stripped by `_write`; reaches the subprocess through the
            # environment, never the command line or job.json.
            "wandb_api_key": wandb_api_key or "",
        }
        with self._lock:
            self._jobs[job_id] = record
            self._write(record)
            self._futures[job_id] = self._executor.submit(self._run, job_id)
        return self._response(record)

    def _command(self, record: dict[str, Any]) -> list[str]:
        return build_training_command(
            record["config"],
            python_executable=self.python_executable,
            training_script=str(self.training_script),
            dataset_path=str(record["dataset_path"]),
            output_dir=str(record["output_dir"]),
        )

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record["cancel_requested"]:
                self._finish_cancelled(record)
                return
            record["status"] = JobStatus.RUNNING
            record["started_at"] = _now()
            self._write(record)
        if self.runner is not None:
            self._run_remote(job_id)
            return
        log_path = self._job_dir(job_id) / "stdout.log"
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                with ACCELERATOR_LOCK:
                    with self._lock:
                        if self._jobs[job_id]["cancel_requested"]:
                            self._finish_cancelled(self._jobs[job_id])
                            return
                    process = subprocess.Popen(
                        self._command(record),
                        cwd=self.repo_root,
                        env=self._environment(record),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        text=True,
                        creationflags=creationflags,
                    )
                    with self._lock:
                        self._processes[job_id] = process
                    return_code = process.wait()
            with self._lock:
                record = self._jobs[job_id]
                if record["cancel_requested"]:
                    self._finish_cancelled(record)
                elif return_code == 0:
                    record["status"] = JobStatus.SUCCEEDED
                    record["finished_at"] = _now()
                    self._write(record)
                else:
                    record["status"] = JobStatus.FAILED
                    record["finished_at"] = _now()
                    record["error"] = f"Training process exited with code {return_code}"
                    self._write(record)
        except Exception as exc:  # subprocess boundary: persist any launch/runtime error
            with self._lock:
                record = self._jobs[job_id]
                record["status"] = JobStatus.FAILED
                record["finished_at"] = _now()
                record["error"] = str(exc)
                self._write(record)
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _run_remote(self, job_id: str) -> None:
        """Giao job cho `self.runner` rồi hỏi trạng thái tới khi kết thúc.

        Chạy trong cùng executor một worker như đường subprocess, nên hai job
        không tranh nhau. Log và checkpoint KHÔNG đi qua đây — máy GPU tự đẩy
        về ba endpoint máy, `_refresh()` nhặt được ngay khi file rơi vào
        `output_dir`.
        """
        settings = get_settings()
        deadline = time.monotonic() + settings.runpod_max_hours * 3600
        try:
            with self._lock:
                record = self._jobs[job_id]
                if record["cancel_requested"]:
                    self._finish_cancelled(record)
                    return
                state = self.runner.start(record)
                record["runner_state"] = state
                self._write(record)
            poll_failures = 0
            while True:
                time.sleep(settings.runpod_poll_interval_s)
                with self._lock:
                    record = self._jobs[job_id]
                    if record["cancel_requested"]:
                        break
                    snapshot = dict(record)
                try:
                    result = self.runner.poll(snapshot)
                except RunPodError as exc:
                    # Hỏi trạng thái hỏng KHÔNG có nghĩa là training hỏng: máy
                    # GPU vẫn chạy và vẫn đẩy log về. Một lần rớt TLS mà đánh
                    # dấu thất bại là vứt cả lần chạy đã trả tiền — đã gặp với
                    # `_ssl.c:999 handshake timed out` trong khi job về đích.
                    poll_failures += 1
                    if poll_failures < _MAX_POLL_FAILURES:
                        continue
                    raise RunPodError(
                        f"Mất liên lạc với RunPod sau {poll_failures} lần hỏi: {exc}"
                    ) from exc
                poll_failures = 0
                status = result["status"]
                if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    with self._lock:
                        record = self._jobs[job_id]
                        record["status"] = status
                        record["finished_at"] = _now()
                        record["error"] = result.get("error")
                        self._write(record)
                    return
                if time.monotonic() > deadline:
                    # Trần thời gian: job vẫn chạy nhưng đã hết ngân sách giờ.
                    # Hủy bên RunPod trước, nếu không worker cứ chạy tiếp và
                    # hóa đơn cứ tăng.
                    #
                    # Đánh dấu CANCELLED chứ không phải FAILED: training lưu
                    # checkpoint theo từng epoch, nên hết giờ là dừng đúng lúc
                    # chứ không phải hỏng — checkpoint đã lưu vẫn dùng được để
                    # Evaluate hoặc train tiếp.
                    with self._lock:
                        snapshot = dict(self._jobs[job_id])
                    self._safe_cancel(snapshot)
                    with self._lock:
                        record = self._jobs[job_id]
                        record["status"] = JobStatus.CANCELLED
                        record["finished_at"] = _now()
                        record["error"] = (
                            f"Hết {settings.runpod_max_hours} giờ GPU — đã dừng, "
                            "checkpoint đã lưu vẫn dùng được"
                        )
                        self._write(record)
                    return
            with self._lock:
                snapshot = dict(self._jobs[job_id])
            self._safe_cancel(snapshot)
            with self._lock:
                self._finish_cancelled(self._jobs[job_id])
        except Exception as exc:  # ranh giới mạng: mọi lỗi phải nằm lại trong job
            with self._lock:
                record = self._jobs[job_id]
                record["status"] = JobStatus.FAILED
                record["finished_at"] = _now()
                record["error"] = str(exc)
                self._write(record)

    def _safe_cancel(self, record: dict[str, Any]) -> None:
        """Hủy phía nhà cung cấp; lỗi ở đây không được che mất kết quả job."""
        try:
            self.runner.cancel(record)
        except Exception:
            pass

    def _finish_cancelled(self, record: dict[str, Any]) -> None:
        # A user may cancel while optional post-training work (for example,
        # W&B artifact upload) is running. Preserve a completed model as a
        # successful training run so its local checkpoints remain evaluable.
        expected_epochs = int(record.get("config", {}).get("epochs", 0))
        output_dir = Path(record["output_dir"])
        training_completed = (
            expected_epochs > 0
            and int(record.get("epoch", 0)) >= expected_epochs
            and any(output_dir.rglob("*.pth"))
        )
        if training_completed:
            record["status"] = JobStatus.SUCCEEDED
            record["cancel_requested"] = False
            record["finished_at"] = _now()
            self._write(record)
            return
        record["status"] = JobStatus.CANCELLED
        record["finished_at"] = _now()
        self._write(record)

    def list(self) -> list[TrainingJobResponse]:
        with self._lock:
            missing = [
                job_id
                for job_id in self._jobs
                if not (self._job_dir(job_id) / "job.json").is_file()
            ]
            for job_id in missing:
                self._jobs.pop(job_id, None)
            for record in self._jobs.values():
                self._refresh(record)
            records = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return [self._response(record) for record in records]

    def get(self, job_id: str) -> TrainingJobResponse | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record and not (self._job_dir(job_id) / "job.json").is_file():
                self._jobs.pop(job_id, None)
                return None
            if record:
                self._refresh(record)
            return self._response(record) if record else None

    def checkpoint(
        self, job_id: str, checkpoint_id: str
    ) -> TrainingCheckpointResponse | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            self._refresh(record)
            item = next(
                (item for item in record["checkpoints"] if item["id"] == checkpoint_id),
                None,
            )
            return TrainingCheckpointResponse.model_validate(item) if item else None

    def checkpoint_path(self, job_id: str, checkpoint_id: str) -> Path | None:
        """Resolve a discovered checkpoint without accepting a path from callers."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            self._refresh(record)
            item = next(
                (item for item in record["checkpoints"] if item["id"] == checkpoint_id),
                None,
            )
            if item is None:
                return None
            output_dir = Path(record["output_dir"]).resolve()
            path = (output_dir / item["filename"]).resolve()
            if not path.is_relative_to(output_dir) or not path.is_file():
                return None
            return path

    def cancel(self, job_id: str) -> TrainingJobResponse | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            if record["status"] not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return self._response(record)
            record["cancel_requested"] = True
            process = self._processes.get(job_id)
            future = self._futures.get(job_id)
            if process is not None:
                process.terminate()
            elif future is not None and future.cancel():
                self._finish_cancelled(record)
            else:
                self._write(record)
            return self._response(record)

    def delete(self, job_id: str) -> bool:
        """Delete one finished job and its managed artifacts."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return False
            if record["status"] in {JobStatus.PENDING, JobStatus.RUNNING}:
                raise ValueError("Không thể xóa training job đang chạy")
            job_dir = self._job_dir(job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir)
            self._jobs.pop(job_id, None)
            self._processes.pop(job_id, None)
            self._futures.pop(job_id, None)
            return True

    def dataset_path(self, job_id: str) -> Path | None:
        """File HDF5 của job, để máy GPU thuê tải về."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            path = Path(record["dataset_path"])
            return path if path.is_file() else None

    def append_log(self, job_id: str, text: str) -> bool:
        """Nối log máy GPU đẩy về vào đúng file mà `_refresh()` đang đọc."""
        with self._lock:
            if job_id not in self._jobs:
                return False
        path = self._job_dir(job_id) / "stdout.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
        return True

    def artifact_path(self, job_id: str, filename: str) -> Path | None:
        """Vị trí ghi một checkpoint máy GPU đẩy về.

        `filename` đến từ máy thuê nên KHÔNG được tin: chỉ nhận `.pth` và bắt
        buộc kết quả nằm trong `output_dir` — chặn `../` thoát ra ngoài.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
        if not filename.endswith(".pth"):
            return None
        output_dir = Path(record["output_dir"]).resolve()
        path = (output_dir / filename).resolve()
        if not path.is_relative_to(output_dir):
            return None
        return path

    def log(self, job_id: str) -> str | None:
        if job_id not in self._jobs:
            return None
        path = self._job_dir(job_id) / "stdout.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def _refresh(self, record: dict[str, Any]) -> None:
        """Refresh progress and artifacts on demand without trusting client paths."""
        before = (
            record["epoch"],
            record["train_loss"],
            record["validation_loss"],
            record["checkpoints"],
        )
        log_candidates = [self._job_dir(record["id"]) / "stdout.log"]
        output_dir = Path(record["output_dir"])
        log_candidates.extend(output_dir.rglob("logs/log.txt") if output_dir.exists() else [])
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in log_candidates
            if path.is_file()
        )
        progress = parse_training_progress(text)
        if progress["epoch"]:
            record.update(progress)
        record["checkpoints"] = discover_checkpoints(output_dir, int(record["epoch"]))
        after = (
            record["epoch"],
            record["train_loss"],
            record["validation_loss"],
            record["checkpoints"],
        )
        if after != before:
            self._write(record)

    @staticmethod
    def _response(record: dict[str, Any]) -> TrainingJobResponse:
        public = {
            key: value
            for key, value in record.items()
            if key not in {"dataset_path", "cancel_requested", "runner_state"}
        }
        return TrainingJobResponse.model_validate(public)
