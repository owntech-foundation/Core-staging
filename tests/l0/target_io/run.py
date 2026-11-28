#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-IOM: the IO module of the API regression suite (Core-staging#40; tiers and depth: #35). spin.gpio,
##        spin.led, spin.dac, spin.comp, spin.timer (encoder) and spin.uart at effect depth (registers read over SWD,
##        return values); GPIO, LED and DAC levels and the UART frame at signal depth on the MHO984 through the SPOC
##        MUX (CH1 only). L0-DAC (communication.analog, Core#168) is folded in under its own IDs (L0-DAC-03..06).
##
## env IO (tests/firmware/io) is a step executor: the host writes a step and its arguments over SWD and the firmware
## calls the API and acknowledges. Expected values come from RM0440 (register encodings), the Spin pinout (Spin pin ->
## MCU pin), the HAL documentation and the docs (VREF 2.048 V, 9600 baud, 'x' when no character waits), never from the
## drivers' own arithmetic. Profiles, each on a fresh boot:
##  P0 boot state (VREFBUF, DAC2 at boot).   P1 GPIO.   P2 LED.   P3 spin.dac.   P4 communication.analog (L0-DAC).
##  P5 comparator.   P6 encoder timers.   P7 UART.
## Loopback rows (L0-IOM-20..23: readPin, UART TX->RX, encoder count, comparator) need wires between CUT pins: hand-held
## until #16's switchable wires exist. They run only with tests/run_all.sh --supervised and BLOCK otherwise.
import os
import statistics
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON, os.path.join(COMMON, 'spoc')]
import target_harness as th  # noqa: E402
import waveform as wf  # noqa: E402

ENV = 'IO'
MAGIC = 0x494F4D31  # "IOM1", tests/firmware/io/main.cpp
LAYOUT = 1
FIELDS = ['magic', 'layout', 'boot_count', 'ack', 'busy', 'step', 'err', 'cycles'] + [f'ret{i}' for i in range(8)] + \
         ['stamp_ms']
STEP = dict(GPIO_CONFIG=1, GPIO_SET=2, GPIO_RESET=3, GPIO_TOGGLE=4, GPIO_WRITE=5, GPIO_READ=6, GPIO_SWEEP=7,
            LED_ON=10, LED_OFF=11, LED_TOGGLE=12, DAC_INIT=20, DAC_SET=21, DAC_CURRENT=22, DAC_SLOPE=23, COMP_INIT=30,
            TIMER_START=40, TIMER_GET=41, QUAD=42, UART_INIT=50, UART_WRITE=51, UART_READ=52, UART_SWAP=53,
            ANALOG_INIT=60, ANALOG_SET=61)
INPUT, INPUT_PULLUP, OUTPUT = 0, 1, 2
N_SPIN = 59
SWEEP_WORDS = 8
BOOT_TIMEOUT_S = 30.0

# Spin pin -> MCU pin (Spin 1.0.0 / 1.1.0 pinouts; the same table as L0-PAD), pins on a GPIO only.
SPIN_TO_MCU = {
    1: 'PB11', 2: 'PB12', 4: 'PB13', 5: 'PB14', 6: 'PB15', 7: 'PC6', 9: 'PC7', 10: 'PC8', 11: 'PC9', 12: 'PA8',
    14: 'PA9', 15: 'PA10', 16: 'PC10', 17: 'PC11', 19: 'PC12', 20: 'PB4', 21: 'PB9', 22: 'PC13', 24: 'PC0',
    25: 'PC1', 26: 'PC2', 27: 'PC3', 29: 'PA0', 30: 'PA1', 31: 'PB0', 32: 'PA5', 34: 'PA6', 35: 'PC4', 37: 'PB1',
    41: 'PB10', 42: 'PB2', 43: 'PC5', 44: 'PA7', 45: 'PA4', 46: 'PA13', 47: 'PA14', 48: 'PA15', 49: 'PD2',
    50: 'PB3', 51: 'PA2', 52: 'PA3', 53: 'PB5', 55: 'PB6', 56: 'PB7', 58: 'PB8'}
SWD_PINS = {46, 47}  # PA13/PA14: the firmware skips them (driving them would cut the probe)
PORTS = 'ABCD'
GPIO_BASE = {p: 0x48000000 + 0x400 * i for i, p in enumerate(PORTS)}
G_OFF = dict(MODER=0, OTYPER=1, OSPEEDR=2, PUPDR=3, IDR=4, ODR=5)  # word offsets
N_GPIO = 10
VREFBUF = 0x40010030
DAC_BASE = {1: 0x50000800, 2: 0x50000C00, 3: 0x50001000}
D_OFF = dict(CR=0x00, DHR12R1=0x08, DOR1=0x2C, MCR=0x3C, STR1=0x58, STMODR=0x60)
N_DAC = 0x64 // 4
COMP_BASE = 0x40010200  # COMP1..COMP7 CSR, 4 bytes apart
TIM_BASE = {3: 0x40000400, 4: 0x40000800}
T_OFF = dict(CR1=0x00, SMCR=0x08, CCMR1=0x18, CCER=0x20, CNT=0x24, ARR=0x2C)
N_TIM = 0x30 // 4
USART1 = 0x40013800
LPUART1 = 0x40008000
U_OFF = dict(CR1=0x00, CR2=0x04, CR3=0x08, BRR=0x0C, ISR=0x1C)
N_USART = 0x20 // 4

VREF = 2.048          # VREFBUF scale 0 (RM0440), the DACs' and ADCs' reference (DacHAL.h, docs)
F_PCLK2 = 170e6       # spin.dts: SYSCLK 170 MHz, APB2 prescaler 1 (USART1 kernel clock)
BAUD = 9600           # UartHAL::usart1Init
HIGH_V, LOW_V = 2.8, 0.4  # VOH >= VDD - 0.4 V (3.3 V supply) less the scope's ~3 % gain; VOL <= 0.4 V
TOL_ABS_V, TOL_REL = 0.030, 0.03  # L0-DAC's tolerance: scope DC gain through the x10 probe and the buffer's offset
DAC_LEVELS = [512, 1024, 2048, 3072, 3584]   # L0-DAC
SPIN_DAC_LEVELS = [1024, 2048, 3072]
SCOPE_GPIO = ['PA7', 'PB2', 'PC12', 'PD2']  # one pin per GPIO port, all routable by the MUX, free on the bare CUT
UART_CHAR = 0x55  # 'U': start + 01010101 + stop is a 4800 Hz square wave at 9600 baud, frames back to back
UART_BURST = 2000

