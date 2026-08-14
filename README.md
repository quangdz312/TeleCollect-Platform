# TeleCollect — Team NEURA

Nền tảng teleoperation & thu thập demonstration cho imitation learning (đề bài RAV-12).

---

## Gate 1

Chi tiết đầy đủ nằm trong các file `.md` được dẫn ở mỗi mục.

### 1. Project Brief — [`docs/gate1/brief.md`](docs/gate1/brief.md)

- [X] **Vấn đề**
- [X] **Giải pháp**
- [X] **Đối tượng**

### 2. PRD — [`docs/gate1/prd.md`](docs/gate1/prd.md)

- [X] **Mục tiêu (Goals)**
- [X] **Tính năng chính (Core Features)**
- [X] **User Stories**
- [X] **Yêu cầu chức năng & phi chức năng**
- [X] **Tech Stack**
- [X] **Tiêu chí thành công**

### 3. Wireframe & UI Flow — [`docs/gate1/ui-flow.md`](docs/gate1/ui-flow.md)

- [X] **User flow**

### 4. AI Log Setup

- [X] Đã tạo API Key trên Phoenix
- [X] Đã tích hợp Hook vào Repo
- [X] Đã test log thành công

---

## Backend — Hướng dẫn chạy

### Yêu cầu môi trường

Phiên bản đang chạy thật trong `.venv` của repo này (đo bằng `pip`/`node --version`,
không phải con số mong muốn):

| Thành phần | Phiên bản | Ghi chú |
|---|---|---|
| Python | **3.12.6** | Không dùng 3.13 — một số thư viện chưa có wheel sẵn |
| robosuite | **1.5.2** | Xem cảnh báo port 1.4.1 → 1.5.2 bên dưới |
| MuJoCo | **3.8.1** | |
| NumPy | 1.26.4 | |
| FastAPI | 0.140.0 | |
| Node | **22.18.0** | |
| Next.js | 16.3.x | |
| React | 19.2.x | |
| TypeScript | 5.8.x | |

> **Port robosuite 1.4.1 → 1.5.2 làm gãy hai thứ**, đã vá trong
> `src/sim/skillgen/compat.py`:
> 1. *Tên site:* `gripper0_grip_site` (1.4) đổi thành `gripper0_right_grip_site`
>    (1.5) — xử lý trong `compat.grip_site_id()`.
> 2. *Seeding RNG:* 1.5 chuyển mọi lần bốc reset từ RNG toàn cục của numpy sang
>    `np.random.default_rng(seed)` riêng theo environment, nên `np.random.seed()`
>    mà skill vendor dùng không còn tác dụng gì. `compat.seed_env()` gieo lại
>    generator của chính environment.

- **ffmpeg / ffprobe** cài sẵn và nằm trong PATH — bắt buộc, dùng để đọc metadata
  video, sinh thumbnail và tạo dữ liệu mẫu.
  Kiểm tra: `ffprobe -version`
  Windows: `winget install Gyan.FFmpeg` (mở lại terminal sau khi cài)

### Chọn GPU (Windows / NVIDIA Optimus)

Trên laptop hai card, Windows giao OpenGL cho GPU tích hợp và mọi lần render
offscreen của MuJoCo phải trả giá. Đặt `SHIM_MCCOMPAT` trong `.env` để đẩy tiến
trình sang card rời — xem `src/sim/gpu.py`. **Đây là thứ chỉ có trên
Windows/Optimus**, trên Linux/macOS không cần và không có tác dụng.

Đo trên máy phát triển: thu một batch 36.1 s → **16.9 s**, render 62.4 s → **8.3 s**.

### Cài đặt

