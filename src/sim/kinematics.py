"""Động học thuận / nghịch và ánh xạ input người dùng sang lệnh khớp.

Trách nhiệm: người điều khiển nghĩ theo không gian Cartesian ("đẩy tay gắp
sang trái"), còn robot nhận lệnh theo không gian khớp. Module này dịch giữa
hai không gian đó, và là nơi duy nhất biết về giới hạn khớp.

Bài toán IK ở đây phải chạy trong ngân sách của một chu kỳ điều khiển
(mặc định 30 Hz → ~33 ms), nên ưu tiên IK xấp xỉ theo vận tốc thay vì giải
tối ưu toàn cục.
"""


def forward_kinematics(qpos: list[float]) -> list[float]:
    """Từ góc khớp suy ra pose end-effector [x, y, z, qw, qx, qy, qz]."""
    raise NotImplementedError


def inverse_kinematics(
    target_pose: list[float],
    qpos_current: list[float],
    max_iters: int = 20,
) -> list[float]:
    """Từ pose end-effector mong muốn suy ra góc khớp.

    Nhận `qpos_current` làm điểm khởi tạo để nghiệm liên tục giữa các bước —
    tránh robot giật khi IK nhảy sang nghiệm khác.
    """
    raise NotImplementedError


def clamp_to_joint_limits(qpos: list[float]) -> list[float]:
    """Kẹp góc khớp vào biên hợp lệ trước khi gửi xuống sim."""
    raise NotImplementedError


def teleop_input_to_action(
    input_delta: dict[str, float],
    qpos_current: list[float],
    scale: float = 1.0,
) -> list[float]:
    """Ánh xạ delta từ bàn phím / gamepad / chuột thành action cấp khớp.

    `input_delta` là chuyển vị tương đối theo trục Cartesian và trạng thái
    gắp; `scale` điều chỉnh độ nhạy theo thiết bị nhập.
    """
    raise NotImplementedError
