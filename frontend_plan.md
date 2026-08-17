# Kế hoạch nối frontend với backend thật

> Cập nhật: 2026-08-10
> Nguyên tắc đã chốt: **nối API thật trước, làm đẹp giao diện sau.** Mục 3 (nối dây) là phần
> chính của tài liệu này; mục 4/5 (giao diện) là phụ, xếp sau và có đánh dấu rõ phần nào phải
> đợi nối dây xong mới được đụng vào.
>
> Phạm vi đọc: toàn bộ `frontend/` (đọc trực tiếp từng file, không suy đoán), cộng
> `detail_backend_withoutRobot.md` (mục 6 — bảng endpoint; mục 5 — vòng đời dữ liệu) và
> `src/models/schemas.py` (schema Pydantic thật — chính xác hơn tài liệu vì tài liệu có thể lệch
> theo thời gian), và `plan_backend.md` mục 3 (gap đã ghi nhận từ trước).
> KHÔNG có dòng code nào bị sửa trong lượt làm việc viết tài liệu này.

---

## 1. Kiểm kê hiện trạng

### 1.1 Phiên bản (đọc `frontend/package.json`, đã `npm run typecheck` để xác nhận cài đặt thật khớp lockfile)

| Gói                      | Version khai báo | Version cài thật (`node_modules/next/package.json`)     |
| ------------------------- | ----------------- | ----------------------------------------------------------- |
| `next`                  | `^16.3.0`       | **16.3.0**                                            |
| `react` / `react-dom` | `^19.2.8`       | (chưa kiểm tra riêng, cùng lockfile nên coi là khớp) |
| `typescript`            | `^5.8.3`        | —                                                          |
| `tailwindcss`           | `^4.1.13`       | —                                                          |
| `@tailwindcss/postcss`  | `^4.1.13`       | —                                                          |
| `postcss`               | `^8.5.26`       | —                                                          |
| `sharp`                 | `^0.35.3`       | —                                                          |

Ghi chú quan trọng: prompt gốc giả định `package.json` "đang có thay đổi chưa commit" (Next
15.5.4 → ^16.3.0, thêm `postcss`+`sharp`). Đã kiểm tra bằng `git status frontend/` và
`git diff frontend/package.json`: **không có gì chưa commit** — `frontend/package.json` ở trạng
thái Next `^16.3.0` đã nằm trong commit `5c2d6b4` (`feat: add demo frontend`) và giữ nguyên tới
`HEAD`. Giả định "chưa commit" trong yêu cầu là thông tin cũ/sai lệch — báo lại ở đây thay vì
lặp lại nó.

**App Router hay Pages Router?** App Router — bằng chứng: cấu trúc `frontend/app/**/page.tsx`
(`app/page.tsx`, `app/login/page.tsx`, `app/review/page.tsx`, `app/review/[id]/page.tsx`,
`app/datasets/page.tsx`, `app/training/page.tsx`, `app/teleop/page.tsx`, `app/admin/page.tsx`)
và `app/layout.tsx` khai báo `RootLayout`. Không có thư mục `pages/`.

**Next 16 breaking changes có ảnh hưởng tới việc nối API không?**
`npm run typecheck` (`tsc --noEmit`) chạy sạch trên toàn bộ `app/`, `components/`, `lib/` với
Next 16.3.0 đã cài — nghĩa là các type của Next 16 (kể cả nếu route handler/`params` async đã
đổi kiểu) không mâu thuẫn với code hiện tại, **vì code hiện tại không dùng bất kỳ Server
Component, Route Handler, hay `params`/`searchParams` phía server nào** — mọi trang đều
`"use client"` (xem cột "file" ở mục 2) và đọc id động qua hook `useParams()` phía client
(`app/review/[id]/page.tsx:22`), không phải qua prop `params` của Server Component. Đây chính là
điểm mà Next 13→15→16 đổi nhiều nhất (params/searchParams thành `Promise`) — nhưng vì app này
100% client-side, thay đổi đó **không áp dụng**. Kết luận: chưa thấy breaking change nào của
Next 16 cản trở việc nối API thật. Nếu về sau chuyển bớt trang sang Server Component để fetch
dữ liệu phía server (không nằm trong kế hoạch hiện tại), cần đọc lại điểm này.
Không tìm thấy exports/`unstable_` API nào của Next trong code để phải rà thêm — **cần kiểm tra
thêm** nếu Next 16 đổi hành vi `next dev`/`next build` (turbopack mặc định...) khi thực sự chạy
`npm run build`, việc đó ngoài phạm vi đọc tĩnh của tài liệu này.

### 1.2 Styling

Tailwind **v4**, cấu hình kiểu CSS-first: `frontend/app/globals.css:1` mở đầu bằng
`@import "tailwindcss";` rồi khai báo token màu trong khối `@theme { ... }` (`globals.css:3-23`)
— đúng cơ chế Tailwind v4, **không phải** `tailwind.config.js` kiểu v3. Xác nhận thêm: repo
không có file `tailwind.config.js`/`.ts` nào (`ls frontend` không liệt kê). PostCSS chỉ có 1
plugin: `@tailwindcss/postcss` (`frontend/postcss.config.mjs:1-5`).

Token tự định nghĩa trong `@theme`: `--color-ink-*` (nền/chữ), `--color-accent-*`,
`--color-ok-*`, `--color-warn-*`, `--color-bad-*` — dùng xuyên suốt qua class Tailwind kiểu
`bg-ink-900`, `text-accent-400`... Không có dark/light mode switch — chỉ một bảng màu tối duy
nhất, hardcode trong `body` (`globals.css:30-40`, gradient nền cố định).

### 1.3 Thư viện UI / icon / chart / animation

**Không có** shadcn/ui, Radix, Headless UI, hay bất kỳ icon set nào (không có `lucide-react`,
`@heroicons`, `react-icons`... trong `dependencies`/`devDependencies` của `package.json`).
Toàn bộ UI component tự viết tay trong `frontend/components/ui.tsx` (`Card`, `Button`, `Badge`,
`Stat`, `Field`, `Input`, `Select`, `TextArea`, `Alert`, `Empty`, `Sparkline`). `Sparkline`
(`ui.tsx:184-262`) là **line chart tự viết bằng SVG thô**, không dùng thư viện chart nào (không
Chart.js/Recharts/visx/D3). Animation: không có thư viện animation nào — chỉ có class Tailwind
có sẵn (`transition-colors` ở `Nav.tsx:43`, `Button` trong `ui.tsx:63`) và một chỗ dùng
`animate-pulse` (`TeleopConsole.tsx:172`, chấm đỏ "REC").

Toàn bộ dependency đã liệt kê đủ ở bảng mục 1.1 — không sót cái nào. `sharp` là dependency runtime
duy nhất không thấy được gọi trực tiếp ở đâu trong `app/`/`components/`/`lib/` (đã `grep -rn "sharp"` — không có kết quả ngoài `package.json`/lockfile); Next tự dùng `sharp` nội bộ cho
`next/image` khi có, nhưng code hiện tại **không dùng `next/image`** ở đâu cả (đã grep, không
match). **Cần kiểm tra thêm**: có thể `sharp` chỉ là dependency cài sẵn phòng khi thêm
`next/image` sau này, không có tác dụng gì ở trạng thái hiện tại — không nên coi đây là dấu hiệu
có tính năng ẩn.

---

## 2. Bản đồ màn hình

| Route            | File                         | Mục đích                                                                                 | Component chính                                            | Gọi hàm nào trong`lib/api.ts`                                                                                                     |
| ---------------- | ---------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `/login`       | `app/login/page.tsx`       | Đăng nhập (giả)                                                                         | `Card`, `Field`, `Input`, `Button` (từ `ui.tsx`) | `useAuth().login` → nội bộ gọi `api.login`                                                                                     |
| `/`            | `app/page.tsx`             | Tổng quan: thống kê, bảng theo task, demo gần đây, trạng thái hệ thống           | `Stat`, `Card`, `StatusBadge`                         | `api.summary`, `api.tasks`, `api.demos`, `api.health`                                                                          |
| `/teleop`      | `app/teleop/page.tsx`      | Điều khiển robot ảo, ghi demo mới                                                      | `TeleopConsole` (`components/TeleopConsole.tsx`)        | `api.tasks`; bản thân điều khiển đi qua `lib/teleop.ts` (`TeleopClient`), không qua `lib/api.ts`                        |
| `/review`      | `app/review/page.tsx`      | Hàng đợi review: lọc theo task/status/label, phân trang                                | `Card`, `Select`, `StatusBadge`                       | `api.demos`, `api.tasks`, `api.summary`                                                                                          |
| `/review/[id]` | `app/review/[id]/page.tsx` | Xem chi tiết 1 demo: playback 2 camera, trim, biểu đồ trajectory, duyệt/từ chối/xoá | `TrimTimeline`, `Sparkline`                             | `api.demo`, `api.trajectory`, `api.review`, `api.reopen`, `api.deleteDemo`                                                   |
| `/datasets`    | `app/datasets/page.tsx`    | Tạo export dataset (LeRobot/RLDS), xem danh sách export, ghi chú DVC                     | `Card`, `Field`, `Select`                             | `api.exports`, `api.summary`, `api.dvc`, `api.tasks`, `api.createExport`, `api.deleteExport`                               |
| `/training`    | `app/training/page.tsx`    | Tạo run huấn luyện, xem loss chart, chạy eval, xem video eval                           | `Sparkline`, `EvalVideos` (nội bộ file)               | `api.exports`, `api.runs`, `api.evals`, `api.tasks`, `api.createRun`, `api.createEval`, `api.runHistory`, `api.runLog` |
| `/admin`       | `app/admin/page.tsx`       | Quản lý user + role (chỉ admin)                                                          | `Card`, `Select`, `Input`                             | `api.users`, `api.createUser`, `api.updateUser`                                                                                  |

Không có route nào khác — `app/` chỉ có 8 thư mục `page.tsx` liệt kê ở trên cộng `layout.tsx`
(không phải page). `Nav.tsx:8-15` xác nhận đúng 6 mục điều hướng khớp 6/8 route trên (`/login`
không hiện trong nav vì `Nav` return `null` khi chưa đăng nhập — `Nav.tsx:20`; `/review/[id]`
không có mục riêng vì nó nằm dưới `/review`).

---

## 3. KẾ HOẠCH NỐI API — phần quan trọng nhất, làm trước

### 3.1 Bảng ánh xạ hàm

Nguồn đối chiếu: từng hàm export trong `api` object của `frontend/lib/api.ts:200-540`, so với
bảng endpoint thật ở `detail_backend_withoutRobot.md` mục 6.1/6.2/6.3/6.4 và schema thật trong
`src/models/schemas.py`.

| Hàm`api.*`                       | Shape trả về hiện tại (demo)                                                                  | Endpoint backend thật                                                                                                                                                                                                                                     | Shape backend thật                                                             | Lệch chỗ nào                                                                                                                                                                                                                                                                                                                                     | Khó                           |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `login(username, password)`       | Nhận mọi mật khẩu, tự tạo user nếu chưa có, set`localStorage` token giả `demo:<id>` | `POST /api/v1/auth/login` (form OAuth2, không phải JSON)                                                                                                                                                                                               | `TokenResponse{access_token, refresh_token, token_type}`                      | Request phải đổi từ JSON sang`application/x-www-form-urlencoded` (OAuth2PasswordRequestForm); response không trả `User`, phải gọi thêm `/auth/me` sau khi có token; sai mật khẩu trả 401 thay vì tự tạo tài khoản                                                                                                           | Trung bình                    |
| `me()`                            | Đọc user từ token giả trong`db()`                                                           | `GET /api/v1/auth/me`                                                                                                                                                                                                                                    | `UserResponse{id, username, display_name, role, is_active, created_at}`       | Field khớp gần hết (xem 3.2 —`Role`/`role` khớp giá trị)                                                                                                                                                                                                                                                                                 | Thấp                          |
| `users()`                         | `[...db().users]`                                                                               | `GET /api/v1/users` (admin only)                                                                                                                                                                                                                         | `list[UserResponse]`                                                          | Không hỗ trợ filter`role`/`is_active` mà mock cũng không dùng — khớp                                                                                                                                                                                                                                                                   | Thấp                          |
| `createUser(body)`                | Push vào store, check trùng username                                                            | `POST /api/v1/users` (admin only)                                                                                                                                                                                                                        | `UserResponse` (201)                                                          | Body request đã khớp field (`username, password, display_name, role`)                                                                                                                                                                                                                                                                          | Thấp                          |
| `updateUser(id, body)`            | Sửa field trực tiếp trên object                                                               | `PATCH /api/v1/users/{id}` (admin only)                                                                                                                                                                                                                  | `UserResponse`                                                                | Backend không cho**tự đổi role/tự khoá chính mình** (400) — mock không mô phỏng giới hạn này, `admin/page.tsx:153` chỉ `disabled={item.id === user.id}` phía UI, cần xử lý 400 trả về khi race                                                                                                                      | Thấp                          |
| `tasks()`                         | `TASKS` hằng số tĩnh                                                                         | `GET /api/v1/tasks`                                                                                                                                                                                                                                      | `list[TaskResponse]`                                                          | Lệch field nặng — xem 3.2                                                                                                                                                                                                                                                                                                                        | Trung bình                    |
| `health()`                        | Object giả`{version, control_hz, active_sessions, max_sessions, tasks, anonymize_faces}`       | **Không có endpoint tương ứng.** Backend Core không có `/health` trả shape này (chỉ có root health-check dùng cho Docker, không nằm dưới `/api/v1`, shape khác hẳn)                                                            | —                                                                              | Toàn bộ field đều không tồn tại phía backend Core (không có control loop, không có sessions)                                                                                                                                                                                                                                            | Không nối được — xem 3.7 |
| `demos(params)`                   | Lọc thủ công trên mảng, trả`{items, total, limit, offset}`                                | `GET /api/v1/demos`                                                                                                                                                                                                                                      | `PaginatedResponse[DemoResponse]{items, total, page, page_size, total_pages}` | **Shape phân trang khác hẳn** (`limit/offset` vs `page/page_size/total_pages`) — xem 3.6; filter `needs_review`/`label` không tồn tại ở backend (backend dùng `status`/`outcome`)                                                                                                                                        | Cao                            |
| `demo(id)`                        | 1 object từ store                                                                                | `GET /api/v1/demos/{id}`                                                                                                                                                                                                                                 | `DemoDetailResponse` (thêm `has_thumbnail`)                                | Field lệch nặng — xem 3.2                                                                                                                                                                                                                                                                                                                        | Cao                            |
| `summary()`                       | `summarise()` tự tính trên store, có field latency/median                                   | `GET /api/v1/demos/summary`                                                                                                                                                                                                                              | `DemoSummaryResponse`                                                         | Backend**không có** `median_latency_ms`/`p95_latency_ms` (không control loop); field tên khác nhau (`by_label`→`by_outcome`, `total_hours`→`total_duration_hours`, `by_task` là `dict[str,int]` phẳng ở backend còn mock là `dict[str, {total,success,approved,rejected}]` lồng)                               | Cao                            |
| `trajectory(id, stride)`          | Sinh trajectory giả có state/action/ee_pose/success/cmd_age_ms/rtt_ms/tick_dt_ms                | **Không có endpoint tương ứng.** Backend Core không lưu trajectory theo frame kiểu này (`has_trajectory` chỉ là cờ boolean có file `trajectory.json` do operator upload hay không, không có API đọc lại nội dung đã parse) | —                                                                              | Không nối được nguyên trạng — xem 3.7                                                                                                                                                                                                                                                                                                       | Không nối được            |
| `review(id, body)`                | Sửa`label/trim_start/trim_end/notes`, rồi nếu có `approve` thì set status                | **2 endpoint khác nhau ở backend:** `PATCH /demos/{id}/trim`, `PATCH /demos/{id}/label` là 2 call riêng; `POST /demos/{id}/review` chỉ nhận `{decision: "approve"\|"reject", note}`                                                     | `DemoResponse`                                                                | Hàm mock gộp 4 việc (trim + label + note + review) vào 1 call — backend tách 3 endpoint riêng, phải gọi tuần tự; ngoài ra**backend chặn tự duyệt demo của chính mình (403)** — mock không mô phỏng luật này                                                                                                           | Cao                            |
| `reopen(id)`                      | Set`status="recorded"`, xoá `reviewer_id/reviewed_at`                                        | `POST /api/v1/demos/{id}/reopen`                                                                                                                                                                                                                         | `DemoResponse`                                                                | Khớp ý nghĩa; nhưng đích đến khi reopen ở backend là`labeled` **hoặc** `recorded` tuỳ đã có `outcome` chưa (`demo_rules.apply_reopen`), không luôn về `recorded` như mock                                                                                                                                       | Thấp                          |
| `deleteDemo(id)`                  | Filter khỏi mảng                                                                                | `DELETE /api/v1/demos/{id}`                                                                                                                                                                                                                              | 204 No Content                                                                  | Khớp ý nghĩa; quyền sở hữu: backend chỉ cho chủ demo hoặc reviewer+ xoá (403 nếu operator xoá demo người khác) — mock không chặn                                                                                                                                                                                                  | Thấp                          |
| `exports()`                       | `[...db().exports]`                                                                             | `GET /api/v1/datasets`                                                                                                                                                                                                                                   | `PaginatedResponse[DatasetResponse]`                                          | Backend gọi là "dataset", không phải "export"; trả có phân trang, mock trả mảng trần; field lệch — xem 3.2                                                                                                                                                                                                                              | Trung bình                    |
| `createExport(body)`              | Tính`eligible` demo tại chỗ, tạo `DatasetExport` ngay, trả về đã "xong"               | `POST /api/v1/datasets`                                                                                                                                                                                                                                  | `DatasetResponse` với `status=building`, HTTP **202**                | Backend đóng gói**nền** (BackgroundTasks) — trả về ngay với `status=building`, KHÔNG có size/num_frames cuối cùng; phải poll `GET /datasets/{id}` tới khi `ready`/`failed`. Backend **không có field `format`** (`lerobot`/`rlds`) trong `DatasetCreateRequest` — không hỗ trợ chọn format qua API | Cao                            |
| `exportInfo(id)`                  | Tìm trong store                                                                                  | `GET /api/v1/datasets/{id}`                                                                                                                                                                                                                              | `DatasetDetailResponse`                                                       | Field lệch — xem 3.2; có thêm`episodes: DemoResponse[]` mà mock không có                                                                                                                                                                                                                                                                   | Trung bình                    |
| `deleteExport(id)`                | Filter khỏi mảng                                                                                | `DELETE /api/v1/datasets/{id}` (reviewer+)                                                                                                                                                                                                               | 204                                                                             | Khớp ý nghĩa, đổi quyền: mock cho phép admin xoá qua UI (`datasets/page.tsx:197`), backend chỉ cần reviewer+ (không cần admin) — UI đang **hẹp hơn** backend, không sai nhưng có thể nới lỏng                                                                                                                         | Thấp                          |
| `dvc()`                           | Object giả`{available: true, status: {...}}`                                                   | **Không có endpoint tương ứng.** Backend Core không tích hợp DVC                                                                                                                                                                             | —                                                                              | Không nối được — xem 3.7                                                                                                                                                                                                                                                                                                                      | Không nối được            |
| `runs()`                          | Mảng`TrainingRun` từ store                                                                    | **Không có endpoint tương ứng chạy thật.** `src/api/training.py` chỉ khai `router = APIRouter(prefix="/training", ...)`, không có route nào (`grep "@router\."` không ra kết quả nào trong file này)                           | —                                                                              | Router rỗng hoàn toàn — không phải "thiếu field", mà thiếu cả endpoint                                                                                                                                                                                                                                                                    | Không nối được — xem 3.7 |
| `run(id)`                         | 1 object                                                                                          | — (như trên)                                                                                                                                                                                                                                            | —                                                                              | —                                                                                                                                                                                                                                                                                                                                                  | Không nối được            |
| `runLog(id)` / `runHistory(id)` | Sinh log/loss-curve giả từ elapsed time                                                         | —                                                                                                                                                                                                                                                         | —                                                                              | —                                                                                                                                                                                                                                                                                                                                                  | Không nối được            |
| `createRun(body)`                 | Tạo`TrainingRun` "đang chạy", tiến độ suy từ thời gian trôi qua                        | `POST /training/jobs` **có trong docstring dự kiến** (`training.py` header comment) nhưng **chưa cài đặt**                                                                                                                         | —                                                                              | —                                                                                                                                                                                                                                                                                                                                                  | Không nối được            |
| `evals()` / `createEval(body)`  | Tương tự runs                                                                                  | — (router rỗng)                                                                                                                                                                                                                                          | —                                                                              | —                                                                                                                                                                                                                                                                                                                                                  | Không nối được            |

