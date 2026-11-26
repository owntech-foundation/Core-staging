# L0-SAF: Safety error thread report

Checks [Core-staging#47](https://github.com/owntech-foundation/Core-staging/issues/47): when a channel trips, the
Safety API's error thread (`thread_error` in
[`uninterruptible_synchronous_task.cpp`](../../../zephyr/modules/owntech_task_api/zephyr/src/uninterruptible_synchronous_task.cpp))
prints "true" for that channel and "false" for the others. Before the fix it printed I2's flag on the V2 line, so a
V2 trip was never shown and an I2 trip showed on both lines.

The CUT is flashed with env `SAF` ([`tests/firmware/safety_report/`](../../firmware/safety_report/main.cpp)), with
the Twist shield configuration. The six channels the thread reports (I1, V1, I2, V2, Ihigh, Vhigh) are acquired on
ADC1/ADC2, software-triggered from a 1 ms TIM6 critical task (no PWM), and all six are watched with wide thresholds.
For each channel in turn the host asks over SWD to trip it: the firmware sets that channel's minimum threshold to
1e9, above any reading of the bare pin, and waits until the Safety API flags it alone. A printk hook copies each
"SAFETY ERROR" block the thread prints into RAM; the host reads the first block printed entirely after the trip.

- **L0-SAF-03.a-f** (I1, V1, I2, V2, Ihigh, Vhigh): the block says "true" for the tripped channel, "false" for the five others.

This test covers only #47. The Safety API on staging is the old one: its other rows in the API regression suite are
a gap until the Safety API graduates (#35).

```sh
tests/run_all.sh --l0 --target     # in a campaign (runs after the SPOC tests: it reflashes the CUT)
```

A trip drives the leg GPIOs low (the open-circuit reaction): run it on a bare Spin only, never on a power stage.
Afterwards the CUT runs the SAF firmware. The SPOC runner puts Core-CUT back. By hand, run
`SPOC_CUT_DIR=<Core-CUT checkout> bash tests/common/bench_restore.sh`.
