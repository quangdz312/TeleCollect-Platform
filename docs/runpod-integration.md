# Nối training với GPU thuê (RunPod Serverless)

Tài liệu giao việc. Viết cho người code tiếp — mô tả **kiến trúc, việc cần làm,
yêu cầu, cách nghiệm thu** cho từng phần. Không chứa code hoàn chỉnh, chỉ chứa
chữ ký hàm và hình dạng dữ liệu để hai đầu khớp nhau.

**Trạng thái: A, B, D đã cài đặt xong** trên nhánh `feat/runpod-training`
(439 test đạt, tăng từ 385). Còn lại **C** (dựng container image) và **E** (tạo
endpoint trên RunPod rồi triển khai) — hai phần này cần tài khoản Docker Hub và
RunPod nên phải làm thủ công trên web.

---

## 0. Bối cảnh và quyết định đã chốt

**Vấn đề.** Server staging (`103.82.195.56`, Ubuntu 24.04, 4 nhân / 7.8 GB RAM /
103 GB trống, không GPU) đang chạy `app-caddy-1` + `app-backend-1` +
`app-frontend-1`. Backend hiện huấn luyện bằng `subprocess.Popen` gọi
`scripts/train_robomimic_bc.py` ngay trên máy chạy backend. Trên server không
GPU, đường này hoặc bị chặn bởi `training_enabled=false`, hoặc chạy trên CPU
chậm tới mức vô dụng.

**Quyết định.** Bấm Train trên web thì backend gọi sang nhà cung cấp GPU
(RunPod Serverless), trả tiền theo giây, không có job thì không tốn.

**Đã chốt — không mở lại:**

| Quyết định | Lý do |
|---|---|
| **Không dùng S3/R2 ở giai đoạn này** | Server còn 103 GB trống. Thêm một nhà cung cấp nữa là thêm một tài khoản, một khóa, một tầng lỗi. Máy GPU sẽ tải dataset **trực tiếp từ server** và đẩy checkpoint **về server**. |
| **Giữ nguyên đường chạy local** | Máy chủ dự án có GPU rời và vẫn cần train được như cũ. Hai chế độ cùng tồn tại, chọn bằng cấu hình. |
| **RunPod Serverless, không phải Pod** | Pod là thuê máy theo giờ, quên tắt là cháy tiền. Serverless co về 0 worker khi rảnh. |
| **Test bằng Python trước, Docker sau** | Hàm handler chạy thẳng được bằng `python`. Sai thì sửa trong 10 giây thay vì dựng lại image 3 GB. |

**Giới hạn phải biết trước.** Serverless không phải "thuê máy rồi SSH vào". Nó
chạy một container **đã chứa sẵn code huấn luyện**, mỗi request là một lần gọi
hàm. Nên thứ tự bắt buộc là: viết handler → dựng image → đẩy lên Docker Hub →
**rồi mới** tạo được endpoint. Không có Endpoint ID trước khi có image.

---

## Kiến trúc

```
Trình duyệt
    │  POST /training/jobs
    ▼
Backend trên server (không GPU)
    │
    ├── TRAINING_RUNNER=local  ──►  subprocess.Popen  (đường hiện tại, giữ nguyên)
    │
    └── TRAINING_RUNNER=runpod ──►  POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run
                                              │
                                              ▼
                                    Máy GPU thuê (container của ta)
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │ 1. GET dataset từ server (token máy)              │
                    │ 2. chạy train_robomimic_bc.py                     │
                    │ 3. POST log + checkpoint về server (token máy)    │
                    └───────────────────────────────────────────────────┘
```

Backend **hỏi trạng thái** bằng `GET /v2/{ENDPOINT_ID}/status/{runpod_id}`
theo nhịp, không chờ đồng bộ. Máy GPU **tự đẩy** log và checkpoint về, vì
backend không mở được kết nối vào máy thuê.

---

## Phần A — Hai chế độ chạy trong backend

### Vấn đề

`TrainingJobManager` (`src/training/jobs.py`) hiện gắn chặt với `subprocess`:
`_run()` gọi `subprocess.Popen`, `_refresh()` đọc `stdout.log` trên đĩa,
`cancel()` gọi `process.terminate()`. Không có chỗ nào cắm được một cách chạy khác.

### Yêu cầu

- Chế độ `local` phải giữ **nguyên hành vi hiện tại**, không đổi một dòng nào ở
  đường đó. 385 test đang xanh phải vẫn xanh.
- Chế độ `runpod` dùng chung định dạng `job.json`, chung `TrainingJobResponse`,
  chung màn hình frontend. Frontend **không được sửa** ở phần A.
