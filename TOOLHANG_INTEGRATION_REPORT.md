# Tích hợp task ToolHang — báo cáo

Ngày: 2026-08-13. Nhánh: `feat/frontend_backend`.
Nguồn yêu cầu: `D:\VIN_AI_TC\Test\INTEGRATION.md`.
Tài liệu kỹ thuật đi kèm: [docs/toolhang_integration.md](docs/toolhang_integration.md).

**Chưa commit, chưa push. Không đụng vào repo `D:\VIN_AI_TC\Test`.**

---

## 1. Điểm xuất phát

ToolHang **đã được tích hợp một phần từ trước**, dựng theo bản handover cũ. Tài
liệu mới lật ba giả định nền, nên việc lần này là *sửa và mở rộng*, không phải
tích hợp từ đầu.

| Vấn đề | Trước | Tài liệu mới |
|---|---|---|
| Nguồn nhãn | lọc theo `result.success` | §5.1: `success` **lỏng hơn** (40mm vs ~130mm), phải dùng `env_predicate` |
| Stage 2 | `stage1_ready_stage2_blocked` | §2: Stage 2 đã chạy được |
| Núm độ khó | không có | §4: `frame_extra`/`tool_extra`/`yaw_extra` là mặt điều khiển |
| Telemetry | vứt toàn bộ `trace`, `failure` | §5.3: đây là giá trị riêng của sim |

## 2. Ba quyết định đã chốt

1. **Phạm vi:** sửa Stage 1 + mở Stage 2 thành full task.
2. **Vendor `skillgen` vào repo**, vá tại chỗ — thay vì sửa upstream hay
   monkey-patch. Bỏ được đường dẫn cứng `D:\VIN_AI_TC\Test` và `sys.path` hack,
   chạy được trong Docker. Đổi lại: fork khỏi repo gốc, phải tự đồng bộ về sau.
3. **Phần chấm điểm chất lượng hoãn lại** — xem mục 7.

## 3. Điểm chặn cứng đã tháo

P-111 chạy **robosuite 1.5.2 / mujoco 3.8.1**; skillgen được kiểm định trên
**1.4.1 / 3.1.6**. Khoanh vùng được: **chỉ một tên bị gãy** —
`gripper0_grip_site` (1.4) vs `gripper0_right_grip_site` (1.5). Joint
(`robot0_joint1..7`) và toàn bộ body name đều khớp.

`primitives.py` sẵn có fallback nên Stage 1 chạy được; `stage2.py`,
`feasibility.py`, `graspplan.py` thì không → Stage 2 crash. Đã gom logic đó vào
`src/sim/skillgen/compat.py::grip_site_id()`.

Đo sau khi vá, dải rộng (`frame_extra=0.04, tool_extra=0.04, yaw_extra=0.35`),
`max_attempts=1`, seed 0-9:

```
stage1 env_predicate : 8/10   (seed 2 ik_unreachable, seed 8 grasp_missed)
stage2 tool_on_frame : 8/10   (8/8 số seed qua được stage 1)
~12s/episode
```

**Cảnh báo:** con số này đo khi seed còn chưa có tác dụng (mục 8bis) nên nó là
*một lần rút thăm*, không phải con số của mười seed đó. Số đo lại sau khi sửa:
stage1 10/10, stage2 9/10 (seed 0 hỏng), lặp ba lần trùng từng bit.

So với số 1.4.1 trong tài liệu (stage1 18/20, stage2 10/18, full 10/20):
**Stage 2 không hồi quy dưới 1.5.2.**

Xác nhận `Stage2` nối tiếp vào recorder của `Stage1` → một quỹ đạo liên tục,
`len(trace) == len(record)` (966 → 1724 trên seed 3).

## 4. Thay đổi theo file

**Mới — `src/sim/skillgen/`** (9 file vendor + 2 file tự viết)

Vendor: `env_setup.py`, `geometry.py`, `primitives.py`, `telemetry.py`,
`stage1.py`, `stage2.py`, `feasibility.py`, `graspplan.py`.
Bỏ: `viewer/report/dataset` (P-111 có pipeline riêng), `jointmove/probe`
(stage1/stage2 không import — chỉ xuất hiện trong comment).

Tự viết: `compat.py`, `__init__.py` (docstring provenance).

Đã đối chiếu `diff` với upstream — **chỉ đúng một sửa đổi**:

```
env_setup.py 0   geometry.py 0   telemetry.py 0   stage1.py 0
primitives.py 10   feasibility.py 6   stage2.py 3   graspplan.py 3
```

**`src/sim/tool_hang.py`** — xoá `DEFAULT_TOOLHANG_ROOT`, `project_root()`,
`_skill_imports()`, biến môi trường `TELECOLLECT_TOOLHANG_ROOT`. Đảo lại hợp
đồng success: `_check_frame_assembled()` (stage 1),
`_check_tool_on_frame()` (stage 2), full task = cả hai.

**`src/sim/tool_hang_collection.py`** — viết lại: chạy cả hai stage trong một
episode; **gate bằng `env_predicate`, hạ `success` xuống mức chẩn đoán**;
`max_attempts=1` (tài liệu §5.1: có retry thì `failure` ghi lỗi của lần cuối,
không phải lỗi thật); **giữ lại episode hỏng** kèm `failure_stage`/
`terminal_phase`; nối ba núm độ khó (mặc định 0.0, không đổi hành vi hiện tại);
trace ra sidecar JSONL.

**`src/sim/scripted_generation.py`** — nhánh `tool_hang` trước đây *bịa* record
`steps=0, success=True` cho mọi episode; nay dựng từ kết quả thật.

**`src/api/labeling.py`** — sửa một bug thật: check `tool_hang` nằm *bên trong*
`if quality not in supported_qualities()`, nên chỉ kích hoạt với quality vốn đã
không hợp lệ. Chạy ToolHang với `quality="good"` lọt qua API rồi nổ ở tầng
collector. Đã đưa ra ngoài.

**`scripts/run_toolhang_stage1.py` → `run_toolhang.py`** — script cũ gọi
`project_root()` để chạy sang script upstream chưa từng được vendor → code chết.
Viết lại thành CLI mỏng.

**`tests/test_tool_hang.py`** (mới, 19 test + 1 opt-in) — trước đó **không có
test nào** nhắc tới tool_hang.

## 5. Kiểm chứng

```
compileall : exit=0
pytest     : 1 failed, 201 passed, 38 skipped
ruff       : 44 lỗi (HEAD: 46) + 54 lỗi trong code vendor nguyên văn
```

Test fail là `test_rule_engine.py::test_can_rule_passes`. **Đã tự kiểm chứng
lại bằng cách stash sạch working tree** — vẫn fail ở HEAD, đúng là lỗi có sẵn,
không liên quan việc này.

Chạy thu thật 2 episode:

```
episode=0 seed=2 success=True stage1=True stage2=True steps=2510
episode=1 seed=3 success=True stage1=True stage2=True steps=1786
demo_0: object=(2510,14)   demo_1: object=(1786,14)   sidecar 2.2MB
```

## 6. Thay đổi schema — cần biết trước khi trộn dữ liệu

`obs/object` và `next_obs/object` của `tool_hang` đi từ **7 float lên 14**:
frame pos (0:3), frame quat (3:7), tool pos (7:10), tool quat (10:14). Bắt buộc,
vì Stage 2 thao tác với cờ lê mà layout cũ không quan sát được. Để frame lên
trước nên slice `(0,3)`/`(3,7)` trong `features.py` vẫn đúng, không phải sửa.

**Dataset thu trước thay đổi này có `object` rộng 7 và không trộn được với dữ
liệu mới.** Trộn sẽ báo lệch shape chứ không sai âm thầm.

## 7. Cố ý để ngoài phạm vi — nợ cần trả trước khi thu số lượng lớn

Chưa đụng `src/labeling/penalties.py` và `src/labeling/checks.py` theo quyết
định ở mục 2. Nhưng các rủi ro sau đã được xác nhận:

- `idle_after_trim = PenaltyBand(0.10, 0.40)` — episode ToolHang **đúng** có
  ~19% đứng yên và đoạn tĩnh 21.3s, *bắt buộc về vật lý* (thanh rơi vào lỗ nhờ
  trọng lực sau khi nhả kẹp — §6.4).
- `jerk_percentile` và `length_zscore` tính theo phân phối của lô — đúng cái
  §6.5 cấm. Tài liệu đã đo: hàng rào Tukey trên tập chỉ-thành-công loại nhầm 1
  episode đúng, còn trên tập bẩn hơn thì không loại ai.
- `EXPECTED_GRIPPER_TOGGLES` không có mục `tool_hang`, mặc định 2; full task gắp
  hai lần.
