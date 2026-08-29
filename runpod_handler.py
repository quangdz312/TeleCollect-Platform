"""Điểm vào chạy trên máy GPU thuê (RunPod Serverless).

RunPod gọi `handler(event)` một lần cho mỗi training job. Hàm này tải dataset
từ server TeleCollect, chạy đúng `scripts/train_robomimic_bc.py` như bản local,
rồi đẩy log và checkpoint ngược về server.

Máy thuê KHÔNG nhìn thấy đĩa của server, và server KHÔNG mở được kết nối vào máy
thuê — nên mọi thứ đi qua ba endpoint máy, xác thực bằng token chỉ dùng được cho
đúng một `job_id`.

Chạy thử không cần Docker, không cần RunPod:

    python runpod_handler.py --job-id <id> --callback-url http://localhost:8000 \
        --machine-token <token>

Token lấy bằng `create_machine_token(job_id, timedelta(hours=2))`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from src.training.jobs import build_training_command

# Đẩy log theo lô: mỗi dòng một request thì một lần train sinh ra hàng nghìn
# request, còn gom quá lâu thì người dùng nhìn màn hình trống tưởng job treo.
LOG_FLUSH_INTERVAL_S = 5.0
HTTP_TIMEOUT_S = 60.0
UPLOAD_TIMEOUT_S = 600.0


class HandlerError(RuntimeError):
    """Lỗi có thông báo đọc được, trả về trong kết quả job."""


def _client(callback_url: str, machine_token: str) -> httpx.Client:
    return httpx.Client(
        base_url=callback_url.rstrip("/"),
        headers={"Authorization": f"Bearer {machine_token}"},
        timeout=HTTP_TIMEOUT_S,
        follow_redirects=True,
    )


def download_dataset(client: httpx.Client, job_id: str, target: Path) -> Path:
    with client.stream("GET", f"/api/v1/training/jobs/{job_id}/dataset") as response:
        if response.status_code != 200:
            raise HandlerError(
                f"Không tải được dataset ({response.status_code}). "
                "Kiểm tra token máy còn hạn và PUBLIC_BASE_URL đúng."
            )
        with target.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                handle.write(chunk)
    if target.stat().st_size == 0:
        raise HandlerError("Dataset tải về rỗng")
    return target


def push_log(client: httpx.Client, job_id: str, text: str) -> None:
    """Đẩy một lô log. Lỗi ở đây KHÔNG được làm hỏng lần train đang chạy."""
    if not text:
        return
    try:
        client.post(f"/api/v1/training/jobs/{job_id}/log", json={"text": text})
    except httpx.HTTPError:
        pass


def selected_checkpoints(output_dir: Path) -> list[Path]:
    """Chỉ giữ `last.pth` và bản `best_validation`.

    Một lần train sinh ra nhiều `model_epoch_*.pth`, mỗi file vài chục tới vài
    trăm MB. Đẩy hết về vừa chậm vừa lấp đĩa server mà gần như không ai dùng
    tới các mốc trung gian.
    """
    keep: list[Path] = []
    for path in sorted(output_dir.rglob("*.pth")):
        name = path.name
        if name == "last_bak.pth":
            continue
        if name == "last.pth" or "best_validation" in name:
            keep.append(path)
    return keep


def upload_checkpoints(client: httpx.Client, job_id: str, output_dir: Path) -> list[str]:
    uploaded: list[str] = []
    for path in selected_checkpoints(output_dir):
        relative = path.relative_to(output_dir).as_posix()
        with path.open("rb") as handle:
            response = client.post(
                f"/api/v1/training/jobs/{job_id}/artifacts",
                files={"file": (relative, handle, "application/octet-stream")},
                timeout=UPLOAD_TIMEOUT_S,
            )
        if response.status_code >= 400:
            raise HandlerError(
                f"Server từ chối checkpoint {relative} ({response.status_code})"
            )
        uploaded.append(relative)
    return uploaded


def _training_environment(wandb_api_key: str | None) -> dict[str, str]:
    """Môi trường cho tiến trình train.

    Khoá W&B đi qua đây chứ không qua tham số dòng lệnh: tham số hiện ra với
    bất kỳ ai liệt kê được tiến trình, và lọt luôn vào log đẩy về server. Đây
    cũng là khoá của riêng người bấm Train, không phải khoá dùng chung.
    """
    environment = dict(os.environ)
    if wandb_api_key:
        environment["WANDB_API_KEY"] = wandb_api_key
    else:
        # Máy thuê không có khoá nào sẵn, nhưng xoá cho chắc: một khoá thừa
        # hưởng từ image sẽ đẩy run của người này vào tài khoản người khác.
        environment.pop("WANDB_API_KEY", None)
    return environment


def run_training(
    client: httpx.Client,
    job_id: str,
    config: dict[str, Any],
    dataset: Path,
    output_dir: Path,
    wandb_api_key: str | None = None,
) -> int:
    """Chạy script train, vừa chạy vừa đẩy log về theo lô."""
    command = build_training_command(
        config,
        python_executable=sys.executable,
        training_script=str(Path(__file__).parent / "scripts" / "train_robomimic_bc.py"),
        dataset_path=str(dataset),
        output_dir=str(output_dir),
    )
    push_log(client, job_id, f"$ {' '.join(command)}\n")
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).parent,
        env=_training_environment(wandb_api_key),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )
    buffer: list[str] = []
    last_flush = time.monotonic()
    assert process.stdout is not None
    for line in process.stdout:
        buffer.append(line)
        if time.monotonic() - last_flush >= LOG_FLUSH_INTERVAL_S:
            push_log(client, job_id, "".join(buffer))
            buffer.clear()
            last_flush = time.monotonic()
    push_log(client, job_id, "".join(buffer))
    return process.wait()


def handler(event: dict[str, Any]) -> dict[str, Any]:
    """Điểm vào RunPod gọi. `event["input"]` theo hợp đồng ở docs/runpod-integration.md."""
    payload = event.get("input") or {}
    missing = [
        key
        for key in ("job_id", "callback_url", "machine_token", "config")
        if not payload.get(key)
    ]
    if missing:
        return {"status": "failed", "error": f"Thiếu trường: {', '.join(missing)}"}

    job_id = str(payload["job_id"])
    config = payload["config"]
    # Không bắt buộc: job không bật W&B thì server không gửi khoá nào cả.
    wandb_api_key = payload.get("wandb_api_key") or None
    try:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            output_dir = root / "output"
            output_dir.mkdir()
            with _client(payload["callback_url"], payload["machine_token"]) as client:
                dataset = download_dataset(client, job_id, root / "dataset.hdf5")
                code = run_training(
                    client, job_id, config, dataset, output_dir, wandb_api_key
                )
                if code != 0:
                    push_log(client, job_id, f"\nTraining thất bại, mã thoát {code}\n")
                    # Vẫn đẩy checkpoint đã có: một lần chạy hỏng ở epoch cuối
                    # vẫn để lại mốc dùng được, và đằng nào cũng đã trả tiền GPU.
                    upload_checkpoints(client, job_id, output_dir)
                    raise HandlerError(f"Training thoát với mã {code}")
                uploaded = upload_checkpoints(client, job_id, output_dir)
                push_log(client, job_id, f"\nĐã đẩy {len(uploaded)} checkpoint về server\n")
        return {"status": "succeeded", "checkpoints": uploaded}
    except HandlerError as exc:
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:  # ranh giới worker: RunPod chỉ thấy dict trả về
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def _main() -> int:
    """Chạy tay ở máy local để thử, không qua RunPod."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--callback-url", required=True)
    parser.add_argument("--machine-token", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        help="File JSON chứa config; bỏ trống thì dùng mặc định TrainingJobRequest",
    )
    args = parser.parse_args()
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    else:
        from src.models.schemas import TrainingJobRequest

        config = TrainingJobRequest(dataset_id="local", name="local_test").model_dump(
            mode="json"
        )
    result = handler({
        "input": {
            "job_id": args.job_id,
            "callback_url": args.callback_url,
            "machine_token": args.machine_token,
            "config": config,
            # Chạy tay thì lấy khoá từ môi trường sẵn có, khỏi gõ vào dòng lệnh.
            "wandb_api_key": os.environ.get("WANDB_API_KEY", ""),
        }
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    # RunPod nạp module này và gọi `handler`; nhánh dưới chỉ dùng khi chạy tay.
    if len(sys.argv) > 1:
        raise SystemExit(_main())
    import runpod  # nạp muộn: chỉ có trong image train, không có ở máy dev

    runpod.serverless.start({"handler": handler})
