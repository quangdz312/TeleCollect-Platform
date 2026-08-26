# TeleCollect — Tổng quan hệ thống hiện tại

Ảnh chụp trạng thái hệ thống tại thời điểm `main @ e9b4c22` (24/08/2026). Mô tả
những gì đã chạy được, không phải kế hoạch — kế hoạch nằm ở
[platform-spec.md](platform-spec.md).

---

## 1. Hệ thống làm gì

Nền tảng thu thập dữ liệu và huấn luyện policy cho cánh tay robot bằng phương
pháp imitation learning. Bao trùm bốn bước:

```
Thu thập  →  Kiểm định  →  Huấn luyện  →  Đánh giá
```

Người dùng điều khiển robot trong mô phỏng để tạo demonstration; hệ thống kiểm
định chất lượng, xuất dataset, huấn luyện behavior cloning, rồi chạy thử policy
trong mô phỏng để đo tỉ lệ thành công.

Điểm khác biệt so với công cụ huấn luyện thông thường: **chọn checkpoint dựa
trên tỉ lệ robot hoàn thành nhiệm vụ trong mô phỏng**, không phải validation loss.

---

## 2. Quy mô

| Hạng mục | Con số |
|---|---|
| Mã nguồn backend | ~22 500 dòng Python |
| Mã nguồn frontend | ~8 400 dòng TypeScript |
| Tệp kiểm thử | 36 tệp, 343 test |
| Điểm cuối API | 63 |
| Trang giao diện | 11 |
| Script dòng lệnh | 20 |

**Dữ liệu đã tích lũy:**

| Hạng mục | Con số |
|---|---|
| Episode scripted đã chấm điểm | 925 |
| Episode đã gắn nhãn | 869 |
| Dataset đã xuất | 8 |
| Episode teleop trong cơ sở dữ liệu | 2 |

Phân bố nhãn:

| Nguồn quyết định | Duyệt | Loại |
|---|---|---|
| Auto-gate | 243 | 112 |
| Người | 355 | 24 |
| Không rõ nguồn (dữ liệu cũ) | 113 | 22 |

---

## 3. Kiến trúc

Toàn bộ chạy trên một máy. Backend FastAPI phục vụ REST và WebSocket; frontend
Next.js gọi vào đó.

```
┌─────────────────────────────────────────────────┐
│  Frontend (Next.js)                             │
│  11 trang · WebSocket cho teleop thời gian thực │
└────────────────┬────────────────────────────────┘
                 │ REST + WS
┌────────────────▼────────────────────────────────┐
│  Backend (FastAPI)                              │
│                                                 │
│  api/       7 router, 63 endpoint               │
│  core/      vòng điều khiển, ghi episode        │
│  sim/       môi trường MuJoCo, scripted policy  │
│  labeling/  chấm điểm, auto-gate, tinh chỉnh    │
│  training/  quản lý job huấn luyện, đánh giá    │
│  export/    xuất HDF5                           │
│  services/  xác thực, quy tắc nghiệp vụ         │
│  models/    lược đồ dữ liệu                     │
└────────────────┬────────────────────────────────┘
                 │
     ┌───────────┴───────────┐
     ▼                       ▼
┌─────────┐          ┌───────────────┐
│ SQLite  │          │ Ổ đĩa         │
│ metadata│          │ video, HDF5,  │
│         │          │ checkpoint    │
└─────────┘          └───────────────┘
```

**Nguyên tắc phân chia:** cơ sở dữ liệu chỉ giữ metadata; mọi tệp nặng nằm trên
ổ đĩa và được tham chiếu bằng đường dẫn.

---

## 4. Bốn nhiệm vụ mô phỏng

| Tên trong simulator | Tên scripted | Mô tả | Số bước tối đa |
|---|---|---|---|
| `lift_cube` | `lift` | Gắp khối lập phương và nhấc lên | 500 |
| `pick_place_can` | `can` | Gắp lon và đặt vào vị trí đích | 400 |
| `nut_assembly_square` | `square` | Gắp đai ốc vuông và lắp vào chốt | 500 |
| `tool_hang` | `tool_hang` | Hai giai đoạn: cắm khung móc, rồi treo cờ-lê | 1500 |

**Lưu ý:** hai hệ dùng hai bộ tên khác nhau. Đường teleop dùng tên dài
(`lift_cube`), đường scripted dùng tên ngắn (`lift`). 925 episode đã lưu theo tên
ngắn nên chưa thống nhất được — giao diện review có bộ lọc nguồn để phân biệt.

ToolHang là nhiệm vụ khó nhất: thành công đòi hỏi khe hở 1.25 mm, tức dưới một
điểm ảnh nếu nhìn từ camera tĩnh cách nửa mét. Đó là lý do có camera cổ tay.

---

## 5. Các thành phần đã hoạt động

### 5.1 Thu thập dữ liệu

**Teleoperation qua trình duyệt.** Ba cách điều khiển:

