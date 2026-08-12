"""UI teleop: điều khiển tay robot bằng bàn phím + chuột và lưu episode.

Vì sao tự dựng cửa sổ GLFW thay vì dùng viewer có sẵn:

- `robosuite.scripts.collect_human_demonstrations` bắt phím bằng `pynput`, tức
  nghe sự kiện bàn phím ở cấp hệ thống. Wayland chặn việc đó vì lý do bảo mật
  nên trên phiên Wayland bấm phím không có tác dụng.
- `mujoco.viewer` nhận phím từ cửa sổ đang focus (chạy được trên Wayland),
  nhưng nó đã chiếm gần hết bảng chữ cái cho phím tắt hiển thị của riêng nó
  (W = wireframe, S = shadow, A = auto-connect, D = static body, dấu cách =
  tạm dừng, phím mũi tên = tua bước...). Không còn phím trống cho teleop, và
  phím người dùng bấm sẽ vừa điều khiển vừa bật/tắt chế độ hiển thị.

Cửa sổ GLFW dựng ở đây chỉ đăng ký đúng những phím bên dưới, nên không có xung
đột, và vẫn nhận phím theo cửa sổ đang focus như mọi ứng dụng khác trên Wayland.

Bố cục: toàn màn hình, chia đôi.

    +---------------------------+------------------+
    |                           |  trạng thái      |
    |                           |  [nút] [nút]     |
    |   khung sim (hình vuông)  |  bàn rê chuột    |
    |                           |  phím tắt        |
    +---------------------------+------------------+

Khung sim để vuông có lý do: quan sát ghi xuống dataset là ảnh vuông
(`--image-size`, mặc định 128×128), nên khung vuông cho người điều khiển thấy
đúng khuôn hình mà policy sẽ thấy lúc huấn luyện, không bị méo hay cắt cạnh.

Việc ghi là chủ động: sim chạy tự do từ lúc mở, chỉ ghi xuống đĩa trong khoảng
giữa hai lần bấm BẮT ĐẦU GHI và DỪNG & LƯU. Nhờ vậy operator có thể đưa tay gắp
vào tư thế chuẩn bị, thử vài đường trước, rồi mới ghi phần thao tác sạch —
không lẫn đoạn mò mẫm vào dataset, và không có chuyện episode tự chốt giữa
chừng theo số bước.

Điều khiển bằng bàn phím (giữ phím để đi liên tục):
    mũi tên trái/phải   di chuyển theo trục x
    mũi tên lên/xuống   di chuyển theo trục y
    W / S               nâng lên / hạ xuống (trục z)
    A / D               xoay cổ tay (yaw)
    dấu cách            đóng / mở tay gắp
    ENTER               công tắc: bắt đầu ghi / dừng và lưu
    BACKSPACE           bỏ đoạn đang ghi (không lưu)
    R                   đưa scene về trạng thái đầu (khi không ghi)
    ESC                 thoát (đoạn đang ghi bị bỏ)

Điều khiển bằng chuột:
    trong bảng phải, giữ chuột trái   rê để đi theo x-y (càng xa tâm càng nhanh)
    trong bảng phải, giữ chuột phải   rê ngang để xoay cổ tay
    trong bảng phải, lăn chuột        nâng / hạ theo trục z
    trong khung sim, kéo / lăn        xoay / tịnh tiến / zoom góc nhìn

Chạy:
    make teleop
    # hoặc: PYTHONPATH=. python scripts/teleop_ui.py --task lift_cube
"""

import argparse
import time
import uuid
from dataclasses import dataclass

import glfw
import mujoco

from src.config import get_settings
from src.core.recorder import EpisodeRecorder
from src.sim import kinematics, tasks
from src.sim.environment import RobotEnv

POS_STEP = 0.6
ROT_STEP = 0.6

# Bảng điều khiển bên phải cần tối thiểu ngần này pixel mới đủ chỗ cho bàn rê
# chuột và phần chữ; khung sim vuông nhường chỗ nếu màn hình quá hẹp.
MIN_PANEL_WIDTH = 340


@dataclass(frozen=True)
class Button:
    """Một nút hành động trong bảng điều khiển."""

    label: str
    color: tuple[float, float, float]


