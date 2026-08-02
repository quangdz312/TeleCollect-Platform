"""Lõi nghiệp vụ teleop — nơi phiên điều khiển và bản ghi được sinh ra.

Package này đứng giữa lớp API (src/api) và lớp mô phỏng (src/sim): API chỉ
mở/đóng phiên và bơm input vào, còn toàn bộ việc chạy vòng điều khiển đúng
nhịp và ghi lại dữ liệu nằm ở đây. Không import FastAPI trong package này —
giữ lõi độc lập với transport để còn chạy được từ script thu dữ liệu hàng loạt.

Module:
    session     — vòng đời phiên teleop, giới hạn số phiên đồng thời
    control_loop — vòng điều khiển realtime theo `control_hz`
    recorder    — ghi đồng bộ observation + action thành episode
    anonymize   — ẩn danh khuôn mặt trước khi lưu trữ lâu dài
"""
