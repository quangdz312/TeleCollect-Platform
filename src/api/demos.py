"""Xem lại, cắt, gắn nhãn và duyệt demonstration.

Trách nhiệm: vòng human-in-the-loop. Một episode đi qua các trạng thái
`recorded → labeled → approved | rejected`, và chỉ demo `approved` mới được
gom vào dataset huấn luyện.

Cắt (trim) chỉ ghi lại khoảng [start_step, end_step] vào metadata, không cắt
file gốc — để reviewer đổi ý được và để giữ nguyên bản ghi thô.

Endpoint dự kiến:
    GET    /demos                    (task, status, operator, page) -> list[DemoResponse]
    GET    /demos/{id}               (id: str)                      -> DemoDetailResponse
    GET    /demos/{id}/playback      (id: str)                      -> StreamingResponse
    PATCH  /demos/{id}/trim          (start_step: int, end_step: int) -> DemoResponse
    PATCH  /demos/{id}/label         (outcome: DemoOutcome, note: str) -> DemoResponse
    POST   /demos/{id}/review        (decision: ReviewDecision, note: str) -> DemoResponse
    DELETE /demos/{id}               (id: str)                      -> None

`/review` yêu cầu vai trò reviewer; các endpoint sửa nhãn giới hạn ở demo do
chính operator thu, trừ khi là reviewer.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/demos", tags=["demos"])
