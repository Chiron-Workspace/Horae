# NOTES — Part 1A

Các điểm spec mơ hồ và cách diễn giải (code vẫn theo đúng spec chữ đen, chỉ ghi rõ lựa chọn ở chỗ chữ đen chưa phủ hết):

## Diễn giải

1. **Luật 3 (break ngoài DayWindow) khi ngày áp dụng KHÔNG có DayWindow** — Spec không nói rõ break áp dụng cho ngày mà `day_windows` thiếu ngày đó thì sao, nhưng test bắt buộc số 4 yêu cầu `day_windows` thiếu Chủ Nhật vẫn hợp lệ, không raise. Diễn giải: **bỏ qua kiểm containment cho ngày không có window** (ngày đó không xếp gì thì break cũng không có ý nghĩa). Preset `PRESET_STUDENT_VN` có break mọi ngày nhưng vẫn validate được khi thiếu ngày trong `day_windows`.

2. **`BreakWindow` với `start >= end`** — 11 luật của `validate()` chỉ cấm điều này cho `DayWindow`, không nói cho `BreakWindow`. Diễn giải: vẫn raise `ConfigError` cho break có `start >= end` (break âm/độ dài 0 là vô nghĩa và sẽ phá các phép tính interval ở part sau). Đây là kiểm mở rộng, không thay mặt luật nào trong 11 luật.

3. **`LeisureRule.days` rỗng** — Spec chỉ chú thích "rỗng = mọi ngày" cho `BreakWindow`, không nói cho `LeisureRule`. Diễn giải: **cũng hiểu là mọi ngày** (0..6), đồng nhất với `BreakWindow`.

4. **Luật 8 (leisure > DayWindow)** — Kiểm tra **tổng thời lượng DayWindow** đúng như chữ spec, không trừ breaks. Chỉ kiểm các ngày có mặt trong `day_windows`; ngày áp dụng leisure mà thiếu window thì bỏ qua (đồng nhất với điểm 1).

5. **Chỉ `Interval` có kiểm tra tz-aware và `start < end` ở `__post_init__`** — Spec chỉ ghi `__post_init__` cho `Interval`. `FixedEvent`/`Assignment`/`Block` tin dữ liệu đầu vào (part sau luôn dựng chúng từ `Interval` đã hợp lệ hoặc từ nguồn bên ngoài). Nếu part sau cần chặt hơn, sẽ bổ sung.

6. **`duration_minutes`** — floor của `total_seconds() / 60`. Spec quy định thời lượng là int phút nên mặc định input luôn lẻ phút chẵn.

7. **`total_minutes` không tự `normalize`** — Theo đúng test bắt buộc số 7 ("normalize rồi total_minutes"): caller chịu trách nhiệm normalize nếu đầu vào chồng lấn, không thì double-count là lỗi của caller.

8. **`find_contiguous_block`** — "latest" = block có **start muộn nhất** (đặt sát cuối interval kết thúc muộn nhất); "earliest" = start sớm nhất (đặt sát đầu interval bắt đầu sớm nhất). Block dài **đúng** `minutes`, không dài hơn. `minutes <= 0` → ValueError. `placement` là tham số bắt buộc (spec không ghi default).

9. **`to_dict` và khóa `day_windows`** — JSON object key phải là string nên `dict[int, DayWindow]` serialize thành `{"0": ..., "6": ...}`; `from_dict` chuyển ngược về `int`. `time` serialize `"HH:MM"`: nếu ai đó tạo `time` lẻ giây thì round-trip sẽ mất giây — mọi preset và dữ liệu thật chỉ dùng phút chẵn.

10. **`subtract` với `base` chưa normalize** — Hàm sort `base` nhưng không tự hợp nhất (chỉ `cuts` được normalize). Nếu `base` chứa các đoạn chồng nhau, kết quả vẫn đúng từng đoạn nhưng có thể chồng nhau; caller nên normalize `base` trước.

11. **Chỉ số ngày ngoài 0..6** — `days` của break/leisure hoặc key của `day_windows` ngoài 0..6 không được kiểm (spec không yêu cầu); giá trị ngoài phạm vi sẽ bị bỏ qua khi xét theo ngày.

## Môi trường

- Venv `.venv/` tạo bằng `uv` (Python 3.12.13), pytest 9.1.1. Chạy test: `.venv/bin/python -m pytest tests/ -v` (hoặc `pytest` từ root — `conftest.py` ở root đảm bảo `import scheduler_core` chạy được).

## Kết quả Part 1A

- 34 test pass (18 config + 16 intervals).

---

# NOTES — Part 1B (`capacity.py`)

## Diễn giải

1. **Đệm di chuyển chỉ áp cho event đã qua bước lọc (bước 2).** Spec bước 5 nói "ap cho event `requires_travel=True`" nhưng bước 2 đã bỏ `is_all_day`/`is_opaque=False`. Diễn giải: travel chỉ tính trên event giữ lại (opaque & không all-day). Một event `requires_travel=True` nhưng `is_opaque=False` → không có đệm (vì nó cũng không bị trừ khỏi khung trống, không vào busy).

2. **`LeisureRule.days` rỗng = mọi ngày** — đồng nhất với Part 1A. Spec bước 7 viết chữ đen `day.weekday() in config.leisure.days` (rỗng thì False), nhưng để nhất quán với diễn giải Part 1A, dùng `not leisure.days or weekday in leisure.days`. Cả preset đều dùng `{5,6}` nên không test nào chạm trường hợp rỗng.

3. **Gap chuỗi dùng strict `<`** — gap đúng bằng `chain_gap_threshold` thì KHÔNG thành chuỗi (test 3). Bắt cầu theo thứ tự sort: A–B < thr và B–C < thr ⇒ A,B,C cùng chuỗi.

4. **"Trừ trọn khoảng giữa các event trong chuỗi"** = trừ từng gap `[a.end, b.start]` giữa hai event liên tiếp trong chuỗi (không phải trừ span từ first.start đến last.end). Gap âm/độ dài 0 (`a.end >= b.start`) thì bỏ qua (tránh `Interval` start≥end).

5. **Nghỉ hồi chỉ cho chuỗi cuối cùng trong ngày** (sort theo start). Một event travel đơn lẻ cũng là "chuỗi cuối" → nhận recovery. Recovery rơi ngoài khung ngày → `subtract` không cắt gì, không raise (test 6).

6. **Thứ tự bước**: leisure (7) → drop mảnh vụn (8) → cắt deadline (9). `dropped_fragments` chụp ở bước 8 (trước cắt deadline); `free_intervals` là kết quả sau cắt deadline.

7. **`deadline_cutoff <= window.start`** → `free = ()` (né khởi tạo `Interval` với start≥end). Bình thường cutoff nằm trong ngày nên không chạm.

