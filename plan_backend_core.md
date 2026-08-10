# Kế hoạch phát triển Backend (Core) — TeleCollect

> Phạm vi rút gọn từ `plan_backend.md`: chỉ làm các chức năng nền tảng kiểu
> "web thương mại điện tử cơ bản" (đăng nhập/đăng ký, danh mục, xem/tua video, duyệt nội dung, tải file).
> **Không làm** phần liên quan robot thật: Teleoperation (điều khiển realtime qua MuJoCo/ROS2),
> Export LeRobot/RLDS/DVC, Training/Eval (PyTorch). Lý do: các phần đó cần thu thập dữ liệu robot thật,
> chưa phải ưu tiên ở giai đoạn này.
>
> Tài liệu phân tích, chưa động vào code. Dùng để duyệt trước khi bắt tay code.

LƯU Ý: BACKEND DÙNG PYTHON 3.11.x (bắt buộc)

LƯU Ý: `ffmpeg`/`ffprobe` là **dependency bắt buộc ở tầng hệ thống** (không phải optional) — dùng để lấy `duration_s`/`fps` lúc upload, sinh thumbnail, và `scripts/seed_demos.py` dùng nguồn `testsrc` của ffmpeg để tạo video mẫu. App phải **kiểm tra sự tồn tại của `ffprobe`/`ffmpeg` lúc khởi động** (ví dụ trong `lifespan`), nếu thiếu thì log cảnh báo rõ ràng ngay từ đầu thay vì để `POST /demos/upload` fail với lỗi khó hiểu giữa chừng. Cần ghi thêm vào README hướng dẫn cài đặt.

---

## 1. Phạm vi giữ lại vs. loại bỏ

| Module (theo `plan_backend.md`) | Trong bản Core? | Ghi chú |
| --- | --- | --- |
| 0. Nền tảng dữ liệu (DB models) | ✅ Giữ | Chỉ tạo bảng cần cho các module dưới: `users`, `episodes` (bảng lưu demo video, giữ tên `episodes` cho khớp docstring gốc trong `src/models/db.py` và khớp luôn phần teleop sau này — API route vẫn là `/demos`), `datasets`, `dataset_episodes`. Bỏ `training_jobs`. |
| 1. Auth (login/register/refresh/me/quản lý user) | ✅ Giữ | Đầy đủ như bản gốc, bao gồm `UserRole.ADMIN`. |
| 2. Tasks (danh mục nhiệm vụ) | ✅ Giữ | Vẫn cần vì Demos gắn với 1 task; coi task như "danh mục sản phẩm". |
| 3. Demos / Review (xem, tua, trim, gắn nhãn, duyệt) | ✅ Giữ (điều chỉnh nguồn dữ liệu) | Video **không** sinh ra từ Teleop nữa — dùng upload thủ công (xem mục 3). |
| 4. Teleoperation (điều khiển robot realtime qua WebSocket) | ❌ Bỏ | Cần MuJoCo/ROS2, control loop 30Hz, đo latency p95 — thuộc phạm vi robot thật, để dành giai đoạn sau. |
| 5. Datasets/Export | ⚠️ Giữ một phần | CRUD list/detail/download; tạo dataset thật bằng cách gom demo `approved` (xem `POST /datasets` ở mục 2.4). **Bỏ** logic export thật sang LeRobot/RLDS và tích hợp DVC. |
| 6. Training/Eval | ❌ Bỏ | Cần PyTorch + GPU + sim để evaluate — thuộc phạm vi robot thật. |
| 7. Tích hợp Frontend thật | ✅ Giữ (thu nhỏ) | Chỉ áp dụng cho các API còn trong scope core. |

---

## 2. Chức năng cụ thể theo từng module (bản Core)

### 2.1 Auth & Users — có mở rộng so với bản gốc (register, ma trận quyền)

