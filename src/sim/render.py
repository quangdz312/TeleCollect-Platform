"""Render offscreen từ MuJoCo thành frame gửi cho frontend.

Trách nhiệm: dựng ảnh camera trong sim và nén thành định dạng đủ nhẹ để đẩy
realtime qua WebSocket. Đây là mắt xích ảnh hưởng lớn nhất tới độ trễ teleop
và chi phí lưu trữ, nên chất lượng/độ phân giải để cấu hình được, và stream
xem trực tiếp tách khỏi stream ghi xuống dataset (xem trực tiếp ưu tiên độ
trễ thấp, bản ghi ưu tiên chất lượng).
"""


class FrameRenderer:
    """Render frame từ một hoặc nhiều camera của scene.

    Chữ ký dự kiến:
        def __init__(self, width: int, height: int, cameras: list[str]) -> None
        def render(self) -> dict[str, bytes]
        def encode_jpeg(self, frame: bytes, quality: int = 80) -> bytes
        def close(self) -> None
    """

    def __init__(self, width: int, height: int, cameras: list[str]) -> None:
        raise NotImplementedError

    def render(self) -> dict[str, bytes]:
        """Render tất cả camera, trả về map tên camera -> frame RGB thô."""
        raise NotImplementedError

    def encode_jpeg(self, frame: bytes, quality: int = 80) -> bytes:
        """Nén một frame để đẩy qua WebSocket."""
        raise NotImplementedError

    def close(self) -> None:
        """Giải phóng context render."""
        raise NotImplementedError
