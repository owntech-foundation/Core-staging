# L0-TIM: Core API timing on the chip

Times the Core API on the Spin with the Cortex-M4 DWT cycle counter. Results go to a RAM report that is read over
SWD while the core runs ([Timing harness and reference timing run (main vs pre-zephyr-4.4)](https://github.com/owntech-foundation/Core-staging/issues/37),
rule from the [API regression suite decision](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)).
The method comes from Core2 `apf_quadrature` (ADR 0005, `tests/l0/timing/`). There it times the micro-inverter's
`controlStep`; here it times **Core API calls** and the **critical task**.

The CUT is flashed with env `TIM` ([`tests/firmware/timing/`](../../firmware/timing/main.cpp)). HRTIM runs at
200 kHz and PWMA is started, so PA8/PA9 toggle: **a bare Spin only**. PWMC is initialised but has no output; it is
the target of the phase-shift call. The critical task runs every 100 µs on the HRTIM master period.

Nothing in Core is instrumented. The firmware times each call from outside, so the same test firmware builds on any
ref: `main`, and the tag `pre-zephyr-4.4`, which predates this test.

## Phases (one boot)

1. **(b0) Bare task.** `startCritical(false)` with no acquisition: the interrupt and the task proxy alone. These rows are named `task0.*`.
2. **(a) Cost per call.** The task is stopped, then acquisition is configured: ADC2 converts pins 34 and 35 every PWM period, one channel per trigger, and ADC1 converts pin 24 on software trigger. The task is restarted with `startCritical(true)`, so the scheduler dispatches the data before each run, as in a user's firmware. Each run times **one** call, taking the calls in turn, with interrupts masked (PRIMASK) around it. The arguments alternate, so no call takes a "nothing changed" shortcut. After 5 warm-up rounds come N rounds (default 100), which gives N samples per call.
3. **(b) Task with dispatch.** The same as (b0), with the scheduler's dispatch. These rows are named `task.*`.

During (b0) and (b), the background task spins on the cycle counter, so any gap in its loop is an interruption.
When a gap holds a critical run:
- **entry latency** is the time from the start of the gap to the user function's entry: the ISR wrapper, the task proxy and the dispatch;
- **exit latency** is the time from the function's exit to the end of the gap;
- **isrOverhead** is the sum of the two.

The HRTIM master counter read at entry gives the time since the period event, modulo the 5 µs PWM period. Subtract
the entry latency and you get **irqHeldOff**: how long the interrupt waited before it was taken. That is the hardware
latency plus anything that masked the interrupt or ran above it.

## Criteria

| ID | Row(s) | What |
|---|---|---|
| L0-TIM-01 | – | env TIM builds, flashes, `createCritical` returns 0, report reaches DONE (PASS/FAIL) |
| L0-TIM-02.`<call>` | `pwm.*`, `data.*`, `probe.empty` | cost per call, mean / σ / worst cycles (probe pair included; `probe.empty` gives it) |
| L0-TIM-03.`task0_period`, `task_period` | period | entry to entry: mean error in ppm, σ = jitter, worst (INFO) |
| L0-TIM-04.`<row>` | `task0.*`, `task.*` entry/exit/isrOverhead/body, `data.dispatch` | interrupt overhead; `data.dispatch` = `task.entryLatency` − `task0.entryLatency` |
| L0-TIM-05.`<row>` | `irqHeldOff`, `otherIrq` | interrupts held off before the critical task; longest interruption by anything else (INFO) |
| L0-TIM-06 | – | PA6 marker on the scope: **BLOCKED, needs scope** (PA6 is driven, the SPOC read is not written) |

Sub-IDs use `_` for `.` (`L0-TIM-02.pwm_setDutyCycle`), because the record splits criterion IDs on the first dot.

## Hot-path calls (the list in (a))

The hot-path calls are the calls a user makes **inside the critical task**: they run every period and add to the
task's budget. They were picked from `PwmHAL.h` and the public part of `DataAPI.h`.

| Call | Why it is on the hot path |
|---|---|
| `pwm.setDutyCycle` | the control loop's output, written every period |
| `pwm.setDutyCycleRaw` | the same, in raw ticks |
| `pwm.setPhaseShift` | interleaving and DAB-style control, updated every period |
| `pwm.setAdcTriggerInstant` | follows the duty cycle to sample mid-pulse |
| `pwm.stopDualOutput` / `startDualOutput` | protection and enable from the task (the micro-inverter's tick does it) |
| `pwm.stopSingleOutput` / `startSingleOutput` | the same, for one switch |
| `pwm.getPeriod` | used to scale duty cycles computed in ticks |
| `data.getLatestValue` (with and without `dataValid`) | the usual measurement read, once per sensor per period |
| `data.peekLatestValue` | a read that does not consume the value |
| `data.getRawValues`, `data.getValues` | every sample since the last run (filters, oversampling) |
| `data.convertValue` | raw to physical, after `getRawValues` |
| `data.triggerAcquisition` | a software-triggered ADC started from the task |
| scheduler dispatch (`doFullDispatch`) | runs before the task when `startCritical(true)`. It is private, so it is timed as `data.dispatch` (derived, L0-TIM-04) |

**Left out** (setup calls, or calls that need a firmware of their own; nightly candidates):
- `setFrequency`, `initVariableFrequency` and dead time: setup, they re-initialise the timer;
- burst mode (`setBurstMode`, `start/stopBurstMode`): it gates every output and the ADC triggers;
- ADC configuration (`enableAcquisition`, `configure*`) and conversion parameters (NVS): setup, not per period;
- `spin.gpio` and `spin.dac`: outside the PWM/DataAPI scope of #37. The Data and IO module tickets will add them.

## Rule and reference

[`evaluate.py`](evaluate.py) applies the rule to each row's **mean**. With
`margin = max(10 % of the reference mean, 3 σ of the reference) + 50 cycles`:
- the row is a **regression** (FAIL) if its mean is above the reference by more than the margin;
- it is **improved: check it** (INFO, suspicious until explained) if its mean is below the reference by more than the margin;
- otherwise it is PASS.

While [`reference.json`](reference.json) does not exist, every verdict is **INFO**. A row the reference lacks is
INFO too, and so are the period, `probe.empty` and `otherIrq`. Verdicts are judged against the entry named in
`against` (default `main`).

The reference never moves by itself:

```sh
python3 tests/l0/target_timing/evaluate.py tests/out/<campaign>/timing.json --update-reference --name main --reason "why"
git add tests/l0/target_timing/reference.json && git commit    # the reason goes into the commit too
```

The reference keeps mean / σ / min / worst / n per row for each named run (`main`, `pre-zephyr-4.4`), plus the
firmware, the SHAs, the date and the reason.

## Run

```sh
tests/run_all.sh --l0 --target                         # in a campaign (after the SPOC tests: it reflashes the CUT)
bash tests/l0/target_timing/run.sh --run [--repeats N] # alone, under the bench lock; results in a temp folder
python3 tests/l0/target_timing/evaluate.py --compare A/timing.json B/timing.json   # B against A, INFO
```

**Reference run, `main` against `pre-zephyr-4.4`** (unattended, about 10 min; reflashes the CUT and puts Core-CUT
back at the end):

```sh
SPOC_CUT_DIR=~/code/Core-CUT bash tests/l0/target_timing/reference_run.sh
```

The script takes the bench lock and runs the preflight (`target_preflight.sh --env=TIM`). Then, for each ref:
1. it checks the ref out under `~/code/Core-timing-ref/<name>`;
2. it overlays this commit's `tests/` on it (the tag has no L0-TIM; Core stays the ref's);
3. it runs L0-TIM as a campaign under `tests/out/`; the `pre-zephyr-4.4` campaign is marked as a reference run, so it never counts as a current result;
4. it writes the ref's entry into `reference.json`.

At the end it writes `tests/out/timing-reference_<date>/main-vs-pre-zephyr-4.4.md` (INFO) and restores the bench.
Nothing is committed: review `reference.json` and commit it with its reason.

## Limits

- The entry latency counts anything else in the same background gap, such as a kernel tick taken just before the critical interrupt. The worst case shows it; the mean barely moves.
- `irqHeldOff` is measured modulo the 5 µs PWM period, so a hold-off longer than 850 cycles wraps around. The PA6 marker on the scope (L0-TIM-06) will check it.
- Rows in (a) are measured with interrupts masked. In a real task, other interrupts and colder caches add to them, and (b) measures the task as a whole.
- (b0) and (a)/(b) run in one boot, with the task stopped and restarted (`stopCritical` / `startCritical(true)`). L0-ADD uses one critical task per boot. If the restart misbehaves on target, split this test into two boots.
- (c) latency to effect (call → pin, scope) waits for the PWM module (#35).

## Ported from Core2 `apf_quadrature`, and what was left behind

| apf_quadrature | Here |
|---|---|
| `src/timing/timing_report.h`, `timing_probe.h` | [`tests/firmware/timing/timing_probe.h`](../../firmware/timing/timing_probe.h): rows with sums of squares (σ), in the test firmware |
| `owntech_timing_probe.h` + `OT_TIMING_*` in Core (dispatch, sensor reads) | not ported: Core is timed at its API boundary, and the dispatch is derived (`data.dispatch`) |
| `src/timing/timing_harness.cpp` (controlStep scenario) | [`tests/firmware/timing/main.cpp`](../../firmware/timing/main.cpp) (API calls and task timing) |
| `run.sh`, `read_report.py`, `preflight.sh` | [`run.py`](run.py) on `tests/common/target_harness.py`; `tests/common/target_preflight.sh --env=TIM` |
| `baseline.json` (worst case, +10 % + 50) | [`reference.json`](reference.json) (mean/σ/worst; the #35 rule), written only by `evaluate.py --update-reference` |
| `evaluate.py`, `compare.py` | [`evaluate.py`](evaluate.py) (verdicts, `--update-reference`, `--compare`) |
| `console_reader.py`, `recap.py` | not needed (no console, no recap) |