8. **`busy_minutes`**: normalize (hợp nhất chồng lấn) → clip vào khung ngày → total. Event vượt `day_end` bị cắt đúng biên (test 7: 21:30–22:45 → 30').

9. **Không thêm hàm vào `intervals.py`** — 7 hàm sẵn của Part 1A đủ dùng cho `capacity.py`.

## Vấn đề test phân biệt chain vs non-chain

PRESET có `pre+post = 120+75 = 195'` > `chain_gap_threshold = 150'`. Khi gap < 150 thì pre+post luôn phủ kín gap, nên kết quả free intervals của "một chuỗi" và "nhiều chuỗi riêng" giống hệt nhau — không thể phân biệt bằng `free_intervals`. Vì vậy test 3 và test 4 dùng **config travel riêng** (`pre=15, post=15, recovery=0`, "test nói khác") với gap lớn hơn pre+post để mảnh tự do giữa các event lộ ra khi KHÔNG thành chuỗi, và bị xóa sạch khi thành chuỗi. Logic `compute_day_capacity` vẫn dùng PRESET bình thường; chỉ test mới đổi config.

## Vấn đề test "capacity cao hơn hẳn" (test 20)

`capacity = min(free_total, ceiling)`. Với preset, `ceiling_free=360` hay `ceiling_busy=240` đều cắt `free_total`, nên gỡ leisure (tăng `free_total`) thường không tăng `capacity`. Test 20 dùng config ceiling cao (`ceiling_free=720`, "test nói khác") để `capacity = free_total`, thấy rõ leisure giảm `free_total` đúng 180' và capacity giảm theo.

## Kết quả Part 1B

- 23 test pass. Tổng cộng 57/57 (34 Part 1A + 23 Part 1B).

---

# NOTES — Part 1C (`allocate.py`)

## Xung đột spec lớn nhất: `Σ == remaining` vs "làm tròn lên"

Spec bước 1 nhánh A viết: `đơn_vị_tổng = remaining ÷ allocation_unit` "(phải chia hết; không chia hết → làm tròn **lên** rồi ghi `NOTES.md`)". Nhưng ngay sau đó, bất biến 4 nói "**Σ phân_bổ phải bằng đúng remaining — không hơn không kém**", và test 5 yêu cầu task `remaining` không chia hết vẫn ra `Σ == remaining`.

Hai câu mâu thuẫn: "làm tròn lên" (làm Σ = ceil×unit > remaining) vs "Σ == remaining". Theo nguyên tắc "code đúng spec + ghi NOTES khi mâu thuẫn", mình ưu tiên **bất biến mạnh `Σ == remaining`** (câu nhấn mạnh "không hơn không kém") và test 5, vì "làm tròn lên" chỉ là ngoặc đơn phụ. Cách thực hiện:

- `total_units = remaining // unit`, `residual = remaining % unit`.
- Phân `total_units` đơn vị bằng largest remainder (Σ units = total_units).
- Phần dư `residual` (< unit) gánh vào ngày có phần dư lớn nhất → ngày đó có phân bổ `units×unit + residual` (không phải bội của unit, nhưng là cách duy nhất để Σ == remaining khi `remaining` không chia hết).

Kết quả: mọi task đều `Σ == remaining` (khi đủ chỗ). Đã ghi nhận lệch chữ đen "làm tròn lên" tại đây.

## Dọn mảnh vụn trong nhánh A — diễn giải "không ngày nào chứa nổi"

Khi largest remainder tạo ra phân bổ `0 < x < min` (vd 60' trên 22 ngày → 4×15'), bước dọn cho các ngày đó về 0 và gom lại. Spec nói "lặp tới khi mọi phân bổ khác 0 đều >= min" rồi "nếu không ngày nào chứa nổi → dồn hết vào ngày fc lớn nhất". Diễn giải:

- Còn ngày "sống sót" (đã >= min): gom `collected` phân lại cho chúng bằng **cùng largest remainder** (tỉ lệ fc), cộng thêm trên đỉnh → không tạo mảnh mới < min.
- Hết ngày sống sót (tất cả đã về 0) **nhưng** vẫn có ngày `fc >= min`: rải `collected` thành các **khối cỡ `min`** theo thứ tự fc giảm dần (hòa nhau → ngày sớm hơn). Phần dư `< min` gộp vào khối đã xếp đầu tiên. Đây là cách test 4 (60' hạn 22 ngày → 30+30 trên 2 ngày) và test 10 (90' → 3 ngày) thoả "không dồn hết / không nén vào 2 ngày đầu".
- Không ngày nào `fc >= min` (thực sự không chứa nổi): fallback chữ đen — dồn `collected` vào ngày fc lớn nhất (có thể < min; đây là trường hợp spec nêu, đã ghi nhận có thể vi phạm bất biến "không trả về < min" nhưng là fallback của spec).

## `free_capacity` chỉ trừ task khẩn hơn

`free_capacity(ngày) = capacity_minutes(ngày) − tổng đã cấp cho các task khẩn hơn` (task phục vụ trước, sort theo deadline tăng). Phân bổ của chính task hiện tại trên các ngày khác **không** làm giảm `free_capacity` của nhau trong cùng task — đúng chữ spec. Sau khi xong một task, phân bổ của nó mới được cộng vào `used` cho task sau thấy.

## Nhánh B — "ngày sớm nhất có đủ chỗ", không phải "trống nhất"

Nhánh B duyệt `days(task)` theo thứ tự thời gian, chọn **ngày đầu tiên** có `free_capacity >= remaining` → cấp trọn. Test 6 kiểm chứng. Không ngày nào đủ → chia thành các phần `>= min` theo thứ tự thời gian (test 8). Nhánh B không làm tròn theo `allocation_unit` (spec chỉ bắt làm tròn ở nhánh A); chỉ tuân thủ `>= min hoặc 0`.

## Ongoing — kiểm "còn mảnh >= min sau khi trừ assignment"

`leftover = capacity − Σassignment_alloc − existing_blocks`. Điều kiện cấp: `leftover >= min` **và** `longest_interval(free_intervals) >= min`. Vì vị trí assignment trong `free_intervals` được định ở part 1D (chưa biết), mình dùng `longest_interval` của `free_intervals` (đã trừ busy/break/leisure/mảnh vụn/cutoff) làm đại diện cho "còn mảnh liên tục >= min sau khi trừ assignment". Bất biến thực: `leftover` (đã trừ assignment) đảm bảo ongoing không vượt capacity / không cướp phút assignment; `leisure_interval` đã nằm ngoài `free_intervals` nên ongoing không đụng. Task nhận `< min` → nhận 0, `leftover` không đổi → task sau cũng 0 (không chia nhỏ).

## Test 12

`allocate_assignments` không nhận `ongoing` làm tham số nên "bỏ ongoing ra" đồng nghĩa gọi lại cùng đầu vào → kết quả giống hệt (hàm thuần, deterministic).

## Không sửa file part 1A/1B

`allocate.py` chỉ import từ `intervals` (`longest_interval`) và `models`/`config`. Không thêm hàm vào `intervals.py`, không đổi chữ ký hàm cũ.

## Kết quả Part 1C

- 20 test pass. Tổng cộng 77/77 (34 + 23 + 20).

---

# NOTES — Part 1D (`blocks.py`, `validate.py`)

## Mâu thuẫn quy tắc chia block (mục 1)

Spec nói: "chia thành `n = ceil(phần / max_minutes)` block đều nhau nhất" và "chọn `n` nhỏ nhất thỏa mọi block <= max và >= min". Sau đó ghi: "Nếu bạn thấy mâu thuẫn với ví dụ '135 → 45/45/45' ở tài liệu cũ, theo quy tắc viết ở đây (n nhỏ nhất) và ghi mâu thuẫn vào NOTES.md."

Với 135 và max=90, min=30: `n = ceil(135/90) = 2` → [68, 67]. Cả hai <= 90 và >= 30, chênh nhau 1 phút. Đây là `n` nhỏ nhất hợp lệ. Ví dụ cũ "45/45/45" (n=3) không phải `n` nhỏ nhất → **theo quy tắc mới**, ghi nhận mâu thuẫn tại đây.

## Gap là tối thiểu, không phải chính xác

"Khoảng nghỉ: giữa hai block cùng task cách nhau `same_task_gap`" — diễn giải là **tối thiểu** `same_task_gap`, không phải chính xác. `round_start_to` có thể đẩy giờ bắt đầu xa hơn (vd gap 10' nhưng round 30' → gap thực 30'). Test 3/4 dùng `round_start_to=None` để kiểm gap chính xác.

## Không đủ chỗ → bỏ phần còn lại

Spec: "Không đủ chỗ đặt hết → đặt được bao nhiêu thì đặt, phần còn lại bỏ." `cut_blocks` không raise, không cảnh báo. Nếu điều này xảy ra trong test, ghi vào đây — chưa xảy ra trong 21 test của part 1D.

## `before_deadline` dùng strict `<`

Block assignment phải kết thúc **trước** deadline: `block.end < deadline` (không phải `<=`). Test 11: deadline 21:00 cùng ngày, block 19:00–20:00 → 20:00 < 21:00 ✓. Nếu block kết thúc đúng 21:00 → fail.

## `no_day_dominance` dùng `remaining // 2`

"Không ngày nào chiếm > 50% remaining" → kiểm `day_alloc > remaining // 2` (chia nguyên). remaining=200 → ngưỡng 100, ngày 120 > 100 → fail. remaining lẻ (vd 201) → ngưỡng 100 (201//2), 101 > 100 → fail.

## `small_task_buffer` — xác định nhánh B

`run_checks` không nhận `first_day` trực tiếp, nên lấy `first_day = min(capacities.keys())`. Sau đó tính `days_until = (deadline.date() - first_day).days` và áp đúng công thức nhánh B của part 1C. Block của task nhánh B phải có `start.date() < deadline.date()` (trước ít nhất 1 ngày).

## `run_checks` tham số `blocks`

`blocks: Mapping[date, Sequence[Block]]` — dict từ ngày sang list block của ngày đó. `allocation: Mapping[date, Mapping[str, int]]` — kết quả của `allocate_assignments`.

## Không sửa file part 1A–1C

`blocks.py` import `config`, `models`. `validate.py` import `config`, `models`. Không thêm hàm vào `intervals.py`/`capacity.py`/`allocate.py`, không đổi chữ ký cũ.

## Kết quả Part 1D

- 21 test pass (12 blocks + 9 validate). Tổng cộng 98/98 (34 + 23 + 20 + 12 + 9).

---

# NOTES — Part 1E (`plan.py`, tích hợp)

## Không sửa file part 1A–1D

Không phát hiện bug thật trong file part trước. `plan.py` chỉ import và gắn kết các hàm sẵn có: `compute_day_capacity`, `allocate_assignments`, `allocate_ongoing`, `cut_blocks`, `run_checks`. Không thêm hàm mới vào module cũ, không đổi chữ ký.

## `dend` khi không có assignment

Spec: "Không có assignment nào → dend = d1 + write_horizon_days - 1." Ongoing task không tham gia quyết định dend. Test 12 kiểm chứng: `assignments=[]` → `capacities` chỉ có 2 ngày (write window), ongoing vẫn được xếp vào cả hai.

## `infeasible` vs `ok`

Task `remaining > capacity_task` → vào `infeasible_task_ids`. Nhưng `ok` chỉ phản ánh kết quả `run_checks` (10 mục). Nếu các block đã đặt đều hợp lệ (trong window, đúng size, trước deadline...) thì `ok=True` kể cả khi task bất khả thi (test 11: 3000' hạn 2 ngày → `infeasible=('big',)`, `ok=True`). Bất biến `within_remaining` chỉ kiểm `alloc <= remaining` (không kiểm `alloc == remaining`), nên phân bổ thiếu không gây fail.

## Test 17 — leisure và cuối tuần

Fixture chuẩn có `dend = Fri` (deadline xa nhất), nên Sat/Sun không nằm trong plan. Test 17 dùng `today=THU` → `d1=FRI`, `write_horizon=4` → write window = Fri–Mon, và thêm assignment hạn Sunday để kéo `dend` qua cuối tuần. So sánh Saturday với/không leisure: capacity 240 vs 360 (leisure cắt 180' free, nhưng ceiling_free=360 chặn mũ nên chênh lệch thực = 120').

## Test 19 — `no_day_dominance` fail khi chặn horizon

`planning_horizon=2` → dend = Tue. Vật Lí 180' dồn vào Mon(105') + Tue(75'). 105 > 90 (50% × 180) → `no_day_dominance` fail → `ok=False`. Đây là hành vi đúng: lịch quá dồn, check báo rõ. Test chỉ assert `dend` bị chặn và phân bổ dồn hơn, không assert `ok=True`.

## Bất biến "ongoing không ảnh hưởng assignment" (test 13)

`allocate_assignments` không nhận `ongoing`. `cut_blocks` đặt block theo thứ tự assignment→ongoing; assignment luôn đặt trước nên vị trí không phụ thuộc ongoing. Hai lần chạy với/không ongoing → list block assignment giống hệt (so sánh `(task_id, start, end)`).

## Spec còn sai / mơ hồ

Danh sách toàn bộ chỗ tôi cho spec còn vấn đề (xem chi tiết ở các phần Part 1A–1D tương ứng):

1. **Part 1A, luật validate 3**: break áp dụng ngày không có DayWindow — spec không nói rõ; diễn giải: bỏ qua (không raise). Test 4 yêu cầu đúng vậy.
2. **Part 1A**: `BreakWindow` có `start >= end` không nằm trong 11 luật — mình vẫn raise (mở rộng).
3. **Part 1A**: `LeisureRule.days` rỗng = mọi ngày — spec không nói; diễn giải đồng nhất với `BreakWindow`.
4. **Part 1C**: "làm tròn lên" vs "Σ == remaining" khi `remaining` không chia hết `allocation_unit` — mâu thuẫn; ưu tiên bất biến `Σ == remaining`.
5. **Part 1C**: "nếu không ngày nào chứa nổi → dồn hết vào ngày fc lớn nhất" — diễn giải thành rải khối `min` theo fc giảm; chỉ fallback dồn 1 ngày khi không ngày nào `fc >= min`.
6. **Part 1D**: quy tắc chia block "n nhỏ nhất" mâu thuẫn với ví dụ cũ "135 → 45/45/45" — theo quy tắc mới (n=2 → 68/67).

## Kết quả Part 1E

- 19 test pass. Tổng cộng 117/117 (34 + 23 + 20 + 12 + 9 + 19).

---

# NOTES — Part 1F (sửa sau review)

## Bốn test cũ đã bị thay

1. **test_12 (allocate)** — cũ: gọi `allocate_assignments` hai lần với cùng tham số rồi so sánh (tautology — kiểm tính xác định, không phải bất biến ongoing). Mới: `test_12_allocate_ongoing_does_not_mutate_assignment_alloc` — gọi `allocate_ongoing` rồi kiểm `a_alloc` không bị mutate (deepcopy snapshot). Thêm `test_12b_allocate_assignments_is_deterministic` kế bên cho kiểm tính xác định có tên đúng.

2. **test_19 (allocate)** — cũ: assert `_sum_task == 60` hai lần, comment nói kiểm "nhiều hơn 1 ngày" nhưng không có assert đó. Mới: so sánh nhánh B (threshold mặc định 90, 90<=90 → 1 ngày) vs nhánh A (threshold 30, 90>30 → 3 ngày × 30'). **Lưu ý:** spec dùng 60' nhưng 60' với min=30 → largest remainder cho 20'/ngày < 30 → cleanup gộp hết vào 1 ngày → không phân biệt được A với B. Đổi thành 90' (90/3=30 ≥ min → A thực sự chia 3 ngày). Ghi nhận lệch spec.

3. **test_13 (allocate)** — cũ: `all(m > 0 for ...)` pass kể cả khi `o_alloc` rỗng (all() trên tập rỗng = True). Mới: thêm `assert len(given) > 0` trước, rồi kiểm `all(m >= min_minutes)`.

4. **test_14 (integration)** — cũ: `sum([]) <= 0` luôn đúng. Mới: so sánh có/không `existing_blocks` — không có → ongoing > 0; có 240 → ongoing == 0.

## Giá trị kỳ vọng đổi do `chain_gap_threshold = 210`

`PRESET_STUDENT_VN.travel.chain_gap_threshold`: cũ 150 → mới 210.

**Không có giá trị kỳ vọng nào trong integration test phải đổi** vì fixture chỉ có một chuỗi (Thứ Ba: Toán 14–17, IELTS 18–20, gap 60') — gap 60 < cả 150 và 210, nên kết quả capacity và block không thay đổi. Lý do đổi threshold: luật 12 cấm `threshold <= pre + post` (120+75=195). Cũ: 150 ≤ 195 → vi phạm. Mới: 210 > 195 → hợp lệ.

## Bug mất phút im lặng — đã sửa

### Vấn đề
`_allocate_branch_a` có `if minutes[d] > fc[d]: minutes[d] = fc[d]` — clip mất phần dư không dấu vết. `residual` dồn vào ngày có dư lớn nhất có thể đã sát fc → bị clip. Đường quá tải trong `_largest_remainder` cũng đặt 0 cho ngày `fc < min`, làm `Σ < total` mà không ai biết.

### Sửa
1. **Safe residual**: `_largest_remainder` dồn `residual` vào ngày đầu tiên (theo dư giảm dần) CÒN CHỖ (`minutes[d] + residual <= fc[d]`), không phải luôn ngày có dư lớn nhất. Không ngày nào còn chỗ → rải nhỏ.
2. **Clip recovery loop**: `_allocate_branch_a` sau khi clip, tính `lost = remaining - Σ`, phân `lost` cho ngày còn chỗ bằng largest remainder, lặp tối đa `len(days)` lần.
3. **`AllocationResult`**: `allocate_assignments` trả về `AllocationResult(by_day, shortfall)` thay vì dict thuần. `shortfall[tid] = remaining - Σ phân_bổ`. `AllocationResult` có dict-like delegation (`__getitem__`, `values()`, `items()`, `get()`, ...) để test cũ không vỡ.
4. **`_cleanup_min` fix**: nhánh `else` (hết survivor còn chỗ) dùng `remaining_fc = fc[d] - minutes[d]` thay vì `fc[d]`, và `minutes[d] += placed[d]` thay vì `minutes[d] = placed[d]` (không ghi đè phân bổ đã có).
5. **`PlanResult`** thêm `shortfall: dict[str, int]`.

### Hội tụ
Clip recovery loop lặp tối đa `len(days) + 1` lần. Mỗi vòng: clip → tính lost → phân lost → cleanup. Nếu không còn ngày nào có chỗ (`room_days` rỗng) → break, phần còn lại vào `shortfall`. Test 21–25 kiểm chứng bất biến `Σ + shortfall == remaining`.

## `ok` và `warnings`

`CheckResult` thêm `severity: Literal["error", "warning"] = "error"`. `no_day_dominance` và `small_task_buffer` → `"warning"`, 8 check còn lại → `"error"`. `PlanResult.ok = all(c.passed for c in checks if c.severity == "error")` — warning fail không làm `ok=False`. `PlanResult.warnings = tuple(name for c in checks if c.severity == "warning" and not c.passed)`.

## `_place_min_blocks` — ưu tiên ngày sớm

Cũ: sort `(-fc[d], d)` — sức chứa lớn nhất trước. Mới: duyệt theo **thứ tự thời gian tăng dần** — đặt khối `min` vào mọi ngày có `fc >= min` cho tới hết `total`. Test 30 kiểm chứng: 60' trên 10 ngày (ngày 0–2 fc=60, ngày 3–9 fc=240) → hai khối 30' vào ngày 0 và 1, không phải ngày 8 và 9.

## Quy tắc chia block mới

`_split_part` thêm tham số `allocation_unit`. Ưu tiên:
1. Mọi block là bội của `allocation_unit`
2. `n` nhỏ nhất
3. Đều nhau nhất

Không cách nào toàn bội unit → bỏ điều kiện 1, lấy n nhỏ nhất + đều nhau. `cut_blocks` truyền `config.blocks.allocation_unit`; test cũ gọi `_split_part(135, 30, 90)` không truyền unit → fallback cũ (n nhỏ nhất) → vẫn pass.

## File part trước đã sửa

| File | Lý do |
|---|---|
| `models.py` | Thêm `severity` vào `CheckResult` (mục 3.2) |
| `config.py` | Thêm luật validate 12, đổi PRESET `chain_gap_threshold` 150→210 (mục 5) |
| `allocate.py` | `AllocationResult`, safe residual, clip recovery, `_place_min_blocks`, `_cleanup_min` fix (mục 2+4) |
| `blocks.py` | `_split_part` unit-aware (mục 6) |
| `validate.py` | `severity` cho 10 check (mục 3) |
| `plan.py` | `shortfall`, `ok`, `warnings`, dùng `AllocationResult.by_day` (mục 2+3) |

## Spec còn sai (cập nhật)

7. **Part 1F, test_19**: spec dùng task 60' để phân biệt nhánh A/B, nhưng 60' với min=30 → largest remainder cho 20'/ngày < 30 → cleanup gộp vào 1 ngày → không phân biệt được. Đổi thành 90' (ghi nhận trên).

## Kết quả Part 1F

- 137 test pass (117 cũ + 20 mới). 0 test chuyển từ xanh sang đỏ ngoài danh sách.

---

# NOTES — Task 2A (adapter + state store)

## Mâu thuẫn spec test 24 (ledger biên `<`)

Spec: "Block kết thúc đúng 00:00 của D1 → được đếm (kiểm biên `<`)." Nhưng luật nói "kết thúc TRƯỚC `before`" = strict `<`. Nếu `end == before` thì `end < before` = False → không đếm. Test 24 nói "được đếm" nhưng luật strict `<` nói không. Mâu thuẫn. Code theo luật (strict `<`), test kiểm chứng cả hai biên: `end == before` → không đếm, `end < before` → đếm.

## `scheduler_core` không sửa

Không thêm `default_ongoing_minutes` vào `SchedulerConfig` (vùng cấm). Đặt `DEFAULT_ONGOING_MINUTES = 60` trong `horae/settings.py` riêng.

## Chống trùng theo `(task_id, date)`

Spec: "block cùng `task_id` cùng ngày đã tồn tại → bỏ qua." Diễn giải: key = `(task_id, start.date())`. Nếu một block cho `vatli` @ D1 đã tồn tại, không tạo thêm block `vatli` nào khác @ D1 — kể cả khi giờ khác. Test 34 ban đầu dùng hai block cùng `task_id` cùng ngày, cả hai bị skip; đã đổi block 2 sang `task_id="hoa"` để test đúng ngữ nghĩa.

## `RunReport` không có `ok` trực tiếp

`RunReport` có `plan: PlanResult` (chứ `ok` nằm trong `plan.ok`) và `errors: tuple[str, ...]` (chỉ khác rỗng khi `plan.ok=False`). Test dùng `report.plan.ok` và `report.errors`.

## `FakeHttp` handler theo URL pattern

FakeHttp khớp handler theo `pattern in url`. Khi nhiều calendar cùng gọi `/events`, tất cả khớp cùng handler → trả cùng response. Test 15 dùng callable handler kiểm `cal_id in url` để phân biệt.

## GCal `list_events` gọi một lần cho cả khoảng

Reader gọi `calendarList` một lần, rồi `events` cho mỗi calendar (trừ bị loại) trong khoảng `[timeMin, timeMax]`. Nhóm theo ngày trong memory (trong runner, không trong reader). Không gọi từng ngày.

## Todoist POST vs GET

Todoist Sync API dùng POST. FakeHttp mặc định nhận GET, nhưng `_post` trong `TodoistSource` gọi `urllib` trực tiếp khi fetch=default; khi fake, gọi `self._fetch(url, headers)` (test tự xử lý). Test dùng FakeHttp trả response cho URL chứa `sync`.

## File part trước không sửa

Không sửa `scheduler_core/`. Adapter phụ thuộc vào core, không ngược lại.

---

# NOTES — Sửa sau review (3 điểm + test_19)

## Bug `_cleanup_min` xác nhận và sửa

**Vấn đề:** `_cleanup_min` gom phút từ ngày có phân bổ < min, rồi đổ lên **survivor** (ngày đã có phân bổ >= min). Với 60'/3 ngày: largest remainder cho 30/15/15 → cleanup gom 30, survivor day0 (30) nhận thêm 30 → 60/0/0 (1 ngày). Nhưng 60'/22 ngày: tất cả 15 → không survivor → `_place_min_blocks` rải 30/30 (2 ngày). Cùng nhánh A, kết quả khác nhau.

**Sửa:** `_cleanup_min` giờ ưu tiên **zero-days** (ngày chưa có phân bổ, có `fc >= min`) trước. Dùng `_place_min_blocks` (thời gian) rải collected cho zero-days → nhất quán. Chỉ khi không zero-day nào chứa nổi mới fall back survivor → fallback cuối.

**Sau sửa:** 60'/3 ngày → 30/30/0 (2 ngày), 60'/22 ngày → 30/30/0 (2 ngày). Nhất quán. `test_19` trả về 60' (spec gốc) và giờ thật sự phân biệt A vs B: B cho 60/1 ngày, A cho 30+30/2 ngày.

## Lỗ hổng chống trùng — sửa từ existence sang tổng phút

**Vấn đề:** Chống trùng theo `(task_id, date)` existence → nếu writer lỗi giữa chừng (tạo 1/3 block), lần sau đọc thấy task đã có mặt trong ngày → bỏ qua **tất cả** block còn lại. Mất 90' học không báo.

**Sửa:** Đổi sang so sánh **tổng phút**:
- `existing_minutes(task, day)` vs `planned_minutes(task, day)`
- `existing >= planned` → bỏ qua toàn bộ (idempotent)
- `existing < planned` → "tiêu thụ" existing theo từng block theo thứ tự, tạo bù phần thiếu. Block tạo bù được đánh dấu `recovered` trong report.
- `existing > planned` → không tạo, cảnh báo lệch

`RunReport` thêm trường `recovered: tuple[Block, ...]`. Test 38 dựng đúng kịch bản: writer lỗi sau block 0 → chạy lại → 2 block còn lại được tạo bù, report ghi `recovered`.

`test_34` đã trả về hai block cùng `task_id` ("vatli"), khẳng định cả hai bị skip khi tổng khớp (60' existing = 60' planned).

## Điểm 4 — cổng an toàn là tautology cho output của cut_blocks

Thử dựng 5 ca dữ liệu thật (không monkeypatch) cố làm cut_blocks sinh block sai:

1. **free_intervals chồng lấn** (manually constructed): cut_blocks đặt block trong fragment đầu → không chồng lấn. run_checks không bắt lỗi nào (chỉ warning `small_task_buffer` vì deadline cùng ngày).
2. **Deadline giữa ngày** (deadline 10:00): cut_blocks đặt block 07:00-08:00, kết thúc trước 10:00. Không vi phạm `before_deadline`.
3. **min_fragment < blocks.min** (bypass validate): fragment 15' < blocks.min=30 → cut_blocks không đặt block → không có gì để check.
4. **Round đẩy past deadline** (07:15 → round 07:30, +60=08:30 > deadline 08:00): cut_blocks skip rounded, fallback 07:15+60=08:15 > 08:00 → skip. Không đặt block.
5. **Gap exhaustive**: hai block 60' trong hai fragment 60' → đặt đúng, không chồng.

**Kết luận:** Không dựng được ca nào làm cut_blocks sinh block sai. cut_blocks tự thực thi tất cả ràng buộc nội bộ (trong free_intervals, trong [min,max], trước deadline, không chồng qua gap cursor). `run_checks` kiểm lại chính các giả định này → **tautology** đối với output của cut_blocks.

**Cổng chỉ bắt được lỗi từ:**
1. Block thủ công constructed/edited (không qua cut_blocks)
2. Thay đổi upstream (build_plan bị sửa, dữ liệu hỏng)
3. Configuration thay đổi sau khi block đã tạo

**Không bắt được:** lỗi nội tại của cut_blocks (vì cut_blocks và run_checks dùng cùng giả định). Đây là thông tin quan trọng trước khi bật xóa: cổng error là thứ duy nhất đứng giữa lịch cũ và mất trắng, nhưng nó chỉ bảo vệ nếu nguồn lỗi nằm ngoài cut_blocks.

## Điểm 1 — ledger đổi `<` thành `<=`

Spec test 24 nói "block kết thúc đúng 00:00 D1 → được đếm" nhưng luật nói "kết thúc trước" (strict `<`). Mâu thuẫn. Theo ngữ nghĩa, test đúng hơn: block kết thúc lúc 00:00 đã hoàn toàn thuộc quá khứ. Đổi luật thành `end > before` (tức `end <= before` → đếm). Thực tế không chạm (day_end=22:00, không block nào kết thúc nửa đêm).

## File đã sửa

| File | Lý do |
|---|---|
| `scheduler_core/allocate.py` | `_cleanup_min`: ưu tiên zero-days thay vì survivor (fix bug 60'/3days) |
| `scheduler_core/allocate.py` | `test_19` trả về 60' (spec gốc), giờ phân biệt được A vs B |
| `horae/runner.py` | Chống trùng đổi sang tổng phút; `RunReport` thêm `recovered` |
| `horae/state/ledger.py` | Đổi `<` thành `<=` (điểm 1) |
| `tests/test_runner.py` | `test_34` trả về cùng task_id; thêm `test_38` phục hồi ghi dở |
| `tests/test_allocate.py` | `test_19` dùng 60' (spec gốc) thay vì 90' |
| `tests/test_ledger.py` | `test_24` kiểm `<=` thay vì `<` |
| `tests/fakes.py` | `FakeCalendarWriter` thêm `fail_all_after` |

## Kết quả

- 178 test pass (177 cũ + test_38 mới). 0 test chuyển từ xanh sang đỏ ngoài danh sách.

---

# NOTES — Task 2B (LLM provider)

## Provider error mapping

Mỗi provider dùng chung `_raise_error` (trong `anthropic.py`, import bởi các provider khác):

| HTTP | LLM error type | Fallback? |
|---|---|---|
| 401, 403 | `LLMAuthError` | Có |
| 429 | `LLMQuotaError` | Có |
| 400–499 (không 401/403/429) | `LLMBadRequestError` | KHÔNG — dừng ngay |
| 500+ | `LLMTransientError` | Có |
| Other | `LLMTransientError` | Có |

DeepSeek, OpenAI, OpenCode Zen dùng cùng `_raise_error` vì shape lỗi giống nhau (OpenAI-compatible API). Anthropic cũng dùng chung (chỉ khác endpoint + header).

## LLM là tùy chọn thật

- `parse_title` với `llm=None` → trả mặc định (Assignment, 60', source='default'). Không raise.
- `parse_title` regex match → trả ngay, LLM không được gọi (test 47: `call_count == 0`).
- `runner.py` không nhận `llm` làm tham số. LLM chỉ được gọi qua `parse_title` khi adapter cần parse tiêu đề tự do — và chỉ khi `llm` khác None.
- Test 52: runner với `llm=None` (không LLM) → mọi test 2A vẫn xanh.

## Registry fallback

`LLMClient.complete` duyệt provider theo đúng thứ tự `LLMConfig.providers`:
- `LLMAuthError`, `LLMQuotaError`, `LLMTransientError` → ghi log, sang kế tiếp
- `LLMBadRequestError` → raise ngay (lỗi của ta, không che)
- Hết provider → `LLMAllProvidersFailed` kèm `attempts: tuple[(name, error_type)]`
- Không provider nào enabled → `NoProviderConfigured`
- Key env không tồn tại → provider bị bỏ qua (không raise)

`LLMResponse.attempts` ghi đúng thứ tự đã thử (test 46).

## `parse_title` JSON fence

LLM có thể trả JSON bọc trong ```json ... ``` (markdown fence). `_strip_json_fence` bỏ fence rồi parse. Test 51 kiểm chứng.

## Không thêm dependency ngoài stdlib (tạm)

Providers dùng `urllib` cho HTTP thật. Test dùng `FakeProvider` inject qua monkeypatch `_build_provider`. Không cần `httpx`/`requests` trong 2B.

## Ghi chú cho Task 3

Cổng trước khi xóa (Task 3) cần bất biến khác `run_checks` (xem phần "Điểm 4" ở trên): chỉ xóa event `[Auto]` trong `Auto-Study`, tổng mới ≥ tổng cũ, trần cứng số lượng, giao với event người dùng = rỗng, đọc lại đối chiếu.

## Kết quả Part 2B

- 15 test pass (test 38–52). Tổng cộng 193/193 (178 + 15).

---

# NOTES — Task 3A (lưới an toàn thật)

## Vấn đề đã xác nhận

`run_checks` trước đây kiểm `no_overlap_with_blocked` bằng cách khẳng định block nằm trong `free_intervals` — chính thứ `cut_blocks` dùng để đặt block. Tautology: hai bên dùng chung nguồn, check luôn đạt. Nếu `compute_day_capacity` tính sai `free_intervals` (quên trừ travel/break/leisure), block rơi vào vùng cấm mà check vẫn báo ĐẠT.

## Sửa: đường tính toán độc lập

Tạo `scheduler_core/verify.py` — **KHÔNG import `capacity.py`** (test 11 kiểm bằng AST parse).

`verify.py` viết theo hướng **cộng dồn** các vùng cấm từ dữ liệu thô, ngược với `capacity.py` (trừ dần từ khung ngày). Hai bên dùng chung `intervals.py`, `models.py`, `config.py` nhưng logic độc lập. Nếu cùng bug do copy-paste thì lưới vẫn vô dụng — nên viết khác là chủ đích.

### `forbidden_intervals(day, events, config, leisure)`
Cộng dồn: event đã lọc (opaque, không all-day) + break + travel padding (pre/post/recovery/chain gap) + leisure. Trả về tuple đã normalize.

### `expected_free_intervals(day, events, config, leisure)`
`day_window` trừ `forbidden_intervals`, rồi bỏ mảnh < `min_fragment_minutes`. Đây là kết quả mà `capacity.compute_day_capacity` PHẢI cho ra.

## `run_checks` đổi

- **Chữ ký thêm** `events_by_day: Mapping[date, Sequence] | None = None` (keyword, mặc định None → test cũ không vỡ).
- **`no_overlap_with_blocked` viết lại**: gọi `forbidden_intervals(...)`, khẳng định block giao rỗng với tập đó. **Không nhắc `free_intervals`** nữa.
- **`capacity_consistent` mới** (severity="error"): với mỗi ngày, so sánh `cap.free_intervals` vs `expected_free_intervals(...)`. Lệch → fail, detail in cả hai tập khoảng để so bằng mắt. Đây là check quan trọng nhất: ép hai đường tính toán độc lập phải khớp.

## 8 test phá hoại

Mỗi test dựng `DayCapacity` thủ công với `free_intervals` SAI, rồi xem `capacity_consistent` có bắt được không:

| Test | Lỗi giả lập | Bắt được? |
|---|---|---|
| 1 | Quên trừ pre 120' trước event offline | FAIL ✓ |
| 2 | Quên trừ break 12:00–14:00 | FAIL ✓ |
| 3 | Quên trừ gap giữa chuỗi offline | FAIL ✓ |
| 4 | Quên trừ leisure cuối tuần | FAIL ✓ |
| 5 | Quên trừ recovery 30' | FAIL ✓ |
| 6 | Free rộng hơn window (06:00) | FAIL ✓ |
| 7 | Còn mảnh 15' (quên drop) | FAIL ✓ |
| 8 | Đúng hoàn toàn (compute_day_capacity thực) | PASS ✓ |

Tất cả 8 fail đúng chỗ. Ca nào không fail được → `verify.py` vẫn phụ thuộc `capacity.py` ở đâu. Không có ca nào.

## Test cũ cập nhật

- `test_validate.py`: thêm `NO_BREAK_CONFIG` (config không break) để `capacity_consistent` pass với cap thủ công. Test 14 dùng config/leisure thật. `run_checks` hiện có 12 check (thêm `leisure_matches`).
- `test_integration.py`: test 1 đếm 12 check.
- `plan.py`: truyền `events_by_day` vào `run_checks`.

## File đã sửa

| File | Lý do |
|---|---|
| `scheduler_core/verify.py` | MỚI — `forbidden_intervals`, `expected_free_intervals` (cộng dồn, không import capacity) |
| `scheduler_core/validate.py` | `run_checks` thêm `events_by_day`, `no_overlap` dùng `forbidden`, thêm `capacity_consistent` |
| `scheduler_core/plan.py` | Truyền `events_by_day` vào `run_checks` |
| `scheduler_core/__init__.py` | Export `forbidden_intervals`, `expected_free_intervals` |
| `tests/test_validate.py` | `NO_BREAK_CONFIG`, config leisure thật, đếm 12 |
| `tests/test_integration.py` | Đếm 12 check |
| `tests/test_verify.py` | MỚI — 12 test (8 phá hoại + 4 verification) |

## Kết quả Part 3A

- 12 test mới. Tổng cộng 205/205 trước Task 3B. 0 test chuyển từ xanh sang đỏ.

---

# NOTES — Sửa sau review 3A (xác minh + vá leisure)

## Xác minh `NO_BREAK_CONFIG` — vô hại

Test_10 mở rộng: chạy `capacity_consistent` trên PRESET_STUDENT_VN thật (có break, có leisure cuối tuần), đủ 7 ngày từ fixture part 1E. **Tất cả 7 ngày khớp.** `NO_BREAK_CONFIG` chỉ dùng cho test_validate.py — cap thủ công không phản ánh break, và bỏ break là cách chỉnh fixture cho nhất quán, không phải né check. Đọc theo cách "vô hại".

## Vá lỗ hổng leisure

**Vấn đề:** `forbidden_intervals` nhận `leisure: Interval | None` từ `DayCapacity.leisure_interval` — do `capacity.py` tính. Nếu `capacity.py` chọn sai vị trí (latest vs earliest), verify dùng lại cùng nguồn.

**Sửa:** `verify.py` tự tính lại leisure qua `compute_leisure` (dùng `find_contiguous_block` + `longest_interval` trên free = window trừ forbidden trước leisure). `forbidden_intervals` chỉ nhận `(day, events, config)` và luôn dùng vị trí leisure tự tính. `run_checks` có thêm `leisure_matches` tường minh, so sánh trực tiếp với `DayCapacity.leisure_interval` và in cả hai khoảng khi lệch. Test_12 kiểm tra đúng check này.

## Ranh giới đã biết của lưới

**`requires_travel` là dữ liệu đã diễn giải, không phải dữ liệu thô.** Cả `capacity.py` và `verify.py` đều nhận `FixedEvent.requires_travel` từ adapter — do adapter đặt từ "Offline"/"Online" trong description. Nếu adapter nhận diện sai (thiếu "Offline" → `requires_travel=False` khi thực ra cần travel), cả hai đường đều dùng cùng giá trị sai → `capacity_consistent` vẫn pass, nhưng block rơi vào vùng cần đệm di chuyển mà không ai bắt.

Đây là **ranh giới đã biết**: `capacity_consistent` phủ mọi lỗi tính toán nội tại (break, travel padding, leisure vị trí, mảnh vụn), nhưng **không phủ** lỗi diễn giải dữ liệu thô ở tầng adapter. Lỗi tầng adapter đã có cảnh báo khi thiếu "Offline"/"Online" trong description, nhưng không có check formal trong `run_checks`.

## File đã sửa (lượt này)

| File | Lý do |
|---|---|
| `scheduler_core/verify.py` | `compute_leisure` tự tính; một `forbidden_intervals` luôn dùng leisure verify, không nhận giá trị capacity |
| `scheduler_core/validate.py` | `no_overlap_with_blocked` dùng forbidden tự tính; thêm `leisure_matches` |
| `tests/test_verify.py` | test_10 mở rộng 7 ngày preset thật; test_12 leisure sai vị trí → FAIL |
| `tests/test_validate.py` | test_14 dùng config có leisure thật + event thật; `FixedEvent` import; `SAT` |

## Kết quả

- 205 test pass (204 + test_12 mới). 0 test chuyển từ xanh sang đỏ.

---

# NOTES — Task 3B (diff reconciliation + safe deletion)

## API và đường gọi xóa

- `horae.runner.run()` đọc AutoBlocks D1/D2, gọi `horae.reconcile.reconcile()` sau `build_plan()`.
- `reconcile()` chỉ gọi `writer.delete_event(event_id, calendar_id)` cho từng event cụ thể; không có và không dùng xóa theo range.
- Adapter thật là `GoogleCalendarWriter.delete_event()`: resolve calendar bằng `calendarList`, kiểm tra đúng ID của calendar tên chính xác `Auto-Study`, rồi gửi `DELETE /calendars/{calendar_id}/events/{event_id}`.
- `GoogleCalendarWriter` không tạo calendar fallback. Không resolve được `Auto-Study` thì gate fail và không có DELETE/POST.

## Diff

Với mỗi `(task_id, date)` trong cửa sổ D1..D2:

- existing = planned: không xóa, không tạo.
- existing < planned: chỉ tạo phần deficit; block đã có một phần được ghi nhận là `recovered`.
- existing > planned: chọn đúng tổng surplus từ các block mới nhất trước, giữ block sớm nhất. `planned == 0` chọn toàn bộ AutoBlocks của task/ngày.
- Task ID không còn trong lô Todoist hiện tại được coi là planned zero và là ứng viên xóa.
- Block trước D1 bị loại khỏi diff, nên không thể bị xóa.
- Nếu surplus không thể biểu diễn bằng tổng của các event nguyên vẹn, `horae/reconcile.py::reconcile` dùng replacement: xóa các block nguyên vẹn mới nhất đủ bao phủ surplus rồi chỉ tạo deficit còn thiếu. Phạm vi vẫn là một `(task_id, date)`, không xóa cả ngày; các interval kế hoạch khớp chính xác được giữ để tránh tạo trùng.

## Safety gate

`ReconciliationReport` tại `horae/reconcile.py` tách hai cổng:

1. `gate_delete`: `PlanResult.ok`, target và mọi điều kiện delete candidate phải đạt; nếu fail thì bỏ qua toàn bộ DELETE.
2. `gate_create`: `PlanResult.ok`, target và điều kiện create phải đạt; nếu fail thì không POST nào được thực hiện.

`PlanResult.ok=False` luôn làm cả hai cổng fail. Delete-gate fail vẫn cho phép các create độc lập nếu `gate_create=True`, kèm warning; replacement create chỉ chạy sau khi mọi DELETE bắt buộc của nó thành công. `dry_run` trả đúng `planned_deletes`/`planned_creates` nhưng không gọi writer. Xóa luôn chạy trước tạo; từng lỗi writer được bắt và ghi vào report, rồi read-back vẫn so tổng phút và ghi `mismatch`.

Replacement delete chỉ được coi là an toàn khi candidate chưa bắt đầu: caller truyền `now` tham chiếu vào `horae/runner.py::run(..., now=...)`, runner chuyển nguyên giá trị đó tới `horae/reconcile.py::reconcile(..., now=...)`, và candidate bị chặn khi `candidate.start <= now` với warning rõ ràng. Không gọi `datetime.now()`; nếu bỏ qua `now` thì giữ behavior test cũ và không có bảo vệ theo thời điểm, nên production caller nên truyền `now`.

Đường dẫn chính xác: `horae/runner.py::run` → `horae/reconcile.py::reconcile` → `CalendarWriter.delete_event(event_id, calendar_id)` / `CalendarWriter.create_block(block, meta)`. Regression tests nằm tại `tests/test_reconcile.py` (`test_17a_non_exact_surplus_replaces_latest_block_with_deficit`, `test_17b_started_replacement_skips_delete_but_runs_unrelated_create`).

Ngưỡng dùng số thực `max(3, total_auto_blocks * 0.5)`, đúng nghĩa giới hạn 50%; ví dụ tổng 10 block thì tối đa 5 delete.

## Sửa sau review 3B

- Khi `existing < planned`, reconcile match block hiện có với block kế hoạch bằng đúng `start/end` trước khi chọn phần thiếu. Nhờ vậy retry sau ghi dở không tạo duplicate cho block đã ghi thành công; chỉ tạo các block chưa có đủ.
- Cổng target không suy đoán calendar ID từ candidate nữa. Writer phải xác nhận tên chính xác `Auto-Study` và ID; nếu có create mà không xác định được ID cũng bị chặn.
- Recovery được ghi tường minh trong `ReconciliationReport.recovered` và warning tiếng Việt `phục hồi ghi dở ...`.
- GCal reader giữ `description` và `calendar_name` khi chuyển event thành `AutoBlock`; nếu làm mất description thì mọi delete thật sẽ bị cổng parseable-task-ID chặn.

## Kết quả Task 3

- 222 test pass (205 trước 3B + 17 reconciliation). Có thêm sửa review nhưng không làm test nào đỏ.

---

# NOTES — Review safety sabotage S1–S6

Agent đã thêm sáu ca sabotage độc lập vào `tests/test_reconcile.py`; tất cả đều chặn đúng:

1. S1: candidate sai `calendar_id` → gate fail, 0 delete.
2. S2: candidate ngoài D1/D2 → gate fail, 0 delete.
3. S3: candidate không có prefix `[Auto]` → gate fail, 0 delete.
4. S4: plan rỗng + 10 AutoBlocks → giới hạn 50% (=5) chặn, 0 delete.
5. S5: duplicate `event_id` → gate fail, không gọi delete hai lần.
6. S6: một delete trả 404 → ghi error, không raise, vẫn thử các candidate sau.

## Kết quả

- 6 test sabotage mới. Tổng cộng 228/228 test pass trước test vòng đời ba lần chạy.

---

# NOTES — Dry-run rollout bước 1

Đã chạy tay mô phỏng 3 ngày liên tiếp với `dry_run=True`, fixture lịch thật đã chuyển thành `RawEvent`, `FakeTaskSource`, `FakeCalendarReader`, `FakeCalendarWriter`:

| run date | ok | gate | deletes | creates | warnings |
|---|---:|---:|---:|---:|---|
| 2026-01-04 | True | True | 0 | 7 | none |
| 2026-01-05 | True | True | 0 | 6 | none |
| 2026-01-06 | True | True | 0 | 5 | `no_day_dominance` |

`writer_deleted=[]` và `writer_created=0` ở cả ba lần, nên simulation không có side effect. Đây **chưa phải** dry-run trên Calendar thật: chưa kiểm tra phân trang, recurring-instance event ID (`<base_id>_<timestamp>`), quyền OAuth hoặc dữ liệu thực tế. Trước khi chạy production cần thực hiện ba ngày dry-run thật theo lộ trình thủ công.

## Recurring events

GCal recurring instance có `event_id` dạng `<base_id>_<timestamp>`. Reconciliation luôn dùng `event_id` cụ thể từ `AutoBlock` và chỉ gọi `delete_event(event_id, calendar_id)`; không suy diễn hay xóa theo base ID. Block `[Auto]` do hệ thống tạo không lặp. Vẫn cần kiểm tra dữ liệu thật ở giai đoạn dry-run.

## Kịch bản ba lần chạy fake trước dry-run thật

Đã thêm `tests/test_runner.py::test_39_three_run_diff_deletion_lifecycle`:

1. Lần 1: task 180' → tạo 2 block 90' ở D1/D2.
2. Lần 2: task giảm còn 90' → xóa đúng block D2, giữ block sớm D1.
3. Lần 3: task biến mất khỏi Todoist → xóa block D1 còn lại.

Writer persistent ghi nhận đúng DELETE cụ thể và không CREATE ngoài kế hoạch. Đây vẫn là fake/in-memory, chưa chạm Calendar thật.

## Dry-run thật lần 1 (2026-08-27)

Đã dùng One CLI với các action GET đã đọc knowledge trước đó để đọc calendar list, Todoist tasks và events thật trong cửa sổ 2026-08-28–2026-09-09. Dữ liệu live được chuyển vào `runner.run(today=2026-08-27, dry_run=True)` qua snapshot reader; writer vẫn là fake để tuyệt đối không POST/DELETE.

- Calendar thật: 9 calendar, trong đó `Auto-Study` tồn tại; `HDT.IE.ADVANCED 32` có trong danh sách nhưng bị bỏ qua.
- 8 calendar không bị loại được đọc, mỗi calendar 1 page trong lần này (chưa phát hiện pagination thực tế).
- Todoist: 4 task đang mở.
- Google Calendar: 38 event trong cửa sổ đọc; 2 AutoBlock hiện có trong Auto-Study.
- Kết quả: `plan_ok=True`, nhưng `gate_passed=False`.
- Gate chặn vì một task hiện có 60' nhưng kế hoạch mới chỉ còn 30'; surplus 30' không khớp tổng của một block nguyên vẹn, nên reconcile không xóa để tránh xóa quá kế hoạch.
- Không có side effect: `writer_deleted=[]`, `writer_created=0`.
- Có warning `small_task_buffer`; không có delete/create được thực thi.

Kết luận lần 1: **an toàn, không ghi/xóa**. Chưa đủ ba ngày; cần lặp dry-run thật vào 2026-08-28 và 2026-08-29 trước khi sang bước 2.

## Recurring event quan sát được trên dữ liệu thật

Google Calendar trả các instance lặp với ID dạng `<base_id>_<timestamp>Z`, ví dụ có hậu tố ngày/giờ UTC. Reconciliation dùng nguyên `event_id` instance cụ thể; không rút gọn về base ID.

## Kết quả cuối hiện tại

## Dry-run thật lần 2 (snapshot 2026-08-27)

Chạy lại với cùng snapshot live sau khi thêm replacement và tách gate:

- `plan_ok=True`, `gate_delete=True`, `gate_create=True`.
- Có 2 delete dự kiến trong Auto-Study: một block 60' được thay bằng block kế hoạch 30' (surplus lệch biên), một block ongoing 60' không còn trong kế hoạch.
- Có 6 create dự kiến.
- `dry_run=True` nên `writer_deleted=[]`, `writer_created=0`; không có side effect.
- Tất cả calendar trả 1 page trong snapshot; recurring instances có ID hậu tố `_YYYYMMDDTHHMMSSZ`.

Đây là cùng ngày/snapshot nên **không tính là ngày thật thứ 2 trong chu kỳ ba ngày**. Cần chạy lại vào ngày kế tiếp với dữ liệu live mới; không được giả lập bằng cách đổi ngày đầu vào.

## Kết quả cuối hiện tại

- 231 test pass (205 3A + 19 reconciliation + 6 sabotage + 1 lifecycle; các suite trước đã nằm trong 205).

## Sửa truncation + cache theo nội dung (trước khi nối 2B)

- max_tokens: 200 → 4000 (200 và cả 1000 đều đã được đo thất bại
  trong thực tế; reasoning budget không tỉ lệ với độ dài prompt)
- Default max_tokens ở protocol và 4 provider: 1000 → 4000, để
  call-site quên truyền tham số cũng không rơi vào ngân sách đã đo thất bại
- Thêm LLMTruncatedError(LLMTransientError), bắt finish_reason="length"
  / stop_reason="max_tokens" ở tầng provider, trước khi content rời khỏi
  hàm complete()
- Bỏ except Exception: pass trong parse JSON, bắt đúng
  (JSONDecodeError, KeyError, ValueError), log lại
- Cache theo sha256(title|description), CHỈ ghi khi parse thành công.
  Fallback KHÔNG BAO GIỜ được cache — nếu không, một lần LLM hỏng sẽ
  pin sai vĩnh viễn (không có cơ chế tự sửa vì hash không đổi khi
  nội dung task không đổi)

## Nguyên tắc kiểm thử rút ra (áp dụng cho mọi module LLM trong Chiron)

Bug loại này (truncation ngẫu nhiên, non-determinism của reasoning
model) KHÔNG lộ ra qua test với fake provider — toàn bộ 15 test cũ
của test_llm.py xanh trước khi phát hiện qua chạy API thật.
Nguyên tắc: mọi module gọi LLM cần một lần "smoke test với provider
thật" như điều kiện bắt buộc trước khi coi milestone đóng — bổ sung
cho test suite, không thay thế.