- Bàn phím và chuột
- Cử chỉ bàn tay qua webcam (MediaPipe): di chuyển tay điều khiển XYZ, xoay cổ
  tay xoay gripper, xòe/nắm tay mở/đóng kẹp, giữ 👍 để tạm ngắt
- Ba khung camera đồng thời: góc nhìn chính, nhìn từ trên xuống, camera cổ tay

Vòng điều khiển chạy 60 Hz. Ảnh xem trước mã hóa JPEG bất đồng bộ, gửi qua
WebSocket theo giao thức nhị phân — một byte tiền tố cho biết camera nào, bốn
byte số thứ tự, rồi dữ liệu ảnh.

**Sinh dữ liệu tự động.** Scripted policy chạy theo bốn mức nhiễu: `clean`,
`good`, `medium`, `poor`. Một số episode không phải `clean` mang thêm đúng một
lỗi ngữ nghĩa có kiểm soát — để dữ liệu huấn luyện có cả trường hợp thất bại.

### 5.2 Kiểm định chất lượng

**Auto-gate** xử lý các trường hợp rõ ràng và giữ lại trường hợp không chắc chắn
cho người:

- Simulator báo thất bại → tự loại
- Không qua kiểm tra bắt buộc → tự loại
- Qua hết mọi kiểm tra → tự duyệt
- Có kiểm tra không thực hiện được → chuyển cho người

**Quyết định của người không bao giờ bị ghi đè.**

**Mẫu audit:** một phần episode đủ điều kiện tự duyệt vẫn được giữ lại cho người
xem — để phát hiện khi chính cơ chế tự động sai.

**Kiểm tra trùng lặp:** hai episode cùng seed môi trường thì cái sau bị loại.

### 5.3 Review và gắn nhãn

Hàng đợi review gộp cả hai nguồn dữ liệu, có bộ lọc theo nguồn, nhiệm vụ, trạng
thái, nhãn, chất lượng và lô thu thập.

**Cắt episode:** thanh kéo hai đầu kèm ô nhập theo giây. Cắt chỉ ghi hai mốc thời
gian vào cơ sở dữ liệu, **không đụng tệp video gốc** — bước xuất HDF5 mới đọc hai
mốc đó để lấy khoảng khung hình ở giữa. Nhờ vậy cắt sai vẫn sửa lại được.

Video review ghép ba khung: khung chính 640×640 bên trái, hai khung phụ 320×320
xếp dọc bên phải, tổng 960×640.

### 5.4 Xuất dataset

Xuất sang HDF5 theo chuẩn robomimic. Một tệp mang nhiều "cách nhìn" chồng lên
nhau thay vì cắt bớt:

| Nhãn phân loại | Nội dung |
|---|---|
| `mask/all` | Mọi episode |
| `mask/verified` | Episode qua kiểm định |
| `mask/clean` | Episode đạt ngưỡng phạt |
| `mask/train`, `mask/valid` | Chia tập huấn luyện và kiểm định |

Lý do giữ hết thay vì lọc: một quỹ đạo thô làm hại huấn luyện đơn nhiệm lại có
thể đúng là thứ một lần chạy kiểm tra độ bền cần. Cắt bớt lúc xuất là quyết định
thay cho người không có mặt.

Dataset được phiên bản hóa bằng DVC.

### 5.5 Huấn luyện

Chạy qua giao diện web, 16 siêu tham số điều chỉnh trực tiếp:

| Nhóm | Tham số |
|---|---|
| Thuật toán | `policy` (bc / bc-rnn) |
| Tối ưu | `epochs`, `batch_size`, `learning_rate`, `seed` |
| Kiến trúc RNN | `sequence_length`, `rnn_hidden_dim`, `rnn_layers` |
| Dữ liệu | `normalize_observations`, `observation_profile` |
| Tài nguyên | `device`, `num_workers` |
| Đánh giá | `rollout_enabled`, `rollout_every_n_epochs` |

Hàng đợi chạy một job mỗi lúc để tránh tranh GPU. Nhật ký hiển thị trực tiếp.
Danh sách checkpoint có đánh dấu bản tốt nhất. Các lần chạy ghim hoặc lưu trữ được.

### 5.6 Đánh giá

Sau huấn luyện, chạy policy trong mô phỏng trên nhiều seed khác nhau, quay video
từng lần. Kết quả gồm tỉ lệ thành công, độ dài trung bình, và trạng thái từng seed.

Đây là phần phân biệt hệ thống với công cụ huấn luyện thông thường: **checkpoint
được chọn theo tỉ lệ robot làm được việc, không theo validation loss.** Loss thấp
không đảm bảo robot gắp được vật.

### 5.7 Phân tích đa dạng dữ liệu

Trang riêng hiển thị độ phủ không gian trạng thái, phân bố độ dài episode, và
thống kê theo giai đoạn nhiễu.

### 5.8 Tài khoản

Ba vai trò:

| Vai trò | Quyền |
|---|---|
| `operator` | Thu dữ liệu, xem bản ghi của mình |
| `reviewer` | Thêm quyền duyệt |
| `admin` | Thêm quyền quản lý người dùng |

Xác thực bằng JWT, có refresh token. Chưa có khái niệm nhóm — mọi người thấy
chung một kho dữ liệu.

---

## 6. Giao diện

| Trang | Chức năng |
|---|---|
| `/` | Tổng quan |
| `/collect` | Thu dữ liệu — chuyển giữa thủ công và tự động |
| `/upload` | Tải lên bản ghi từ nơi khác |
| `/review` | Hàng đợi review |
| `/review/[id]` | Chi tiết một episode, cắt và duyệt |
| `/scripted` | Chi tiết episode scripted |
| `/diversity` | Phân tích đa dạng dữ liệu |
| `/datasets` | Quản lý dataset |
| `/training` | Huấn luyện và đánh giá |
| `/teleop` | Điều khiển trực tiếp |
| `/admin` | Quản lý người dùng |

Giao diện dùng bảng màu sáng. Các bề mặt kỹ thuật — khung camera, thanh thời
gian, khối nhật ký — cố ý giữ nền tối bằng bộ token riêng.

---

## 7. Triển khai

Đã có cấu hình Docker cho cả backend và frontend, kèm ba tệp compose (mặc định,
local, production) và script sao lưu, phục hồi, kiểm tra sau triển khai.

**Chạy được trên máy không GPU** nhờ render phần mềm OSMesa, nhưng phải hạ tham
số. Số đo trong container:

| Kích thước ảnh | Thời gian render | Tải cho 3 camera @10fps |
|---|---|---|
| 640px | ~45 ms | 225% một nhân |
| 320px | ~11 ms | 56% |
| 256px | 7.3 ms | 36% |
| 192px | ~4 ms | 20% |

Vật lý gần như không tốn gì (dưới 0.01 ms mỗi bước). Toàn bộ chi phí nằm ở render.

Trên máy có GPU, render 256px chỉ mất 0.67 ms — nhanh hơn 11 lần.

---

## 8. Những chỗ đã biết là chưa ổn

### Chức năng còn thiếu

- **Không chọn được tập con dữ liệu để huấn luyện.** Các nhãn phân loại chất
  lượng đã ghi vào tệp HDF5 nhưng giao diện chưa cho chọn, nên chỉ huấn luyện
  được trên toàn bộ.
- **Upload chỉ nhận video.** Ai đã có sẵn dữ liệu robomimic thì không đưa vào
  được — hệ thống hiện chỉ nhận dữ liệu do chính nó sinh ra.
- **Không có khái niệm nhóm.** Mọi người thấy chung một kho dữ liệu.
- **Không quét được siêu tham số.** Phải bấm từng lần một.
- **Không so sánh được nhiều lần chạy trên một biểu đồ.**

### Lỗi đã biết

- **Nút "Save trim & notes only" có thể không lưu ghi chú.** Trong mã nguồn,
  phần ghi chú chỉ được gửi kèm khi gắn nhãn hoặc duyệt — chưa xác nhận bằng thử
  nghiệm thực tế.
- **Hai hệ đặt tên nhiệm vụ khác nhau** (`lift` với `lift_cube`). 925 episode đã
  lưu theo tên ngắn nên chưa thống nhất được.
- **Một test thất bại trên nhánh chính** (`test_release_check`): episode `lift`
  tham chiếu chỉ nâng 11 mm so với ngưỡng 20 mm. Do dữ liệu cục bộ, không do mã.

### Hạn chế môi trường

- **CI đỏ vì hạn mức thanh toán của tổ chức**, ngoài tầm kiểm soát của nhóm.
  Hiện kiểm tra bằng cách chạy test cục bộ trước mỗi lần đẩy.
- **Cần ffmpeg** cho tải lên, ảnh thu nhỏ và phát lại video. Thiếu nó thì các
  tính năng video hỏng nhưng phần còn lại vẫn chạy.
- **Máy Windows có hai GPU** cần đặt biến môi trường chọn card rời trước khi tạo
  ngữ cảnh đồ họa đầu tiên. `src/sim/gpu.py` xử lý việc này lúc import.

---

## 9. Cài đặt và chạy

```bash
# Backend
pip install -r requirements.txt
python -m scripts.seed_tasks          # nạp 4 nhiệm vụ từ simulator
uvicorn src.main:app --port 8000

# Frontend
cd frontend && npm install && npm run dev

# Kiểm thử
python -m pytest -q
```

**Huấn luyện** cần thêm `requirements-train.txt`. Trên Windows không cài trọn
được bằng một lệnh vì hai lý do độc lập — tệp đó ghi rõ cách làm từng bước.

**Biến môi trường** xem `.env.example`. Ba dòng cấu hình camera phải có đủ:
thiếu dòng thứ ba khiến hai khung phụ hiển thị cùng một ảnh.
