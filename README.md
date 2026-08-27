# Horae — Scheduler Core

Lõi xếp lịch thuần hàm (Python 3.11+). Không I/O, không mạng, không đồng hồ hệ thống. Mọi giá trị điều chỉnh nằm trong `config.py`.

## Cách chạy test

```bash
# Tạo venv (lần đầu)
uv venv && uv pip install pytest

# Chạy toàn bộ test
.venv/bin/python -m pytest -v

# Chạy riêng một part
.venv/bin/python -m pytest tests/test_config.py -v
.venv/bin/python -m pytest tests/test_integration.py -v
```

## Cấu trúc

```
scheduler_core/
  __init__.py     — re-export toàn bộ API
  config.py       — SchedulerConfig, 2 preset (DEFAULT_CONFIG, PRESET_STUDENT_VN)
  models.py       — Interval, FixedEvent, Assignment, OngoingTask, Block, DayCapacity, CheckResult
  intervals.py    — normalize, subtract, clip, total_minutes, drop_shorter_than, find_contiguous_block, longest_interval
  capacity.py     — compute_day_capacity
  allocate.py     — allocate_assignments (→ AllocationResult), allocate_ongoing
  blocks.py       — cut_blocks
  validate.py     — run_checks (12 CheckResult, severity error/warning)
  plan.py         — build_plan (tích hợp) → PlanResult

horae/
  settings.py         — hằng số adapter (DEFAULT_ONGOING_MINUTES, IGNORED_CALENDARS, ...)
  adapters/
    protocols.py      — TaskSource, CalendarReader, CalendarWriter, RawTask, RawEvent, AutoBlock, BlockMeta
    parsing.py        — parse_tasks (phân loại RawTask → Assignment/OngoingTask/skipped)
    todoist.py        — TodoistSource
    gcal.py           — GoogleCalendarReader, GoogleCalendarWriter
  state/
    ledger.py         — build_ledger (sổ tiến độ từ block [Auto] quá khứ)
    store.py          — LocalStore (config.json + runs/{date}.json)
  reconcile.py        — diff reconciliation + safety gate + read-back report
  runner.py           — run() → RunReport (đọc → build_plan → diff → gate → ghi → báo cáo)

horae/llm/                       — LLM provider (tùy chọn, không phải phụ thuộc)
  protocol.py                    — LLMProvider, LLMError + 4 subclass (Auth/Quota/Transient/BadRequest)
  registry.py                    — LLMConfig, LLMClient (fallback theo thứ tự config)
  providers/anthropic.py         — Anthropic Claude
  providers/openai.py            — OpenAI
  providers/deepseek.py          — DeepSeek
  providers/opencode_zen.py      — OpenCode Zen
  tasks/parse_title.py           — regex trước, LLM sau, None→default

tests/
  fixtures.py          — lịch thật của người dùng (tz Asia/Ho_Chi_Minh)
  fakes.py             — FakeHttp, FakeTaskSource, FakeCalendarReader/Writer, FakeProvider
  fixtures/*.json      — response API thật đã bỏ thông tin nhạy cảm
  test_config.py       — 22 test
  test_intervals.py    — 16 test
  test_capacity.py     — 23 test
  test_allocate.py     — 27 test
  test_blocks.py       — 17 test
  test_validate.py     — 13 test (run_checks: 12 mục)
  test_integration.py — 19 test
  test_parsing.py      — 16 test
  test_todoist.py      — 4 test
  test_gcal.py         — 7 test
  test_ledger.py       — 6 test
  test_runner.py       — 9 test
  test_llm.py          — 15 test
  test_verify.py       — 12 test (sabotage + verification)
  test_reconcile.py    — 25 test (Task 3B + S1–S6 sabotage)
```

Tổng: 231 test.

## Luồng `build_plan`

```
today ──► d1 = today + 1
           write_window = [d1, d1 + write_horizon_days - 1]
           dend = min(max deadline, d1 + planning_horizon_days - 1)
           (không assignment → dend = cuối write_window)

  ┌─────────────────────────────────────────────────────────────┐
  │  Lọc assignment: remaining <= 0 → completed_task_ids         │
  │  Lọc assignment: remaining > 0  → active                     │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  compute_day_capacity (mỗi ngày d1..dend)                    │
  │  trừ event → break → travel padding → leisure → mảnh vụn     │
  │  → busy_minutes, ceiling_minutes, capacity_minutes           │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  allocate_assignments (active, capacities, config)           │
  │  sort theo deadline → nhánh A (largest remainder)            │
  │                       hoặc nhánh B (ngày sớm nhất)           │
  │  → assignment_alloc: {date: {task_id: phút}}                  │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  allocate_ongoing (ongoing, capacities, assignment_alloc)    │
  │  leftover = capacity − assignment − existing                 │
  │  sort ongoing theo title → mỗi task min(target, leftover)    │
  │  → ongoing_alloc: {date: {task_id: phút}}                     │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  cut_blocks (mỗi ngày trong write_window)                     │
  │  chia phần → block [min, max] → earliest-fit + gap + round   │
  │  → blocks: list[Block]  (chỉ write window)                    │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  run_checks (blocks, capacities, active, ongoing, alloc)     │
  │  12 CheckResult (severity error/warning):                    │
  │    error: window, overlap, ceiling, deadline, size, fragments │
  │    warning: no_day_dominance, small_task_buffer              │
  │  ok = không có error nào fail                                 │
  └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
                     PlanResult { blocks, projected,
                       capacities, checks, ok, warnings,
                       completed_task_ids, infeasible_task_ids,
                        shortfall }
                                 │
                                 ▼
                 horae.reconcile: diff → safety gate →
                 delete exact surplus → create deficit → read-back
```

- **`blocks`**: chỉ trong write window (gần nhất) — sẵn sàng ghi vào lịch.
- **`projected`**: phân bổ dự kiến ngoài write window (xa hơn) — chưa cắt block.
- **`ok`**: `True` khi không có check `severity="error"` nào fail. Warning fail không làm `ok=False`.
- **`warnings`**: tên các check `severity="warning"` fail (vd `no_day_dominance`).
- **`shortfall`**: `{task_id: phút}` — số phút không xếp được do thiếu capacity.
- `build_plan` không raise vì lịch xấu.
