# Chất lượng dữ liệu — chấm điểm, gắn nhãn, và các mức nhiễu

Cách hệ thống quyết định một episode có dùng được hay không, và những chỗ cơ chế
đó còn hở. Mọi con số trong tài liệu này đo từ dữ liệu thật, không phải giá trị
đề xuất.

---

## 1. Công thức

```
score = (tích các hard check) × (1 − penalty tệ nhất)
         ────────┬────────       ───────┬───────
         0 hoặc 1, đọc từ sim    cảnh báo mềm [0,1]
         None nếu không          giật · đi vòng ·
         kiểm chứng được         bão hoà · đứng yên
```

**Nhân** chứ không cộng: một lỗi nghiêm trọng cho ra 0, không chỉ số đẹp nào kéo
lại được. **Lấy max** penalty: mười lỗi vặt không cộng dồn thành một lỗi giả.

Một phép kiểm không thực hiện được trả `None` và **bị loại khỏi tích**, chứ không
tính là đạt hay hỏng.

## 2. Ba phán quyết

Auto-gate mô tả *mức độ kiểm chứng được*, không phải mức độ đẹp:

| | Nghĩa |
|---|---|
| `reject` | Đã kiểm chứng và hỏng |
| `approve` | Mọi phép kiểm chạy được đều đạt |
| `review` | Máy không có câu trả lời → cần người |

Quyết định của người **không bao giờ bị ghi đè**. Một phần episode đủ điều kiện
tự duyệt vẫn giữ lại cho người xem (mẫu audit, 10% — riêng `tool_hang` là 20%),
để phát hiện khi chính cơ chế tự động sai.

Hai episode cùng vân tay trạng thái đầu + hành động thì cái sau bị loại là bản sao.

## 3. Các phép kiểm

| Hard check | Trạng thái |
|---|---|
| `E_integrity` | Số khung, giá trị hữu hạn, hình dạng và biên hành động |
| `E_success` | Cờ thành công của simulator |
| `E_skill` | Kẹp đóng vào vật, độ lệch ổn định k khung, vật rời mặt bàn, Can/Square có vận chuyển |
| `E_no_drop` | Vật rơi bao xa sau mỗi lần nhả |
| `E_stable_end` | Báo *không kiểm chứng được* trên episode thành công |
| `E_no_stray` | Chưa làm — cần định nghĩa "ngoài phạm vi nhiệm vụ" riêng cho từng task |
| `E_teleop` | Không áp dụng cho thu tự động |

Sáu penalty đã làm: độ giật, số lần đảo kẹp, đường đi vòng, độ dài bất thường,
chạm biên, đứng yên sau khi cắt.

**Penalty không gate bất cứ thứ gì** — ngưỡng chưa hiệu chỉnh trên dữ liệu thật,
nên chúng chỉ được ghi lại để báo cáo.

## 4. Vì sao có lý do, không chỉ phán quyết

Ngưỡng vẫn chỉ suy ra từ phán quyết — phần toán không đổi. Cái mà lý do mang lại
là khả năng trả lời *vì sao* một ngưỡng ra kết quả lạ, thứ mà ghi chú tự do không
tổng hợp được.

Mỗi lý do gọi tên phép kiểm hoặc penalty đáng lẽ phải bắt được nó. Khi người
duyệt tick `bad_grasp` trên episode mà `E_skill` trả 1, phép kiểm đó quá dễ dãi,
và chỗ cần sửa là phép kiểm chứ không phải ngưỡng. Báo cáo liệt kê những trường
hợp đó dưới mục **checks that missed**.

Penalty cố ý không bị đối chiếu kiểu này: một penalty là ước lượng đã hiệu chỉnh,
và bất đồng với nó là chuyện hiệu chỉnh, không phải lỗi.

Loại bỏ cần ít nhất một lý do hoặc một ghi chú. Chấp nhận thì không cần gì thêm.

## 5. Duyệt mù

Người duyệt thấy ý kiến của máy trước sẽ neo vào đó, và nhãn thu được mất giá trị
hiệu chỉnh. `review_cli.py` giấu điểm trừ khi truyền `--show-score`; API web giữ
lại **ở phía máy chủ** trừ khi client hỏi `include_score=true` — giấu bằng
JavaScript không phải là giấu, DevTools vẫn thấy phản hồi.

