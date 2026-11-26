# L0-CTK: critical task period range

Checks [Core#174](https://github.com/owntech-foundation/Core/issues/174). `task.createCritical()` used to accept
periods its interrupt source cannot produce, and the task then ran at another rate without any message.

The CUT is flashed with env `CTK` ([`tests/firmware/critical_task/`](../../firmware/critical_task/main.cpp)): HRTIM at
200 kHz with unit A initialised (no output started). The host asks for `createCritical()` calls over SWD. The
critical task counts its runs and toggles PA6, which the MUX routes to scope CH1. Each source gets one boot, because
only one critical task can run per boot:

| Source | Refused (L0-CTK-02) | Accepted, then measured (L0-CTK-03 to 05) |
|---|---|---|
| HRTIM, 5 µs periods, 8-bit repetition counter | 5000 µs (the issue's case), 1285 µs (257 periods) | 1280 µs (256 periods) |
| TIM6, 10 MHz, 16-bit auto-reload | 10000 µs (the issue's case), 6554 µs | 6553 µs |

The accepted period is measured on-chip (runs against uptime over 4 s) and on PA6 (1 / (2 × frequency)), both
±1 %. When an out-of-range period is accepted, as before the fix, L0-CTK-06 records the rate the task actually ran at.

```sh
tests/run_all.sh --l0 --target     # in a campaign (after the SPOC tests: it reflashes the CUT)
```
