"""Model CSDL (SQLAlchemy) và bố cục bảng.

Trách nhiệm: lưu metadata — artifact nặng (video, parquet) nằm trên đĩa dưới
`settings.storage_dir`, CSDL chỉ giữ con trỏ tới chúng. Nhồi frame vào CSDL
sẽ phá cả chi phí lưu trữ lẫn tốc độ truy vấn.

Bảng dự kiến:
    users      — tài khoản, vai trò, mật khẩu đã băm
    episodes   — một demonstration: task, operator, trạng thái, nhãn, khoảng cắt
    datasets   — tập demo đã duyệt được đóng băng, kèm tag DVC
    dataset_episodes — bảng nối datasets ↔ episodes (nhiều-nhiều)
    training_jobs    — job huấn luyện và kết quả đánh giá

Quan hệ quan trọng: `dataset_episodes` phải giữ được lịch sử kể cả khi
episode bị xoá về sau, nếu không sẽ mất khả năng truy ngược model ↔ dữ liệu.

Chữ ký dự kiến:
    class Base(DeclarativeBase): ...
    def get_engine() -> Engine
    def get_session() -> Iterator[Session]
    def init_db() -> None
"""