MOVE_KEYS = {
    glfw.KEY_LEFT: ("dx", -POS_STEP),
    glfw.KEY_RIGHT: ("dx", POS_STEP),
    glfw.KEY_DOWN: ("dy", -POS_STEP),
    glfw.KEY_UP: ("dy", POS_STEP),
    glfw.KEY_W: ("dz", POS_STEP),
    glfw.KEY_S: ("dz", -POS_STEP),
    glfw.KEY_A: ("drz", -ROT_STEP),
    glfw.KEY_D: ("drz", ROT_STEP),
}


class Window:
    """Cửa sổ GLFW: khung sim vuông bên trái, bảng điều khiển chuột bên phải."""

    def __init__(self, model, data, title: str, fullscreen: bool = True) -> None:
        if not glfw.init():
            raise RuntimeError("Không khởi tạo được GLFW — máy có màn hình không?")

        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)
        if fullscreen:
            width, height = mode.size.width, mode.size.height
            self._window = glfw.create_window(width, height, title, monitor, None)
        else:
            width, height = int(mode.size.width * 0.8), int(mode.size.height * 0.8)
            self._window = glfw.create_window(width, height, title, None, None)
        if not self._window:
            glfw.terminate()
            raise RuntimeError("Không tạo được cửa sổ GLFW")

        glfw.make_context_current(self._window)
        glfw.swap_interval(1)

        self._model = model
        self._data = data
        self.camera = mujoco.MjvCamera()
        self.option = mujoco.MjvOption()
        # MjvOption mặc định bật cả nhóm geom 0 (lưới va chạm) lẫn nhóm 1 (lưới
        # hiển thị), nên vỏ va chạm thô lòi ra đè lên vỏ đẹp, khớp trông lởm
        # chởm. robosuite tắt nhóm 0 cho renderer của nó (environments/base.py
        # theo cờ `render_collision_mesh`, mặc định tắt); cửa sổ tự dựng ở đây
        # phải tự làm điều tương tự.
        self.option.geomgroup[0] = 0
        # Camera mặc định của MuJoCo lùi ra đủ xa để thấy cả phòng, robot chỉ
        # còn vài chục pixel. Đóng khung quanh mặt bàn để nhìn rõ tay gắp và
        # vật thể — người điều khiển vẫn kéo chuột đổi góc nhìn được.
        self.camera.lookat[:] = [0.0, 0.0, 0.9]
        self.camera.distance = 1.6
        self.camera.azimuth = 135.0
        self.camera.elevation = -25.0
        self._scene = mujoco.MjvScene(model, maxgeom=10_000)
        self._context = mujoco.MjrContext(model, mujoco.mjtFontScale.mjFONTSCALE_150.value)

        self.held: set[int] = set()
        self.pressed: list[int] = []
        self._cursor = (0.0, 0.0)
        self._last_cursor = (0.0, 0.0)
        self._drag_in_sim = False
        self._z_scroll = 0.0

        self.clicks: list[tuple[float, float]] = []

        glfw.set_key_callback(self._window, self._on_key)
        glfw.set_cursor_pos_callback(self._window, self._on_cursor)
        glfw.set_scroll_callback(self._window, self._on_scroll)
        glfw.set_mouse_button_callback(self._window, self._on_mouse_button)

    # ----- bố cục -----

    def layout(self) -> tuple[mujoco.MjrRect, mujoco.MjrRect, mujoco.MjrRect]:
        """Trả về (toàn khung, khung sim vuông, bảng điều khiển)."""
        width, height = glfw.get_framebuffer_size(self._window)
        side = min(height, max(200, width - MIN_PANEL_WIDTH))
        full = mujoco.MjrRect(0, 0, width, height)
        sim = mujoco.MjrRect(0, (height - side) // 2, side, side)
        panel = mujoco.MjrRect(side, 0, width - side, height)
        return full, sim, panel

    def _button_rects(self, panel: mujoco.MjrRect) -> tuple[mujoco.MjrRect, ...]:
        """Hai nút hành động, nằm ngay dưới khối trạng thái."""
        height = int(panel.height * 0.075)
        bottom = panel.bottom + panel.height - int(panel.height * 0.17) - height
        margin = 20
        width = (panel.width - 3 * margin) // 2
        return (
            mujoco.MjrRect(panel.left + margin, bottom, width, height),
            mujoco.MjrRect(panel.left + 2 * margin + width, bottom, width, height),
        )

    def _pad_rect(self, panel: mujoco.MjrRect) -> mujoco.MjrRect:
        """Bàn rê chuột — hình vuông, nằm giữa bảng điều khiển.

        Bảng chia bốn tầng từ trên xuống: trạng thái, hai nút hành động, bàn rê
        chuột, rồi phím tắt. Kích thước bàn rê chừa đủ chỗ cho các khối kia để
        chúng không đè lên nhau khi màn hình thấp.
        """
        buttons = self._button_rects(panel)
        size = min(panel.width - 80, int(panel.height * 0.32))
        left = panel.left + (panel.width - size) // 2
        bottom = buttons[0].bottom - 20 - size
        return mujoco.MjrRect(left, bottom, size, size)

    def button_hit(self, pos: tuple[float, float]) -> int | None:
        """Chỉ số nút bị bấm tại vị trí `pos`, hoặc None nếu không trúng nút nào."""
        _, _, panel = self.layout()
        for index, rect in enumerate(self._button_rects(panel)):
            if self._in_rect(pos[0], pos[1], rect):
                return index
        return None

    def _cursor_fb(self) -> tuple[float, float]:
        """Vị trí con trỏ theo hệ toạ độ framebuffer (gốc dưới-trái như MuJoCo)."""
        _, height = glfw.get_framebuffer_size(self._window)
        scale = self._fb_scale()
        return self._cursor[0] * scale, height - self._cursor[1] * scale

    def _fb_scale(self) -> float:
        win_w, _ = glfw.get_window_size(self._window)
        fb_w, _ = glfw.get_framebuffer_size(self._window)
        return fb_w / win_w if win_w else 1.0

    def _in_rect(self, x: float, y: float, rect: mujoco.MjrRect) -> bool:
        return rect.left <= x <= rect.left + rect.width and rect.bottom <= y <= rect.bottom + rect.height

    # ----- sự kiện -----

    def _on_key(self, _window, key, _scancode, action, _mods) -> None:
        if action == glfw.PRESS:
            self.held.add(key)
            self.pressed.append(key)
        elif action == glfw.RELEASE:
            self.held.discard(key)

    def _on_mouse_button(self, window, button, action, _mods) -> None:
        if button != glfw.MOUSE_BUTTON_LEFT or action != glfw.PRESS:
            return
        # Đọc thẳng vị trí con trỏ thay vì dùng vị trí lưu từ lần di chuyển
        # trước: bấm chuột mà không rê thì vị trí lưu có thể đã cũ.
        xpos, ypos = glfw.get_cursor_pos(window)
        _, height = glfw.get_framebuffer_size(window)
        scale = self._fb_scale()
        self.clicks.append((xpos * scale, height - ypos * scale))

    def take_clicks(self) -> list[tuple[float, float]]:
        """Lấy và xoá danh sách cú bấm chuột trái kể từ lần gọi trước."""
        clicks, self.clicks = self.clicks, []
        return clicks

    def _on_cursor(self, window, xpos, ypos) -> None:
        dx, dy = xpos - self._last_cursor[0], ypos - self._last_cursor[1]
        self._last_cursor = (xpos, ypos)
        self._cursor = (xpos, ypos)

        left = glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS
        right = glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_RIGHT) == glfw.PRESS
        if not (left or right):
            self._drag_in_sim = False
            return

        _, sim_rect, _ = self.layout()
        cx, cy = self._cursor_fb()
        if not self._drag_in_sim and self._in_rect(cx, cy, sim_rect):
            self._drag_in_sim = True
        if not self._drag_in_sim:
            return  # đang rê trong bảng điều khiển — không đụng tới camera

        _, height = glfw.get_window_size(window)
        action = mujoco.mjtMouse.mjMOUSE_ROTATE_V if left else mujoco.mjtMouse.mjMOUSE_MOVE_V
        mujoco.mjv_moveCamera(self._model, action, dx / height, dy / height, self._scene, self.camera)

    def _on_scroll(self, _window, _xoffset, yoffset) -> None:
        _, sim_rect, _ = self.layout()
        cx, cy = self._cursor_fb()
        if self._in_rect(cx, cy, sim_rect):
            mujoco.mjv_moveCamera(
                self._model,
                mujoco.mjtMouse.mjMOUSE_ZOOM,
                0.0,
                -0.05 * yoffset,
                self._scene,
                self.camera,
            )
        else:
            # Lăn trong bảng điều khiển: nâng/hạ theo z. Cộng dồn rồi để nó tắt
            # dần, nếu không mỗi nấc lăn chỉ nhúc nhích đúng một chu kỳ.
            self._z_scroll = max(-1.0, min(1.0, self._z_scroll + 0.35 * yoffset))

    # ----- đọc input -----

    def take_pressed(self) -> list[int]:
        """Lấy và xoá danh sách phím vừa được bấm (sự kiện một lần)."""
        pressed, self.pressed = self.pressed, []
        return pressed

    def mouse_input(self) -> dict[str, float]:
        """Delta sinh ra từ chuột trong bảng điều khiển."""
        delta: dict[str, float] = {}

        if abs(self._z_scroll) > 1e-3:
            delta["dz"] = self._z_scroll * POS_STEP
            self._z_scroll *= 0.85
        else:
            self._z_scroll = 0.0

        if self._drag_in_sim:
            return delta

        _, _, panel = self.layout()
        pad = self._pad_rect(panel)
        cx, cy = self._cursor_fb()
        if not self._in_rect(cx, cy, pad):
            return delta

        left = glfw.get_mouse_button(self._window, glfw.MOUSE_BUTTON_LEFT)
        right = glfw.get_mouse_button(self._window, glfw.MOUSE_BUTTON_RIGHT)
        # Lệch khỏi tâm bàn rê, chuẩn hoá về [-1, 1] — càng xa tâm càng nhanh.
        offset_x = (cx - (pad.left + pad.width / 2)) / (pad.width / 2)
        offset_y = (cy - (pad.bottom + pad.height / 2)) / (pad.height / 2)

        if left == glfw.PRESS:
            delta["dx"] = offset_x * POS_STEP
            delta["dy"] = offset_y * POS_STEP
        if right == glfw.PRESS:
            delta["drz"] = offset_x * ROT_STEP
        return delta

    def is_open(self) -> bool:
        return not glfw.window_should_close(self._window)

    # ----- vẽ -----

    def draw(self, status: str, help_text: str, buttons: tuple[Button, ...] = ()) -> None:
        """Vẽ một khung hình rồi đưa lên màn hình."""
        self.render_frame(status, help_text, buttons)
        glfw.swap_buffers(self._window)
        glfw.poll_events()

    def render_frame(self, status: str, help_text: str, buttons: tuple[Button, ...] = ()) -> None:
        """Vẽ khung hình vào back buffer, chưa swap — tách ra để test đọc pixel."""
        glfw.make_context_current(self._window)
        # Bắt buộc: mỗi chu kỳ điều khiển, việc ghi video render offscreen
        # (FrameRenderer, cỡ image_size) để lại framebuffer đang trỏ vào buffer
        # offscreen. Không trỏ ngược về buffer cửa sổ thì toàn bộ khung hình bị
        # vẽ lọt vào vùng image_size×image_size ở góc dưới-trái, phần còn lại đen.
        mujoco.mjr_setBuffer(mujoco.mjtFramebuffer.mjFB_WINDOW.value, self._context)
        full, sim_rect, panel = self.layout()

        mujoco.mjr_rectangle(full, 0.11, 0.12, 0.14, 1.0)
        mujoco.mjv_updateScene(
            self._model,
            self._data,
            self.option,
            None,
            self.camera,
            mujoco.mjtCatBit.mjCAT_ALL.value,
            self._scene,
        )
        mujoco.mjr_render(sim_rect, self._scene, self._context)

        self._draw_panel(panel, status, help_text, buttons)

    def _draw_buttons(self, panel: mujoco.MjrRect, buttons: tuple[Button, ...]) -> None:
        cursor = self._cursor_fb()
        for rect, button in zip(self._button_rects(panel), buttons, strict=False):
            red, green, blue = button.color
            if self._in_rect(cursor[0], cursor[1], rect):  # sáng lên khi rê vào
                red, green, blue = min(red + 0.15, 1.0), min(green + 0.15, 1.0), min(blue + 0.15, 1.0)
            mujoco.mjr_rectangle(rect, red, green, blue, 1.0)

    def _draw_button_labels(self, panel: mujoco.MjrRect, buttons: tuple[Button, ...]) -> None:
        # Vẽ chữ sau cùng để không bị nền nút phủ lên. mjr_overlay neo chữ vào
        # góc nên thụt rect vào một chút cho chữ nằm gần giữa nút.
        for rect, button in zip(self._button_rects(panel), buttons, strict=False):
            inset = mujoco.MjrRect(rect.left + 14, rect.bottom - 4, rect.width, rect.height)
            mujoco.mjr_overlay(
                mujoco.mjtFont.mjFONT_NORMAL.value,
                mujoco.mjtGridPos.mjGRID_TOPLEFT.value,
                inset,
                button.label,
                "",
                self._context,
            )

    def _draw_panel(
        self,
        panel: mujoco.MjrRect,
        status: str,
        help_text: str,
        buttons: tuple[Button, ...],
    ) -> None:
        mujoco.mjr_rectangle(panel, 0.14, 0.15, 0.18, 1.0)

        self._draw_buttons(panel, buttons)

        pad = self._pad_rect(panel)
        cx, cy = self._cursor_fb()
        active = self._in_rect(cx, cy, pad) and not self._drag_in_sim
        shade = 0.30 if active else 0.22
        mujoco.mjr_rectangle(pad, shade, shade + 0.04, shade + 0.10, 1.0)

        # Vạch chữ thập đánh dấu tâm — rê ra xa tâm thì tay gắp đi nhanh hơn.
        cx_pad = pad.left + pad.width // 2
        cy_pad = pad.bottom + pad.height // 2
        mujoco.mjr_rectangle(mujoco.MjrRect(pad.left, cy_pad, pad.width, 1), 0.45, 0.47, 0.52, 1.0)
        mujoco.mjr_rectangle(mujoco.MjrRect(cx_pad, pad.bottom, 1, pad.height), 0.45, 0.47, 0.52, 1.0)

        if active:
            marker = 10
            mujoco.mjr_rectangle(
                mujoco.MjrRect(int(cx) - marker // 2, int(cy) - marker // 2, marker, marker),
                0.95,
                0.75,
                0.25,
                1.0,
            )

        font = mujoco.mjtFont.mjFONT_NORMAL.value
        mujoco.mjr_overlay(
            font,
            mujoco.mjtGridPos.mjGRID_TOPLEFT.value,
            pad,
            "BAN RE CHUOT",
            "",
            self._context,
        )
        self._draw_button_labels(panel, buttons)
        mujoco.mjr_overlay(
            font,
            mujoco.mjtGridPos.mjGRID_TOPLEFT.value,
            panel,
            status,
            "",
            self._context,
        )
        mujoco.mjr_overlay(
            font,
            mujoco.mjtGridPos.mjGRID_BOTTOMLEFT.value,
            panel,
            help_text,
            "",
            self._context,
        )

    def close(self) -> None:
        glfw.terminate()


class TeleopUI:
    """Vòng teleop: đọc phím/chuột -> action -> step sim -> ghi observation."""

    HELP = (
        "mui ten : x / y     W / S : nang / ha\n"
        "A / D   : xoay      dau cach : tay gap\n"
        "CHUOT trong bang: trai = di x/y,\n"
        "  phai = xoay, lan = nang / ha\n"
        "ENTER : bat dau / dung ghi\n"
        "BACKSPACE : bo    R : reset\n"
        "ESC : thoat"
    )

    BUTTON_START = Button("BAT DAU GHI  (ENTER)", (0.16, 0.42, 0.24))
    BUTTON_RESET = Button("RESET SCENE  (R)", (0.26, 0.27, 0.32))
    BUTTON_STOP = Button("DUNG & LUU  (ENTER)", (0.45, 0.28, 0.13))
    BUTTON_DISCARD = Button("BO  (BACKSPACE)", (0.42, 0.18, 0.20))

    def __init__(self, task_name: str, operator_id: str, image_size: int) -> None:
        self.spec = tasks.get_task(task_name)
        self.control_hz = get_settings().control_hz
        self.image_size = image_size
        self.operator_id = operator_id

        self.env = RobotEnv(
            model_path=self.spec.model_path,
            task=self.spec.name,
            control_hz=self.control_hz,
            image_size=image_size,
        )
        self.obs = self.env.reset()

        self._grip = -1.0
        self.recorder: EpisodeRecorder | None = None
        self._saved = 0

    def _status(self) -> str:
        # Font bitmap của MuJoCo không có gạch dài / dấu tiếng Việt, ký tự lạ
        # hiện thành ô vuông — chữ trên bảng dùng ASCII thuần.
        if self.recorder is None:
            state = "CHUA GHI - bam BAT DAU GHI"
        else:
            state = f"DANG GHI - {self.recorder.num_steps} buoc"
        return (
            f"Task: {self.spec.name}\n"
            f"{state}\n"
            f"Tay gap: {'DONG' if self._grip > 0 else 'MO'}\n"
            f"Da luu: {self._saved} episode"
        )

    def _buttons(self) -> tuple[Button, ...]:
        if self.recorder is None:
            return (self.BUTTON_START, self.BUTTON_RESET)
        return (self.BUTTON_STOP, self.BUTTON_DISCARD)

    def _on_action(self, index: int) -> None:
        """Xử lý nút thứ `index` — thứ tự khớp với `_buttons()`."""
        if self.recorder is None:
            if index == 0:
                self._start_recording()
            else:
                self._reset_scene()
        elif index == 0:
            self._stop_recording(save=True)
        else:
            self._stop_recording(save=False)

    def run(self, fullscreen: bool = True) -> None:
        window = Window(
            model=self.env.sim.model._model,
            data=self.env.sim.data._data,
            title=f"TeleCollect — {self.spec.name}",
            fullscreen=fullscreen,
        )
        period = 1.0 / self.control_hz
        quit_requested = False

        print("Sim đang chạy. Bấm BAT DAU GHI (hoặc ENTER) khi muốn ghi episode.")
        try:
            while window.is_open() and not quit_requested:
                tick = time.time()

                for key in window.take_pressed():
                    if key == glfw.KEY_ESCAPE:
                        quit_requested = True
                    elif key == glfw.KEY_SPACE:
                        self._grip = -self._grip
                    elif key == glfw.KEY_ENTER:
                        # ENTER là công tắc: chưa ghi thì bắt đầu, đang ghi thì
                        # dừng và lưu — cùng hành vi với nút trái trên bảng.
                        self._on_action(0)
                    elif key == glfw.KEY_BACKSPACE and self.recorder is not None:
                        self._stop_recording(save=False)
                    elif key == glfw.KEY_R and self.recorder is None:
                        self._reset_scene()

                for click in window.take_clicks():
                    index = window.button_hit(click)
                    if index is not None:
                        self._on_action(index)

                input_delta = {"grip": self._grip}
                input_delta.update(window.mouse_input())
                for key in window.held:
                    if key in MOVE_KEYS:
                        axis, value = MOVE_KEYS[key]
                        input_delta[axis] = value

                action = kinematics.teleop_input_to_action(input_delta, self.obs.qpos)
                self.obs = self.env.step(action)
                if self.recorder is not None:
                    self.recorder.append(self.obs, action)

                window.draw(
                    status=self._status(),
                    help_text=self.HELP,
                    buttons=self._buttons(),
                )

                elapsed = time.time() - tick
                if elapsed < period:
                    time.sleep(period - elapsed)
        finally:
            if self.recorder is not None:
                self.recorder.abort()
            self.env.close()
            window.close()

        print(f"Đã lưu {self._saved} episode vào {get_settings().storage_dir}/")

    def _start_recording(self) -> None:
        episode_id = f"{self.spec.name}_{uuid.uuid4().hex[:8]}"
        self.recorder = EpisodeRecorder(
            episode_id=episode_id,
            task_name=self.spec.name,
            operator_id=self.operator_id,
            control_hz=self.control_hz,
            image_size=self.image_size,
        )
        print(f"[bắt đầu ghi] {episode_id}")

    def _stop_recording(self, save: bool) -> None:
        """Dừng ghi. Không tự reset scene — operator chủ động bấm RESET.

        Giữ nguyên hiện trạng sau khi lưu để còn xem lại kết quả vừa thao tác
        (vd tay gắp có thật sự nhấc được khối lên không) trước khi sang demo
        tiếp theo.
        """
        if self.recorder is None:
            return

        if save:
            success = self.env.is_success()
            meta = self.recorder.finalize()
            self._saved += 1
            print(f"[lưu] {meta.episode_id}: {meta.num_steps} bước, {meta.duration_s:.1f}s, task success={success}")
        else:
            self.recorder.abort()
            print("[bỏ] episode đã huỷ")
        self.recorder = None

    def _reset_scene(self) -> None:
        self.obs = self.env.reset()
        self._grip = -1.0
        print("[reset] scene đã về trạng thái đầu")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="lift_cube")
    parser.add_argument("--operator", default="local", help="Định danh operator ghi demo")
    parser.add_argument("--image-size", type=int, default=128)
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Chạy trong cửa sổ thay vì toàn màn hình (tiện lúc debug)",
    )
    args = parser.parse_args()

    get_settings().ensure_data_dirs()

    spec = tasks.get_task(args.task)
    print(f"Task: {spec.name} — {spec.description}")

    TeleopUI(task_name=args.task, operator_id=args.operator, image_size=args.image_size).run(
        fullscreen=not args.windowed
    )


if __name__ == "__main__":
    main()
