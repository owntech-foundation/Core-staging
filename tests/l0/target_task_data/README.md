# L0-TKD: the Task and Data module of the API regression suite

Second module of the API regression suite ([Regression suite module: Task and Data](https://github.com/owntech-foundation/Core-staging/issues/39);
tiers, depth and coverage fields decided in [#35](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)).
It checks every `task` (TaskAPI, 8 calls) and `spin.data` (DataAPI, 18 calls) call at *effect* depth on the chip, on
the pattern of [the PWM module](../target_pwm/README.md):

- **One firmware for the module.** env `TKD` ([`tests/firmware/task_data/`](../../firmware/task_data/main.cpp)) holds no
  test logic: it is a step executor. The host writes a step's arguments into `g_tkd_args`, then `(sequence << 8) | step`
  into `g_tkd_cmd`, over SWD; the firmware calls the API, puts the return values in `g_tkd_report` and acknowledges.
- **The host judges** from the returns, the critical-task run counter (stamped against the DWT cycle counter), the
  background tasks' loop statistics, and the ADC1/ADC2 and NVIC registers it reads itself in the same OpenOCD session.
- **Expected values from an independent source**: the TaskAPI.h and DataAPI.h documentation, RM0440 (ADC `CFGR`
  fields; `EXTSEL` codes as ST's LL header encodes them), Zephyr (zero-latency interrupts sit above `irq_lock()`'s
  `BASEPRI`), the Kconfig defaults (3 background tasks, 10 kHz kernel tick), the NTC Beta model; never the driver's
  own arithmetic. The stimulus is DAC2 on PA6 (pin 34), as in L0-ADD.

**A bare Spin only** (the SPOC CUT): PD2 starts PWMA. No scope, no MUX.

## Folded in, with their IDs

The module is this test plus four that already existed; each keeps its ID and history, and this test adds only what
they do not check. They all carry `covers` in [`catalogue.json`](../../catalogue.json) and sit in the *Task and Data* tab
(L0-TIM stays in *Timing*).

| Test | What it covers for the module |
|---|---|
| [L0-CTK](../target_critical_task/README.md) | `createCritical` range per source (Core#174), task period on-chip and on PA6 (scope) |
| [L0-ADD](../target_data_dispatch/README.md) | `startCritical(true)` dispatch: no sample lost, `peekLatestValue` on a full buffer, heap-short boot |
| [L0-PAD](../target_data_pins/README.md) | `enableAcquisition` pin-to-ADC map on every pin (Core-staging#50) |
| [L0-TIM](../target_timing/README.md) | task timing (period, jitter, ISR overhead, interrupts held off) and cost per call of the hot-path DataAPI calls |

## Profiles and checks

| Profile (fresh boot) | IDs | Calls |
|---|---|---|
| PT task | 02–09 | `createCritical` refusals and redefinition, `startCritical`, `stopCritical`, the zero-latency check, `createBackground`, `startBackground`, `stopBackground`, `suspendBackgroundMs/Us` |
| PD1 data, software trigger, no task | 10–23 | every DataAPI call; two conversion entries stored in NVS |
| PD2 data after a reset | 21, 12, 13, 24 | NVS entries retrieved then deleted; `TRIG_PWM` and discontinuous mode registers; `startCritical(true)` on a TIM6 task making the hot-path calls |

- **L0-TKD-05, the critical task is a zero-latency interrupt.** With `source_tim6` the task keeps running while a
  thread holds `irq_lock()`, and the TIM6 interrupt priority is above `irq_lock()`'s `BASEPRI`. The kernel cannot
  protect its own data from code running there, so **a critical task must make no kernel call** (`k_uptime_get_32()`
  in one faulted the board, L0-ADD). The test firmware's tasks follow the rule; it is written in
  [`docs/task_introduction.md`](../../../docs/task_introduction.md) and `TaskAPI.h`. `source_hrtim` is recorded (INFO).
- **L0-TKD-21, NVS.** Pins 51 and 52 (ADC1 IN3/IN4) are used because no shield's sensors use them; the entries are
  deleted at the end of PD2, so the CUT's NVS is left as it was (apart from the NVS version entry the first store
  writes). Survival across a firmware update (R12) is for the nightly NVS module.
- **L0-TKD-23, Core-staging#56**: the system heap's allocated bytes (`CONFIG_SYS_HEAP_RUNTIME_STATS`) do not move over
  100 rounds of the hot-path calls. **L0-TKD-24** makes the same calls from a 100 µs TIM6 (zero-latency) task.

`known_failure` in the catalogue marks the checks that fail on `main` today, with the issue that tracks them: they are
reported as known and do not fail the campaign. Remove the marking with the fix.

```sh
tests/run_all.sh --l0 --target            # in a campaign (after the SPOC tests: it reflashes the CUT)
bash tests/l0/target_task_data/run.sh --run   # alone, under the bench lock; then tests/common/bench_restore.sh
```

## Duration

Measured on the SPOC CUT (2026-11-28, campaign `2026-11-28_00e37b3_112027`): **L0-TKD 129 s** (flash with the build
cached, three profiles, 114 steps; 131–134 s in the three runs before; L0-TKD-25 records it in each campaign). A cold
build adds about 55 s. The whole module, with its folded-in tests, from the campaign's log times:

| Test | Seconds |
|---|---|
| L0-TKD | 129 |
| L0-CTK | 93 |
| L0-ADD | 96 |
| L0-PAD | 78 |
| L0-TIM | 76 |
| **Task and Data module** | **472 (about 8 min)** |

## Known failures on `main` (catalogue `known_failure`, issue #39)

| Check | What is wrong |
|---|---|
| L0-TKD-12.b | ADC_1/ADC_2 default to software trigger; DataAPI.h says TRIG_PWM |
| L0-TKD-18.c/.d, 19.e, 21.c | error returns are -2; DataAPI.h says -5000 (which an `int8_t` cannot even hold) |
| L0-TKD-18.e | `convertValue` before `start()` with no parameters set reads through a null pointer (open question) |
| L0-TKD-20.b | `getConversionParameterValue(r0/b/rdiv/t0)` returns the wrong NTC parameter (index off by two) |
| L0-TKD-21.e | `retrieveConversionParametersFromMemory` on an NTC entry returns -4 after a reset: retrieve sizes its buffer for 2 parameters, a thermistor entry has 4 (21.a/.b/.d failed earlier with `-EIO` from the CUT's NVS flash state, not Core; they pass since #42's repair) |
| L0-TKD-22.e/.f | `start()` after `stop()` never returns |