Tổng kết nhanh (đếm lại bằng `grep -nE "^  [a-zA-Z]+: async|^  async [a-zA-Z]+\(" lib/api.ts`,
object `api` có đúng **26 hàm**): **10/26 hàm** (`health`, `trajectory`, `dvc`, `runs`, `run`,
`runLog`, `runHistory`, `createRun`, `evals`, `createEval`) **không có endpoint thật nào ở
backend Core** để nối vào. **16/26 hàm còn lại** (`login`, `me`, `users`, `createUser`,
`updateUser`, `tasks`, `demos`, `demo`, `summary`, `review`, `reopen`, `deleteDemo`, `exports`,
`createExport`, `exportInfo`, `deleteExport`) đều có endpoint thật để nối nhưng hầu hết cần đổi
shape (mục 3.2) hoặc đổi luồng gọi (mục 3.3).

### 3.2 Field lệch tên

**Task** — frontend `Task` (`lib/api.ts:30-36`) vs `TaskResponse` (`src/models/schemas.py:106-116`):

| Frontend         | Backend          | Ghi chú                                                                                                       |
| ---------------- | ---------------- | -------------------------------------------------------------------------------------------------------------- |
| `id`           | `name`         | Backend dùng`name` làm khoá chính (string, pattern `^[a-z][a-z0-9_]{2,49}$`), không có `id` riêng |
| `title`        | *(không có)* | Backend không có tiêu đề hiển thị riêng — chỉ có`description`                                     |
| `instruction`  | `instruction`  | Khớp tên                                                                                                     |
| *(không có)* | `description`  | Backend có thêm field frontend không dùng                                                                  |
| `max_steps`    | `max_steps`    | Khớp tên                                                                                                     |
| `hints`        | `hints`        | Khớp tên                                                                                                     |
| *(không có)* | `action_dim`   | Backend bắt buộc field này (dùng validate`trajectory.json` khi upload) — frontend demo không có       |

Đề xuất: **viết adapter**, không đổi tên ở component — lý do xem giả thuyết 3.3. Adapter map
`name → id`, và **derive `title` từ `name`** thuần client-side (`pick_place` → "Pick Place") thay
vì dùng `description` (câu dài tiếng Việt, không phù hợp làm nhãn ngắn trong dropdown/badge) —
đã đối chiếu 3 task thật (`scripts/seed_tasks.py:26-50`) với 3 `id` frontend demo dùng
(`lib/demo-data.ts:71-102`) và khớp tuyệt đối, không cần hỏi thêm ai (kết luận đầy đủ ở mục 7 câu
3). Đây là quyết định đã chốt trong tài liệu này, không phải câu hỏi mở. `action_dim` **bỏ qua**
phía hiển thị (không dùng ở UI nào hiện tại).

**Demo** — frontend `Demo` (`lib/api.ts:38-63`) vs `DemoResponse`/`DemoDetailResponse`
(`schemas.py:176-211`):

| Frontend                                                                                                                              | Backend                                                                               | Ghi chú                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `task_id`                                                                                                                           | `task_name`                                                                         | Đổi tên đơn thuần                                                                                                                                                                                                                                                   |
| `operator_id`                                                                                                                       | `operator_id`                                                                       | Khớp                                                                                                                                                                                                                                                                     |
| `operator_name`                                                                                                                     | *(không có)*                                                                      | Backend không JOIN sẵn tên operator vào`DemoResponse` — chỉ có `operator_id`. Muốn hiện tên phải tự gọi thêm `GET /users` rồi map ở frontend, hoặc chấp nhận chỉ hiện id                                                                       |
| `status` (4 giá trị: `recording/recorded/approved/rejected`)                                                                    | `status` (**5 giá trị**: `recording/recorded/labeled/approved/rejected`)  | **Backend có thêm trạng thái `labeled` mà frontend không biết tới** — đây là lệch nghiêm trọng nhất trong toàn bộ enum, xem cảnh báo bên dưới                                                                                              |
| `label` (`success\|failure\|null`)                                                                                                  | `outcome` (`success\|failure\|null`)                                                | Đổi tên, giá trị khớp (`DemoOutcome`)                                                                                                                                                                                                                             |
| `trim_start`/`trim_end` (đơn vị **frame**, số nguyên)                                                                  | `trim_start_s`/`trim_end_s` (đơn vị **giây**, số thực)                | Không chỉ đổi tên —**đổi cả đơn vị**. Mọi phép tính dùng `trim_end - trim_start` như số frame (`review/[id]/page.tsx:176-178`, `TrimTimeline.tsx` toàn bộ) sẽ sai nếu nhận thẳng giây từ backend mà không nhân lại với `fps` |
| `review_notes`                                                                                                                      | `note`                                                                              | Đổi tên                                                                                                                                                                                                                                                                |
| `reviewer_id`/`reviewer_name`/`reviewed_at`                                                                                     | `reviewer_id`/`reviewed_at` (không có `reviewer_name`)                        | Thiếu tên hiển thị, như operator                                                                                                                                                                                                                                     |
| `seed`, `auto_success`, `auto_success_frame`, `latency_p50_ms`, `latency_p95_ms`, `control_jitter_ms`, `dropped_frames` | **Không tồn tại ở backend**                                                 | Toàn bộ nhóm field "chất lượng điều khiển" này chỉ có ý nghĩa khi có control loop thật (Teleop) — backend Core không có, nên các field này**vĩnh viễn null/absent** cho tới khi module Teleop được xây (không nằm trong Core)        |
| *(không có)*                                                                                                                      | `has_wrist`, `has_trajectory`, `has_thumbnail` (chỉ ở `DemoDetailResponse`) | Backend có cờ file đính kèm mà frontend demo không cần vì luôn giả định có đủ 2 camera                                                                                                                                                                    |

Cảnh báo riêng cho `status`: enum backend thật (`src/models/enums.py:27-43`) có
`RECORDING, RECORDED, LABELED, APPROVED, REJECTED`. Type `DemoStatus` phía frontend
(`lib/api.ts:17`) chỉ khai 4 giá trị, **thiếu `"labeled"`**. Nếu union type TypeScript giữ
nguyên khi đổi ruột `api.ts` sang gọi API thật mà không sửa type này, mọi demo backend trả về
`status: "labeled"` sẽ vừa gây lỗi kiểu (TS không nhận diện) vừa khiến `StatusBadge`
(`components/StatusBadge.tsx:7-11`) rơi vào nhánh mặc định "needs review" — **về mặt hiển thị
tình cờ chấp nhận được** (labeled cũng đang chờ duyệt) nhưng là trùng hợp, không phải xử lý có
chủ đích, và filter `statusFilter` ở `review/page.tsx:28,92-96` (chỉ có option
`recorded/approved/rejected`) sẽ không có cách lọc riêng "đã gắn nhãn, chưa duyệt" dù backend có
trạng thái đó.

**Dataset** — frontend `DatasetExport` (`lib/api.ts:102-114`) vs
`DatasetResponse`/`DatasetDetailResponse` (`schemas.py:300-322`):

| Frontend                                         | Backend                                      | Ghi chú                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| *(không có)*                                 | `id, name, task_names, include_failures`   | `task_names` là mảng — frontend demo `tasks: string[]` tên gần giống nhưng `include_failures`/`format` xử lý khác (xem dưới)                                                                                                                                                                                               |
| `format` (`lerobot\|rlds`, chọn lúc tạo)   | *(không có ở `DatasetCreateRequest`)* | Backend Core**không hỗ trợ chọn format** — chỉ đóng gói 1 kiểu zip cố định (`meta.json` + `episodes/<id>/{front.mp4,wrist.mp4,trajectory.json,meta.json}`) theo `detail_backend_withoutRobot.md` mục 6.4/4. Trường `format` phải bị bỏ khỏi form tạo dataset hoặc gắn nhãn "demo mode — chưa hỗ trợ" |
| *(không có)*                                 | `status` (`building\|ready\|failed`)       | Backend có trạng thái đóng gói bất đồng bộ mà frontend demo không mô phỏng (mock coi export là xong ngay)                                                                                                                                                                                                                       |
| `num_episodes`, `num_frames`, `size_bytes` | Khớp tên                                   | —                                                                                                                                                                                                                                                                                                                                             |
| `path`                                         | *(không có)*                             | Backend không trả đường dẫn file hệ thống ra ngoài (đúng, vì đó là chi tiết server nội bộ)                                                                                                                                                                                                                                   |
| `dvc_hash`                                     | *(không có)*                             | Backend Core không tích hợp DVC — field này vĩnh viễn không có dữ liệu thật                                                                                                                                                                                                                                                        |

**User** — frontend `User` (`lib/api.ts:21-28`) vs `UserResponse` (`schemas.py:40-50`): **khớp
hoàn toàn cả tên field lẫn kiểu** (`id, username, display_name, role, is_active, created_at`).
Đây là object duy nhất trong 4 object chính **không cần đổi tên field nào** — chỉ cần đổi kiểu
`role` từ `Role` (kiểu literal tự khai ở frontend) sang đọc trực tiếp từ backend (giá trị chuỗi
giống hệt: `operator/reviewer/admin`).

### 3.3 Đánh giá giả thuyết: giữ nguyên interface `lib/api.ts`, chỉ thay ruột + adapter field

**Đúng phần lớn, nhưng có 4 chỗ giả thuyết không áp dụng được nguyên trạng — phải sửa component,
không chỉ đổi ruột `lib/api.ts`.**

Đúng ở đâu:

- Với **Task**, **Dataset/export cơ bản**, **User**, **Auth (`me`, `login` ở mức trả về
  `User`)** — hàm giữ nguyên chữ ký, chỉ đổi thân hàm sang `fetch` thật + map field, component
  gọi `api.tasks()`/`api.users()` không cần biết gì thay đổi. Giả thuyết đúng ở nhóm này.
- Việc đổi tên field (`task_id↔task_name`, `label↔outcome`, `review_notes↔note`...) đúng là việc
  của một lớp adapter thuần túy (object spread + rename key), không cần logic nghiệp vụ, nên
  không cần sửa JSX ở các trang chỉ đọc field này để hiển thị.

Đúng nhưng **không đủ** — cần sửa thêm component, không chỉ interface `api.ts`:

1. **`demos()` — phân trang lệch mô hình, không lệch tên.** Mock dùng `limit/offset` (con trỏ
   tuyệt đối theo số bản ghi), backend dùng `page/page_size` (con trỏ theo trang). Đây không phải
   đổi tên field mà đổi **đơn vị điều hướng**: `review/page.tsx:24,131-141` giữ state `offset`
   dạng số (`offset - PAGE_SIZE`, `offset + PAGE_SIZE`) và hiển thị `offset+1`–`min(offset+SIZE, total)`. Adapter có thể giả lập `offset = (page-1)*page_size` để giữ nguyên state, nhưng khi
   `total_pages` từ backend không khớp phép tính suy ra từ `offset`/`total` (ví dụ trang cuối lẻ)
   sẽ lệch — **cách sạch hơn là đổi state của trang `review/page.tsx` từ `offset` sang `page`**,
   tức là **có sửa component**, giả thuyết "không sửa component nào" không giữ được ở đây.
