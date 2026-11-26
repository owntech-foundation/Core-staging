# L0-DAC: analog communication bus DAC

Checks [Core#168](https://github.com/owntech-foundation/Core/pull/168): `AnalogCommunication::init()` sets up the
DAC that `setAnalogCommValue()` writes (DAC2 channel 1, pin PA6).

The CUT is flashed with env `DAC` ([`tests/firmware/dac/`](../../firmware/dac/main.cpp)), which calls
`communication.analog.init()` with the Twist shield configuration and starts no PWM. For each level 512, 1024,
2048, 3072 and 3584, the host writes the level over SWD and the firmware applies it. The host then checks two things:

- **L0-DAC-03**: DAC2's output register (DOR1, read over SWD) holds the level.
- **L0-DAC-04**: PA6, routed by the MUX to scope CH1, averages n × 2.048 V / 4096 ±(30 mV + 3 %) (the Spin's DAC and ADCs share the 2.048 V VREFBUF) (median of 5 readings).
  The tolerance covers the scope's DC gain through the ×10 probe (about +3 % on the bench) and the offset of the buffered DAC.

Level 0 sits below the output buffer's linear range and is only recorded (L0-DAC-05), as is the fitted full scale
and offset (L0-DAC-06).

```sh
tests/run_all.sh --l0 --target     # in a campaign (runs after the SPOC tests: it reflashes the CUT)
```

Afterwards the CUT runs the DAC firmware. The SPOC runner puts Core-CUT back. By hand, run
`SPOC_CUT_DIR=<Core-CUT checkout> bash tests/common/bench_restore.sh`.
