# TeleCollect — Đặc tả nền tảng

Tài liệu này mô tả kiến trúc đích và các tính năng cần xây, ở mức đủ để phân
công và nghiệm thu. Không chứa code — chi tiết triển khai thuộc về lúc làm.

---

## 1. Kiến trúc

Hiện tại mọi thứ chạy trên một máy: sim, web, huấn luyện. Điều này giới hạn hệ
thống ở đúng một người dùng, trên đúng một máy có GPU.

Kiến trúc đích tách làm ba, mỗi phần đặt ở nơi nó chạy hiệu quả nhất.

```
┌── MÁY NGƯỜI DÙNG ──┐   ┌── WEB (VPS thường) ──┐   ┌── GPU THUÊ ──┐
│                    │   │                      │   │              │
│  App local         │──▶│  Tài khoản & team    │──▶│  Huấn luyện  │
│  · Sim + teleop    │   │  Review & duyệt      │   │  Đánh giá    │
│  · Thu episode     │   │  Dataset & phiên bản │◀──│              │
│  · GPU cá nhân     │   │  Điều phối job       │   │  Tắt khi rảnh│
└────────────────────┘   └──────────────────────┘   └──────────────┘
       episode                                          checkpoint

Dataset và checkpoint đi qua kho lưu trữ trung gian (S3) —
máy thuê không truy cập được ổ đĩa của server.
```

### Vì sao chia như vậy

| Phần | Đặt ở đâu | Lý do |
|---|---|---|
| Sim + render | Máy người dùng | Cần GPU và độ trễ thấp. Stream video qua internet luôn giật, và ảnh dùng để huấn luyện phải là ảnh gốc từ MuJoCo. |
| Quản lý dữ liệu | Web | Dữ liệu phải tập trung để nhiều người cùng review và chia sẻ. Không cần GPU. |
| Huấn luyện | GPU thuê | Chạy từng đợt, mỗi lần vài chục phút. Thuê theo giờ rẻ hơn nhiều so với giữ máy GPU chạy liên tục. |

### Điều kiện tiên quyết

**GPU của người truy cập web không dùng được.** Trình duyệt cô lập phần cứng —
không có cách nào để trang web gọi CUDA trên máy người xem. Đây là giới hạn cứng,
không phải thiếu công cụ. Mọi tính toán phải xảy ra ở server, ở máy thuê, hoặc
trong một ứng dụng cài đặt.

---

## 2. Tính năng 1 — Team và phân quyền dữ liệu

### Vấn đề

Hiện có ba vai trò (operator / reviewer / admin) nhưng không có khái niệm nhóm.
Mọi người thấy chung một kho dữ liệu. Không thể để hai nhóm cùng dùng hệ thống
mà dữ liệu tách biệt.

### Yêu cầu

- Một người thuộc nhiều team, chuyển qua lại được
- Mỗi liên kết người–team mang vai trò riêng: **owner** (mời/xóa thành viên),
  **member** (thu, review, train), **viewer** (chỉ xem)
- Episode, dataset, job huấn luyện đều thuộc về một team
- Người dùng chỉ thấy dữ liệu của team đang chọn
- Dữ liệu hiện có (925 episode, 8 dataset) không được mất khi chuyển đổi

### Việc cần làm

1. **Dựng công cụ migration.** Hiện chưa có, nên mọi thay đổi cấu trúc dữ liệu
   không có đường lùi. Phải làm trước tiên.
2. **Thêm bảng team và thành viên.**
3. **Gắn team vào dữ liệu.** Dữ liệu cũ gán vào một team mặc định — để trống sẽ
   khiến chúng biến mất khỏi giao diện sau khi lọc.
4. **Lọc theo team ở mọi nơi đọc dữ liệu.** Bốn khu vực: demo, dataset, labeling,
   training. Teleop không cần vì nó chuyển sang app local (xem mục 6).
5. **Giao diện quản lý team.** Mời thành viên, đổi vai trò, chọn team hiện hành.

### Rủi ro

**Rò rỉ dữ liệu giữa các team.** Bỏ sót một truy vấn là team này đọc được dữ liệu
team kia. Không kiểm được bằng mắt — phải có test tự động cho từng endpoint.

### Đánh giá hoàn thành

- [ ] Tạo hai team, mỗi team một episode. Tài khoản team A không thấy dữ liệu
      team B ở bất kỳ trang nào
- [ ] Test tự động cho mọi endpoint đọc dữ liệu, khẳng định team A nhận 404 khi
      gọi vào tài nguyên team B
