#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-PAD: DataAPI pin-to-ADC resolution on every Spin pin, judged on the chip over SWD (Core-staging#50).
##
## env PAD (tests/firmware/data_pins) is flashed on the CUT. The expected values come from the STM32G474 pin map
## (ADCx_INy of each MCU pin, as in the Zephyr STM32 HAL pinctrl) and the Spin pinout (GpioHAL, docs/images/
## SPIN_1.1.0_*_ADC_only.svg), never from the DataAPI tables under test.
## Boot 1, mode 1: spin.data.enableAcquisition(pin) on pins 1..59.
##  - L0-PAD-02: accepted exactly on the ADC-capable pins.
##  - L0-PAD-03: each ADC-capable pin converts on its lowest-numbered ADC, at its own channel (ADC SQR registers).
##  - L0-PAD-04: peekLatestValue(pin) gives a value on every ADC-capable pin (floating on a bare CUT: any value).
## Boot 2, mode 2: spin.data.enableAcquisition(pin, adc) on every pin and ADC 1..5.
##  - L0-PAD-05: accepted exactly on the (pin, ADC) pairs the chip wires.
##  - L0-PAD-06: each accepted pair converts its own channel (rank k of ADC n = k-th pin accepted on ADC n).
##  - L0-PAD-07: peekLatestValue(pin) gives a value on every ADC-capable pin, read through its last ADC.
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON]
import target_harness as th  # noqa: E402

ENV = 'PAD'
MAGIC = 0x50414431  # "PAD1"
LAYOUT = 1
LAST_PIN = 59
HEAD = ['magic', 'layout', 'req_mode', 'req_seq', 'ack_seq', 'start_rc', 'triggers']
WORDS = len(HEAD) + 2 * (LAST_PIN + 1)
NO_VALUE = -10000.0
BOOT_TIMEOUT_S = 30.0
MIN_TRIGGERS = 20

# Spin pin -> MCU pin (GpioHAL::getPinNumber/getGpioDevice; Spin 1.0.0 and 1.1.0 pinouts), pins on a GPIO only.
SPIN_TO_MCU = {
    1: 'PB11', 2: 'PB12', 4: 'PB13', 5: 'PB14', 6: 'PB15', 7: 'PC6', 9: 'PC7', 10: 'PC8', 11: 'PC9', 12: 'PA8',
    14: 'PA9', 15: 'PA10', 16: 'PC10', 17: 'PC11', 19: 'PC12', 20: 'PB4', 21: 'PB9', 22: 'PC13', 24: 'PC0',
    25: 'PC1', 26: 'PC2', 27: 'PC3', 29: 'PA0', 30: 'PA1', 31: 'PB0', 32: 'PA5', 34: 'PA6', 35: 'PC4', 37: 'PB1',
    41: 'PB10', 42: 'PB2', 43: 'PC5', 44: 'PA7', 45: 'PA4', 46: 'PA13', 47: 'PA14', 48: 'PA15', 49: 'PD2',
    50: 'PB3', 51: 'PA2', 52: 'PA3', 53: 'PB5', 55: 'PB6', 56: 'PB7', 58: 'PB8'}

# STM32G474RE: ADC regular inputs on GPIO pins, {MCU pin: {ADC: channel}} (DS12288 / hal_stm32 pinctrl adcN_inM_pXY).
MCU_ADC = {
    'PA0': {1: 1, 2: 1}, 'PA1': {1: 2, 2: 2}, 'PA2': {1: 3}, 'PA3': {1: 4}, 'PA4': {2: 17}, 'PA5': {2: 13},
    'PA6': {2: 3}, 'PA7': {2: 4}, 'PA8': {5: 1}, 'PA9': {5: 2}, 'PB0': {1: 15, 3: 12}, 'PB1': {1: 12, 3: 1},
    'PB2': {2: 12}, 'PB11': {1: 14, 2: 14}, 'PB12': {1: 11, 4: 3}, 'PB13': {3: 5}, 'PB14': {1: 5, 4: 4},
    'PB15': {2: 15, 4: 5}, 'PC0': {1: 6, 2: 6}, 'PC1': {1: 7, 2: 7}, 'PC2': {1: 8, 2: 8}, 'PC3': {1: 9, 2: 9},
    'PC4': {2: 5}, 'PC5': {2: 11}}

EXPECTED = {pin: MCU_ADC.get(SPIN_TO_MCU.get(pin), {}) for pin in range(1, LAST_PIN + 1)}
ADC_PINS = [p for p in EXPECTED if EXPECTED[p]]

ADC_BASE = {1: 0x50000000, 2: 0x50000100, 3: 0x50000400, 4: 0x50000500, 5: 0x50000600}
SQR1 = 0x30


