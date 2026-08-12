"""Định nghĩa task và điều kiện thành công.

Trách nhiệm: mô tả các task mà operator sẽ demo (tiêu chí nâng cao yêu cầu
thu dataset nhiều task), gồm model MJCF dùng, cách sinh trạng thái đầu, và
hàm chấm thành công. Registry ở đây là nguồn sự thật cho cả lúc thu demo lẫn
lúc đánh giá policy, để success rate hai bên so sánh được với nhau.

Backend mô phỏng là robosuite (xem `environment.py`): robosuite tự quản lý
model MJCF theo cặp (env_name, robot), nên ở đây `model_path` không phải một
đường dẫn file mà là chuỗi "EnvName:Robot" — id môi trường robosuite ghép
với robot cụ thể (vd "Lift:Panda"). `environment.RobotEnv` parse chuỗi này
để gọi `robosuite.make`.
"""

from collections.abc import Callable
from dataclasses import dataclass

_ROBOSUITE_SEP = ":"


@dataclass(frozen=True)
class TaskSpec:
    """Đặc tả một task demo."""

    name: str
    """Định danh dùng trong dataset và URL, vd: "pick_place_cube"."""

    description: str
    """Mô tả hiển thị cho operator trước khi bắt đầu ghi."""

    model_path: str
    """"EnvName:Robot" của robosuite dùng cho task này, vd "Lift:Panda"."""

    max_steps: int
    """Trần số bước một episode, tránh phiên treo vô hạn."""

    success_fn: Callable[..., bool]
    """Hàm chấm thành công, nhận env robosuite đang sống và trả về True/False."""


def parse_model_path(model_path: str) -> tuple[str, str]:
    """Tách "EnvName:Robot" thành (env_name, robot)."""
    env_name, _, robot = model_path.partition(_ROBOSUITE_SEP)
    if not env_name or not robot:
        raise ValueError(f"model_path phải có dạng 'EnvName:Robot', nhận '{model_path}'")
    return env_name, robot


_REGISTRY: dict[str, TaskSpec] = {}


def register(spec: TaskSpec) -> None:
    """Thêm một task vào registry."""
    _REGISTRY[spec.name] = spec


def get_task(name: str) -> TaskSpec:
    """Lấy đặc tả task theo tên; ném KeyError nếu chưa đăng ký."""
    return _REGISTRY[name]


def list_tasks() -> list[TaskSpec]:
    """Liệt kê mọi task khả dụng — API `/tasks` dùng hàm này."""
    return list(_REGISTRY.values())


def _check_success(env: object) -> bool:
    return bool(env._check_success())  # type: ignore[attr-defined]


register(
    TaskSpec(
        name="lift_cube",
        description="Gắp khối lập phương trên bàn và nhấc lên khỏi mặt bàn.",
        model_path="Lift:Panda",
        max_steps=500,
        success_fn=_check_success,
    )
)

register(
    TaskSpec(
        name="pick_place_can",
        description="Gắp lon trên bàn và đặt vào đúng vị trí mục tiêu.",
        model_path="PickPlaceCan:Panda",
        max_steps=400,
        success_fn=_check_success,
    )
)

register(
    TaskSpec(
        name="nut_assembly_square",
        description="Gắp đai ốc vuông và lắp vào đúng chốt trên bàn.",
        model_path="NutAssemblySquare:Panda",
        max_steps=500,
        success_fn=_check_success,
    )
)


def _tool_hang_success(env: object) -> bool:
    # Import lazily: listing tasks and starting the API must work even when the
    # optional ToolHang simulator environment is not installed in this venv.
    from src.sim.tool_hang import tool_hang_success

    return tool_hang_success(env)


register(
    TaskSpec(
        name="tool_hang",
        description=(
            "Giai doan 1: cam khung moc vao de dung; giai doan 2 treo co-le "
            "len moc se duoc mo sau khi hoan thien reachability."
        ),
        model_path="ToolHang:Panda",
        max_steps=1500,
        success_fn=_tool_hang_success,
    )
)
