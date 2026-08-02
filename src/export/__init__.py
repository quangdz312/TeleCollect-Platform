"""Xuất demo đã duyệt sang định dạng dataset chuẩn.

Tên package là `export` (không phải `data`) để không lẫn với thư mục `data/`
ở gốc repo — nơi chứa dữ liệu runtime: CSDL SQLite, artifact episode, cache.

Ranh giới: package này đọc episode thô từ storage và viết ra dataset bất
biến; nó không sửa episode gốc và không biết gì về HTTP.

Module:
    builder  — chọn demo đã duyệt, đóng băng thành phiên bản dataset, tag DVC
    lerobot  — writer định dạng LeRobot
    rlds     — writer định dạng RLDS
"""