- `checks.py:236` — nhánh `E_skill` chỉ kiểm chứng vận chuyển khung của Stage 1,
  nên episode Stage 2 đang bị chấm bằng luật Stage 1.

**Hệ quả: bật thu Stage 2 lúc này thì episode làm đúng sẽ bị chấm trượt.** Nên
xử lý trước khi thu nhiều, không thì phải chấm lại toàn bộ.

## 8. Điểm tài liệu handover nói không khớp thực tế

Mục 8.1–8.3 đã được **tự đo lại và kết luận**; xem mục 8bis bên dưới.

1. **§4 nói cùng seed + cùng tham số luôn cho cùng kết quả — không đúng.** Chạy
   lại cùng seed cho cùng kết cục nhưng khác số bước (seed 3: 971→1838 rồi
   967→1834 ở bản upstream; 966→1724 rồi 977→1864 ở bản vendor).
2. **Số 8/10 không lặp lại được**, kể cả với code upstream. Bốn lần chạy cùng 10
   seed cho stage1 8/10, 10/10, 7/10, 8/10 — và seed nào hỏng thì đổi mỗi lần.
   Kết quả bản vendor nằm trong biên độ của bản upstream nên **không phải hồi quy
   do vendor**, nhưng n=10 quá nhỏ để phân biệt hồi quy với nhiễu.
3. **Kết quả phụ thuộc episode chạy trước đó trong cùng env.** Seed 3 chạy một
   mình: thành công, 2877 bước. Seed 3 chạy sau seed 2: `grasp_missed` ở bước 74.
   `hard_reset=False` + env dùng chung làm rò trạng thái qua `reset_and_settle`.
   **Đây là vấn đề chất lượng dữ liệu thật** — episode không độc lập với nhau.
4. **Sai lệch §5.1 mà baseline không thấy thì đã xuất hiện** khi chạy nhiều hơn:
   seed 2 cho `s1_geom=True` nhưng `s1_env=False`. Code cũ gate bằng `success`
   nên sẽ ghi episode đó thành clean với thanh kẹt lưng chừng. Tức là **bug này
   có thật, không phải lý thuyết.**
5. `telemetry.StepTelemetry.stage` **luôn bằng 1**, kể cả trong stage 2 — không
   chỗ nào set thành 2. Trường `phase` vẫn phân biệt được nên thông tin không
   mất, nhưng trường `stage` đang gây hiểu nhầm. Vendor nguyên văn, chưa sửa.
6. `telemetry.PHASES` **thiếu** — trace thật phát ra `home`, `align_handle`,
   `lower_ring`, `slide_inboard` không có trong danh sách. Code nào coi `PHASES`
   là đầy đủ sẽ hỏng.

## 8bis. Kiểm chứng lại mục 8.1–8.3: nguyên nhân và cách sửa

Đã tự đo. **Hiện tượng có thật, nhưng nguyên nhân không phải như đã đoán.**

### Nguyên nhân thật

`np.random.seed(seed)` trong `env_setup.py` **không điều khiển gì cả** trên
robosuite 1.5. 1.4 lấy vị trí vật thể và nhiễu tư thế ban đầu của robot từ RNG
toàn cục của numpy; 1.5 chuyển hết sang một generator riêng của env:

```
environments/base.py:142        self.rng = np.random.default_rng(seed)
environments/manipulation/tool_hang.py:397   UniformRandomSampler(..., rng=self.rng)
environments/robot_env.py:526   robot.reset(deterministic=..., rng=self.rng)
```

`make_env` không truyền `seed` cho `suite.make`, nên `env.rng` được gieo bằng
entropy hệ thống và `np.random.seed` không chạm tới được. **Mỗi lần reset là một
cảnh mới, bất kể seed.** Đây là cùng một loại lỗi với `grip_site_id`: hỏng do
lệch phiên bản, không phải do logic.

### Số đo (seed 3, dải rộng, `max_attempts=1`)

Đo trạng thái đã lắng ngay sau `reset_and_settle` (băm `qpos`+`qvel`) — phép thử
sắc nhất, vì nếu trạng thái đầu đã khác thì phân kỳ nằm ở reset chứ không ở
điều khiển.

