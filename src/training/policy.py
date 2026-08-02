"""Mạng policy behavior cloning.

Trách nhiệm: ánh xạ observation (ảnh camera + state khớp) sang action, học
theo lối giám sát từ demonstration của người điều khiển.

Chữ ký giữ nguyên hình dạng của `RobotEnv.step` để policy thay được chỗ của
người điều khiển lúc đánh giá mà không cần lớp chuyển đổi.
"""


class BCPolicy:
    """Policy behavior cloning (kế thừa nn.Module khi implement).

    Chữ ký dự kiến:
        def __init__(self, action_dim: int, state_dim: int, image_size: int = 96) -> None
        def forward(self, image: Tensor, state: Tensor) -> Tensor
        def predict(self, obs: Observation) -> list[float]
        def save(self, path: str) -> None
        @classmethod
        def load(cls, path: str) -> "BCPolicy"
    """

    def __init__(self, action_dim: int, state_dim: int, image_size: int = 96) -> None:
        raise NotImplementedError

    def forward(self, image: object, state: object) -> object:
        """Lượt truyền xuôi cho một batch."""
        raise NotImplementedError

    def predict(self, obs: object) -> list[float]:
        """Sinh một action từ một quan sát — dùng khi chạy trong sim."""
        raise NotImplementedError

    def save(self, path: str) -> None:
        """Lưu trọng số kèm thống kê chuẩn hoá và cấu hình."""
        raise NotImplementedError

    @classmethod
    def load(cls, path: str) -> "BCPolicy":
        """Nạp policy từ checkpoint."""
        raise NotImplementedError
