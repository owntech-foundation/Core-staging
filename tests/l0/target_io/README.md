# L0-IOM: the IO module of the API regression suite

IO module of the API regression suite ([Regression suite module: IO](https://github.com/owntech-foundation/Core-staging/issues/40);
tiers, depth and coverage fields decided in [#35](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)).
It checks the 20 calls of `spin.gpio` (6), `spin.led` (3), `spin.dac` (4), `spin.comp` (1), `spin.timer` (2) and
`spin.uart` (4), and folds in **L0-DAC** (`communication.analog`, Core#168) under its IDs. It copies the layout of
[L0-PWM](../target_pwm/README.md):

- **One firmware for the module.** env `IO` ([`tests/firmware/io/`](../../firmware/io/main.cpp)) is a step executor. The
  host writes a step's arguments into `g_io_args`, then `(sequence << 8) | step` into `g_io_cmd`, over SWD. The firmware
  calls the API, puts return values in `g_io_report` and acknowledges. Shield config twist (inherited, for the analog
  bus) and usart1 on PB6 (TX) / PB7 (RX) from its `app.overlay` (the O2 wiring; RX pulled up so it idles high on a bare
  CUT).
- **Many checks per flash.** The host ([`run.py`](run.py)) judges each step from what it reads itself over SWD (GPIO
  A–D, DAC1–3, COMP, TIM3/TIM4, USART1, LPUART1, VREFBUF) and from the scope through the SPOC MUX (CH1). Each profile
  starts from a reset, not a new flash. One step (`S_GPIO_SWEEP`) snapshots the GPIO registers on the chip, so that the
  58 Spin pin numbers cost one step.
- **Expected values from an independent source**: RM0440 (register encodings: COMP INMSEL/INPSEL, TIM encoder mode,
  DAC MODE/WAVE, VREFBUF scale 0 = 2.048 V), the Spin pinout (Spin pin → MCU pin, the table of L0-PAD), the HAL headers
  (`'x'` when no character waits, comparator inputs, LED on led0 = PA5), 9600 baud and 170 MHz from `UartHAL`/`spin.dts`.

**A bare Spin only** (the SPOC CUT): it drives GPIO outputs on most pins, the DACs on PA4/PA6 and USART1.

## Checks

| Profile (fresh boot) | IDs | Calls | Depth, tier |
|---|---|---|---|
| P0 boot state | 02 | VREFBUF 2.048 V (R1), DAC2 at 2048 at boot (R2) | effect, per-PR |
| P1 GPIO | 03, 04, 05 | every Spin pin number through `configurePin/setPin/resetPin` (right MCU pin, no other, non-GPIO numbers do nothing); STM32 names on one pin per port with all six calls; INPUT/INPUT_PULLUP; `readPin` on an output and on a pulled-up unconnected input; levels on the scope | effect + signal, per-PR |
| P2 LED | 06, 07 | `turnOn/turnOff/toggle` (PA5 register and level) | effect + signal, per-PR |
| P3 spin.dac | 08, 09 | `initConstValue/setConstValue` on DAC1/2/3; PA4 voltage at 1024/2048/3072 | effect + signal, per-PR |
| P3b current mode | 10 | `currentModeInit/slopeCompensation` sawtooth registers, DAC1/DAC3 still disabled (MCR.MODE is writable only then) | effect, per-PR |
| P4 analog bus (L0-DAC) | L0-DAC-03..06 | `communication.analog.init/setAnalogCommValue`: DAC2 DOR1 and PA6 voltage at 512…3584, ±(30 mV + 3 %) | effect + signal, per-PR |
| P5 comparator | 11 | `initialize(1)`, `(3)`, `(2)` (CSR) | effect, per-PR |
| P6 encoder timers | 12 | `startLogIncrementalEncoder/getIncrementalEncoderValue` on TIMER4, TIMER3 | effect, per-PR |
| P7 UART | 13, 14, 15 | `usart1Init` registers, `usart1ReadChar` = `'x'`; `usart1WriteChar` frame on PB6 (4800 Hz square of `'U'`); `usart1SwapRxTx` (SWAP bit, TX leaves PB6) | effect + signal, per-PR |
| Loopbacks (wires) | 20–23 | `readPin`, UART TX→RX, encoder count, comparator output | signal, **supervised** → nightly |

L0-DAC-00/01/02 (flash and report of the old env `DAC`) are gone: the module flashes once, so L0-IOM-00/01 cover them.
L0-DAC-03..06 keep their IDs and criteria, so their history continues.

## Scope through the MUX

CH1 only (TP1–TP3 ties CH1 to CH2, the injector loads CH3, [#5](https://github.com/owntech-foundation/Core-staging/issues/5)).
Pins checked on the scope: **PA7, PB2, PC12, PD2** (GPIO, one per port), **PA5** (LED), **PA4** (DAC1), **PA6** (DAC2,
L0-DAC), **PB6** (UART TX, and flat after `usart1SwapRxTx`). Logic levels: high ≥ 2.8 V, low ≤ 0.4 V (VAVG).

**PB7 on this bench**: the MUX route to PB7 (mux2 input 7) shows PB6's UART signal while USART1 RX on PB7 receives
nothing and reads idle (L0-IOM-14.b/.c, 2026-11-28). PB7 is therefore recorded, not judged; a stuck select line on
mux2 (input 7 read as input 6) would explain it and is worth checking on the SPOC.

### Gaps (catalogue rows L0-IOM-30..35, status Blocked)

| Row | What | Why |
|---|---|---|
| 30 | PA13/PA14 (Spin 46/47) as GPIO | SWD: driving them cuts the ST-Link the test reads through |
| 31 | PA11, PA12, PC14, PC15, PD0, PD1, PD3 | USB lines / not on the Spin header / not on the STM32G474RE, and no MUX input |
| 32 | DAC3 on a pin | DAC3 has no pin: registers only, its level through COMP1 in the loopback |
| 33 | Comparator output on a pin | the driver sets no COMPx_OUT alternate function: CSR VALUE only |
| 34 | Slope-compensation ramp on PA4 | moves only on HRTIM triggers (a PWM unit in current mode): belongs with the PWM/Twist current-mode checks, nightly |
| 35 | Inputs (readPin, UART RX, encoder, comparator inputs) | show nothing by themselves: the loopbacks below |

## Loopbacks (supervised until #16)

L0-IOM-20..23 need wires between CUT pins. Unattended they **BLOCK** with "needs the switchable wires of #16". With
`--supervised` the test prompts once for all six wires (Spin header pin numbers in brackets), then once to take them out:

| Wire | Pins | For |
|---|---|---|
| W1 | PC12 (19) – PC13 (22) | L0-IOM-20 `readPin(PC13)` while PC12 is driven |
| W2 | PB6 (55) – PB7 (56) | L0-IOM-21 UART TX → RX, `usart1ReadChar` |
| W3 | PB4 (20) – PC6 (7) | L0-IOM-22 encoder A (TIM3 CH1), driven by GPIO |
| W4 | PA15 (48) – PC7 (9) | L0-IOM-22 encoder B (TIM3 CH2), driven by GPIO |
| W5 | PA4 (45) – PA1 (30) | L0-IOM-23.a DAC1 → COMP1 plus input (minus: DAC3 at 1.024 V) |
| W6 | PA6 (34) – PC1 (25) | L0-IOM-23.b DAC2 → COMP3 plus input (minus: DAC1 at 1.024 V) |

The MUX is disabled during the loopbacks. The encoder index input (PD2, TIM3 ETR) stays unconnected, as on an encoder
without index. **Take the wires out before any other firmware runs**: Core-CUT and the PWM firmware drive PC6/PC7 and
the DAC pins as outputs.

```sh
tests/run_all.sh --l0 --target                 # in a campaign (after the SPOC tests: it reflashes the CUT)
tests/run_all.sh --l0 --target --supervised    # also L0-IOM-20..23: prompts for the wires on the terminal
```

`known_failure` in [`catalogue.json`](../../catalogue.json) marks the checks that fail on `main` today, with the issue
that tracks them. Afterwards the CUT runs the IO firmware: `SPOC_CUT_DIR=<Core-CUT checkout> bash tests/common/bench_restore.sh`.

**Duration**: about 3.5 min per campaign (213 s on 2026-11-28: build and flash ~75 s, nine profiles, ~120 steps); the
supervised loopbacks add about 1 min plus wiring.
