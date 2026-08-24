"""Lớp HTTP/WebSocket — mỏng, chỉ validate và điều phối.

Route tách theo tài nguyên, mỗi module một `APIRouter` và được `routes.py`
gom lại. Logic nghiệp vụ nằm ở src/core và src/sim; module ở đây không tự
chạy vòng điều khiển hay đọc/ghi file dataset.

Module:
    auth      — đăng nhập, JWT, phân quyền operator / reviewer
    tasks     — liệt kê task khả dụng
    teleop    — WebSocket điều khiển realtime + đóng/mở phiên
    demos     — xem lại, cắt, gắn nhãn, duyệt demo (human-in-the-loop)
    datasets  — gom demo đã duyệt thành dataset và export
    raw       — danh sách/chi tiết hợp nhất episode teleop và scripted
    training  — chạy huấn luyện behavior cloning và xem kết quả đánh giá
"""