- Đổi chế độ bằng biến môi trường, không cần sửa code.

### Việc cần làm

**A1. Thêm cấu hình vào `src/config.py`**

```python
training_runner: Literal["local", "runpod"] = "local"
runpod_api_key: str = ""
runpod_endpoint_id: str = ""
runpod_poll_interval_s: float = 10.0
runpod_max_hours: float = 1.0          # trần thời gian, cắt job vượt ngưỡng
public_base_url: str = ""              # URL server mà máy GPU gọi ngược về
machine_token_secret: str = ""         # ký token máy; rỗng thì dùng jwt_secret
```

Thêm `.env.example` tương ứng. Ghi rõ `runpod_api_key` là bí mật, không commit.

**A2. Tách cách chạy khỏi `TrainingJobManager`**

Định nghĩa một giao diện tối thiểu — đủ cho ba việc `TrainingJobManager` cần:

```python
class TrainingRunner(Protocol):
    def start(self, record: dict) -> dict: ...
    # trả về phần trạng thái riêng của runner, ghi vào record["runner_state"]

    def poll(self, record: dict) -> dict: ...
    # trả về {"status": JobStatus, "error": str | None}

    def cancel(self, record: dict) -> None: ...
```

`LocalRunner` bọc đúng đoạn `subprocess` đang có — **chuyển chỗ, không viết
lại**. `RunPodRunner` là phần mới.

**A3. `RunPodRunner`**

- `start()`: `POST https://api.runpod.ai/v2/{endpoint_id}/run`, header
  `Authorization: Bearer {api_key}`, thân là payload ở phần B. Lưu `id` RunPod
  trả về vào `record["runner_state"]["runpod_id"]`.
- `poll()`: `GET /v2/{endpoint_id}/status/{runpod_id}`. Ánh xạ trạng thái:

  | RunPod | JobStatus |
  |---|---|
  | `IN_QUEUE`, `IN_PROGRESS` | `RUNNING` |
  | `COMPLETED` | `SUCCEEDED` |
  | `FAILED`, `TIMED_OUT` | `FAILED` |
  | `CANCELLED` | `CANCELLED` |

- `cancel()`: `POST /v2/{endpoint_id}/cancel/{runpod_id}`.
- Một luồng nền hỏi trạng thái mỗi `runpod_poll_interval_s`. Không hỏi dày hơn
  — mỗi lần hỏi là một request tính phí.

**A4. Chỗ nhận log và checkpoint từ máy GPU**

Ba endpoint mới trong `src/api/training.py`, xác thực bằng **token máy** (phần
D), không dùng tài khoản người dùng:

| Endpoint | Việc |
|---|---|
| `GET /training/jobs/{job_id}/dataset` | Máy GPU tải file HDF5 về |
| `POST /training/jobs/{job_id}/log` | Nối thêm vào `stdout.log` của job |
| `POST /training/jobs/{job_id}/artifacts` | Nhận file `.pth`, ghi vào `output_dir` |

Sau khi checkpoint rơi vào `output_dir`, `discover_checkpoints()` hiện có tự
nhặt được — **không cần sửa** hàm đó.

### Rủi ro

**Ghi đè đường local.** Cám dỗ lớn nhất là "nhân tiện dọn luôn" đoạn subprocess.
Đừng. Chuyển nguyên khối, giữ nguyên `ACCELERATOR_LOCK`, giữ nguyên
`creationflags`, giữ nguyên cách bắt lỗi.

**Ghi file tùy tiện.** `POST /artifacts` nhận tên file từ máy GPU. Phải kiểm
`(output_dir / filename).resolve().is_relative_to(output_dir)` — repo đã có mẫu
này ở `checkpoint_path()` và `_resolve_within()`, dùng lại.

**Job treo.** Máy GPU chết giữa chừng thì RunPod báo `FAILED`, nhưng nếu nó
sống mà không tiến triển, job chạy tới hết `runpod_max_hours` mới dừng. Chấp
nhận ở giai đoạn này, nhưng phải có trần đó.

### Đánh giá hoàn thành

- [x] `TRAINING_RUNNER=local`: 439 đạt, 0 lỗi (385 cũ + 54 test mới)
- [x] `TRAINING_RUNNER=runpod` thiếu cấu hình: job `failed`, lỗi ghi rõ tên biến
      còn thiếu, backend vẫn sống
- [x] `tests/test_runpod_runner.py` — 18 test, đủ 6 ánh xạ trạng thái
- [x] 4 biến thể path traversal bị chặn, có test
- [x] Ba endpoint trả 401 khi thiếu token; token job A gọi job B trả 403

