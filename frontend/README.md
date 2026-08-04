# TeleCollect frontend — demo chạy một mình

Folder này là **toàn bộ giao diện TeleCollect, chạy độc lập**: không cần backend,
không cần database, không cần Python, không cần MuJoCo. Chỉ cần Node.js.

Copy nguyên folder `frontend-demo` sang chỗ nào cũng được, `npm install`,
`npm run dev` — là bấm được hết mọi nút trong app.

---

## 1. Chạy

```bash
cd frontend
npm install
npm run dev
```

Mở [http://localhost:3000](http://localhost:3000). Trang login hiện ra.

**Đăng nhập: gõ gì cũng vào.** Không có server nào để xác thực cả. Mật khẩu bất
kỳ (khác rỗng) đều được. Role được suy ra từ username, vì mỗi role nhìn thấy một
tập màn hình khác nhau:

| Gõ username là…                         | Role nhận được | Thấy thêm gì                        |
| ------------------------------------------ | ------------------ | -------------------------------------- |
| `admin` (hoặc tên chứa `admin`)     | admin              | tất cả, kể cả trang**Users** |
| `reviewer` (hoặc tên chứa `review`) | reviewer           | duyệt demo, export dataset, train     |
| còn lại, vd`operator`, `linh`        | operator           | teleop + xem hàng đợi review        |

Ba nút gợi ý sẵn ở dưới form login bấm là điền hộ username/password.

Muốn build bản production:

```bash
npm run build
npm run start        # http://localhost:3000
```

Yêu cầu: **Node.js 18 trở lên** (đã test trên Node 24). Không cần biến môi
trường nào cả — không có file `.env`.

---
