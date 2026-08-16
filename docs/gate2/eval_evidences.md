# Eval Evidences — Gate 2

Năm test case chạy tay, đi hết luồng chính của người dùng: đăng nhập → thu demo
bằng tay → sinh data scripted → chấm nhãn tự động → duyệt và đóng gói.

**Mọi output trong tài liệu này là output thật, copy nguyên văn từ lần chạy
ngày 16/08/2026.** Không có đoạn nào được viết lại cho đẹp.

## Môi trường lúc chạy

| Hạng mục | Giá trị |
|---|---|
| Ngày chạy | 16/08/2026, 17:00–17:05 (+07:00) |
| Branch | `demo-v2` |
| Backend | `python -m uvicorn src.main:app --port 8000` → http://localhost:8000 |
| Frontend | `npm run dev` (Next.js 16.3) → http://localhost:3000, `✓ Ready in 695ms` |
| Python | 3.12.6 (`.venv`) |
| GPU | RTX 3050 qua `SHIM_MCCOMPAT=0x800000001` |
| pytest | `223 passed, 39 skipped in 26.40s` — đúng bằng baseline, delta = 0 |

**Một cảnh báo có thật lúc khởi động backend**, giữ nguyên vì nó ảnh hưởng tới
phạm vi test case 5:

```
INFO:     Started server process [23072]
INFO:     Waiting for application startup.
ffmpeg/ffprobe not found on PATH - video upload, thumbnail and playback features will fail. Install ffmpeg (e.g. `apt-get install ffmpeg` or `choco install ffmpeg`) and restart the app.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

`ffmpeg`/`ffprobe` **không có trên máy chạy test** (kiểm bằng `Get-Command ffmpeg`
lẫn `which ffmpeg`, cả hai đều không thấy). Điều này **không chặn** bốn test case
đầu vì video teleop được ghi bằng `imageio-ffmpeg` bundle trong venv, không phải
binary hệ thống. Nó chỉ chặn nhánh upload demo từ file ngoài — nhánh đó không
nằm trong năm case này. Xem mục "Khoảng trống đã biết" ở cuối.

---

## Case 1 — Đăng nhập

**Mục tiêu.** Đăng ký một tài khoản operator, lấy JWT, và dùng JWT đó gọi được
một endpoint yêu cầu xác thực. Đồng thời chứng minh endpoint đó thật sự bị chặn
khi không có token.

**Các bước.**

1. `POST /api/v1/auth/register` tạo user `gate2_operator`.
2. `POST /api/v1/auth/login` lấy `access_token`.
3. `GET /api/v1/auth/me` kèm `Authorization: Bearer <token>`.
4. Hai case âm: gọi `/auth/me` không token, và login sai mật khẩu.

**Kết quả mong đợi.** 201 khi đăng ký, 200 + cặp token khi login, 200 + đúng
thông tin user khi gọi `/auth/me`, 401 cho cả hai case âm. Role phải là
`operator` (register không bao giờ được tạo admin).

**Output thực tế.**

```console
$ curl -X POST http://localhost:8000/api/v1/auth/register \
    -H "Content-Type: application/json" \
    -d '{"username":"gate2_operator","password":"Gate2Pass123","display_name":"Gate2 Operator"}'

{"id":"0423cbd6-a8a8-4678-8473-3bb7e4e3e14d","username":"gate2_operator","display_name":"Gate2 Operator","role":"operator","is_active":true,"created_at":"2026-08-16T09:59:49.632984"}
HTTP 201  time=0.471335s
```

```console
$ curl -X POST http://localhost:8000/api/v1/auth/login \
    -d "username=gate2_operator&password=Gate2Pass123"