---

## Phần B — Handler chạy trên máy GPU

### Vấn đề

Container trên RunPod cần một điểm vào biết nhận việc, tải dữ liệu, train, và
báo kết quả về. Code train đã có (`scripts/train_robomimic_bc.py`) — thiếu lớp
vỏ quanh nó.

### Yêu cầu

- Chạy được **bằng Python trên máy có GPU**, không cần Docker, không cần RunPod.
  Đây là điều kiện để test nhanh.
- Không nhận đường dẫn hay lệnh từ payload. Chỉ nhận tham số huấn luyện.
- Đẩy log về theo lô, không mỗi dòng một request.

### Việc cần làm

**B1. `runpod_handler.py` ở gốc repo**

```python
def handler(event: dict) -> dict:
    """event["input"] chứa payload ở B2. Trả về {"status", "epochs", "error"}."""
```

Trình tự bên trong:

1. Đọc `payload["job_id"]`, `payload["callback_url"]`, `payload["machine_token"]`
2. `GET {callback_url}/training/jobs/{job_id}/dataset` → ghi ra `/tmp/dataset.hdf5`
3. Dựng danh sách tham số dòng lệnh từ `payload["config"]` — **dùng lại
   `TrainingJobManager._command()`**, tách nó thành hàm thuần để hai bên không
   lệch nhau
4. `subprocess.Popen`, vừa chạy vừa đọc stdout, cứ ~5 giây `POST .../log` một lô
5. Kết thúc: quét `output_dir` tìm `*.pth`, `POST .../artifacts` từng file
6. Trả về dict tổng kết

**B2. Hình dạng payload** — hợp đồng giữa phần A và phần B, đổi là phải đổi cả hai:

```json
{
  "input": {
    "job_id": "hex32",
    "callback_url": "https://telecollect.example.com",
    "machine_token": "<JWT ngắn hạn>",
    "config": { "...": "nguyên TrainingJobRequest.model_dump(mode='json')" }
  }
}
```

**B3. Chạy thử ở máy local**

Một script nhỏ nạp `handler` rồi gọi thẳng với một dict dựng tay, trỏ
`callback_url` vào `http://localhost:8000`. Không qua RunPod, không qua Docker.

### Rủi ro

**Hai nơi dựng dòng lệnh sẽ lệch nhau.** Nếu handler tự viết lại danh sách
tham số, thêm một cờ mới ở `TrainingJobRequest` là quên một chỗ. Bắt buộc tách
`_command()` thành hàm dùng chung.

**Checkpoint quá to.** Mỗi `.pth` cỡ vài chục tới vài trăm MB, và một lần train
sinh ra nhiều file. Chỉ đẩy `last.pth` và file `best_validation` — bỏ các
`model_epoch_*` trung gian, trừ khi payload yêu cầu.

**Token hết hạn giữa chừng.** Job chạy 1 giờ mà token sống 15 phút thì bước 5
thất bại sau khi đã train xong — mất trắng. Token máy phải sống lâu hơn
`runpod_max_hours`.

### Đánh giá hoàn thành

- [ ] **CHƯA CHẠY THỬ THẬT** — cần một máy có GPU và một dataset thật. Đây là
      việc tiếp theo, trước khi động vào phần C
- [x] Lỗi bất ngờ được gói lại thành `{"status": "failed", "error": ...}`, có test
- [x] `build_training_command()` là nguồn duy nhất; `_command()` gọi vào nó

---

## Phần C — Container image

### Vấn đề

RunPod chỉ chạy được thứ đã nằm trong một container image công khai. Image phải
có torch bản CUDA, robomimic, và code của repo.

### Yêu cầu

- Base image có sẵn CUDA — **không** cài driver bằng tay.
- Không chứa `.env`, không chứa khóa, không chứa `data/`.
- Không cần MuJoCo rendering nếu tắt rollout. Nếu bật rollout thì cần OSMesa và
  cả hai biến `MUJOCO_GL=osmesa` + `PYOPENGL_PLATFORM=osmesa` (xem
  `docs/HANDOVER.md`, mục bẫy môi trường).

### Việc cần làm

**C1. `Dockerfile.train`** — tách hẳn khỏi `Dockerfile` hiện có, vì image này
nặng và chỉ dùng cho GPU.

- Base: `runpod/pytorch` hoặc `nvidia/cuda:12.1-runtime-ubuntu22.04`
- Cài `requirements-train.txt` (đọc kỹ ghi chú trong file — nó không cài trọn
  bằng một lệnh trên Windows, nhưng trên Linux thì được)
