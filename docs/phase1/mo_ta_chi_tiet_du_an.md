# TeleCollect — Mô tả chi tiết dự án

## Bài toán

Imitation learning dạy robot bằng cách cho nó bắt chước người: đưa vào một tập
**demonstration** (bản ghi đồng bộ giữa *quan sát* và *hành động*), robot học ra
policy. Nút thắt không nằm ở thuật toán mà ở dữ liệu — phải có người ngồi điều
khiển robot và ghi lại từng bước.

Đội không có công cụ nào để làm việc đó: không có giao diện teleoperation, không
có chỗ lưu bản ghi tử tế, không có quy trình lọc bản ghi hỏng, và không có đường
từ dữ liệu thô sang định dạng thư viện huấn luyện đọc được.

## Đối tượng sử dụng

Ba vai trò, quyền kế thừa nhau (`admin ⊇ reviewer ⊇ operator`):

- **Operator** — thu dữ liệu: điều khiển robot, ghi episode, gắn nhãn bản ghi của mình.
- **Reviewer** — kiểm duyệt: xem lại bản ghi của bất kỳ ai, cắt đoạn thừa, duyệt
  hoặc từ chối, đóng gói dataset.
- **Admin** — quản trị user và danh mục task.

## Cách TeleCollect giải quyết

Một nền tảng web đưa dữ liệu đi trọn vòng đời trong cùng một chỗ:

```
Điều khiển robot mô phỏng → ghi đồng bộ observation + action
   → reviewer duyệt → đóng gói dataset → huấn luyện behavior cloning
   → đo success rate ngược lại trong sim
```

Robot là cánh tay Panda mô phỏng bằng **MuJoCo** qua **robosuite**. Frontend
Next.js, backend FastAPI, nối nhau bằng **WebSocket** thay vì polling REST để giữ
độ trễ đủ thấp.

Điểm cốt lõi: **chỉ episode đã được duyệt (`approved`) mới vào dataset huấn
luyện**. Ràng buộc này ép bằng code, không phải bằng quy ước.

Có hai cách thu dữ liệu, dùng chung phần còn lại của đường ống.

## Thu tự động hoạt động như thế nào

Mỗi task có một **scripted operator** — máy trạng thái hữu hạn biết cách giải task
đó. Với task nâng khối lập phương, các pha là: tiếp cận → căn chỉnh → hạ xuống →
ổn định → kẹp → nâng → giữ, kèm hai pha phục hồi khi kẹp trượt.

Chạy đi chạy lại một kịch bản sẽ cho hàng trăm episode giống hệt nhau, vô dụng cho
huấn luyện. Nên hệ thống thêm **perturbation** theo bốn mức: `clean`, `good`,
`medium`, `poor`. Nhiễu sinh từ seed cố định, nên cùng seed luôn cho đúng cùng một
episode — tái lập được hoàn toàn.

Người dùng chọn task, mức chất lượng, số episode, seed rồi bấm chạy. Backend chạy
job nền, xong thì đẩy episode vào hàng chờ duyệt. Episode thất bại vẫn được ghi
bình thường — tỷ lệ thành công thật là thông tin cần đo, không phải thứ để giấu.

## Thu tay bằng camera hoạt động như thế nào

Phần khác biệt nhất: operator điều khiển robot **bằng bàn tay trước webcam**.

Trình duyệt chạy **MediaPipe HandLandmarker**, nhận diện 21 điểm mốc trên bàn tay
ngay tại máy client. Từ đó suy ra lệnh:

| Cử chỉ | Lệnh |
|---|---|
| Vị trí lòng bàn tay trong khung hình | Dịch chuyển theo trục X, Y |
| Kích thước bàn tay (đưa gần camera thì to hơn) | Dịch chuyển theo trục Z |
| Góc xoay cổ tay (yaw) | Xoay gripper |
| Nắm tay / xoè tay | Đóng / mở gripper |
| Cử chỉ 👍 | *Clutch* — tạm ngắt để đưa tay về vị trí thoải mái, như nhấc chuột khỏi bàn di |

Phải **calibrate** một lần để lấy tư thế tay làm gốc. Lệnh gửi qua WebSocket ở
60 Hz; backend dịch thành action 7 chiều `[dx, dy, dz, drx, dry, drz, grip]` cho
controller OSC_POSE, step simulator, rồi trả ảnh ba camera về dạng JPEG.

## Dữ liệu sinh ra

Mỗi episode lưu thành một thư mục gồm:

- **Video mp4** ba góc camera: `review_front`, `birdview`, và camera cổ tay
  `robot0_eye_in_hand`.