2. **`review()` — 1 hàm mock gộp việc mà backend tách 3 endpoint.** Trang
   `review/[id]/page.tsx:86-113` gọi `api.review(id, {trim_start, trim_end, notes, ...body})`
   trong **một** lần submit cho mọi nút (label / approve / reject / lưu trim). Backend không có
   endpoint gộp — phải gọi `PATCH /trim`, `PATCH /label`, `POST /review` **riêng, tuần tự**, và
   mỗi cái có bộ mã lỗi 409 riêng theo trạng thái hiện tại của demo (xem 3.6). Có thể giữ chữ ký
   `api.review(id, body)` và để adapter tự quyết định gọi 1–3 request bên trong nó, nhưng khi một
   trong các bước giữa chừng thất bại (ví dụ trim OK, review 403 vì tự duyệt) thì **UI phải biết
   phần nào đã lưu, phần nào chưa** — điều mà 1 hàm `try/catch` bọc ngoài ở
   `review/[id]/page.tsx:86-113` hiện không phân biệt được. Đây là chỗ **bắt buộc sửa lại logic
   trong `submit()`**, không chỉ đổi ruột `api.ts`.
3. **`trajectory()`, `health()`, `dvc()`, nhóm `runs/evals`/Training — không có gì để "thay ruột"
   vào.** Giả thuyết ngầm định mọi hàm đều có endpoint thật đối ứng để gọi; với 10 hàm không có
   endpoint (mục 3.1), không có "ruột thật" nào để thay — bắt buộc phải sửa component gọi các hàm
   này (ẩn nút, thay bằng thông báo, hoặc xoá hẳn — xem 3.7), **component chắc chắn phải đổi**.
4. **`createExport()` — đổi từ đồng bộ sang bất đồng bộ (poll).** Mock trả `DatasetExport` đã
   "xong" ngay trong 1 lần gọi (`lib/api.ts:385-431`, có `await delay(record, 900)` giả lập độ
   trễ nhưng vẫn trả kết quả cuối). Backend trả `202` với `status=building` **ngay lập tức**, dữ
   liệu thật (`num_episodes`, `size_bytes`...) chỉ có sau khi job nền chạy xong. `datasets/page.tsx:50-73`
   (hàm `createExport`) hiện set `info` (thông báo thành công) **ngay sau 1 lần await** — phải
   đổi thành: gọi tạo dataset → nhận `status=building` → **bắt đầu vòng lặp poll**
   `GET /datasets/{id}` cho tới `ready`/`failed` → lúc đó mới hiện thông báo thật. Đây là thay
   đổi luồng điều khiển (control flow), không thể giấu hết trong `api.ts` nếu muốn UI hiện đúng
   trạng thái "đang đóng gói" thay vì im lặng đợi.

Kết luận về giả thuyết: **giữ được với Task/User/Dataset cơ bản/Auth**; **không giữ được nguyên
trạng với phân trang demo, luồng review, luồng tạo dataset (polling), và toàn bộ nhóm Teleop/
Training/DVC** (vì không có gì để nối). Đề xuất khác: coi lớp adapter là **tầng 1 bắt buộc** (đổi
tên field, không đụng logic), rồi liệt kê rõ 4 điểm trên là **tầng 2 — sửa component có chủ đích**,
làm sau khi tầng 1 xong và test được bằng dữ liệu thật.

### 3.4 Xác thực

**Nơi lưu token:** giữ `localStorage` (khớp cơ chế hiện tại ở `lib/api.ts:165-176`,
`TOKEN_KEY = "telecollect.token"`) thay vì đổi sang cookie. Lý do đề xuất giữ nguyên: đổi sang
cookie kéo theo phải bật `httpOnly` cookie do **server** set (Next Route Handler hoặc backend tự
set `Set-Cookie`), nhưng backend thật trả token trong **body JSON** của `POST /auth/login`
(`TokenResponse`, `detail_backend_withoutRobot.md` §6.1) chứ không tự set cookie — nghĩa là muốn
dùng cookie thì phải tự thêm 1 lớp Next Route Handler làm proxy để set cookie hộ, tăng việc không
cần thiết cho giai đoạn "nối dây trước". Đánh đổi khi giữ `localStorage`: token **có thể đọc được
bằng JS** (rủi ro XSS đọc trộm token) — chấp nhận được ở quy mô nội bộ hiện tại, nhưng nêu rõ đây
là nợ kỹ thuật nếu sau này expose ra ngoài Internet công khai.

**Xử lý 401 giữa chừng — refresh rồi retry, hay đá về login?**
Backend: access token sống `access_token_expire_minutes` (mặc định 30 phút,
`src/config.py`), có `refresh_token` riêng (`refresh_token_expire_days`, mặc định 7 ngày) và
endpoint `POST /auth/refresh`. Đề xuất: **refresh 1 lần rồi retry đúng 1 lần**, chỉ đá về
`/login` nếu refresh cũng thất bại (refresh token hết hạn/không hợp lệ) — không refresh vô hạn
lần (tránh vòng lặp nếu refresh token thật sự hỏng). Vị trí thêm logic này: 1 lớp fetch wrapper
dùng chung trong `lib/api.ts` (thay cho hàm `delay()` hiện tại đang là điểm chung duy nhất mọi
call đi qua) — mọi hàm trong `api.*` nên đi qua 1 hàm `request()` trung tâm thay vì tự
`fetch` rải rác, để chỗ retry-401 chỉ viết một lần. **Hiện tại hoàn toàn không có** cơ chế này
(`AuthProvider.tsx:32-45`, hàm `refresh()` chỉ gọi `api.me()` lúc mount, không xử lý 401 giữa
phiên).

**Vai trò và ẩn/hiện theo role:**
Ba role thật `operator ⊂ reviewer ⊂ admin` khớp đúng 3 giá trị frontend đã dùng
(`Role` type ở `lib/api.ts:16`, `ROLE_HELP` ở `admin/page.tsx:18-22`). Những chỗ UI đã ẩn/hiện
theo role, cần giữ nguyên khi nối thật vì logic đúng với ma trận quyền backend
(`plan_backend_core.md` mục 2.1):

- `Nav.tsx:8-15`: `/teleop` chỉ hiện cho `operator`/`admin`; `/admin` chỉ hiện cho `admin`.
- `teleop/page.tsx:19-26`: chặn `reviewer` vào trang teleop bằng thông báo — **khớp** quy tắc
  thật "reviewer không lái robot" (dù bản thân teleop chưa nối được, xem 3.7, quy tắc ẩn/hiện vẫn
  đúng và nên giữ).