- [ ] 925 episode và 8 dataset cũ vẫn truy cập bình thường
- [ ] Một người thuộc hai team chuyển qua lại được, dữ liệu đổi theo

---

## 3. Tính năng 2 — Token máy và nhận dữ liệu ngoài

### Vấn đề

Hai vấn đề độc lập, cùng chặn app local:

**App local không có cách tự xác thực.** Hiện chỉ có JWT ngắn hạn cho trình
duyệt. Ứng dụng chạy nền không thể bắt người dùng đăng nhập lại mỗi giờ.

**Upload chỉ nhận video.** Ai đã có sẵn dữ liệu robomimic hoặc tải từ HuggingFace
thì không đưa vào được. Đây là lỗ hổng lớn nhất với định vị "nền tảng" — hệ thống
hiện chỉ nhận được dữ liệu do chính nó sinh ra.

### Yêu cầu

- Token dài hạn, gắn với người dùng và team, tạo và thu hồi trên web
- Token chỉ hiện một lần lúc tạo; hệ thống lưu bản băm, không lưu bản gốc
- Upload nhận file HDF5 đúng chuẩn robomimic
- File hỏng bị từ chối ngay lúc nhận, kèm lý do cụ thể
- Upload file lớn chịu được mất mạng giữa chừng

### Việc cần làm

1. **Bảng token máy** kèm giao diện tạo/thu hồi
2. **Chấp nhận token ở lớp xác thực**, song song với JWT hiện có
3. **Kiểm tra cấu trúc HDF5 khi nhận** — có group `data`, có các `demo_*`, mỗi
   demo có `obs` và `actions`
4. **Upload theo khối**, cho phép tiếp tục từ khối dang dở

### Đánh giá hoàn thành

- [ ] Tạo token trên web, dùng nó gọi API thành công từ dòng lệnh
- [ ] Thu hồi token, gọi lại nhận 401
- [ ] Đẩy file HDF5 300MB lên, thấy nó xuất hiện đúng team
- [ ] Ngắt mạng giữa chừng rồi chạy lại — upload tiếp tục, không bắt đầu lại
- [ ] Đẩy file HDF5 sai cấu trúc — bị từ chối kèm thông báo nói rõ thiếu gì

---

## 4. Tính năng 3 — Ứng dụng thu dữ liệu tại máy

### Vấn đề

Teleop qua web luôn giật khi server không có GPU. Đo được: render một khung 640px
bằng phần mềm mất khoảng 45 ms, trong khi vòng điều khiển 60 Hz chỉ có ngân sách
16.7 ms mỗi bước. Ba camera cùng lúc ngốn hơn ba nhân CPU cho một phiên.

Giải pháp là đưa sim về máy người dùng, nơi có GPU và độ trễ bằng không.

### Yêu cầu

- Người dùng tải một file, cài, chạy — không cần biết Python hay Docker
- Đăng nhập một lần bằng token, nhớ cho các lần sau
- Thu xong tự đẩy lên nền tảng
- Mất mạng không mất dữ liệu: episode xếp hàng chờ, đẩy lại khi có mạng
- Chạy được trên máy Windows có hai GPU (tích hợp + rời)

### Việc cần làm

1. **Tách phần chạy độc lập** — sim, teleop, giao diện điều khiển. Phần backend
   đã tách sạch giữa quản lý phiên teleop và các router quản lý dữ liệu, nên
   việc này chủ yếu là ghép lại.
2. **Hàng đợi đồng bộ** — ghi ra đĩa trước, đẩy lên sau, thử lại khi thất bại
3. **Màn hình đăng nhập token**
4. **Đóng gói thành file cài đặt**
5. **Kiểm tra trên máy chưa từng cài Python**

### Rủi ro

**Đóng gói MuJoCo là chỗ dễ hỏng nhất của cả kế hoạch.** MuJoCo mang thư viện
nhị phân, robosuite mang tệp tài nguyên nằm ngoài gói Python — công cụ đóng gói
không tự tìm ra cả hai.

Máy Windows hai GPU cần đặt biến môi trường chọn card rời **trước khi** tạo ngữ
cảnh đồ họa đầu tiên. Hệ thống đã xử lý việc này, nhưng thứ tự khởi động trong
bản đóng gói khác với lúc chạy từ mã nguồn.

**Phương án dự phòng:** nếu bế tắc, phát hành bằng Docker và chấp nhận người dùng
phải cài Docker. Mất tính tiện lợi nhưng chắc chắn chạy được.

