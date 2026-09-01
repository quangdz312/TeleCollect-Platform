# TeleCollect — Team NEURA

Nền tảng teleoperation & thu thập demonstration cho imitation learning (đề bài RAV-12).

Thu dữ liệu điều khiển tay hoặc scripted trên robosuite/MuJoCo → chấm điểm và
gắn nhãn tự động → đóng gói dataset → huấn luyện BC/BC-RNN → đánh giá bằng
rollout trong sim.

## Dùng thử

| | |
|---|---|
| **Web** | [telecollect.io.vn](https://telecollect.io.vn) — duyệt dữ liệu, đóng gói dataset, huấn luyện, đánh giá |
| **App desktop** | [Tải bản cài Windows](https://drive.google.com/drive/folders/1RR_behNMTDSdjDQ_tcsquJ3DFx4yJ_Y6?usp=drive_link) — thu dữ liệu ngay trên máy |
| **Video demo** | [Xem trên YouTube](https://www.youtube.com/watch?v=eWnuH2-vsIE) — toàn bộ luồng từ thu dữ liệu tới đánh giá |

App chạy độc lập, không cần cài Python hay Node. Thu xong đăng nhập vào tài
khoản máy chủ rồi bấm **Push** ở trang Review để đẩy đợt thu lên web. Huấn
luyện và đánh giá chỉ có trên web vì cần GPU và kho dữ liệu chung.

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Kiến trúc, phạm vi, cài đặt và chạy |
| [`docs/DATA_QUALITY.md`](docs/DATA_QUALITY.md) | Chấm điểm, gắn nhãn tự động, các mức nhiễu |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Triển khai lên VPS, sao lưu, khôi phục |
| [`docs/runpod-integration.md`](docs/runpod-integration.md) | Huấn luyện trên GPU thuê |
| [`docs/toolhang_integration.md`](docs/toolhang_integration.md) | Task ToolHang hai giai đoạn |
| [`docs/weekly-log.md`](docs/weekly-log.md) | Nhật ký tuần và phân công |
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | Bàn giao — lỗi đã biết, quyết định đã chốt |

**Bài nộp theo giai đoạn:**

| Giai đoạn | Thư mục | Nội dung |
|---|---|---|
| Gate 1 | [`docs/gate1/`](docs/gate1/) | Project Brief, PRD, Wireframe & UI Flow, AI Log Setup |
| Gate 2 | [`docs/gate2/`](docs/gate2/) | Video demo MVP, sơ đồ kiến trúc, eval evidences |
| Phase 1 | [`docs/phase1/`](docs/phase1/) | Mô tả chi tiết dự án, nội dung slide trình bày |
