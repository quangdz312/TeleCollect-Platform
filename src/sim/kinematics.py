"""Động học thuận / nghịch và ánh xạ input người dùng sang lệnh khớp.

Trách nhiệm: người điều khiển nghĩ theo không gian Cartesian ("đẩy tay gắp
sang trái"), còn robot nhận lệnh theo không gian khớp. Module này dịch giữa
hai không gian đó, và là nơi duy nhất biết về giới hạn khớp.

Bài toán IK ở đây phải chạy trong ngân sách của một chu kỳ điều khiển
(mặc định 30 Hz → ~33 ms), nên ưu tiên IK xấp xỉ theo vận tốc thay vì giải
tối ưu toàn cục.

Chỉ hỗ trợ cánh tay Panda 7 khớp (robot mặc định của các task đăng ký trong
`tasks.py`) — `qpos` trong toàn module này nghĩa là 7 góc khớp cánh tay,
không gồm khớp gripper hay vật thể trong scene.

`teleop_input_to_action` không gọi `inverse_kinematics`: controller mặc định
của robosuite cho task teleop (OSC_POSE, xem `environment.py`) đã tự làm IK ở
tầng dưới khi nhận action là delta pose Cartesian, nên với đường điều khiển
thật sự dùng trong `control_loop.py` không cần giải IK tường minh ở đây.
`forward_kinematics`/`inverse_kinematics` vẫn được cung cấp như tiện ích độc
lập (vd. kiểm tra điểm đến có khả thi trước khi bắt đầu ghi).
"""

import numpy as np

_ARM_DOF = 7
_EEF_SITE = "gripper0_right_grip_site"

# Giới hạn khớp của Panda (rad), lấy từ MJCF robot mà robosuite dùng cho các
# task đã đăng ký. Nếu sau này thêm robot khác, đây là nơi cần mở rộng.
ARM_JOINT_LIMITS: list[tuple[float, float]] = [
    (-2.8973, 2.8973),
    (-1.7628, 1.7628),
    (-2.8973, 2.8973),
    (-3.0718, -0.0698),
    (-2.8973, 2.8973),
    (-0.0175, 3.7525),
    (-2.8973, 2.8973),
]

_scratch_env = None


def _scratch():
    """Env robosuite dùng riêng cho tính toán động học (không render, không step vật lý).

    Tạo một lần, dùng lại cho mọi lời gọi FK/IK — build model MJCF mỗi lần gọi
    quá tốn cho vòng điều khiển 30 Hz.
    """
    global _scratch_env
    if _scratch_env is None:
        import robosuite as suite
        from robosuite.controllers import load_composite_controller_config

        _scratch_env = suite.make(
            env_name="Lift",
            robots="Panda",
            controller_configs=load_composite_controller_config(controller="BASIC"),
            has_renderer=False,
            has_offscreen_renderer=False,
            use_camera_obs=False,
            control_freq=20,
            horizon=1,
        )
    return _scratch_env


def _site_pose(sim, qpos: list[float]) -> list[float]:
    sim.data.qpos[:_ARM_DOF] = qpos
    sim.forward()
    site_id = sim.model.site_name2id(_EEF_SITE)
    pos = sim.data.site_xpos[site_id].copy()
    xmat = sim.data.site_xmat[site_id].copy()
    quat = np.zeros(4)
    import mujoco

    mujoco.mju_mat2Quat(quat, xmat)
    return [*pos.tolist(), *quat.tolist()]


def forward_kinematics(qpos: list[float]) -> list[float]:
    """Từ góc khớp suy ra pose end-effector [x, y, z, qw, qx, qy, qz]."""
    env = _scratch()
    return _site_pose(env.sim, qpos)


def inverse_kinematics(
    target_pose: list[float],
    qpos_current: list[float],
    max_iters: int = 20,
) -> list[float]:
    """Từ pose end-effector mong muốn suy ra góc khớp.

    Nhận `qpos_current` làm điểm khởi tạo để nghiệm liên tục giữa các bước —
    tránh robot giật khi IK nhảy sang nghiệm khác. Dùng damped least squares
    trên Jacobian site — xấp xỉ, đủ nhanh cho một chu kỳ điều khiển, không
    phải nghiệm tối ưu toàn cục.
    """
    import mujoco

    env = _scratch()
    sim = env.sim
    site_id = sim.model.site_name2id(_EEF_SITE)
    target_pos = np.array(target_pose[:3])
    target_quat = np.array(target_pose[3:7])

    q = np.array(qpos_current[:_ARM_DOF], dtype=float)
    damping = 1e-4
    for _ in range(max_iters):
        sim.data.qpos[:_ARM_DOF] = q
        sim.forward()

        cur_pos = sim.data.site_xpos[site_id].copy()
        cur_xmat = sim.data.site_xmat[site_id].copy()
        cur_quat = np.zeros(4)
        mujoco.mju_mat2Quat(cur_quat, cur_xmat)

        pos_err = target_pos - cur_pos
        neg_cur_quat = np.zeros(4)
        mujoco.mju_negQuat(neg_cur_quat, cur_quat)
        err_quat = np.zeros(4)
        mujoco.mju_mulQuat(err_quat, target_quat, neg_cur_quat)
        ori_err = err_quat[1:4] * 2.0

        err = np.concatenate([pos_err, ori_err])
        if np.linalg.norm(err) < 1e-4:
            break

        jacp = sim.data.get_site_jacp(_EEF_SITE).reshape(3, sim.model.nv)
        jacr = sim.data.get_site_jacr(_EEF_SITE).reshape(3, sim.model.nv)
        jac = np.concatenate([jacp[:, :_ARM_DOF], jacr[:, :_ARM_DOF]], axis=0)

        lam = damping * np.eye(6)
        dq = jac.T @ np.linalg.solve(jac @ jac.T + lam, err)
        step_norm = np.linalg.norm(dq)
        max_step = 0.2
        if step_norm > max_step:
            dq *= max_step / step_norm
        q = np.array(clamp_to_joint_limits((q + dq).tolist()))

    return q.tolist()


def clamp_to_joint_limits(qpos: list[float]) -> list[float]:
    """Kẹp góc khớp vào biên hợp lệ trước khi gửi xuống sim."""
    return [min(max(angle, lo), hi) for angle, (lo, hi) in zip(qpos, ARM_JOINT_LIMITS, strict=False)]


def teleop_input_to_action(
    input_delta: dict[str, float],
    qpos_current: list[float],
    scale: float = 1.0,
) -> list[float]:
    """Ánh xạ delta từ bàn phím / gamepad / chuột thành action cấp khớp.

    `input_delta` là chuyển vị tương đối theo trục Cartesian và trạng thái
    gắp; `scale` điều chỉnh độ nhạy theo thiết bị nhập. Trả về action 7 chiều
    cho controller OSC_POSE của robosuite: [dx, dy, dz, drx, dry, drz, grip].
    `qpos_current` không dùng ở đây (OSC tự lo IK) — giữ trong chữ ký để
    tương lai chuyển sang controller cấp khớp mà không đổi API.
    """
    del qpos_current
    dx = input_delta.get("dx", 0.0) * scale
    dy = input_delta.get("dy", 0.0) * scale
    dz = input_delta.get("dz", 0.0) * scale
    drx = input_delta.get("drx", 0.0) * scale
    dry = input_delta.get("dry", 0.0) * scale
    drz = input_delta.get("drz", 0.0) * scale
    grip = input_delta.get("grip", 0.0)
    return [dx, dy, dz, drx, dry, drz, grip]