### Đánh giá hoàn thành

- [ ] Một thành viên trong nhóm, trên máy Windows chưa cài Python, tải file cài
      đặt về, cài, dán token, thu một episode, thấy nó lên web — không cần hỏi ai
- [ ] Tắt mạng, thu ba episode, bật mạng — cả ba tự lên nền tảng
- [ ] Trên máy hai GPU, kiểm tra sim dùng card rời chứ không phải card tích hợp

---

## 5. Tính năng 4 — Huấn luyện trên GPU thuê

### Vấn đề

Huấn luyện hiện chạy bằng tiến trình con ngay trên máy chạy web. Khi web nằm trên
VPS không GPU, bấm Train sẽ chạy trên CPU — chậm tới mức vô dụng.

### Yêu cầu

- Người dùng bấm một nút trên web, không cài gì, không gõ lệnh
- Hệ thống tự thuê GPU, chạy xong tự trả, không để máy chạy không
- Giữ được nhật ký chạy để theo dõi tiến trình
- Giữ nguyên hàng đợi và trạng thái job hiện có

---

### 5.1 Chọn nhà cung cấp

Ba lựa chọn thực tế:

| Nhà cung cấp | Mô hình | Ưu | Nhược |
|---|---|---|---|
| **RunPod Serverless** | Tự dựng máy khi có việc, tự tắt khi xong | Không tốn tiền lúc rảnh, không phải quản vòng đời máy | Khởi động chậm, nhật ký không chảy về thời gian thực |
| RunPod Pods | Thuê máy nguyên, tự bật tắt | Nhật ký thời gian thực, kiểm soát nhiều hơn | Phải tự nhớ tắt, quên là mất tiền |
| Vast.ai | Chợ máy của người lạ | Rẻ nhất | Máy có thể bị chủ lấy lại giữa chừng |

**Chọn RunPod Serverless** vì nó được thiết kế đúng cho tình huống này: chạy từng
đợt, không đoán trước lúc nào có việc. Phần còn lại của mục này mô tả theo lựa
chọn đó.

### 5.2 Chọn cấu hình GPU

RunPod gom các card cùng dung lượng vào một bậc giá, không tính riêng từng loại:

| Bậc | Card thuộc bậc | Giá/giờ |
|---|---|---|
| **16 GB** | A4000, A4500, RTX 4000, RTX 2000 | **0.58 USD** |
| 24 GB | L4, A5000, 3090 | 0.69 USD |
| 48 GB | L40, L40S, 6000 Ada | 1.75 USD |
| 80 GB | A100 | 2.72 USD |

Huấn luyện BC với backbone ResNet-18 và ảnh 84×84 chỉ dùng khoảng **4–6 GB
VRAM**. Bậc 16 GB là quá đủ; chọn bậc cao hơn chỉ tốn tiền mà không nhanh hơn
đáng kể, vì nút thắt nằm ở kích thước mô hình chứ không ở dung lượng nhớ.

### 5.3 Cách hoạt động

```
Người dùng bấm Train
      │
      ├─▶ Server đẩy dataset lên kho trung gian
      │
      ├─▶ Server gọi API RunPod, kèm cấu hình huấn luyện
      │        POST /v2/{endpoint}/run
      │        { "input": { "dataset_key": ..., "epochs": 200 } }
      │        ← nhận về mã job
      │
      │   RunPod dựng máy, tải gói phần mềm, chạy bộ nhận việc
      │        │
      │        ├─ tải dataset từ kho về
      │        ├─ chạy script huấn luyện đã có sẵn
      │        └─ đẩy checkpoint lên kho
      │
      ├─▶ Server hỏi trạng thái mỗi vài giây
      │        GET /v2/{endpoint}/status/{job}
      │        ← IN_QUEUE → IN_PROGRESS → COMPLETED
      │
      ├─▶ Server tải checkpoint từ kho về, lưu vào cơ sở dữ liệu
      │
      └─▶ RunPod hủy máy, ngừng tính tiền
```

Không có "kết nối" nào tới GPU. Chỉ là gọi HTTP kèm khóa xác thực, giống gọi bất
kỳ dịch vụ nào khác.

### 5.4 Ba thành phần phải chuẩn bị

**Gói phần mềm huấn luyện.** Máy RunPod dựng lên là máy trống — chưa có Python,
chưa có PyTorch, chưa có mã nguồn. Nó chỉ biết tải một gói đã đóng sẵn về rồi
chạy. Gói này chứa PyTorch bản CUDA, thư viện huấn luyện, và cả môi trường mô
phỏng vì vòng đánh giá chạy rollout trong sim. Kích thước khoảng **10 GB** —
đây là nguyên nhân chính của độ trễ khởi động.

