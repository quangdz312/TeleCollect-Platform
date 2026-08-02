"""Danh mục task demo.

Trách nhiệm: cho frontend biết có những task nào để operator chọn trước khi
mở phiên, kèm thông tin cần để dựng giao diện điều khiển (số chiều action,
biên, mô tả). Dữ liệu lấy từ registry trong `src.sim.tasks`.

Endpoint dự kiến:
    GET /tasks           ()               -> list[TaskResponse]
    GET /tasks/{name}    (name: str)      -> TaskResponse
    GET /tasks/{name}/stats (name: str)   -> TaskStatsResponse
        # số demo đã thu / đã duyệt / tỷ lệ thành công của task
"""

from fastapi import APIRouter

router = APIRouter(prefix="/tasks", tags=["tasks"])
