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
"""

from dataclasses import dataclass


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
    """

    def __init__(self, episode_id: str, task_name: str, operator_id: str) -> None:
        raise NotImplementedError

    def append(self, obs: object, action: list[float]) -> None:
        """Ghi một cặp observation-action. Gọi mỗi chu kỳ điều khiển."""
        raise NotImplementedError

    def finalize(self) -> EpisodeMeta:
        """Chốt episode: flush video, ghi parquet + meta.json, trả metadata.

        Chạy bước ẩn danh khuôn mặt trước khi ghi xuống nếu
        `enable_face_anonymization` bật.
        """
        raise NotImplementedError

    def abort(self) -> None:
        """Huỷ bản ghi dở và xoá artifact tạm (phiên rớt giữa chừng)."""
        raise NotImplementedError
