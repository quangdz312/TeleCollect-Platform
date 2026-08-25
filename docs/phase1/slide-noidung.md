# Nội dung slide — TeleCollect (12 trang)

> **Cách dùng:** mỗi mục là một slide. Phần **Trên slide** là thứ chiếu lên màn
> hình — giữ ít chữ. Phần *Nói* là kịch bản trình bày miệng, không đưa lên slide.
> Câu **in đậm gạch chân** trong phần *Nói* là câu chốt của slide đó.

---

## Slide 1 — Tên đề tài và thành viên

### Trên slide

> # TeleCollect
> **Thu thập và kiểm định dữ liệu demonstration cho imitation learning**
>
> Đề bài RAV-12 · VinUni AI20K Build Phase · Team NEURA

| Thành viên | Vai trò |
|---|---|
| *(điền tên)* | *(vd: teleop & control loop)* |
| *(điền tên)* | *(vd: scripted collection & scoring)* |
| *(điền tên)* | *(vd: frontend & review UI)* |
| *(điền tên)* | *(vd: training & evaluation)* |

> ⚠️ Repo không có danh sách thành viên chính thức. Lịch sử commit cho thấy 4
> người đóng góp: PakerPP (59 commit), Trần Gia Thế (32), QP (23), Minh Nguyen
> (20). Cần thay bằng họ tên đầy đủ trước khi trình bày.

*Nói:* Một câu định vị ngay từ đầu — **<u>đây không phải công cụ lái robot, đây là
đường ống dữ liệu có kiểm định chất lượng.</u>**

---

## Slide 2 — Bài toán: ba nỗi đau

### Trên slide

Imitation learning chỉ học được từ demonstration do **người** tạo ra.
Đội không có công cụ nào để tạo ra chúng.

| | Nỗi đau | Hệ quả thực tế |
|---|---|---|
| **P1** | Không có cách điều khiển robot mô phỏng thuận tiện | Không ai thu được demo. Việc thu dữ liệu **không bắt đầu được** |
| **P2** | Không ghi được **đồng bộ** observation ↔ action | Dữ liệu *trông có vẻ đúng* nhưng lệch pha vài frame. Model học sai và **không ai phát hiện ra** cho tới lúc training thất bại |
| **P3** | Không có bước kiểm duyệt trước khi vào tập huấn luyện | Demo hỏng lẫn vào dữ liệu tốt. Không truy được mẫu nào do ai tạo, ai duyệt |

> ### Nỗi đau lớn nhất không phải *"thiếu dữ liệu"*, mà là *"có dữ liệu nhưng không biết nó tốt hay xấu"*.

*Nói:* Dừng lâu ở P2 — đây là nỗi đau **âm thầm** nhất. Sai lệch pha không báo
lỗi, không crash, chỉ làm model học sai và mất hàng tuần mới phát hiện.
**<u>Một tập dữ liệu không đo được chất lượng thì tương đương không có.</u>**

---

## Slide 3 — Giải pháp: một vòng lặp khép kín

### Trên slide

```
   ┌──────────────────────────────────────────────────────┐
   │                                                      │
   ▼                                                      │
 Thu demo  →  Ghi đồng bộ obs+action  →  Kiểm duyệt      │
                                            ↓             │
                                      Đóng gói dataset    │
                                            ↓             │
                                   Huấn luyện BC          │
                                            ↓             │
                            Đo success rate trong sim ────┘
```

> **Con số ở cuối chính là thước đo cho chất lượng dữ liệu ở đầu.**

Ba vai trò tách bạch: **Operator** thu · **Reviewer** duyệt · **Admin** quản trị

*Nói:* Hai điểm phải nhấn:
1. Vòng lặp **khép kín** — trả lời được P2 và P3 bằng cùng một cơ chế.
2. **<u>Tách Operator khỏi Reviewer là điều làm cho bước kiểm duyệt có ý nghĩa:
   người duyệt một bản ghi không phải là người tạo ra nó.</u>** Reviewer thậm chí
   không được phép điều khiển robot bằng tay.

---

