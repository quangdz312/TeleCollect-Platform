"""Render offscreen từ MuJoCo thành frame gửi cho frontend.

Trách nhiệm: dựng ảnh camera trong sim và nén thành định dạng đủ nhẹ để đẩy
realtime qua WebSocket. Đây là mắt xích ảnh hưởng lớn nhất tới độ trễ teleop
và chi phí lưu trữ, nên chất lượng/độ phân giải để cấu hình được, và stream
xem trực tiếp tách khỏi stream ghi xuống dataset (xem trực tiếp ưu tiên độ
trễ thấp, bản ghi ưu tiên chất lượng).

Render offscreen đọc trạng thái vật lý *hiện tại* của một phiên — không thể
tự tạo state đó một cách độc lập. Vì vậy khác với chữ ký gốc, `FrameRenderer`
ở đây nhận thêm `sim` (đối tượng `MjSim` của robosuite, lấy từ
`RobotEnv.sim`): width/height/cameras vẫn cấu hình riêng, để một phiên có thể
mở hai `FrameRenderer` cùng trỏ vào một `sim` — một độ phân giải thấp cho
stream xem trực tiếp, một độ phân giải cao cho stream ghi.
"""

from typing import Any

import cv2
import mujoco
import numpy as np


class FrameRenderer:
    """Render frame từ một hoặc nhiều camera của scene.

    Chữ ký dự kiến:
        def __init__(self, width: int, height: int, cameras: list[str], sim: Any) -> None
        def render(self) -> dict[str, bytes]
        def encode_jpeg(self, frame: bytes, quality: int = 80) -> bytes
        def close(self) -> None
    """

    def __init__(self, width: int, height: int, cameras: list[str], sim: Any) -> None:
        self.width = width
        self.height = height
        self.cameras = cameras
        self._sim = sim

    def _bind(self) -> None:
        """Trỏ OpenGL về context/buffer offscreen trước khi render.

        robosuite chỉ bind context và buffer offscreen đúng một lần lúc dựng
        render context; `render()` của nó giả định binding đó còn nguyên. Giả
        định này vỡ ngay khi tiến trình có thêm một context khác vẽ xen kẽ —
        vd cửa sổ teleop trong `scripts/teleop_ui.py` vẽ mỗi chu kỳ điều khiển.
        Khi đó ảnh quan sát ghi xuống dataset đứng im ở khung hình cũ, mà nhìn
        vào file mp4 không thấy sai ở đâu cả. Bind lại mỗi lần render là rẻ và
        làm FrameRenderer độc lập với mọi context khác.
        """
        context = self._sim._render_context_offscreen
        context.gl_ctx.make_current()
        mujoco.mjr_setBuffer(mujoco.mjtFramebuffer.mjFB_OFFSCREEN, context.con)

    def render(self) -> dict[str, bytes]:
        """Render tất cả camera, trả về map tên camera -> frame RGB thô.

        `[::-1]` lật dọc là BẮT BUỘC, không phải tuỳ chọn thẩm mỹ: OpenGL (và
        do đó `sim.render`) đánh số hàng pixel từ dưới lên, còn mọi thứ đọc
        ảnh sau đó — PyAV/H.264, JPEG, PIL, mắt người — giả định hàng đầu tiên
        là hàng trên cùng. Thiếu bước này thì video ghi xuống dataset bị lộn
        ngược: bàn nằm trên, đế robot treo lơ lửng dưới. Sai kiểu đó không làm
        chương trình lỗi, chỉ khiến policy học từ ảnh lật.
        """
        self._bind()
        return {
            camera: self._sim.render(camera_name=camera, width=self.width, height=self.height)[::-1]
            .astype(np.uint8)
            .tobytes()
            for camera in self.cameras
        }

    def encode_jpeg(self, frame: bytes, quality: int = 80) -> bytes:
        """Nén một frame để đẩy qua WebSocket."""
        rgb = np.frombuffer(frame, dtype=np.uint8).reshape(self.height, self.width, 3)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("Không encode được frame thành JPEG")
        return encoded.tobytes()

    def close(self) -> None:
        """Giải phóng context render.

        `sim` không thuộc sở hữu của `FrameRenderer` (nhiều renderer có thể
        chia sẻ cùng một sim của phiên) nên không giải phóng ở đây —
        `RobotEnv.close()` mới là nơi đóng sim.
        """