# Loopback wiring (README): hand-held wires until #16.
LOOP_OUT, LOOP_IN = 'PC12', 'PC13'
ENC_A, ENC_B = 'PB4', 'PA15'
ENC_CYCLES = 100
SUB = 'abcdefghijklmnopqrstuvwxyz'

CASES = [
    ('L0-IOM-01', 'env IO builds, flashes and answers its steps'),
    ('L0-IOM-02', 'boot state: VREFBUF at 2.048 V, DAC2 channel 1 at 2048 on its pin'),
    ('L0-IOM-03', 'Spin pin numbers: configurePin/setPin/resetPin drive the right MCU pin and no other'),
    ('L0-IOM-04', 'STM32 pin names: configurePin modes, set/reset/toggle/write, readPin on the chip'),
    ('L0-IOM-05', 'GPIO levels on the scope (one pin per port)'),
    ('L0-IOM-06', 'LED: turnOn/turnOff/toggle on PA5'),
    ('L0-IOM-07', 'LED level on the scope (PA5)'),
    ('L0-IOM-08', 'spin.dac initConstValue/setConstValue: DAC1/2/3 registers'),
    ('L0-IOM-09', 'spin.dac setConstValue on PA4 (DAC1) on the scope'),
    ('L0-IOM-10', 'spin.dac currentModeInit/slopeCompensation: sawtooth registers'),
    ('L0-IOM-11', 'spin.comp initialize: COMP1/COMP3 registers'),
    ('L0-IOM-12', 'spin.timer encoder: TIM3/TIM4 registers, getIncrementalEncoderValue'),
    ('L0-IOM-13', 'spin.uart usart1Init registers; usart1ReadChar with nothing received'),
    ('L0-IOM-14', 'usart1WriteChar: frame on PB6 (TX) on the scope'),
    ('L0-IOM-15', 'usart1SwapRxTx: USART1 TX and RX swapped'),
    ('L0-DAC-03', 'Analog bus DAC (Core#168): DAC2 output register follows setAnalogCommValue'),
    ('L0-DAC-04', 'Analog bus DAC (Core#168): PA6 voltage at each level'),
    ('L0-DAC-05', 'PA6 voltage at level 0'),
    ('L0-DAC-06', 'DAC full scale and offset'),
    ('L0-IOM-20', 'SUPERVISED loopback: readPin follows a driven input'),
    ('L0-IOM-21', 'SUPERVISED loopback: UART TX -> RX, usart1ReadChar'),
    ('L0-IOM-22', 'SUPERVISED loopback: encoder count follows quadrature edges'),
    ('L0-IOM-23', 'SUPERVISED loopback: comparator output flips across its DAC threshold'),
]
SUPERVISED = {'L0-IOM-20', 'L0-IOM-21', 'L0-IOM-22', 'L0-IOM-23'}
LOOPBACK_REASON = 'needs the switchable wires of #16 (hand-held until then: tests/run_all.sh --l0 --target --supervised)'


def f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def pin_code(name):
    """STM32-style pin name -> the GpioHAL pin_t value (0x80 | port << 4 | n)."""
    return 0x80 | (PORTS.index(name[1]) << 4) | int(name[2:])


def tol(v):
    return TOL_ABS_V + TOL_REL * v


def line(cid, ok, what, expected, measured):
    verdict = ok if isinstance(ok, str) else th.judge(bool(ok))
    th.line(cid, verdict, what, expected, measured)
    return verdict in ('PASS', 'INFO')