```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash
# source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt

cp .env.example .env
# Mở .env, đặt JWT_SECRET bằng chuỗi ngẫu nhiên:
# python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Chạy server

```bash
python -m uvicorn src.main:app --reload --port 8000
```

- API docs: http://localhost:8000/docs
- OpenAPI spec (cho frontend sinh client): http://localhost:8000/openapi.json

### Tạo dữ liệu mẫu

Mở terminal thứ hai, nhớ activate venv trước:

```bash
python -m scripts.create_admin --username admin --password Admin12345
python -m scripts.seed_tasks
python -m scripts.seed_demos --reset      # sinh 40 demo mẫu, mất 1–2 phút
```

### Chạy test

```bash
python -m pytest -q
```

### Đổi schema DB

Dự án chưa dùng Alembic; `init_db` chỉ gọi `create_all` nên **thêm hoặc sửa cột KHÔNG tự
áp dụng lên DB đã tồn tại** (lỗi `no such column: ...`). Khi kéo code mới về mà gặp lỗi này:

```bash
# dừng server (Ctrl+C) trước, Windows khoá file SQLite
rm -rf data/
# chạy lại server, rồi chạy lại 3 script seed ở trên
```

Ví dụ gần đây: đổi `Episode.size_bytes`/`Dataset.size_bytes` từ `Integer` sang `BigInteger`
(tránh tràn số khi dataset >2GB trên PostgreSQL) — kéo code mới về mà DB SQLite cũ vẫn còn thì
phải `rm -rf data/` rồi seed lại như trên, không tự động migrate.

### Chuẩn bị demo trực tiếp (test tay luồng review)

Từ khi chặn tự duyệt (`ensure_not_self_review` — xem `detail_backend_withoutRobot.md` §6.3),
luồng demo → review cần **HAI tài khoản khác nhau**: một tài khoản upload demo (operator), một
tài khoản khác duyệt (reviewer/admin). Dùng tài khoản admin có sẵn (`create_admin`) để tạo thêm
một user role `reviewer`:

```bash
TOKEN=$(curl -s -X POST -d "username=admin&password=Admin12345" \
  http://localhost:8000/api/v1/auth/login | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"username":"reviewer1","password":"...","display_name":"Reviewer 1","role":"reviewer"}' \
  http://localhost:8000/api/v1/users
```

Muốn duyệt bằng cùng một tài khoản đã upload (ví dụ demo nhanh, không cần tài khoản thứ hai),
đặt `ALLOW_SELF_REVIEW=true` trong `.env` — mặc định `false`.

### Lưu ý

- Luôn dùng `python -m uvicorn`, `python -m pytest`, `python -m scripts.xxx` — nếu máy có
  nhiều bản Python thì gõ lệnh trần rất dễ chạy nhầm môi trường.
- `data/` chứa video, DB và file zip — không commit lên git.
- Hook `.git/hooks/pre-push` (nộp AI log) hiện không chạy được trên Windows, phải push kèm
  `--no-verify`. **Đang chờ xử lý** — xem mục Việc còn tồn đọng.

### Phạm vi hiện tại

- **Auth/Users, Tasks, Demos** (upload, xem, tua, trim, gắn nhãn, duyệt), **Datasets**
  (đóng gói zip, tải về).
- **Teleoperation thật** — điều khiển realtime qua WebSocket trên robosuite/MuJoCo,
  ghi episode vào DB. Xem `SIM_BACKEND_FRONTEND_INTEGRATION.md`.
- **Thu scripted tự động** cho 4 task: `lift`, `can`, `square`, `tool_hang`.
- **Chấm nhãn tự động (MVP)** — xem `docs/auto_labeling_mvp.md`.

Chưa làm: Training/Eval (PyTorch) và export LeRobot/RLDS/DVC.

> `plan_backend_core.md`, `plan_backend.md` và `detail_backend_withoutRobot.md` là
> **tài liệu kế hoạch cũ**, giữ lại làm lịch sử. Chúng nói Teleoperation "không làm" —
> điều đó **không còn đúng**. Đọc mục này và các file trong `docs/` để biết trạng
> thái thật.

### Task ToolHang

Là **task đầy đủ hai giai đoạn**: giai đoạn 1 cắm khung móc vào đế dựng, giai đoạn 2
treo cờ-lê lên móc vừa dựng. Skill được vendor tại `src/sim/skillgen/`.
Chi tiết: [`docs/toolhang_integration.md`](docs/toolhang_integration.md).

> Lưu ý về định danh: dataset của ToolHang ghi `tool_name = "tool_hang_stage1"`.
> Đó là **định danh lịch sử** từ thời chỉ có giai đoạn 1, giữ nguyên để dữ liệu đã
> thu vẫn đọc được. Tên hiển thị cho người đọc là `TOOLHANG_DISPLAY_NAME`
> ("ToolHang (stage 1 + stage 2)") trong `src/sim/tool_hang.py`.

### Camera review

Ba camera, dùng **cùng một khung hình** ở cả Teleop lẫn lúc review:

| Camera | Nguồn |
|---|---|
| `review_front` | Cài vào scene bởi `src/sim/review_camera.py` |
| `birdview` | Có sẵn trong robosuite |
| `robot0_eye_in_hand` | Camera cổ tay, có sẵn |

File mp4 được ghi **ngay lúc thu**, nên mở trang review không phải chờ render.

### Hiệu năng vòng điều khiển — số đo thật

`control_hz` đặt **60**, nhưng đo thật chỉ đạt **~46.6 Hz**. Mục tiêu 60 Hz
**chưa đạt**. Nguyên nhân: physics 3.2 ms + ba camera 640 px 7.2 ms ≈ **13.4 ms**
so với ngân sách 16.7 ms; phần còn lại là chi phí vòng lặp và mã hoá.
Kiểm `control_hz_actual` trong `stats` để biết loop có giữ nhịp không.

---
