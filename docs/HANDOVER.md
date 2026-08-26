# Bàn giao công việc — TeleCollect

Tài liệu bàn giao tính đến **25/08/2026**, nhánh `main @ 49fc2f2`.

Đọc theo thứ tự:

| Tài liệu | Nội dung |
|---|---|
| [system-overview.md](system-overview.md) | Hệ thống hiện có gì |
| [platform-spec.md](platform-spec.md) | Sẽ xây thêm gì, đặc tả 5 tính năng |
| File này | Trạng thái, việc đang dở, lỗi đã biết |

---

## 1. Trạng thái repo

**Nhánh `main` sạch, đã đồng bộ với remote.** Không có nhánh tạm nào chờ xử lý.

Hai tệp chưa commit, đều là tài liệu mới viết:

```
docs/platform-spec.md
docs/system-overview.md
```

Chưa commit vì chờ chủ dự án duyệt nội dung.

**Kiểm thử:** 343 test đạt, 1 thất bại. Test thất bại là
`test_release_check::test_reference_lift_episode_reads_as_lifted` — episode `lift`
tham chiếu chỉ nâng 11 mm so với ngưỡng 20 mm. **Do dữ liệu cục bộ, không do mã
nguồn.** Test này đã đỏ từ trước, không phải hồi quy.

**CI đỏ** vì hạn mức thanh toán ở cấp tổ chức GitHub — ngoài tầm kiểm soát của
nhóm. Hiện kiểm tra bằng cách chạy test cục bộ trước mỗi lần đẩy:

```bash
python -m pytest -q
cd frontend && npx tsc --noEmit && npx next build
```

---

## 2. Việc đã hoàn thành gần đây

Trong hai ngày 24–25/08:

| Commit | Nội dung |
|---|---|
| `02e0ae7` | Đưa giao diện sáng từ nhánh `demo-v2` vào `main` |
| `0da1158` | `seed_tasks.py` đọc danh sách nhiệm vụ từ simulator |
| `3346eae` | Đặt tên nhiệm vụ theo mã định danh, thêm bộ lọc nguồn |
| `c52a25b` | Chuyển nhãn giao diện sang tiếng Anh |
| `6e3c53e` | Hàng đợi review mở vào trạng thái chờ duyệt |

### Ba lỗi đã sửa, đáng ghi nhớ

**Tên nhiệm vụ hiện thành câu tiếng Việt.** Nguyên nhân: hàm chuyển đổi lấy
trường mô tả làm nhãn, mà trường đó chứa câu hướng dẫn cho người thu dữ liệu.
Đã đổi sang dùng tên định danh.

**Bảng nhiệm vụ trong cơ sở dữ liệu lệch khỏi simulator.** Bảng chứa `pick_place`,
`push`, `stack` — nhiệm vụ mẫu của robosuite từ giai đoạn đầu. Simulator đã
chuyển sang bộ bốn nhiệm vụ thật nhưng script khởi tạo vẫn ghi cứng bộ cũ. Đã sửa
để đọc thẳng từ simulator, nên không lệch lại được.

**Episode teleop vô hình ở hàng đợi review.** Teleop lưu episode ở trạng thái đã
gắn nhãn (vì simulator tự biết thành/bại), nhưng hàng đợi mặc định lọc trạng thái
chờ gắn nhãn. Người thu xong nhìn vào thấy trống, tưởng mất dữ liệu. Đã đổi mặc
định và sửa nhãn trạng thái cho đúng nghĩa.

---

## 3. Việc tiếp theo

Đặc tả đầy đủ ở [platform-spec.md](platform-spec.md). Tóm tắt:

```
Tính năng 1 (Team và phân quyền)
      ↓
Tính năng 2 (Token máy + nhận HDF5)
      ↓
      ├────────────────────┐
      ↓                    ↓
Tính năng 3            Tính năng 4
(App local)            (GPU thuê)
      └────────────────────┘
      ↓
Tính năng 5 (Hạn mức + triển khai)
```

### Bắt đầu từ đâu

**Tính năng 1 phải làm đầu tiên** vì nó thay đổi cấu trúc cơ sở dữ liệu. Hiện có
925 episode đã chấm điểm và 8 dataset — càng thêm dữ liệu thì việc chuyển đổi
càng rủi ro.

Bước đầu tiên trong tính năng 1: **khởi tạo Alembic**. Gói này đã có trong
`requirements.txt` nhưng chưa có thư mục migration, nên hiện mọi thay đổi cấu
trúc dữ liệu không có đường lùi.

**Tính năng 3 và 4 độc lập nhau**, làm song song được.

---

## 4. Quyết định kiến trúc đã chốt

Những điều này đã bàn kỹ, không cần mở lại:

**Sim chạy ở máy người dùng, không chạy trên server.** Đo được: render một khung
640px bằng phần mềm trên máy không GPU mất khoảng 45 ms, trong khi vòng điều khiển
60 Hz chỉ có ngân sách 16.7 ms mỗi bước. Trên GPU, cùng khung đó mất 0.67 ms.
Physics gần như miễn phí (dưới 0.01 ms mỗi bước) — toàn bộ chi phí nằm ở render.

