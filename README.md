# TeleCollect — Team NEURA

Nền tảng teleoperation & thu thập demonstration cho imitation learning (đề bài RAV-12).

---

## Tài liệu theo giai đoạn

| Giai đoạn | Thư mục | Nội dung |
|---|---|---|
| Gate 1 | [`docs/gate1/`](docs/gate1/) | Project Brief, PRD, Wireframe & UI Flow, AI Log Setup |
| Gate 2 | [`docs/gate2/`](docs/gate2/) | Video demo MVP, sơ đồ kiến trúc, eval evidences |
| Phase 1 | [`docs/phase1/`](docs/phase1/) | Mô tả chi tiết dự án, nội dung slide trình bày |

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

### Biến môi trường

`src/config.py` là nguồn sự thật; `.env.example` chép lại kèm chú thích. Chỉ
`JWT_SECRET` là bắt buộc đặt tay, phần còn lại có mặc định chạy được ngay.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JWT_SECRET` | *(rỗng)* | **Bắt buộc.** Khoá ký JWT |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` | Phải là driver async |
| `STORAGE_DIR` | `./data` | Gốc chứa episode, dataset zip |
| `REVIEW_DIR` | `./data/review` | Workspace scripted + chấm nhãn |
| `MAX_UPLOAD_MB` | `200` | Trần mỗi file upload |
| `CONTROL_HZ` | `60` | Nhịp vòng điều khiển |
| `MAX_CONCURRENT_SESSIONS` | `4` | Số phiên teleop song song |
| `TELEMETRY_HZ` / `STREAM_FPS` / `JPEG_QUALITY` | `15` / `15` / `75` | Nhịp và chất lượng stream về browser |
| `TELEOP_CAMERAS` | `review_front,birdview,robot0_eye_in_hand` | Camera ghi vào episode |
| `PREVIEW_CAMERA` / `_SECONDARY` / `_TERTIARY` | `review_front` / `birdview` / `robot0_eye_in_hand` | Ba khung stream |
| `PREVIEW_SIZE` | `640` | Độ phân giải preview chính; `0` = dùng lại ảnh ghi |
| `ALLOW_SELF_REGISTER` | `true` | Bật `POST /auth/register` |
| `ALLOW_SELF_REVIEW` | `false` | Cho tự duyệt demo mình upload |
| `CORS_ORIGINS` | `http://localhost:3000` | Origin frontend được phép |
| `RULE_*` (9 biến) | xem `src/config.py` | Ngưỡng cho auto-label rule |
| `SHIM_MCCOMPAT` | — | Chỉ Windows/Optimus, xem mục Chọn GPU |
| `AI_LOG_SERVER` / `AI_LOG_API_KEY` / `AI_LOG_DIR` | BTC cung cấp | Nộp AI log |

> **Bỏ qua `OPENAI_API_KEY`, `LANGCHAIN_*` và `CHROMA_PERSIST_DIR`.** Chúng đi
> theo template dự án của môn học và **không được đọc ở bất cứ đâu trong
> `src/`** — dự án này không gọi LLM lúc chạy. Đừng mất công điền.
>
> Hai lệch nhỏ giữa `.env.example` và `src/config.py`, mặc định trong code mới
> là cái đang chạy: `CONTROL_HZ` (60, không phải 30), và nhóm camera
> (`.env.example` còn ghi `agentview`/`frontview` từ trước khi có `review_front`).
> Nhóm `RULE_*` cùng `PREVIEW_CAMERA_TERTIARY` chưa có trong `.env.example`.

### Sample queries

Các lệnh dưới đây **đã chạy thật** trên server local ngày 16/08/2026; response
là bản rút gọn của output thật. Bản đầy đủ nằm trong
[`docs/gate2/eval_evidences.md`](docs/gate2/eval_evidences.md).

**1. Đăng nhập, lấy token** — mọi lệnh sau đều cần nó.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=admin&password=Admin12345" \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

```json
{"id":"7807e84b-...","username":"admin","display_name":"admin",
 "role":"admin","is_active":true,"created_at":"2026-08-16T..."}
```

**2. Xem danh sách task sim** — tên task dùng cho teleop và scripted.

```bash
curl -s http://localhost:8000/api/v1/teleop/tasks -H "Authorization: Bearer $TOKEN"
```

```json
[{"name":"lift_cube","action_dim":7,"max_steps":500,...},
 {"name":"pick_place_can","action_dim":7,"max_steps":400,...},
 {"name":"nut_assembly_square","action_dim":7,"max_steps":500,...},
 {"name":"tool_hang","max_steps":1500,
  "description":"Giai doan 1: cam khung moc vao de dung; giai doan 2: treo co-le len moc vua dung. ..."}]
```

