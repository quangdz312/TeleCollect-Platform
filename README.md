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

- **Python 3.11.x** (không dùng 3.13 — một số thư viện chưa có wheel sẵn)
- **ffmpeg / ffprobe** cài sẵn và nằm trong PATH — bắt buộc, dùng để đọc metadata
  video, sinh thumbnail và tạo dữ liệu mẫu.
  Kiểm tra: `ffprobe -version`
  Windows: `winget install Gyan.FFmpeg` (mở lại terminal sau khi cài)

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

### Lưu ý

- Luôn dùng `python -m uvicorn`, `python -m pytest`, `python -m scripts.xxx` — nếu máy có
  nhiều bản Python thì gõ lệnh trần rất dễ chạy nhầm môi trường.
- `data/` chứa video, DB và file zip — không commit lên git.
- Hook `.git/hooks/pre-push` (nộp AI log) hiện không chạy được trên Windows, phải push kèm
  `--no-verify`. **Đang chờ xử lý** — xem mục Việc còn tồn đọng.

### Phạm vi hiện tại

Backend đang ở **bản Core**: Auth/Users, Tasks, Demos (upload, xem, tua, trim, gắn nhãn,
duyệt), Datasets (đóng gói zip, tải về). Video demo được đưa vào qua upload thủ công.

Phần Teleoperation (điều khiển robot realtime), Training/Eval và export LeRobot/RLDS
**chưa làm** — xem `plan_backend_core.md` để biết ranh giới phạm vi, và `plan_backend.md`
cho kế hoạch đầy đủ.

---
