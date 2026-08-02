# Problem Formulation — TeleCollect

> **Team:** NEURA
> **Mã nhiệm vụ:** RAV-12
> **Chương trình:** VinUni AI20K Build Phase

---

## 1. Tên nhiệm vụ

**TeleCollect** — Nền tảng teleoperation & thu thập demonstration cho imitation learning.

## 2. Chi tiết nội dung

### 📍 Thực trạng

Imitation learning cho robot cần dữ liệu demonstration do người điều khiển tạo ra, nhưng
thiếu công cụ teleoperation tiện dụng để thu, ghi lại đồng bộ (quan sát + hành động) và
quản lý các bản ghi chất lượng cao.

### 🎯 Vấn đề

Xây dựng nền tảng cho phép người điều khiển teleoperate một robot mô phỏng qua giao diện
(bàn phím / gamepad / chuột), ghi lại đồng bộ luồng quan sát và hành động thành dataset
demonstration, kèm công cụ xem lại, cắt và gắn nhãn thành công / thất bại cho từng bản ghi.

### 🔒 Ràng buộc

| # | Ràng buộc | Ý nghĩa với thiết kế |
|---|-----------|----------------------|
| 1 | Kỹ sư review và duyệt demonstration trước khi đưa vào tập huấn luyện (human-in-the-loop) | Cần trạng thái `pending → approved / rejected` cho mỗi bản ghi, tách biệt vai trò operator và reviewer |
| 2 | Teleoperation chỉ trên robot mô phỏng, không điều khiển robot thật khi chưa validate | Backend chỉ expose adapter tới simulator; không có đường dẫn tới phần cứng thật |
| 3 | Chất lượng đo được: số demo hợp lệ, tỷ lệ thành công, success rate của policy imitation | Cần pipeline evaluation trong sim và dashboard thống kê dataset |
| 4 | Tối ưu độ trễ điều khiển realtime và chi phí lưu trữ | WebSocket thay vì polling REST; nén video quan sát, tách metadata khỏi payload nặng |
| 5 | Nếu có hình ảnh người thì ẩn danh khuôn mặt | Bước face-blur trong pipeline ingest trước khi lưu trữ lâu dài |

## 3. Công nghệ / Kỹ năng yêu cầu

| Nhóm | Công nghệ |
|------|-----------|
| Ngôn ngữ | Python |
| Mô phỏng | MuJoCo (hoặc Isaac Sim / Gazebo) |
| Middleware robot | ROS2 để truyền lệnh / quan sát |
| Huấn luyện | PyTorch cho imitation learning (behavior cloning) |
| Định dạng dữ liệu | LeRobot / RLDS |
| Backend | FastAPI + WebSocket |
| Frontend | React / Next.js cho giao diện teleop và xem lại |
| Quản lý dữ liệu | DVC |
| Hạ tầng | Docker + GPU |

## 4. Tiêu chí đánh giá

### Cơ bản

- [ ] Nền tảng teleop một robot mô phỏng qua trình duyệt
- [ ] Ghi lại đồng bộ observation–action
- [ ] Xem lại và gắn nhãn bản ghi (thành công / thất bại, cắt đoạn)
- [ ] ≥ 2 vai trò người dùng
- [ ] HITL: duyệt demo trước khi vào tập huấn luyện

### Nâng cao

- [ ] Thu dataset nhiều task
- [ ] Huấn luyện policy behavior cloning từ dữ liệu thu được
- [ ] Đánh giá success rate của policy trong sim
- [ ] Tối ưu độ trễ teleop và pipeline xử lý dữ liệu

---

## Phạm vi (Scope)

**Trong phạm vi:**
- Teleoperation robot mô phỏng qua trình duyệt, điều khiển bằng bàn phím / gamepad / chuột
- Ghi và lưu trữ episode dạng observation–action đồng bộ theo timestamp
- Giao diện xem lại: phát lại episode, cắt (trim) đoạn thừa, gắn nhãn kết quả
- Vai trò **Operator** (thu demo) và **Reviewer** (duyệt / từ chối demo)
- Export dataset theo định dạng LeRobot / RLDS
- Huấn luyện behavior cloning và đánh giá success rate trong sim

**Ngoài phạm vi:**
- Điều khiển robot vật lý thật
- Reinforcement learning online trên phần cứng
- Multi-robot / multi-operator đồng thời trên cùng một scene

## Giả định

- Robot mô phỏng chạy trên cùng hạ tầng với backend (độ trễ mạng nội bộ không đáng kể)
- Người điều khiển dùng trình duyệt hiện đại hỗ trợ WebSocket và Gamepad API
- Dữ liệu demonstration chủ yếu là camera quan sát trong sim, không phải video người thật;
  bước ẩn danh khuôn mặt chỉ áp dụng khi có webcam người điều khiển

## Chỉ số thành công

| Chỉ số | Mục tiêu |
|--------|----------|
| Độ trễ điều khiển end-to-end (p95) | < 100 ms |
| Số demo hợp lệ thu được (đã duyệt) | ≥ 100 episode / task |
| Tỷ lệ demo được duyệt trên tổng số thu | ≥ 70% |
| Success rate của policy BC trong sim | ≥ 60% trên task cơ bản |
| Chi phí lưu trữ trung bình / episode | < 20 MB |
