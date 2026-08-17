"""Persistent subprocess jobs for RoboMimic training."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models.enums import JobStatus
from src.models.schemas import (
    TrainingCheckpointResponse,
    TrainingJobRequest,
    TrainingJobResponse,
)

_EPOCH_BLOCK = re.compile(
    r"(?P<kind>Train|Validation) Epoch (?P<epoch>\d+)\s*\n(?P<body>\{.*?\})",
    re.DOTALL,
)
_CHECKPOINT_NAME = re.compile(
    r"model_epoch_(?P<epoch>\d+)(?:_best_validation_(?P<loss>[0-9.eE+-]+))?\.pth$"
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
        match = _CHECKPOINT_NAME.fullmatch(path.name)
        loss = float(match.group("loss")) if match and match.group("loss") else None
        epoch = int(match.group("epoch")) if match else current_epoch
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
        max(found, key=lambda item: (item["created_at"], item["filename"]))["is_latest"] = True
        with_loss = [item for item in found if item["validation_loss"] is not None]
        if with_loss:
            min(with_loss, key=lambda item: item["validation_loss"])["is_best_validation"] = True
    return sorted(found, key=lambda item: (item["epoch"], item["filename"]), reverse=True)


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
    ) -> None:
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

    def _write(self, record: dict[str, Any]) -> None:
        job_dir = self._job_dir(record["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / "job.json"
        temporary = job_dir / "job.json.tmp"
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
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

    def submit(self, config: TrainingJobRequest, dataset_path: Path) -> TrainingJobResponse:
        job_id = uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=False)
        record: dict[str, Any] = {
            "id": job_id,
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
        }
        with self._lock:
            self._jobs[job_id] = record
            self._write(record)
            self._futures[job_id] = self._executor.submit(self._run, job_id)
        return self._response(record)

    def _command(self, record: dict[str, Any]) -> list[str]:
        config = record["config"]
        command = [
            self.python_executable,
            str(self.training_script),
            "--dataset", str(record["dataset_path"]),
            "--output-dir", str(record["output_dir"]),
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
        ]
        if config.get("save_every_n_epochs") is not None:
            command.extend(["--save-every-n-epochs", str(config["save_every_n_epochs"])])
        return command

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record["cancel_requested"]:
                self._finish_cancelled(record)
                return
            record["status"] = JobStatus.RUNNING
            record["started_at"] = _now()
            self._write(record)
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

    def _finish_cancelled(self, record: dict[str, Any]) -> None:
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
        public = {key: value for key, value in record.items() if key not in {"dataset_path", "cancel_requested"}}
        return TrainingJobResponse.model_validate(public)
