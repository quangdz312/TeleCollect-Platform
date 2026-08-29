"""Handler chạy trên máy GPU thuê: chọn checkpoint, gom log, xử lý lỗi."""

import pytest

import runpod_handler
from runpod_handler import handler, selected_checkpoints


def _touch(directory, name: str, size: int = 10):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# --- chọn checkpoint --------------------------------------------------------


def test_keeps_only_last_and_best_validation(tmp_path):
    _touch(tmp_path, "last.pth")
    _touch(tmp_path, "model_epoch_10_best_validation_0.12.pth")
    _touch(tmp_path, "model_epoch_5.pth")
    _touch(tmp_path, "model_epoch_20.pth")
    names = {path.name for path in selected_checkpoints(tmp_path)}
    assert names == {"last.pth", "model_epoch_10_best_validation_0.12.pth"}


def test_skips_the_backup_file(tmp_path):
    _touch(tmp_path, "last.pth")
    _touch(tmp_path, "last_bak.pth")
    assert [p.name for p in selected_checkpoints(tmp_path)] == ["last.pth"]


def test_empty_output_dir_yields_nothing(tmp_path):
    assert selected_checkpoints(tmp_path) == []


# --- kiểm tra payload -------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["job_id", "callback_url", "machine_token", "config"]
)
def test_missing_field_fails_with_a_readable_message(missing):
    payload = {
        "job_id": "job-1",
        "callback_url": "http://localhost:8000",
        "machine_token": "token",
        "config": {"name": "run"},
    }
    payload.pop(missing)
    result = handler({"input": payload})
    assert result["status"] == "failed"
    assert missing in result["error"]


def test_empty_event_fails_cleanly():
    assert handler({})["status"] == "failed"


# --- đường thành công và thất bại -------------------------------------------


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """Thay tải dataset và chạy train bằng hàm giả; giữ nguyên phần còn lại."""
    calls = {"uploaded": [], "logs": []}

    def fake_client(callback_url, machine_token):
        class _Ctx:
            def __enter__(self):
                return "client"

            def __exit__(self, *args):
                return False

        return _Ctx()

    def fake_download(client, job_id, target):
        target.write_bytes(b"data")
        return target

    def fake_push_log(client, job_id, text):
        calls["logs"].append(text)

    def fake_upload(client, job_id, output_dir, save_every_n_epochs=None):
        calls["uploaded"].append(output_dir)
        return ["last.pth"]

    monkeypatch.setattr(runpod_handler, "_client", fake_client)
    monkeypatch.setattr(runpod_handler, "download_dataset", fake_download)
    monkeypatch.setattr(runpod_handler, "push_log", fake_push_log)
    monkeypatch.setattr(runpod_handler, "upload_checkpoints", fake_upload)
    return calls


def _event():
    return {
        "input": {
            "job_id": "job-1",
            "callback_url": "http://localhost:8000",
            "machine_token": "token",
            "config": {"name": "run"},
        }
    }


def test_successful_run_reports_checkpoints(monkeypatch, stubbed):
    monkeypatch.setattr(runpod_handler, "run_training", lambda *a, **k: 0)
    result = handler(_event())
    assert result == {"status": "succeeded", "checkpoints": ["last.pth"]}


def test_failed_run_still_uploads_what_exists(monkeypatch, stubbed):
    """Đã trả tiền GPU rồi thì mốc dở dang vẫn đáng giữ."""
    monkeypatch.setattr(runpod_handler, "run_training", lambda *a, **k: 1)
    result = handler(_event())
    assert result["status"] == "failed"
    assert "mã 1" in result["error"]
    assert len(stubbed["uploaded"]) == 1


def test_unexpected_exception_is_contained(monkeypatch, stubbed):
    def boom(*args, **kwargs):
        raise RuntimeError("gpu fell over")

    monkeypatch.setattr(runpod_handler, "run_training", boom)
    result = handler(_event())
    assert result["status"] == "failed"
    assert "gpu fell over" in result["error"]


# --- khoá W&B ---------------------------------------------------------------


def test_the_wandb_key_reaches_the_training_process(monkeypatch, stubbed):
    """Khoá phải đi tới tiến trình train qua môi trường, nếu không W&B trống."""
    seen = {}

    def capture(client, job_id, config, dataset, output_dir, wandb_api_key=None):
        seen["key"] = wandb_api_key
        return 0

    monkeypatch.setattr(runpod_handler, "run_training", capture)
    event = _event()
    event["input"]["wandb_api_key"] = "wandb-secret"
    assert handler(event)["status"] == "succeeded"
    assert seen["key"] == "wandb-secret"


def test_the_environment_carries_the_key():
    environment = runpod_handler._training_environment("wandb-secret")
    assert environment["WANDB_API_KEY"] == "wandb-secret"


def test_an_inherited_key_is_dropped_when_the_job_brought_none(monkeypatch):
    """Khoá thừa hưởng sẽ đẩy run của người này vào tài khoản người khác."""
    monkeypatch.setenv("WANDB_API_KEY", "somebody-elses-key")
    assert "WANDB_API_KEY" not in runpod_handler._training_environment(None)


def test_popen_receives_the_key_not_just_the_function(monkeypatch, tmp_path):
    """Kiểm tra tận `Popen`: các test trên stub `run_training` nên không thấy
    được lúc quên truyền `env=` — chính là lỗi đã xảy ra."""
    seen = {}

    class FakeProcess:
        stdout = iter(())

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(runpod_handler.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runpod_handler, "push_log", lambda *a, **k: None)
    monkeypatch.setattr(
        runpod_handler, "build_training_command", lambda *a, **k: ["python", "-c", ""]
    )

    runpod_handler.run_training(
        None, "job-1", {"name": "run"}, tmp_path / "d.hdf5", tmp_path, "wandb-secret"
    )
    assert seen["env"]["WANDB_API_KEY"] == "wandb-secret"