Thứ bị giữ lại nhiều hơn cả điểm số. `recorded_success` là cờ thành công của
chính simulator, và tỉ lệ review được định nghĩa là mức độ người khác với đúng cờ
đó; cho thấy nó thì con số sụp thành thước đo mức độ người chép lại máy. Khối
`provenance` còn tệ hơn — nó nói thẳng kết quả, giai đoạn thất bại và mức nhiễu.

**Còn một chỗ hở:** `episode_id` vẫn mang tên đợt thu (`lift_poor_seed0`), nên mức
chất lượng yêu cầu vẫn suy ra được. Bịt lại cần một mã review riêng không nói lên
gì, là việc sau MVP. Ghi ở đây để không ai tưởng chế độ mù là kín.

## 6. Video mà không cần thu lại

Lúc thu, bộ render ngoài màn hình tắt, nên không có ảnh nào được ghi. Nhưng toàn
bộ trạng thái MuJoCo từng khung thì có, cùng với XML cảnh — nên
`src/labeling/playback.py` phát lại episode rồi render sau.

Khôi phục một trạng thái và render tái tạo đúng khung hình gốc; điều này đã được
đối chiếu với một lần chạy trực tiếp. Khung nào nằm trong đoạn đề nghị cắt thì bị
làm mờ và đánh dấu `TRIM`.

## 7. Bốn mức nhiễu khi thu tự động

| Mức | Nhiễu | Lỗi ngữ nghĩa |
|---|---|---|
| `clean` | Không | Không |
| `good` | Nhẹ | Không |
| `medium` | Vừa | Nhiều nhất một, có kiểm soát |
| `poor` | Nặng | Nhiều nhất một, có kiểm soát |

Nhiễu chỉ tác động lên các mốc mà người vận hành lập kế hoạch từ đó, không phải
lên toàn bộ quỹ đạo.

Trộn có chủ đích: model chỉ thấy dữ liệu hoàn hảo sẽ hỏng khi gặp tình huống lệch.

## 8. Các quyết định hiệu chỉnh, và lý do

Giá trị khởi điểm trong tài liệu gốc dành cho teleop của người. Ba thứ phải đổi
khi nhìn thấy phân bố thật.

**Bán kính grasp theo từng task, và đó là hình học.** Quan sát báo gốc toạ độ
thân vật thể, không phải chỗ kẹp thực sự nắm. Đo trên các khung vật đang thực sự
được mang: Lift 0.007–0.040 m, Can 0.030–0.037 m, Square **0.054–0.087 m** — đai
ốc của Square được giữ ở tay cầm lệch khỏi gốc toạ độ. Một bán kính 0.06 m dùng
chung làm `E_skill` âm thầm đánh hỏng những episode Square hoàn toàn ổn.

**Dải bão hoà nới từ 0.02–0.15 lên 0.35–0.75.** Một scripted operator ra lệnh
delta gần hết thang theo thiết kế, nên cả episode sạch cũng bão hoà: 0.03–0.16
trên Lift, 0.04–0.45 trên Can, 0.03–0.60 trên Square. Dải cũ chấm episode sạch
bằng không.

**Penalty theo phân vị và MAD ngừng hoạt động dưới 30 episode mỗi task.** Một
phân vị là phát biểu về phân bố; trên tám episode thì nó không phải vậy, và
episode nào tình cờ giật nhất sẽ nằm ở phân vị 100 rồi bị phạt biến mất chỉ vì là
tệ nhất trong tám. Chúng báo `insufficient_corpus` và đóng góp 0. Phạt nhẹ hơn là
hướng an toàn — một penalty chỉ có thể đẩy episode về phía review.

**Ngưỡng rơi 0.15 m sau khi nhả.** Đặt đai ốc lên chốt tốn khoảng 0.10 m trên
episode Square sạch; nhả từ độ cao mang thì tốn hơn.

## 9. Hai hạn chế đáng biết

**Giai đoạn giữ sau thành công mới ghi cho `lift`.** Trước đây
`src/sim/tools/executor.py` thoát khỏi rollout ngay khung thành công đầu tiên,
nên không có khung nào sau đó tồn tại — `E_success` xác nhận thành công đã kích
hoạt nhưng báo `hold_verified: false`, và `E_stable_end` tự báo không kiểm chứng
được trên mọi episode thành công.

