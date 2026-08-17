"""Regression test: probe_video/generate_thumbnail phải chạy được trên
SelectorEventLoop (loop mà uvicorn dùng trên Windows), không chỉ trên
ProactorEventLoop (loop mặc định của pytest-asyncio trên Windows).

`asyncio.create_subprocess_exec` raise NotImplementedError trên
SelectorEventLoop ở Windows -> phải chạy subprocess.run trong thread pool.
"""

import asyncio
import subprocess

import pytest

from src.services.media import generate_thumbnail, probe_video


def _run_in_selector_loop(coro_factory):
    """Chạy coroutine trong một SelectorEventLoop tường minh, tái hiện đúng
    môi trường uvicorn trên Windows (nơi ProactorEventLoop không được dùng)."""
    loop = asyncio.SelectorEventLoop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def test_probe_video_works_on_selector_event_loop(sample_mp4_bytes, tmp_path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(sample_mp4_bytes)

    probe = _run_in_selector_loop(lambda: probe_video(video_path))

    assert probe.duration_s > 0
    assert probe.fps > 0
    assert probe.num_frames > 0


def test_generate_thumbnail_works_on_selector_event_loop(sample_mp4_bytes, tmp_path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(sample_mp4_bytes)
    output_path = tmp_path / "thumb.jpg"

    ok = _run_in_selector_loop(lambda: generate_thumbnail(video_path, output_path))

    assert ok is True
    assert output_path.exists()


@pytest.mark.asyncio
async def test_probe_video_ffprobe_failure_raises_media_probe_error(tmp_path):
    from src.services.media import MediaProbeError

    bad_path = tmp_path / "not-a-video.mp4"
    bad_path.write_bytes(b"not a real video")

    with pytest.raises(MediaProbeError):
        await probe_video(bad_path)


@pytest.mark.asyncio
async def test_generate_thumbnail_ffmpeg_failure_returns_false(tmp_path):
    bad_path = tmp_path / "not-a-video.mp4"
    bad_path.write_bytes(b"not a real video")
    output_path = tmp_path / "thumb.jpg"

    ok = await generate_thumbnail(bad_path, output_path)

    assert ok is False


@pytest.mark.asyncio
async def test_probe_video_timeout_raises_media_probe_error(monkeypatch, tmp_path):
    from src.services import media

    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"x")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=media.FFPROBE_TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    with pytest.raises(media.MediaProbeError, match="timeout"):
        await probe_video(video_path)
