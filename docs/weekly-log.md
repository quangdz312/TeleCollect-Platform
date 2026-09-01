# Nhật ký tuần — TeleCollect

Team NEURA · 25/07 – 01/09/2026 · 177 commit trên nhánh `main`

## Thành viên

### Phân công

| Tên | GitHub | Vai trò chính |
|---|---|---|
| Bùi Xuân Tùng | @PakerPP | Backend, frontend, mô phỏng, task ToolHang, triển khai server |
| Trần Gia Thế | @TranGiaThe2004 | Backend, frontend, triển khai server |
| Đặng Minh Quang | @quangdz312 | Mô phỏng, frontend, điều khiển cử chỉ tay, scripted 3 task, huấn luyện policy, đánh giá |
| Nguyễn Nhật Minh | @Helooeverybody | Khảo sát công nghệ, khởi tạo dự án, kiểm thử web |

### Số commit theo tuần

| Tuần | Ngày | Tùng | Thế | Quang | Minh | Tổng |
|---|---|---:|---:|---:|---:|---:|
| W30 | 25/07 | – | – | – | 1 | 1 |
| W31 | 02/08 | – | – | – | 14 | 14 |
| W32 | 05–09/08 | 1 | 6 | – | 1 | 8 |
| W33 | 10–16/08 | 11 | 5 | 7 | – | 23 |
| W34 | 17–23/08 | 39 | 10 | 11 | 2 | 62 |
| W35 | 24–30/08 | 48 | 2 | 4 | – | 54 |
| W36 | 01/09 | 15 | – | – | – | 15 |
| **Tổng** | | **114** | **23** | **22** | **18** | **177** |

---

## W30–W31 · 25/07 – 02/08 — Khởi tạo và định hình đề bài

Dựng repo, chuyển khung template sang TeleCollect, khởi tạo cơ sở dữ liệu bất
đồng bộ và DVC. **Nộp Gate 1**: project brief, PRD, UI flow.

Song song, Nguyễn Nhật Minh khảo sát công nghệ — chọn robosuite/MuJoCo cho mô
phỏng và RoboMimic HDF5 làm định dạng dữ liệu. Hai lựa chọn này định hình toàn
bộ phần còn lại của dự án.

## W32 · 05–09/08 — Backend Core

Hoàn thành nền: cơ sở dữ liệu, xác thực và phân quyền, quản lý task, demo (tải
lên, xem lại, duyệt), đóng gói dataset. **185 test.** Frontend có bản demo đầu.

## W33 · 10–16/08 — Mô phỏng, teleoperation và gắn nhãn

Tuần mở rộng phạm vi lớn nhất. Tùng và Quang đưa toàn bộ phần mô phỏng vào sản
phẩm; frontend nối với backend thật.

- **Điều khiển bằng cử chỉ tay qua webcam** (Quang) — nền tảng cho luận điểm
  không cần thiết bị chuyên dụng
- **Bốn task scripted**: Quang làm `lift_cube`, `pick_place_can`,
  `nut_assembly_square`; Tùng làm ToolHang — task hai giai đoạn, khó nhất
- **Auto-gate** với ba phán quyết `approve` / `reject` / `review` — `review`
  nghĩa là máy không kiểm chứng được, để người xem
- Đóng gói dataset RoboMimic và huấn luyện BC đầu tiên

**Nộp Gate 2**: video demo MVP, sơ đồ kiến trúc.

## W34 · 17–23/08 — Chuẩn hoá chất lượng và lên server

62 commit, tuần nhiều nhất.

Chuẩn hoá gắn nhãn tự động trên nguyên tắc **kiểm chứng, không phải ý kiến**:
thêm kiểm tra nhất quán vật lý, mặt nạ chất lượng, đánh giá tất định. Huấn luyện
thành cấu hình được và có rollout đánh giá.

Triển khai lên VPS: Docker Compose, Caddy, HTTPS, sao lưu và khôi phục — Tùng và
Thế. Frontend chuyển sang bản thiết kế mới, hàng đợi duyệt mở đúng thứ người
duyệt xử lý được — Tùng, Thế và Quang cùng làm.

## W35 · 24–30/08 — Từ công cụ thành nền tảng nhiều người dùng

*Tùng và Thế triển khai server; Quang huấn luyện policy và đánh giá; Minh kiểm
thử web sau mỗi lần triển khai.*

- **GPU thuê theo giây** qua RunPod, tính giờ theo từng tài khoản
- **App desktop** tách khỏi bản web, đăng nhập và đẩy dữ liệu lên máy chủ
- **Đánh giá tách khỏi huấn luyện** — server không có GPU vẫn chạy rollout được
- **Tài khoản** đăng ký chờ admin duyệt
- Xuất LeRobot v3, chống nghẽn API, chặn ingest quá lớn trước khi đầy đĩa

## W36 · 01/09 — Đồng bộ app–server và dọn dẹp

*Tiếp nối tuần trước: Tùng và Thế lo đồng bộ app–server, Quang tiếp tục huấn
luyện và đánh giá, Minh kiểm thử web.*

Sửa để app và máy chủ khớp nhau — nút Push trước đó chưa từng gửi được tập nào
thu bằng app vì đóng gói nhầm thư mục. Kèm theo: đợt thu hiện đúng tên, tập nhập
vào được auto-gate quyết ngay thay vì nằm chờ, biểu mẫu huấn luyện nhận input
trở lại.

Thêm script nạp training run từ máy khác. Dọn 9 tài liệu kế hoạch cũ ở gốc repo.
Gói cài giảm từ 1.77 GB xuống 488 MB.

---

## Ghi chú

Số commit đo mức độ hoạt động, không đo giá trị đóng góp — một commit có thể là
sửa một dòng hoặc đưa cả module mô phỏng vào. Bảng nên đọc cùng phần mô tả công
việc. Riêng phần khảo sát công nghệ giai đoạn đầu gần như không để lại commit.
