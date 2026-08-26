"""RunPodRunner: ánh xạ trạng thái, hợp đồng payload, và lỗi mạng."""

import httpx
import pytest

from src.config import get_settings
from src.models.enums import JobStatus
from src.services.security import decode_machine_token
from src.training.runpod_runner import RunPodError, RunPodRunner

ENDPOINT = "ep-123"


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "runpod_api_key", "key-abc")
    monkeypatch.setattr(settings, "runpod_endpoint_id", ENDPOINT)
    monkeypatch.setattr(settings, "public_base_url", "https://tele.example.com/")
    monkeypatch.setattr(settings, "runpod_max_hours", 1.0)
    return settings


def _record(**overrides):
    record = {
        "id": "job-1",
        "config": {"name": "run", "epochs": 3},
        "runner_state": {"runpod_id": "rp-9"},
    }
    record.update(overrides)
    return record


def _stub(monkeypatch, handler):
    """Thay `httpx.request` bằng một hàm trả response dựng sẵn."""
    calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return handler(method, url)

    monkeypatch.setattr(httpx, "request", fake_request)
    return calls


def _response(payload, status_code=200):
    return httpx.Response(
        status_code, json=payload, request=httpx.Request("GET", "https://x")
    )


# --- cấu hình ---------------------------------------------------------------


def test_missing_config_names_the_variables(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "runpod_api_key", "")
    monkeypatch.setattr(settings, "runpod_endpoint_id", "")
    monkeypatch.setattr(settings, "public_base_url", "")
    with pytest.raises(RunPodError) as exc:
        RunPodRunner().start(_record())
    assert "RUNPOD_API_KEY" in str(exc.value)
    assert "PUBLIC_BASE_URL" in str(exc.value)


# --- payload: hợp đồng với runpod_handler.py --------------------------------


def test_payload_carries_a_machine_token_for_this_job(configured):
    payload = RunPodRunner().payload(_record())["input"]
    assert payload["job_id"] == "job-1"
    assert decode_machine_token(payload["machine_token"]) == "job-1"


def test_payload_strips_trailing_slash_from_base_url(configured):
    payload = RunPodRunner().payload(_record())["input"]
    assert payload["callback_url"] == "https://tele.example.com"


# --- start ------------------------------------------------------------------


def test_start_returns_runpod_id(configured, monkeypatch):
    calls = _stub(monkeypatch, lambda m, u: _response({"id": "rp-42"}))
    state = RunPodRunner().start(_record(runner_state={}))
    assert state["runpod_id"] == "rp-42"
    assert calls[0]["url"] == f"https://api.runpod.ai/v2/{ENDPOINT}/run"
    assert calls[0]["headers"]["Authorization"] == "Bearer key-abc"


def test_start_without_id_is_an_error(configured, monkeypatch):
    _stub(monkeypatch, lambda m, u: _response({"error": "nope"}))
    with pytest.raises(RunPodError):
        RunPodRunner().start(_record(runner_state={}))


# --- poll: ánh xạ trạng thái ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IN_QUEUE", JobStatus.RUNNING),
        ("IN_PROGRESS", JobStatus.RUNNING),
        ("COMPLETED", JobStatus.SUCCEEDED),
        ("FAILED", JobStatus.FAILED),
        ("TIMED_OUT", JobStatus.FAILED),
        ("CANCELLED", JobStatus.CANCELLED),
    ],
)
def test_poll_maps_every_runpod_status(configured, monkeypatch, raw, expected):
    _stub(monkeypatch, lambda m, u: _response({"status": raw}))
    assert RunPodRunner().poll(_record())["status"] == expected


def test_unknown_status_keeps_the_job_running(configured, monkeypatch):
    """Kết thúc nhầm một job đang train là mất cả lần chạy đã trả tiền."""
    _stub(monkeypatch, lambda m, u: _response({"status": "SOMETHING_NEW"}))
    assert RunPodRunner().poll(_record())["status"] == JobStatus.RUNNING


def test_failed_status_carries_the_reason(configured, monkeypatch):
    _stub(monkeypatch, lambda m, u: _response({"status": "FAILED", "error": "OOM"}))
    assert RunPodRunner().poll(_record())["error"] == "OOM"


def test_poll_without_runpod_id_is_an_error(configured):
    with pytest.raises(RunPodError):
        RunPodRunner().poll(_record(runner_state={}))


# --- lỗi mạng ---------------------------------------------------------------


def test_http_error_becomes_runpod_error(configured, monkeypatch):
    def boom(method, url):
        return httpx.Response(
            500, text="upstream down", request=httpx.Request(method, url)
        )

    _stub(monkeypatch, boom)
    with pytest.raises(RunPodError) as exc:
        RunPodRunner().poll(_record())
    assert "500" in str(exc.value)


def test_connection_error_becomes_runpod_error(configured, monkeypatch):
    def boom(method, url, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "request", boom)
    with pytest.raises(RunPodError):
        RunPodRunner().poll(_record())


# --- cancel -----------------------------------------------------------------


def test_cancel_calls_the_provider(configured, monkeypatch):
    calls = _stub(monkeypatch, lambda m, u: _response({"status": "CANCELLED"}))
    RunPodRunner().cancel(_record())
    assert calls[0]["url"] == f"https://api.runpod.ai/v2/{ENDPOINT}/cancel/rp-9"


def test_cancel_without_runpod_id_is_a_no_op(configured, monkeypatch):
    calls = _stub(monkeypatch, lambda m, u: _response({}))
    RunPodRunner().cancel(_record(runner_state={}))
    assert calls == []
