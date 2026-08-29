# Bước E — Tạo endpoint RunPod và nối vào server

Hướng dẫn thao tác trên web. Làm sau khi image đã đẩy lên Docker Hub.

Địa chỉ image của dự án: **`pakerpp/telecollect-train:v1`**

---

## E0. Kiểm tra image đã lên Docker Hub chưa

Image đã dựng và kiểm tra xong ở máy local — **5.55 GB**, nội dung xác nhận:

| Gói | Bản |
|---|---|
| torch | 2.6.0+**cu124** (bản CUDA, không phải CPU) |
| robomimic | 0.5.0 |
| mujoco | 3.8.1 |
| numpy | 1.26.4 (đúng nhánh 1.x) |
| runpod | 1.12.0 |

`runpod_handler` import được trong container.

**Đã biết:** Python trong image là `3.11.0rc1` — bản thử nghiệm, vì kho mặc
định của Ubuntu 22.04 chỉ có bản đó. Chưa gây lỗi gì, nhưng nếu về sau gặp
hành vi lạ ở tầng Python thì đây là chỗ đáng nghi đầu tiên; sửa bằng cách thêm
kho `deadsnakes` vào `Dockerfile.train`.

Vào https://hub.docker.com/r/pakerpp/telecollect-train/tags — phải thấy tag `v1`.

Nếu chưa thấy, image chưa được đẩy lên. RunPod không tải được từ máy bạn, chỉ
tải được từ kho công khai.

**Repository phải để Public.** Nếu để Private, RunPod sẽ báo lỗi kéo image, và
phải khai báo thêm thông tin đăng nhập trong phần cấu hình endpoint.

---

## E1. Lấy API key

1. Đăng nhập https://runpod.io
2. Menu trái → **Settings**
3. Mục **API Keys** → nút **+ Create API Key**
4. Đặt tên bất kỳ (ví dụ `telecollect-backend`)
5. Phân quyền — hộp thoại có hai dòng riêng, chọn như sau:

   | Mục | Chọn | Vì sao |
   |---|---|---|
   | (hàng trên cùng) | **Restricted** | Không cấp toàn quyền |
   | `api.runpod.io/graphql` | **None** | API quản lý tài khoản (tạo/xóa endpoint, hóa đơn) — backend không dùng |
   | `api.runpod.ai` | **Read / Write** | API chạy job: `run`, `status`, `cancel` — backend cần đủ ba |

   Chọn `Read only` cho `api.runpod.ai` thì hỏi được trạng thái nhưng **không
   tạo được job**, bấm Train sẽ lỗi. Chọn `All` thì khóa lọt ra ngoài là người
   khác xóa được endpoint và đọc được thông tin thanh toán.

6. Bấm **Create** rồi **copy ngay**

> Key chỉ hiện **một lần duy nhất**. Đóng hộp thoại là không xem lại được, phải
> tạo key mới. Dán tạm vào đâu đó trước khi làm tiếp.

Key có dạng `rpa_XXXXXXXXXXXXXXXXXXXX`.

---

## E2. Nạp tiền

Serverless không có bậc miễn phí. Menu trái → **Billing** → nạp tối thiểu
(thường 10 USD). Chưa nạp thì tạo endpoint được nhưng job sẽ nằm mãi trong hàng
đợi và không bao giờ chạy.

---

## E3. Tạo endpoint

Menu trái → **Serverless** → nút **+ New Endpoint**.

### Chọn nguồn

Chọn **Docker Image** (không phải GitHub Repo, không phải template có sẵn).

Ô **Container Image** điền đúng chuỗi này:

```
pakerpp/telecollect-train:v1
```

Không thêm `docker.io/` ở đầu, không thêm `https://`.

### Chọn GPU

Chọn tier **16 GB** (nhãn thường là `AMPERE_16` — A4000, A4500, RTX 4000).

Đủ cho BC và BC-RNN ở kích thước hiện tại. Tier lớn hơn không train nhanh hơn
đáng kể mà đắt gấp đôi.

### Cấu hình worker

| Ô | Điền | Vì sao |
|---|---|---|
| **Active Workers** | `0` | Quan trọng nhất. Để `0` thì không có job là không tốn tiền. Đặt `1` là trả tiền 24/7. |
| **Max Workers** | `1` | Chặn nhiều job chạy song song ngoài ý muốn. |
| **Idle Timeout** | `5` giây | Xong việc thì tắt sớm. |
| **Execution Timeout** | `3600` giây | Trần 1 giờ, khớp với `RUNPOD_MAX_HOURS=1` ở backend. |
| **Container Disk** | `20` GB | Chứa image + dataset + checkpoint. |

**FlashBoot**: bật nếu có. Nó giữ image sẵn để lần khởi động sau nhanh hơn,
không tính thêm tiền.

### Biến môi trường

Không cần điền gì. Backend gửi mọi thứ handler cần qua payload của từng job.

Bấm **Deploy**.

---

## E4. Lấy Endpoint ID

Sau khi tạo xong, mở endpoint vừa tạo. **Endpoint ID** nằm ngay dưới tên, dạng
`abc123def456`. Đó là thứ backend cần.

Đừng nhầm với URL đầy đủ `https://api.runpod.ai/v2/abc123def456/run` — backend
chỉ cần đoạn `abc123def456`.

---

## E5. Thử endpoint ngay trên web

Tab **Requests** của endpoint có ô gửi thử. Dán vào:

```json
{
  "input": {
    "job_id": "test",
    "callback_url": "https://example.com",
    "machine_token": "test",
    "config": {}
  }
}
```

Kết quả mong đợi: job chạy rồi **thất bại** với thông báo không tải được dataset
(vì `callback_url` là địa chỉ giả). Đó là dấu hiệu **tốt** — chứng tỏ image kéo
về được, Python chạy được, handler được gọi.

