"""Huấn luyện policy imitation learning và đánh giá trong sim.

Vòng khép kín của dự án: dữ liệu người điều khiển thu được → behavior cloning
→ success rate đo trong cùng bộ sim đã dùng để thu. Vì cả hai đầu dùng chung
`src.sim.tasks`, số đo đánh giá so sánh trực tiếp được với tỷ lệ thành công
của chính người điều khiển.

Package này chạy được độc lập với backend (script/job nền), nên không import
gì từ src/api.

Module:
    dataset  — torch Dataset đọc dataset đã export
    policy   — mạng policy behavior cloning
    train    — vòng huấn luyện và checkpoint
    evaluate — chạy policy trong sim, tính success rate
"""