## Slide 4 — Kiến trúc và bài toán độ trễ

### Trên slide

```
Frontend Next.js ──REST──> FastAPI ──> SQLite (chỉ metadata)
       │                      │
       └────WebSocket────> ControlLoop ──> robosuite / MuJoCo
                          (1 thread/phiên)      │
                                          mp4 + HDF5
```

**Ba quyết định để giữ độ trễ thấp:**

| Quyết định | Lý do |
|---|---|
| WebSocket, không polling REST | Mỗi vòng điều khiển là một round-trip, REST quá đắt |
| **Input dùng mailbox depth-1, command dùng hàng đợi FIFO** | Input cũ **bỏ được** (robot phải bám tay người); mất một lệnh `stop` là **mất cả episode** |
| Render chậm thì bỏ frame hiển thị, **không bao giờ bỏ mẫu ghi** | Người xem chịu được giật; dataset thì phải liên tục |

> **Kết quả đo: p95 = 24.6 ms** (mục tiêu đặt ra: < 100 ms)

*Nói:* Slide này là chỗ ghi điểm kỹ thuật. **<u>Sự khác biệt giữa mailbox và hàng
đợi chính là sự khác biệt giữa "trễ tay" và "mất dữ liệu"</u>** — hai loại lỗi
khác hẳn nhau nên phải xử lý khác nhau. MuJoCo không thread-safe nên mỗi phiên
có đúng một thread sở hữu simulator; WebSocket handler không bao giờ chạm vào nó.

---

## Slide 5 — Thu data tự động: đa dạng có kiểm soát

### Trên slide

**Scripted operator** = máy trạng thái hữu hạn biết cách giải task

```
tiếp cận → căn chỉnh → hạ → ổn định → kẹp → nâng → giữ
              ↑                │
              └── phục hồi ────┘   (khi kẹp trượt)
```

Chạy trần thì 100 episode giống hệt nhau → thêm **perturbation có liều lượng**:

| Task | clean | good | medium | poor |
|---|---:|---:|---:|---:|
| Lift | 0.00 | 0.25 | 1.70 | 2.50 |
| Can | 0.00 | 0.25 | 1.10 | 1.20 |
| Square | 0.00 | 0.18 | 0.90 | 1.30 |

Nhiễu sinh từ **seed cố định** → cùng seed cho đúng cùng một episode, tái lập 100%

> **Tên preset là một *yêu cầu*, không phải nhãn chất lượng quan sát được.**
> Batch xin `medium` mà đo ra `good` thì **giữ nguyên** và ghi lại sai lệch.

*Nói:* Câu cuối là điểm liêm chính của thiết kế. **<u>Không có episode nào bị
resample, đổi nhãn hay vứt đi để đạt một tỷ lệ thành công đẹp.</u>** Episode thất
bại được ghi như mọi episode khác — vì tỷ lệ thành công thật là thứ cần đo.

---

## Slide 6 — Thu data tay: điều khiển bằng bàn tay qua webcam

### Trên slide

**Không bàn phím. Không gamepad. Chỉ cần webcam.**

**MediaPipe HandLandmarker** — 21 điểm mốc bàn tay, chạy ngay trên trình duyệt

| Cử chỉ | Lệnh |
|---|---|
| Vị trí lòng bàn tay | Dịch chuyển trục X, Y |
| Kích thước bàn tay (đưa gần camera → to hơn) | Dịch chuyển trục Z |
| Xoay cổ tay (yaw) | Xoay gripper |
| Nắm tay / xoè tay | Đóng / mở gripper |
| 👍 | **Clutch** — tạm ngắt để đưa tay về vị trí thoải mái |

60 Hz qua WebSocket → action 7 chiều `[dx,dy,dz,drx,dry,drz,grip]` → OSC_POSE

*Nói:* **Đây là slide nên demo trực tiếp.** Nhấn vào **clutch**: giống nhấc chuột
khỏi bàn di. **<u>Không có clutch thì tay người chạm mép khung hình sau 10 giây và
tính năng này chỉ là đồ chơi.</u>** Chi tiết nhỏ đó mới là thứ biến nó thành công
cụ dùng được. Bàn phím vẫn còn, và keybinding được đối chiếu **từng phím** với
driver `robosuite/devices/keyboard.py` để khớp quy ước gốc.

