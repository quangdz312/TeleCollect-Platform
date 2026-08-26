"""Chạy training job trên GPU thuê qua RunPod Serverless.

Server staging không có GPU, nên `TRAINING_RUNNER=runpod` đẩy việc huấn luyện
sang một worker RunPod: trả tiền theo giây, không có job thì không tốn.

Luồng ngược chiều nhau, cố ý:

- Backend -> RunPod: gọi API `run` / `status` / `cancel`. Backend chủ động hỏi.
- Máy GPU -> Backend: tải dataset, đẩy log và checkpoint qua ba endpoint máy.
  Backend KHÔNG mở được kết nối vào máy thuê nên máy thuê phải tự gọi về.

Không dùng S3/R2 ở giai đoạn này: server còn 103 GB trống, thêm một nhà cung
cấp nữa là thêm một tài khoản, một khóa và một tầng lỗi.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from src.config import get_settings
from src.models.enums import JobStatus
from src.services.security import create_machine_token

API_ROOT = "https://api.runpod.ai/v2"

# RunPod trả trạng thái riêng của nó; đây là ánh xạ sang vòng đời job của
# TeleCollect. `IN_QUEUE` gộp vào RUNNING vì với người dùng thì bấm Train xong
# là job đã bắt đầu — chờ worker khởi động cũng là một phần của lần chạy.
_STATUS_MAP = {
    "IN_QUEUE": JobStatus.RUNNING,
    "IN_PROGRESS": JobStatus.RUNNING,
    "COMPLETED": JobStatus.SUCCEEDED,
    "FAILED": JobStatus.FAILED,
    "TIMED_OUT": JobStatus.FAILED,
    "CANCELLED": JobStatus.CANCELLED,
}

# Token máy phải sống lâu hơn lần train dài nhất: hết hạn giữa chừng thì máy GPU
# train xong nhưng không đẩy được checkpoint về, mất trắng cả lần chạy.
_TOKEN_GRACE = timedelta(hours=1)


class RunPodError(RuntimeError):
    """Lỗi khi gọi RunPod — luôn được `TrainingJobManager` ghi vào `job.error`."""


class RunPodRunner:
    """Điều phối một training job trên RunPod Serverless.

    Không giữ trạng thái riêng: mọi thứ cần cho `poll`/`cancel` nằm trong
    `record["runner_state"]`, nên backend khởi động lại vẫn theo dõi tiếp được
    job đang chạy.
    """

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s

    def _config(self) -> tuple[str, str, str]:
        settings = get_settings()
        missing = [
            name
            for name, value in (
                ("RUNPOD_API_KEY", settings.runpod_api_key),
                ("RUNPOD_ENDPOINT_ID", settings.runpod_endpoint_id),
                ("PUBLIC_BASE_URL", settings.public_base_url),
            )
            if not value
        ]
        if missing:
            raise RunPodError(
                "Thiếu cấu hình cho TRAINING_RUNNER=runpod: " + ", ".join(missing)
            )
        return (
            settings.runpod_api_key,
            settings.runpod_endpoint_id,
            settings.public_base_url.rstrip("/"),
        )

    def _request(self, method: str, url: str, api_key: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self._timeout_s,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise RunPodError(
                f"RunPod trả {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RunPodError(f"Không gọi được RunPod: {exc}") from exc

    def payload(self, record: dict[str, Any]) -> dict[str, Any]:
        """Hợp đồng giữa backend và `runpod_handler.py` — đổi là phải đổi cả hai."""
        settings = get_settings()
        _, _, base_url = self._config()
        token = create_machine_token(
            record["id"],
            timedelta(hours=settings.runpod_max_hours) + _TOKEN_GRACE,
        )
        return {
            "input": {
                "job_id": record["id"],
                "callback_url": base_url,
                "machine_token": token,
                "config": record["config"],
            }
        }

    def start(self, record: dict[str, Any]) -> dict[str, Any]:
        api_key, endpoint_id, _ = self._config()
        body = self._request(
            "POST",
            f"{API_ROOT}/{endpoint_id}/run",
            api_key,
            json=self.payload(record),
        )
        runpod_id = body.get("id")
        if not runpod_id:
            raise RunPodError(f"RunPod không trả job id: {body}")
        return {"runpod_id": str(runpod_id), "endpoint_id": endpoint_id}

    def poll(self, record: dict[str, Any]) -> dict[str, Any]:
        api_key, endpoint_id, _ = self._config()
        runpod_id = record.get("runner_state", {}).get("runpod_id")
        if not runpod_id:
            raise RunPodError("Job chưa có runpod_id để hỏi trạng thái")
        body = self._request(
            "GET", f"{API_ROOT}/{endpoint_id}/status/{runpod_id}", api_key
        )
        raw = str(body.get("status", "")).upper()
        status = _STATUS_MAP.get(raw)
        if status is None:
            # Trạng thái lạ: coi như vẫn chạy thay vì kết luận thất bại — RunPod
            # có thể thêm trạng thái mới, và kết thúc nhầm một job đang train là
            # mất cả lần chạy đã trả tiền.
            return {"status": JobStatus.RUNNING, "error": None}
        error = None
        if status == JobStatus.FAILED:
            error = str(body.get("error") or f"RunPod báo {raw}")
        return {"status": status, "error": error}

    def cancel(self, record: dict[str, Any]) -> None:
        api_key, endpoint_id, _ = self._config()
        runpod_id = record.get("runner_state", {}).get("runpod_id")
        if not runpod_id:
            return
        self._request("POST", f"{API_ROOT}/{endpoint_id}/cancel/{runpod_id}", api_key)