- `review/[id]/page.tsx:39,246-250`: `canReview = role reviewer||admin` — khớp, nhưng **thiếu
  1 lớp mới bắt buộc phải thêm**: backend còn chặn **tự duyệt demo do chính mình upload** (403,
  trừ khi `settings.allow_self_review=true` — tính năng vừa thêm ở lượt review trước, xem
  `detail_backend_withoutRobot.md` §6.3). Hiện `canReview` chỉ so role, không so
  `demo.operator_id !== user.id`. Cần thêm nhánh UI riêng: nếu `role∈{reviewer,admin}` **và**
  `demo.operator_id === user.id` → không ẩn hẳn khối "Verdict" (vẫn cho xem), nhưng **disable nút
  Approve/Reject kèm tooltip rõ ràng** ("Không thể tự duyệt demo do chính bạn upload — cần tài
  khoản reviewer khác"), thay vì để họ bấm rồi nhận lỗi 403 khó hiểu.
- `datasets/page.tsx:48`, `training/page.tsx:58`: `canExport`/`canTrain = reviewer||admin` — khớp
  ma trận quyền tạo dataset thật (`require_min_role(REVIEWER)` ở `src/api/datasets.py:64`).
- `admin/page.tsx:46`: chặn non-admin bằng `Alert` — khớp (`users.py` yêu cầu admin toàn bộ
  router).

### 3.5 Video và file — chỗ đặc biệt

Backend nhận token qua header **hoặc** query `?token=` cho đúng 3 endpoint media:
`GET /demos/{id}/playback`, `GET /demos/{id}/thumbnail`, `GET /datasets/{id}/download`
(dependency `current_user_allow_query_token`, `src/services/security.py:138-165`, dùng ở
`src/api/demos.py:387,406` và `src/api/datasets.py:174`). Lý do đúng như đề bài nêu: thẻ
`<video src>`/`<img src>` của trình duyệt không tự thêm được header `Authorization` — trình
duyệt chỉ gửi được cái nó tự quản (cookie) hoặc cái nằm ngay trong URL.

**Cách dựng URL đúng:** `mediaUrl()` hiện tại (`lib/api.ts:550-552`) chỉ trả về 1 trong 2 file
tĩnh cố định ở `public/demo/` — không có token vì không có gì để xác thực. Khi nối thật, hàm này
phải đổi thành ghép `${NEXT_PUBLIC_API_ORIGIN}/api/v1/demos/{id}/playback?camera=front&token=${accessToken}`
(tương tự cho `thumbnail`, và `datasets/{id}/download`) rồi gán **thẳng vào `src` của thẻ
`<video>`** như đang làm ở `review/[id]/page.tsx:152,159` — **không đổi cách gọi** ở 2 dòng đó,
chỉ đổi bên trong `mediaUrl()`.

**Vì sao KHÔNG được fetch thành blob rồi gán `URL.createObjectURL()`:** nếu fetch cả file về
dưới dạng blob (cách né gửi token qua query để "an toàn hơn"), trình duyệt nhận được **toàn bộ
response một lần**, không còn khả năng gửi header `Range` khi người dùng tua — thẻ `<video>` mất
khả năng seek tới giữa file mà không tải lại từ đầu. Đây là điểm cố ý của backend
(`detail_backend_withoutRobot.md` §7 "HTTP Range trên `/playback`") — `stream_file_range()` phục
vụ đúng khúc byte được yêu cầu qua header `Range`, và cơ chế đó chỉ hoạt động khi trình duyệt
**tự** quản lý request tới URL đó (qua thẻ `<video src>` thật), không phải khi JS fetch hộ rồi
đưa blob tĩnh vào. Video demo hiện tại (`public/demo/front.webm`) là file tĩnh nên tua mượt dù
không ai để ý tới cơ chế Range — nhưng với file thật do backend stream, bỏ Range = mất tua hoặc
phải tải lại toàn bộ video mỗi lần tua.

Hệ quả phụ cần lưu ý: token trong query string sẽ lọt vào access log của server và lịch sử trình
duyệt — đây là nợ kỹ thuật **backend đã tự ghi nhận** (`detail_backend_withoutRobot.md` mục 13,
dòng "Token xác thực nằm trong query string"), không phải điều frontend gây ra hay có thể tự sửa
một mình; frontend chỉ cần biết và không "sửa hộ" theo hướng blob (sẽ hỏng tua) khi thấy token
trong URL trông không an toàn.

### 3.6 Những chỗ khác biệt về hành vi mà UI phải xử lý

**Phân trang:** backend trả `PaginatedResponse{items, total, page, page_size, total_pages}`
(`schemas.py:25-33`, `src/api/demos.py:293-298`). Mock hiện trả `{items, total, limit, offset}`
(`DemoPage`, `lib/api.ts:65-70`) — khác cả tên field lẫn đơn vị điều hướng (xem 3.3 mục 1). Trang
duy nhất dùng phân trang server-side hiện tại là `/review` (`review/page.tsx`); `/` dùng
`api.demos({limit: 8})` chỉ lấy 8 bản ghi gần nhất, không phân trang thật — với backend, gọi
`api.demos({page:1, page_size:8})` là đủ, không cần sửa gì thêm ở trang `/`.

**Upload:** hiện **không có UI upload demo nào cả** — đã tìm khắp `app/`/`components/`, không có
form/input `type="file"` nào gọi `api.*upload*`. Việc tạo demo mới duy nhất đi qua
`TeleopClient.stopRecording()` (`lib/teleop.ts:521-573`, ghi thẳng vào store, không qua
`lib/api.ts`) — nhưng đây là dữ liệu sinh ra từ simulator, **ngoài phạm vi** tài liệu này (xem Phụ
lục A). Sau khi nối API thật, việc tạo demo qua giao diện **bắt buộc** phải được lấp bằng một
đường khác — không phải khoảng trống tuỳ chọn — vì không có cách nào khác tạo demo qua giao diện.
**Kết luận (đã chốt): xây form upload file-picker riêng, route/modal độc lập, không động tới
`/teleop`/`TeleopConsole.tsx`/`lib/teleop.ts`** — xem thiết kế đầy đủ ở mục 3.10 (đợt 4). Cần xử
lý `413` (quá `settings.max_upload_mb`) và `422` (sai magic bytes / trajectory sai `action_dim`) —
2 mã lỗi này **hiện chưa có UI nào xử lý** vì chưa có luồng upload.

**Tạo dataset — polling `building→ready`:** đã phân tích ở 3.3 mục 4. Bổ sung ở đây: UI cần hiện
rõ 3 trạng thái (`building` — có thể là spinner + "Đang đóng gói…"; `ready` — hiện số liệu thật;
`failed` — hiện `error_message` từ `DatasetDetailResponse.error_message`, field này **mock hoàn
toàn không có**, `datasets/page.tsx` không có chỗ hiển thị lỗi build).

**403 tự duyệt demo của chính mình:** đã phân tích chi tiết ở 3.4. Bổ sung: luồng demo giờ **bắt
buộc 2 tài khoản** (một operator upload, một reviewer/admin khác duyệt) — nếu chỉ test bằng 1
tài khoản admin duy nhất (như 3 tài khoản gợi ý sẵn ở `login/page.tsx:7-11`,
`operator/reviewer/admin`), luồng "tự tạo rồi tự duyệt bằng admin" sẽ luôn dính 403 trừ khi bật
`allow_self_review=true` ở backend. Trang login demo hiện tại vẫn ổn cho việc **thử từng role
riêng**, chỉ cần thêm ghi chú trong UI hoặc README rằng duyệt phải dùng tài khoản khác tài khoản
đã upload.

**409 khi transition sai:** `PATCH /trim`/`PATCH /label` trả 409 nếu demo đã ở `approved`/
`rejected` (`demo_rules.ensure_status_in`); `POST /review` trả 409 nếu demo không ở
`recorded`/`labeled` (chống double-click duyệt 2 lần); `POST /reopen` trả 409 nếu demo chưa
`approved`/`rejected`. Hiện tại `review/[id]/page.tsx:106-107` có bắt lỗi chung
(`catch (exc) { setError(exc.message) }`) nên **về mặt kỹ thuật không crash**, nhưng thông báo sẽ
là message thô từ backend (tiếng Việt, kiểu "Không thể 'duyệt' khi demo đang ở trạng thái
'approved'") trộn lẫn với các lỗi khác — chấp nhận được cho giai đoạn nối dây đầu tiên, có thể
làm đẹp riêng từng mã lỗi ở đợt giao diện sau (mục 5).

### 3.7 Trang không nối được

Backend Core (theo `detail_backend_withoutRobot.md` §11 và xác nhận trực tiếp bằng
`grep "@router\." src/api/teleop.py src/api/training.py` — **không ra kết quả nào**, tức 2 file
này chỉ có `router = APIRouter(prefix=..., tags=...)` và docstring "Endpoint dự kiến", chưa cài
route thật nào) **không có**: Teleoperation, Training/Eval, Export định dạng LeRobot/RLDS, DVC.

| Trang/tính năng                                                        | Vì sao không nối được                                                                                                                                                                                                                    | Đề xuất                                                                                                                                                 | Ưu / nhược                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/teleop` (toàn trang, `TeleopConsole`)                             | `lib/teleop.ts` là simulator vật lý đồ chơi chạy bằng canvas 2D **trong trình duyệt**, không có WebSocket server thật đứng sau (`teleop.ts:3-17` tự ghi rõ điều này); `src/api/teleop.py` chưa có route nào | **Giữ nguyên, gắn nhãn "Demo mode"** rõ ràng trên UI (badge hoặc banner ở đầu trang) thay vì ẩn                                         | Ưu: đây là trang**bắt mắt nhất khi demo trực tiếp** (đúng như đề bài lưu ý) — xoá hoặc ẩn sẽ mất hẳn phần trình diễn ấn tượng nhất, trong khi 3 tuần còn lại không đủ để xây WebSocket + MuJoCo thật (đây là module rủi ro kỹ thuật cao nhất theo chính `plan_backend.md` mục 4, bước 4, ghi "phức tạp nhất... rủi ro kỹ thuật cao nhất"). Nhược: demo ghi ra từ trang này **không** đi vào DB thật (chỉ lưu `localStorage` qua `demo-data.ts`) — nếu không gắn nhãn rõ, người xem dễ hiểu lầm đây là dữ liệu thật đã vào hệ thống, trong khi thực ra 2 hệ dữ liệu (canvas demo vs backend thật) **hoàn toàn tách biệt** sau khi nối API |
| `/training` (toàn trang)                                              | Không có endpoint`POST /training/jobs`, `GET /training/jobs`, `.../evaluate` nào chạy thật                                                                                                                                          | **Đã chốt (mục 7 câu 2): ẩn khỏi `Nav`** (bỏ mục "Training" khỏi `Nav.tsx:13`) — không giữ route với thông báo "chưa hỗ trợ"  | Ưu: tránh người dùng bấm "Start training" rồi nhận lỗi mạng khó hiểu (không phải 404 JSON gọn mà network error vì route không tồn tại); tránh giám khảo tò mò bấm vào lúc demo trực tiếp rồi thấy trang trống/thông báo dở dang. Nhược: ẩn hẳn nghĩa là hoãn phần "nghiệm thu cuối" của demo tới khi có module Training thật — chấp nhận được vì đây vốn là bước cuối cùng theo lộ trình backend (`plan_backend.md` mục 4, bước 6, "làm cuối")                                                                                                                                                                                                                                              |
| Nút "Export dataset" với`format=rlds`, field `format` nói chung   | `DatasetCreateRequest` (`schemas.py:285-297`) không có field `format` — backend chỉ đóng 1 kiểu zip cố định                                                                                                                    | **Bỏ dropdown chọn format** khỏi form (`datasets/page.tsx:98-103`), hoặc giữ nhưng disable option `rlds` kèm chú thích "chưa hỗ trợ" | Ưu: đỡ tạo kỳ vọng sai — chọn`rlds` rồi nhận zip không đúng định dạng đã chọn sẽ gây nhầm lẫn nghiêm trọng hơn là ẩn hẳn. Nhược: không có                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Khối "Version control" / DVC (`datasets/page.tsx:219-231`)            | Không có endpoint`dvc` nào ở backend Core                                                                                                                                                                                                | **Ẩn khối này** (không phải tính năng cốt lõi, chỉ là ghi chú quy trình)                                                                | Không có nhược điểm đáng kể — đây là phần diễn giải quy trình, không thao tác được gì thật                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `EvalVideos`, biểu đồ loss training (`training/page.tsx:250-421`) | Phụ thuộc`/training`                                                                                                                                                                                                                       | Đi theo quyết định ẩn`/training` ở trên                                                                                                           | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

### 3.8 Cấu hình

Backend đã bật CORS cho đúng `http://localhost:3000` và `http://localhost:5173`
(`.env.example:54-55`: `CORS_ORIGINS=http://localhost:3000,http://localhost:5173`; đọc bởi
`src/config.py:23`, mặc định code là chỉ `http://localhost:3000` nếu không set biến môi trường —
`.env.example` đã liệt kê rộng hơn default trong code, tức là **file `.env` thật đang dùng phải
tồn tại và có dòng này** để origin `5173` cũng được chấp nhận, không phải mặc định sẵn có).
`next dev -p 3000` (`package.json` script `dev`) chạy đúng cổng `3000` đã được backend cho phép —
không cần đổi cổng dev hiện tại.

Chưa có file `.env.local` hay `.env.local.example` nào trong `frontend/` (`ls frontend` không
liệt kê, `.gitignore` cũng không có dòng `.env*` — nghĩa là nếu tạo `.env.local` sau này, cần tự
thêm dòng ignore, Next tự động ignore `.env.local` theo mặc định nội bộ của nó nên không bắt buộc
nhưng nên thêm tường minh để rõ ràng). Cần thêm biến môi trường:

```
# frontend/.env.local.example (file mới, cần tạo)
NEXT_PUBLIC_API_ORIGIN=http://localhost:8000
```

`NEXT_PUBLIC_` là bắt buộc vì mọi lời gọi API đều nằm trong Client Component (`"use client"` ở
đầu `lib/api.ts:1`) — biến không có tiền tố này sẽ không lọt vào bundle phía trình duyệt.
`next.config.mjs` hiện tại (`next.config.mjs:1-7`) chỉ có `reactStrictMode: true` và ghi chú "bản
frontend thật rewrite `/api/*` sang FastAPI server ở đây" — nếu chọn hướng gọi thẳng
`NEXT_PUBLIC_API_ORIGIN` từ client (như CORS backend đã cho phép) thì **không cần** thêm
`rewrites()` vào `next.config.mjs`; rewrite chỉ cần nếu muốn giấu origin thật sau proxy Next
(không bắt buộc, có thể để lại cho đợt sau).

### 3.9 Ba vấn đề vòng đời/runtime chưa nêu ở bản trước — bổ sung

**a. Token trong URL video hết hạn giữa chừng.** `access_token_expire_minutes` mặc định **30
phút** (`src/config.py:49`). Khi `mediaUrl()` nối thật (mục 3.5), token được nhúng thẳng vào
`src` của `<video>` — nếu người dùng mở `/review/[id]` rồi để đó (đọc trajectory, đi pha cà phê)
quá 30 phút rồi mới bấm play, request tới `/demos/{id}/playback?token=...` sẽ nhận **401**. Vấn
đề không chỉ là lỗi — là **lỗi vô hình**: thẻ `<video>` không expose mã trạng thái HTTP ra
JavaScript, `onError` chỉ nhận được một `MediaError` chung chung (thường là `MEDIA_ERR_SRC_NOT_SUPPORTED`
hoặc tương tự), người dùng chỉ thấy video đen thui không rõ vì sao — không phân biệt được với
"file hỏng" hay "sai định dạng".

Đề xuất 2 lớp, không loại trừ nhau:

1. **Chủ động, làm trước:** trong `AuthProvider` (hoặc 1 hook riêng), đặt một `setInterval` refresh
   access token định kỳ (ví dụ mỗi 20 phút, ngắn hơn hẳn 30 phút hết hạn) *trong khi có ít nhất 1
   trang đang mở* — access token mới thì `mediaUrl()` phải được gọi lại để nhúng token mới vào
   `src`, nghĩa là component xem video cần theo dõi token hiện tại (qua context, không đọc thẳng
   `localStorage` mỗi lần) và tự set lại `src` khi token đổi.
2. **Bị động, làm để không vỡ khi (1) trễ hoặc bị bỏ qua:** bắt sự kiện `onError` trên thẻ
   `<video>` ở `review/[id]/page.tsx:150-162`, khi bắt được thì thử gọi `POST /auth/refresh` một
   lần, nếu thành công thì build lại `src` với token mới rồi gọi `video.load()`; nếu refresh cũng
   thất bại thì mới hiện thông báo lỗi thật cho người dùng. Không tự động thử vô hạn lần (tránh
   vòng lặp nếu file thật sự hỏng, không phải do token).

**b. Nút "Start recording" ở `/teleop` thành ngõ cụt sau khi nối API thật.**
`TeleopClient.stopRecording(true)` (`lib/teleop.ts:521-573`) ghi `Demo` thẳng vào `store.demos`
qua hàm `save()` của `demo-data.ts` (tức `localStorage`), **không** gọi qua bất kỳ hàm nào trong
`lib/api.ts`. Sau khi `api.demos()` được nối sang gọi `GET /api/v1/demos` thật (mục 3.1), 2 nguồn
dữ liệu **tách hẳn**: demo ghi ở `/teleop` nằm trong `localStorage` của trình duyệt, còn
`/review` đọc từ DB backend qua HTTP — **demo ghi ở `/teleop` sẽ không bao giờ xuất hiện ở
`/review`, `/datasets`, hay bất kỳ đâu đọc qua API thật.** Tài liệu bản trước có nhắc "hai hệ dữ
liệu tách biệt" ở mục 3.7 nhưng chưa nói thẳng ra hệ quả cụ thể này với đúng nút bấm — bổ sung ở
đây cho rõ.

**Đây là việc bàn giao, không phải việc của phạm vi tài liệu này.** Sửa nút "Stop & save" để nó
không còn là ngõ cụt nghĩa là phải đụng vào `TeleopConsole.tsx`/`lib/teleop.ts` — nằm ngoài ràng
buộc phạm vi đã chốt (chỉ nối web thường: auth/users/tasks/demos/datasets/upload, không động tới
robot/Teleop). Việc này thuộc về người phụ trách Teleop, cần được phân công riêng, không nằm
trong các đợt ở mục 6.

**Hệ quả nếu không ai làm trước ngày demo:** nút "Stop & save" ở `/teleop` vẫn ghi vào
`localStorage` như hiện tại — không vào DB thật, không xuất hiện ở `/review`/`/datasets`. Nếu hôm
demo trực tiếp có người bấm ghi thử ở `/teleop` rồi tưởng đó là một demo thật, nó sẽ **biến mất
không dấu vết** ngay sau khi rời trang (không toast báo lỗi, không log) — người xem sẽ thấy sản
phẩm hỏng ngay giữa buổi. Đây là việc **phải bàn giao trước ngày demo** để tránh tình huống đó,
tối thiểu bằng cách disable nút hoặc thêm chú thích cảnh báo ngay trên UI của `/teleop` — nhưng cả
việc tối thiểu đó cũng đụng `TeleopConsole.tsx`, nên vẫn nằm ngoài phạm vi của tôi, chỉ ghi nhận
ở đây để nhóm biết mà phân công.

**c. Backend chết hoàn toàn (mất mạng, server sập) — hiện không trang nào xử lý.**
`app/page.tsx:31` bọc toàn bộ `Promise.all([...])` bằng `.catch(() => undefined)` — nuốt lỗi hoàn
toàn, trang đứng yên ở trạng thái rỗng vĩnh viễn không có bất kỳ dấu hiệu nào cho biết đang lỗi
mạng chứ không phải "chưa có dữ liệu". `review/page.tsx:56-57` còn tệ hơn: `void api.tasks().then(setTasks)` không có `.catch` nào — nếu lỗi, đây là **unhandled promise
rejection** im lặng (không crash app nhờ React không phụ thuộc promise đó để render, nhưng lỗi
biến mất không dấu vết, kể cả trong console một cách rõ ràng cho người dùng cuối).

Đề xuất một cơ chế dùng chung, không cần sửa từng trang: thêm 1 `ApiStatusContext` nhỏ (tương tự
`AuthProvider`, đặt cạnh nó trong `app/layout.tsx`) giữ state `unreachable: boolean`. Hàm
`request()` trung tâm đề xuất ở mục 3.4 (điểm mọi lời gọi `api.*` đều đi qua để xử lý refresh
401) là đúng chỗ để bắt luôn lỗi mạng (`TypeError: Failed to fetch`, hoặc `fetch` reject) — khi
bắt được, set `unreachable=true` qua context thay vì để lỗi rơi tự do; `Nav.tsx` (luôn mount, xem
mục 2) hiện 1 banner cố định phía trên khi `unreachable=true` ("Không kết nối được máy chủ — thử
lại"), tự tắt khi có request nào đó thành công trở lại. Cách này gom xử lý về **một chỗ** (hàm
`request()`), khớp với đề xuất refresh-401 đã có sẵn ở mục 3.4 — không phải thêm `try/catch`
riêng ở từng `useEffect` của 8 trang.

### 3.10 Thiết kế đợt 4: Form upload file-picker

> Ràng buộc phạm vi: **không động một dòng nào tới `lib/teleop.ts`/`TeleopConsole.tsx`, không
> dùng dữ liệu sinh ra từ simulator.** Phương án `MediaRecorder` ghi canvas → `/teleop` thành
> nguồn upload thật (bản trước của mục này) đã bị loại vì vi phạm đúng ràng buộc đó — toàn bộ
> phân tích thực nghiệm của phương án đó vẫn còn giá trị, chuyển sang **Phụ lục A** cuối tài liệu
> cho người phụ trách Teleop sau này, không xoá.

**Đặt ở đâu — route mới `/upload` hay modal trên `/review`?**

| Phương án            | Ưu                                                                                                                                                                                          | Nhược                                                                                                                                                                                                                                                                                                         |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route riêng`/upload` | URL riêng để chia sẻ/bookmark; không phải maintain state modal chồng lên danh sách`/review` đang có phân trang + filter; đơn giản hơn khi thêm progress bar to, dễ nhìn | Thêm 1 entry Nav, thêm 1`page.tsx`                                                                                                                                                                                                                                                                          |
| Modal trên`/review`  | Không rời khỏi ngữ cảnh hàng đợi review                                                                                                                                              | Phải quản lý state modal chồng lên state phân trang/filter đã có sẵn ở`review/page.tsx` (`offset`, `statusFilter`...); modal có progress bar + nhiều trạng thái lỗi (413/422/404) sẽ làm component `review/page.tsx` phình to, trộn lẫn 2 mối quan tâm (danh sách vs tạo mới) |

**Chọn route riêng `/upload`** — đơn giản hơn, tách bạch rõ giữa "xem danh sách" và "tạo demo
mới", chi phí thêm 1 entry Nav là nhỏ.

Thêm mục "Upload" vào `Nav.tsx` — theo ma trận quyền `plan_backend_core.md` §2.1, **mọi role đều
upload được** (operator tạo demo của chính mình; reviewer/admin cũng không bị chặn tạo demo), nên
mục nav mới **hiện cho cả 3 role**, không có điều kiện ẩn theo role như `/teleop`/`/admin`.

**Trường trong form:**

| Trường               | Bắt buộc | Nguồn                                                               | Ghi chú                                                                                                                                                                                                                                                                                                                                                                           |
| ---------------------- | ---------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Task                   | Có        | dropdown, lấy từ`api.tasks()`                                    | dùng lại adapter`name→id`/`title` đã chốt ở mục 3.2                                                                                                                                                                                                                                                                                                                    |
| `front` (video)      | Có        | `<input type="file" accept="video/mp4,video/quicktime,.mp4,.mov">` | `accept` liệt kê cả `.mov` — xem kết luận ngay dưới, `.mov` upload được nguyên trạng, không cần lọc riêng                                                                                                                                                                                                                                                   |
| `wrist` (video)      | Không     | `<input type="file" accept="video/mp4,video/quicktime,.mp4,.mov">` |                                                                                                                                                                                                                                                                                                                                                                                    |
| `trajectory` (.json) | Không     | `<input type="file" accept="application/json">`                    | **Giữ ô này** — đọc file rồi đính vào `FormData` là I/O thuần (`file.text()` hoặc gửi thẳng file gốc), không phải logic mô phỏng robot, không đụng `lib/teleop.ts`; backend đã nhận sẵn field này ở `/demos/upload` (`_validate_trajectory`, `src/api/demos.py:123-146`) nên không cần sửa gì backend cho riêng trường này |

**Thanh tiến trình upload:** file vài chục MB cần phản hồi thị giác — dùng
`XMLHttpRequest.upload.onprogress` (không dùng `fetch` thuần vì `fetch` không expose progress
event cho request body chuẩn hoá ở mọi trình duyệt hiện tại) để cập nhật `%` lên một thanh progress
trong lúc `POST /demos/upload` đang chạy.

**Xử lý mã lỗi — mỗi mã một thông báo riêng, không gộp:**

| Mã | Nguyên nhân                                                                                                               | Thông báo đề xuất                                                                                                                                                                                                         |
| --- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 413 | Vượt`settings.max_upload_mb`                                                                                            | "File vượt quá dung lượng cho phép (tối đa {max_upload_mb} MB) — hãy nén hoặc cắt bớ video."                                                                                                                    |
| 422 | Sai magic bytes (không phải mp4 hợp lệ) hoặc`trajectory.json` sai `action_dim`/shape                               | "File video không đúng định dạng .mp4" hoặc "File trajectory không khớp định dạng — kiểm tra lại field`action`." (đọc `detail` trong response để phân biệt 2 nguyên nhân, không gộp chung 1 câu) |
| 404 | `task_name` không tồn tại (hiếm khi xảy ra qua dropdown, nhưng có thể do task bị xoá giữa lúc form đang mở) | "Task đã chọn không còn tồn tại — tải lại danh sách task và chọn lại."                                                                                                                                           |

**Sau khi thành công:** điều hướng tới `/review/[id]` của demo vừa tạo (response `POST /demos/upload` trả `DemoResponse` có `id`).

**Backend không cần sửa gì cho luồng .mp4** — upload `.mp4` đã chạy sẵn từ Bước 3a của
`plan_backend_core.md` (endpoint `POST /demos/upload`, validate magic bytes + `ffprobe`, đã có
test). Ghi rõ ở đây để không ai trong nhóm tưởng còn việc backend cho nhánh .mp4.

**`.mov` từ iPhone — đã đọc code, KẾT LUẬN DỨT KHOÁT: upload được nguyên trạng, không cần sửa
backend.** Ba điểm đã xác nhận trực tiếp trong code, không còn là câu hỏi mở:

1. `has_mp4_magic_bytes()` (`src/services/media.py:35-44`) chỉ kiểm `header[4:8] == b"ftyp"` —
   **không so khớp brand** (`isom`/`mp42`/`qt  `...). Bất kỳ file nào có marker `ftyp` ở offset 4
   đều qua được bước này.
2. `src/api/demos.py` (dòng 184 cho `front`, dòng 199 cho `wrist`) **không có bước whitelist đuôi
   file hay `Content-Type`** nào khác — chỉ gọi đúng `has_mp4_magic_bytes()` rồi tới `probe_video()`
   (ffprobe, đọc trực tiếp codec trong container, không quan tâm đuôi file).
3. `.mov` (QuickTime) cùng họ **ISO Base Media File Format** với `.mp4` — cũng có marker `ftyp` ở
   offset 4, mang brand `qt  ` (không phải `isom`/`mp42` nhưng vẫn là `ftyp`). `ffprobe` đọc
   `.mov` bình thường (khác hẳn vấn đề "thiếu duration" của webm streaming ở Phụ lục A — `.mov`
   ghi theo kiểu file hoàn chỉnh, luôn có duration).

=> `.mov` **đi qua được cả `has_mp4_magic_bytes()` lẫn `probe_video()` mà không cần sửa dòng
backend nào.** Không còn 2 hướng (a)/(b) để cân nhắc — chỉ có 1 việc cần làm ở UI: cho phép chọn
file `.mov` trong form (đã cập nhật `accept` ở bảng trên).

Hai lưu ý nhỏ, ghi làm nợ kỹ thuật (đã thêm vào `detail_backend_withoutRobot.md` mục 13):

- **Tên file không khớp nội dung thật.** `storage.py:26-27` hardcode `FRONT_FILENAME = "front.mp4"`/`WRIST_FILENAME = "wrist.mp4"` — file `.mov` upload lên sẽ được lưu với đuôi
  `.mp4` dù nội dung bên trong là container QuickTime. Không gây lỗi runtime: `ffprobe` đọc theo
  nội dung byte thật (không theo đuôi file), và endpoint playback trả cứng `media_type="video/mp4"`
  (`src/api/demos.py:399`) nên trình duyệt vẫn tự nhận diện được codec bên trong (H.264/AAC —
  track phổ biến nhất từ iPhone — tương thích cả 2 container). Chỉ là tên file trên storage không
  phản ánh đúng định dạng gốc — chấp nhận được, không cần sửa.
- **`accept` trong form nên liệt kê cả `.mov`.** Nếu chỉ để `accept="video/mp4"`, bộ lọc chọn file
  của hệ điều hành sẽ ẩn bớt file `.mov` theo mặc định, buộc người dùng phải đổi bộ lọc thủ công
  ("All Files") dù backend chấp nhận file đó bình thường — đổi `accept` thành
  `"video/mp4,video/quicktime,.mp4,.mov"` để tránh gây hiểu nhầm ngược với thực tế backend.

**Ước lượng công sức đợt upload (form file-picker):** thay thế hoàn toàn cho ước lượng 3–3.5 ngày
của phương án `MediaRecorder` (Phụ lục A) — **giảm mạnh còn ~1–1.5 ngày, hoàn toàn không cần việc
backend nào** (khác với ước lượng bản trước có tính thêm giờ backend cho việc nới whitelist —
không còn cần nữa vì `.mov` đã chạy được nguyên trạng): route `/upload` + form 4 trường + progress
bar (`XMLHttpRequest`) + 3 thông báo lỗi riêng (413/422/404) + điều hướng sau thành công.

---

## 4. Vấn đề giao diện — làm SAU khi nối xong

Ghi chú theo yêu cầu đề bài: mục này liệt kê hiện trạng cụ thể theo file, **không đưa lời khuyên
chung chung**.

### Trạng thái thiếu

- `app/page.tsx`: có xử lý khi `summary`/`recent` rỗng (`Empty` ở dòng 131, 150) nhưng **không có
  trạng thái loading** trong lúc `Promise.all` 4 lời gọi API chạy (dòng 18-32) — trong lúc chờ,
  toàn bộ `Stat` hiện `"—"` (dòng 66, 72, 79, 84) chứ không phải skeleton, và các `Card` rỗng
  hoàn toàn tới khi `useEffect` xong, không có chỉ báo "đang tải".
- `app/review/page.tsx`: có biến `loading` (dòng 30, set ở `load()`) nhưng khi `loading===true`
  chỉ render `<Empty>Loading…</Empty>` (dòng 148) **thay thế toàn bộ bảng**, bao gồm cả khi đang
  load lại trang 2/3 (không phải lần đầu) — bảng dữ liệu cũ biến mất rồi hiện lại, gây giật thay
  vì giữ dữ liệu cũ mờ đi trong lúc tải trang mới.
- `app/review/[id]/page.tsx`: dòng 119, `if (!demo || !trajectory) return <Empty>Loading recording…</Empty>` — **không có trạng thái disabled** riêng cho từng nút trong lúc `busy` là
  đúng (`disabled={busy}` lặp ở nhiều nút, dòng 256-347) nhưng **không có skeleton cho khối
  video/trajectory** trong lúc `load()` (dòng 41-47) chạy lần đầu — chỉ có `Empty` toàn trang, mất
  hẳn layout 2 cột lúc đang tải.
- `app/datasets/page.tsx`, `app/admin/page.tsx`, `app/training/page.tsx`: **không có biến
  `loading` nào cho `useEffect` gọi API ban đầu** (ví dụ `datasets/page.tsx:41-45`,
  `admin/page.tsx:40-43`) — bảng/section hiện `Empty` (nội dung rỗng) trong khi đang tải cũng
  như khi tải xong mà thật sự không có dữ liệu, 2 trạng thái này **không phân biệt được** trên
  UI.
- Không trang nào có UI cho trạng thái **error** khi lời gọi API ban đầu thất bại (không phải lỗi
  submit form) — ví dụ `app/page.tsx:31` `.catch(() => undefined)` **nuốt lỗi hoàn toàn**, nếu
  `api.summary()` lỗi mạng, trang chỉ đứng yên ở trạng thái rỗng vĩnh viễn, không có thông báo gì.
  Tương tự `app/review/page.tsx:56-57` (`.then(setTasks)`, `.then(setSummary)`, không `.catch`
  nào) — lỗi mạng ở đây sẽ là **unhandled promise rejection** im lặng.

### Phản hồi thao tác

- Nút "Save trim & notes only" và các nút review khác ở `review/[id]/page.tsx:281-301` dùng chung
  state `busy` (disable đồng loạt) nhưng **không có spinner/icon loading trong chính nút bấm** —
  chỉ dựa vào `disabled`, người dùng không có phản hồi thị giác nào khác ngoài nút bị mờ đi
  (`disabled:opacity-40` từ `ui.tsx:63`).
- Không có toast/snackbar ở bất kỳ đâu trong app — mọi thông báo thành công dùng `Alert tone="ok"` gắn cố định trong layout (ví dụ `review/[id]/page.tsx:322`, `datasets/page.tsx:155-159`),
  nghĩa là thông báo **biến mất khi điều hướng trang**, không tồn tại độc lập như toast thật.
- `admin/page.tsx:154-157,169-172`: đổi role / bật-tắt user gọi `await api.updateUser(...)` trực
  tiếp trong `onChange`/`onClick` của `<Select>`/`<Button>` **không có `busy` cục bộ cho hàng đó**
  — trong lúc network đang chạy, người dùng có thể bấm lại hoặc đổi tiếp, không có gì chặn double
  submit ở đúng hàng đang xử lý (khác với form "Add a user" phía trên có `busy` riêng, dòng 34).

### Chuyển động

- Toàn bộ app chỉ có 2 chỗ dùng chuyển động: `transition-colors` trên link nav
  (`Nav.tsx:43`) và nút (`ui.tsx:63`), và `animate-pulse` trên chấm đỏ "REC"
  (`TeleopConsole.tsx:172`). Không có transition khi:
  - Chuyển trang (Next App Router không tự thêm transition, và code không có gì custom).
  - `Card`/`Alert` xuất hiện — mọi khối `{condition && <Card>...}` (rất nhiều chỗ, ví dụ
    `app/page.tsx:175`, `review/[id]/page.tsx:342`) **hiện đột ngột**, không fade-in.
  - `TrimTimeline.tsx` kéo thả handle (dòng 118-137) — cập nhật vị trí tức thời theo pointer, hợp
    lý cho thao tác kéo nhưng khi bấm nút "Set in = playhead"/"Reset trim" (`review/[id]/page.tsx:179-194`)
    thanh trim **nhảy vị trí ngay lập tức**, không có animate.

### Responsive

- Có dùng breakpoint Tailwind chuẩn (`sm:`, `lg:`, `xl:`) rải rác — ví dụ
  `app/page.tsx:63` (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-4`),
  `review/[id]/page.tsx:146` (`xl:grid-cols-[minmax(0,1fr)_340px]`),
  `TeleopConsole.tsx:163` (`xl:grid-cols-[minmax(0,1fr)_360px]`).
- Bảng trong `review/page.tsx:152-201`, `datasets/page.tsx:167-215`,
  `admin/page.tsx:133-181` đều có `overflow-x-auto` bọc ngoài `<table>` — cuộn ngang được, không
  vỡ layout trên mobile, nhưng **không có phiên bản card-list thay thế** cho màn hình hẹp; trên
  điện thoại, người dùng phải cuộn ngang một bảng 9-10 cột (`review/page.tsx:156-166`) để đọc hết
  — dùng được nhưng không thoải mái.
- `TeleopConsole.tsx:210-243`: khối canvas dùng `xl:h-[calc(100dvh-11.5rem)]` (chỉ ở breakpoint
  `xl`) — dưới `xl` không có chiều cao cố định tương ứng, `aspect-square` tự co theo chiều rộng
  nên vẫn hoạt động, nhưng phần điều khiển dưới canvas trên màn hình dọc/hẹp (điện thoại) sẽ cần
  cuộn nhiều vì khối `Card` "Latency and control loop" (dòng 297) nằm ngay dưới, không có
  `lg`/`md` riêng để rút gọn số cột (`grid-cols-2 sm:grid-cols-4 lg:grid-cols-7`, dòng 298) —
  ở `sm` (điện thoại ngang) vẫn là 4 cột số liệu, khá chật.

### Khả năng tiếp cận

- `Input`/`Select`/`TextArea` (`ui.tsx:143-157`) có `focus:border-accent-500 focus:ring-1 focus:ring-accent-500/40` — có focus ring, nhưng **`Button`
  (`ui.tsx:53-69`) không có style `focus-visible` riêng nào**, chỉ dựa vào outline mặc định của
  trình duyệt (không bị `outline-none` ghi đè ở đây, nên vẫn hoạt động, nhưng không nhất quán về
  mặt thiết kế với input).
- Không tìm thấy `aria-label` nào trong toàn bộ `app/`/`components/` (đã tìm không ra kết quả).
  Đáng chú ý nhất: nút Play/pause native của thẻ `<video controls>` (`review/[id]/page.tsx:150-156`)
  dùng UI mặc định trình duyệt nên có accessibility sẵn, nhưng các nút tự viết như handle kéo trim
  (`TrimTimeline.tsx:123-137`, chỉ có `title` không có `aria-label`/`role="slider"`) hay checkbox
  "Loop the trimmed clip" (dòng 208-215, có `<label>` bọc `<input>` nên **đã** có accessible name
  qua label — điểm này thực ra ổn) không đồng nhất về mức độ gắn nhãn.
- Điều hướng bàn phím: `TrimTimeline` không bind phím mũi tên để nhích handle từng frame — chỉ
  kéo chuột (`onPointerDown/Move/Up`, dòng 56-71), người dùng chỉ dùng bàn phím không thao tác
  được thanh trim.
- Tương phản màu: chưa đo số liệu (cần công cụ ngoài — **cần kiểm tra thêm** bằng Lighthouse/axe
  thay vì đọc code suy đoán), nhưng bảng màu `ink-400` trên nền `ink-950`
  (`globals.css:4,10`) dùng làm text phụ ở rất nhiều nơi (`text-ink-400` xuất hiện hàng chục lần)
  — đáng để đo tỉ lệ tương phản thật trước khi kết luận đạt/không đạt WCAG AA.

### Form

- `login/page.tsx:46-63`: có `required` HTML5 trên input nhưng không có validate client tuỳ biến
  (ví dụ độ dài mật khẩu tối thiểu) trước khi submit — để nguyên cũng hợp lý vì backend thật sẽ
  validate qua `PASSWORD_MIN_LENGTH=8` (`schemas.py:35-37`) ở `RegisterRequest`, nhưng **login
  không có ràng buộc độ dài**, submit rồi mới biết sai qua thông báo lỗi từ `catch` (dòng 26-27).
- `admin/page.tsx:104`: nút "Create user" disable khi `!form.username || form.password.length < 4`
  — **ngưỡng 4 ký tự không khớp** `PASSWORD_MIN_LENGTH=8` bên backend thật (`schemas.py:36`); nộp
  mật khẩu 4-7 ký tự sẽ qua được điều kiện disable ở client rồi mới bị backend từ chối (422) —
  UI hiện không đọc field lỗi 422 chi tiết theo field, chỉ show `exc.message` gộp (dòng 113).
- Không trang nào có trạng thái "đang submit" tách riêng khỏi trạng thái nút bị disable (không có
  text nút đổi thành "Đang lưu…" ở form Add user hay Create export lúc gọi API, ngoại trừ
  `training/page.tsx:199` "Start training" **có** đổi text theo `busy` — đây là chỗ **duy nhất**
  làm đúng mẫu này, còn `admin/page.tsx:118-120` ("Create user") thì không đổi text theo `busy`).

### Danh sách 40+ demo

- `demo-data.ts:174` sinh cố định **46 demo** giả lập — không phải test case biên, là kích cỡ mặc
  định của seed data.
- `review/page.tsx` **có** phân trang thật (`PAGE_SIZE = 25`, dòng 18) — không load hết 1 lần,
  đúng hướng cho danh sách lớn.
- **Không có thumbnail nào hiển thị trong danh sách** — cả bảng `review/page.tsx:169-198` lẫn
  danh sách "Recent recordings" ở `app/page.tsx:152-170` chỉ hiện text (task, operator, thời
  gian), không có `<img>` thumbnail nào — nghĩa là vấn đề "lazy-load thumbnail cho danh sách dài"
  **chưa tồn tại vì tính năng thumbnail chưa được hiển thị ở bất kỳ đâu**, dù backend đã có sẵn
  endpoint `GET /demos/{id}/thumbnail` (`detail_backend_withoutRobot.md` §6.3) sẵn sàng dùng.

---

## 5. Đề xuất cải thiện giao diện

Ưu tiên nhóm **không cần dependency** — còn 3 tuần, mỗi thư viện mới là một rủi ro (theo đúng yêu
cầu). Cột "An toàn trước/Phải đợi" đánh dấu theo tiêu chí: có đụng tới field/shape dữ liệu từ
`lib/api.ts` hay component đang chờ sửa ở mục 3 hay không.

### Nhóm A — KHÔNG cần thêm thư viện

| Hạng mục                                                                                    | File cần sửa                                                                                                                                                                                                          | Mô tả cụ thể                                                                                                                                                                       | Công sức (giờ)    | Ưu tiên                                      | An toàn trước / Phải đợi                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Skeleton loading cho`Card`                                                                  | `components/ui.tsx` (thêm biến thể `Card` hoặc component `Skeleton` mới, CSS thuần `animate-pulse` có sẵn trong Tailwind)                                                                               | Thay`Empty`/`"—"` lúc tải lần đầu ở `app/page.tsx`, `review/page.tsx`, `datasets/page.tsx`, `admin/page.tsx` bằng khối xám nhấp nháy đúng layout cuối cùng | 3–4                 | Trung bình                                    | **An toàn trước** — chỉ cần biết field nào sẽ hiện, không cần shape thật                                                                                                                                                                                                                               |
| Toast tự viết (context + portal, không dùng thư viện)                                   | Tạo`components/Toast.tsx` mới + `app/layout.tsx` bọc provider                                                                                                                                                    | Thay`Alert` cố định trong `Card` bằng toast nổi góc màn hình, tự biến mất sau vài giây, không mất khi điều hướng                                                | 4–6                 | Trung bình                                    | **An toàn trước** — thuần UI, không phụ thuộc shape API                                                                                                                                                                                                                                                     |
| Focus-visible cho`Button`                                                                   | `components/ui.tsx` (`BUTTON_STYLES`/class chung)                                                                                                                                                                   | Thêm`focus-visible:ring-2 focus-visible:ring-accent-500/60 focus-visible:outline-none` nhất quán với `Input`/`Select`                                                        | 0.5                  | Cao (rẻ, tác động lớn tới a11y)          | **An toàn trước**                                                                                                                                                                                                                                                                                                |
| `aria-label` cho control tự viết                                                          | `components/TrimTimeline.tsx` (2 handle, track), `components/TeleopConsole.tsx` (canvas, các nút icon-only nếu có)                                                                                              | Thêm`aria-label`/`role="slider"` + `aria-valuenow` cho handle trim; `aria-label` cho canvas điều khiển                                                                     | 2–3                 | Trung bình                                    | **An toàn trước**                                                                                                                                                                                                                                                                                                |
| Bàn phím cho`TrimTimeline`                                                                | `components/TrimTimeline.tsx`                                                                                                                                                                                         | Bind`ArrowLeft`/`ArrowRight` khi handle đang focus để nhích 1 frame; `Home`/`End` nhảy về đầu/cuối                                                                    | 2                    | Thấp                                          | **An toàn trước** — nhưng phụ thuộc đơn vị `frame` hiện tại; nếu tầng adapter đổi trim sang giây nội bộ (xem 3.2) thì phải làm **sau khi** đã quyết định đơn vị hiển thị cuối cùng — **nên đợi**                                                                |
| Fade-in cho khối điều kiện (`Card`/`Alert` xuất hiện)                               | CSS thuần: thêm class Tailwind`animate-in fade-in` **không có sẵn** trong Tailwind lõi — dùng `transition-opacity` + `useEffect` toggle class, hoặc `@keyframes` tự viết trong `globals.css` | Áp cho các khối`{condition && <Card>}` ở `app/page.tsx:175`, `review/[id]/page.tsx:342`, `datasets/page.tsx:150-159`                                                       | 2–3                 | Thấp                                          | **An toàn trước**                                                                                                                                                                                                                                                                                                |
| Card-list thay bảng trên mobile                                                             | `review/page.tsx`, `datasets/page.tsx`, `admin/page.tsx`                                                                                                                                                          | Thêm biến thể hiển thị dạng thẻ xếp dọc khi`< sm`, ẩn `<table>` ở breakpoint đó (`hidden sm:block` + khối card `sm:hidden`)                                      | 4–6 (nhân 3 trang) | Thấp                                          | **Phải đợi** — nếu field bảng đổi tên/đơn vị theo mục 3.2 sau khi đã viết responsive card riêng, phải sửa 2 nơi thay vì 1; nên làm responsive **sau khi** bảng đã chạy trên dữ liệu thật                                                                                       |
| Text nút đổi theo`busy` ở mọi nơi thiếu (mẫu đã có ở `training/page.tsx:199`) | `admin/page.tsx`, `datasets/page.tsx` (nút "Export dataset" đã có, nút "Create user" thì chưa)                                                                                                               | Nhân rộng mẫu "Đang lưu…" đã đúng ở 1 chỗ ra các nút còn thiếu                                                                                                         | 1                    | Cao (rẻ)                                      | **An toàn trước**                                                                                                                                                                                                                                                                                                |
| Ngưỡng validate mật khẩu ở`admin/page.tsx:104`                                         | `admin/page.tsx`                                                                                                                                                                                                      | Đổi`password.length < 4` thành `< 8` khớp `PASSWORD_MIN_LENGTH` backend thật                                                                                                | 0.2                  | Cao (rẻ, tránh round-trip lỗi 422 vô ích) | **An toàn trước** — đây là 1 hằng số client-side thuần (`4 → 8`), không đụng shape/field API nào, không phụ thuộc `api.createUser` đã nối thật hay chưa; xếp vào "Phải đợi" ở bản trước là sai vì lẫn với các mục thật sự phụ thuộc shape dữ liệu — nên làm ngay |
| Thumbnail trong danh sách                                                                    | `review/page.tsx`, `app/page.tsx` (Recent recordings)                                                                                                                                                               | Thêm`<img>` nhỏ dùng `mediaUrl(...)` trỏ `GET /demos/{id}/thumbnail?token=...`                                                                                               | 2–3                 | Trung bình                                    | **Phải đợi rõ ràng** — cần `mediaUrl()`/token thật hoạt động trước (mục 3.5), làm trước sẽ vô nghĩa vì chưa có ảnh thật để hiện                                                                                                                                                       |

### Nhóm B — CẦN thêm thư viện

| Hạng mục                                                                                | Thư viện đề xuất                                                          | Dung lượng (ước lượng, gzip)    | Lý do không tự làm được                                                                                                                                                                                                                                                                                                       | Công sức tích hợp (giờ)    | Ưu tiên                                                                                                                                             | An toàn trước / Phải đợi                                                                                                                                                   |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Toast có animation mượt, xếp chồng nhiều toast, tự quản lý hàng đợi           | `sonner` (hoặc tương đương nhẹ)                                       | ~5–7 KB                              | Tự viết được (đã đưa vào nhóm A ở bản đơn giản); chỉ cần thư viện nếu muốn animation stack/swipe-to-dismiss mượt như sản phẩm thương mại —**không bắt buộc**, chỉ nêu để so sánh                                                                                                        | 1–2 (thay thế bản tự viết) | Thấp — cân nhắc kỹ, bản tự viết ở nhóm A đã đủ dùng                                                                                    | **An toàn trước** nếu chọn dùng, nhưng khuyến nghị **không thêm** trong 3 tuần còn lại                                                                 |
| Chart tương tác thật (zoom, tooltip theo con trỏ chính xác, legend bấm ẩn/hiện) | `recharts`/`visx`                                                          | 40–90 KB tuỳ bộ phận import       | `Sparkline` tự viết (`ui.tsx:184-262`) là SVG tĩnh, không có tooltip/zoom — nếu cần tương tác sâu (hover ra đúng giá trị điểm, pan/zoom trajectory dài) thì viết tay sẽ tốn nhiều giờ hơn tích hợp thư viện                                                                                       | 4–8                            | Thấp —`Sparkline` hiện tại **đã đủ** cho mục đích xem nhanh, chỉ cần nếu người dùng thật sự phàn nàn thiếu tương tác | **Phải đợi hẳn** — chỉ nên cân nhắc sau khi nối xong Training thật (nếu Training được xây), vì hiện `/training` còn chưa nối được gì (mục 3.7) |
| Table nâng cao (sort theo cột, resize, virtualize hàng nghìn dòng)                   | `@tanstack/react-table` (+ `@tanstack/react-virtual` nếu cần virtualize) | ~15 KB (core, chưa tính virtualize) | 3 bảng hiện tại (`review`, `datasets`, `admin`) đều tự viết `<table>` tay, đơn giản, dữ liệu tối đa vài trăm dòng (46 demo mock, thật tế có thể nhiều hơn nhưng đã phân trang server-side) — virtualize/sort phức tạp chỉ cần khi dữ liệu thật sự lớn tới mức phân trang không đủ | 6–10                           | Thấp                                                                                                                                                 | **Phải đợi** — chỉ xét tới khi biết quy mô dữ liệu thật (số demo thật trong DB) sau khi nối xong, không nên đoán trước                                |

---

## 6. Lộ trình

Nguyên tắc: **nối dây trước, giao diện sau**; mỗi đợt xong phải xem được kết quả ngay trên trình
duyệt thật (không chỉ đọc code).

| Đợt       | Nội dung                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Phụ thuộc                                                                                                                                       | Ước lượng                                                                |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **0** | Thêm`NEXT_PUBLIC_API_ORIGIN`, tạo `.env.local` + `.env.local.example` (mục 3.8); chạy backend thật (`uvicorn`) + `npm run dev`, xác nhận CORS không chặn (mở tab Network kiểm tra 1 request `fetch` tay tới `/api/v1/tasks`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Backend đã chạy được (đã xác nhận ở các lượt trước — 198 test pass)                                                              | 0.5 ngày                                                                    |
| **1** | Auth: viết lại`api.login`/`api.me`/`setToken`/`getToken` gọi thật `POST /auth/login` (form-data) + `GET /auth/me`; thêm hàm `request()` trung tâm có refresh-and-retry 401 (mục 3.4) **và** bắt lỗi mạng để bật `ApiStatusContext.unreachable` (mục 3.9.c) — làm chung 1 lần vì cùng nằm trong 1 hàm. Test bằng tay: đăng nhập 3 tài khoản thật (cần tạo qua `scripts/create_admin.py` + `POST /users`, theo README backend mục "Chuẩn bị demo trực tiếp")                                                                                                                                                                                                                                                                                                                                                                           | Đợt 0                                                                                                                                           | 1–1.5 ngày                                                                 |
| **2** | Tasks + Users: nối`api.tasks` (kèm derive `title` từ `name`, mục 7 câu 3 — đã kết luận, không cần chờ quyết định gì thêm), `api.users`, `api.createUser`, `api.updateUser` qua adapter đổi tên field (mục 3.2). Đây là 2 nhóm object **ít lệch nhất**, làm trước để có luồng nối-test-xác nhận chạy trơn tru sớm                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Đợt 1                                                                                                                                           | 0.5–1 ngày                                                                 |
| **3** | **Backend trước, frontend sau, cùng 1 đợt:** thêm `operator_username`/`reviewer_username` vào `DemoResponse` (mục 7 câu 4 — đã kết luận, ~2 giờ backend: `selectinload` ở 3 điểm + field schema + test), rồi mới nối `api.demos` (đổi phân trang `page/page_size`, sửa `review/page.tsx` theo mục 3.3), `api.demo`, `api.summary` (bỏ field latency không có thật, sửa `app/page.tsx` phần Stat "Teleop latency" — xem mục 7 câu 5). Nối `mediaUrl()` thật (mục 3.5) **kèm cơ chế refresh token định kỳ cho video** (mục 3.9.a) ngay từ đầu, không để đợt sau — tránh phải quay lại sửa chỗ đã "xong". Xác nhận **video tua được** trên trình duyệt thật, không chỉ đọc code                                                                                                       | Đợt 2                                                                                                                                           | 2–2.5 ngày (thêm ~0.25 ngày so với bản trước vì gộp việc backend) |
| **4** | **Upload — form file-picker độc lập (mục 3.10), không động `/teleop`.** Route mới `/upload` + entry `Nav.tsx` (hiện cho mọi role). Form: dropdown task, `front` (bắt buộc), `wrist`/`trajectory` (optional), `accept` cho phép cả `.mp4`/`.mov`, progress bar (`XMLHttpRequest.upload.onprogress`), xử lý riêng `413`/`422`/`404`, điều hướng `/review/[id]` sau thành công. **Không cần sửa gì backend** — đã xác nhận `.mp4` lẫn `.mov` đều upload được nguyên trạng qua endpoint có sẵn (mục 3.10). Đây là đợt **bắt buộc** — không có nó, không có cách nào tạo demo thật qua giao diện (mục 3.6). Làm **trước** đợt Review (đảo thứ tự so với bản trước) để đợt sau có demo thật tạo qua UI làm dữ liệu test, không phải chỉ chạy seed script | Đợt 3 (cần`mediaUrl`/auth đã nối; độc lập với đợt Review)                                                                           | ~1–1.5 ngày, hoàn toàn frontend                                          |
| **5** | Demos ghi: tách`api.review` thành gọi `PATCH /trim` + `PATCH /label` + `POST /review` tuần tự (mục 3.3 mục 2), sửa `submit()` ở `review/[id]/page.tsx` để phân biệt bước nào lỗi; thêm nhánh UI chặn tự duyệt (mục 3.4); `api.reopen`, `api.deleteDemo`. Test tay bắt buộc: luồng 2 tài khoản (operator upload qua form `/upload` ở đợt 4, reviewer khác duyệt)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Đợt 3 (không phụ thuộc cứng vào đợt 4, nhưng nên làm sau để có demo thật từ`/upload` làm dữ liệu test thay vì seed script) | 1.5 ngày                                                                    |
| **6** | Datasets:`api.exports`→`api.createExport` với polling `building→ready` (mục 3.3 mục 4, 3.6), bỏ field `format`/DVC khỏi UI (mục 3.7). Test tay: tạo dataset thật, chờ zip đóng gói xong, tải về                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Đợt 5 (cần demo`approved` thật để test — giờ có thể tạo demo thật qua đợt 4 rồi duyệt ở đợt 5, thay vì chỉ seed script)    | 1 ngày                                                                      |
| **7** | **Đã chốt (mục 7 câu 2): ẩn `/training` khỏi `Nav.tsx:13`** (không giữ route thông báo "chưa hỗ trợ" — tránh giám khảo bấm vào rồi thấy trang trống ngay giữa buổi demo). `/teleop` **giữ nguyên** ở trạng thái "trang không nối được" (mục 3.7, bảng), chỉ cần đảm bảo badge/banner "Demo mode" đã hiện rõ; xoá `resetDemoData`/`db()`-store liên quan tới `Demo` (không xoá phần liên quan `User`/`TrainingRun` nếu code `/training` vẫn giữ lại, chỉ ẩn ở Nav)                                                                                                                                                                                                                                                                                                                                          | Có thể làm song song đợt 3-6 (chỉ là 1 dòng ẩn khỏi`Nav.tsx`; `/teleop` không phụ thuộc đợt nào)                              | 0.5 ngày                                                                    |
| **8** | Giao diện nhóm A (mục 5) — chọn theo ưu tiên "Cao" trước: focus-visible, text nút "Đang lưu…", ngưỡng validate mật khẩu, skeleton loading                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Sau đợt 1-7 (toàn bộ item nhóm A đánh "Phải đợi" cần đợt 3-6 xong trước)                                                           | 2–3 ngày rải theo hạng mục                                              |
| **9** | (Tuỳ chọn, không bắt buộc trong 3 tuần) Giao diện nhóm B nếu còn dư thời gian                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Sau đợt 8, và sau khi biết quy mô dữ liệu thật                                                                                            | —                                                                           |

Tổng ước lượng đợt 0–7 (phần "nối dây" bắt buộc, **đã gồm** form upload file-picker và 2 giờ
backend thêm field tên người): **~9–10 ngày làm việc** (giảm so với bản trước — phương án
`MediaRecorder`/trajectory buffer đã bị loại khỏi phạm vi, đợt upload rẻ hơn hẳn ~1.5–2 ngày, và
**không còn giờ backend nào** cho nhánh `.mov` vì đã xác nhận chạy được nguyên trạng). Đợt 8
(giao diện tối thiểu) thêm ~2–3 ngày. **Tổng ~11–13 ngày trên tổng ~15 ngày làm việc (3 tuần) —
còn dư khoảng 2–4 ngày biên độ cho phát sinh**, nhiều hơn hẳn so với bản trước (gần như không dư).
**Đề xuất thứ tự cắt nếu chậm tiến độ** (cắt từ dưới lên, không cắt tuỳ ý): (1) cắt đợt 9 (giao
diện nhóm B — vốn đã tuỳ chọn); (2) rút gọn đợt 8 xuống chỉ 3 hạng mục "Cao" (focus-visible, text
nút "Đang lưu…", ngưỡng mật khẩu — đều rẻ, đã đánh "An toàn trước"), bỏ skeleton/toast/card-list
mobile; (3) không có phương án cắt sâu hơn ở đợt 4 (upload) vì bản thân form đã là mức tối thiểu và
không còn phần backend nào để cắt — nếu vẫn thiếu thời gian, cắt tiếp vào đợt 6 (ví dụ hoãn polling
`building→ready` thành hiển thị đơn giản "đang đóng gói, tải lại trang sau").

**Nhận định chiến lược cần đưa ra bàn với nhóm:** với ràng buộc phạm vi đã chốt (không động tới
`/teleop`/`lib/teleop.ts`), `/teleop` **vẫn tách rời hoàn toàn** khỏi hệ thống thật sau khi hoàn
tất lộ trình này — demo ghi ở đó chỉ lưu `localStorage`, không bao giờ vào DB, không xuất hiện ở
`/review`/`/datasets`. Phần Teleop thật (MuJoCo + WebSocket, module rủi ro kỹ thuật cao nhất theo
`plan_backend.md` mục 4) **vẫn là khoảng trống chưa ai lấp** — đây là việc cần nhóm phân công
riêng cho người phụ trách Teleop (xem Phụ lục A cho tư liệu kỹ thuật đã kiểm chứng sẵn có), **không
nằm trong phạm vi nối dây** của tài liệu này. Không nên hiểu lộ trình đợt 0–8 ở trên là đã "giải
quyết" phần Teleop — nó chỉ nối các chức năng web thường; ai đọc tài liệu này cần biết rõ ranh
giới đó để không phân bổ nhầm người/thời gian.

---

## 7. Rủi ro và câu hỏi cần bạn quyết

> **7/7 mục đã chốt — không còn mục nào cần bạn quyết.** Câu 1, 2, 3, 5 có kết luận dứt khoát từ
> việc đọc code. Câu 4, 6, 7 — các đánh đổi chi phí/lịch trình/vận hành — đã được chốt ở vòng
> quyết định cuối cùng (xem kết luận trong từng mục).

1. **Số phận `/teleop` — đã chốt, không còn là câu hỏi mở.** Giữ nguyên "Demo mode" (canvas giả,
   không nối upload thật, không sửa `lib/teleop.ts`/`TeleopConsole.tsx`) đúng ràng buộc phạm vi.
   Đường tạo demo qua UI đi theo form upload file-picker độc lập ở `/upload` (mục 3.10, đợt 4) —
   tách biệt hoàn toàn khỏi `/teleop`. Phương án `MediaRecorder` ghi canvas đã bị loại, chuyển
   sang Phụ lục A làm tư liệu tham khảo cho người phụ trách Teleop sau này.
2. **Số phận `/training` — đã chốt, không còn là câu hỏi mở: ẨN khỏi `Nav.tsx:13`**, không giữ
   route với thông báo "chưa hỗ trợ". Lý do: lúc demo trực tiếp mà giám khảo tò mò bấm vào mục
   "Training" trên thanh nav rồi thấy trang trống/thông báo "chưa hỗ trợ" gây ấn tượng xấu hơn hẳn
   so với việc mục đó không tồn tại trên nav ngay từ đầu — im lặng tốt hơn phô ra một tính năng
   dở dang. Áp dụng luôn cho `datasets/page.tsx`: bỏ nút/field liên quan tới `format`/training
   pipeline theo đúng mục 3.7.
3. **Field `Task.title` map từ đâu? — đã kết luận, không còn là câu hỏi mở.** Đối chiếu 3 task
   thật duy nhất trong seed backend (`scripts/seed_tasks.py:26-50`: `pick_place`, `stack`, `push`)
   với 3 `id` frontend demo dùng (`lib/demo-data.ts:71-102`, cùng 3 giá trị) — khớp tuyệt đối,
   không có tên lạ/khó derive. Suy `title` từ `name` thuần client-side (`pick_place` → tách `_`
   → viết hoa chữ đầu mỗi từ → "Pick Place") là đủ dùng cho toàn bộ dữ liệu thật hiện có, **không
   cần đổi field `description` sang tiếng Anh ngắn gọn, không cần thêm field mới ở backend.** Nếu
   sau này có task tên khó derive đẹp (ví dụ viết tắt), xử lý bằng 1 bảng ánh xạ thủ công nhỏ
   ngay trong adapter frontend — vẫn không cần đụng backend.
4. **`operator_name`/`reviewer_name` — đã có khuyến nghị rõ, không còn để mở.** So chi phí 2
   hướng:
   - **(a) Thêm `operator_username` (và `reviewer_username`) vào `DemoResponse` ở backend.**
     `Episode` đã có sẵn quan hệ `operator`/`reviewer` tới `User` (`src/models/db.py:96-97`), nên
     về nguyên tắc chỉ là thêm field + đọc qua quan hệ có sẵn — nhưng **chi phí thật không phải
     "30 phút"**: đã kiểm tra `src/api/demos.py` và không có `selectinload`/`joinedload` nào cho
     `Episode.operator`/`Episode.reviewer` ở bất kỳ query nào (`list_demos`, `get_demo`,
     `get_dataset`) — SQLAlchemy async lazy-load quan hệ **ngoài** một `await` tường minh sẽ raise
     `MissingGreenlet`/`greenlet_spawn has not been called`, không tự động chạy được. Phải thêm
     `.options(selectinload(Episode.operator), selectinload(Episode.reviewer))` ở **3 điểm** gọi
     query episode ra `DemoResponse` (`list_demos`, `get_demo`, và `get_dataset` vì nó cũng trả
     `list[DemoResponse]`), cộng field mới trong schema, cộng test. Ước lượng thật: **~1.5–2 giờ**,
     không phải 30 phút, nhưng vẫn rẻ.
   - **(b) Để UI hiện UUID thô cho non-admin** (vì `GET /users` chỉ admin gọi được,
     `src/api/users.py:29` gắn `require_min_role(ADMIN)` cho cả router) — không tốn giờ backend
     nào, nhưng đổi lại: mọi màn hình operator/reviewer nhìn thấy demo của người khác (hàng đợi
     review, danh sách demo) sẽ hiện chuỗi UUID 36 ký tự thay vì tên người — hỏng trải nghiệm ở
     đúng những màn hình trung tâm nhất của sản phẩm (`review/page.tsx`, `app/page.tsx`), vĩnh
     viễn cho tới khi có ai đó sửa backend sau này.
   - **ĐÃ CHỐT: chọn (a).** Thêm `operator_username` và `reviewer_username` vào `DemoResponse` ở
     backend (~2 giờ: `selectinload` ở 3 điểm gọi query + field schema + test). Lý do: 2 giờ đổi
     lấy việc mọi màn hình trung tâm (`/review`, trang chủ) hiện tên người thay vì chuỗi UUID 36
     ký tự; chủ backend là người thực hiện nên không phải chờ ai. **Ràng buộc lộ trình: việc
     backend này phải làm TRƯỚC khi nối `api.demos` trong cùng đợt 3**, nếu không sẽ phải sửa
     phần hiển thị hai lần.
5. **`Stat` "Teleop latency (p50)" ở `app/page.tsx:82-87` — đã kết luận, không còn là câu hỏi
   mở: thay bằng ô "Chờ duyệt".** `DemoSummaryResponse.by_status` (`schemas.py:262`) là
   `dict[str, int]` đếm theo từng giá trị `status` thật (`recording/recorded/labeled/approved/ rejected`, xem cảnh báo enum ở mục 3.2) — dùng luôn field này, không cần backend thêm gì.
   Công thức: `pending = (summary.by_status["recorded"] ?? 0) + (summary.by_status["labeled"] ?? 0)` — cộng 2 trạng thái "đã ghi nhưng chưa có quyết định duyệt cuối cùng" (`recorded`: chưa gắn
   nhãn; `labeled`: đã gắn nhãn, chờ approve/reject). Label ô đổi thành `"Chờ duyệt"`, bỏ `hint`
   p95 hiện tại (không còn ý nghĩa), `tone` có thể giữ nguyên logic cảnh báo nhưng đổi ngưỡng theo
   số lượng demo tồn đọng thay vì latency (ví dụ `tone="warn"` khi `pending > 10`, cần tự chọn
   ngưỡng hợp lý theo quy mô dữ liệu thật, không suy đoán con số ở đây).
6. **`allow_self_review` — ĐÃ CHỐT: giữ `FALSE`, luôn dùng 2 tài khoản khi test, KHÔNG bật cờ
   tạm.** `scripts/seed_demos.py` đã tạo sẵn user reviewer riêng nên tài khoản thứ hai luôn có
   sẵn, không cần bật cờ. Quan trọng hơn — **nguyên tắc chung, không chỉ riêng quyết định này**:
   bật cờ khi test nghĩa là đang test một cấu hình KHÁC với cấu hình lúc demo — đúng loại sai
   lệch đã gây ra 2 sự cố trong dự án này (venv sai phiên bản, `.env` trỏ nhầm DB). Luôn test
   đúng cấu hình sẽ dùng lúc demo, không bật cờ/tắt kiểm tra "tạm thời" vì tiện.
7. **Video demo lấy từ đâu để upload qua form `/upload` — ĐÃ CHỐT: dùng cả hai, mỗi thứ một mục
   đích.**
   - **Lúc phát triển và test:** file `.mp4` sẵn có ở `data/episodes/<id>/front.mp4` do
     `scripts/seed_demos.py` sinh (dựng bằng `ffmpeg testsrc`, xem `scripts/seed_demos.py:56` trở
     đi). Luôn có sẵn sau khi seed, không phải chuẩn bị gì thêm.
   - **Lúc demo trực tiếp:** screen-record trang `/teleop` bằng Game Bar (Windows) hoặc OBS ra
     `.mp4`, rồi upload file đó qua form `/upload`. Không đụng một dòng code robot nào (chỉ ghi
     màn hình bằng công cụ ngoài, không sửa `lib/teleop.ts`/`TeleopConsole.tsx`) nhưng câu chuyện
     trước mặt người xem trở nên liền mạch: điều khiển robot mô phỏng → ghi lại phiên đó → nạp
     vào hệ thống → tài khoản khác duyệt → gom thành dataset → tải zip về. **Lưu ý kỹ thuật quan
     trọng:** `/teleop` và backend vẫn tách rời (đúng như mục 3.7/3.9.b) — đây chỉ là cách trình
     bày để không có khoảng trống phải giải thích giữa buổi demo, **KHÔNG được hiểu là đã nối
     được `/teleop`** vào backend.
   - **Video quay bằng điện thoại (`.mov`):** giữ làm phương án dự phòng — iPhone quay ra `.mov`
     mặc định, đã xác nhận upload được nguyên trạng, không cần chuyển đổi gì (mục 3.10).
   - Đã loại: `public/demo/front.webm`/`wrist.webm` (`frontend/public/demo/`) là file **`.webm`**,
     không phải `.mp4` — mâu thuẫn với mục 3.5 (2 file này chỉ là asset tĩnh cho UI demo cũ) và sẽ
     bị `has_mp4_magic_bytes()` chặn ngay (EBML, không có marker `ftyp`) — không dùng được.

## Phụ lục A — NGOÀI PHẠM VI: tư liệu cho người làm Teleop sau này

> Nội dung dưới đây (mục A.1, A.2) là phân tích và thực nghiệm cho phương án nối `/teleop` thành
> nguồn upload thật qua `MediaRecorder`. Phương án này đã **bị loại khỏi kế hoạch chính** vì vi
> phạm ràng buộc phạm vi đã chốt (không động tới `lib/teleop.ts`/`TeleopConsole.tsx`, không dùng
> dữ liệu sinh ra từ simulator) — xem mục 3.10 để biết hướng đã chọn thay thế (form upload
> file-picker độc lập). Giữ nguyên nội dung ở đây vì phần thí nghiệm ffprobe/webm thiếu duration
> (Segment size unknown, hex dump, so sánh remux vs transcode) là kiến thức đã kiểm chứng thực
> nghiệm, có giá trị cho ai làm phần Teleop thật (MuJoCo/WebSocket) về sau — khi đó việc ghi
> video từ canvas/stream thật và xử lý container webm streaming sẽ lại cần tới đúng phân tích này.

### A.1 `trajectory.json` thật, không chỉ video

Bản trước của mục 3.10 chỉ nghĩ tới việc upload 2 file mp4/webm — bỏ sót phần giá trị nhất của
phương án này. Đọc lại `lib/teleop.ts` và đối chiếu với validate thật ở backend cho thấy:

**Dữ liệu trạng thái đã được sinh ra mỗi tick, nhưng KHÔNG được tích luỹ thành mảng.**
`tick()` (`lib/teleop.ts:576-645`) tính `joints = pseudoJoints(this.world)` mỗi 33ms và gửi qua
callback `onFrame?.({ state: joints, action: joints, ee: [...], ... })` (dòng 620-630) — đúng là
dữ liệu "thật" theo nghĩa nó phản ánh state của simulator, không phải số giả tĩnh. Nhưng
`TeleopConsole.tsx:79-84` — nơi duy nhất gán `client.onFrame` — chỉ dùng callback này để
`setFrame(state)` (vẽ panel latency hiện tại) rồi **vứt luôn**, không đẩy vào mảng nào cả.
`stopRecording()` (`teleop.ts:521-573`) cũng không đọc lại lịch sử tick — nó chỉ tính `p50/p95`
từ `recLatencies` (mảng latency, không phải mảng state/action) và ghi 1 object `Demo` tổng hợp
duy nhất, không có trường trajectory nào.

**Kết luận: "gửi kèm trajectory.json thật" không phải việc chép dữ liệu có sẵn — phải xây mới một
bộ đệm ghi.** Cần thêm: (1) một mảng (ví dụ `recBuffer: number[][]`) trong `TeleopClient`, push
`joints` vào đó mỗi tick khi `this.recording === true` (ngay cạnh chỗ đã push `recLatencies`,
`teleop.ts:613`); (2) ở `stopRecording(true)`, dựng object `{ action: recBuffer, ... }` và giữ lại
(trả về hoặc set lên instance) để nơi gọi (`TeleopConsole`) lấy ra đóng gói `Blob` JSON.

**Shape có khớp validate backend không:** `_validate_trajectory()` (`src/api/demos.py:123-146`)
chỉ đọc key `data["action"]` — nếu có, phải là `list[list[...]]`, mỗi hàng dài đúng
`task.action_dim` (`src/models/schemas.py` field trên `TaskResponse`, đối chiếu mục 3.2). `joints`
hiện tại là `pseudoJoints(this.world)` (định nghĩa ở nơi khác trong `teleop.ts`) — **cần đọc thêm
để xác nhận độ dài mảng này khớp `action_dim` thật của 3 task seed** (`pick_place`/`stack`/`push`,
`scripts/seed_tasks.py`); nếu simulator dùng số chiều khớp cố định khác `action_dim` khai trong DB,
phải hoặc pad/cắt cho khớp, hoặc chấp nhận model chỉ đúng giả lập cho 1 action_dim rồi báo lệch ở
task khác. Các key khác trong object (`state`, `ee`) **không bị validate** — có thể gửi kèm tuỳ ý,
không rủi ro 422, nhưng cũng không được backend dùng tới ở bước upload (chỉ lưu nguyên file).

**Giá trị thật của việc này:** nếu làm, file zip dataset xuất ra sẽ chứa `trajectory.json` có
action thật lấy từ input người vận hành qua bàn phím/gamepad (`InputCollector` trong
`teleop.ts`), không phải file trắng/giả — đây là điểm khiến `/teleop` + upload trở thành **nguồn
dữ liệu cho imitation learning** thật sự (state-action pairs có thể học), chứ không chỉ là một
trang quản lý clip video. Không làm phần này, dataset export vẫn "hợp lệ" về mặt schema (trường
`trajectory` là optional) nhưng rỗng về giá trị huấn luyện.

**Ước lượng công sức thêm:** buffer + gửi kèm trong `FormData` (field `trajectory`, cùng lúc với
`front`/`wrist`) là việc nhỏ về khối lượng code (~1-2 giờ: thêm mảng, push mỗi tick, serialize
`JSON.stringify` thành `Blob` lúc `onstop`) nhưng **cần xác nhận riêng phần đối chiếu action_dim**
ở trên trước khi coi là xong — nếu lệch chiều, phải sửa cách `pseudoJoints` sinh ra mảng, tốn thêm
thời gian không cố định trước. Gộp vào ước lượng frontend ở bảng dưới đợt 5 (mục 6): cộng thêm
~0.5 ngày so với con số cũ.

### A.2 Thẩm định phương án: MediaRecorder ghi canvas → `POST /demos/upload` trực tiếp

Đây là phương án gộp giải quyết cả mục "Upload" (3.6) lẫn mục "`/teleop` không nối được" (3.7)
làm một — đáng đọc kỹ trước khi chốt lộ trình mục 6.

**Ý tưởng:** thay vì `stopRecording()` ghi vào `localStorage` (vấn đề 3.9.b), dùng
`HTMLCanvasElement.captureStream(30)` trên 2 canvas đã có sẵn (`TeleopConsole.tsx:212-232`,
`frontRef`/`wristRef`) làm nguồn cho 2 `MediaRecorder`, gom `Blob` khi dừng ghi, rồi `POST` thẳng
multipart lên `/demos/upload` bằng `fetch` (không qua thẻ `<video>` nên **không vướng** giới hạn
"không gửi được header" đã nêu ở mục 3.5 — `fetch` gửi header `Authorization` bình thường).

**Đã tự kiểm chứng bằng thực nghiệm** (không suy đoán): dùng `ffmpeg` cài sẵn trong môi trường
này để mô phỏng 2 kiểu webm khác nhau và chạy `ffprobe` y hệt `src/services/media.py:69-90` sẽ
chạy khi upload thật.

```
ffmpeg -f lavfi -i testsrc=size=64x64:rate=10 -t 3 -c:v libvpx -f webm normal.webm
ffmpeg -f lavfi -i testsrc=size=64x64:rate=10 -t 3 -c:v libvpx -f webm -live 1 live.webm
```

`normal.webm` (mux thường, biết trước tổng thời lượng — giống 1 file ghi sẵn rồi mux lại) →
`ffprobe -show_format` trả `"duration": "3.000000"` bình thường.

`live.webm` (`-live 1`: mux kiểu streaming, **đúng cách `MediaRecorder` của trình duyệt tạo file**
— ghi tăng dần qua `ondataavailable`, không biết trước tổng thời lượng lúc bắt đầu, Segment
element mang **kích thước "unknown"** — hex dump đầu file xác nhận: `1853 8067 01 ff ff ff ff ff ff ff` tại offset Segment ID, `01 ff ff ff ff ff ff ff` là giá trị "unknown size" chuẩn EBML) →
`ffprobe -show_format` **hoàn toàn không có key `"duration"`** trong JSON trả về (đã in ra JSON
đầy đủ, chỉ có `filename/nb_streams/format_name/size/tags`, không có `duration`).

Đối chiếu với `src/services/media.py:102-105`:

```python
try:
    duration_s = float(data["format"]["duration"])
except (KeyError, TypeError, ValueError) as exc:
    raise MediaProbeError("Không đọc được duration từ ffprobe") from exc
```

`KeyError` chắc chắn xảy ra → `probe_video()` raise `MediaProbeError` → router bắt và trả
**422** (`src/api/demos.py:216-219`) → **mọi lần upload từ `MediaRecorder` sẽ bị từ chối ngay**,
không phải rủi ro lý thuyết mà là lỗi chắc chắn xảy ra nếu ghép thẳng không xử lý gì thêm.

**Đã thử nghiệm 2 cách vòng — CHỐT một hướng: transcode sang mp4, không remux webm.**

Cách 1 (đã thử, có tác dụng nhưng KHÔNG chọn): `ffmpeg -i live.webm -c copy remuxed.webm` —
`-c copy` chỉ viết lại container webm (copy nguyên stream, không giải mã/mã hoá lại — cực nhanh),
`ffprobe` trên `remuxed.webm` trả lại `"duration": "3.000000"` chính xác. Về mặt kỹ thuật việc này
chứng minh vấn đề "thiếu duration" có cách vá rẻ — nhưng **đi kèm hệ quả không chọn được ở đây**:
giữ webm nghĩa là phải đổi `storage.FRONT_FILENAME`/`WRIST_FILENAME` (hiện hardcode `.mp4`,
`src/services/storage.py:26-27`) và đổi `media_type="video/mp4"` hardcode ở
`src/api/demos.py:399` — đụng vào cấu trúc file zip dataset đã ghi cứng trong tài liệu
(`detail_backend_withoutRobot.md` mục 4), tức là **không rẻ** như con số CPU của riêng lệnh remux
gợi ý.

Cách 2 (chọn): `ffmpeg -i live.webm -c:v libx264 out.mp4` — transcode thật (giải mã VP8 rồi mã hoá
lại H.264), dựng lại container mp4 từ đầu nên `duration` luôn có sẵn đúng chuẩn, **không cần bước
remux riêng nữa** — 1 lệnh làm cả 2 việc (đổi định dạng + có duration). Giữ nguyên hợp đồng tên
file `.mp4` và `media_type="video/mp4"` sẵn có — không phải sửa `storage.py`/dòng
`media_type` ở `demos.py:399`.

**Chi phí CPU của transcode — cho video 256-640px, dài 10-30 giây:** đây là clip rất nhỏ theo
chuẩn video (độ phân giải thấp, thời lượng ngắn), khác hẳn "transcode video HD dài" mà trực giác
"tốn hơn hẳn" thường ám chỉ. `libx264` ở preset mặc định xử lý độ phân giải này nhanh hơn thời gian
thực nhiều lần trên CPU thông thường — encode 10-30 giây nội dung 256-640px dự kiến mất dưới vài
giây xử lý, không phải điểm nghẽn đáng lo so với các bước I/O khác của upload (lưu file, ffprobe,
sinh thumbnail). Kết luận: "tốn hơn hẳn remux" đúng về tương đối (transcode luôn tốn CPU hơn copy
container), nhưng ở quy mô clip demo này, chênh lệch tuyệt đối nhỏ, không phải lý do để chọn remux
và gánh chi phí sửa storage/media_type.

**Đánh giá theo từng câu hỏi:**

| Câu hỏi                                                                        | Đánh giá                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Công sức phía frontend                                                        | ~1–1.5 ngày. Hạ tầng đã có sẵn phần lớn:`TeleopConsole` đã có 2 `<canvas>` (`frontRef`/`wristRef`), state `recording`, nút Start/Stop đã nối UI (`TeleopConsole.tsx:246-263`). Việc cần thêm: gọi `canvas.captureStream(30)` lúc bắt đầu ghi, tạo `MediaRecorder` (kiểm `MediaRecorder.isTypeSupported('video/webm;codecs=vp8')` trước, xem hàng dưới), gom `Blob` ở `onstop`, dựng `FormData` (`task_name`, `front`, `wrist`), `fetch(POST /demos/upload, {headers: {Authorization}, body: formData})`, thay `stopRecording()` hiện tại (ghi `localStorage`) bằng luồng này, cộng UI trạng thái "Đang tải lên…" + xử lý `413`/`422` (đúng phần "Upload" đã nêu ở mục 3.6, giờ được giải quyết trong cùng 1 khối việc thay vì làm form riêng)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Công sức phía backend                                                         | ~0.5–1 ngày,**nhiều hơn** phần bạn ước lượng "chỉ nới whitelist magic bytes". **Đúng 2 việc, theo đúng THỨ TỰ trong pipeline upload** (thứ tự sai sẽ chặn nhầm — xem cảnh báo dưới): (1) `has_mp4_magic_bytes` (`src/services/media.py:35-43`, offset 4 byte `ftyp`) — thêm nhánh nhận diện EBML `1A 45 DF A3` ở offset 0, đây là bước **đầu tiên** file chạm phải trong pipeline (`src/api/demos.py:184,199` gọi ngay sau khi lưu file tạm) nên phải sửa trước tiên; (2) **transcode sang mp4 bắt buộc** (`ffmpeg -i in.webm -c:v libx264 out.mp4`, xem lựa chọn transcode-thay-remux ở trên) chèn vào **giữa** bước (1) và `probe_video()` — không làm bước này thì dù đã sửa (1), upload webm từ `MediaRecorder` vẫn 422 ở `probe_video()` vì thiếu duration, đây là phát hiện **quan trọng nhất** của bảng này. Vì chọn transcode (không remux), **không cần đụng** `storage.FRONT_FILENAME`/`WRIST_FILENAME` (`src/services/storage.py:26-27`, vẫn `.mp4`) lẫn `media_type="video/mp4"` ở `src/api/demos.py:399` — cả 2 giữ nguyên                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Chất lượng video canvas ghi ra có đủ nhìn không                          | **Đánh giá bản trước sai — "đồ hoạ vector đơn giản nên không mờ/vỡ" nhầm lẫn giữa độ phức tạp nội dung và độ phân giải nguồn.** `width={256} height={256}` (`TeleopConsole.tsx:214-215`, đã xác nhận đây là **kích thước buffer pixel thật** của canvas, không phải CSS hiển thị — `paint()` ở dòng 483-489 còn set `canvas.width/height` lại theo bitmap để tránh lệch) nghĩa là `captureStream()` ghi đúng 256×256 pixel, bất kể nội dung vẽ đơn giản hay phức tạp. Khi trình chiếu lên máy chiếu/màn hình lớn (1920px trở lên), 256px phải phóng ~7.5 lần — mọi cạnh hình hộp/đoạn thẳng (dù là vector lúc vẽ) đã bị rasterize thành pixel cố định trong canvas **trước khi** ghi hình, phóng to sau đó chỉ nội suy pixel có sẵn (nearest/bilinear tuỳ trình chiếu), tạo hiệu ứng rỗ/mờ cạnh rõ rệt — không liên quan gì tới việc nội dung là vector hay video thật. Đây là vấn đề độ phân giải nguồn, không phải vấn đề nén ảnh. **Đề xuất: nâng `width`/`height` của cả 2 canvas lên 512 (front) — giữ nguyên kích thước hiển thị CSS (layout hiện tại dùng `aspect-square` co giãn theo khung, không phụ thuộc thuộc tính `width`/`height` gốc) — rồi `captureStream()` từ buffer lớn hơn.** Ảnh hưởng CPU: `drawScene`/`drawBox`/`drawSegment` (`lib/teleop.ts:325-411`) vẽ vài chục primitive hình học phẳng mỗi frame — chi phí canvas 2D chủ yếu theo số lệnh vẽ, gần như không đổi theo kích thước buffer (không có shader/pixel-shading nặng); tăng từ 256² lên 512² (gấp 4 lần diện tích) chủ yếu tăng chi phí encode VP8 của `MediaRecorder`, không tăng chi phí `drawScene`, ước lượng CPU encode tăng nhưng vẫn nhẹ ở độ phân giải này trên máy hiện đại — nên đo thử tay 1 lần trước khi chốt (không suy đoán số cụ thể). `captureStream(30)` khớp đúng `CONTROL_HZ=30` (`lib/teleop.ts:63`), không cần giảm khung hình |
| Tương thích trình duyệt                                                     | Chrome/Edge (Chromium): hỗ trợ đầy đủ`MediaRecorder` + `video/webm` (vp8/vp9/opus) từ lâu, không có rủi ro. Firefox: tương tự, hỗ trợ tốt. Safari: **rủi ro thật** — theo hiểu biết chung (không chạy được Safari trong môi trường này để tự kiểm chứng, **cần kiểm tra thêm** bằng tay trên máy có Safari/WebKit trước khi cam kết): Safari chỉ thêm `MediaRecorder` từ bản 14.1 trở lên, và historically ưu tiên `video/mp4` (H.264) hơn `video/webm` cho recording — `MediaRecorder.isTypeSupported('video/webm')` có thể trả `false` trên Safari tuỳ phiên bản. Bắt buộc phải `isTypeSupported()` runtime-check trước khi tạo `MediaRecorder`, và có fallback UI ("Trình duyệt này không hỗ trợ ghi trực tiếp — dùng Chrome/Edge/Firefox") thay vì giả định luôn thành công                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `ffprobe` đọc được `duration`/`fps` từ webm `MediaRecorder` không | **Đã kiểm chứng thực nghiệm ở trên: KHÔNG, nếu không xử lý trước.** Webm dạng streaming (đúng cách `MediaRecorder` ghi) thiếu hẳn `format.duration` trong output `ffprobe`, không phải "có thể thiếu" mà chắc chắn thiếu (Segment size "unknown" theo chuẩn EBML streaming). `fps` (qua `avg_frame_rate`) **vẫn đọc được bình thường** kể cả không xử lý gì — chỉ riêng `duration` là vấn đề                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Hệ quả nếu không xử lý, và cách vòng                                    | Hệ quả:**mọi upload từ `MediaRecorder` bị 422 ngay ở bước `probe_video()`**, không phải lỗi hiếm — xảy ra 100% các lần thử nếu không xử lý. Ảnh hưởng dây chuyền: `Episode.duration_s` không có giá trị để lưu, `TrimRequest` validate `0 <= trim_start_s < trim_end_s <= duration_s` (`schemas.py:220-227`, `src/api/demos.py` router) không có `duration_s` để so sánh, toàn bộ luồng trim/review đứng hình ngay từ bước upload. **Cách vòng đã kiểm chứng và CHỐT: transcode `ffmpeg -c:v libx264` sang mp4** (không remux — xem lựa chọn ở trên) — dựng lại container từ đầu nên duration luôn có, giải quyết dứt điểm. **Thứ tự đúng của pipeline upload sau khi sửa, phải làm theo đúng trình tự này** (sai thứ tự là bẫy thật — nếu vẫn probe/check mp4 trước khi nhận webm, file bị chặn ngay bước đầu, không bao giờ tới bước transcode): (1) lưu file tạm lên đĩa (đã có, `_save_upload_chunked`) → (2) `has_mp4_magic_bytes` **đã sửa để chấp nhận EBML** (magic bytes webm) → (3) validate trajectory nếu có (không phụ thuộc video) → (4) **transcode webm→mp4** (bước mới, chèn ở đây) → (5) `probe_video()` chạy trên **file mp4 đã transcode**, không phải file webm gốc → (6) sinh thumbnail, tính size, ghi DB (như hiện tại). Nếu giữ nguyên thứ tự cũ (magic bytes chỉ nhận mp4 → probe ngay) thì bước (2) chặn webm trước khi kịp tới bước transcode — không bao giờ đạt được lợi ích của bước (4)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

**Kết luận: NÊN làm**, với điều kiện đi kèm transcode webm→mp4 phía backend theo đúng thứ tự
pipeline nêu trên (không phải "chỉ nới whitelist" như ước lượng ban đầu — cần thêm ~0.5 ngày so
với ước lượng gốc), **cộng thêm phần trajectory.json thật ở mục A.1** (thêm ~0.5 ngày frontend). Phương án này giải
quyết đồng thời: (1) lỗ hổng "không có cách nào tạo demo qua UI" (mục 3.6, 3.9.b), và (2) biến
`/teleop` từ "đồ trang trí không vào DB thật" (mục 3.7) thành nguồn dữ liệu thật — đáng giá hơn
xây riêng 1 form file-picker chung chung, vì trình giả lập canvas hiện tại là **nguồn ghi hình
duy nhất** ứng dụng có (không có camera thật, không có file video nào sẵn có để "chọn" qua file
picker ngoài 2 file tĩnh demo ở `public/demo/`).

**Trạng thái hiện tại: KHÔNG áp dụng.** Ràng buộc phạm vi đã chốt loại bỏ hoàn toàn phương án này
khỏi kế hoạch chính — đợt 4 (mục 6) dùng form upload file-picker độc lập (mục 3.10), không động
tới `/teleop`. Giữ lại nội dung này làm tư liệu tham khảo cho người phụ trách Teleop khi module đó
được xây dựng thật (MuJoCo/WebSocket) — lúc đó việc ghi hình từ nguồn thật và xử lý webm streaming
sẽ là vấn đề cần giải lại, và các thực nghiệm ở trên (A.1, A.2) vẫn còn nguyên giá trị.

---