{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwNDIzY2JkNi1hOGE4LTQ2NzgtODQ3My0zYmI3ZTRlM2UxNGQiLCJyb2xlIjoib3BlcmF0b3IiLCJ0eXBlIjoiYWNjZXNzIiwiaWF0IjoxNzg2ODc0MzkwLCJleHAiOjE3ODY4NzYxOTB9.DVLDv2wDtIHvpy11Obw3grn5BFunIuRiSUqBq6I4Pvs","refresh_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwNDIzY2JkNi1hOGE4LTQ2NzgtODQ3My0zYmI3ZTRlM2UxNGQiLCJyb2xlIjoib3BlcmF0b3IiLCJ0eXBlIjoicmVmcmVzaCIsImlhdCI6MTc4Njg3NDM5MCwiZXhwIjoxNzg3NDc5MTkwfQ.5etcJRimzZALb2UfuS8TVACh8oNkpG4e3NWIvEHi7O8","token_type":"bearer"}
HTTP 200  time=0.432884s
```

Giải mã payload của `access_token` (phần giữa, base64):
`{"sub":"0423cbd6-a8a8-4678-8473-3bb7e4e3e14d","role":"operator","type":"access","iat":1786874390,"exp":1786876190}`
— `exp - iat = 1800s = 30 phút`, khớp `access_token_expire_minutes` mặc định.

```console
$ curl http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $OP"

{"id":"0423cbd6-a8a8-4678-8473-3bb7e4e3e14d","username":"gate2_operator","display_name":"Gate2 Operator","role":"operator","is_active":true,"created_at":"2026-08-16T09:59:49.632984"}
HTTP 200
```

Hai case âm:

```console
$ curl http://localhost:8000/api/v1/auth/me            # không có token
{"detail":"Không xác thực được"}
HTTP 401

$ curl -X POST http://localhost:8000/api/v1/auth/login -d "username=gate2_operator&password=WRONG"
{"detail":"Sai username hoặc mật khẩu"}
HTTP 401
```

**Đạt.** JWT cấp đúng, role bị ép về `operator`, endpoint có bảo vệ thật chứ
không phải chỉ trả 200 cho mọi request. Thông báo lỗi khi sai mật khẩu không
tiết lộ username có tồn tại hay không.

---

## Case 2 — Thu demo bằng điều khiển tay (teleop)

**Mục tiêu.** Mở phiên teleop thật trên robosuite/MuJoCo, nối WebSocket, điều
khiển tay gắp, thu và lưu một episode. Bằng chứng phải cho thấy **ba luồng
camera** và **episode nằm trên đĩa**.

**Các bước.**

1. `POST /api/v1/teleop/sessions` với `task_name=lift_cube`, `seed=42`.
2. Nối `ws://localhost:8000/api/v1/teleop/ws/{session_id}?token=...`.
3. Gửi `{"type":"record","action":"start"}`.
4. Gửi chuỗi lệnh `input`: hạ tay (gripper mở) → đóng gripper → nhấc lên → giữ.
5. Gửi `{"type":"record","action":"stop"}`, đọc event `recording_saved`.
6. Kiểm thư mục episode và bản ghi DB.

**Kết quả mong đợi.** Session tạo được, WebSocket nhận cả obs JSON lẫn frame
JPEG nhị phân của ba camera, episode lưu xuống `data/episodes/<id>/` với đủ mp4
+ parquet + meta.json, và một dòng trong bảng `episodes`.

**Output thực tế.**

```console
$ curl -X POST http://localhost:8000/api/v1/teleop/sessions \
    -H "Authorization: Bearer $OP" -H "Content-Type: application/json" \
    -d '{"task_name":"lift_cube","seed":42}'

{"session_id":"3a8e1589-cfcb-4e0e-9af9-611ee5763394","task_name":"lift_cube","state":"idle","ws_url":"/api/v1/teleop/ws/3a8e1589-cfcb-4e0e-9af9-611ee5763394","action_spec":{"dim":7,"low":[-1.0,-1.0,-1.0,-1.0,-1.0,-1.0,-1.0],"high":[1.0,1.0,1.0,1.0,1.0,1.0,1.0]}}
HTTP 201

real	0m4.008s
```

Dựng MuJoCo mất 4.0 s — đúng như docstring `create_session` cảnh báo, nên nó
chạy trong executor.

Chạy WebSocket bằng gói `websockets` 15.0.1 có sẵn trong venv:

```
[ws] connected to ws://localhost:8000/api/v1/teleop/ws/3a8e1589-cfcb-4e0e-9af9-611ee5763394?token=...
[obs] first observation: {"type": "obs", "seq": 0, "sim_time": 25.2333, "qpos": [-0.0091, 0.18956, -0.01581], "qvel": [2e-05, 0.0, -1e-05], "ee_pose": [-0.08732, -0.01314, 1.02295], "gripper_closed": false, "task_success": false, "session_state": "idle", "episode_id": null, "recorded_steps": 0}
[frame] first frame camera 0: 25139 bytes -> teleop_cam0.jpg
[frame] first frame camera 1: 55358 bytes -> teleop_cam1.jpg
[frame] first frame camera 2: 16354 bytes -> teleop_cam2.jpg
[ws] after 2s warmup: obs=27 frames={0: 22, 1: 22, 2: 22}
[event] {"type": "recording_started", "episode_id": "lift_cube_1f89f148"}
[drive] descend, gripper open: dx=0.0 dy=0.0 dz=-0.35 grip=-1.0 x60
[drive] close gripper: dx=0.0 dy=0.0 dz=0.0 grip=1.0 x30
[drive] lift: dx=0.0 dy=0.0 dz=0.45 grip=1.0 x70
[drive] hold: dx=0.0 dy=0.0 dz=0.0 grip=1.0 x20
[drive] driving done, 7.47s of recording
[event] {"type": "recording_saved", "episode_id": "lift_cube_1f89f148", "num_steps": 370, "duration_s": 6.15, "task_success": false, "partial": false, "stop_reason": "operator"}

===== SUMMARY =====
ws session total: 13.67s, recording window: 7.47s
obs messages: 178
camera 0: 156 frames, 3918639 bytes total, avg 25119 B/frame
camera 1: 155 frames, 8595798 bytes total, avg 55456 B/frame
camera 2: 155 frames, 2663732 bytes total, avg 17185 B/frame
last stats: {"type": "stats", "ticks": 2083, "dropped_frames": 388, "p50_latency_ms": 17.55, "p95_latency_ms": 24.6, "overruns": 1294, "jitter_rms_ms": 24.16, "control_hz_actual": 45.94}
```

**Ba camera đều có frame thật, không phải khung trắng.** Frame đầu của mỗi
camera được ghi ra file và đính kèm ở đây:

| Camera | Byte header | Nguồn | Ảnh |
|---|---|---|---|
| `review_front` | `\x00` | `src/sim/review_camera.py` | ![review_front](img/teleop_review_front.jpg) |
| `birdview` | `\x01` | robosuite | ![birdview](img/teleop_birdview.jpg) |
| `robot0_eye_in_hand` | `\x02` | camera cổ tay | ![eye_in_hand](img/teleop_eye_in_hand.jpg) |

Ảnh `review_front` thấy rõ cánh tay Panda, mặt bàn và khối lập phương đỏ; ảnh
cổ tay thấy khối đỏ nằm giữa hai ngón gripper. Đây là điểm mà một vòng làm việc
trước đã báo "đạt" trong khi video ra trắng trơn — lần này ảnh nằm ngay trong
repo để người chấm tự kiểm.

Episode trên đĩa:

```console
$ ls -la data/episodes/lift_cube_1f89f148/
-rw-r--r-- 1 buitu 197609 187744 Aug 16 17:01 actions.parquet
-rw-r--r-- 1 buitu 197609 308400 Aug 16 17:01 birdview.mp4
-rw-r--r-- 1 buitu 197609 252670 Aug 16 17:01 front.mp4
-rw-r--r-- 1 buitu 197609   1004 Aug 16 17:01 meta.json
-rw-r--r-- 1 buitu 197609 252670 Aug 16 17:01 review_front.mp4
-rw-r--r-- 1 buitu 197609 339048 Aug 16 17:01 robot0_eye_in_hand.mp4
-rw-r--r-- 1 buitu 197609 339048 Aug 16 17:01 wrist.mp4
```

`meta.json` thật:

```json
{
  "episode_id": "lift_cube_1f89f148",
  "task_name": "lift_cube",
  "operator_id": "0423cbd6-a8a8-4678-8473-3bb7e4e3e14d",
  "num_steps": 370,
  "duration_s": 6.149999999999654,
  "control_hz": 60,
  "started_at": "2026-08-16T17:00:59.349748+07:00",
  "cameras": ["birdview", "review_front", "robot0_eye_in_hand"],
  "teleop_schema_version": 2,
  "video": {"width": 640, "height": 640, "fps": 60, "codec": "h264", "pixel_format": "yuv420p"},
  "privileged_state": {"format": "mujoco_flat_state", "column": "privileged_state",
                       "dtype": "float64", "dim": 32, "bytes_per_frame": 256, "recorded": true},
  "seed": 42,
  "task_success": false,
  "partial": false,
  "interrupted": false,
  "stop_reason": "operator",
  "p50_server_latency_ms": 17.496600001322804,
  "p95_server_latency_ms": 24.42950000113342,
  "jitter_rms_ms": 6.304846885468454,
  "overruns": 1226,
  "dropped_stream_frames": 386
}
```

Bản ghi DB (`sqlite3 data/app.db`):

```json
{
  "id": "lift_cube_1f89f148",
  "task_name": "lift_cube",
  "operator_id": "0423cbd6-a8a8-4678-8473-3bb7e4e3e14d",
  "status": "labeled",
  "outcome": "failure",
  "note": "Generated by web teleop",
  "fps": 60.0,
  "num_frames": 370,
  "duration_s": 6.15,
  "size_bytes": 1680584,
  "has_wrist": 1,
  "created_at": "2026-08-16 10:01:07.401258"
}
```

`GET /teleop/sessions/{id}/stats` ngay trước khi đóng phiên:

```json
{"ticks":2945,"dropped_frames":597,"p95_latency_ms":23.65,"overruns":1876,
 "p50_latency_ms":17.03,"jitter_rms_ms":6.35,"control_hz_actual":46.14}
```

**Đạt** — với một ghi chú trung thực.

*`task_success: false`.* Chuỗi lệnh điều khiển ở trên là kịch bản mù (hạ – kẹp –
nhấc theo số bước cố định), không có vòng phản hồi thị giác, nên gripper không
kẹp trúng khối. Đây là **kết quả thật của một lần điều khiển tay vụng**, và nó
vẫn chứng minh đúng thứ case này cần chứng minh: đường điều khiển, ba luồng
camera và việc ghi episode đều hoạt động. Case 5 sẽ gắn nhãn lại episode này
thành `success` một cách tường minh qua API để chạy tiếp luồng duyệt.

---

## Case 3 — Sinh data scripted (ToolHang)

**Mục tiêu.** Sinh một episode ToolHang qua API thật, chứng minh nó **chạy cả
hai giai đoạn**, kèm thời gian và các file đã ghi.

**Các bước.**

1. `POST /api/v1/labeling/runs` với `task=tool_hang`, `quality=clean`, `episodes=1`.
2. Poll `GET /api/v1/labeling/runs/{job_id}` cho tới khi `succeeded`.
3. Kiểm file HDF5 + mp4 và đọc attrs trong HDF5.

**Kết quả mong đợi.** Job 202 rồi `succeeded`, log ghi rõ `stage1=True stage2=True`,
sinh ra một file HDF5 đúng schema robomimic và một mp4.

**Output thực tế.**

```console
$ date +"start %H:%M:%S"
start 17:02:04

$ curl -X POST http://localhost:8000/api/v1/labeling/runs \
    -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
    -d '{"task":"tool_hang","quality":"clean","episodes":1}'

{"id":"0ae9e796e013","kind":"collect","request":{"task":"tool_hang","quality":"clean","episodes":1,"seed":1,"horizon":null,"output":"tool_hang_clean_seed1.hdf5"},"status":"running",...,"progress":0.0}
HTTP 202
```

Job kết thúc:

```json
{
    "id": "0ae9e796e013",
    "kind": "collect",
    "request": {"task": "tool_hang", "quality": "clean", "episodes": 1, "seed": 1,
                "horizon": null, "output": "tool_hang_clean_seed1.hdf5"},
    "status": "succeeded",
    "started_at": "2026-08-16T10:02:04+00:00",
    "finished_at": "2026-08-16T10:02:49+00:00",
    "done": 1,
    "total": 1,
    "log": [
        "episode=0 seed=1 success=True stage1=True stage2=True failure=None steps=1797",
        "video saved: tool_hang_002"
    ],
    "error": null,
    "result": {
        "output": "data\\review\\datasets\\tool_hang_clean_seed1.hdf5",
        "task": "tool_hang", "quality": "clean", "episodes": 1, "successes": 1,
        "profile_version": "tool-hang-stage1-baseline",
        "corpus_episodes": 5, "videos": 1
    },
    "progress": 1.0
}
```

**Thời gian: 45 giây** (`10:02:04` → `10:02:49`) cho một episode 1797 bước.
Dòng log `stage1=True stage2=True` là bằng chứng trực tiếp cả hai giai đoạn đều
đạt — cắm khung vào đế, rồi treo cờ-lê lên khung.

File đã ghi:

```console
$ ls -la data/review/datasets/tool_hang_clean_seed1.hdf5
-rw-r--r-- 1 buitu 197609 2695768 Aug 16 17:02 data/review/datasets/tool_hang_clean_seed1.hdf5

$ ls -la data/review/videos/ | grep tool_hang
-rw-r--r-- 1 buitu 197609 1848485 Aug 14 16:25 tool_hang_clean_seed0__demo_0.mp4
-rw-r--r-- 1 buitu 197609 1784495 Aug 16 17:02 tool_hang_clean_seed1__demo_0.mp4
```

HDF5 2.57 MB + mp4 1.70 MB, cả hai đóng dấu thời gian 17:02 của lần chạy này.

Cấu trúc HDF5 (đọc bằng `h5py`):

```
data/demo_0/actions: shape=(1797, 7) dtype=float64
data/demo_0/dones: shape=(1797,) dtype=int64
data/demo_0/rewards: shape=(1797,) dtype=float64
data/demo_0/states: shape=(1797, 58) dtype=float64
data/demo_0/obs/object: shape=(1797, 14) dtype=float64
data/demo_0/obs/robot0_eef_pos: shape=(1797, 3) dtype=float64
data/demo_0/obs/robot0_eef_quat: shape=(1797, 4) dtype=float64
data/demo_0/obs/robot0_gripper_qpos: shape=(1797, 2) dtype=float64
data/demo_0/obs/robot0_joint_pos: shape=(1797, 7) dtype=float64
data/demo_0/obs/robot0_joint_vel: shape=(1797, 7) dtype=float64
   (… và bộ next_obs/* tương ứng)
```

Attrs xác nhận cả hai giai đoạn:

```
env_args = {"env_name": "ToolHang", "type": 1,
            "env_kwargs": {"robots": "Panda", "control_freq": 20,
                           "horizon": 6000, "stage": "stage1+stage2"}}
telecollect_coverage       = stage1+stage2
telecollect_task           = tool_hang
telecollect_tool_name      = tool_hang_stage1
telecollect_requested_quality = clean
telecollect_noise_scale    = 0.0
total                      = 1797
```

Attrs của `demo_0`:

```
success                = True
operator_version       = toolhang-stage1-stage2-robosuite-1.5.2
telecollect_outcome    = success
telecollect_terminal_reason = success
telecollect_episode_length  = 1797
telecollect_sampled_variation = {"final_depth_mm": 150.03268314090946,
  "final_lateral_mm": 0.4325675031675264, "stage1_attempts": 1,
  "stage1_env_predicate": true, "stage1_geometric_success": true,
  "stage1_steps": 956, "stage2_reached": true, "stage2_tool_on_frame": true,
  "wall_time_s": 17.921239614486694, ...}
```

`stage2_reached: true` và `stage2_tool_on_frame: true` — cờ-lê thật sự nằm trên
khung, không chỉ là "chạy hết horizon". `tool_name = tool_hang_stage1` là định
danh lịch sử mà README đã giải thích, không phải dấu hiệu chỉ chạy giai đoạn 1.

**Đạt.**

---

## Case 4 — Auto-label

**Mục tiêu.** Cho thấy điểm và nhãn mà episode vừa sinh nhận được, và nhãn đó
**khớp với luật**: ToolHang chỉ được `review` hoặc `reject`, không bao giờ
`accept`.

**Các bước.**

1. `GET /api/v1/labeling/episodes?task=tool_hang&include_score=true`.
2. Đối chiếu với `classify_scripted()` trong `src/services/auto_label.py`.
3. `POST /api/v1/labeling/labels` — người chấm chốt nhãn.

**Kết quả mong đợi.** `auto_score` cao (mọi hard check qua), nhưng
`auto_label = "review"` chứ không phải `accept`, kèm lý do nói rõ vì sao.

**Output thực tế** cho `tool_hang_002` (chính episode của case 3), đã rút gọn
phần `provenance` trùng lặp:

```json
{
    "display_name": "tool_hang_002",
    "episode_id": "tool_hang_clean_seed1.hdf5::demo_0",
    "task": "tool_hang",
    "length": 1797,
    "auto_score": 1.0,
    "gate_decision": "needs_review",
    "recorded_success": true,
    "scorer_version": "telecollect-autolabel-dc36d21a0636",
    "auto_flags": {
        "checks": {
            "E_integrity": {"problems": [], "value": 1},
            "E_no_drop":   {"airborne_frames": 1714, "release_events": 0,
                            "worst_fall_after_release_m": 0.0,
                            "drop_fall_threshold_m": 0.15, "value": 1},
            "E_skill":     {"horizontal_travel_m": 0.2850697111169725,
                            "max_object_rise_m": 0.44611283625272735,
                            "moved_to_target": true, "rose_above_table": true,
                            "was_picked_up": true,
                            "task_specific": "tool_hang_stage1_frame_transport",
                            "value": 1},
            "E_success":   {"termination_reason": "success", "hold_verified": false,
                            "hold_unverifiable_reason": "episode ends at the first success frame",
                            "value": 1}
        },
        "failed_checks": [],
        "unavailable_checks": [],
        "penalties": {
            "gripper_toggles": {"expected": 4, "observed": 4, "raw": 0.0, "value": 0.0},
            "hitting_limits":  {"raw": 0.136895, "value": 0.0,
                                "note": "arm dimensions only; the gripper is bang-bang by contract"},
            "idle_after_trim": {"raw": 0.53645, "value": 0.0, "status": "advisory_only",
                                "reason": "tool_hang is labelled by the simulator predicate"},
            "jerkiness":       {"jerk_rms": 0.0011109750343649709, "raw": 100.0, "value": 0.0,
                                "status": "advisory_only",
                                "reason": "tool_hang is labelled by the simulator predicate"},
            "unusual_length":  {"length": 1797, "task_median": 1860.5, "mad": 63.5,
                                "raw": 0.6745, "value": 0.0, "status": "advisory_only",
                                "reason": "tool_hang is labelled by the simulator predicate"},
            "wandering_path":  {"path_length_m": 4.348095243852186,
                                "task_median_m": 4.366128636690869,
                                "raw": 0.99587, "value": 0.0}
        },
        "worst_penalty": "jerkiness",
        "worst_penalty_value": 0.0
    },
    "auto_label": "review",
    "auto_label_reason": "ToolHang accepts are pending a full quality rule",
    "label": null,
    "video_ready": true
}
```

**Nhãn có khớp luật không.** Luật trong `src/services/auto_label.py`:

```python
if recorded_success is False or gate_decision in {"rejected", "auto_reject"}:
    return AutoLabelResult("reject", "Hard failure or failed task predicate")
# ToolHang keeps a human in the loop on anything that is not an outright failure
if task == "tool_hang":
    return AutoLabelResult("review", "ToolHang accepts are pending a full quality rule")
```

Episode có `recorded_success = true` nên không rơi nhánh `reject`; `task == "tool_hang"`
nên rơi đúng nhánh thứ hai → `review`. **Khớp.** Đáng chú ý là `auto_score` bằng
**1.0** và `failed_checks` **rỗng** — nghĩa là kể cả khi mọi check đều qua hết,
ToolHang vẫn không được tự động `accept`. Đây là hành vi cố ý: bộ luật chất
lượng của ToolHang hiện vẫn là bộ của giai đoạn 1, nên `accept` sẽ là khẳng định
nhiều hơn những gì đã thật sự kiểm.

Ba `penalties` bị đánh `status: advisory_only` cũng cùng một lý do
(`"tool_hang is labelled by the simulator predicate"`) — chúng được tính và ghi
lại để tham khảo nhưng `value` bị ép về 0, không kéo điểm.

Người chấm chốt nhãn:

```console
$ curl -X POST http://localhost:8000/api/v1/labeling/labels \
    -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
    -d '{"episode_id":"tool_hang_clean_seed1.hdf5::demo_0","decision":"approved",
         "reasons":[],"note":"Gate2 manual test: stage1+stage2 both true","blind":false}'

{
    "label": {
        "episode_id": "tool_hang_clean_seed1.hdf5::demo_0",
        "task": "tool_hang", "requested_quality": "clean",
        "human_decision": "approved", "reasons": [],
        "note": "Gate2 manual test: stage1+stage2 both true",
        "reviewer": "admin", "blind": false,
        "scorer_version": "telecollect-autolabel-dc36d21a0636",
        "reviewed_at": "2026-08-16T10:03:36+00:00"
    },
    "workspace": {
        "root": "data\\review", "datasets": 5, "episodes": 5,
        "reviewed": 1, "approved": 1, "rejected": 0, "pending": 4,
        "per_task": {"can": {"total": 1, "reviewed": 0},
                     "lift": {"total": 1, "reviewed": 0},
                     "square": {"total": 1, "reviewed": 0},
                     "tool_hang": {"total": 2, "reviewed": 1}}
    }
}
```

Một quan sát phụ có thật: API từ chối `decision: "pass"` bằng
`{"detail":"decision must be approved or rejected, got 'pass'"}` (HTTP 400) —
validation hoạt động, và từ vựng đúng là `approved`/`rejected`.

**Đạt.**

---

## Case 5 — Duyệt và đóng gói

**Mục tiêu.** Duyệt một episode rồi build dataset, và cho thấy artefact đóng gói
thật: file zip, dung lượng, sha256.

**Các bước.**

1. `PATCH /api/v1/demos/{id}/label` đặt `outcome=success`.
2. `POST /api/v1/demos/{id}/review` với `decision=approve`, **bằng tài khoản
   admin khác với operator đã thu** (ràng buộc `ensure_not_self_review`).
3. `POST /api/v1/datasets` build zip.
4. `GET /api/v1/datasets/{id}` chờ `ready`.
5. Tính sha256, liệt kê nội dung zip, và tải về qua endpoint download.

**Kết quả mong đợi.** Demo chuyển sang `approved` và có `reviewer_id`; dataset
đi từ `building` → `ready`; zip tồn tại, mở được, và bản tải về trùng byte với
bản trên đĩa.

**Output thực tế.**

```console
$ curl -X PATCH http://localhost:8000/api/v1/demos/lift_cube_1f89f148/label \
    -d '{"outcome":"success","note":"Gate2 manual test"}'

{"id":"lift_cube_1f89f148",...,"status":"labeled","outcome":"success","reviewer_id":null,...}
```

```console
$ curl -X POST http://localhost:8000/api/v1/demos/lift_cube_1f89f148/review \
    -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
    -d '{"decision":"approve","note":"Gate2 manual test approval"}'

{"id":"lift_cube_1f89f148","task_name":"lift_cube",
 "operator_id":"0423cbd6-a8a8-4678-8473-3bb7e4e3e14d",
 "status":"approved","outcome":"success","note":"Gate2 manual test approval",
 "reviewer_id":"7807e84b-4bf3-4891-a714-b2632f97ec69",
 "reviewed_at":"2026-08-16T10:03:53.041711",
 "fps":60.0,"num_frames":370,"duration_s":6.15,"size_bytes":1680584,...}
HTTP 200
```

`operator_id` (`0423cbd6…`, gate2_operator) **khác** `reviewer_id` (`7807e84b…`,
admin) — chốt chặn tự duyệt được thoả mãn bằng hai tài khoản thật, không phải
bằng cách bật `ALLOW_SELF_REVIEW`.

Ghi chú thật: `POST .../review` với `decision:"approved"` bị từ chối 422
(`Input should be 'approve' or 'reject'`). Hai endpoint duyệt dùng **hai bộ từ
vựng khác nhau** — `/labeling/labels` nhận `approved`/`rejected`, còn
`/demos/{id}/review` nhận `approve`/`reject`. Không phải lỗi chặn luồng, nhưng
là chỗ dễ vấp, nên ghi lại đây và trong sample queries của README.

Build dataset:

```console
$ curl -X POST http://localhost:8000/api/v1/datasets \
    -d '{"name":"gate2_demo_dataset","task_names":["lift_cube"],
         "include_failures":false,"overwrite":true}'

{"id":"277e7403-111a-4422-ab2c-7fcfc3373e7b","name":"gate2_demo_dataset",
 "task_names":["lift_cube"],"include_failures":false,"status":"building",
 "num_episodes":0,"num_frames":0,"size_bytes":null,
 "created_at":"2026-08-16T10:04:00.800799"}
HTTP 202
```

Sau ~5 giây:

```json
{
    "id": "277e7403-111a-4422-ab2c-7fcfc3373e7b",
    "name": "gate2_demo_dataset",
    "task_names": ["lift_cube"],
    "include_failures": false,
    "status": "ready",
    "num_episodes": 1,
    "num_frames": 370,
    "size_bytes": 593713,
    "error_message": null,
    "episodes": [
        {"id": "lift_cube_1f89f148", "task_name": "lift_cube",
         "status": "approved", "outcome": "success",
         "reviewer_id": "7807e84b-4bf3-4891-a714-b2632f97ec69",
         "reviewed_at": "2026-08-16T10:03:53.041711",
         "num_frames": 370, "duration_s": 6.15, ...}
    ]
}
```

Artefact trên đĩa:

```console
$ ls -la data/datasets/ | grep 277e7403
-rw-r--r-- 1 buitu 197609 593713 Aug 16 17:04 277e7403-111a-4422-ab2c-7fcfc3373e7b.zip

path:   data\datasets\277e7403-111a-4422-ab2c-7fcfc3373e7b.zip
size:   593713 bytes
sha256: 9f8a56d9de078a5da0766bbb8092bce29e55b2343041d98e1582752e3f8967aa
```

Nội dung zip:

```
gate2_demo_dataset/episodes/lift_cube_1f89f148/front.mp4  252670 bytes
gate2_demo_dataset/episodes/lift_cube_1f89f148/wrist.mp4  339048 bytes
gate2_demo_dataset/episodes/lift_cube_1f89f148/meta.json  298 bytes
gate2_demo_dataset/meta.json  979 bytes
```

Manifest `gate2_demo_dataset/meta.json` trong zip:

```json
{
  "schema_version": 1,
  "created_at": "2026-08-16T10:04:00.839535+00:00",
  "format": "raw",
  "name": "gate2_demo_dataset",
  "task_names": ["lift_cube"],
  "include_failures": false,
  "num_episodes": 1,
  "episodes": [
    {
      "episode_id": "lift_cube_1f89f148",
      "files": {
        "front.mp4": "64d02d1bcad38be11bb6be459819401b63f209aa8ed598901d40581d22f9699e",
        "wrist.mp4": "41fa9c88a312ad6eb50c0dfdbc58fda843cfd107f5881f122e173e4bc5b70511"
      },
      "task_name": "lift_cube", "outcome": "success",
      "note": "Gate2 manual test approval",
      "trim_start_s": 0.0, "trim_end_s": 6.15,
      "fps": 60.0, "duration_s": 6.15, "num_frames": 370,
      "operator": "gate2_operator", "reviewer": "admin",
      "created_at": "2026-08-16T10:01:07.401258"
    }
  ],
  "warnings": [],
  "note": "Snapshot dữ liệu tại thời điểm tạo dataset — không cập nhật khi demo nguồn đổi sau đó."
}
```

Manifest ghi sha256 **của từng file video**, không chỉ của cả gói — kiểm được
từng phần.

Tải về qua API và đối chiếu:

```console
$ curl -o ds.zip http://localhost:8000/api/v1/datasets/277e7403-.../download \
    -H "Authorization: Bearer $ADMIN"
HTTP 200  size=593713 bytes  type=application/zip  time=0.221309s

downloaded size:   593713
downloaded sha256: 9f8a56d9de078a5da0766bbb8092bce29e55b2343041d98e1582752e3f8967aa
```

sha256 của bản tải về **trùng khít** bản trên đĩa — đường download không làm
hỏng byte nào.

**Đạt.**

---

## Tổng kết

| # | Test case | Kết quả |
|---|---|---|
| 1 | Đăng nhập | **Đạt** — JWT hợp lệ, 2/2 case âm trả 401 đúng |
| 2 | Thu demo bằng điều khiển tay | **Đạt** — 3 camera có ảnh thật, episode 370 bước trên đĩa |
| 3 | Sinh data scripted ToolHang | **Đạt** — 45 s, `stage1=True stage2=True`, HDF5 2.57 MB + mp4 1.70 MB |
| 4 | Auto-label | **Đạt** — score 1.0 nhưng nhãn `review`, khớp luật ToolHang |
| 5 | Duyệt và đóng gói | **Đạt** — zip 593 713 B, sha256 khớp giữa đĩa và bản tải về |

`pytest`: **223 passed, 39 skipped** — không lệch so với baseline.

## Khoảng trống đã biết

Ghi ra để người chấm không phải tự đoán:

1. **`ffmpeg`/`ffprobe` không có trên máy chạy test.** Backend cảnh báo ngay lúc
   khởi động. Video teleop và video scripted vẫn ghi được vì đi qua
   `imageio-ffmpeg` bundle trong venv. Nhánh **upload demo từ file ngoài** (và
   thumbnail của nó) chưa được kiểm trong năm case này — nó cần binary hệ thống.
   README đã liệt kê ffmpeg là bắt buộc; đây là lệch giữa yêu cầu và máy chạy,
   không phải lỗi code.

2. **`task_success: false` ở case 2.** Điều khiển tay bằng script mù, không có
   phản hồi thị giác, nên không kẹp trúng khối. Luồng kỹ thuật đúng hết; chỉ là
   thao tác vụng. Một người điều khiển thật nhìn màn hình sẽ gắp được.

3. **Ảnh chụp là frame JPEG lấy thẳng từ luồng WebSocket**, không phải screenshot
   trình duyệt. Playwright không được cài và không tự cài thêm. Ảnh này chứng
   minh được nội dung camera thật, nhưng không chứng minh phần bố cục giao diện
   — phần đó xem `demo_mvp.mp4`.

5. **Case 5 đóng gói 1 episode.** Đủ để chứng minh đường đóng gói (zip, manifest,
   sha256, download) nhưng chưa phải phép thử tải nặng.
