# L0-PAD: DataAPI pin-to-ADC resolution

Checks [Core-staging#50](https://github.com/owntech-foundation/Core-staging/issues/50): the DataAPI resolves each
Spin pin to an ADC and a channel the chip actually wires to that pin.

The CUT is flashed with env `PAD` ([`tests/firmware/data_pins/`](../../firmware/data_pins/main.cpp)): no shield, no
PWM, the pins are only read. The expected values come from the STM32G474 pin map (ADCx_INy of each MCU pin) and the
Spin pinout (Spin pin → MCU pin), written out in [`run.py`](run.py), never from the DataAPI tables under test.

Boot 1 calls `spin.data.enableAcquisition(pin)` on every pin 1..59, starts the DataAPI and software-triggers each
ADC every 10 ms:

- **L0-PAD-02**: it returns 0 on exactly the 24 ADC-capable pins.
- **L0-PAD-03**: each pin converts on its lowest-numbered ADC at its own channel, read from the ADC SQR registers.
- **L0-PAD-04**: `peekLatestValue(pin)` gives a value on each of them (floating pins on a bare CUT: any value).

Boot 2 calls `spin.data.enableAcquisition(pin, adc)` on every pin and ADC 1..5:

- **L0-PAD-05**: it returns 0 on exactly the 36 (pin, ADC) pairs the chip wires.
- **L0-PAD-06**: rank k of ADC n converts the channel of the k-th pin accepted on ADC n.
- **L0-PAD-07**: `peekLatestValue(pin)` gives a value on each pin, read through the last ADC it was enabled on.

```sh
tests/run_all.sh --l0 --target     # in a campaign (runs after the SPOC tests: it reflashes the CUT)
```

Afterwards the CUT runs the PAD firmware. The SPOC runner puts Core-CUT back. By hand, run
`SPOC_CUT_DIR=<Core-CUT checkout> bash tests/common/bench_restore.sh`.
