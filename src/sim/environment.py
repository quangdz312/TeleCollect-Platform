"""Wrapper môi trường MuJoCo cho một robot mô phỏng.

Trách nhiệm: nạp model MJCF, giữ trạng thái vật lý của một phiên teleop, và
cung cấp vòng `reset` / `step` / `observe` cho control loop. Mỗi phiên teleop
sở hữu riêng một instance để các phiên không giẫm lên state của nhau.

Backend vật lý là robosuite (`robosuite.make`), không nạp MJCF trực tiếp:
`model_path` là chuỗi "EnvName:Robot" của robosuite (xem `tasks.py`), robosuite
tự chọn model MJCF tương ứng. Controller mặc định là OSC_POSE (điều khiển
delta pose Cartesian) — action mà `step()` nhận vào có 7 chiều
`[dx, dy, dz, drx, dry, drz, grip]`, đúng định dạng
`kinematics.teleop_input_to_action` sinh ra.
"""

import logging
from dataclasses import dataclass
from typing import Any

import mujoco
import numpy as np

from src.sim import tasks
from src.sim.render import FrameRenderer

_EEF_SITE = "gripper0_right_grip_site"
_LOG = logging.getLogger(__name__)


@dataclass
class Observation:
    """Một lát cắt quan sát tại thời điểm `t`.

    Đây là đơn vị dữ liệu được ghi lại đồng bộ cùng action, nên mọi trường
    phải serialise được để đẩy qua WebSocket và lưu xuống dataset.
    """

    t: float
    """Thời gian mô phỏng (giây) kể từ lúc reset — dùng để đồng bộ với action."""

    qpos: list[float]
    """Vị trí các khớp (7 khớp cánh tay + 2 khớp gripper)."""

    qvel: list[float]
    """Vận tốc các khớp, cùng thứ tự với `qpos`."""

    ee_pose: list[float]
    """Pose end-effector dạng [x, y, z, qw, qx, qy, qz]."""

    images: dict[str, bytes]
    """Frame RGB thô từng camera, khoá là tên camera."""

    privileged_state: list[float] | None = None
    """Trạng thái MuJoCo phẳng từ `sim.get_state()`, dùng cho auto-label độc lập."""


