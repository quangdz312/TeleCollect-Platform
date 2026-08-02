"""Gom demo đã duyệt thành dataset và export.

Trách nhiệm: đóng băng một tập demo `approved` thành dataset có phiên bản,
export sang LeRobot / RLDS và bàn giao cho lớp huấn luyện. Mỗi dataset gắn
một tag DVC để về sau truy ngược được model nào huấn luyện từ dữ liệu nào.

Dataset là bất biến sau khi build: thu thêm demo thì tạo phiên bản mới, không
sửa phiên bản cũ — nếu không, success rate của các lần huấn luyện sẽ không so
sánh được với nhau.

Endpoint dự kiến:
    GET  /datasets                  ()                          -> list[DatasetResponse]
    POST /datasets                  (name, task_names, format)  -> DatasetResponse
    GET  /datasets/{id}             (id: str)                   -> DatasetDetailResponse
    POST /datasets/{id}/export      (format: DatasetFormat)     -> ExportJobResponse
    GET  /datasets/{id}/download    (id: str)                   -> FileResponse
"""

from fastapi import APIRouter

router = APIRouter(prefix="/datasets", tags=["datasets"])
