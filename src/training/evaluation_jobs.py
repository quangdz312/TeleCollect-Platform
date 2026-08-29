"""Persistent, cancellable RoboMimic rollout jobs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models.enums import JobStatus
from src.models.schemas import EvalResultResponse, EvaluationJobRequest
from src.training.jobs import ACCELERATOR_LOCK, TrainingJobManager


def _now() -> str:
    return datetime.now(UTC).isoformat()


def split_rollouts(seed: int, count: int, workers: int) -> list[tuple[int, int]]:
    """Chia `count` rollout thành các dải `(seed đầu, số lượng)` liên tiếp.

    Dải liên tiếp chứ không xen kẽ, để mỗi process nhận đúng một đoạn seed
    dùng được luôn với `--seed` và `--n-rollouts` của script hiện có.
    """
    workers = max(1, min(workers, count))
    base, remainder = divmod(count, workers)
    chunks: list[tuple[int, int]] = []
    start = seed
    for index in range(workers):
        size = base + (1 if index < remainder else 0)
        chunks.append((start, size))
        start += size
    return chunks


def merge_results(parts: list[dict[str, Any]], requested: int) -> dict[str, Any]:
    """Gộp `result.json` của từng process thành một, đúng schema cũ.

    Bốn số tổng hợp phải tính lại từ danh sách gộp — cộng trung bình của các
    phần chỉ đúng khi các phần bằng nhau, mà chúng không bằng nhau khi số
    rollout không chia hết cho số process.
    """
    episodes: list[dict[str, Any]] = []
    task_name = "unknown"
    for part in parts:
        if part.get("task_name") and part["task_name"] != "unknown":
            task_name = part["task_name"]
        episodes.extend(part.get("episodes", []))
    episodes.sort(key=lambda item: int(item.get("seed", 0)))
    completed = len(episodes)
    successes = sum(1 for item in episodes if item.get("success"))
    return {
        "task_name": task_name,
        "num_episodes": requested,
        "completed_episodes": completed,
        "success_rate": successes / completed if completed else None,
        "mean_episode_length": (
            sum(int(item.get("steps", 0)) for item in episodes) / completed
            if completed
            else None
        ),
        "episodes": episodes,
    }


class EvaluationJobManager:
    def __init__(
        self,
        training_root: Path,
        training_jobs: TrainingJobManager,
        *,
        repo_root: Path | None = None,
        python_executable: str | None = None,
        evaluation_script: Path | None = None,
    ) -> None:
        self.training_root = training_root.resolve()
        self.training_jobs = training_jobs
        self.repo_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.python_executable = python_executable or sys.executable
        self.evaluation_script = (
            evaluation_script or self.repo_root / "scripts" / "evaluate_robomimic.py"
        ).resolve()
        self.training_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, list[subprocess.Popen[str]]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evaluation-job")
        self._load_existing()

    def _job_dir(self, training_run_id: str, evaluation_id: str) -> Path:
        path = (
            self.training_root / training_run_id / "evaluations" / evaluation_id
        ).resolve()
        if not path.is_relative_to(self.training_root):
            raise ValueError("Evaluation job id không hợp lệ")
        return path

    def _write(self, record: dict[str, Any]) -> None:
        directory = self._job_dir(record["training_run_id"], record["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "job.json.tmp"
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, directory / "job.json")

    def _load_existing(self) -> None:
        for path in self.training_root.glob("*/evaluations/*/job.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["status"] in {JobStatus.PENDING, JobStatus.RUNNING}:
                    record["status"] = JobStatus.FAILED
                    record["finished_at"] = _now()
                    record["error"] = "Backend đã khởi động lại khi evaluation đang chạy."
                    self._write(record)
                self._refresh(record)
                self._jobs[record["id"]] = record
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

    def submit(self, config: EvaluationJobRequest) -> EvalResultResponse:
        checkpoint_path = self.training_jobs.checkpoint_path(
            config.training_run_id, config.checkpoint_id
        )
        if checkpoint_path is None:
            raise ValueError("Checkpoint không tồn tại hoặc không thuộc training job")
        evaluation_id = uuid.uuid4().hex
        directory = self._job_dir(config.training_run_id, evaluation_id)
        (directory / "videos").mkdir(parents=True, exist_ok=False)
        record: dict[str, Any] = {
            "id": evaluation_id,
            "training_run_id": config.training_run_id,
            "checkpoint_id": config.checkpoint_id,
            "checkpoint_path": str(checkpoint_path.resolve()),
            "config": config.model_dump(mode="json"),
            "task_name": "unknown",
            "status": JobStatus.PENDING,
            "num_episodes": config.num_rollouts,
            "success_rate": None,
            "mean_episode_length": None,
            "episodes": [],
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[evaluation_id] = record
            self._write(record)
            self._futures[evaluation_id] = self._executor.submit(self._run, evaluation_id)
        return self._response(record)

    def _state_bank(self, record: dict[str, Any]) -> Path:
        """Ngân hàng trạng thái khởi đầu, đặt tên theo TOÀN BỘ dải seed.

        Cố ý không phụ thuộc số process: chia việc là quyết định vận hành, còn
        cảnh khởi đầu của mỗi seed phải giữ nguyên qua các lần chạy, nếu không
        so sánh hai checkpoint sẽ so cả sự khác biệt của cảnh.
        """
        config = record["config"]
        start_seed = int(config["seed"])
        end_seed = start_seed + int(config["num_rollouts"]) - 1
        return (
            self.training_root
            / record["training_run_id"]
            / "evaluation_state_banks"
            / f"seeds_{start_seed}_{end_seed}.npz"
        ).resolve()

    def _command(
        self,
        record: dict[str, Any],
        *,
        seed: int | None = None,
        rollouts: int | None = None,
        result: Path | None = None,
        record_videos: int | None = None,
    ) -> list[str]:
        """Dòng lệnh cho một phần công việc. Bỏ trống các tham số thì chạy trọn
        dải seed vào `result.json` — đúng hành vi khi chỉ có một process."""
        directory = self._job_dir(record["training_run_id"], record["id"])
        config = record["config"]
        command = [
            self.python_executable,
            str(self.evaluation_script),
            "--agent", str(record["checkpoint_path"]),
            "--result", str(result or directory / "result.json"),
            "--video-dir", str(directory / "videos"),
            "--n-rollouts", str(config["num_rollouts"] if rollouts is None else rollouts),
            "--seed", str(config["seed"] if seed is None else seed),
            "--record-videos", str(
                config["record_videos"] if record_videos is None else record_videos
            ),
            "--state-bank", str(self._state_bank(record)),
        ]
        if config.get("horizon") is not None:
            command.extend(["--horizon", str(config["horizon"])])
        return command

    def _worker_count(self, rollouts: int) -> int:
        from src.config import get_settings

        return max(1, min(int(get_settings().evaluation_workers), rollouts))

    def _environment(self, workers: int) -> dict[str, str]:
        """Chia số nhân cho các process, thay vì để mỗi cái đòi cả máy.

        torch mặc định mở thread bằng số nhân, nên hai process song song sẽ tạo
        gấp đôi số thread mà máy có. Đo được trên 8 rollout: một process 46 s,
        hai process không giới hạn 77 s — CHẬM HƠN cả tuần tự vì tranh nhau —
        còn hai process giới hạn hai thread mỗi cái chỉ 41 s.
        """
        environment = dict(os.environ)
        if workers > 1:
            share = max(1, (os.cpu_count() or workers) // workers)
            for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                environment[name] = str(share)
        return environment

    def _spawn(
        self, command: list[str], log: Any, creationflags: int, workers: int = 1
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            command,
            cwd=self.repo_root,
            env=self._environment(workers),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )

    def _run_rollouts(
        self,
        record: dict[str, Any],
        directory: Path,
        log: Any,
        creationflags: int,
    ) -> int:
        """Chạy các rollout, chia cho nhiều process nếu được cấu hình.

        Một process thì giữ nguyên đường cũ: nó tự ghi thẳng `result.json`.
        Nhiều process thì mỗi cái ghi file riêng rồi gộp lại ở cuối.
        """
        evaluation_id = record["id"]
        config = record["config"]
        rollouts = int(config["num_rollouts"])
        workers = self._worker_count(rollouts)

        if workers == 1:
            process = self._spawn(self._command(record), log, creationflags)
            with self._lock:
                self._processes[evaluation_id] = [process]
            return process.wait()

        # Ngân hàng trạng thái phải có TRƯỚC khi chia việc: nhiều process cùng
        # dựng nó sẽ ghi đè lên nhau, và mỗi process chỉ dựng được phần seed
        # của mình. Một lượt dựng trước, các process sau chỉ đọc.
        bank = self._state_bank(record)
        if not bank.is_file():
            log.write(f"preparing state bank for {rollouts} seeds\n")
            log.flush()
            warmup = self._spawn(
                self._command(record, rollouts=1, result=directory / "warmup.json",
                              record_videos=0),
                log,
                creationflags,
            )
            with self._lock:
                self._processes[evaluation_id] = [warmup]
            code = warmup.wait()
            (directory / "warmup.json").unlink(missing_ok=True)
            if code != 0:
                return code
            with self._lock:
                if self._jobs[evaluation_id]["cancel_requested"]:
                    return 0

        chunks = split_rollouts(int(config["seed"]), rollouts, workers)
        log.write(f"running {rollouts} rollouts across {len(chunks)} processes\n")
        log.flush()

        # Hạn ngạch video thuộc về cả lần chạy, không phải từng nhóm: nhóm đầu
        # nhận trọn, các nhóm sau chỉ quay rollout thất bại (script luôn giữ
        # video của thất bại). Chia đều thì tổng số video vượt yêu cầu.
        videos_left = int(config["record_videos"])
        processes: list[subprocess.Popen[str]] = []
        parts: list[Path] = []
        for index, (chunk_seed, chunk_size) in enumerate(chunks):
            part = directory / f"result.part{index}.json"
            parts.append(part)
            share = min(videos_left, chunk_size)
            videos_left -= share
            processes.append(
                self._spawn(
                    self._command(
                        record,
                        seed=chunk_seed,
                        rollouts=chunk_size,
                        result=part,
                        record_videos=share,
                    ),
                    log,
                    creationflags,
                    workers=len(chunks),
                )
            )
        with self._lock:
            self._processes[evaluation_id] = processes

        codes = [process.wait() for process in processes]
        failed = next((code for code in codes if code != 0), 0)

        # Gộp cả khi có process hỏng: những nhóm chạy xong vẫn là kết quả thật,
        # và người dùng nhìn thấy phần nào đã chạy thay vì một trang trống.
        merged = merge_results(
            [
                json.loads(part.read_text(encoding="utf-8"))
                for part in parts
                if part.is_file()
            ],
            rollouts,
        )
        (directory / "result.json").write_text(
            json.dumps(merged, indent=2), encoding="utf-8"
        )
        for part in parts:
            part.unlink(missing_ok=True)
        return failed

    def _run(self, evaluation_id: str) -> None:
        with self._lock:
            record = self._jobs[evaluation_id]
            if record["cancel_requested"]:
                self._finish_cancelled(record)
                return
            record["status"] = JobStatus.RUNNING
            record["started_at"] = _now()
            self._write(record)
        directory = self._job_dir(record["training_run_id"], evaluation_id)
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with (directory / "stdout.log").open(
                "w", encoding="utf-8", errors="replace"
            ) as log:
                with ACCELERATOR_LOCK:
                    with self._lock:
                        if self._jobs[evaluation_id]["cancel_requested"]:
                            self._finish_cancelled(self._jobs[evaluation_id])
                            return
                    return_code = self._run_rollouts(
                        record, directory, log, creationflags
                    )
            with self._lock:
                record = self._jobs[evaluation_id]
                self._refresh(record)
                if record["cancel_requested"]:
                    self._finish_cancelled(record)
                elif return_code == 0:
                    record["status"] = JobStatus.SUCCEEDED
                    record["finished_at"] = _now()
                    self._write(record)
                else:
                    record["status"] = JobStatus.FAILED
                    record["finished_at"] = _now()
                    record["error"] = f"Evaluation process exited with code {return_code}"
                    self._write(record)
        except Exception as exc:
            with self._lock:
                record = self._jobs[evaluation_id]
                record["status"] = JobStatus.FAILED
                record["finished_at"] = _now()
                record["error"] = str(exc)
                self._write(record)
        finally:
            with self._lock:
                self._processes.pop(evaluation_id, None)

    def _finish_cancelled(self, record: dict[str, Any]) -> None:
        record["status"] = JobStatus.CANCELLED
        record["finished_at"] = _now()
        self._write(record)

    def _refresh(self, record: dict[str, Any]) -> None:
        path = self._job_dir(record["training_run_id"], record["id"]) / "result.json"
        if not path.is_file():
            return
        result = json.loads(path.read_text(encoding="utf-8"))
        for key in ("task_name", "success_rate", "mean_episode_length", "episodes"):
            if key in result:
                record[key] = result[key]

    def list(self, training_run_id: str | None = None) -> list[EvalResultResponse]:
        with self._lock:
            missing = [
                evaluation_id
                for evaluation_id, record in self._jobs.items()
                if not (
                    self._job_dir(record["training_run_id"], evaluation_id) / "job.json"
                ).is_file()
            ]
            for evaluation_id in missing:
                self._jobs.pop(evaluation_id, None)
            records = []
            for record in self._jobs.values():
                if training_run_id and record["training_run_id"] != training_run_id:
                    continue
                self._refresh(record)
                records.append(record)
            records.sort(key=lambda item: item["created_at"], reverse=True)
            return [self._response(record) for record in records]

    def get(self, evaluation_id: str) -> EvalResultResponse | None:
        with self._lock:
            record = self._jobs.get(evaluation_id)
            if record is None:
                return None
            if not (
                self._job_dir(record["training_run_id"], evaluation_id) / "job.json"
            ).is_file():
                self._jobs.pop(evaluation_id, None)
                return None
            self._refresh(record)
            return self._response(record)

    def cancel(self, evaluation_id: str) -> EvalResultResponse | None:
        with self._lock:
            record = self._jobs.get(evaluation_id)
            if record is None:
                return None
            if record["status"] not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return self._response(record)
            record["cancel_requested"] = True
            processes = self._processes.get(evaluation_id)
            future = self._futures.get(evaluation_id)
            if processes:
                # Dừng tất cả: bỏ sót một cái là nó chạy tiếp và vẫn ghi kết
                # quả vào một job người dùng đã huỷ.
                for process in processes:
                    process.terminate()
            elif future is not None and future.cancel():
                self._finish_cancelled(record)
            else:
                self._write(record)
            return self._response(record)

    def retry(self, evaluation_id: str) -> EvalResultResponse | None:
        with self._lock:
            record = self._jobs.get(evaluation_id)
            if record is None:
                return None
            if record["status"] in {JobStatus.PENDING, JobStatus.RUNNING}:
                raise ValueError("Không thể retry evaluation đang chạy")
            config = EvaluationJobRequest.model_validate(record["config"])
        return self.submit(config)

    def delete(self, evaluation_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(evaluation_id)
            if record is None:
                return False
            if record["status"] in {JobStatus.PENDING, JobStatus.RUNNING}:
                raise ValueError("Không thể xóa evaluation đang chạy")
            directory = self._job_dir(record["training_run_id"], evaluation_id)
            if directory.exists():
                shutil.rmtree(directory)
            self._jobs.pop(evaluation_id, None)
            self._processes.pop(evaluation_id, None)
            self._futures.pop(evaluation_id, None)
            return True

    def log(self, evaluation_id: str) -> str | None:
        record = self._jobs.get(evaluation_id)
        if record is None:
            return None
        path = self._job_dir(record["training_run_id"], evaluation_id) / "stdout.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def video(self, evaluation_id: str, filename: str) -> Path | None:
        with self._lock:
            record = self._jobs.get(evaluation_id)
            if record is None:
                return None
            self._refresh(record)
            allowed = {episode.get("video") for episode in record["episodes"]}
            if filename not in allowed:
                return None
            base = self._job_dir(record["training_run_id"], evaluation_id) / "videos"
            path = (base / filename).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():
                return None
            return path

    @staticmethod
    def _response(record: dict[str, Any]) -> EvalResultResponse:
        public = {
            key: value
            for key, value in record.items()
            if key not in {"checkpoint_path", "config", "started_at", "cancel_requested"}
        }
        return EvalResultResponse.model_validate(public)
