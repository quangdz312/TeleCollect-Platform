"""Chạy huấn luyện behavior cloning và xem kết quả đánh giá.

Trách nhiệm: khởi động job huấn luyện từ một dataset đã đóng băng, theo dõi
tiến độ, và trả về success rate đo trong sim — chỉ số nghiệm thu cuối cùng
của cả dự án.

Huấn luyện chạy nền chứ không chặn request; endpoint chỉ tạo job và trả job
id để client hỏi trạng thái sau.

Endpoint dự kiến:
    POST /training/jobs               (dataset_id, config)  -> TrainingJobResponse
    GET  /training/jobs               ()                    -> list[TrainingJobResponse]
    GET  /training/jobs/{id}          (id: str)             -> TrainingJobResponse
    POST /training/jobs/{id}/cancel   (id: str)             -> TrainingJobResponse
    POST /training/jobs/{id}/evaluate (num_episodes: int)   -> EvalResultResponse
    GET  /training/policies           ()                    -> list[PolicyResponse]
"""

from fastapi import APIRouter

router = APIRouter(prefix="/training", tags=["training"])
