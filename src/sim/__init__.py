"""Lớp mô phỏng — mọi thứ chạm tới MuJoCo nằm trong package này.

Ràng buộc dự án: teleoperation chỉ trên robot mô phỏng. Package này là biên
giới duy nhất giữa hệ thống và bộ mô phỏng; không module nào ngoài đây được
gọi thẳng vào MuJoCo. Khi nào validate xong và muốn chuyển sang robot thật,
chỉ cần thêm một implementation khác cùng giao diện `RobotEnv`.

Module:
    environment  — wrapper môi trường MuJoCo (reset / step / observe)
    kinematics   — động học thuận & nghịch, ánh xạ lệnh người dùng sang khớp
    tasks        — định nghĩa task và điều kiện thành công
    render       — render offscreen thành frame ảnh cho frontend
"""
