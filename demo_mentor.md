# Demo cho Mentor tối nay — TeleCollect (Gate 2)

> Ghi chú: đây là *note thao tác* để biết nên bấm gì / nói gì khi demo trực
> tiếp cho mentor, không phải tài liệu kiến trúc chính thức. Nguồn: `README.md`,
> `SIM_BACKEND_FRONTEND_INTEGRATION.md`, `docs/auto_labeling_mvp.md`,
> `docs/toolhang_integration.md`, kết quả test thật chạy sáng nay
> (262/262 pass, bao gồm sim/robot).

## 1. Định vị trước khi demo (nói 30s đầu)

TeleCollect không phải chatbot/LLM-agent — đây là nền tảng **thu thập dữ liệu
demonstration cho imitation learning robot** (đề bài RAV-12). "User flow chính"
ở đây là: vận hành viên điều khiển robot ảo (hoặc chạy scripted collection) →
hệ thống ghi lại episode → reviewer duyệt/gắn nhãn → gom thành dataset tải về.
End-to-end thật, không mock — chạy trên robosuite/MuJoCo thật, video ghi thật,
DB thật.

## 2. Chuẩn bị trước giờ demo (làm trước, không làm live)

```bash
python -m uvicorn src.main:app --reload --port 8000   # terminal 1
cd frontend && npm run dev                              # terminal 2 (hoặc `make dev` chạy cả 2)
```

- Đã có sẵn tài khoản `admin` (script `create_admin`) + 1 tài khoản `reviewer1`
  (xem README mục "Chuẩn bị demo trực tiếp") — **cần 2 tài khoản khác nhau**
  để demo review vì hệ thống chặn tự duyệt demo do chính mình upload
  (`ensure_not_self_review`).
- Đã seed sẵn task (`pick_place`/`lift`/`can`/`square`/`tool_hang`) và một ít
  demo mẫu (`seed_demos.py`) để trang review/datasets không trống trơn.
- Test lại nhanh 1 lần: `python -m pytest -q` → kỳ vọng xanh hết trước khi lên demo.

## 3. Kịch bản demo (theo đúng 1 user flow chính, end-to-end)

### Bước 1 — Đăng nhập, phân quyền (`/login`)
- Login bằng `admin` → cho mentor thấy 3 role thật (`operator`/`reviewer`/`admin`),
  không phải giả lập — vào `/admin` chỉ đạo diễn khi role đúng, tạo thử 1 user
  operator ngay trên UI.

### Bước 2 — Thu dữ liệu (chọn 1 trong 2 cách, nên demo cả 2 nếu kịp giờ)

**Cách A — Teleoperation thật (`/teleop`)**
- Chọn task, kết nối phiên WebSocket vào robosuite/MuJoCo thật (không phải
  simulator canvas giả như bản demo cũ).
- Điều khiển bằng bàn phím/chuột, xem 2–3 camera (`review_front`, `birdview`,
  `robot0_eye_in_hand`) render realtime.
- Bấm ghi (record) một đoạn ngắn → dừng lại giữ episode.

**Cách B — Scripted collection (`/scripted`)**
- Chọn task `tool_hang` (task 2 giai đoạn: cắm khung móc rồi treo cờ-lê —
  điểm nhấn kỹ thuật, xem `docs/toolhang_integration.md`), chọn noise preset,
  bấm Start.
- Trang tự thu 1 batch episode, tự chấm điểm (`scores.jsonl`), người vận hành
  đánh dấu Đạt/Loại kèm lý do (`labels.jsonl`) — đây chính là vòng lặp
  human-in-the-loop cho auto-labeling MVP (`docs/auto_labeling_mvp.md`).

### Bước 3 — Review (`/review`, `/review/[id]`)
- Đăng xuất, đăng nhập lại bằng `reviewer1`.
- Vào danh sách demo vừa thu, mở chi tiết 1 demo: xem lại video (tua được thật —
  HTTP Range, không phải tải hết mới xem), trim đầu/cuối trên timeline.
- Gắn nhãn success/failure, bấm Approve/Reject → cho mentor thấy state machine
  thật (`recorded → labeled → approved/rejected`, sai transition trả 409 chứ
  không âm thầm bỏ qua).

### Bước 4 — Datasets (`/datasets`)
- Tạo 1 dataset mới, gom các demo `approved` vừa duyệt, chọn task + có/không
  gộp bản fail.
