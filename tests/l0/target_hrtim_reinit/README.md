# L0-HRI: up-down timing unit initialised again

Checks [Core#172](https://github.com/owntech-foundation/Core/pull/172). In up-down (center-aligned) mode,
`hrtim_tu_init()` stored the counter frequency (twice the switching frequency) as the unit's switching frequency, so
each further `initUnit()` on the same unit doubled its PWM frequency. After a few re-inits the period fell below the
minimum and the `assert` in `_period_ckpsc()` aborted (kernel panic).

The CUT is flashed with env `HRI` ([`tests/firmware/hrtim_reinit/`](../../firmware/hrtim_reinit/main.cpp)): PWMA
up-down at 200 kHz, duty 0.5, started on PA8/PA9, which the MUX routes (PA8) to scope CH1. The host asks over SWD for
10 further `initUnit(PWMA)` calls, one at a time. After each, the firmware records PERxR of timer A, the master period
MPER and the driver's period, and the host measures PA8's frequency on the scope.

| ID | Criterion |
|---|---|
| L0-HRI-01 | `pio run -e HRI -t upload` exits 0 |
| L0-HRI-02 | Boot init: PERxR(A) = MPER / 2 ±1 (the up-down counter runs at twice the switching frequency) |
| L0-HRI-03 | Boot init: PA8 at 200 kHz ±1 % (median of 5 FREQuency readings) |
| L0-HRI-04.01–.10 | Re-init k: `initUnit(PWMA)` returns within 2 s and PERxR(A) equals its boot value |
| L0-HRI-05.01–.10 | Re-init k: PA8 still at 200 kHz ±1 % |
| L0-HRI-06 | INFO, only when a re-init does not return: where the firmware stopped (halted in the assert, or rebooted) |

Before the fix, re-init k runs at 200 kHz × 2^k and the assert aborts at about the 8th: L0-HRI-06 records it and the
criteria not reached fail, so the test never hangs.

Drives PWMA: only on a bare Spin such as the SPOC CUT, never on a power stage.

```sh
tests/run_all.sh --l0 --target     # in a campaign (after the SPOC tests: it reflashes the CUT)
```