`lift` giờ ghi tiếp 30 bước (`post_success_steps=30`, khoảng 1.5 giây ở 20 Hz).
Ba task còn lại chưa. Thêm giai đoạn giữ làm đổi độ dài episode và do đó đổi
baseline đã ghi, nên đó là quyết định của dự án chứ không phải sửa âm thầm.

**Trạng thái sau hành động cuối chỉ nằm trong `next_obs`.** Vì thành công được
đánh giá sau khi bước, khung mà nhiệm vụ thực sự thành công lại là khung không
bao giờ xuất hiện trong `obs`. `EpisodeArrays` có một khung nhìn `*_trajectory`
dài `T + 1` đúng vì lý do này; đọc riêng `obs` làm mọi episode Lift thành công
trông như khối lập phương gần như không nhúc nhích. Có test hồi quy cho việc này.

## 10. ToolHang luôn chuyển cho người, có chủ đích

`src/services/auto_label.py` trả `review` cho mọi episode `tool_hang` không phải
thất bại rõ ràng, với lý do *"ToolHang accepts are pending a full quality rule"*.

Đây là quyết định, không phải sơ suất: các phép kiểm riêng cho task trong
`src/labeling/checks.py` mới chỉ đo giai đoạn 1
(`task_specific: "tool_hang_stage1_frame_transport"`), nên một `accept` sẽ khẳng
định nhiều hơn những gì thực sự kiểm chứng được. Giữ vậy tới khi có luật chất
lượng đầy đủ cho cả hai giai đoạn.

## 11. Lỗi đã biết: `wandering_path` phụ thuộc lô

**Chưa sửa — chủ dự án hoãn lại. Đừng coi điểm `wandering_path` là phát biểu
tuyệt đối về chất lượng.**

`src/labeling/penalties.py` tính penalty này thành tỉ số so với
`stats.path_length_median` — trung vị độ dài đường đi *của những episode khác
trong cùng lô chấm điểm*:

```
ratio = path_length_m / task_median_m
```

Nên cùng một episode, giống nhau từng byte, lại có điểm khác nhau tuỳ nó được
chấm cùng với kho nào. Thêm một lô episode dài làm những episode vốn bình thường
trông ra hiệu quả, và ngược lại.

Quan sát thực tế: trên một kho hỗn hợp, penalty này kích hoạt ở **0.9987**, kéo
`auto_score` của episode đó xuống **0.001253** — vì điểm là `(tích hard check) ×
(1 − penalty tệ nhất)`, một penalty tiến tới 1.0 xoá sổ điểm số bất kể episode
chạy tốt đến đâu.

Penalty láng giềng `unusual_length` cho thấy hình dạng đúng: nó có cổng
`relative_ready` và ngừng hoạt động dưới 30 episode mỗi task, trả
`insufficient_corpus`. `wandering_path` **không có cổng đó**, và đó chính là lỗi.
Sửa đúng cần một ngưỡng độ dài đường đi tuyệt đối cho từng task, đo từ episode
sạch, chứ không phải trung vị trong lô.

## 12. Ngưỡng và chấm lại

Chưa đặt ngưỡng nào, và không nên đặt tới khi `shadow_report.py` cho phép. Nó
đòi: không episode nào đã duyệt nằm dưới `tau_reject`, AUC ít nhất 0.75, và đủ
episode trong vùng duyệt — 300 để có giới hạn 1% duyệt sai theo quy tắc ba.

Hai ngưỡng là một phía và suy ra độc lập, nên chúng có thể ra theo thứ tự nào
cũng được. Khi hai quần thể chồng lấn thì `tau_reject < tau_approve` và khoảng
giữa là hàng đợi review. Khi điểm số tách chúng dứt khoát thì cặp ngưỡng **bắt
chéo**, và khoảng giữa không chứa episode quan sát nào. `decide()` chuyển khoảng
đó cho người trong cả hai trường hợp, thay vì để luật nào được kiểm trước thì
thắng.

`scorer_version` là băm của mọi ngưỡng trong cấu hình. Đổi bất kỳ cái nào thì
phiên bản đổi, và đó là tín hiệu phải chấm lại. Chấm điểm là thao tác theo lô và
tất định: cùng episode, cùng phiên bản, cùng kết quả.

Nếu robot, kẹp, thang hành động hay camera thay đổi thì hard check vẫn đo đúng sự
thật, nhưng dải penalty và mọi ngưỡng thì không. Tăng phiên bản, chấm lại, suy
lại, chạy lại chế độ bóng.