**3. Chạy scripted collection** — cần role ≥ reviewer. Trả 202 ngay, việc thu
chạy nền; poll `runs/{id}` cho tới khi `succeeded`.

```bash
curl -s -X POST http://localhost:8000/api/v1/labeling/runs \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"task":"tool_hang","quality":"clean","episodes":1}'

curl -s http://localhost:8000/api/v1/labeling/runs/0ae9e796e013 -H "Authorization: Bearer $TOKEN"
```

```json
{"id":"0ae9e796e013","status":"succeeded","done":1,"total":1,
 "log":["episode=0 seed=1 success=True stage1=True stage2=True failure=None steps=1797",
        "video saved: tool_hang_002"],
 "result":{"output":"data\\review\\datasets\\tool_hang_clean_seed1.hdf5","successes":1}}
```

Một episode ToolHang mất ~45 s trên RTX 3050. `tool_hang` chỉ nhận
`quality=clean`; mức nhiễu khác trả 400.

**4. Đọc nhãn tự động.** Mặc định endpoint **giấu điểm máy** (chấm mù);
thêm `include_score=true` mới thấy `auto_score`, `gate_decision`, `auto_flags`.

```bash
curl -s "http://localhost:8000/api/v1/labeling/episodes?task=tool_hang&include_score=true" \
  -H "Authorization: Bearer $TOKEN"
```

```json
{"episodes":[{"display_name":"tool_hang_002",
  "episode_id":"tool_hang_clean_seed1.hdf5::demo_0",
  "auto_score":1.0,"gate_decision":"needs_review","recorded_success":true,
  "auto_flags":{"failed_checks":[],"unavailable_checks":[]},
  "auto_label":"review",
  "auto_label_reason":"ToolHang accepts are pending a full quality rule"}],
 "count":2,"total":5}
```

Điểm 1.0 mà nhãn vẫn là `review` — **đúng thiết kế**, xem caveat ToolHang bên dưới.

**5. Duyệt demo rồi đóng gói dataset.** Chú ý hai endpoint duyệt dùng **hai bộ
từ vựng khác nhau**: `/demos/{id}/review` nhận `approve`/`reject`, còn
`/labeling/labels` nhận `approved`/`rejected`.

```bash
curl -s -X POST http://localhost:8000/api/v1/demos/lift_cube_1f89f148/review \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"decision":"approve","note":"ok"}'

curl -s -X POST http://localhost:8000/api/v1/datasets \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"gate2_demo_dataset","task_names":["lift_cube"],
       "include_failures":false,"overwrite":true}'

curl -s http://localhost:8000/api/v1/datasets/277e7403-111a-4422-ab2c-7fcfc3373e7b \
  -H "Authorization: Bearer $TOKEN"
```

```json
{"id":"277e7403-111a-4422-ab2c-7fcfc3373e7b","name":"gate2_demo_dataset",
 "status":"ready","num_episodes":1,"num_frames":370,"size_bytes":593713}
```

Tải file zip về (endpoint cũng nhận `?token=` thay cho header):

```bash
curl -s -o dataset.zip \
  http://localhost:8000/api/v1/datasets/277e7403-111a-4422-ab2c-7fcfc3373e7b/download \
  -H "Authorization: Bearer $TOKEN"
```

Nhớ **duyệt bằng tài khoản khác tài khoản đã thu** (`ensure_not_self_review`) —
xem mục "Chuẩn bị demo trực tiếp" bên dưới.

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

**Về nhãn tự động.** ToolHang hiện luôn nhận nhãn `review`, kể cả khi đạt điểm
1.0 và qua hết các kiểm tra bắt buộc. Bộ metric đánh giá `accept` cho task này
đang được xây dựng: là task hai giai đoạn, nó cần thêm tiêu chí cho chất lượng
thao tác bên cạnh điều kiện thành công. Giữ người duyệt trong vòng lặp ở giai
đoạn này vừa bảo đảm không có episode nào vào tập đã duyệt mà chưa ai xem, vừa
tích luỹ dữ liệu để hiệu chỉnh ngưỡng cho luật tự động.

### Camera review

Ba camera, dùng **cùng một khung hình** ở cả Teleop lẫn lúc review:

| Camera | Nguồn |
|---|---|
| `review_front` | Cài vào scene bởi `src/sim/review_camera.py` |
| `birdview` | Có sẵn trong robosuite |
| `robot0_eye_in_hand` | Camera cổ tay, có sẵn |

File mp4 được ghi **ngay lúc thu**, nên mở trang review không phải chờ render.

---