---

## Slide 7 — Hai phương pháp, một đường ống

### Trên slide

| | **Thu tay (teleop)** | **Thu tự động (scripted)** |
|---|---|---|
| Nguồn hành động | Bàn tay người qua webcam | Máy trạng thái + nhiễu có seed |
| Tốc độ | Chậm, cần người ngồi | Nhanh, chạy job nền hàng loạt |
| Đa dạng | Tự nhiên, khó lặp lại | Có kiểm soát, tái lập 100% |
| Trạng thái sim đặc quyền | Không đủ để tự kiểm chứng | Đầy đủ |
| Vào review | **Luôn cần người duyệt** | Có thể auto-pass |

Cả hai dùng **chung một `Episode` index, chung vòng đời trạng thái, chung cách
đóng gói**.

*Nói:* Dòng áp chót là quyết định thiết kế đáng nói nhất. **<u>Bản ghi tay không
lưu đủ trạng thái đặc quyền của simulator để máy tự kiểm chứng, nên nó luôn phải
qua mắt người — máy không được phép đoán.</u>**

---

## Slide 8 — Cấu trúc HDF5 RoboMimic

### Trên slide

```
dataset.hdf5
└── data/                        ← attrs: env_args, total
    ├── demo_0/                  ← attrs: num_samples,
    │   ├── actions    (N, 7)             source_episode_id,
    │   ├── states     (N, …)             review_decision
    │   ├── rewards    (N,)
    │   ├── dones      (N,)
    │   ├── obs/       {object, robot0_eef_pos,
    │   └── next_obs/   robot0_eef_quat, robot0_gripper_qpos}
    ├── demo_1/ …
└── mask/
    ├── train / valid            ← chia tập ổn định, không phụ thuộc thứ tự
    └── all / verified / clean   ← lọc theo mức kiểm chứng
```

Định dạng **chuẩn RoboMimic** → nạp thẳng, không cần viết loader riêng
Kèm **sha256**, dataset đã đóng gói là **bất biến**

*Nói:* Hai chi tiết đáng khoe:
1. `attrs` giữ `source_episode_id` và `review_decision` → **<u>từ một mẫu trong
   file huấn luyện truy ngược được ai tạo, ai duyệt</u>** — chính là lời giải cho P3.
2. `mask/` cho phép cùng một file huấn luyện trên toàn bộ hoặc chỉ trên phần đã
   kiểm chứng, chỉ bằng cách đổi tên mask.

---

## Slide 9 — Chấm điểm và kiểm duyệt

### Trên slide

**Hai tầng, cố ý tách rời:**

```
score = (tích các hard check) × (1 − penalty tệ nhất)
         ─────────┬─────────      ────────┬────────
          Sự thật: 0 hoặc 1        Cảnh báo mềm: [0,1]
          đọc trạng thái sim       độ giật, đường đi vòng,
          trả None nếu             bão hoà, đứng yên
          không kiểm chứng được
```

**Nhân** chứ không cộng → một lỗi nghiêm trọng cho ra **0**, không chỉ số đẹp nào
kéo lại được. **Lấy max** penalty → mười lỗi vặt không cộng dồn thành một lỗi giả.

**Auto-gate — ba phán quyết mô tả *mức độ kiểm chứng được*, không phải mức độ đẹp:**

| | Nghĩa |
|---|---|
| `reject` | Đã kiểm chứng và **hỏng** |
| `approve` | Mọi phép kiểm tra chạy được đều **đạt** |
| `review` | **Máy không có câu trả lời** → cần người |

*Nói:* Nhấn `review`. **<u>Máy nói "tôi không biết" thay vì đoán bừa — và đó là
thiết kế có chủ đích, không phải thiếu sót.</u>** Bổ sung: các penalty **không
gate** bất cứ thứ gì, vì nhóm tự nhận chưa hiệu chỉnh được ngưỡng của chúng trên
dữ liệu thật. Chúng được ghi lại để báo cáo, không dùng để quyết định.