| Phép đo | Trước | Sau |
|---|---|---|
| 16 lần lấy trạng thái đã lắng (env mới / reset lặp / đổi thứ tự / sau 600 bước ngẫu nhiên) | 16 trạng thái khác nhau, lệch tới 0.38 rad | 1 trạng thái, trùng từng bit |
| Episode đầy đủ, env mới, 5 lần | 5 số bước khác nhau (1911–2392), 2 kết cục | 969→1723 cả 5 lần |
| Seed 3 chạy một mình / sau seed 2 / sau seed 2,5 | 3 số bước khác nhau, một lần `grasp_missed` ở 204 bước | giống hệt lần chạy một mình |
| Quét 10 seed, lặp 3 lần | stage1 9/10, 9/10, 10/10; seed hỏng đổi mỗi lần ([7], [6], []) | trùng từng bit; stage1 10/10, stage2 9/10 |

Seed vẫn còn tác dụng: seed 0–5 cho 6 cảnh khác nhau.

**Chi phí: bằng không.** Cùng bộ 41 episode: 601 s trước, 584 s sau. Gieo lại
một bit generator không phải dựng lại model.

### Hai thứ bị nghi oan

- **`hard_reset=False` không rò trạng thái.** Sau 600 bước hành động ngẫu nhiên,
  lần reset có seed kế tiếp vẫn trùng từng bit với lần chạy độc lập. Và **phải
  giữ `False`**: `hard_reset=True` chạy lại `_load_model`, dựng lại
  `placement_initializer` nên **xoá mất phần nới dải** của ba núm `*_extra`
  (đo được: `x_range` của frame từ `[-0.10, 0.02]` về `[-0.06, -0.02]`).
- **Ba chỗ `np.random.default_rng()` không gieo hạt không bao giờ chạy tới.**
  Đo bằng cách bọc hàm trong 41 episode thật: cả ba đều 0 lần gọi.
  `plan_grasp` và `best_of` không có ai gọi; `holdable` chỉ được gọi từ
  `Stage2._ring_upright_mat`, mà hàm này cũng không có ai gọi. Giữ nguyên văn,
  chỉ ghi lại như một cạm bẫy trong docstring của `compat.seed_env`.

### Đã sửa

`src/sim/skillgen/compat.py::seed_env(env, seed)` gieo lại `env.rng` **tại chỗ**
(sampler giữ tham chiếu tới đúng object đó, gán lại `env.rng` sẽ không tới được
chúng), `env_setup.py` gọi nó ở cả `make_env` và `reset_and_settle`. Ghi thành
sai khác #2 trong `src/sim/skillgen/__init__.py`. Test chốt tính chất này:
`tests/test_tool_hang.py::test_same_seed_reproduces_the_same_settled_scene`
(opt-in, `TELECOLLECT_RUN_SIM_TESTS=1`, ~2 s).

### Hệ quả

- Mọi dataset thu **trước** thay đổi này có trường `seed` không tái tạo được
  episode của chính nó.
- Mọi tỉ lệ thành công đo trước đó (kể cả `8/10` ở mục 3 và trong
  `docs/toolhang_integration.md`) là **một mẫu rút ra từ phân phối**, không phải
  con số của mười seed đó. Số đúng: stage1 10/10, stage2 9/10 (seed 0 hỏng).

## 9. Việc còn lại, xếp theo mức ưu tiên

1. Xử lý ngưỡng chấm điểm (mục 7) — chặn việc thu số lượng lớn.
2. ~~Điều tra rò trạng thái giữa các episode (mục 8.3)~~ — **đã xong**, mục 8bis.
   Không phải rò trạng thái; là seed không có tác dụng. Đã sửa và có test chốt.
3. Tự kiểm chứng lại các phát hiện còn lại ở mục 8 (8.4–8.6) bằng số của mình.
4. Quyết định nhà cuối cùng cho per-step trace (hiện là sidecar JSONL, tạm thời).
5. `src/sim/tasks.py` đặt `max_steps=1500`, quá ngắn cho full task (~3400 bước).
   Chỉ ảnh hưởng đường teleop/playback, không ảnh hưởng scripted collection.
6. Cân nhắc đẩy ba núm độ khó lên UI. §6.1 cảnh báo: ở dải mặc định mọi episode
   hội tụ về đúng một điểm (`radial = 6.0mm` ở cả 20 seed), nên **số episode
   không phải thước đo giá trị dữ liệu** — trang `/scripted` hiện đang đưa số
   episode làm núm chính, dễ khiến người dùng hiểu sai.
