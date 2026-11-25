# L0-ADD: ADC data dispatch under a critical task

Checks the four `data_dispatch` changes of [Bench tests and merge: ADC dispatch fixes](https://github.com/owntech-foundation/Core-staging/issues/9),
on the chip over SWD (the bench research's advice). The scope plays no part.

The CUT is flashed with env `ADD` ([`tests/firmware/data_dispatch/`](../../firmware/data_dispatch/main.cpp)). HRTIM
runs at 200 kHz and unit A is started, so PA8/PA9 toggle: **a bare Spin only**. There are two boots, because only one
critical task can run per boot.

**Boot 1, dispatch.** ADC2 converts pins 34 (PA6) and 35 (PC4) every 5 µs PWM period, one channel per trigger. The DAC
writes a sawtooth on PA6, so pin 34 sees a changing signal. A 100 µs HRTIM task lets the scheduler dispatch the data.
It fetches pin 35 every run and never fetches pin 34.

| ID | Checks | Core |
|---|---|---|
| L0-ADD-02.a | No pin 35 sample lost: fetched = elapsed periods / 2, ±1 %, over 4 s | #161, #162 |
| L0-ADD-02.b | The same, with a 200 µs busy-wait every 50 runs (the next dispatch comes late) | #162 |
| L0-ADD-03 | `peek(34)` follows the sawtooth while its buffer is full and never fetched | #173 |
| L0-ADD-04.a/.b | The halfword just past each of pin 34's buffers does not change (read through `adc_channel_buffers`) | #161, #173 |
| L0-ADD-07 | INFO: background spins per second, the CPU the dispatch leaves | #161 |

**Boot 2, heap.** Pins 24 (ADC1) and 34 (ADC2) with a 5 ms TIM6 task. Each DMA buffer holds 1000+ samples, more than
the 4 KB heap can hold for both.

| ID | Checks | Core |
|---|---|---|
| L0-ADD-05 | The task runs at 5 ms ±5 % and the background keeps running (no fault) | #175 |
| L0-ADD-06 | INFO: which ADCs give values (`NO_VALUE`: left out for lack of memory) | #175 |

```sh
tests/run_all.sh --l0 --target     # in a campaign (after the SPOC tests: it reflashes the CUT)
```