- Copy `src/`, `scripts/`, `runpod_handler.py`
- `CMD ["python", "-u", "runpod_handler.py"]`

**C2. Đẩy lên Docker Hub**

```bash
docker build -f Dockerfile.train -t <tài-khoản>/telecollect-train:v1 .
docker push <tài-khoản>/telecollect-train:v1
```

Đặt tag phiên bản, **đừng dùng `latest`** — RunPod cache image, `latest` khiến
không biết endpoint đang chạy bản nào.

### Rủi ro

**Image quá lớn.** torch CUDA một mình đã ~2.5 GB. Lần khởi động nguội đầu tiên
của worker phải kéo cả image về, mất vài phút, và **thời gian đó có tính phí**.
Giữ image gọn nhất có thể.

**Build trên Windows.** Đã gặp: `evdev` cần `build-essential linux-libc-dev`
trong builder stage; `pip install --user` để gói ở `/root/.local` mode 0700 nên
user thường không đọc được. `Dockerfile` hiện tại trên `main` đã xử lý bằng
venv — tham khảo cách đó.

### Đánh giá hoàn thành

- [ ] `docker run --rm --gpus all <image> python -c "import torch; print(torch.cuda.is_available())"` → `True`
- [ ] Chạy container ở local, trỏ callback vào backend local: job hoàn thành
- [ ] `docker history` không thấy tệp bí mật nào

---

## Phần D — Token máy

### Vấn đề

Máy GPU thuê phải gọi được ba endpoint mới, nhưng nó không phải người dùng và
không nên có tài khoản. Nếu đưa cho nó token của người bấm Train thì token đó
nằm trên máy của người lạ, dùng được cho **mọi** endpoint.

### Yêu cầu

- Token chỉ dùng được cho **đúng một `job_id`**.
- Chỉ dùng được cho ba endpoint máy, không cho gì khác.
- Sống lâu hơn `runpod_max_hours`, và hết hạn sau đó.

### Việc cần làm

Ký JWT bằng `machine_token_secret` (rỗng thì rơi về `jwt_secret`), payload
`{"type": "machine", "job_id": "...", "exp": ...}` — repo dùng khóa `type`, không
phải `typ`.

**Không dùng lại `_decode_typed_token`**: hàm đó bắt buộc có `sub` (id người
dùng), mà token máy cố ý không có `sub` để `current_user` từ chối nó. Vì vậy có
`decode_machine_token` riêng, đọc `job_id` thay cho `sub`.

Dependency mới `current_machine_job(job_id)`: giải mã, kiểm `type == "machine"`,
kiểm `job_id` trong token khớp `job_id` trên đường dẫn. Lệch thì 403.

### Rủi ro

**Dùng nhầm dependency.** Nếu ai đó gắn `current_machine_job` lên một endpoint
người dùng, hoặc ngược lại, cả hai đều là lỗ hổng. Ghi chú cảnh báo ngay trên
hàm — repo đã có tiền lệ ở `current_user_allow_query_token`.

**Token lọt vào log.** Đưa qua header `Authorization`, **không** qua query
param như endpoint media.

### Đánh giá hoàn thành

- [x] Token của job A gọi endpoint job B → 403, có test
- [x] Token máy gọi `GET /training/jobs` → 401, có test
- [x] Token hết hạn → 401, có test

---

## Phần E — Tạo endpoint và nối đầu cuối

Phần này làm trên web, sau khi C xong.

### E1. Lấy khóa RunPod

Settings → API Keys → Create API Key, quyền **Read/Write**. Copy ngay, trang
chỉ hiện một lần.

### E2. Tạo endpoint

Serverless → New Endpoint → Custom Source → Docker image, điền
`<tài-khoản>/telecollect-train:v1`.

| Thiết lập | Giá trị | Vì sao |
|---|---|---|
| GPU tier | 16 GB (`AMPERE_16`) | Đủ cho BC-RNN ở kích thước hiện tại |
| Active workers | **0** | Không có job thì không tốn tiền |
| Max workers | 1 | Chặn nhiều job chạy song song ngoài ý muốn |
| Idle timeout | 5 s | Tắt sớm sau khi xong |
| Execution timeout | theo `runpod_max_hours` | Trần chi phí |
| Container disk | 20 GB | Chứa image + dataset + checkpoint |

Copy **Endpoint ID** ở đầu trang.

### E3. Chi phí

Tier 16 GB khoảng **0.58 USD/giờ**. Một lần train 30 phút ≈ **0.29 USD**. Cộng
thêm thời gian kéo image ở lần chạy nguội đầu tiên.

### E4. Cấu hình server