class RobotEnv:
    """Môi trường một robot trong MuJoCo (qua robosuite).

    Chữ ký dự kiến:
        def __init__(self, model_path: str, task: str, control_hz: int) -> None
        def reset(self, seed: int | None = None) -> Observation
        def step(self, action: list[float]) -> Observation
        def observe(self) -> Observation
        def is_success(self) -> bool
        def close(self) -> None
    """

    def __init__(
        self,
        model_path: str,
        task: str,
        control_hz: int,
        cameras: tuple[str, ...] = ("agentview",),
        image_size: int = 84,
        preview_camera: str | None = None,
        preview_size: int = 0,
    ) -> None:
        env_name, robot = tasks.parse_model_path(model_path)
        spec = tasks.get_task(task)

        self._task_name = task
        self._control_hz = control_hz
        self._t = 0.0

        self._cameras = list(cameras)
        self._image_size = image_size
        # Stream xem trực tiếp tách khỏi stream ghi (xem docstring `render.py`):
        # người điều khiển cần ảnh đủ lớn để nhìn rõ, còn dataset cố định ở
        # `image_size` mà policy sẽ thấy lúc huấn luyện. Đặt `preview_size = 0`
        # để tắt và dùng lại chính ảnh ghi.
        self._preview_camera = preview_camera
        self._preview_size = preview_size
        if task == "tool_hang":
            # ToolHang is a custom environment supplied by the companion skill
            # project. Keep its optional dependency out of normal app startup.
            from src.sim.tool_hang import make_tool_hang_environment

            self._env = make_tool_hang_environment(
                render=False,
                control_freq=control_hz,
                horizon=spec.max_steps,
                offscreen=True,
            )
        else:
            import robosuite as suite
            from robosuite.controllers import load_composite_controller_config

            self._env = suite.make(
                env_name=env_name,
                robots=robot,
                controller_configs=load_composite_controller_config(controller="BASIC"),
            has_renderer=False,
            has_offscreen_renderer=True,
            use_camera_obs=False,
            control_freq=control_hz,
            horizon=spec.max_steps,
            # Hết `horizon` bước, robosuite đánh dấu episode kết thúc và mọi
            # `step()` sau đó ném ValueError. Phiên teleop do người điều khiển
            # quyết định lúc nào dừng (và `max_steps` được kiểm ở tầng trên),
            # nên không để sim tự chốt giữa lúc đang thu demo.
            ignore_done=True,
            # Giữ nguyên một `MjSim` qua các lần reset. Mặc định robosuite huỷ
            # và dựng lại sim mỗi lần reset, khiến mọi thứ đang giữ tham chiếu
            # tới model/data cũ (FrameRenderer, cửa sổ mujoco viewer trong
            # scripts/teleop_ui.py) trỏ vào bộ nhớ chết và render sai.
                hard_reset=False,
            )
        # Every task gets the same review angle, so the operator and the
        # reviewer look at one layout no matter which task is running. Tasks
        # differ only in what the camera aims at (see `review_camera`).
        self._install_review_camera()
        self._drop_missing_cameras()
        self._rebuild_renderer()

    def _rebuild_renderer(self) -> None:
        # Phòng trường hợp `sim` vẫn bị thay (vd ai đó bỏ `hard_reset=False`):
        # renderer giữ tham chiếu sim cũ sẽ render sai, nên dựng lại sau reset.
        self._renderer = FrameRenderer(
            width=self._image_size,
            height=self._image_size,
            cameras=self._cameras,
            sim=self.sim,
        )
        # Renderer thứ hai chỉ phục vụ hiển thị — cùng `sim`, khác độ phân giải.
        # Bỏ hẳn khi nó sẽ render đúng thứ `self._renderer` vừa render: cùng
        # camera, cùng kích thước nghĩa là một lần render 640px lặp lại vô ích
        # mỗi tick (đo được ~2 ms), trong khi ngân sách 60 Hz chỉ có 16.7 ms.
        # `_maybe_publish_frame` tự lùi về ảnh đã ghi khi không có renderer này.
        redundant = (
            self._preview_camera in self._cameras
            and self._preview_size == self._image_size
        )
        self._preview_renderer = None
        if self._preview_camera and self._preview_size > 0 and not redundant:
            self._preview_renderer = FrameRenderer(
                width=self._preview_size,
                height=self._preview_size,
                cameras=[self._preview_camera],
                sim=self.sim,
            )

    def render_preview(self) -> bytes | None:
        """Frame RGB thô độ phân giải cao cho stream xem trực tiếp.

        Tách khỏi `Observation.images` một cách có chủ ý: ảnh này KHÔNG đi vào
        dataset, và được phép bỏ khi vòng điều khiển bận. Chỉ worker thread sở
        hữu sim được gọi (giống mọi thao tác render khác).
        """
        if self._preview_renderer is None:
            return None
        return self._preview_renderer.render().get(self._preview_camera)

    @property
    def preview_size(self) -> int:
        return self._preview_size if self._preview_renderer is not None else self._image_size

    @property
    def sim(self) -> Any:
        """`MjSim` sống của phiên — dùng cho `FrameRenderer` khác (vd stream xem trực tiếp)."""
        return self._env.sim

    def _install_review_camera(self) -> None:
        """Bake the review camera into this session's model. See `review_camera`."""
        from src.sim.review_camera import install_into_env

        install_into_env(self._env)

    def _drop_missing_cameras(self) -> None:
        """Keep only cameras this scene actually has, so rendering cannot fail.

        The configured list is shared by every task. A task where the review
        camera could not be installed must still record something, so an absent
        camera is replaced by `agentview` rather than left to raise on render.
        """
        model = self._env.sim.model
        available = {model.camera_id2name(i) for i in range(model.ncam)}
        kept = [name for name in self._cameras if name in available]
        if not kept:
            kept = ["agentview"] if "agentview" in available else sorted(available)[:1]
        self._cameras = kept
        if self._preview_camera not in available:
            _LOG.warning(
                "preview camera %r is not in this scene; falling back to %r, which is "
                "a different shot from the one review shows",
                self._preview_camera,
                kept[0],
            )
            self._preview_camera = kept[0]

    def reset(self, seed: int | None = None) -> Observation:
        """Đưa robot về trạng thái đầu của task, trả về quan sát đầu tiên."""
        if seed is not None:
            np.random.seed(seed)
        self._env.reset()
        # The camera is baked into the model at the object's pose, and reset
        # re-rolls that pose. Without re-aiming, a new scene is framed for where
        # the object used to be.
        self._install_review_camera()
        self._drop_missing_cameras()
        self._rebuild_renderer()
        self._t = 0.0
        return self._observation()

    def step(self, action: list[float]) -> Observation:
        """Áp một action trong đúng một chu kỳ điều khiển, trả về quan sát mới."""
        self._env.step(np.asarray(action, dtype=float))
        self._t += 1.0 / self._control_hz
        return self._observation()

    def observe(self) -> Observation:
        """Lấy quan sát hiện tại mà không tiến thời gian mô phỏng."""
        return self._observation()

    def is_success(self) -> bool:
        """Kiểm tra điều kiện thành công của task — dùng để gợi ý nhãn tự động."""
        spec = tasks.get_task(self._task_name)
        return spec.success_fn(self._env)

    def close(self) -> None:
        """Giải phóng tài nguyên mô phỏng và render."""
        self._env.close()

    @property
    def action_spec(self) -> dict[str, Any]:
        """Số chiều và biên của action, để frontend biết cách map input."""
        low, high = self._env.action_spec
        return {"dim": self._env.action_dim, "low": low.tolist(), "high": high.tolist()}

    def _observation(self) -> Observation:
        obs = self._env._get_observations()
        qpos = np.concatenate([obs["robot0_joint_pos"], obs["robot0_gripper_qpos"]]).tolist()
        qvel = np.concatenate([obs["robot0_joint_vel"], obs["robot0_gripper_qvel"]]).tolist()

        sim = self.sim
        site_id = sim.model.site_name2id(_EEF_SITE)
        pos = sim.data.site_xpos[site_id]
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, sim.data.site_xmat[site_id])
        ee_pose = [*pos.tolist(), *quat.tolist()]

        privileged_state = sim.get_state().flatten().astype(float).tolist()
        images = self._renderer.render()
        return Observation(
            t=self._t,
            qpos=qpos,
            qvel=qvel,
            ee_pose=ee_pose,
            privileged_state=privileged_state,
            images=images,
        )