- `POST /api/v1/auth/register` — tạo user mới (username, password, display_name); role mặc định `operator` (không cho tự chọn role admin/reviewer lúc đăng ký — đổi role là việc của admin qua `PATCH /users/{id}`).
- `POST /api/v1/auth/login` — xác thực thật (hash password, so khớp DB), không tự tạo user khi login.
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`
- `PATCH /api/v1/auth/me/password` — user tự đổi mật khẩu (old_password, new_password).
- `GET /api/v1/users`, `POST /api/v1/users`, `PATCH /api/v1/users/{id}` — quản lý user (chỉ admin).
- `DELETE /api/v1/users/{id}` — **soft delete** (`is_active=false`), không xoá cứng vì `episodes` còn tham chiếu tới `operator_id`/`reviewer_id`.
- Bổ sung `UserRole.ADMIN` vào `src/models/enums.py` (hiện chỉ có operator/reviewer).
- **Phân quyền có kế thừa:** `require_role("reviewer")` phải cho cả `reviewer` lẫn `admin` đi qua (admin luôn được coi là bao gồm quyền của reviewer/operator), không so khớp role tuyệt đối. Áp dụng cho mọi endpoint dùng `require_role`.
- **Ma trận quyền cụ thể (chốt ở đây để tránh CLI tự đoán):**

  | Hành động | operator | reviewer | admin |
  | --- | --- | --- | --- |
  | Upload demo | ✅ | ✅ | ✅ |
  | Label/trim/xoá demo **của chính mình** (so `operator_id`) | ✅ | ✅ | ✅ |
  | Label/trim/xoá demo **của người khác** | ❌ | ✅ | ✅ |
  | Review demo (approve/reject) của **người khác** | ❌ | ✅ | ✅ |
  | Review demo (approve/reject) do **chính mình** upload | ❌ | ❌ (403, trừ khi `allow_self_review=true`) | ❌ (403, trừ khi `allow_self_review=true`) |
  | Reopen demo (mọi demo, kể cả của chính mình) | ❌ | ✅ | ✅ |
  | Tạo/xoá dataset | ❌ | ✅ | ✅ |
  | CRUD user | ❌ | ❌ | ✅ |
  | CRUD task (POST/PATCH `/tasks`) | ❌ | ❌ | ✅ |

### 2.2 Tasks — giống bản gốc, đơn giản hoá

- `GET /api/v1/tasks` → danh mục task (giữ nguyên như "danh mục sản phẩm").
- `GET /api/v1/tasks/{name}` → chi tiết 1 task.
- `GET /api/v1/tasks/{name}/stats` → thống kê số demo theo task.
- `POST /api/v1/tasks`, `PATCH /api/v1/tasks/{name}` — tạo/sửa task, chỉ role admin (xem ma trận quyền ở mục 2.1).
- `action_dim` giữ trong schema nhưng không chỉ là metadata trống: dùng để validate chiều dữ liệu của `trajectory.json` (optional) khi upload demo, xem mục 2.3.
- **Nguồn dữ liệu ban đầu:** `scripts/seed_tasks.py` seed cứng 3 task (`pick_place`, `stack`, `push`) khi khởi tạo DB. Không có bước này thì bảng `tasks` rỗng và `POST /demos/upload` sẽ fail vì `task_name` không tồn tại — đây là lý do bắt buộc phải có seed, không chỉ là tiện ích test.

### 2.3 Demos / Review — điều chỉnh nguồn video

- **Khác biệt so với bản gốc:** vì không có Teleop, video demo được đưa vào hệ thống qua **upload thủ công** (multipart) thay vì tự động ghi từ control loop. Không nhận link/reference tới file có sẵn ngoài hệ thống.
  - `POST /api/v1/demos/upload` (multipart: task_name, `front` video file — **bắt buộc**, `wrist` video file — **optional**, `trajectory` file JSON — optional) → tạo `Demo` mới (`id` dạng UUID) với status `recorded`, lưu file vào storage cục bộ theo `episodes/<id>/{front.mp4,[wrist.mp4],[trajectory.json]}`. (Endpoint mới, cần thiết kế thêm — không có trong docstring gốc.)
  - **`duration`/`num_frames`/`fps` không nhận từ client** — client gửi lên không đáng tin (dễ sai lệch với file thật), trong khi các giá trị này là căn cứ để validate trim ở bước sau. Server tự chạy **`ffprobe`** trên file `front.mp4` (luôn có, vì bắt buộc) vừa lưu để lấy `duration_s`, `fps`, suy ra `num_frames`, ghi vào DB — không đụng tới `wrist.mp4` cho việc này.
  - **Guardrail bắt buộc lúc code (không hoãn):**
    - Ghi file theo **chunk** (không load hết vào RAM), đếm byte trong lúc ghi, vượt giới hạn thì huỷ và trả **413 Payload Too Large**.
    - Giới hạn mặc định qua config (`settings.max_upload_mb`, ví dụ 200MB/file), có thể chỉnh qua `.env`.
    - **Whitelist đuôi file/content-type:** video chỉ nhận `.mp4` (`video/mp4`); `trajectory` chỉ nhận `.json` (`application/json`).
    - **Tên file trên storage dùng UUID** (không dùng tên gốc người dùng upload) để tránh path traversal/đụng tên.
    - Nếu có `trajectory.json`, validate chiều dữ liệu action khớp `task.action_dim` trước khi chấp nhận — sai thì trả 422.
  - Sau khi lưu, sinh **thumbnail** (`GET /api/v1/demos/{id}/thumbnail` → JPEG frame đầu của `front.mp4`, tạo bằng `ffmpeg` ngay lúc upload, không tạo lazy lúc request).
- `GET /api/v1/demos` — danh sách, lọc theo task/status/label/operator, phân trang.
- `GET /api/v1/demos/{id}` — chi tiết.
- `GET /api/v1/demos/{id}/thumbnail` — trả JPEG frame đầu (sinh sẵn lúc upload).
- `GET /api/v1/demos/{id}/playback` — stream video thật, **đặc tả Range đầy đủ** (không chỉ 3 header, thiếu 1 trong các điểm dưới là hỏng phần tua video):
  - Có `Range` hợp lệ → trả `206 Partial Content` kèm `Content-Range`, `Accept-Ranges: bytes`.
  - Có `Range` nhưng sai định dạng hoặc vượt kích thước file → trả **`416 Range Not Satisfiable`**.
  - Không có `Range` → trả `200` kèm **`Accept-Ranges: bytes`** (thiếu header này thì trình duyệt không biết là có thể tua).
  - `StreamingResponse` đọc file theo **chunk ~1MB**, tuyệt đối không đọc cả file vào RAM (video có thể tới `max_upload_mb`).
  - Đường dẫn file phải **resolve tuyệt đối và kiểm tra nằm trong `settings.storage_dir`** trước khi mở — chặn path traversal qua `id`/tên file.
- `PATCH /api/v1/demos/{id}/trim` (`trim_start_s`, `trim_end_s` — đơn vị **giây**, kiểu float; server validate `0 <= trim_start_s < trim_end_s <= duration_s`, sai thì `422`) — cắt bớt đầu/cuối, chỉ ghi metadata, không đụng file gốc. Chốt dùng thời gian (giây) thay vì step/frame index vì bản Core không có control loop sinh ra frame index thật. Tên field `trim_start_s`/`trim_end_s` dùng thống nhất ở request, DB và `meta.json` trong dataset (xem mục 4).
- `PATCH /api/v1/demos/{id}/label` (outcome, note).
- `POST /api/v1/demos/{id}/review` (decision, note) — yêu cầu role reviewer (admin cũng đi qua nhờ kế thừa role, xem mục 2.1). Hợp lệ từ `recorded` hoặc `labeled` (xem quy tắc "nới" ở phần chuyển trạng thái bên dưới); approve khi chưa có label sẽ tự gán `outcome=success`. **Chặn tự duyệt:** nếu `episode.operator_id == current_user.id` → `403` (đảm bảo mọi demo đã duyệt được người khác thẩm định), trừ khi `settings.allow_self_review=true` (mặc định `false`). Không áp dụng cho reopen.
- `POST /api/v1/demos/{id}/reopen` — mở lại demo đã duyệt.
- `DELETE /api/v1/demos/{id}` — nếu demo đang được tham chiếu trong `dataset_episodes` (đã nằm trong 1+ dataset), chỉ **xoá row `dataset_episodes` tương ứng**, **không đụng** tới file zip đã đóng gói của dataset đó (zip là bản snapshot độc lập, đã copy nguyên file video vào bên trong lúc build). Dataset zip là snapshot tại thời điểm tạo, không phản ánh trạng thái hiện tại của DB — ghi rõ điều này trong `meta.json` cấp dataset (mục 4) để không ai hiểu nhầm là link động.
- `GET /api/v1/demos/summary` — bảng tổng hợp (total, by_status, by_label, by_task, success_rate, approval_rate...).
- **Bỏ:** endpoint `GET /demos/{id}/trajectory` (biểu đồ state/action/ee_pose theo từng bước) — không có UI/consumer nào trong bản Core cần đọc lại trajectory, nên không code endpoint riêng. Tuy nhiên **file `trajectory.json` vẫn được nhận lúc upload (optional)** và đóng gói vào dataset — không phải file chết, vì nó dùng để validate `action_dim` lúc upload và giữ chỗ cho giai đoạn robot sau này đọc lại trực tiếp từ file trong dataset zip.

#### Quy tắc chuyển trạng thái (`DemoStatus`) — bắt buộc validate ở service layer

```
recorded → labeled → approved | rejected
recorded ────────→ approved | rejected   (review thẳng, xem quy tắc "nới" bên dưới)
approved | rejected → recorded   (chỉ qua "reopen")
```

- Sai transition (ví dụ reopen một demo đang `recorded`) → trả **`409 Conflict`**, không âm thầm cho qua.
- `label` chỉ hợp lệ khi status đang `recorded` hoặc `labeled`.
- **`review` (approve/reject) — chốt "nới":** hợp lệ khi status đang `recorded` **hoặc** `labeled` (không bắt buộc phải label trước). Nếu approve một demo chưa có `label` (đang `recorded`), server **tự gán `outcome=success`** trước khi chuyển sang `approved` — đúng hành vi bản gốc/frontend demo. Lý do chọn nới thay vì chặt: nhóm vận hành ít người, review thường do cùng một người thao tác nên nếu bắt buộc phải qua bước label riêng rồi mới review, demo dễ bị kẹt vĩnh viễn khi thao tác viên quên bước label.
- `reopen` chỉ hợp lệ khi status đang `approved` hoặc `rejected`.

### 2.4 Datasets — chỉ phần CRUD/download

- `GET /api/v1/datasets` — danh sách dataset đã "đóng gói" (gồm cả đang `building`).
- `GET /api/v1/datasets/{id}` — chi tiết, gồm `status` (`building`/`ready`/`failed`).
- `POST /api/v1/datasets` (name, task_names, include_failures, overwrite) → tạo record `Dataset` với status `building` bằng cách **gom danh sách demo `approved`** khớp `task_names` (+ lọc theo `include_failures`), trả về ngay; job đóng gói zip chạy nền qua **BackgroundTasks** theo cấu trúc đã chốt ở mục 4 (`<name>/meta.json` + `episodes/<id>/{front.mp4,wrist.mp4,trajectory.json,meta.json}`, `ZIP_STORED`, không re-encode theo trim). Khi xong chuyển status sang `ready` (hoặc `failed` nếu lỗi). Chỉ role reviewer/admin (xem ma trận quyền mục 2.1).
  - **Ngữ nghĩa `overwrite`:** trùng `name` với dataset đã có + `overwrite=false` (mặc định) → trả **`409 Conflict`**, không tạo. Trùng `name` + `overwrite=true` → **xoá dataset cũ** (cả record DB lẫn file zip đã đóng gói trên storage) rồi tạo lại từ đầu với status `building`.
- `GET /api/v1/datasets/{id}/download` — tải file zip đã đóng gói (chỉ khả dụng khi status `ready`), **hỗ trợ Range như `/demos/{id}/playback`** (206/416/chunk/path traversal) — file zip thường lớn hơn cả video, cần resume khi tải dở bị ngắt.
- `DELETE /api/v1/datasets/{id}` — chỉ role reviewer/admin.
- **Bỏ:** `POST /datasets/{id}/export` (format LeRobot/RLDS), `GET /datasets/dvc/status` — cần tích hợp DVC/format chuẩn robot học máy, để dành giai đoạn sau.

---

## 3. Lộ trình phát triển (Core) — làm xong module, test kỹ, mới sang module tiếp

| Bước | Module | Nội dung chính | Vì sao ưu tiên trước |
| --- | --- | --- | --- |
| 0 | **Nền tảng dữ liệu** | `src/models/db.py`: SQLAlchemy models rút gọn (`users`, `episodes`, `datasets`, `dataset_episodes`), `get_engine/get_session/init_db`. | Mọi module sau cần DB layer chạy được trước. |
| 1 | **Auth** | Hash password, register/login/refresh/me, `require_role` (có kế thừa role), bổ sung `UserRole.ADMIN`, CRUD user cho admin. | Mọi API khác cần xác thực/phân quyền. |
| 2 | **Tasks** | `GET /tasks`, `GET /tasks/{name}`, `GET /tasks/{name}/stats`, `POST/PATCH /tasks` (chỉ admin), kèm `scripts/seed_tasks.py` (3 task cứng). | Danh mục là input cho Demos, đơn giản, dễ test. Bắt buộc có seed trước khi sang bước 3, nếu không `task_name` không tồn tại và upload demo sẽ fail. |
| 3 | **Demos / Review** | Upload video thủ công (kèm guardrail), list/detail/playback (có Range), trim (giây), label, review, reopen, delete, summary. Kèm `scripts/seed_demos.py` để tạo dữ liệu mẫu (nhiều demo với đủ status/label/task khác nhau), phục vụ test filter/phân trang/summary mà không phải upload tay từng cái. | Lõi nghiệp vụ chính của bản Core — luồng "xem, tua, duyệt nội dung". |
| 4 | **Datasets (rút gọn)** | CRUD list/detail/download, gom demo `approved` thành gói tải về. | Phụ thuộc dữ liệu `approved` từ Demos, làm sau khi review flow có dữ liệu thật. |
| 5 | **Tích hợp Frontend thật** | Nối các API core với frontend thật, rà lại contract schema cho các phần đã làm. | Việc cuối cùng của giai đoạn Core. |

**Không đưa vào lộ trình giai đoạn này:** Teleoperation, Export LeRobot/RLDS/DVC, Training/Eval. Khi nào quyết định làm phần robot thật, quay lại dùng `plan_backend.md` (bước 4, 5 phần export thật, và bước 6) làm tài liệu tiếp theo.

---

## 4. Các điểm đã chốt

- **Upload video:** `POST /api/v1/demos/upload` nhận file qua **multipart** (mp4 upload trực tiếp). Bỏ hẳn phương án "link tới file có sẵn" — không nhận URL/reference tới storage ngoài. Mỗi demo gồm `front.mp4` (bắt buộc), `wrist.mp4` (optional) + `trajectory.json` (optional), để khớp cấu trúc episode ở mục dataset bên dưới. Guardrail bắt buộc code ngay (không hoãn): ghi theo chunk + đếm byte + 413 khi vượt `settings.max_upload_mb`, whitelist đuôi file/content-type, tên file lưu trên storage dùng UUID — chi tiết xem mục 2.3.
- **`Task.action_dim`/`max_steps`:** giữ nguyên field trong `TaskResponse` (không xoá khỏi schema), dù bản Core chưa có sim đứng sau — coi là metadata dự phòng cho giai đoạn robot sau này.
- **Cấu trúc gói dataset (zip + meta.json):**

  ```
  <name>/
    meta.json
    episodes/
      <id>/
        front.mp4
        wrist.mp4        (nếu demo có upload wrist)
        trajectory.json   (nếu demo có upload trajectory)
        meta.json
  ```

  - `meta.json` (cấp dataset) chứa: `schema_version`, `created_at`, `format`, `task_names`, `include_failures`, danh sách episode kèm `sha256` từng file. Ghi rõ trong `meta.json` rằng đây là **snapshot tại thời điểm tạo dataset** — xoá demo gốc sau đó (`DELETE /demos/{id}`) không ảnh hưởng tới nội dung file zip đã đóng gói.
  - `meta.json` (cấp episode) chứa metadata riêng của demo đó (task, label, `trim_start_s`/`trim_end_s`, operator, reviewer...). Thống nhất tên field trim là `trim_start_s`/`trim_end_s` ở cả DB, API (`TrimRequest`) và `meta.json` — không dùng tên khác nhau giữa các lớp.
  - Dùng **ZIP_STORED** (không nén thêm) vì mp4 đã nén sẵn, deflate không có lợi.
  - **Không re-encode** video theo `trim_start_s`/`trim_end_s` khi đóng gói — chỉ ghi 2 giá trị đó vào `meta.json` của episode, phát lại (playback) tự áp dụng trim ở phía client/player.
  - Việc nén zip chạy trong **BackgroundTasks** (không block request tạo dataset); `Dataset` có `status`: `building` → `ready` / `failed`.
  - `trajectory.json` trong mỗi episode: **optional nhưng không phải file chết** — nếu người upload có sẵn (ví dụ ghi tay từ thiết bị khác), file được nhận lúc upload, validate chiều dữ liệu theo `task.action_dim`, và đóng gói nguyên vẹn vào dataset. Nếu demo không có file này, episode tương ứng trong zip chỉ thiếu `trajectory.json`, không lỗi.

- **Role có kế thừa:** `admin` tự động thoả mọi `require_role(...)` thấp hơn (`reviewer`, `operator`) — không so khớp tuyệt đối. 3 role: `operator`, `reviewer`, `admin` (bổ sung `ADMIN` vào `UserRole` enum).

---

## 5. Schema Pydantic cần sửa (`src/models/schemas.py`)

Schema gốc lẫn nhiều field của control loop (Teleop) — bản Core không dùng, cần dọn để CLI không tự đoán khi code.

- **Bỏ khỏi `DemoResponse`/`DemoDetailResponse`:** `latency_p50_ms`, `latency_p95_ms`, `control_jitter_ms`, `dropped_frames`, `auto_success`, `auto_success_frame` — toàn số liệu control loop, không có nguồn sinh ra trong bản Core.
- **Thêm vào `DemoResponse`/`DemoDetailResponse`:** `fps`, `num_frames`, `duration_s`, `size_bytes`, `trim_start_s`, `trim_end_s`, `note`, `reviewer_id`, `reviewed_at`, `has_trajectory`. **Không thêm `seed`** — bản Core không có sim nên không có nguồn nào sinh ra giá trị này (khác `fps`/`duration_s`/`num_frames` vốn lấy được từ `ffprobe`); giữ lại sẽ thành field chết y hệt vấn đề `trajectory` đã bàn. Nếu sau này cần, phải cho `POST /demos/upload` nhận `seed` như metadata optional trước.
- **`TrimRequest`:** đổi field sang `trim_start_s`/`trim_end_s`, kiểu `float`, bỏ field kiểu step/frame nếu đang có.
- **Thêm mới:** `RegisterRequest` (username, password, display_name), `DemoUploadResponse`, `DemoSummaryResponse`, `PaginatedResponse[T]` (generic, dùng chung cho list demos/datasets có phân trang), `DatasetCreateRequest` (name, task_names, include_failures, overwrite).
- **Giữ nguyên, không xoá** dù bản Core không dùng: `SessionResponse`, `LoopStatsResponse`, `TrainingJobResponse`, `EvalResultResponse` — để dành cho giai đoạn robot thật sau này, tránh phải viết lại từ đầu.

---

## 6. Chuẩn test

- Dùng `pytest` + `httpx.AsyncClient`, DB test chạy trên **SQLite in-memory** (không đụng file DB thật).
- Fixture sẵn: 3 user (1 operator, 1 reviewer, 1 admin) + 3 task (`pick_place`, `stack`, `push`) — dựng lại đúng dữ liệu mà `scripts/seed_tasks.py`/`seed_demos.py` tạo ra ở môi trường thật.
- Mỗi endpoint tối thiểu: **1 happy path + 1 case lỗi (4xx phù hợp) + 1 case phân quyền** (đúng ma trận quyền ở mục 2.1).
- **Bắt buộc có test cho Range** trên `/demos/{id}/playback`: request `Range: bytes=0-1023` phải trả đúng 1024 byte kèm `206`; request range vượt kích thước file phải trả `416`.
- Test riêng cho quy tắc chuyển trạng thái (mục 2.3): mọi transition sai (label demo `approved`, reopen demo `recorded`...) phải trả `409`.

---

## 7. Ghi chú

- Theo `CLAUDE.md` của dự án: chỉ phụ trách Backend, không sửa code Frontend.
- Tài liệu này là bản rút gọn của `plan_backend.md` — giữ nguyên các quyết định thiết kế (schema, role, DB) cho các phần trùng lặp, chỉ khác ở việc loại bỏ pha con phụ thuộc robot thật và đổi nguồn video từ Teleop sang upload thủ công.
