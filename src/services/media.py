"""Bọc ffprobe/ffmpeg — đo video và sinh thumbnail.

Trách nhiệm: nơi duy nhất gọi subprocess ffprobe/ffmpeg. Mọi lệnh có timeout
để một file hỏng/độc hại không treo request upload vô thời hạn.
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FFPROBE_TIMEOUT_S = 10.0
FFMPEG_TIMEOUT_S = 10.0

MP4_MAGIC = b"ftyp"
MP4_MAGIC_OFFSET = 4
"""ISO base media file format: 4 byte box-size rồi tới 4 byte 'ftyp' — offset 4-8."""


class MediaProbeError(Exception):
    """ffprobe fail, timeout, hoặc file không có stream video hợp lệ."""


@dataclass
class VideoProbe:
    duration_s: float
    fps: float
    num_frames: int


def has_mp4_magic_bytes(path: Path) -> bool:
    """Kiểm tra magic bytes thay vì chỉ tin đuôi file/content-type — client
    (curl, nhiều SDK) gửi `application/octet-stream` cho mp4 hoàn toàn hợp lệ,
    chỉ check content-type sẽ chặn nhầm."""
    try:
        with open(path, "rb") as f:
            header = f.read(8)
    except OSError:
        return False
    return len(header) >= 8 and header[MP4_MAGIC_OFFSET : MP4_MAGIC_OFFSET + 4] == MP4_MAGIC


def _parse_frame_rate(rate: str) -> float:
    """ffprobe trả fps dạng phân số chuỗi ("30000/1001", "30/1") — KHÔNG
    `float()` thẳng chuỗi có dấu '/', sẽ crash (ValueError)."""
    if "/" in rate:
        num_str, _, den_str = rate.partition("/")
        try:
            num, den = float(num_str), float(den_str)
        except ValueError:
            return 0.0
        return num / den if den else 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


async def probe_video(path: Path) -> VideoProbe:
    """Chạy ffprobe lấy duration/fps/num_frames.

    Raise `MediaProbeError` nếu ffprobe fail, timeout, hoặc không tìm thấy
    stream video — gọi nơi endpoint bắt và trả 422 + cleanup.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=FFPROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError("ffprobe timeout") from exc
    except OSError as exc:
        raise MediaProbeError(f"ffprobe không chạy được: {exc}") from exc

    if result.returncode != 0:
        raise MediaProbeError(f"ffprobe lỗi: {result.stderr.decode(errors='ignore')[:500]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError("ffprobe trả output không phải JSON hợp lệ") from exc

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise MediaProbeError("Không tìm thấy stream video trong file")

    try:
        duration_s = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaProbeError("Không đọc được duration từ ffprobe") from exc

    fps = _parse_frame_rate(video_stream.get("avg_frame_rate", "0/0"))
    if fps <= 0:
        fps = _parse_frame_rate(video_stream.get("r_frame_rate", "0/0"))

    nb_frames_raw = video_stream.get("nb_frames")
    num_frames: int
    if nb_frames_raw is not None:
        try:
            num_frames = int(nb_frames_raw)
        except ValueError:
            num_frames = round(duration_s * fps) if fps > 0 else 0
    else:
        num_frames = round(duration_s * fps) if fps > 0 else 0

    return VideoProbe(duration_s=duration_s, fps=fps, num_frames=num_frames)


async def generate_thumbnail(video_path: Path, output_path: Path) -> bool:
    """Sinh JPEG frame đầu bằng ffmpeg.

    Lỗi KHÔNG raise — trả `False` và log warning: thumbnail hỏng không phải
    lý do để từ chối cả một video hợp lệ.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        str(output_path),
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        logger.warning("Sinh thumbnail timeout: %s", video_path)
        return False
    except OSError as exc:
        logger.warning("Sinh thumbnail thất bại (%s): %s", video_path, exc)
        return False

    if result.returncode != 0 or not output_path.exists():
        logger.warning(
            "Sinh thumbnail thất bại (%s): %s",
            video_path,
            result.stderr.decode(errors="ignore")[:500],
        )
        return False
    return True