Trên server, thêm vào `.env` của backend rồi dựng lại container:

```
TRAINING_RUNNER=runpod
TRAINING_ENABLED=true
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...
PUBLIC_BASE_URL=https://<tên miền server>
MACHINE_TOKEN_SECRET=<chuỗi ngẫu nhiên>
```

**Chưa biết thư mục triển khai.** `ls ~` của user `deploy` trống và
`find / -maxdepth 3 -name 'docker-compose*.yml'` không ra kết quả. Chạy trước:

```bash
docker inspect app-backend-1 --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect app-backend-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

Kết quả cho biết chỗ sửa `.env` và chỗ dataset nằm trên host.

### Đánh giá hoàn thành

- [ ] `PUBLIC_BASE_URL` truy cập được từ ngoài internet (máy GPU phải gọi được)
- [ ] Bấm Train trên web: job chạy, log hiện dần, checkpoint tải về được
- [ ] Bấm Cancel: worker RunPod dừng, hóa đơn ngừng tăng
- [ ] Bảng Billing của RunPod khớp với thời lượng job

---

## Thứ tự làm

```
D (token máy)            ĐÃ XONG
A (backend hai chế độ)   ĐÃ XONG
B (handler)              ĐÃ XONG — chưa chạy thử với GPU thật
      │
      ▼
C (container image)      CÒN LẠI — cần tài khoản Docker Hub
      │
      ▼
E (endpoint + deploy)    CÒN LẠI — cần tài khoản RunPod
```

**Không bắt đầu C trước khi B chạy được ở local.** Mỗi vòng build-push-test qua
Docker mất vài phút; qua Python mất vài giây.

---

## Đã cài đặt những gì

Nhánh `feat/runpod-training`. Chưa commit, chưa đẩy lên remote.

| Tệp | Thay đổi |
|---|---|
| `src/services/security.py` | `create_machine_token`, `decode_machine_token`, `current_machine_job` |
| `src/config.py` | 7 cấu hình mới, mặc định giữ nguyên hành vi cũ |
| `src/training/runpod_runner.py` | **mới** — gọi `run` / `status` / `cancel` |
| `src/training/jobs.py` | `build_training_command()` tách ra; `_run_remote()`; `dataset_path()`, `append_log()`, `artifact_path()` |
| `src/api/training.py` | chọn runner theo cấu hình; 3 endpoint máy |
| `src/models/schemas.py` | `MachineLogRequest` |
| `runpod_handler.py` | **mới** — điểm vào chạy trên máy GPU thuê |
| `.env.example` | mục Training runner |
| `tests/` | 3 tệp mới, 54 test |

### Ba điểm lệch so với bản đặc tả ban đầu

**Không có lớp `LocalRunner`.** Đường subprocess giữ nguyên tại chỗ, `runner=None`
là chế độ local. Bọc nó lại thành một lớp chỉ để đối xứng nghĩa là phải đụng vào
đúng đoạn code mà yêu cầu nói là không được đụng.

**Token máy không dùng lại `_decode_typed_token`.** Hàm đó bắt buộc có `sub`, mà
token máy cố ý không có — xem phần D.

**`poll()` coi trạng thái lạ là `RUNNING`.** Nếu RunPod thêm một trạng thái mới,
kết luận thất bại sẽ giết một job đang train mà ta đã trả tiền. Nhầm theo hướng
chờ thêm thì `runpod_max_hours` vẫn chặn được.

### Việc tiếp theo, đúng thứ tự

1. **Chạy thử `runpod_handler.py` trên máy có GPU** — chưa làm, và đây là bước
   duy nhất chứng minh phần B thật sự chạy:

   ```bash
   # tạo token máy cho một job có thật
   python -c "from datetime import timedelta; from src.services.security import create_machine_token; print(create_machine_token('<job_id>', timedelta(hours=2)))"

   python runpod_handler.py --job-id <job_id>        --callback-url http://localhost:8000 --machine-token <token>
   ```

2. Tạo tài khoản Docker Hub → phần C
3. Lấy RunPod API key và tạo endpoint → phần E

---

## Việc còn treo, không thuộc phạm vi tài liệu này

- 3 tài liệu chưa commit: `docs/HANDOVER.md`, `docs/platform-spec.md`,
  `docs/system-overview.md` — chờ chủ dự án duyệt
- `local_app/` chưa theo dõi bởi git, Codex đang làm
- `HANDOVER.md` mục Tính năng 3 nói app local chưa bắt đầu — đã lạc hậu
- Chưa kiểm chứng: nút "Save trim & notes only" có thể không lưu ghi chú
