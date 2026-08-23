"""Ghi đồng bộ observation + action thành một episode.

Trách nhiệm cốt lõi của dự án: mỗi bước phải lưu đúng cặp (quan sát tại t,
action người điều khiển áp lên t) cùng một mốc thời gian chung. Lệch một
bước giữa hai luồng là dataset hỏng mà nhìn bằng mắt không phát hiện ra, nên
timestamp lấy từ thời gian mô phỏng (`Observation.t`) chứ không phải wall
clock — wall clock sẽ trôi khi sim chạy chậm hơn realtime.

Bố cục lưu trữ dưới `settings.storage_dir`, tách theo chi phí truy cập:

    {storage_dir}/{episode_id}/
        meta.json        # task, operator, thời điểm, số bước
        actions.parquet  # action + state cấp thấp, dạng cột, load nhanh
        {camera}.mp4     # frame quan sát, nén H.264

Frame được encode ngay trong `append()` chứ không gom vào RAM rồi ghi một
lượt lúc finalize: một episode 30 Hz vài phút với ảnh RGB thô là hàng GB.
Ngược lại action/state cấp thấp thì nhẹ, gom trong RAM tới `finalize()` để
ghi parquet một lần (parquet ghi theo cột nên cần cả cột trong tay).

Cột `t` được dời về mốc 0 tại mẫu đầu tiên của episode. `Observation.t` đếm
từ lúc reset sim, mà người điều khiển thường chỉnh tư thế một lúc rồi mới bấm
ghi (xem `scripts/teleop_ui.py`), nên nếu giữ nguyên thì mỗi episode bắt đầu ở
một mốc khác nhau và `t[-1]` không còn là độ dài episode. Trừ đi một hằng số
không làm mất tính chống trôi của đồng hồ mô phỏng.
"""

import json
import queue
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.config import get_settings
from src.services import storage

TELEOP_SCHEMA_VERSION = 2
PRIVILEGED_STATE_FORMAT = "mujoco_flat_state"
VIDEO_CODEC = "h264"
VIDEO_PIXEL_FORMAT = "yuv420p"


@dataclass
class EpisodeMeta:
    """Metadata của một episode đã ghi xong."""

    episode_id: str
    task_name: str
    operator_id: str
    num_steps: int
    duration_s: float
    control_hz: int
    """Nhịp lúc ghi — cần cho lúc phát lại và lúc huấn luyện."""