---

## Slide 10 — Kết quả

### Trên slide

| Chỉ số | Kết quả |
|---|---|
| Độ trễ điều khiển | p50 **17.5 ms** · **p95 24.6 ms** (mục tiêu < 100 ms) |
| Task chạy được | **4** — lift_cube, pick_place_can, nut_assembly_square, tool_hang |
| Test tự động | **368 pass**, 15 skip |
| Kiểm tra kiểu frontend | sạch |

**Vòng đời chạy thông đầu-cuối:**
thu tay 3 camera → thu tự động → chấm điểm → duyệt → đóng gói HDF5 →
huấn luyện `bc`/`bc-rnn` → **rollout đánh giá trong sim kèm video**

Ứng dụng có 8 màn: Overview · Collect · Upload · Review · **Data diversity** ·
Datasets · Training · Users

*Nói:* Nhấn màn **Data diversity** — nó đo phân bố vị trí khởi tạo của vật thể,
tức là trả lời câu hỏi *"dữ liệu của tôi đã đủ đa dạng chưa"* **trước khi** tốn
tiền huấn luyện. Đánh giá dùng **seed chưa từng dùng lúc thu**, nếu không success
rate sẽ ảo cao.

---

## Slide 11 — Hạn chế hiện tại

### Trên slide

| Hạn chế | Ảnh hưởng |
|---|---|
| **Nền tảng huấn luyện chưa tối ưu phần cứng** | Mới chọn được `cuda`/`cpu`; chưa mixed precision, chưa multi-GPU |
| **Dataset scripted chưa đủ đa dạng** | Có nhiễu, nhưng vẫn xoay quanh **một chiến lược giải task duy nhất** |
| **Auto-label mới dừng ở mức ngưỡng** | So threshold để pass/reject, chưa "hiểu" episode |
| **Chưa hỗ trợ nhiều policy và model lớn** | Mới có `bc`, `bc-rnn`; chưa có Diffusion Policy / ACT |
| **Vision-based hạn chế vì thiếu depth camera** | Trục Z suy từ kích thước bàn tay → nhạy với khoảng cách ngồi, ánh sáng |

*Nói:* Trình bày thẳng. **<u>Nhóm biết rõ ngưỡng nào đã đo được và ngưỡng nào mới
là placeholder — và ghi thẳng điều đó vào code.</u>** Người chấm đánh giá cao việc
biết giới hạn của chính mình hơn là giấu đi.

---

## Slide 12 — Roadmap và kết luận

### Trên slide

| Mốc | Việc |
|---|---|
| Gần | Đa dạng hoá **chiến lược** giải task, không chỉ đa dạng nhiễu |
| Gần | Thu đủ cỡ mẫu để công bố success rate **kèm khoảng tin cậy** |
| Trung | Auto-label **học từ lịch sử quyết định của reviewer** thay vì chỉnh ngưỡng tay |
| Trung | Mở kênh ảnh làm observation → policy vision-based thật |
| Xa | Camera RGB-D cho điều khiển tay · Diffusion Policy / ACT |

> ### Kết luận
> TeleCollect đưa dữ liệu imitation learning đi trọn vòng đời — từ **bàn tay
> người điều khiển** tới **file HDF5 nạp thẳng vào thư viện huấn luyện** — với
> human-in-the-loop **ép bằng code**, mọi mẫu **truy ngược được tới người duyệt**,
> và độ trễ điều khiển **p95 24.6 ms**.

*Nói:* Chốt bằng đúng một câu, quay lại slide 2: **<u>Chúng tôi không giải bài
toán "thiếu dữ liệu". Chúng tôi giải bài toán "không biết dữ liệu tốt hay xấu".</u>**
Và mốc "auto-label học từ quyết định của reviewer" không phải ý tưởng thêm vào —
hệ thống ghi lại quyết định của người duyệt **ngay từ đầu** chính là để chuẩn bị
cho bước đó.