**Server không phục vụ teleop.** Hệ quả của quyết định trên. Server chỉ nhận dữ
liệu, gắn nhãn, quản lý dataset, điều phối job. Nhờ vậy chỉ cần VPS 2 nhân 4 GB.

**GPU của người truy cập web không dùng được.** Trình duyệt cô lập phần cứng,
không có cách nào để trang web gọi CUDA. Đây là giới hạn cứng, không phải thiếu
công cụ. Mọi tính toán phải ở server, máy thuê, hoặc ứng dụng cài đặt.

**Giữ tên `TeleCollect`.** Tên đã nằm trong mã nguồn, cơ sở dữ liệu, tài liệu và
các báo cáo đã nộp. Đổi giữa dự án gây rối hơn lợi.

**Hai hệ đặt tên nhiệm vụ khác nhau — chấp nhận, không thống nhất.** Đường teleop
dùng `lift_cube`, đường scripted dùng `lift`. 925 episode đã lưu theo tên ngắn
trong tệp điểm số và tên tệp HDF5. Giao diện review có bộ lọc nguồn để phân biệt.

---

## 5. Lỗi và hạn chế đã biết

### Chưa xác nhận

**Nút "Save trim & notes only" có thể không lưu ghi chú.** Đọc mã nguồn thấy
trường ghi chú chỉ được gửi kèm khi gắn nhãn hoặc duyệt, không gửi khi chỉ lưu
mốc cắt. **Chưa thử nghiệm thực tế** — cần kiểm chứng: gõ ghi chú, bấm nút, tải
lại trang xem còn không.

### Đã xác nhận

**Thiếu ffmpeg** thì tải lên, tạo ảnh thu nhỏ và phát lại video đều hỏng. Phần
còn lại vẫn chạy. Cài bằng `choco install ffmpeg` trên Windows.

**Không chọn được tập con dữ liệu để huấn luyện.** Các nhãn phân loại chất lượng
đã ghi vào tệp HDF5 nhưng giao diện chưa cho chọn. Đây là việc nhỏ nhưng mở khóa
được phần thí nghiệm đo ảnh hưởng của dữ liệu.

**Tải lên chỉ nhận video.** Ai đã có sẵn dữ liệu robomimic thì không đưa vào được.

### Bẫy môi trường

**Máy Windows có hai GPU** cần đặt biến môi trường chọn card rời **trước khi** tạo
ngữ cảnh đồ họa đầu tiên. `src/sim/gpu.py` xử lý việc này lúc import — nên phải
import module đó sớm nhất trong mọi điểm khởi động. Bỏ qua thì sim chạy trên card
tích hợp và chậm gấp mấy chục lần.

**Cài `requirements-train.txt` trên Windows không chạy trọn bằng một lệnh** vì hai
lý do độc lập. Tệp đó ghi rõ cách làm từng bước.

**Tệp requirements cần dấu BOM UTF-8.** Không có thì pip đọc bằng bảng mã cục bộ
và lỗi trên máy Windows tiếng Việt.

---

## 6. Việc cần nhắn cho nhóm

**Ai pull `main` về cần chạy một lần:**

```bash
python -m scripts.seed_tasks
```

Để dọn `pick_place`, `push`, `stack` khỏi cơ sở dữ liệu cục bộ. Script an toàn,
chạy lại nhiều lần không sao, và không xóa nhiệm vụ nào còn episode tham chiếu.

**Ai có tệp `.env` cũ** cần kiểm tra ba dòng cấu hình camera. Thiếu dòng thứ ba
khiến hai khung phụ hiển thị cùng một ảnh. Xem `.env.example`.

**Nhánh `demo-v2` đã được đưa vào `main`.** Lần sau tạo nhánh mới từ `main` hiện
tại, đừng dùng lại nhánh cũ.

---

## 7. Bài học rút ra khi làm việc với repo này

**Test đạt không có nghĩa tính năng còn sống.** Một lần gộp nhánh tự động đã âm
thầm nuốt mất một liên kết điều hướng mà 325 test vẫn xanh. Sau mỗi lần gộp, phải
kiểm tra thủ công từng tính năng bị chạm tới.

**Nhánh lùi quá xa thì cherry-pick, đừng merge.** Nhánh `demo-v2` lùi 32 commit;
gộp thẳng sẽ đọc thành xóa cả pipeline đánh giá. Cùng tình huống với các PR
#17–#19.

**Sao lưu cơ sở dữ liệu trước mọi migration:**

```bash
cp data/app.db data/app.db.$(date +%Y%m%d-%H%M).backup
```

---

## 8. Lệnh hay dùng

```bash
# Chạy
uvicorn src.main:app --port 8000
cd frontend && npm run dev

# Kiểm thử
python -m pytest -q
cd frontend && npx tsc --noEmit && npx next build

# Khởi tạo dữ liệu
python -m scripts.seed_tasks
python scripts/create_admin.py

# Migration (sau khi khởi tạo Alembic)
alembic revision --autogenerate -m "mô tả"
alembic upgrade head
alembic downgrade -1
```

**Trên Windows** dùng `.venv\Scripts\python` thay cho `python`, và đặt
`PYTHONIOENCODING=utf-8` khi script in tiếng Việt.