def f32(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def report(addr):
    w = th.read_words(addr, WORDS)
    if not w:
        return None
    rep = dict(zip(HEAD, w[:len(HEAD)]))
    if rep['start_rc'] >= 1 << 31:
        rep['start_rc'] -= 1 << 32
    n = len(HEAD)
    rep['accept'] = w[n:n + LAST_PIN + 1]
    rep['peek'] = [f32(b) for b in w[n + LAST_PIN + 1:n + 2 * (LAST_PIN + 1)]]
    return rep


def sequence(adc):
    """@brief Channels ADC adc converts, in rank order, from SQR1..SQR4 (length L+1, set by the last trigger)."""
    w = th.read_words(ADC_BASE[adc] + SQR1, 4)
    if w is None:
        return None
    if w == [0, 0, 0, 0]:
        return []  # reset value: never configured nor triggered (the firmware skips ADCs without channels)
    length = (w[0] & 0xF) + 1
    fields = [(w[0] >> s) & 0x1F for s in (6, 12, 18, 24)]
    for reg in (w[1], w[2]):
        fields += [(reg >> s) & 0x1F for s in (0, 6, 12, 18, 24)]
    fields += [(w[3] >> s) & 0x1F for s in (0, 6)]
    return fields[:length]


def wait_boot(addr):
    t0 = time.monotonic()
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        rep = report(addr)
        # After a reset RAM keeps the previous boot until startup clears it: a fresh boot has req_seq 0.
        if rep and rep['magic'] == MAGIC and rep['layout'] == LAYOUT and rep['req_seq'] == 0 and rep['ack_seq'] == 0:
            return rep
        time.sleep(0.5)
    return None


def run_mode(addr, mode, timeout=10.0):
    th.write_word(addr + 4 * HEAD.index('req_mode'), mode)
    th.write_word(addr + 4 * HEAD.index('req_seq'), 1)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rep = report(addr)
        if rep and rep['ack_seq'] == 1 and (rep['start_rc'] != 0 or rep['triggers'] >= MIN_TRIGGERS):
            return rep
        time.sleep(0.3)
    return report(addr)


def name(pin):
    return f"{pin} ({SPIN_TO_MCU.get(pin, '-')})"


def fmt_pairs(d):
    return ', '.join(f'ADC{a} IN{c}' for a, c in sorted(d.items())) or 'none'


def values_case(cid, what, rep):
    bad = [name(p) for p in ADC_PINS if rep['peek'][p] == NO_VALUE]
    stray = [name(p) for p in range(1, LAST_PIN + 1) if p not in ADC_PINS and rep['peek'][p] != NO_VALUE]
    ok = not bad and not stray
    th.line(cid, th.judge(ok), what, f'a value on each of the {len(ADC_PINS)} ADC-capable pins, NO_VALUE elsewhere',
            f"{rep['triggers']} rounds; " + (f"all {len(ADC_PINS)} give a value" if ok else
            f"NO_VALUE on {', '.join(bad) or 'none'}; value on non-ADC pins {', '.join(stray) or 'none'}"))
    return ok


def default_boot(addr):
    good = True
    rep = run_mode(addr, 1)
    if rep is None or rep['ack_seq'] != 1 or rep['start_rc'] != 0:
        th.line('L0-PAD-02', 'FAIL', 'mode 1: enableAcquisition(pin) on every pin', 'start() returns 0',
                'no acknowledgement' if rep is None or rep['ack_seq'] != 1 else f"start() returns {rep['start_rc']}")
        return False
    accepted = [p for p in range(1, LAST_PIN + 1) if rep['accept'][p] & 1]
    missing = [name(p) for p in ADC_PINS if p not in accepted]
    extra = [name(p) for p in accepted if p not in ADC_PINS]
    ok = not missing and not extra
    th.line('L0-PAD-02', th.judge(ok), 'mode 1: enableAcquisition(pin) accepts exactly the ADC-capable pins',
            f'0 on the {len(ADC_PINS)} ADC-capable pins, -1 elsewhere',
            f'{len(accepted)} accepted' + ('' if ok else
            f"; refused {', '.join(missing) or 'none'}; accepted without ADC {', '.join(extra) or 'none'}"))
    good &= ok

    seqs = {a: sequence(a) for a in ADC_BASE}
    converted = {(a, c) for a, s in seqs.items() if s for c in s}
    wrong = []
    for p in ADC_PINS:
        adc = min(EXPECTED[p])
        if (adc, EXPECTED[p][adc]) not in converted:
            wrong.append(f'{name(p)} not ADC{adc} IN{EXPECTED[p][adc]}')
    wanted = {(min(EXPECTED[p]), EXPECTED[p][min(EXPECTED[p])]) for p in ADC_PINS}
    stray = sorted(converted - wanted)
    ok = all(s is not None for s in seqs.values()) and not wrong and not stray
    th.line('L0-PAD-03', th.judge(ok), 'mode 1: each ADC-capable pin converts on its lowest ADC, at its own channel',
            'ADC SQR sequences hold exactly the lowest-ADC channel of each pin',
            '; '.join(f"ADC{a} {s}" for a, s in seqs.items()) +
            ('' if ok else f" | wrong: {', '.join(wrong) or 'none'}; unexpected: "
             f"{', '.join(f'ADC{a} IN{c}' for a, c in stray) or 'none'}"))
    good &= ok
    good &= values_case('L0-PAD-04', 'mode 1: peekLatestValue(pin) gives a value on every ADC-capable pin', rep)
    return good


def every_adc_boot(addr):
    good = True
    rep = wait_boot(addr)
    if rep is None:
        th.line('L0-PAD-05', 'FAIL', 'mode 2: enableAcquisition(pin, adc) on every pin and ADC', '-',
                'firmware did not boot after the reset')
        return False
    rep = run_mode(addr, 2)
    if rep is None or rep['ack_seq'] != 1 or rep['start_rc'] != 0:
        th.line('L0-PAD-05', 'FAIL', 'mode 2: enableAcquisition(pin, adc) on every pin and ADC', 'start() returns 0',
                'no acknowledgement' if rep is None or rep['ack_seq'] != 1 else f"start() returns {rep['start_rc']}")
        return False
    diffs = []
    n_pairs = 0
    for p in range(1, LAST_PIN + 1):
        got = {a for a in range(1, 6) if rep['accept'][p] >> a & 1}
        n_pairs += len(got)
        if got != set(EXPECTED[p]):
            diffs.append(f"{name(p)} ADC {sorted(got) or '-'} (chip: {sorted(EXPECTED[p]) or '-'})")
    want_pairs = sum(len(v) for v in EXPECTED.values())
    ok = not diffs
    th.line('L0-PAD-05', th.judge(ok), 'mode 2: enableAcquisition(pin, adc) accepts exactly the wired (pin, ADC) pairs',
            f'0 on the {want_pairs} pairs of the STM32G474 pin map, -1 elsewhere',
            f'{n_pairs} pairs accepted' + ('' if ok else '; ' + '; '.join(diffs)))
    good &= ok

    wrong = []
    seqs = {}
    for a in ADC_BASE:
        pins = [p for p in range(1, LAST_PIN + 1) if rep['accept'][p] >> a & 1]
        s = sequence(a) if pins else []
        seqs[a] = s
        if s is None:
            wrong.append(f'ADC{a} SQR not read')
            continue
        if pins and len(s) != len(pins):
            wrong.append(f'ADC{a} converts {len(s)} ranks for {len(pins)} pins')
        for p, c in zip(pins, s):
            if EXPECTED[p].get(a) != c:
                wrong.append(f"{name(p)} ADC{a} IN{c} (chip: {'IN%d' % EXPECTED[p][a] if a in EXPECTED[p] else 'none'})")
    ok = not wrong
    th.line('L0-PAD-06', th.judge(ok), 'mode 2: each accepted (pin, ADC) pair converts its own channel',
            'rank k of ADC n = channel of the k-th pin accepted on ADC n',
            '; '.join(f'ADC{a} {s}' for a, s in seqs.items()) + ('' if ok else ' | ' + '; '.join(wrong)))
    good &= ok
    good &= values_case('L0-PAD-07', 'mode 2: peekLatestValue(pin) gives a value on every ADC-capable pin', rep)
    return good


def blocked(reason):
    for cid, what in (('L0-PAD-01', 'env PAD builds and flashes over the ST-Link'),
                      ('L0-PAD-02', 'enableAcquisition(pin) accepts exactly the ADC-capable pins'),
                      ('L0-PAD-03', 'default ADC and channel of each pin'),
                      ('L0-PAD-04', 'a value arrives on each pin (default ADC)'),
                      ('L0-PAD-05', 'enableAcquisition(pin, adc) accepts exactly the wired pairs'),
                      ('L0-PAD-06', 'channel of each (pin, ADC) pair'),
                      ('L0-PAD-07', 'a value arrives on each pin (last ADC)')):
        th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_pad_')
    flash_log = os.path.join(out, 'target_data_pins_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-PAD-01', th.judge(rc == 0), 'env PAD builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        blocked('flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_pad_report')
    if size != 4 * WORDS:
        sys.exit(f'g_pad_report is {size} bytes, run.py expects {4 * WORDS}')
    if wait_boot(addr) is None:
        th.line('L0-PAD-02', 'FAIL', 'mode 1: enableAcquisition(pin) on every pin', '-', 'firmware did not boot')
        return 1
    good = default_boot(addr)
    th.openocd('reset run', check=False)  # one configuration per boot
    good &= every_adc_boot(addr)
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