- Cho xem trạng thái `building → ready` (đóng gói zip chạy nền, không block
  request), rồi tải file zip về (cũng có Range/resume thật).

### Bước 5 (tuỳ thời gian) — Upload thủ công (`/upload`)
- Trang mới thêm: upload video có sẵn (mp4) làm demo, cùng đi qua guardrail
  (magic bytes check, giới hạn dung lượng, ffprobe lấy metadata thật) — cho
  thấy hệ thống không tin dữ liệu client gửi lên mù quáng.

## 4. Bằng chứng "chạy thật, không mock" (nói khi mentor hỏi xoáy)

- `python -m pytest -q` → **260 passed, 2 skipped** (2 skip là 2 test sim nặng,
  mặc định tắt để chạy suite nhanh).
- `TELECOLLECT_RUN_SIM_TESTS=1 python -m pytest -q` → **262 passed, 0 skipped,
  0 failed** — bao gồm cả 2 test dựng environment robosuite thật và chạy 1
  episode ToolHang thật (~15s), không mock MuJoCo.
- `npm run build` (frontend) → build production thành công, TypeScript sạch,
  toàn bộ route dựng được.
- Video/zip stream qua HTTP Range thật (không phải giả lập tua) — có test
  riêng chống rò rỉ file handle trên Windows
  (`tests/test_demos_playback.py::test_episode_dir_deletable_after_streaming_no_handle_leak`).

## 5. Đối chiếu với checklist Gate 2 — cái nào đã có, cái nào còn thiếu

| Deliverable | Trạng thái | Ghi chú |
| --- | --- | --- |
| MVP demo video 3 phút, end-to-end | ⚠️ Chưa quay | Kịch bản ở mục 3 chính là nội dung để quay — quay **sau** buổi demo tối nay hoặc quay lại luôn nếu mentor đồng ý dùng bản ghi màn hình demo làm video nộp. |
| Architecture diagram | ✅ Có | `docs/architecture_diagram.md` — cần xác nhận còn khớp với hiện trạng (đã có Teleop/Sim thật) trước khi nộp, không chỉ dùng bản Gate 1. |
| Repo ≥ 10 PR merged | ❓ Cần tự kiểm | Không thuộc phạm vi kiểm tra code của tôi — kiểm bằng `gh pr list --state merged` trước khi báo cáo con số cho mentor. |
| README.md (setup, env vars, sample queries) | ✅ Có, khá đầy đủ | `README.md` đã có hướng dẫn cài đặt, chạy server, seed data, chuẩn bị demo 2 tài khoản. Có thể thiếu phần liệt kê rõ **toàn bộ env vars** (`.env.example`) và "sample queries" dạng request mẫu — nên bổ sung 1 bảng liệt kê từng biến trong `.env.example` kèm ý nghĩa, và vài lệnh `curl` mẫu cho các endpoint chính. |
| Eval evidence ≥ 5 test case manual + output thực tế | ⚠️ Chưa gom thành tài liệu riêng | Dữ liệu thô đã có sẵn: mỗi lần scripted collection tự sinh `scores.jsonl` (điểm auto-label kèm lý do) + `labels.jsonl` (quyết định người thật) — đây chính là eval evidence tốt nhất vì có cả input, score tự động, và quyết định người, không phải bịa. Việc còn thiếu: **trích 5 case cụ thể ra một file** (ví dụ `eval_evidence.md`), mỗi case gồm task/seed, score, lý do auto-label, quyết định người, ảnh/video minh chứng. |

## 6. Rủi ro khi demo live (chuẩn bị phương án B)

- Robosuite/MuJoCo cần GPU render — nếu máy demo không có GPU rời hoặc chưa
  set `SHIM_MCCOMPAT`, render Teleop/scripted sẽ chậm hẳn (theo README: 62s →
  8.3s khi đúng GPU). Có thể quay sẵn 1 đoạn video ngắn của bước 2 làm dự phòng.
- Nhớ `rm -rf data/` + seed lại nếu schema DB vừa đổi mà chưa migrate (SQLite
  không tự migrate cột mới) — kiểm tra trước giờ demo, không phát hiện live.
- `.git/hooks/pre-push` (nộp AI log) hiện lỗi trên Windows, phải push kèm
  `--no-verify` — không liên quan demo tối nay nhưng cần biết nếu mentor hỏi
  về AI log lúc push code.