class EpisodeRecorder:
    """Ghi một episode từ lúc bắt đầu tới lúc chốt.

    Chữ ký dự kiến:
        def __init__(self, episode_id: str, task_name: str, operator_id: str) -> None
        def append(self, obs: Observation, action: list[float]) -> None
        def finalize(self) -> EpisodeMeta
        def abort(self) -> None

    Khác chữ ký gốc, constructor nhận thêm `control_hz` và `image_size`:
    `EpisodeMeta` có trường `control_hz` nên recorder buộc phải biết nhịp ghi,
    còn `Observation.images` là byte RGB thô không mang theo kích thước ảnh mà
    encoder H.264 lại cần biết trước.

    `human_cameras` liệt kê những luồng có thể chứa hình ảnh người (vd webcam
    operator) — chỉ những luồng này mới đi qua bước ẩn danh khuôn mặt. Camera
    trong sim không có người nên chạy detector trên chúng chỉ tốn thời gian
    finalize mà không đổi gì (xem docstring `src/core/anonymize.py`).
    """

    def __init__(
        self,
        episode_id: str,
        task_name: str,
        operator_id: str,
        control_hz: int = 30,
        image_size: int = 480,
        human_cameras: tuple[str, ...] = (),
    ) -> None:
        self.episode_id = episode_id
        self.task_name = task_name
        self.operator_id = operator_id
        self.control_hz = control_hz
        self.image_size = image_size
        self.human_cameras = human_cameras

        self._num_steps = 0
        self._first_t: float | None = None
        self._last_t = 0.0
        self._rows: list[dict[str, Any]] = []
        self._containers: dict[str, Any] = {}
        self._streams: dict[str, Any] = {}
        self._cameras_seen: set[str] = set()
        self._frame_queue: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=256)
        self._encoder_thread: threading.Thread | None = None
        self._encoder_error: BaseException | None = None
        self._started_at = datetime.now().astimezone().isoformat()

        Path(storage.episode_dir(episode_id)).mkdir(parents=True, exist_ok=True)

    @property
    def num_steps(self) -> int:
        """Số bước đã ghi — nơi gọi dùng để kiểm trần `TaskSpec.max_steps`."""
        return self._num_steps

    def append(self, obs: Any, action: list[float]) -> None:
        """Ghi một cặp observation-action. Gọi mỗi chu kỳ điều khiển."""
        if self._first_t is None:
            self._first_t = obs.t
        elapsed = obs.t - self._first_t

        self._rows.append(
            {
                "t": elapsed,
                "qpos": list(obs.qpos),
                "qvel": list(obs.qvel),
                "ee_pose": list(obs.ee_pose),
                "privileged_state": None
                if getattr(obs, "privileged_state", None) is None
                else list(obs.privileged_state),
                "action": list(action),
            }
        )
        for camera, frame in obs.images.items():
            self._cameras_seen.add(camera)
            self._queue_frame(camera, frame)

        self._num_steps += 1
        self._last_t = elapsed

    def finalize(self, extra_meta: dict[str, Any] | None = None) -> EpisodeMeta:
        """Chốt episode: flush video, ghi parquet + meta.json, trả metadata.

        Chạy bước ẩn danh khuôn mặt trước khi ghi xuống nếu
        `enable_face_anonymization` bật.

        `extra_meta` là các trường tuỳ chọn mà nơi gọi biết còn recorder thì
        không (task_success, seed, partial/interrupted, số đo độ trễ của vòng
        điều khiển). Chúng được trộn vào `meta.json` chứ không vào
        `EpisodeMeta` để bản ghi cũ vẫn đọc được — mọi trường bắt buộc của
        schema cũ giữ nguyên tên và kiểu.
        """
        cameras = sorted(self._cameras_seen)
        self._close_videos()

        table = pa.table(
            {
                "t": [r["t"] for r in self._rows],
                "qpos": [r["qpos"] for r in self._rows],
                "qvel": [r["qvel"] for r in self._rows],
                "ee_pose": [r["ee_pose"] for r in self._rows],
                "privileged_state": [r["privileged_state"] for r in self._rows],
                "action": [r["action"] for r in self._rows],
            }
        )
        pq.write_table(table, storage.actions_path(self.episode_id))

        if get_settings().enable_face_anonymization and self.human_cameras:
            from src.core import anonymize

            for camera in cameras:
                if camera in self.human_cameras:
                    anonymize.anonymize_video(str(storage.video_path(self.episode_id, camera)))

        meta = EpisodeMeta(
            episode_id=self.episode_id,
            task_name=self.task_name,
            operator_id=self.operator_id,
            num_steps=self._num_steps,
            duration_s=self._last_t,
            control_hz=self.control_hz,
        )
        state_dim = 0
        for row in self._rows:
            if row["privileged_state"] is not None:
                state_dim = len(row["privileged_state"])
                break
        payload = (
            asdict(meta)
            | {
                "started_at": self._started_at,
                "cameras": cameras,
                "teleop_schema_version": TELEOP_SCHEMA_VERSION,
                "video": {
                    "width": self.image_size,
                    "height": self.image_size,
                    "fps": self.control_hz,
                    "codec": VIDEO_CODEC,
                    "pixel_format": VIDEO_PIXEL_FORMAT,
                },
                "privileged_state": {
                    "format": PRIVILEGED_STATE_FORMAT,
                    "column": "privileged_state",
                    "dtype": "float64",
                    "dim": state_dim,
                    "bytes_per_frame": state_dim * 8,
                    "recorded": state_dim > 0,
                },
            }
            | dict(extra_meta or {})
        )
        Path(storage.meta_path(self.episode_id)).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return meta

    def abort(self) -> None:
        """Huỷ bản ghi dở và xoá artifact tạm (phiên rớt giữa chừng)."""
        self._close_videos()
        self._rows.clear()
        storage.delete_episode(self.episode_id)

    def _write_frame(self, camera: str, frame: bytes) -> None:
        self._queue_frame(camera, frame)

    def _queue_frame(self, camera: str, frame: bytes) -> None:
        if self._encoder_error is not None:
            raise RuntimeError("video encoder failed") from self._encoder_error
        if self._encoder_thread is None:
            self._encoder_thread = threading.Thread(
                target=self._encode_worker,
                name=f"telecollect-video-{self.episode_id}",
                daemon=False,
            )
            self._encoder_thread.start()
        # Never silently drop recording frames. Blocking only occurs if the
        # encoder remains behind for a sustained period.
        self._frame_queue.put((camera, bytes(frame)))

    def _encode_worker(self) -> None:
        try:
            while True:
                item = self._frame_queue.get()
                try:
                    if item is None:
                        return
                    camera, frame = item
                    self._encode_frame(camera, frame)
                finally:
                    self._frame_queue.task_done()
        except BaseException as exc:
            self._encoder_error = exc

    def _encode_frame(self, camera: str, frame: bytes) -> None:
        import av

        if camera not in self._containers:
            container = av.open(storage.video_path(self.episode_id, camera), mode="w")
            stream = container.add_stream("libx264", rate=self.control_hz)
            stream.width = self.image_size
            stream.height = self.image_size
            stream.pix_fmt = "yuv420p"
            self._containers[camera] = container
            self._streams[camera] = stream

        arr = np.frombuffer(frame, dtype=np.uint8).reshape(self.image_size, self.image_size, 3)
        av_frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in self._streams[camera].encode(av_frame):
            self._containers[camera].mux(packet)

    def _close_videos(self) -> None:
        if self._encoder_thread is not None:
            if self._encoder_error is None:
                self._frame_queue.put(None)
            self._encoder_thread.join()
            self._encoder_thread = None
        if self._encoder_error is not None:
            error = self._encoder_error
            self._encoder_error = None
            raise RuntimeError("video encoder failed") from error
        for camera, container in self._containers.items():
            for packet in self._streams[camera].encode():
                container.mux(packet)
            container.close()
        self._containers.clear()
        self._streams.clear()