**Kho lưu trữ trung gian.** Server web và máy thuê nằm ở hai nơi khác nhau, không
chia sẻ ổ đĩa. Dataset phải đẩy lên một kho mà cả hai cùng truy cập được;
checkpoint đi ngược lại. Cloudflare R2 miễn phí 10 GB và không tính phí tải ra —
đủ cho quy mô hiện tại.

**Bộ nhận việc.** Một đoạn chương trình chạy bên trong máy thuê: đọc cấu hình từ
yêu cầu, tải dataset về, gọi script huấn luyện đã có, đẩy kết quả lên kho. Đây là
thành phần duy nhất thật sự viết mới, khoảng 40 dòng.

### 5.5 Cấu hình endpoint

Trên RunPod tạo một *serverless endpoint* — điểm nhận việc, khai sẵn dùng gói nào
và GPU bậc nào:

| Mục | Giá trị | Lý do |
|---|---|---|
| Gói phần mềm | ảnh đã chuẩn bị ở 5.4 | |
| Bậc GPU | 16 GB | Đủ cho BC, rẻ nhất |
| Số máy tối thiểu | **0** | Không có việc thì không máy nào chạy — đây là chỗ tiết kiệm tiền |
| Số máy tối đa | 2 | Chặn trần chi phí khi nhiều người bấm cùng lúc |
| Thời gian chờ trước khi tắt | 5 giây | Tắt nhanh sau khi xong |
| Trần thời gian thực thi | 3600 giây | Chặn job treo đốt tiền |

### 5.6 Việc cần làm ở phía hệ thống

1. **Đẩy dataset lên kho** trước khi gửi job, và **tải checkpoint về** sau khi xong
2. **Đóng gói phần mềm huấn luyện** và đưa lên kho công cộng
3. **Viết bộ nhận việc** chạy bên trong máy thuê
4. **Đổi cách thực thi job** ở backend: thay tiến trình con bằng gọi API và hỏi
   trạng thái định kỳ. Hàng đợi, lưu trạng thái, bảng job giữ nguyên
5. **Đẩy nhật ký về theo chu kỳ**, vì dịch vụ chỉ trả kết quả một lần khi xong

### 5.7 Chi phí thực tế

Giả sử mỗi lần huấn luyện 40 phút trên bậc 16 GB (0.58 USD/giờ):

```
1 lần train   ≈ 0.39 USD
10 lần/tuần   ≈ 3.9 USD/tuần   ≈ 16 USD/tháng
```

Chấp nhận được nếu dùng vừa phải. Nhưng **không có hạn mức thì một người chạy
hai mươi tiếng trong một đêm là 11.6 USD chỉ trong một lần** — đó là lý do tính
năng 5 phải xong trước khi mở cho người ngoài.

Ba khoản đều bị tính tiền: thời gian khởi động máy, thời gian chạy, và khoảng
chờ trước khi tắt. Nên một job 2 phút vẫn tốn tiền của 5–7 phút.

### 5.8 Rủi ro

**Khởi động chậm.** Gói phần mềm 10 GB, lần đầu trong ngày máy thuê mất 2–5 phút
chỉ để tải về. Người bấm nút sẽ tưởng hệ thống treo nếu không có thông báo trạng
thái riêng cho giai đoạn này.

**Nhật ký không chảy về thời gian thực.** Serverless trả kết quả một lần khi
xong. Giao diện hiện có nhật ký trực tiếp — sẽ mất tính năng đó trừ khi bộ nhận
việc chủ động đẩy nhật ký lên kho theo chu kỳ.

**Job treo đốt tiền.** Nếu huấn luyện rơi vào vòng lặp vô hạn, máy vẫn chạy và
vẫn tính tiền. Phải đặt trần thời gian ở cả hai phía: trong bộ nhận việc và trên
cấu hình endpoint.

**Cần thẻ tín dụng quốc tế.** Mọi nhà cung cấp GPU đều yêu cầu. Đây là điều kiện
tiên quyết, nên kiểm tra trước khi bắt đầu.

### 5.9 Phương án dự phòng

Nếu không có thẻ quốc tế, hoặc muốn tránh chi phí, dùng mô hình **agent**:

```
Máy có GPU (máy cá nhân, máy lab)
      │
      ├─ mỗi 5 giây hỏi server: "có việc nào không?"
      ├─ có → tải dataset, huấn luyện, đẩy kết quả lên
      └─ không → chờ tiếp
```

Máy chủ động hỏi lên nên **không cần IP công khai, không mở cổng, ở sau NAT vẫn
chạy**. Đây là cách các nền tảng như Weights & Biases và GitHub Actions
self-hosted runner hoạt động.

So sánh:

| | Agent | RunPod Serverless |
|---|---|---|
| Chi phí | 0 | ~0.39 USD mỗi lần train |
| Cần thẻ quốc tế | Không | **Có** |
| Cần kho trung gian | Không | **Có** |
| Máy phải bật | **Có** | Không |
| Co giãn nhiều người | Không | **Có** |
| Lượng việc | Ít | Nhiều hơn đáng kể |

Agent làm được ngay và giống hệt về mặt trình diễn. RunPod đáng làm khi cần chạy
nhiều job song song — chẳng hạn thí nghiệm đo ảnh hưởng của dữ liệu cần 10–15 lần
huấn luyện.

### 5.10 Đánh giá hoàn thành

- [ ] Thử gọi dịch vụ trực tiếp từ dòng lệnh, nhận về checkpoint — **trước khi**
      sửa backend. Bỏ qua bước này thì lúc lỗi không biết nằm ở endpoint hay ở mã
- [ ] Bấm Train trên web, thấy nhật ký chạy, xong thì tải được checkpoint mà
      không đụng dòng lệnh nào
- [ ] Kiểm bảng chi phí nhà cung cấp: máy đã tự tắt sau khi xong, không còn máy
      nào chạy không
- [ ] Hủy job đang chạy từ web — máy dừng và ngừng tính tiền
- [ ] Gửi job với dataset không tồn tại — nhận thông báo lỗi rõ ràng thay vì
      treo vô hạn

## 6. Tính năng 5 — Hạn mức và triển khai

### Vấn đề

Hai việc khác nhau về kỹ thuật nhưng cùng phải xong trước khi mở hệ thống cho
người ngoài.

**Không có hạn mức.** Số epoch hiện cho tới 10 000 — vô lý với hệ thống nhiều
người dùng. Một người chạy hai mươi tiếng GPU trong một đêm là 11.6 USD chỉ trong
một lần.

**Chưa có bản triển khai công khai.** Web mới chạy trên máy cá nhân.

### Phạm vi server

**Server không phục vụ teleop.** Mô phỏng và điều khiển chạy ở app local, nơi có
GPU và độ trễ bằng không. Server chỉ làm bốn việc:

- Nhận dữ liệu từ app local
- Review và duyệt
- Quản lý dataset
- Điều phối job huấn luyện

Quyết định này bỏ đi toàn bộ phần render trên server — thứ chiếm hơn ba nhân CPU
cho mỗi phiên teleop. Nhờ vậy server chỉ cần cấu hình của một web app thông thường.

### Yêu cầu tối thiểu — máy người dùng

App local chạy mô phỏng và render ba camera ở 60 Hz. Đây là phần nặng nhất của
hệ thống.

| | Tối thiểu | Khuyến nghị |
|---|---|---|
| **GPU** | Rời, 4 GB VRAM, hỗ trợ OpenGL 3.3 | RTX 3050 trở lên, 6 GB |
| **CPU** | 4 nhân | 6 nhân trở lên |
| **RAM** | 8 GB | 16 GB |
| **Đĩa trống** | 10 GB | 20 GB |
| **Hệ điều hành** | Windows 10/11, Ubuntu 22.04 | |
| **Mạng** | 5 Mbps để đẩy dữ liệu lên | |

**Không có GPU rời thì không chạy được.** Card tích hợp render một khung 640px
mất khoảng 45 ms, trong khi vòng điều khiển 60 Hz chỉ có ngân sách 16.7 ms mỗi
bước — robot sẽ giật liên tục. Trên GPU rời, cùng khung đó mất 0.67 ms.

**Máy Windows có hai GPU** (tích hợp + rời) cần chọn đúng card rời. Hệ thống xử
lý việc này bằng biến môi trường đặt lúc khởi động, nhưng phải kiểm tra lại trong
bản đóng gói.

**Dung lượng đĩa:** mỗi episode khoảng 4 MB. 10 GB đủ cho khoảng 2 000 episode
chờ đồng bộ, cộng với phần mềm và mô hình mô phỏng.

### Yêu cầu tối thiểu — server

