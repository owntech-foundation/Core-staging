# L0-PWM: the PWM module of the API regression suite

First module of the API regression suite ([Regression suite module: PWM](https://github.com/owntech-foundation/Core-staging/issues/38);
tiers, depth and coverage fields decided in [#35](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)).
It checks every `spin.pwm` (PwmHAL) call, 46 of them, and is **the pattern later modules copy**:

- **One firmware per module.** env `PWM` ([`tests/firmware/pwm/`](../../firmware/pwm/main.cpp)) holds no test logic: it is a
  step executor over the API. The host writes a step's arguments into `g_pwm_args`, then `(sequence << 8) | step` into
  `g_pwm_cmd`, over SWD. The firmware calls the API, samples the HRTIM output pins (GPIO IDR, 4096 samples with a random
  gap: the fraction of time each pin is high), puts return values in `g_pwm_report` and acknowledges.
- **Many checks per flash.** The host ([`run.py`](run.py)) judges each step from what it reads itself: the HRTIM register
  block over SWD (one OpenOCD session per step reads the report and the registers), the pins sampled on the chip, and the
  scope through the SPOC MUX. Profiles that need calls *before* `initUnit` start from a reset, not a new flash.
- **Expected values from an independent source**: RM0440 (period = 32 × 170 MHz / f at CKPSC 0, dead-time step 735 ps,
  compare ≥ 0x60), the PwmHAL.h documentation (184/368 ps, the default ADC trigger routing, PWMx2 = duty on the low side), the docs' 27200
  ticks at 200 kHz; never the driver's own arithmetic.
- **Depth** ([#35](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)): *effect* (registers, returns, pins
  on the chip) for every call; *signal* on the scope for outputs, frequency, phase and convention; *accuracy* only where a
  figure is stated (dead time, supervised).

**A bare Spin only** (the SPOC CUT): it starts outputs of PWMA–PWMD and drives PA6.

## Scope through the MUX

Only scope **CH1 and CH4** are used: TP1–TP3 joins CH1 and CH2, and the injector loads CH3 (TP5), so CH1 and CH4 are the
two independent nodes ([#5](https://github.com/owntech-foundation/Core-staging/issues/5)). Frequency, duty, phase (from
waveforms, ±2° = 28 ns at 200 kHz) and DC levels are within the MUX path's reach (edges ~20–35 ns); dead time and
ns-level pulses are not, and run supervised on hand-held probes until [#16](https://github.com/owntech-foundation/Core-staging/issues/16).

## Profiles and checks

| Profile (fresh boot) | IDs | Calls |
|---|---|---|
| P1 left-aligned, 200 kHz, PWMA (+ PWMB, PWMC for phase) | 02–13, 10 (INFO), 12.e | `setFrequency`, `initUnit`, every getter, duty, outputs, latency, `setPhaseShift`, ADC trigger, burst, period event |
| P2 up-down PWMA (PWMx2, dead time 200/50, ADC edge down), up-down PWMC (PWMx1), current-mode PWMD | 14–17 | pre-init calls, `setMode`/`setEev`, the low-duty sweep (#8) |
| P3 `initFixedFrequency(100 kHz)` | 18 | |
| P4 `initVariableFrequency(200 kHz, 50 kHz)` | 19 | `setFrequency` within and below the range |
| Supervised (hand-held probes) | 20, 21 | dead time ±3 ns; ns low-duty sweep |

- **L0-PWM-06, latency to effect**: PA6 (marker, CH1) rises with interrupts masked just before the call; PA8 on CH4.
  `setDutyCycle` must leave pulses already started unchanged and give the new width to the first period that starts
  after the call (preload on the period); `stopDualOutput` must take PA8 low within 1 µs; `startDualOutput` must switch
  within a period. Latencies and the call's DWT cycles are recorded (timing is INFO until a reference run, #35).
- **L0-PWM-10** answers the question in [#38's comment](https://github.com/owntech-foundation/Core-staging/issues/38):
  what `setPhaseShift(PWMA, …)` does (registers and pins). INFO.
- **L0-PWM-15/16**: Core#166/#167 low-duty reversal ([#8](https://github.com/owntech-foundation/Core-staging/issues/8)):
  duty in (0, 0.0025) and near 1 on an up-down PWMx2 unit (the Core#166 boost case) and an up-down PWMx1 unit.
- **Core#172** (an up-down unit doubles its frequency at each re-init) is [L0-HRI](../target_hrtim_reinit/README.md),
  which belongs to this module's campaign.

`known_failure` in [`catalogue.json`](../../catalogue.json) marks the checks that fail on `main` today (with the issue that
tracks them): they are reported as known and do not fail the campaign. Remove the marking with the fix.

```sh
tests/run_all.sh --l0 --target                 # in a campaign (after the SPOC tests: it reflashes the CUT)
tests/run_all.sh --l0 --target --supervised    # also L0-PWM-20/21: prompts on the terminal for the probes
```

## Adding a module

Copy this layout: `tests/firmware/<module>/main.cpp` (a step executor: report struct with magic, layout, boot_count,
ack, busy, step, err; `g_<m>_cmd`, `g_<m>_args`), an env in `pio_tests.ini`, `tests/l0/target_<module>/run.py` (a `Bench`
with `step()`, one function per profile, `known_failure` in the catalogue rather than in the code), and catalogue rows
with `covers`, `depth`, `tier`, `target_tier`.
