"""Ẩn danh khuôn mặt trong frame trước khi lưu trữ lâu dài.

Trách nhiệm: đáp ứng ràng buộc "nếu có hình ảnh người thì ẩn danh khuôn mặt".
Frame từ camera trong sim không có người, nhưng bản ghi có thể kèm webcam của
operator; đường ống ghi luôn đi qua đây khi `enable_face_anonymization` bật.

Xử lý ở bước finalize chứ không phải trong vòng điều khiển: phát hiện khuôn
mặt quá nặng để chạy trong ngân sách một chu kỳ 33 ms.
"""


def detect_faces(frame: bytes) -> list[tuple[int, int, int, int]]:
    """Tìm khuôn mặt trong một frame, trả về danh sách bbox (x, y, w, h)."""
    raise NotImplementedError


def blur_regions(frame: bytes, boxes: list[tuple[int, int, int, int]]) -> bytes:
    """Làm mờ các vùng đã cho, trả về frame mới."""
    raise NotImplementedError


def anonymize_video(path: str) -> int:
    """Ẩn danh toàn bộ video tại chỗ, trả về số frame đã sửa."""
    raise NotImplementedError