Nếu thấy lỗi kéo image hoặc `ModuleNotFoundError` thì image có vấn đề, phải sửa
rồi build lại — chưa nối vào server vội.

Lần chạy đầu tiên mất vài phút vì phải tải image ~5 GB. **Thời gian đó có tính
phí.** Các lần sau nhanh hơn nhiều.

---

## E6. Triển khai lên server

Đường dẫn lấy từ `docs/DEPLOYMENT_STAGING.md` — không cần dò nữa:

| Đường dẫn | Chứa gì |
|---|---|
| `/srv/telecollect/app` | Mã nguồn, chỗ chạy `docker compose` |
| `/srv/telecollect/secrets/.env.production` | Cấu hình bí mật, quyền 600 |
| `/srv/telecollect/data` | Dữ liệu, gắn vào `/app/data` trong container |

### 1. Lấy mã nguồn mới

VPS chưa có Git remote hợp lệ, nên **không dùng `git pull` trên VPS**. Đưa mã
lên bằng Git bundle theo đúng quy trình trong file bàn giao
(`TELECOLLECT_P111_TEAMMATE_DEPLOYMENT_HANDOFF`, mục "tạo full Git bundle"):
tạo bundle từ `main` đã review trên máy Windows, copy sang VPS, rồi fetch từ
bundle đó.

### 2. Thêm cấu hình

```bash
sudo nano /srv/telecollect/secrets/.env.production
```

Sửa hai dòng đã có sẵn (đang là `false`):

```
TRAINING_ENABLED=true
NEXT_PUBLIC_TRAINING_ENABLED=true
```

**Phải sửa cả hai.** Dòng đầu cho backend nhận job, dòng sau cho giao diện hiện
nút Train — chỉ sửa một cái thì trang Training vẫn báo "chưa khả dụng".

Thêm mới sáu dòng:

```
TRAINING_RUNNER=runpod
RUNPOD_API_KEY=<khóa lấy ở E1>
RUNPOD_ENDPOINT_ID=y21vsntfwzoglp
RUNPOD_MAX_HOURS=1
PUBLIC_BASE_URL=https://<PROJECT_DOMAIN đang dùng>
MACHINE_TOKEN_SECRET=<sinh bằng lệnh dưới>
```

Sinh khóa ký token máy:

```bash
openssl rand -hex 32
```

`PUBLIC_BASE_URL` lấy đúng giá trị `PROJECT_DOMAIN` đang có trong file đó, thêm
`https://` ở đầu. Máy GPU thuê nằm ở mạng khác nên phải là địa chỉ truy cập
được từ internet — `localhost` hay IP nội bộ đều vô dụng.

### 3. Dựng lại và khởi động

```bash
cd /srv/telecollect/app
docker compose   --env-file /srv/telecollect/secrets/.env.production   -f docker-compose.prod.yml build

docker compose   --env-file /srv/telecollect/secrets/.env.production   -f docker-compose.prod.yml up -d
```

Frontend phải dựng lại chứ không chỉ khởi động lại: `NEXT_PUBLIC_*` được nhúng
vào lúc build, đổi giá trị mà không build lại thì giao diện vẫn dùng giá trị cũ.

### 4. Kiểm tra

```bash
docker compose   --env-file /srv/telecollect/secrets/.env.production   -f docker-compose.prod.yml ps

docker compose   --env-file /srv/telecollect/secrets/.env.production   -f docker-compose.prod.yml logs backend --tail=50
```

Ba container phải `Up`, backend `healthy`.

---

## E7. Nghiệm thu

- [ ] Bấm Train trên web → job chuyển `running` trong vài giây
- [ ] Tab **Requests** trên RunPod thấy job tương ứng
- [ ] Log hiện dần trên trang training (máy GPU đẩy về mỗi ~5 giây)
- [ ] Job xong → checkpoint xuất hiện, tải về được
- [ ] Bấm Cancel giữa chừng → worker trên RunPod dừng
- [ ] **Billing** khớp với thời lượng job

---

## Chi phí

Tier 16 GB khoảng **0.58 USD/giờ**, tính theo giây.

| Việc | Thời gian | Tiền |
|---|---|---|
| Lần chạy đầu (phải tải image) | ~5 phút | ~0.05 USD |
| Một lần train 30 phút | 30 phút | ~0.29 USD |
| Một lần train 1 giờ (trần) | 60 phút | ~0.58 USD |

Nạp 10 USD đủ cho khoảng 17 giờ train.

**Đặt Active Workers = 0.** Đây là ô quyết định giữa "trả tiền khi train" và
"trả tiền 24/7". Để `1` thì một tháng mất khoảng 420 USD dù không train gì.

---

## Khi có lỗi

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| Job nằm mãi ở `IN_QUEUE` | Chưa nạp tiền, hoặc Max Workers = 0 |
| Lỗi kéo image | Repository để Private, hoặc gõ sai tag |
| `ModuleNotFoundError` | Image thiếu gói — sửa `Dockerfile.train`, build lại với tag `v2` |
| Handler báo không tải được dataset | `PUBLIC_BASE_URL` sai, hoặc server không truy cập được từ ngoài |
| 403 khi đẩy checkpoint | `MACHINE_TOKEN_SECRET` trên server đã đổi sau khi job bắt đầu |
| `torch.cuda.is_available()` False | Image cài nhầm torch bản CPU |

**Sửa image thì phải đổi tag** (`v2`, `v3`...) rồi cập nhật endpoint trỏ sang
tag mới. RunPod cache theo tag, đẩy đè lên `v1` thì worker vẫn chạy bản cũ.
