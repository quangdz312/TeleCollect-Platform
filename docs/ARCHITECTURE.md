# TeleCollect — Kiến trúc và trạng thái hệ thống

Mô tả những gì đã chạy được tại `main @ 5fe4ace` (01/09/2026). Không phải kế
hoạch — mọi thứ trong tài liệu này đều kiểm chứng được trong mã nguồn.

---

## 1. Hệ thống làm gì

Nền tảng thu thập dữ liệu và huấn luyện policy cho cánh tay robot bằng imitation
learning, bao trùm bốn bước:

```
Thu thập  →  Kiểm định  →  Huấn luyện  →  Đánh giá
```

Người dùng điều khiển robot trong mô phỏng để tạo demonstration; hệ thống kiểm
định chất lượng, xuất dataset, huấn luyện behavior cloning, rồi chạy thử policy
trong mô phỏng để đo tỉ lệ thành công.

Điểm khác biệt so với công cụ huấn luyện thông thường: **chọn checkpoint theo tỉ
lệ robot hoàn thành nhiệm vụ trong mô phỏng**, không theo validation loss. Loss
thấp không đảm bảo robot gắp được vật.

## 2. Quy mô

| Hạng mục | Con số |
|---|---|
| Backend | ~21 700 dòng Python |
| Frontend | ~12 700 dòng TypeScript |
| Kiểm thử | 56 tệp, 591 test |
| Điểm cuối API | 88 |
| Trang giao diện | 12 |

**Dữ liệu tích luỹ** (kho phát triển, 14/8 – 28/8):

| Hạng mục | Con số |
|---|---|
| Episode đã chấm điểm | 750 |
| Episode đã gắn nhãn | 694 |
| Auto-gate quyết | 309 (205 duyệt, 104 loại) |
| Người quyết | 385 (358 duyệt, 27 loại) |

## 3. Kiến trúc

Hai môi trường chạy, dùng chung một bản mã frontend phân biệt bằng cờ lúc build.

```
┌── App desktop (Electron) ──┐              ┌── Server: telecollect.io.vn ──┐
│  Thu dữ liệu               │    Push      │  Duyệt · Dataset              │
│  MuJoCo / robosuite        │  ─────────►  │  Huấn luyện · Đánh giá        │
│  SQLite, chạy offline      │              │  Docker · Caddy · HTTPS       │
└────────────────────────────┘              └───────────────────────────────┘
```

Bên trong mỗi phía:

```
┌─────────────────────────────────────────────────┐
│  Frontend (Next.js 16 · React 19)               │
│  12 trang · WebSocket cho teleop thời gian thực │
└────────────────┬────────────────────────────────┘
                 │ REST + WS
┌────────────────▼────────────────────────────────┐
│  Backend (FastAPI · Python 3.12)                │
│  api/       88 endpoint                         │
│  core/      vòng điều khiển, ghi episode        │
│  sim/       môi trường MuJoCo, scripted policy  │
│  labeling/  chấm điểm, auto-gate, tinh chỉnh    │
│  training/  quản lý job huấn luyện, đánh giá    │
│  export/    xuất HDF5, LeRobot                  │
│  services/  xác thực, quy tắc nghiệp vụ         │
└────────────────┬────────────────────────────────┘
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

## 4. Bốn nhiệm vụ mô phỏng

| Tên simulator | Tên scripted | Mô tả | Bước tối đa |
|---|---|---|---|
| `lift_cube` | `lift` | Gắp khối lập phương và nhấc lên | 500 |
| `pick_place_can` | `can` | Gắp lon và đặt vào vị trí đích | 400 |
| `nut_assembly_square` | `square` | Gắp đai ốc vuông, lắp vào chốt | 500 |
| `tool_hang` | `tool_hang` | Hai giai đoạn: cắm khung, treo cờ-lê | 1500 |

Hai hệ dùng hai bộ tên khác nhau — đường teleop dùng tên dài, đường scripted
dùng tên ngắn. Dữ liệu đã lưu theo tên ngắn nên chưa thống nhất được; giao diện
review có bộ lọc nguồn để phân biệt.

ToolHang khó nhất: thành công đòi hỏi khe hở 1.25 mm, dưới một điểm ảnh nếu nhìn
từ camera tĩnh cách nửa mét. Đó là lý do có camera cổ tay.

## 5. Các thành phần

### 5.1 Thu thập dữ liệu

**Teleoperation qua trình duyệt** — ba cách điều khiển:

- Bàn phím và chuột
- Cử chỉ bàn tay qua webcam (MediaPipe): di chuyển tay điều khiển XYZ, xoay cổ
  tay xoay gripper, xoè/nắm mở/đóng kẹp, giữ 👍 để tạm ngắt
- Ba khung camera đồng thời: góc nhìn chính, từ trên xuống, cổ tay

Vòng điều khiển 60 Hz. Ảnh xem trước mã hoá JPEG bất đồng bộ, gửi qua WebSocket
theo giao thức nhị phân — một byte tiền tố cho biết camera, bốn byte số thứ tự,
rồi dữ liệu ảnh.

**Sinh dữ liệu tự động** — scripted policy chạy theo bốn mức nhiễu `clean`,
`good`, `medium`, `poor`. Một số episode không phải `clean` mang thêm đúng một
lỗi ngữ nghĩa có kiểm soát, để dữ liệu huấn luyện có cả trường hợp thất bại.

### 5.2 Kiểm định chất lượng

Công thức chấm điểm:

```
score = (tích các hard check) × (1 − penalty tệ nhất)
```

**Nhân** chứ không cộng — một lỗi nghiêm trọng cho ra 0, không chỉ số đẹp nào kéo
lại được. **Lấy max** penalty — mười lỗi vặt không cộng dồn thành một lỗi giả.

Penalty **không gate** bất cứ thứ gì: ngưỡng chưa hiệu chỉnh trên dữ liệu thật,
nên chúng chỉ được ghi lại để báo cáo.

**Auto-gate** có ba phán quyết, mô tả *mức độ kiểm chứng được* chứ không phải mức
độ đẹp:

| | Nghĩa |
|---|---|
| `reject` | Đã kiểm chứng và hỏng |
| `approve` | Mọi phép kiểm chạy được đều đạt |
| `review` | Máy không có câu trả lời → cần người |

Quyết định của người **không bao giờ bị ghi đè**. Một phần episode đủ điều kiện
tự duyệt vẫn được giữ cho người xem (mẫu audit) — để phát hiện khi chính cơ chế
tự động sai. Hai episode cùng seed môi trường thì cái sau bị loại.

`tool_hang` bị soi chặt hơn: cần cả hai chứng cứ giai đoạn, `terminal_phase` phải
là `done`, và tỉ lệ audit 20% thay vì 10%.

### 5.3 Review và gắn nhãn

Hàng đợi gộp cả hai nguồn, lọc theo nguồn / nhiệm vụ / trạng thái / nhãn / chất
lượng / đợt thu.

**Cắt episode** chỉ ghi hai mốc thời gian vào cơ sở dữ liệu, **không đụng video
gốc** — bước xuất HDF5 mới đọc hai mốc đó. Nhờ vậy cắt sai vẫn sửa lại được.

Video review ghép ba khung: chính 640×640 bên trái, hai phụ 320×320 xếp dọc bên
phải, tổng 960×640.

### 5.4 Xuất dataset

HDF5 chuẩn RoboMimic. Một tệp mang nhiều "cách nhìn" chồng lên nhau thay vì cắt bớt:

| Nhãn | Nội dung |
|---|---|
| `mask/all` | Mọi episode |
| `mask/verified` | Episode qua kiểm định |
| `mask/clean` | Episode đạt ngưỡng phạt |
| `mask/train`, `mask/valid` | Chia tập huấn luyện và kiểm định |

Giữ hết thay vì lọc, vì một quỹ đạo thô làm hại huấn luyện đơn nhiệm lại có thể
đúng là thứ một lần chạy kiểm tra độ bền cần. Cắt bớt lúc xuất là quyết định thay
cho người không có mặt.

Cũng xuất được LeRobot v3. Dataset phiên bản hoá bằng DVC.

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

Hàng đợi chạy một job mỗi lúc để tránh tranh GPU. Nhật ký hiển thị trực tiếp,
danh sách checkpoint đánh dấu bản tốt nhất, các lần chạy ghim hoặc lưu trữ được.

Chạy được trên GPU thuê qua RunPod Serverless — xem [runpod-integration.md](runpod-integration.md).
Mỗi người nối tài khoản Weights & Biases riêng.

### 5.6 Đánh giá

Chạy policy trong mô phỏng trên nhiều seed, quay video từng lần. Kết quả gồm tỉ
lệ thành công, độ dài trung bình, trạng thái từng seed.

Thành công phải **giữ được N bước liên tiếp** (`success_hold_steps`, mặc định 10)
— một bước sai làm chuỗi đếm lại từ đầu. Không có ràng buộc này thì một lần chạm
ngưỡng thoáng qua cũng tính là thành công.

Đánh giá tách khỏi huấn luyện bằng cờ `EVALUATION_ENABLED` riêng: rollout chạy
được trên CPU, nên server không có GPU vẫn đánh giá được.

### 5.7 Tài khoản

| Vai trò | Quyền |
|---|---|
| `operator` | Thu dữ liệu, xem bản ghi của mình |
| `reviewer` | Thêm quyền duyệt |
| `admin` | Thêm quyền quản lý người dùng |

JWT có refresh token. Đăng ký xong chờ admin duyệt. **Chưa có khái niệm nhóm** —
mọi người thấy chung một kho dữ liệu.

## 6. Hiệu năng

Chạy được trên máy không GPU nhờ render phần mềm OSMesa, nhưng phải hạ tham số.
Số đo trong container:

| Kích thước ảnh | Thời gian render | Tải cho 3 camera @10fps |
|---|---|---|
| 640px | ~45 ms | 225% một nhân |
| 320px | ~11 ms | 56% |
| 256px | 7.3 ms | 36% |
| 192px | ~4 ms | 20% |

Vật lý gần như không tốn gì (dưới 0.01 ms mỗi bước) — toàn bộ chi phí ở render.
Trên máy có GPU, render 256px chỉ mất 0.67 ms, nhanh hơn 11 lần.

Độ trễ điều khiển teleop: p50 17.5 ms, p95 24.6 ms (mục tiêu < 100 ms).

## 7. Những chỗ đã biết là chưa ổn

**Chức năng còn thiếu**

- Không chọn được tập con dữ liệu để huấn luyện — nhãn chất lượng đã ghi vào
  HDF5 nhưng giao diện chưa cho chọn
- Upload chỉ nhận video; ai đã có sẵn dữ liệu RoboMimic thì không đưa vào được
- Không có khái niệm nhóm
- Không quét được siêu tham số, không so sánh nhiều lần chạy trên một biểu đồ

**Vấn đề dữ liệu**

- `tool_hang` mới 25 episode — chưa đủ để huấn luyện
- Penalty chưa hiệu chỉnh ngưỡng nên chưa dùng để gate
- Hai hệ đặt tên nhiệm vụ khác nhau, dữ liệu cũ giữ tên ngắn

**Hạn chế môi trường**

- Cần `ffmpeg` cho tải lên, ảnh thu nhỏ và phát lại video
- Máy Windows hai GPU cần đặt biến môi trường chọn card rời trước khi tạo ngữ
  cảnh đồ hoạ đầu tiên — `src/sim/gpu.py` xử lý lúc import

## 8. Cài đặt và chạy

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

Huấn luyện cần thêm `requirements-train.txt`; trên Windows không cài trọn được
bằng một lệnh — tệp đó ghi rõ cách làm từng bước.

Biến môi trường xem `.env.example`. Ba dòng cấu hình camera phải có đủ: thiếu
dòng thứ ba khiến hai khung phụ hiển thị cùng một ảnh.

Triển khai lên server: xem [DEPLOYMENT.md](DEPLOYMENT.md).
