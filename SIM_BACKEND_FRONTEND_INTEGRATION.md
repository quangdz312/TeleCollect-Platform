# Tổng hợp ghép frontend, backend, sim và auto data

Ngày cập nhật: 2026-08-11

## Trạng thái hiện tại

Đã ghép chọn lọc phần `sim-demo` vào nhánh local hiện tại `feat/frontend_backend`.

Không pull, không merge nguyên nhánh, không commit và không push.

Mục tiêu khi ghép:

- Giữ backend hiện tại làm nền chính: auth, user, task, demo review, dataset.
- Mang phần sim/teleop/scripted generation từ `origin/sim-demo` vào như module mới.
- Cho frontend gọi backend thật thay vì mock.
- Cho manual teleop lưu episode vào DB để trang Review/Dataset nhìn thấy.
- Cho scripted auto collection sinh HDF5 và chấm nhãn trên web.

## Link test

Chỉ cần mở link chính:

```text
http://localhost:3000
```

Các trang con:

```text
http://localhost:3000/teleop
http://localhost:3000/scripted
```

Backend health:

```text
http://127.0.0.1:8000/health
```

Tài khoản dev đã dùng:

```text
admin / Admin12345
```

## Backend đã ghép

### Teleop thật

File chính:

- `src/api/teleop.py`
- `src/core/session.py`
- `src/core/control_loop.py`
- `src/core/recorder.py`
- `src/core/protocol.py`
- `src/sim/environment.py`
- `src/sim/tasks.py`

Endpoint chính:

```text
GET  /api/v1/teleop/tasks
POST /api/v1/teleop/sessions
GET  /api/v1/teleop/sessions
GET  /api/v1/teleop/sessions/{id}
GET  /api/v1/teleop/sessions/{id}/stats
DEL  /api/v1/teleop/sessions/{id}
WS   /api/v1/teleop/ws/{session_id}?token=...
```

Điểm đã chỉnh so với `sim-demo`:

- `POST /teleop/sessions` yêu cầu JWT.
- `operator_id` lấy từ user đăng nhập, không tin dữ liệu client gửi.
- WebSocket xác thực bằng token query.
- Stop & Save sẽ tạo row `Episode` trong DB hiện tại.
- Video sim được alias sang `front.mp4` và `wrist.mp4` để trang Review hiện tại phát được.
- Thêm background reaper trong `src/main.py` để dọn session rớt kết nối.

### Scripted auto collection + labeling

File chính:

- `src/api/labeling.py`
- `src/labeling/**`
- `src/sim/scripted_generation.py`
- `src/sim/tools/**`
- `src/sim/operators/**`
- `src/sim/task_adapters/**`
- `src/sim/perturbations/**`

Endpoint chính:

```text
GET  /api/v1/labeling/config
GET  /api/v1/labeling/overview
POST /api/v1/labeling/runs
GET  /api/v1/labeling/runs
GET  /api/v1/labeling/runs/{job_id}
GET  /api/v1/labeling/episodes
GET  /api/v1/labeling/episodes/detail
POST /api/v1/labeling/episodes/video
GET  /api/v1/labeling/video
POST /api/v1/labeling/labels
GET  /api/v1/labeling/report
```

Điểm đã chỉnh:

- Các endpoint JSON yêu cầu role `reviewer` hoặc `admin`.
- Video endpoint nhận token query để thẻ `<video>` xem được.
- Reviewer lấy từ user thật, không dùng hard-code `"web"`.
- Lazy import phần HDF5/robosuite để backend vẫn start được nếu chưa dùng scripted.
- Thêm fallback metadata cho `lift/can/square` khi thiếu reference dataset:
  - `data/datasets/lift/ph/low_dim_v15.hdf5`
  - `data/datasets/can/ph/low_dim_v15.hdf5`
  - `data/datasets/square/ph/low_dim_v15.hdf5`

## Frontend đã ghép

