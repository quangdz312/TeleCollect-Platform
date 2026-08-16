# Gate 2 — TeleCollect (Team NEURA)

Nền tảng thu thập dữ liệu trình diễn cho robot: người vận hành điều khiển tay
máy Panda mô phỏng hoặc để chính sách scripted tự chạy, mọi episode đều được
ghi lại, chấm điểm tự động, người duyệt xem lại rồi đóng gói thành dataset
huấn luyện.

---

## 🎥 Video demo MVP

**Xem trên Google Drive (không cần clone repo):**

### 👉 https://drive.google.com/file/d/1Nhpw48HzYSTEMAM8HiRT_9rRvc0BOLiJ/view?usp=sharing

Bản trong repo: [`demo_mvp.mp4`](demo_mvp.mp4) — 21 MB.

Video đi hết luồng chính: đăng nhập → thu demo bằng điều khiển tay với ba góc
camera → sinh data tự động cho task ToolHang → chấm điểm → duyệt và đóng gói
dataset.

---

## Các deliverable khác

| Tài liệu | Nội dung |
|---|---|
| [`architecture_diagram.md`](architecture_diagram.md) | Sơ đồ kiến trúc: tổng quan hệ thống, luồng teleop, luồng chấm điểm, mô hình dữ liệu |
| [`eval_evidences.md`](eval_evidences.md) | Năm test case chạy tay trên app thật, kèm output nguyên văn |
| [`img/`](img/) | Ảnh camera lấy thẳng từ luồng WebSocket, dùng làm bằng chứng |
| [`../../README.md`](../../README.md) | Hướng dẫn cài đặt, biến môi trường, sample queries |

---

## Chạy thử tại máy

Hướng dẫn đầy đủ nằm ở [README gốc](../../README.md). Tóm tắt:

```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate     # Windows Git Bash
python -m pip install -r requirements.txt

cp .env.example .env              # rồi đặt JWT_SECRET trong .env
python -m uvicorn src.main:app --reload

cd frontend && npm install && npm run dev
```

Mở http://localhost:3000 — dùng `localhost`, không dùng `127.0.0.1`, vì CORS chỉ
mở cho `localhost`.