Không render, không mô phỏng. Chỉ phục vụ web, cơ sở dữ liệu và điều phối.

| | Tối thiểu | Khuyến nghị |
|---|---|---|
| **CPU** | 2 nhân | 4 nhân |
| **RAM** | 4 GB | 8 GB |
| **Đĩa** | 40 GB SSD | 100 GB |
| **GPU** | Không cần | |
| **Băng thông** | 2 TB/tháng | |

**Đĩa là chỗ cần tính trước.** Mỗi episode khoảng 4 MB, mỗi dataset HDF5 vài trăm
MB. Với 925 episode và 8 dataset hiện có, khoảng 8 GB. Cần dự trù tăng trưởng
hoặc chuyển sang lưu ngoài.

Không cần GPU, nên VPS thường khoảng 6–12 USD/tháng là đủ.

### Việc cần làm

1. **Trần thời gian mỗi job**, và hạ trần số epoch từ 10 000 xuống mức hợp lý
2. **Hạn mức giờ GPU theo team mỗi tuần**, hết thì job xếp hàng chờ kỳ sau
3. **Gỡ teleop khỏi bản triển khai server.** Giao diện thay bằng hướng dẫn tải
   app local — người vào web không nên thấy nút không dùng được
4. **Tên miền và chứng chỉ**, không mở cổng trực tiếp
5. **Kiểm tra tài nguyên** dưới tải thực để xác nhận cấu hình tối thiểu đúng

### Đánh giá hoàn thành

- [ ] Đặt trần 2 phút rồi chạy job — dừng đúng lúc, checkpoint cuối vẫn tải được
- [ ] Team hết hạn mức không gửi được job mới, nhận thông báo rõ ràng
- [ ] Truy cập được qua tên miền có HTTPS từ máy ngoài mạng nội bộ
- [ ] Trang teleop trên server hiển thị hướng dẫn tải app local, không phải giao
      diện điều khiển hỏng
- [ ] Chạy thử trên VPS 2 nhân 4 GB với năm người dùng đồng thời — không quá tải

## 7. Thứ tự thực hiện

```
Tính năng 1 (Team)
      ↓
Tính năng 2 (Token + HDF5)
      ↓
      ├──────────────────┐
      ↓                  ↓
Tính năng 3          Tính năng 4
(App local)          (GPU thuê)
      └──────────────────┘
      ↓
Tính năng 5 (Hạn mức + triển khai)
```

**Tính năng 1 phải làm đầu tiên** vì nó thay đổi cấu trúc dữ liệu. Hiện có 925
episode; càng thêm dữ liệu thì việc chuyển đổi càng rủi ro.

**Tính năng 3 và 4 độc lập với nhau**, làm song song được.

**Tính năng 5 phải xong trước khi mở cho người ngoài.**

---

## 8. Những gì đã có sẵn

Không phải xây từ đầu. Hệ thống hiện tại đã có:

| Đã có | Ghi chú |
|---|---|
| Thu dữ liệu teleop và scripted | 925 episode trên bốn nhiệm vụ |
| Kiểm định chất lượng tự động | 243 tự duyệt, 112 tự loại, kèm mẫu audit |
| Quy trình review có người | Cắt, gắn nhãn, duyệt |
| Xuất dataset HDF5 | 8 dataset, có phiên bản hóa |
| Huấn luyện qua giao diện | 16 siêu tham số |
| Đánh giá bằng rollout trong sim | Có quay video từng lần chạy |
| Ba vai trò người dùng | Nền cho phân quyền team |
| Hàng đợi job và lưu trạng thái | Nền cho việc gọi GPU thuê |

Kế hoạch này chủ yếu là **tách và nối lại** những phần đã chạy được, không phải
viết mới.

---

## 9. Khoảng trống chưa nằm trong kế hoạch

Ghi lại để không quên, nhưng chưa xếp vào giai đoạn nào:

- **Chọn tập con dữ liệu để huấn luyện.** Hệ thống đã ghi các nhãn phân loại
  chất lượng vào file HDF5, nhưng giao diện chưa cho chọn — nên hiện chỉ huấn
  luyện được trên toàn bộ. Đây là việc nhỏ nhưng mở khóa được phần thí nghiệm
  đo ảnh hưởng của dữ liệu.
- **Quét siêu tham số.** Chạy nhiều cấu hình rồi so bảng, thay vì bấm từng lần.
- **So sánh nhiều lần chạy trên một biểu đồ.** Hiện xem từng cái riêng lẻ.