class Regs:
    """GPIO A-D, DAC1-3, COMP1-7, TIM3/TIM4, USART1, LPUART1 and VREFBUF, read over SWD in one session."""
    REGIONS = ([(GPIO_BASE[p], N_GPIO) for p in PORTS] + [(DAC_BASE[d], N_DAC) for d in (1, 2, 3)]
               + [(COMP_BASE, 7), (TIM_BASE[3], N_TIM), (TIM_BASE[4], N_TIM), (USART1, N_USART),
                  (LPUART1, N_USART), (VREFBUF, 2)])

    def __init__(self, blocks):
        self.b = blocks

    def gpio(self, port, reg):
        return self.b[PORTS.index(port)][G_OFF[reg]]

    def mode(self, pin):
        return self.gpio(pin[1], 'MODER') >> (2 * int(pin[2:])) & 3

    def pupd(self, pin):
        return self.gpio(pin[1], 'PUPDR') >> (2 * int(pin[2:])) & 3

    def odr(self, pin):
        return self.gpio(pin[1], 'ODR') >> int(pin[2:]) & 1

    def idr(self, pin):
        return self.gpio(pin[1], 'IDR') >> int(pin[2:]) & 1

    def dac(self, n, reg):
        return self.b[4 + n - 1][D_OFF[reg] // 4]

    def comp(self, n):
        return self.b[7][n - 1]

    def tim(self, n, reg):
        return self.b[8 if n == 3 else 9][T_OFF[reg] // 4]

    def usart(self, reg):
        return self.b[10][U_OFF[reg] // 4]

    def lpuart(self, reg):
        return self.b[11][U_OFF[reg] // 4]

    def vrefbuf(self):
        return self.b[12][0]


class Bench:
    def __init__(self):
        self.rep_addr, size = th.symbol(ENV, 'g_io_report')
        if size != 4 * len(FIELDS):
            sys.exit(f'g_io_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
        self.cmd_addr, _ = th.symbol(ENV, 'g_io_cmd')
        self.args_addr, _ = th.symbol(ENV, 'g_io_args')
        self.sweep_addr, size = th.symbol(ENV, 'g_io_sweep')
        if size != 4 * N_SPIN * SWEEP_WORDS:
            sys.exit(f'g_io_sweep is {size} bytes, run.py expects {4 * N_SPIN * SWEEP_WORDS}')
        self.seq = 0
        self.log = []

    def report(self):
        w = th.read_words(self.rep_addr, len(FIELDS))
        return dict(zip(FIELDS, w)) if w else None

    def regs(self):
        blocks = th.read_blocks(Regs.REGIONS)
        return Regs(blocks) if blocks else None

    def wait_boot(self):
        t0 = time.monotonic()
        while time.monotonic() - t0 < BOOT_TIMEOUT_S:
            r = self.report()
            if r and r['magic'] == MAGIC and r['layout'] == LAYOUT and r['ack'] == 0 and r['busy'] == 0:
                self.seq = 0
                return r
            time.sleep(0.3)
        return None

    def reset(self):
        th.openocd('reset run', check=False)
        time.sleep(0.5)
        return self.wait_boot()

    def send(self, name, *args):
        self.seq += 1
        a = list(args) + [0] * (4 - len(args))
        pairs = [(self.args_addr + 4 * i, a[i]) for i in range(4)]
        pairs.append((self.cmd_addr, (self.seq << 8) | STEP[name]))
        th.write_words(pairs)
        return time.monotonic()

    def wait_ack(self, name, args, t0, timeout):
        while time.monotonic() - t0 < timeout:
            time.sleep(0.05)
            blocks = th.read_blocks([(self.rep_addr, len(FIELDS))] + Regs.REGIONS)
            if not blocks:
                continue
            rep = dict(zip(FIELDS, blocks[0]))
            if rep['ack'] == self.seq and rep['busy'] == 0:
                if rep['err']:
                    raise RuntimeError(f'step {name}{tuple(args)} refused by the firmware (err)')
                self.log.append((name, args, round(time.monotonic() - t0, 2)))
                return rep, Regs(blocks[1:])
        raise TimeoutError(f'step {name}{tuple(args)} not acknowledged in {timeout} s')

    def step(self, name, *args, timeout=5.0):
        """Runs one step; @return the report and the registers after it."""
        return self.wait_ack(name, args, self.send(name, *args), timeout)

    def sweep(self):
        w = th.read_words(self.sweep_addr, N_SPIN * SWEEP_WORDS)
        return [w[i * SWEEP_WORDS:(i + 1) * SWEEP_WORDS] for i in range(N_SPIN)] if w else None


class Scope:
    """MHO984 through the SPOC MUX, CH1 only (TP1-TP3 ties CH1 to CH2; the injector loads CH3, #5)."""

    def __init__(self, scope, mux):
        self.s, self.mux, self.pin = scope, mux, None
        scope.set_acquire('NORMal')

    def to(self, pin):
        if pin != self.pin:
            self.mux.disable_all()
            self.mux.route('ch1', pin)
            self.pin = pin
            time.sleep(0.3)

    def level_setup(self):
        self.s.setup_channel(1, scale=1.0, offset=-1.65, probe=10)
        self.s.set_timebase(1e-3)
        self.s.set_edge_trigger(1, 1.65, sweep='AUTO')
        self.s.run()

    def dac_setup(self):
        self.s.setup_channel(1, scale=0.5, offset=-1.0, probe=10)
        self.s.set_timebase(1e-3)
        self.s.set_edge_trigger(1, 1.0, sweep='AUTO')
        self.s.run()

    def vavg(self, pin, n=1, settle=0.6):
        self.to(pin)
        time.sleep(settle)
        vals = [v for v in (self.s.measure('VAVG', 1) for _ in range(n)) if v is not None]
        return (statistics.median(vals) if vals else None), vals


def level_ok(v, high):
    return v is not None and (v >= HIGH_V if high else v <= LOW_V)


def fmt_v(v):
    return 'invalid' if v is None else f'{v:.2f} V'


# ----------------------------------------------------------------------------------------------------------- P0


def p0_boot(b, sc):
    r = b.regs()
    if r is None:
        return line('L0-IOM-02', 'FAIL', 'boot state', 'registers readable', 'no SWD read')
    v = r.vrefbuf()
    ok_v = (v & 1) == 1 and (v >> 1 & 1) == 0 and (v >> 4 & 3) == 0 and (v >> 3 & 1) == 1
    good = line('L0-IOM-02.a', ok_v, 'VREFBUF enabled at 2.048 V before main() (R1)',
                'ENVR 1, HIZ 0, VRS 00 (2.048 V), VRR 1',
                f'CSR {v:#x}: ENVR {v & 1}, HIZ {v >> 1 & 1}, VRS {v >> 4 & 3}, VRR {v >> 3 & 1}')
    cr, mcr, dor = r.dac(2, 'CR'), r.dac(2, 'MCR'), r.dac(2, 'DOR1')
    good &= line('L0-IOM-02.b', cr & 1 and (mcr & 7) == 0 and dor & 0xFFF == 2048,
                 'DAC2 channel 1 initialised at boot: 2048, buffered, on its pin (R2)', 'EN1 1, MODE1 000, DOR1 2048',
                 f'EN1 {cr & 1}, MODE1 {mcr & 7:03b}, DOR1 {dor & 0xFFF}')
    return good


# ----------------------------------------------------------------------------------------------------------- P1


def p1_gpio(b, sc):
    good = True
    b.step('GPIO_SWEEP', timeout=10.0)
    sw = b.sweep()
    bad_map, bad_other, bad_none = [], [], []
    for n in range(1, N_SPIN):
        if n in SWD_PINS:
            continue
        w = sw[n]
        set_ab, set_cd, rst_ab, rst_cd = w[4], w[5], w[6], w[7]
        changed = ((set_ab ^ rst_ab), (set_cd ^ rst_cd))
        mcu = SPIN_TO_MCU.get(n)
        if mcu is None:
            if changed != (0, 0):
                bad_none.append(f'{n}: ODR changed {changed[0]:#x}/{changed[1]:#x}')
            continue
        port, bit = mcu[1], int(mcu[2:])
        k = PORTS.index(port)
        word, shift = (0, 0) if port == 'A' else (0, 16) if port == 'B' else (1, 0) if port == 'C' else (1, 16)
        mask = 1 << (bit + shift)
        moder = w[k] >> (2 * bit) & 3
        hi = (w[4 + word] & mask) != 0
        lo = (w[6 + word] & mask) == 0
        if moder != 1 or not hi or not lo:
            bad_map.append(f'{n}->{mcu}: MODER {moder:02b}, after set {int(hi)}, after reset {int(not lo)}')
        expect = [0, 0]
        expect[word] = mask
        if list(changed) != expect:
            bad_other.append(f'{n}->{mcu}: other bits {(changed[0] & ~expect[0]) | 0:#x}/{changed[1] & ~expect[1]:#x}')
    mapped = [n for n in SPIN_TO_MCU if n not in SWD_PINS]
    good &= line('L0-IOM-03.a', not bad_map, f'configurePin(n, OUTPUT), setPin(n), resetPin(n) on the {len(mapped)} '
                 'Spin GPIO pin numbers drive the MCU pin of the Spin pinout', 'MODER 01, ODR 1 after set, 0 after reset',
                 '; '.join(bad_map[:6]) or f'all {len(mapped)} right')
    good &= line('L0-IOM-03.b', not bad_other, 'setPin(n) changes no other output', 'only the pin\'s own ODR bit',
                 '; '.join(bad_other[:6]) or 'none')
    others = [n for n in range(1, N_SPIN) if n not in SPIN_TO_MCU]
    good &= line('L0-IOM-03.c', not bad_none, f'Spin pin numbers with no GPIO ({len(others)}: power, ground, ...) '
                 'change nothing', 'no ODR bit changes', '; '.join(bad_none[:6]) or 'none')

    # STM32-style names, port by port.
    bad = []
    for pin in SCOPE_GPIO:
        c = pin_code(pin)
        _, r = b.step('GPIO_CONFIG', c, OUTPUT)
        if r.mode(pin) != 1:
            bad.append(f'{pin} OUTPUT: MODER {r.mode(pin):02b}')
        _, r = b.step('GPIO_SET', c)
        s = (r.odr(pin), r.idr(pin))
        rep, r = b.step('GPIO_READ', c)
        rd_hi = rep['ret0']
        _, r = b.step('GPIO_RESET', c)
        rs = (r.odr(pin), r.idr(pin))
        rep, r = b.step('GPIO_READ', c)
        rd_lo = rep['ret0']
        _, r = b.step('GPIO_TOGGLE', c)
        t1 = r.odr(pin)
        _, r = b.step('GPIO_TOGGLE', c)
        t2 = r.odr(pin)
        _, r = b.step('GPIO_WRITE', c, 1)
        w1 = r.odr(pin)
        _, r = b.step('GPIO_WRITE', c, 0)
        w0 = r.odr(pin)
        if s != (1, 1) or rs != (0, 0) or (t1, t2) != (1, 0) or (w1, w0) != (1, 0) or (rd_hi, rd_lo) != (1, 0):
            bad.append(f'{pin}: set ODR/IDR {s}, reset {rs}, toggle {t1}{t2}, write {w1}{w0}, readPin {rd_hi}{rd_lo}')
    good &= line('L0-IOM-04.a', not bad, f'configurePin(OUTPUT), setPin, resetPin, togglePin x2, writePin(1/0), readPin '
                 f'on an output, on {", ".join(SCOPE_GPIO)}',
                 'MODER 01; ODR=IDR 1 / 0; toggle 1 then 0; write 1 then 0; readPin 1 then 0',
                 '; '.join(bad) or 'all right')
    pin = LOOP_IN
    c = pin_code(pin)
    _, r = b.step('GPIO_CONFIG', c, INPUT)
    m_in, p_in = r.mode(pin), r.pupd(pin)
    _, r = b.step('GPIO_CONFIG', c, INPUT_PULLUP)
    m_pu, p_pu = r.mode(pin), r.pupd(pin)
    rep, r = b.step('GPIO_READ', c)
    good &= line('L0-IOM-04.b', (m_in, p_in, m_pu, p_pu) == (0, 0, 0, 1) and rep['ret0'] == 1,
                 f'configurePin(INPUT), (INPUT_PULLUP), readPin on {pin} (unconnected: the pull-up holds it high)',
                 'INPUT: MODER 00, PUPDR 00; INPUT_PULLUP: MODER 00, PUPDR 01; readPin 1',
                 f'INPUT MODER {m_in:02b} PUPDR {p_in:02b}; INPUT_PULLUP MODER {m_pu:02b} PUPDR {p_pu:02b}; '
                 f"readPin {rep['ret0']}")
    b.step('GPIO_CONFIG', c, INPUT)

    # Levels on the scope.
    sc.level_setup()
    bad, seen = [], []
    for pin in SCOPE_GPIO:
        c = pin_code(pin)
        b.step('GPIO_CONFIG', c, OUTPUT)
        vals = []
        for name, args, high in (('GPIO_SET', (c,), True), ('GPIO_RESET', (c,), False),
                                 ('GPIO_WRITE', (c, 1), True), ('GPIO_TOGGLE', (c,), False),
                                 ('GPIO_TOGGLE', (c,), True), ('GPIO_WRITE', (c, 0), False)):
            b.step(name, *args)
            v, _ = sc.vavg(pin)
            vals.append(fmt_v(v))
            if not level_ok(v, high):
                bad.append(f'{pin} {name.lower()}{args[1:] or ""}: {fmt_v(v)}')
        seen.append(f'{pin} ' + '/'.join(vals))
        b.step('GPIO_CONFIG', c, INPUT)
    good &= line('L0-IOM-05', not bad, f'setPin, resetPin, writePin(1), togglePin, togglePin, writePin(0) on '
                 f'{", ".join(SCOPE_GPIO)} (MUX -> CH1, VAVG)', f'high >= {HIGH_V} V, low <= {LOW_V} V, in that order',
                 '; '.join(bad) if bad else '; '.join(seen))
    return good


# ----------------------------------------------------------------------------------------------------------- P2


def p2_led(b, sc):
    led = 'PA5'
    rows = []
    for name in ('LED_OFF', 'LED_ON', 'LED_TOGGLE', 'LED_TOGGLE', 'LED_OFF'):
        _, r = b.step(name)
        rows.append((name, r.mode(led), r.odr(led)))
    want = [0, 1, 0, 1, 0]
    ok = all(m == 1 for _, m, _ in rows) and [o for _, _, o in rows] == want
    good = line('L0-IOM-06', ok, 'turnOff (first call: lazy init), turnOn, toggle, toggle, turnOff on PA5 (led0)',
                'MODER 01; ODR 0, 1, 0, 1, 0', ', '.join(f'{n.lower()} MODER {m:02b} ODR {o}' for n, m, o in rows))
    sc.level_setup()
    bad, vals = [], []
    for name, high in (('LED_ON', True), ('LED_OFF', False), ('LED_TOGGLE', True), ('LED_TOGGLE', False)):
        b.step(name)
        v, _ = sc.vavg(led)
        vals.append(f'{name.lower()} {fmt_v(v)}')
        if not level_ok(v, high):
            bad.append(vals[-1])
    good &= line('L0-IOM-07', not bad, 'turnOn, turnOff, toggle, toggle: PA5 level (MUX -> CH1, VAVG)',
                 f'high >= {HIGH_V} V, low <= {LOW_V} V', '; '.join(bad) if bad else ', '.join(vals))
    return good


# ----------------------------------------------------------------------------------------------------------- P3


def p3_dac(b, sc):
    good = True
    for k, n in enumerate((1, 2, 3)):
        _, r = b.step('DAC_INIT', n)
        cr, mcr, dor0 = r.dac(n, 'CR'), r.dac(n, 'MCR'), r.dac(n, 'DOR1') & 0xFFF
        _, r = b.step('DAC_SET', n, 1, 1234)
        dor = r.dac(n, 'DOR1') & 0xFFF
        _, r = b.step('DAC_SET', n, 1, 3000)
        dor2 = r.dac(n, 'DOR1') & 0xFFF
        ok = cr & 1 and (cr >> 6 & 3) == 0 and (cr >> 1 & 1) == 0 and dor0 == 0 and dor == 1234 and dor2 == 3000
        if n != 3:
            ok = ok and (mcr & 7) == 0
        good &= line(f'L0-IOM-08.{SUB[k]}', ok, f'initConstValue({n}), setConstValue({n}, 1, 1234 then 3000)',
                     'EN1 1, WAVE1 00, TEN1 0, DOR1 0 after init then 1234, 3000' +
                     ('; MODE1 000 (buffered, on its pin)' if n != 3 else ''),
                     f'EN1 {cr & 1}, WAVE1 {cr >> 6 & 3:02b}, TEN1 {cr >> 1 & 1}, DOR1 {dor0} -> {dor} -> {dor2}, '
                     f'MODE1 {mcr & 7:03b}')
    line('L0-IOM-08.d', 'INFO', 'DAC3 (no pin on the STM32G474): MODE1 set by initConstValue(3)', 'recorded',
         f"MODE1 {r.dac(3, 'MCR') & 7:03b} (000 = external pin, buffered: DAC3 has no pin, RM0440 wants 011 for "
         "on-chip peripherals)")

    sc.dac_setup()
    bad, vals = [], []
    for k, n in enumerate(SPIN_DAC_LEVELS):
        b.step('DAC_SET', 1, 1, n)
        v, vs = sc.vavg('PA4', n=5, settle=1.0)
        e = n * VREF / 4096
        vals.append(f'{n}: {fmt_v(v)}')
        if v is None or abs(v - e) > tol(e):
            bad.append(f'{n}: {fmt_v(v)} (expected {e:.3f} ±{tol(e) * 1000:.0f} mV)')
    good &= line('L0-IOM-09', not bad, f'setConstValue(1, 1, n) on PA4 (DAC1 OUT1, MUX -> CH1), n = '
                 f'{", ".join(map(str, SPIN_DAC_LEVELS))}', 'median of 5 VAVG = n x 2.048 V / 4096 ±(30 mV + 3 %)',
                 '; '.join(bad) if bad else ', '.join(vals))

    return good


def p3b_current(b, sc):
    """Current mode on a fresh boot: DAC1 and DAC3 still disabled, as an application calling currentModeInit first
    finds them (RM0440: MCR.MODE can only be written while the channel is disabled)."""
    good = True
    # Sawtooth generator, decrementing (DacHAL.h), on DAC1 (pin + on-chip) and DAC3 (on-chip).
    for k, (n, tu, mode, peak, low) in enumerate(((1, 0, 0b001, 1.5, 0.5), (3, 1, 0b011, 2.0, 1.0))):
        _, r = b.step('DAC_CURRENT', n, tu)
        cr, mcr, st = r.dac(n, 'CR'), r.dac(n, 'MCR'), r.dac(n, 'STR1')
        ok = cr & 1 and (cr >> 6 & 3) == 3 and (st >> 12 & 1) == 0 and (mcr & 7) == mode
        good &= line(f'L0-IOM-10.{SUB[2 * k]}', ok, f'currentModeInit({n}, TIM{"AB"[tu]}): sawtooth, decrementing',
                     f'EN1 1, WAVE1 11 (sawtooth), STDIR1 0 (decrement), MODE1 {mode:03b}',
                     f'EN1 {cr & 1}, WAVE1 {cr >> 6 & 3:02b}, STDIR1 {st >> 12 & 1}, MODE1 {mcr & 7:03b}; '
                     f"STR1 {st:#010x}, STMODR {r.dac(n, 'STMODR'):#x} (trigger selection recorded)")
        _, r = b.step('DAC_SLOPE', n, f32(peak), f32(low))
        st = r.dac(n, 'STR1')
        rst, inc = st & 0xFFF, st >> 16
        want = min(4095, int(4096 * peak / VREF))
        steps = (peak - low) / VREF * 4096 * 16 / inc if inc else None
        good &= line(f'L0-IOM-10.{SUB[2 * k + 1]}', rst == want and inc > 0,
                     f'slopeCompensation({n}, {peak} V, {low} V): ramp starts at the peak and decrements',
                     f'STRSTDATA1 = {peak} V x 4096 / 2.048 = {want}; STINCDATA1 > 0',
                     f'STRSTDATA1 {rst}, STINCDATA1 {inc} (12.4: reaches {low} V after '
                     f'{steps:.0f} steps)' if steps else f'STRSTDATA1 {rst}, STINCDATA1 0')
    return good


# ----------------------------------------------------------------------------------------------------------- P4


def p4_analog(b, sc):
    """L0-DAC (Core#168), folded in under its IDs: communication.analog drives DAC2 channel 1 on PA6."""
    b.step('ANALOG_INIT')
    sc.dac_setup()
    sc.to('PA6')
    time.sleep(1.5)
    good, pts = True, []
    for i, n in enumerate(DAC_LEVELS + [0]):
        try:
            _, r = b.step('ANALOG_SET', n)
            dor = r.dac(2, 'DOR1') & 0xFFF
            acked = True
        except (RuntimeError, TimeoutError):
            dor, acked = None, False
        v, vals = sc.vavg('PA6', n=5, settle=1.0)
        expected = n * VREF / 4096
        if n == 0:
            line('L0-DAC-05', 'INFO', 'PA6 mean voltage at level 0 (below the buffer\'s linear range)', 'recorded',
                 'not measured' if v is None else f'{v:.3f} V, DOR {dor}')
            continue
        good &= line(f'L0-DAC-03.{SUB[i]}', acked and dor == n, f'DAC2 output register at level {n}', f'{n}',
                     'no acknowledgement from the firmware' if not acked else f'DOR {dor}')
        good &= line(f'L0-DAC-04.{SUB[i]}', v is not None and abs(v - expected) <= tol(expected),
                     f'PA6 mean voltage at level {n}', f'{expected:.3f} V ±{tol(expected) * 1000:.0f} mV',
                     'invalid' if v is None else f'{v:.3f} V ({(v - expected) * 1000:+.0f} mV; {len(vals)} readings, '
                                                 f'spread {(max(vals) - min(vals)) * 1000:.0f} mV)')
        if v is not None:
            pts.append((n, v))
    if len(pts) >= 2:
        xs, ys = zip(*pts)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        slope = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x in xs)
        line('L0-DAC-06', 'INFO', 'PA6 voltage against level: least-squares fit', 'recorded',
             f'{slope * 4096:.3f} V full scale (ideal {VREF:.3f}), offset {(my - slope * mx) * 1000:+.0f} mV')
    return good


# ----------------------------------------------------------------------------------------------------------- P5


def comp_fields(c):
    return dict(EN=c & 1, INMSEL=c >> 4 & 7, INPSEL=c >> 8 & 1, POL=c >> 15 & 1, HYST=c >> 16 & 7,
                BLANKSEL=c >> 19 & 0x1F)


def p5_comp(b, sc):
    good = True
    _, r0 = b.step('COMP_INIT', 2)
    _, r = b.step('COMP_INIT', 1)
    c1 = comp_fields(r.comp(1))
    # RM0440 COMP1: INMSEL 100 = DAC3_CH1, INPSEL 0 = PA1. COMP3: INMSEL 101 = DAC1_CH1, INPSEL 1 = PC1.
    good &= line('L0-IOM-11.a', c1 == dict(EN=1, INMSEL=4, INPSEL=0, POL=0, HYST=0, BLANKSEL=0) and r.mode('PA1') == 3,
                 'initialize(1): COMP1, minus input DAC3 CH1, plus input PA1 (CompHAL.h)',
                 'EN 1, INMSEL 100, INPSEL 0, POL 0, HYST 0, BLANKSEL 0; PA1 analog (MODER 11)',
                 f"{c1}; PA1 MODER {r.mode('PA1'):02b}")
    _, r = b.step('COMP_INIT', 3)
    c3 = comp_fields(r.comp(3))
    good &= line('L0-IOM-11.b', c3 == dict(EN=1, INMSEL=5, INPSEL=1, POL=0, HYST=0, BLANKSEL=0) and r.mode('PC1') == 3,
                 'initialize(3): COMP3, minus input DAC1 CH1, plus input PC1 (CompHAL.h)',
                 'EN 1, INMSEL 101, INPSEL 1, POL 0, HYST 0, BLANKSEL 0; PC1 analog (MODER 11)',
                 f"{c3}; PC1 MODER {r.mode('PC1'):02b}")
    good &= line('L0-IOM-11.c', r0.comp(2) == 0, 'initialize(2) (not 1 or 3) touches no comparator', 'COMP2 CSR 0',
                 f'COMP2 CSR {r0.comp(2):#x}')
    return good


# ----------------------------------------------------------------------------------------------------------- P6


def p6_timer(b, sc):
    good = True
    pins = {3: ('PC6', 'PC7'), 4: ('PB6', 'PB7')}
    for k, n in enumerate((4, 3)):
        rep, r = b.step('TIMER_GET', n)
        before = rep['ret0']
        _, r = b.step('TIMER_START', n)
        cr1, smcr, ccmr1 = r.tim(n, 'CR1'), r.tim(n, 'SMCR'), r.tim(n, 'CCMR1')
        sms = (smcr & 7) | (smcr >> 16 & 1) << 3
        pa, pb = pins[n]
        modes = (r.mode(pa), r.mode(pb), r.pupd(pa), r.pupd(pb))
        ok = cr1 & 1 and sms == 3 and (ccmr1 & 3) == 1 and (ccmr1 >> 8 & 3) == 1 and modes == (2, 2, 1, 1)
        good &= line(f'L0-IOM-12.{SUB[2 * k]}', ok, f'startLogIncrementalEncoder(TIMER{n}): TIM{n} counting in encoder '
                     f'mode on {pa}/{pb}', 'CEN 1, SMS 0011 (encoder, both inputs), CC1S = CC2S = 01; pins AF, pull-up',
                     f'CEN {cr1 & 1}, SMS {sms:04b}, CC1S {ccmr1 & 3:02b}, CC2S {ccmr1 >> 8 & 3:02b}; '
                     f'{pa}/{pb} MODER {modes[0]:02b}/{modes[1]:02b}, PUPDR {modes[2]:02b}/{modes[3]:02b}')
        rep, r = b.step('TIMER_GET', n)
        cnt = r.tim(n, 'CNT') & 0xFFFF
        good &= line(f'L0-IOM-12.{SUB[2 * k + 1]}', before == 0 and rep['ret0'] == cnt,
                     f'getIncrementalEncoderValue(TIMER{n}): 0 before the start, then the counter (no edge on a bare CUT)',
                     '0 before; TIM CNT after', f"{before} before; {rep['ret0']} after, CNT {cnt}")
    return good


# ----------------------------------------------------------------------------------------------------------- P7


def uart_burst(b, sc, pin):
    """Writes UART_BURST 'U' characters and captures pin on CH1 during the burst; (freq, duty, switching)."""
    sc.to(pin)
    sc.s.setup_channel(1, scale=1.0, offset=-1.65, probe=10)
    sc.s.set_timebase(200e-6)
    sc.s.set_edge_trigger(1, 1.65, sweep='AUTO')
    t0 = b.send('UART_WRITE', UART_CHAR, UART_BURST)
    time.sleep(0.4)
    sc.s.single()
    t, v = sc.s.waveform(1)
    b.wait_ack('UART_WRITE', (UART_CHAR, UART_BURST), t0, timeout=10.0)
    sw = wf.is_switching(v, 1.0)
    return (wf.frequency(t, v) if sw else None, wf.duty(t, v) if sw else None, sw, max(v))


def fmt_burst(s):
    f, d, sw, vmax = s
    if not sw:
        return f'flat, max {vmax:.2f} V'
    return (f'{f:.0f} Hz, duty {d:.2f}, max {vmax:.2f} V' if f and d is not None
            else f'switching, no full period, max {vmax:.2f} V')


def p7_uart(b, sc):
    good = True
    _, r = b.step('UART_INIT')
    cr1, cr2, brr = r.usart('CR1'), r.usart('CR2'), r.usart('BRR')
    m = (cr1 >> 12 & 1) | (cr1 >> 28 & 1) << 1
    over8 = cr1 >> 15 & 1
    baud = F_PCLK2 * (2 if over8 else 1) / brr if brr else 0
    ok = (cr1 & 1 and cr1 >> 2 & 1 and cr1 >> 3 & 1 and cr1 >> 5 & 1 and m == 0 and (cr1 >> 10 & 1) == 0
          and (cr2 >> 12 & 3) == 0 and abs(baud - BAUD) <= 0.01 * BAUD)
    good &= line('L0-IOM-13.a', ok, 'usart1Init(): USART1 9600 8N1, receive interrupt on',
                 'UE, RE, TE, RXNEIE 1; M 00 (8 bits), PCE 0, STOP 00; 170 MHz / BRR = 9600 ±1 %',
                 f'CR1 {cr1:#x} (UE {cr1 & 1}, RE {cr1 >> 2 & 1}, TE {cr1 >> 3 & 1}, RXNEIE {cr1 >> 5 & 1}, M {m:02b}, '
                 f'PCE {cr1 >> 10 & 1}), STOP {cr2 >> 12 & 3:02b}, BRR {brr} -> {baud:.0f} baud')
    rep, r = b.step('UART_READ')
    c = rep['ret0']
    good &= line('L0-IOM-13.b', c == ord('x'), 'usart1ReadChar() with nothing received (RX idle, pulled up)',
                 "'x' (UartHAL.h: no character waiting)", f'{c:#04x} ({chr(c) if 32 <= c < 127 else "?"})')

    sc.s.set_acquire('NORMal')
    s = uart_burst(b, sc, 'PB6')
    f_want = BAUD / 2
    ok = s[2] and s[0] is not None and abs(s[0] - f_want) <= 0.01 * f_want and s[1] is not None and abs(s[1] - 0.5) <= 0.05
    good &= line('L0-IOM-14.a', ok, f"usart1WriteChar('U') x {UART_BURST}: frame on PB6 (TX, MUX -> CH1)",
                 f'{f_want:.0f} Hz ±1 % square wave (9600 baud, 0x55 frames back to back), duty 0.50 ±0.05',
                 fmt_burst(s))

    s7 = uart_burst(b, sc, 'PB7')
    line('L0-IOM-14.b', 'INFO', 'PB7 (RX, pulled up) during the same burst, before the swap', 'recorded (flat expected)',
         fmt_burst(s7))
    rep, r = b.step('UART_READ')
    c = rep['ret0']
    line('L0-IOM-14.c', 'INFO', 'usart1ReadChar() after the bursts (does RX on PB7 hear TX on this bench?)',
         "recorded ('x': nothing received)", f'{c:#04x} ({chr(c) if 32 <= c < 127 else "?"}); PB7 IDR {r.idr("PB7")}; USART1 ISR {r.usart("ISR"):#x}')
    _, r = b.step('UART_SWAP')
    swap, lp = r.usart('CR2') >> 15 & 1, r.lpuart('CR2') >> 15 & 1
    s6 = uart_burst(b, sc, 'PB6')
    s7 = uart_burst(b, sc, 'PB7')
    # PB6 is judged on the scope; PB7 only recorded: on this bench the MUX route to PB7 (mux2 input 7) shows PB6's
    # signal while RX on PB7 receives nothing (L0-IOM-14.b/.c), so a TX on PB7 cannot be told from one on PB6.
    good &= line('L0-IOM-15', swap == 1 and not s6[2], "usart1SwapRxTx(), then usart1WriteChar('U'): TX leaves PB6",
                 'USART1 CR2.SWAP 1; PB6 flat during the burst (PB7 recorded)',
                 f'USART1 SWAP {swap} (LPUART1 SWAP {lp}); PB6 {fmt_burst(s6)}; PB7 {fmt_burst(s7)}')
    return good


# ----------------------------------------------------------------------------------------------------------- supervised


def tty_prompt(text):
    with open('/dev/tty', 'r+') as tty:
        tty.write('\n' + text + '\n  Press Enter when done (or type s to skip): ')
        tty.flush()
        return tty.readline().strip().lower() != 's'


WIRING = ('L0-IOM-20..23 (supervised loopbacks): with the CUT powered, put six wires between CUT pins (Spin pin numbers '
          'on the header):\n'
          '  W1 PC12 (19) - PC13 (22)   readPin\n'
          '  W2 PB6  (55) - PB7  (56)   UART TX -> RX\n'
          '  W3 PB4  (20) - PC6  (7)    encoder A (TIM3 CH1)\n'
          '  W4 PA15 (48) - PC7  (9)    encoder B (TIM3 CH2)\n'
          '  W5 PA4  (45) - PA1  (30)   DAC1 -> COMP1 plus input\n'
          '  W6 PA6  (34) - PC1  (25)   DAC2 -> COMP3 plus input')


def supervised(b, sc):
    who = os.environ.get('CORE_SUPERVISOR') or th.subprocess.run(
        ['git', 'config', 'user.name'], capture_output=True, text=True).stdout.strip() or 'unknown'
    sc.mux.disable_all()
    sc.pin = None
    if not tty_prompt(WIRING):
        for cid, what in CASES:
            if cid in SUPERVISED:
                th.line(cid, 'BLOCKED', what, '-', f'skipped by the supervisor ({who})')
        return True
    line('L0-IOM-20.0', 'INFO', 'supervised by', 'recorded', who)
    good = True
    try:
        good &= loop_gpio(b)
        good &= loop_uart(b)
        good &= loop_encoder(b)
        good &= loop_comp(b)
    finally:
        b.reset()  # outputs off before the wires come out
        tty_prompt('Remove the six wires W1-W6 (other firmware drives PC6/PC7, PA4, PA6 as outputs).')
    return good


def loop_gpio(b):
    b.reset()
    o, i = pin_code(LOOP_OUT), pin_code(LOOP_IN)
    b.step('GPIO_CONFIG', o, OUTPUT)
    b.step('GPIO_CONFIG', i, INPUT)
    got = []
    for name, args in (('GPIO_SET', (o,)), ('GPIO_RESET', (o,)), ('GPIO_WRITE', (o, 1)), ('GPIO_TOGGLE', (o,))):
        b.step(name, *args)
        rep, _ = b.step('GPIO_READ', i)
        got.append(rep['ret0'])
    b.step('GPIO_CONFIG', i, INPUT_PULLUP)
    rep, _ = b.step('GPIO_READ', i)
    got.append(rep['ret0'])
    return line('L0-IOM-20', got == [1, 0, 1, 0, 0], f'readPin({LOOP_IN}) while {LOOP_OUT} (W1) is set, reset, written 1, '
                'toggled; then INPUT_PULLUP with the driver low', '1, 0, 1, 0, 0', ', '.join(map(str, got)))


def loop_uart(b):
    b.reset()
    b.step('UART_INIT')
    got = []
    for ch in ('A', 'z'):
        b.step('UART_WRITE', ord(ch), 1)
        time.sleep(0.05)
        rep, _ = b.step('UART_READ')
        got.append(rep['ret0'])
    rep, _ = b.step('UART_READ')
    got.append(rep['ret0'])
    txt = ', '.join(repr(chr(c)) if 32 <= c < 127 else f'{c:#04x}' for c in got)
    return line('L0-IOM-21', got == [ord('A'), ord('z'), ord('x')],
                "usart1WriteChar('A') / ('z') looped PB6 -> PB7 (W2): usart1ReadChar returns each, then 'x'",
                "'A', 'z', 'x'", txt)


def loop_encoder(b):
    b.reset()
    a, bb = pin_code(ENC_A), pin_code(ENC_B)
    b.step('GPIO_CONFIG', a, OUTPUT)
    b.step('GPIO_CONFIG', bb, OUTPUT)
    b.step('GPIO_WRITE', a, 0)
    b.step('GPIO_WRITE', bb, 0)
    _, r = b.step('TIMER_START', 3)
    c0 = b.step('TIMER_GET', 3)[0]['ret0']
    b.step('QUAD', a, bb, ENC_CYCLES, 0, timeout=10.0)
    c1 = b.step('TIMER_GET', 3)[0]['ret0']
    b.step('QUAD', a, bb, ENC_CYCLES, 1, timeout=10.0)
    c2 = b.step('TIMER_GET', 3)[0]['ret0']
    d1, d2 = (c1 - c0 + 0x8000) % 0x10000 - 0x8000, (c2 - c1 + 0x8000) % 0x10000 - 0x8000
    n = 4 * ENC_CYCLES  # encoder mode on both inputs counts every edge (RM0440, SMS 0011)
    return line('L0-IOM-22', abs(d1) == n and d2 == -d1,
                f'getIncrementalEncoderValue(TIMER3) over {ENC_CYCLES} quadrature cycles on {ENC_A}/{ENC_B} (W3/W4), '
                'A leading then B leading (index PD2 left unconnected)', f'±{n} counts, then back by the same amount',
                f'{c0} -> {c1} ({d1:+d}) -> {c2} ({d2:+d})')


def loop_comp(b):
    good = True
    # COMP1: minus = DAC3 (internal, set through currentModeInit then setConstValue), plus = PA1 <- DAC1 on PA4 (W5).
    # COMP3: minus = DAC1 (pin + internal, the same way), plus = PC1 <- DAC2 on PA6 (W6). Threshold 2048 (1.024 V).
    for k, (comp, ref, stim) in enumerate(((1, 3, 1), (3, 1, 2))):
        b.reset()
        b.step('DAC_CURRENT', ref, 0)
        b.step('DAC_SET', ref, 1, 2048)
        b.step('DAC_INIT', stim)
        b.step('COMP_INIT', comp)
        got = []
        for level in (1024, 3072, 1024):
            b.step('DAC_SET', stim, 1, level)
            time.sleep(0.05)
            r = b.regs()
            got.append(r.comp(comp) >> 30 & 1)
        good &= line(f'L0-IOM-23.{SUB[k]}', got == [0, 1, 0],
                     f'COMP{comp} output (CSR VALUE) with DAC{ref} at 1.024 V on the minus input and DAC{stim} at '
                     f'0.512 V, 1.536 V, 0.512 V on the plus input (W{5 + k})', '0, 1, 0', ', '.join(map(str, got)))
    return good


# ----------------------------------------------------------------------------------------------------------- main


def blocked(reason, only=None):
    for cid, what in CASES:
        if only is None or cid in only:
            th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def run_profile(name, fn, b, sc, first):
    only = os.environ.get('CORE_IO_PROFILES')  # development: e.g. "P7" runs only that profile
    if only and name.split()[0] not in only.split(','):
        return True
    t0 = time.monotonic()
    if b.reset() is None:
        th.line(first, 'FAIL', f'{name}: fresh boot', 'report reachable', 'firmware did not boot after the reset')
        return False
    try:
        ok = fn(b, sc)
    except Exception as e:  # noqa: BLE001 - a step that never returns must not hide the remaining profiles
        th.line(first, 'FAIL', f'{name} stopped', 'every step acknowledged', f'{type(e).__name__}: {e}')
        ok = False
    print(f'== {name}: {time.monotonic() - t0:.0f} s, {len(b.log)} steps so far', flush=True)
    return ok


def main():
    t_start = time.monotonic()
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT, needs the SPOC bench)')
    import rigol_mho
    from spoc import boards
    from mux_controller import MuxController
    mux_port = boards.find('mux')
    if not mux_port:
        return blocked('MUX Spin not found (bash tests/common/spoc_preflight.sh)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_io_')
    flash_log = os.path.join(out, 'target_io_flash.log')
    rc = th.flash(ENV, flash_log)
    if rc != 0:
        th.line('L0-IOM-01', 'FAIL', 'env IO builds and flashes over the ST-Link', 'pio upload exit 0',
                f'exit {rc}, see {os.path.basename(flash_log)}')
        blocked('flash failed', [c for c, _ in CASES[1:]])
        return 1
    b = Bench()
    scope = rigol_mho.RigolScope(rigol_mho.open_transport())
    mux = MuxController(mux_port)
    mux.connect()
    good = True
    try:
        mux.disable_all()
        sc = Scope(scope, mux)
        boot = b.wait_boot()
        good &= line('L0-IOM-01', boot is not None, 'env IO builds, flashes and answers over SWD',
                     f'pio upload exit 0, magic {MAGIC:#010x}', 'no report' if boot is None else
                     f"magic {boot['magic']:#010x}, boot {boot['boot_count']}")
        if boot is None:
            return 1
        good &= run_profile('P0 boot state', p0_boot, b, sc, 'L0-IOM-02')
        good &= run_profile('P1 GPIO', p1_gpio, b, sc, 'L0-IOM-03')
        good &= run_profile('P2 LED', p2_led, b, sc, 'L0-IOM-06')
        good &= run_profile('P3 spin.dac', p3_dac, b, sc, 'L0-IOM-08')
        good &= run_profile('P3b current mode', p3b_current, b, sc, 'L0-IOM-10')
        good &= run_profile('P4 analog bus (L0-DAC)', p4_analog, b, sc, 'L0-DAC-03')
        good &= run_profile('P5 comparator', p5_comp, b, sc, 'L0-IOM-11')
        good &= run_profile('P6 encoder timers', p6_timer, b, sc, 'L0-IOM-12')
        good &= run_profile('P7 UART', p7_uart, b, sc, 'L0-IOM-13')
        if os.environ.get('CORE_SUPERVISED') == '1':
            good &= supervised(b, sc)
        else:
            blocked(LOOPBACK_REASON, SUPERVISED)
        b.reset()  # leave the CUT with no output driven
    finally:
        mux.disable_all()
        mux.close()
    line('L0-IOM-24', 'INFO', 'module duration (flash, nine profiles, scope)', 'recorded',
         f'{time.monotonic() - t_start:.0f} s, {len(b.log)} steps')
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