- **Chuỗi observation–action** theo timestamp: vị trí khớp (`qpos`), vận tốc khớp
  (`qvel`), pose end-effector, trạng thái gripper, action đã thực thi.
- **`meta.json`** ghi metadata và số đo độ trễ của chính phiên thu đó.

Luồng thu tự động ghi thẳng ra **HDF5** theo cấu trúc RoboMimic: `actions`,
`rewards`, `dones`, `observations`, `next_observations`.

## Review và export RoboMimic

Vòng đời episode: `recording → recorded → labeled → approved / rejected`.

Máy hỗ trợ người duyệt bằng ba thứ nhưng không thay người quyết định:

1. **Auto-gate** cho một trong ba phán quyết, mô tả *mức độ kiểm chứng được* chứ
   không phải mức độ "đẹp": `reject` là đã kiểm chứng và hỏng, `approve` là mọi
   phép kiểm tra chạy được đều đạt, `review` nghĩa là **máy không có câu trả lời**.
2. **Điểm chất lượng** theo công thức `score = (tích các hard check) × (1 −
   penalty tệ nhất)`. Nhân chứ không cộng, nên một lỗi nghiêm trọng cho ra 0 và
   không chỉ số đẹp nào kéo lại được.
3. **Auto-trim** đề xuất cắt đoạn đứng yên đầu/cuối (idle filter của DROID). Luôn
   chỉ là đề xuất, reviewer kéo lại tay được.

Khi đóng gói, hệ thống lọc episode `approved`, áp khoảng cắt reviewer đã đặt, rồi
xuất **HDF5 chuẩn RoboMimic** kèm chia tập train/validation ổn định và sha256 để
đối chiếu. Dataset đã đóng gói là bất biến.

## Kết quả hiện tại

- **Bốn task** chạy được: `lift_cube`, `pick_place_can`, `nut_assembly_square`,
  `tool_hang`.
- **Độ trễ điều khiển** đo trên máy phát triển: p50 ≈ 17.5 ms, **p95 ≈ 24.6 ms** —
  tốt hơn nhiều so với mục tiêu dưới 100 ms.
- **Toàn bộ vòng đời chạy thông**: thu tay ba camera, thu tự động, chấm điểm,
  duyệt, đóng gói, huấn luyện, đánh giá.
- **368 test tự động pass** (15 skip); kiểm tra kiểu frontend sạch.

## Hạn chế và hướng phát triển

- **Nền tảng huấn luyện chưa tối ưu phần cứng.** Job huấn luyện mới chỉ chọn được
  `cuda` hay `cpu`, chưa tận dụng GPU một cách bài bản: chưa có mixed precision,
  chưa có multi-GPU, chưa tinh chỉnh dataloader và batch size theo cấu hình máy.

- **Dataset sinh từ scripted algorithm chưa đủ đa dạng.** Nhiễu hiện áp lên độ trễ
  hành động, thời điểm đóng/mở gripper, sai lệch vị trí và hướng của mốc, cùng
  gain/bias của cánh tay — nhưng tất cả vẫn xoay quanh **một chiến lược giải task
  duy nhất** của máy trạng thái. Policy học từ đó khớp tốt với phân bố này, chưa
  chắc tổng quát hoá được sang mọi tình huống.

- **Auto-label mới dừng ở mức ngưỡng.** Bản chất là so sánh với các threshold cấu
  hình được (`rule_lift_height_m`, `rule_grasp_radius_m`, `rule_can_tail_speed_mps`…)
  để tự động pass hoặc reject. Nó không "hiểu" episode, nên mọi trường hợp mập mờ
  đều đẩy sang cho người duyệt.

- **Chưa hỗ trợ huấn luyện nhiều loại policy và model lớn.** Hiện chỉ có `bc` và
  `bc-rnn` (LSTM). Các họ mạnh hơn như Diffusion Policy hay ACT chưa tích hợp, chủ
  yếu do giới hạn phần cứng để huấn luyện và đánh giá chúng cho tử tế.

- **Điều khiển vision-based còn hạn chế vì thiếu depth camera.** Webcam thường chỉ
  cho ảnh 2D, nên chiều sâu (trục Z) phải suy ra gián tiếp từ **kích thước bàn tay
  trong khung hình** — tay đưa gần camera thì to hơn. Cách này nhạy với khoảng cách
  ngồi, góc đặt camera và ánh sáng, nên trục Z kém ổn định hơn hai trục còn lại.
  Dùng camera RGB-D sẽ cho toạ độ thật thay vì ước lượng.