### Teleop

File chính:

- `frontend/app/teleop/page.tsx`
- `frontend/components/TeleopConsole.tsx`
- `frontend/lib/real-teleop.ts`
- `frontend/lib/teleop.ts`

Điểm chính:

- Trang `/teleop` lấy task từ `/api/v1/teleop/tasks`.
- Client WebSocket thật nằm ở `frontend/lib/real-teleop.ts`.
- Giữ UI `TeleopConsole` hiện tại.
- Space/gripper đã sửa:
  - đóng gripper: `+1`
  - mở gripper: `-1`

Lỗi đã sửa:

- Sim nhấp nháy do frontend đóng `ImageBitmap` quá sớm.
- Space chỉ đổi state UI nhưng không mở gripper trong sim do gửi sai dấu gripper.

### Scripted

File chính:

- `frontend/app/scripted/page.tsx`
- `frontend/lib/labeling.ts`
- `frontend/components/Nav.tsx`

Trang `/scripted` hiện có:

- Chọn task `lift/can/square`.
- Chọn quality `clean/good/medium/poor`.
- Start collection.
- Poll job progress.
- List episode.
- Request/render video.
- Approve/reject với lý do.
- Recompute shadow report.

## Data output

Manual teleop lưu vào:

```text
data/episodes/<episode_id>/
```

Gồm các file:

```text
meta.json
actions.parquet
agentview.mp4
robot0_eye_in_hand.mp4
front.mp4
wrist.mp4
```

Scripted auto collection lưu vào:

```text
data/review/
```

Gồm:

```text
data/review/datasets/*.hdf5
data/review/videos/*.mp4
data/review/scores.jsonl
data/review/labels.jsonl
```

Đã test scripted `lift clean 1 episode` thành công, output:

```text
data/review/datasets/lift_clean_seed0.hdf5
```

## Dependency đã cập nhật

File:

```text
requirements.txt
```

Thêm/chỉnh:

```text
mujoco>=3.3.0,<3.9.0
numpy>=1.26.0,<2.0.0
robosuite>=1.5.0
h5py>=3.11.0
opencv-python-headless>=4.10.0,<4.12.0
```

Lý do:

- `robosuite 1.5.x` chưa hợp với MuJoCo mới hơn `3.9`.
- `mink/numba/robosuite` cần NumPy `<2`.
- OpenCV headless bản mới kéo NumPy 2, nên cần ghim `<4.12`.

Đã chạy:

```text
pip check
```

Kết quả:

```text
No broken requirements found.
```

## Cách chạy

Backend:

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///./data/app.db"
$env:JWT_SECRET="dev-local-secret"
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
$env:NEXT_PUBLIC_API_URL="http://127.0.0.1:8000"
$env:NEXT_PUBLIC_WS_BASE="http://127.0.0.1:8000"
npm run dev
```

Mở:

```text
http://localhost:3000
```

## Verification đã chạy

Backend:

```text
python -m compileall -q src
backend import ok
sim deps import ok
pytest
```

Kết quả pytest:

```text
161 passed, 37 skipped
```

Frontend:

```text
npm run typecheck
npm run build
```

Kết quả:

```text
passed
```

Scripted API:

```text
GET /api/v1/labeling/config
POST /api/v1/labeling/runs
```

Đã test `lift clean 1 episode`:

```text
status: succeeded
episodes: 1
successes: 1
```

## Lưu ý còn lại

- Backend log báo máy chưa có `ffmpeg/ffprobe` trong PATH. Các flow upload thumbnail/playback video cũ có thể lỗi nếu chưa cài ffmpeg.
- `robosuite` có warning chưa có private `macros.py`; không chặn import/chạy cơ bản.
- Local branch đang `behind 1` so với `origin/feat/frontend_backend`.
- Workspace còn nhiều file modified/untracked vì chưa commit.
- Chưa push bất kỳ thay đổi nào.

