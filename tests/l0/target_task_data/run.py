#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-TKD: the TASK + DATA module of the API regression suite (Core-staging#39; tiers and depth: #35). Every
##        `task` (TaskAPI, 8) and `spin.data` (DataAPI, 18) call at effect depth on the chip: return values, the run
##        and loop counters, the ADC and NVIC registers read over SWD, ADC samples of a DAC level.
##
## env TKD (tests/firmware/task_data) is a step executor: the host writes a step and its arguments over SWD, the
## firmware calls the API and acknowledges. Expected values come from the headers' documentation (TaskAPI.h,
## DataAPI.h), RM0440 (ADC CFGR fields; EXTSEL codes as ST's LL header encodes them), Zephyr (irq_lock() BASEPRI,
## zero-latency interrupts above it) and the Kconfig defaults; never from the driver's own arithmetic.
## Profiles, each on a fresh boot:
##  PT  task: createCritical / startCritical / stopCritical on both sources, the zero-latency check, background
##      tasks (create past the maximum, start, stop, suspend Ms/Us).
##  PD1 data, software trigger, no task: before start, enable, conversion parameters, start, trigger, raw, values,
##      peek, latest, convert, stop and restart, heap probe; stores two conversion entries in NVS.
##  PD2 data after a reset: the NVS entries retrieved (then deleted), PWM trigger and discontinuous mode registers,
##      startCritical(true) on a TIM6 (zero-latency) task making the hot-path DataAPI calls.
## Folded into the module (their IDs kept): L0-CTK (createCritical range, task period), L0-ADD (dispatch under the
## critical task), L0-PAD (pin-to-ADC map), L0-TIM (task timing and hot-path cost). This test adds only what they do
## not check.
import math
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON]
import target_harness as th  # noqa: E402

ENV = 'TKD'
MAGIC = 0x544B4431  # "TKD1", tests/firmware/task_data/main.cpp
LAYOUT = 1
N_RET = 16
BG_FIELDS = ['loops', 'n', 'sum_us', 'min_us', 'max_us', 'priority']
FIELDS = (['magic', 'layout', 'boot_count', 'ack', 'busy', 'step', 'err', 'cycles'] + [f'ret{i}' for i in range(N_RET)]
          + ['crit_runs', 'stamp_runs', 'stamp_cyc', 'stamp_ms', 'core_hz']
          + [f'bg{i}_{f}' for i in range(3) for f in BG_FIELDS]
          + ['cd_values_n', 'cd_raw_n', 'cd_latest', 'cd_dv_ok', 'cd_peek', 'cd_convert', 'cd_last_value'])
STEP = dict(CREATE_CRIT=1, START_CRIT=2, STOP_CRIT=3, CREATE_BG=4, START_BG=5, STOP_BG=6, BG_CONFIG=7, BG_RESET=8,
            MASK=9, PWM=10, ENABLE=20, TRIG_SRC=21, DISC=22, START=23, STOP=24, STARTED=25, TRIGGER=26, RAW=27,
            VALUES=28, PEEK=29, LATEST=30, CONVERT=31, SET_LIN=32, SET_NTC=33, GET_PARAM=34, STORE=35, RETRIEVE=36,
            NVS_DELETE=37, DAC=38, HEAP_PROBE=39, CRIT_DATA=40)
HRTIM, TIM6 = 0, 1
FN_COUNTER, FN_NULL, FN_DATA = 0, 1, 2
ADC_1, ADC_2 = 1, 2
SOFTWARE, PWM = 0, 1

# DataAPI.h
NO_VALUE = -10000.0
DATA_IS_OK, DATA_IS_OLD, DATA_IS_MISSING = 0, 1, 2
CONV_LINEAR, CONV_THERM, CONV_NO_CHANNEL = 0, 1, -5
DOC_ERROR = -5000.0          # DataAPI.h: convertValue / getConversionParameterValue "returns -5000" on error
# Pins: 34 (PA6, ADC2 IN3) driven by DAC2; 35 (PC4, ADC2 IN5) floating; 51/52 (PA2/PA3, ADC1 IN3/IN4) for NVS;
# 32 (PA5, ADC2 IN13) enabled after start; 3 is not an ADC pin (L0-PAD's map).
PIN_DAC, PIN_FLOAT, PIN_NTC, PIN_LIN, PIN_LATE, PIN_NONE = 34, 35, 51, 52, 32, 3
LIN35 = (0.5, 10.0)          # gain, offset on pin 35: exact in float, raw = 2 (v - 10)
LIN52 = (0.25, 3.0)          # stored in NVS (PD1), retrieved after the reset (PD2)
NTC51 = (10000.0, 3950.0, 10000.0, 298.15)  # r0, b, rdiv, t0
DAC_TOL = 150                # LSB: ADC sample of DAC2's level on PA6 (L0-ADD-03's tolerance)

# RM0440 ADC registers (ADC1 0x50000000, ADC2 0x50000100): CR 0x08, CFGR 0x0C, SQR1 0x30.
ADC_BASE = {1: 0x50000000, 2: 0x50000100}
ADC_WORDS = 0x34 // 4
# RM0440 Table "ADC1/ADC2 external triggers for regular channels", as stm32g4xx_ll_adc.h encodes them
# (LL_ADC_REG_TRIG_EXT_HRTIM_TRGn): HRTIM_ADC_TRG1 = 21, TRG3 = 22.
EXTSEL_HRTIM_TRG = {1: 21, 3: 22}
# NVIC interrupt priority bytes (IPR at 0xE000E400 + IRQ): TIM6_DAC_IRQn 54 (source_tim6), HRTIM1_Master_IRQn 67.
NVIC_IPR = 0xE000E400
IRQ_TIM6, IRQ_HRTIM = 54, 67
IPR_ADDR, IPR_WORDS = NVIC_IPR + 52, 4   # bytes 52..67

BOOT_TIMEOUT_S = 30.0
TICK_US = 100                # CONFIG_SYS_CLOCK_TICKS_PER_SEC = 10000 (Zephyr default of the Spin build)
MAX_BG = 3                   # CONFIG_OWNTECH_TASK_MAX_ASYNCHRONOUS_TASKS default (owntech_task_api Kconfig)
PERIOD_TOL = 0.01

CASES = [
    ('L0-TKD-01', 'env TKD builds, flashes and answers its steps'),
    ('L0-TKD-02', 'createCritical refuses what TaskAPI.h refuses, and redefines a stopped task'),
    ('L0-TKD-03', 'startCritical: nothing before createCritical; startCritical(false) runs the task only'),
    ('L0-TKD-04', 'stopCritical stops the task; startCritical resumes it'),
    ('L0-TKD-05', 'the critical task is a zero-latency interrupt (source_tim6): it runs while the kernel masks'),
    ('L0-TKD-06', 'createBackground numbers the tasks and refuses past the maximum'),
    ('L0-TKD-07', 'startBackground / stopBackground'),
    ('L0-TKD-08', 'suspendBackgroundMs: loop period'),
    ('L0-TKD-09', 'suspendBackgroundUs: loop period'),
    ('L0-TKD-10', 'before start(): started() false, no value, DATA_IS_MISSING'),
    ('L0-TKD-11', 'start / started; enableAcquisition after start is refused'),
    ('L0-TKD-12', 'configureTriggerSource: ADC CFGR EXTEN/EXTSEL, and the documented defaults'),
    ('L0-TKD-13', 'configureDiscontinuousMode: ADC CFGR DISCEN/DISCNUM'),
    ('L0-TKD-14', 'triggerAcquisition + getRawValues: one sample per trigger, values follow the input'),
    ('L0-TKD-15', 'peekLatestValue follows the input and does not consume'),
    ('L0-TKD-16', 'getLatestValue and its dataValid flag'),
    ('L0-TKD-17', 'getValues: gain x raw + offset, one per sample'),
    ('L0-TKD-18', 'convertValue: linear and NTC, errors'),
    ('L0-TKD-19', 'setConversionParametersLinear, getConversionParameterValue / Type'),
    ('L0-TKD-20', 'setConversionParametersNtcThermistor, read back'),
    ('L0-TKD-21', 'store / retrieve conversion parameters across a reset'),
    ('L0-TKD-22', 'stop, and start again after stop'),
    ('L0-TKD-23', 'the hot-path DataAPI calls allocate nothing (Core-staging#56)'),
    ('L0-TKD-24', 'startCritical(true) on a TIM6 task making the hot-path DataAPI calls'),
    ('L0-TKD-25', 'module duration'),
]


def f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def fl(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xFFFFFFFF))[0]


def s32(u):
    return u - (1 << 32) if u >= 1 << 31 else u


def ntc_celsius(raw, r0, b, rdiv, t0):
    """Beta model of an NTC in a divider (the thermistor's datasheet equation): V = raw / 4096 x 2.048 V (Spin ADC
    reference, VREFBUF), R = rdiv V / (3.3 V - V), 1/T = 1/T0 + ln(R/R0)/B."""
    v = raw / 4096.0 * 2.048
    r = rdiv * v / (3.3 - v)
    return 1.0 / (1.0 / t0 + math.log(r / r0) / b) - 273.15


class Adc:
    def __init__(self, words):
        self.cr, self.cfgr, self.sqr1 = words[2], words[3], words[12]

    @property
    def exten(self):
        return self.cfgr >> 10 & 3

    @property
    def extsel(self):
        return self.cfgr >> 5 & 0x1F

    @property
    def discen(self):
        return self.cfgr >> 16 & 1

    @property
    def discnum(self):
        return self.cfgr >> 17 & 7

    def __str__(self):
        return (f'CFGR {self.cfgr:#010x} (EXTEN {self.exten}, EXTSEL {self.extsel}, DISCEN {self.discen}, '
                f'DISCNUM {self.discnum}), CR {self.cr:#x}')


class Regs:
    def __init__(self, blocks):
        self.adc = {1: Adc(blocks[1]), 2: Adc(blocks[2])}
        ipr = b''.join(struct.pack('<I', w) for w in blocks[3])
        self.ipr = {IRQ_TIM6: ipr[IRQ_TIM6 - 52], IRQ_HRTIM: ipr[IRQ_HRTIM - 52]}


class Bench:
    def __init__(self):
        self.rep_addr, size = th.symbol(ENV, 'g_tkd_report')
        if size != 4 * len(FIELDS):
            sys.exit(f'g_tkd_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
        self.cmd_addr, _ = th.symbol(ENV, 'g_tkd_cmd')
        self.args_addr, _ = th.symbol(ENV, 'g_tkd_args')
        self.seq = 0
        self.log = []

    def report(self):
        w = th.read_words(self.rep_addr, len(FIELDS))
        return dict(zip(FIELDS, w)) if w else None

    def wait_boot(self):
        t0 = time.monotonic()
        while time.monotonic() - t0 < BOOT_TIMEOUT_S:
            r = self.report()
            if r and r['magic'] == MAGIC and r['layout'] == LAYOUT and r['ack'] == 0 and r['busy'] == 0 \
                    and r['stamp_ms'] > 0:
                self.seq = 0
                return r
            time.sleep(0.3)
        return None

    def reset(self):
        th.openocd('reset run', check=False)
        time.sleep(0.5)
        return self.wait_boot()

    def step(self, name, *args, timeout=5.0):
        """Runs one step; @return (report, registers) after it. Raises on no acknowledgement or err."""
        self.seq += 1
        a = [int(x) & 0xFFFFFFFF for x in args] + [0] * (6 - len(args))
        pairs = [(self.args_addr + 4 * i, a[i]) for i in range(6)]
        pairs.append((self.cmd_addr, (self.seq << 8) | STEP[name]))
        th.write_words(pairs)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            time.sleep(0.05)
            blocks = th.read_blocks([(self.rep_addr, len(FIELDS)), (ADC_BASE[1], ADC_WORDS),
                                     (ADC_BASE[2], ADC_WORDS), (IPR_ADDR, IPR_WORDS)])
            if not blocks:
                continue
            rep = dict(zip(FIELDS, blocks[0]))
            if rep['ack'] == self.seq and rep['busy'] == 0:
                if rep['err']:
                    raise RuntimeError(f'step {name}{tuple(args)} refused by the firmware (err)')
                self.log.append((name, args, round(time.monotonic() - t0, 2)))
                return rep, Regs(blocks)
        raise TimeoutError(f'step {name}{tuple(args)} not acknowledged in {timeout} s')

    def ret(self, name, *args):
        rep, _ = self.step(name, *args)
        return [rep[f'ret{i}'] for i in range(N_RET)]

    def runs_rate(self, seconds):
        """Critical runs per second from the executor's stamps (DWT), over seconds; (rate, runs)."""
        a = self.report()
        time.sleep(seconds)
        b = self.report()
        if not a or not b:
            return None, 0
        runs = (b['stamp_runs'] - a['stamp_runs']) & 0xFFFFFFFF
        cyc = (b['stamp_cyc'] - a['stamp_cyc']) & 0xFFFFFFFF
        if not cyc:
            return None, runs
        return runs * b['core_hz'] / cyc, runs


def line(cid, ok, what, expected, measured):
    verdict = ok if isinstance(ok, str) else th.judge(bool(ok))
    th.line(cid, verdict, what, expected, measured)
    return verdict in ('PASS', 'INFO')


def fv(x):
    """A float return as text: NO_VALUE named."""
    return 'NO_VALUE' if x == NO_VALUE else f'{x:g}'


# ----------------------------------------------------------------------------------------------------------- PT


def pt(b):
    good = True
    b.step('PWM', 0)  # HRTIM at 200 kHz, unit A initialised, no output: the HRTIM source needs it (TaskAPI.h note)

    cid = 'L0-TKD-03'
    b.step('START_CRIT', 0)
    time.sleep(0.2)
    rep = b.report()
    good &= line(f'{cid}.a', rep['crit_runs'] == 0, 'startCritical before any createCritical does nothing',
                 'no critical run', f"{rep['crit_runs']} runs")

    cid = 'L0-TKD-02'
    rc = s32(b.ret('CREATE_CRIT', HRTIM, 100, FN_NULL)[0])
    good &= line(f'{cid}.a', rc == -1, 'createCritical(nullptr, 100 us, source_hrtim)', 'returns -1', rc)
    rc = s32(b.ret('CREATE_CRIT', HRTIM, 102, FN_COUNTER)[0])
    good &= line(f'{cid}.b', rc == -1, 'createCritical(102 us, source_hrtim): not a multiple of the 5 us PWM period',
                 'returns -1 (TaskAPI.h: an integer multiple of the HRTIM period)', rc)
    rc = s32(b.ret('CREATE_CRIT', HRTIM, 100, FN_COUNTER)[0])
    good &= line(f'{cid}.c', rc == 0, 'createCritical(100 us, source_hrtim)', 'returns 0', rc)

    cid = 'L0-TKD-03'
    started = b.ret('START_CRIT', 0)[0]
    rate, runs = b.runs_rate(1.0)
    ok = rate is not None and abs(rate - 1e4) <= PERIOD_TOL * 1e4
    good &= line(f'{cid}.b', ok, 'startCritical(false) runs the 100 us HRTIM task', f'10000 runs/s ±{PERIOD_TOL:.0%}',
                 'no runs' if rate is None else f'{rate:.0f} runs/s ({runs} runs)')
    good &= line(f'{cid}.c', started == 0, 'startCritical(false) leaves the DataAPI stopped', 'spin.data.started() false',
                 f'started() {bool(started)}')

    cid = 'L0-TKD-02'
    rc = s32(b.ret('CREATE_CRIT', HRTIM, 200, FN_COUNTER)[0])
    good &= line(f'{cid}.d', rc == -1, 'createCritical while the critical task runs', 'returns -1 (TaskAPI.h)', rc)

    cid = 'L0-TKD-05'
    ret = b.ret('MASK', 20)
    hr_masked, basepri = ret[0], ret[1]
    _, r = b.step('STARTED')
    line(f'{cid}.a', 'INFO', 'source_hrtim: critical runs during a 20 ms irq_lock(); interrupt priority',
         'recorded', f'{hr_masked} runs (200 if not masked); IPR {r.ipr[IRQ_HRTIM]:#04x}, irq_lock BASEPRI {basepri:#04x}')

    cid = 'L0-TKD-04'
    b.step('STOP_CRIT')
    time.sleep(0.05)
    a = b.report()
    time.sleep(0.3)
    c = b.report()
    good &= line(f'{cid}.a', a['crit_runs'] == c['crit_runs'], 'stopCritical stops the task',
                 'no critical run over 0.3 s', f"{c['crit_runs'] - a['crit_runs']} runs")
    b.step('START_CRIT', 0)
    rate, runs = b.runs_rate(0.5)
    ok = rate is not None and abs(rate - 1e4) <= PERIOD_TOL * 1e4
    good &= line(f'{cid}.b', ok, 'startCritical after stopCritical resumes the task at its period',
                 f'10000 runs/s ±{PERIOD_TOL:.0%}', 'no runs' if rate is None else f'{rate:.0f} runs/s')
    b.step('STOP_CRIT')

    cid = 'L0-TKD-02'
    rc = s32(b.ret('CREATE_CRIT', TIM6, 200, FN_COUNTER)[0])
    good &= line(f'{cid}.e', rc == 0, 'createCritical(200 us, source_tim6) after stopCritical redefines the task',
                 'returns 0 (TaskAPI.h: the previous task must have been suspended)', rc)
    b.step('START_CRIT', 0)
    rate, runs = b.runs_rate(1.0)
    ok = rate is not None and abs(rate - 5e3) <= PERIOD_TOL * 5e3
    good &= line(f'{cid}.f', ok, 'the redefined task runs at its own period and source', f'5000 runs/s ±{PERIOD_TOL:.0%}',
                 'no runs' if rate is None else f'{rate:.0f} runs/s ({runs} runs)')

    cid = 'L0-TKD-05'
    ret = b.ret('MASK', 20)
    tim_masked, basepri = ret[0], ret[1]
    _, r = b.step('STARTED')
    good &= line(f'{cid}.b', tim_masked >= 95, 'source_tim6: the task keeps running during a 20 ms irq_lock()',
                 '>= 95 of the 100 runs (zero-latency: above the kernel\'s masking)', f'{tim_masked} runs')
    ipr = r.ipr[IRQ_TIM6]
    good &= line(f'{cid}.c', basepri != 0 and ipr < basepri,
                 'source_tim6: interrupt priority above irq_lock()\'s BASEPRI (Zephyr IRQ_ZERO_LATENCY)',
                 'IPR(TIM6_DAC) < BASEPRI', f'IPR {ipr:#04x}, BASEPRI {basepri:#04x}')
    b.step('STOP_CRIT')

    # Background tasks: the executor is task 0.
    cid = 'L0-TKD-06'
    rcs = [s32(b.ret('CREATE_BG', k)[0]) for k in (1, 2, 1)]
    good &= line(f'{cid}.a', rcs[:2] == [1, 2], 'createBackground numbers the tasks after the executor (task 0)',
                 'returns 1, then 2', rcs[:2])
    good &= line(f'{cid}.b', rcs[2] == -1, f'createBackground past CONFIG_OWNTECH_TASK_MAX_ASYNCHRONOUS_TASKS ({MAX_BG})',
                 'returns -1 (TaskAPI.h)', rcs[2])

    cid = 'L0-TKD-07'
    b.step('BG_CONFIG', 1, 0, 10)
    b.step('BG_CONFIG', 2, 1, 500)
    time.sleep(0.2)
    rep = b.report()
    good &= line(f'{cid}.a', rep['bg1_loops'] == 0 and rep['bg2_loops'] == 0, 'a created task does not run before startBackground',
                 'no loop', f"task 1: {rep['bg1_loops']}, task 2: {rep['bg2_loops']}")
    b.step('START_BG', 1)
    b.step('START_BG', 2)
    time.sleep(0.3)
    b.step('BG_RESET')
    time.sleep(2.0)
    rep = b.report()
    good &= line(f'{cid}.b', rep['bg1_loops'] > 100 and rep['bg2_loops'] > 1000, 'startBackground runs the tasks',
                 'task 1 > 100 loops, task 2 > 1000 loops in 2.3 s', f"task 1: {rep['bg1_loops']}, task 2: {rep['bg2_loops']}")
    line(f'{cid}.c', 'INFO', 'background task thread priority', 'recorded',
         f"task 1: {s32(rep['bg1_priority'])}, task 2: {s32(rep['bg2_priority'])}")

    cid = 'L0-TKD-08'
    mean = rep['bg1_sum_us'] / rep['bg1_n'] if rep['bg1_n'] else None
    ok = mean is not None and 10000 <= mean <= 10000 + 2 * TICK_US
    good &= line(f'{cid}.a', ok, 'suspendBackgroundMs(10): mean loop period',
                 f'10.0 to {10 + 2 * TICK_US / 1000:.1f} ms (never shorter; +2 kernel ticks)',
                 'no loops' if mean is None else
                 f"{mean / 1000:.3f} ms (min {rep['bg1_min_us'] / 1000:.3f}, max {rep['bg1_max_us'] / 1000:.3f}, n {rep['bg1_n']})")

    cid = 'L0-TKD-09'
    for k, us in enumerate((500, 2500)):
        if k:
            b.step('BG_CONFIG', 2, 1, us)
            time.sleep(1.5)
            rep = b.report()
        mean = rep['bg2_sum_us'] / rep['bg2_n'] if rep['bg2_n'] else None
        ok = mean is not None and us <= mean <= us + 2 * TICK_US
        good &= line(f'{cid}.{"ab"[k]}', ok, f'suspendBackgroundUs({us}): mean loop period',
                     f'{us} to {us + 2 * TICK_US} us (never shorter; +2 kernel ticks of {TICK_US} us)',
                     'no loops' if mean is None else
                     f"{mean:.0f} us (min {rep['bg2_min_us']}, max {rep['bg2_max_us']}, n {rep['bg2_n']})")

    cid = 'L0-TKD-07'
    b.step('STOP_BG', 1)
    time.sleep(0.1)
    a = b.report()
    time.sleep(0.5)
    c = b.report()
    good &= line(f'{cid}.d', c['bg1_loops'] == a['bg1_loops'] and c['bg2_loops'] > a['bg2_loops'],
                 'stopBackground(1) stops task 1 only', 'task 1 frozen, task 2 running over 0.5 s',
                 f"task 1 +{c['bg1_loops'] - a['bg1_loops']}, task 2 +{c['bg2_loops'] - a['bg2_loops']}")
    b.step('START_BG', 1)
    time.sleep(0.5)
    d = b.report()
    good &= line(f'{cid}.e', d['bg1_loops'] > c['bg1_loops'] + 20, 'startBackground(1) after stopBackground resumes it',
                 '> 20 loops in 0.5 s', f"+{d['bg1_loops'] - c['bg1_loops']}")
    return good


# ----------------------------------------------------------------------------------------------------------- PD1


def pd1(b):
    good = True
    cid = 'L0-TKD-10'
    started = b.ret('STARTED')[0]
    good &= line(f'{cid}.a', started == 0, 'started() on a fresh boot', 'false', bool(started))
    peek = fl(b.ret('PEEK', PIN_DAC)[0])
    good &= line(f'{cid}.b', peek == NO_VALUE, 'peekLatestValue(34) before enable and start', 'NO_VALUE', fv(peek))
    ret = b.ret('LATEST', PIN_DAC, 1)
    good &= line(f'{cid}.c', fl(ret[0]) == NO_VALUE and ret[1] == DATA_IS_MISSING,
                 'getLatestValue(34, &dataValid) before enable and start', 'NO_VALUE, DATA_IS_MISSING',
                 f'{fv(fl(ret[0]))}, dataValid {ret[1]}')

    cid = 'L0-TKD-18'
    v = fl(b.ret('CONVERT', PIN_DAC, 1000)[0])
    good &= line(f'{cid}.d', v == DOC_ERROR, 'convertValue on a pin not enabled', 'returns -5000 (DataAPI.h)', fv(v))
    cid = 'L0-TKD-19'
    ret = b.ret('GET_PARAM', PIN_DAC)
    good &= line(f'{cid}.d', s32(ret[6]) == CONV_NO_CHANNEL, 'getConversionParameterType on a pin not enabled',
                 'returns -5 (DataAPI.h)', s32(ret[6]))
    good &= line(f'{cid}.e', fl(ret[0]) == DOC_ERROR, 'getConversionParameterValue(gain) on a pin not enabled',
                 'returns -5000 (DataAPI.h)', fv(fl(ret[0])))

    for pin, adc in ((PIN_DAC, ADC_2), (PIN_FLOAT, ADC_2), (PIN_LIN, 0), (PIN_NTC, 0)):
        rc = s32(b.ret('ENABLE', pin, adc)[0])
        if rc != 0:
            raise RuntimeError(f'enableAcquisition({pin}, {adc}) returned {rc} (L0-PAD covers the map)')

    cid = 'L0-TKD-18'
    v = fl(b.ret('CONVERT', PIN_DAC, 1000)[0])
    good &= line(f'{cid}.e', v in (1000.0, DOC_ERROR),
                 'convertValue(34, 1000) after enable, before start, no parameters set (open question)',
                 '1000 (default gain 1, offset 0) or -5000 (error); never a read through a null pointer', fv(v))

    b.step('TRIG_SRC', ADC_2, SOFTWARE)
    b.step('SET_LIN', PIN_FLOAT, f32(LIN35[0]), f32(LIN35[1]))
    cid = 'L0-TKD-19'
    ret = b.ret('GET_PARAM', PIN_FLOAT)
    g, o, t = fl(ret[0]), fl(ret[1]), s32(ret[6])
    good &= line(f'{cid}.a', (g, o) == LIN35, 'setConversionParametersLinear(35, 0.5, 10) then getConversionParameterValue',
                 'gain 0.5, offset 10', f'gain {g:g}, offset {o:g}')
    good &= line(f'{cid}.b', t == CONV_LINEAR, 'getConversionParameterType after setConversionParametersLinear',
                 'conversion_linear (0)', t)

    # NVS: entries for pins 52 (linear) and 51 (NTC), retrieved after the reset in PD2.
    cid = 'L0-TKD-21'
    b.step('SET_LIN', PIN_LIN, f32(LIN52[0]), f32(LIN52[1]))
    rc = s32(b.ret('STORE', PIN_LIN)[0])
    good &= line(f'{cid}.a', rc == 0, 'storeConversionParametersInMemory(52), linear 0.25 / 3', 'returns 0', rc)
    b.step('SET_NTC', PIN_NTC, *[f32(x) for x in NTC51])
    rc = s32(b.ret('STORE', PIN_NTC)[0])
    good &= line(f'{cid}.b', rc == 0, 'storeConversionParametersInMemory(51), NTC', 'returns 0', rc)
    rc = s32(b.ret('STORE', PIN_NONE)[0])
    good &= line(f'{cid}.c', rc == DOC_ERROR, 'storeConversionParametersInMemory on a pin with no ADC',
                 'returns -5000 (DataAPI.h: pin not found)', rc)
    b.step('SET_LIN', PIN_LIN, f32(1.0), f32(0.0))  # changed in RAM: PD2 must get 0.25 / 3 back from NVS

    cid = 'L0-TKD-20'
    ret = b.ret('GET_PARAM', PIN_NTC)
    t = s32(ret[6])
    got = [fl(ret[k]) for k in (2, 3, 4, 5)]
    good &= line(f'{cid}.a', t == CONV_THERM, 'getConversionParameterType after setConversionParametersNtcThermistor',
                 'conversion_therm (1)', t)
    ok = all(abs(x - y) <= 1e-3 * abs(y) for x, y in zip(got, NTC51))
    good &= line(f'{cid}.b', ok, 'getConversionParameterValue(r0, b, rdiv, t0) after setConversionParametersNtcThermistor',
                 'r0 10000, b 3950, rdiv 10000, t0 298.15', ', '.join(f'{x:g}' for x in got))

    b.step('DAC', 1000)
    cid = 'L0-TKD-11'
    rc = s32(b.ret('START')[0])
    good &= line(f'{cid}.a', rc == 0, 'start()', 'returns 0', rc)
    rep, r = b.step('STARTED')
    good &= line(f'{cid}.b', rep['ret0'] == 1, 'started() after start()', 'true', bool(rep['ret0']))
    rc = s32(b.ret('START')[0])
    good &= line(f'{cid}.c', rc == -1, 'start() a second time', 'returns -1 (DataAPI.h)', rc)
    rc = s32(b.ret('ENABLE', PIN_LATE, ADC_2)[0])
    good &= line(f'{cid}.d', rc == -1, 'enableAcquisition(32) after start()', 'returns -1 (DataAPI.h: before start)', rc)

    cid = 'L0-TKD-12'
    a2, a1 = r.adc[2], r.adc[1]
    good &= line(f'{cid}.a', a2.exten == 0, 'configureTriggerSource(ADC_2, TRIG_SOFTWARE)', 'ADC2 CFGR EXTEN 0 (software)',
                 str(a2))
    good &= line(f'{cid}.b', a1.exten == 1 and a1.extsel == EXTSEL_HRTIM_TRG[1],
                 'ADC_1 left at its default trigger source', 'TRIG_PWM (DataAPI.h): EXTEN 1 (rising), EXTSEL 21 (HRTIM_ADC_TRG1)',
                 str(a1))
    cid = 'L0-TKD-13'
    good &= line(f'{cid}.a', a2.discen == 0, 'discontinuous mode by default', 'ADC2 CFGR DISCEN 0 (DataAPI.h)', str(a2))
    good &= line(f'{cid}.b', a2.cfgr & 1 == 1, 'DMA on for the enabled ADC', 'ADC2 CFGR DMAEN 1', str(a2))

    cid = 'L0-TKD-14'
    peek = fl(b.ret('PEEK', PIN_DAC)[0])
    good &= line(f'{cid}.a', peek == NO_VALUE, 'peekLatestValue(34) after start, before any trigger', 'NO_VALUE', fv(peek))
    b.step('TRIGGER', ADC_2, 1)
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.b', ret[0] == 1 and abs(ret[1] - 1000) <= DAC_TOL,
                 'triggerAcquisition(ADC_2) once, then getRawValues(34)', f'1 value, 1000 ±{DAC_TOL} (DAC2 level)',
                 f'{ret[0]} value(s), {ret[1]}')
    ret = b.ret('RAW', PIN_FLOAT)
    good &= line(f'{cid}.c', ret[0] == 1, 'getRawValues(35): the other channel of the sequence', '1 value', f'{ret[0]} value(s)')
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.d', ret[0] == 0, 'getRawValues(34) again, no trigger since', '0 values (consumed)',
                 f'{ret[0]} value(s)')
    b.step('TRIGGER', ADC_2, 10)
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.e', ret[0] == 10 and abs(ret[3] - 1000) <= DAC_TOL and abs(ret[4] - 1000) <= DAC_TOL,
                 'triggerAcquisition(ADC_2) x10, then getRawValues(34)', f'10 values, each 1000 ±{DAC_TOL}',
                 f'{ret[0]} values, min {ret[3]}, max {ret[4]}')
    b.step('DAC', 3000)
    time.sleep(0.05)
    b.step('TRIGGER', ADC_2, 3)
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.f', ret[0] == 3 and abs(ret[2] - 3000) <= DAC_TOL, 'the values follow the input (DAC2 to 3000)',
                 f'3 values, last 3000 ±{DAC_TOL}', f'{ret[0]} values, last {ret[2]}')
    b.ret('RAW', PIN_FLOAT)  # empty pin 35's buffer

    cid = 'L0-TKD-15'
    b.step('TRIGGER', ADC_2, 3)
    p1 = fl(b.ret('PEEK', PIN_DAC)[0])
    p2 = fl(b.ret('PEEK', PIN_DAC)[0])
    good &= line(f'{cid}.a', abs(p1 - 3000) <= DAC_TOL and p1 == p2, 'peekLatestValue(34) twice after 3 triggers',
                 f'3000 ±{DAC_TOL} (default gain 1), the same value twice', f'{fv(p1)}, {fv(p2)}')
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.b', ret[0] == 3, 'getRawValues(34) after the peeks', '3 values (peek consumed nothing)',
                 f'{ret[0]} value(s)')
    peek = fl(b.ret('PEEK', PIN_NONE)[0])
    good &= line(f'{cid}.c', peek == NO_VALUE, 'peekLatestValue on a pin with no ADC', 'NO_VALUE', fv(peek))

    cid = 'L0-TKD-16'
    b.ret('RAW', PIN_FLOAT)
    b.step('TRIGGER', ADC_2, 1)
    ret = b.ret('LATEST', PIN_FLOAT, 1)
    v1, dv1 = fl(ret[0]), ret[1]
    raw = (v1 - LIN35[1]) / LIN35[0]
    good &= line(f'{cid}.a', dv1 == DATA_IS_OK and raw == int(raw) and 0 <= raw <= 4095,
                 'getLatestValue(35, &dataValid) after a trigger', 'DATA_IS_OK, value = 0.5 x raw + 10 (integer raw)',
                 f'{fv(v1)} (raw {raw:g}), dataValid {dv1}')
    ret = b.ret('LATEST', PIN_FLOAT, 1)
    good &= line(f'{cid}.b', ret[1] == DATA_IS_OLD and fl(ret[0]) == v1, 'getLatestValue(35, &dataValid) again, no trigger since',
                 'the same value, DATA_IS_OLD', f'{fv(fl(ret[0]))}, dataValid {ret[1]}')
    b.step('TRIGGER', ADC_2, 1)
    ret = b.ret('LATEST', PIN_DAC, 0)
    good &= line(f'{cid}.c', abs(fl(ret[0]) - 3000) <= DAC_TOL, 'getLatestValue(34) without dataValid',
                 f'3000 ±{DAC_TOL}', fv(fl(ret[0])))
    ret = b.ret('LATEST', PIN_NONE, 1)
    good &= line(f'{cid}.d', fl(ret[0]) == NO_VALUE and ret[1] == DATA_IS_MISSING, 'getLatestValue on a pin with no ADC',
                 'NO_VALUE, DATA_IS_MISSING', f'{fv(fl(ret[0]))}, dataValid {ret[1]}')

    cid = 'L0-TKD-17'
    b.ret('RAW', PIN_FLOAT)
    b.ret('RAW', PIN_DAC)
    b.step('TRIGGER', ADC_2, 5)
    ret = b.ret('VALUES', PIN_FLOAT)
    good &= line(f'{cid}.a', ret[0] == 5 and ret[3] == 0 and ret[4] == 1, 'getValues(35) after 5 triggers (gain 0.5, offset 10)',
                 '5 values, each 0.5 x raw + 10 for an integer raw in 0..4095',
                 f'{ret[0]} values, {ret[3]} not of that form, first {fl(ret[1]):g}, last {fl(ret[2]):g}')
    ret = b.ret('VALUES', PIN_DAC)
    good &= line(f'{cid}.b', ret[0] == 5 and ret[3] == 0 and abs(fl(ret[2]) - 3000) <= DAC_TOL,
                 'getValues(34) (default gain 1, offset 0)', f'5 values, integer, last 3000 ±{DAC_TOL}',
                 f'{ret[0]} values, {ret[3]} not integer, last {fl(ret[2]):g}')
    ret = b.ret('VALUES', PIN_DAC)
    good &= line(f'{cid}.c', ret[0] == 0, 'getValues(34) again, no trigger since', '0 values', f'{ret[0]} values')

    cid = 'L0-TKD-18'
    v = fl(b.ret('CONVERT', PIN_FLOAT, 1000)[0])
    good &= line(f'{cid}.a', v == 510.0, 'convertValue(35, 1000), linear 0.5 / 10', '510 exactly', fv(v))
    exp = ntc_celsius(2048, *NTC51)
    v = fl(b.ret('CONVERT', PIN_NTC, 2048)[0])
    good &= line(f'{cid}.b', abs(v - exp) <= 0.05, 'convertValue(51, 2048), NTC r0 10k, B 3950, rdiv 10k, T0 298.15 K',
                 f'{exp:.2f} °C ±0.05 (Beta model, VREF 2.048 V, divider on 3.3 V)', f'{v:.3f}')
    v = fl(b.ret('CONVERT', PIN_NONE, 1000)[0])
    good &= line(f'{cid}.c', v == DOC_ERROR, 'convertValue on a pin with no ADC', 'returns -5000 (DataAPI.h)', fv(v))

    cid = 'L0-TKD-22'
    rc = s32(b.ret('STOP')[0])
    good &= line(f'{cid}.a', rc == 0, 'stop()', 'returns 0', rc)
    started = b.ret('STARTED')[0]
    good &= line(f'{cid}.b', started == 0, 'started() after stop()', 'false', bool(started))
    rc = s32(b.ret('STOP')[0])
    good &= line(f'{cid}.c', rc == -1, 'stop() a second time', 'returns -1 (DataAPI.h)', rc)
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.d', ret[0] == 0, 'getRawValues(34) after stop()', '0 values', f'{ret[0]} value(s)')
    rc = s32(b.ret('START')[0])
    good &= line(f'{cid}.e', rc == 0, 'start() again after stop()', 'returns 0', rc)
    b.step('TRIGGER', ADC_2, 2)
    ret = b.ret('RAW', PIN_DAC)
    good &= line(f'{cid}.f', ret[0] == 2 and abs(ret[2] - 3000) <= DAC_TOL, 'samples after the restart (2 triggers)',
                 f'2 values, 3000 ±{DAC_TOL}', f'{ret[0]} value(s), last {ret[2]}')

    cid = 'L0-TKD-23'
    ret = b.ret('HEAP_PROBE', 100)
    good &= line(f'{cid}.a', ret[0] == ret[1], 'heap bytes allocated around 100 rounds of the hot-path calls',
                 'unchanged (triggerAcquisition, getRawValues, getValues, peekLatestValue, getLatestValue, convertValue)',
                 f'{ret[0]} -> {ret[1]} bytes over {ret[2]} calls')
    return good


# ----------------------------------------------------------------------------------------------------------- PD2


def pd2(b):
    good = True
    cid = 'L0-TKD-21'
    for pin in (PIN_LIN, PIN_NTC):
        b.ret('ENABLE', pin, 0)
    rc = s32(b.ret('RETRIEVE', PIN_LIN)[0])
    ret = b.ret('GET_PARAM', PIN_LIN)
    g, o, t = fl(ret[0]), fl(ret[1]), s32(ret[6])
    good &= line(f'{cid}.d', rc == 0 and (g, o) == LIN52 and t == CONV_LINEAR,
                 'retrieveConversionParametersFromMemory(52) after a reset', 'returns 0; linear, gain 0.25, offset 3',
                 f'returns {rc}; type {t}, gain {g:g}, offset {o:g}')
    rc = s32(b.ret('RETRIEVE', PIN_NTC)[0])
    ret = b.ret('GET_PARAM', PIN_NTC)
    t = s32(ret[6])
    v = fl(b.ret('CONVERT', PIN_NTC, 2048)[0])
    exp = ntc_celsius(2048, *NTC51)
    good &= line(f'{cid}.e', rc == 0 and t == CONV_THERM and abs(v - exp) <= 0.05,
                 'retrieveConversionParametersFromMemory(51) after a reset (NTC)',
                 f'returns 0; conversion_therm, convertValue(51, 2048) {exp:.2f} °C',
                 f'returns {rc}; type {t}, convertValue {v:.3f}')
    # Clean-up: the CUT's NVS as it was (ADC1 IN4 and IN3), then check the entries are gone.
    b.ret('NVS_DELETE', ADC_1, 4)
    b.ret('NVS_DELETE', ADC_1, 3)
    rc = s32(b.ret('RETRIEVE', PIN_LIN)[0])
    line(f'{cid}.f', 'INFO', 'retrieve after the clean-up deleted the entries', 'recorded (-4: nothing for this pin)', rc)

    b.step('PWM', 1)  # 200 kHz, PWMA started, ADC trigger on ADC2 every period (as L0-ADD)
    b.step('TRIG_SRC', ADC_1, SOFTWARE)
    b.step('TRIG_SRC', ADC_2, PWM)
    b.step('DISC', ADC_2, 1)
    for pin in (PIN_DAC, PIN_FLOAT):
        b.ret('ENABLE', pin, ADC_2)
    b.step('SET_LIN', PIN_FLOAT, f32(LIN35[0]), f32(LIN35[1]))
    b.step('DAC', 2000)

    cid = 'L0-TKD-24'
    rc = s32(b.ret('CREATE_CRIT', TIM6, 100, FN_DATA)[0])
    good &= line(f'{cid}.a', rc == 0, 'createCritical(data task, 100 us, source_tim6)', 'returns 0', rc)
    rep, r = b.step('START_CRIT', 1)
    good &= line(f'{cid}.b', rep['ret0'] == 1, 'startCritical(true) starts the DataAPI', 'spin.data.started() true (TaskAPI.h)',
                 bool(rep['ret0']))
    a2 = r.adc[2]
    cid = 'L0-TKD-12'
    good &= line(f'{cid}.c', a2.exten == 1 and a2.extsel == EXTSEL_HRTIM_TRG[3], 'configureTriggerSource(ADC_2, TRIG_PWM)',
                 'ADC2 CFGR EXTEN 1 (rising), EXTSEL 22 (HRTIM_ADC_TRG3)', str(a2))
    cid = 'L0-TKD-13'
    good &= line(f'{cid}.c', a2.discen == 1 and a2.discnum == 0, 'configureDiscontinuousMode(ADC_2, 1)',
                 'ADC2 CFGR DISCEN 1, DISCNUM 0 (one rank per trigger)', str(a2))

    cid = 'L0-TKD-24'
    time.sleep(0.5)
    a = b.ret('CRIT_DATA')
    t0 = time.monotonic()
    time.sleep(2.0)
    c = b.ret('CRIT_DATA')
    dt = time.monotonic() - t0
    runs = c[7] - a[7]
    alive = b.report()
    ok = runs >= 0.9 * dt * 1e4 and alive is not None
    good &= line(f'{cid}.c', ok, 'the TIM6 task keeps running with the hot-path calls, the board alive',
                 f'>= 90 % of 10000 runs/s over {dt:.1f} s, executor answering', f'{runs} runs')
    nv, nr, dv = c[0] - a[0], c[1] - a[1], c[3] - a[3]
    # 100 us = 20 PWM periods, ADC2 alternates two channels: 10 samples per channel per run.
    exp = runs * 10
    good &= line(f'{cid}.d', abs(nv - exp) <= 0.02 * exp and abs(nr - exp) <= 0.02 * exp,
                 'getValues(35) and getRawValues(34) in the task: every sample', f'{exp} each (10 per run) ±2 %',
                 f'getValues {nv}, getRawValues {nr}')
    last = fl(c[6])
    rawl = (last - LIN35[1]) / LIN35[0]
    good &= line(f'{cid}.e', fl(c[5]) == 510.0 and rawl == int(rawl) and 0 <= rawl <= 4095,
                 'convertValue and getValues in the task', 'convertValue(35, 1000) = 510; getValues = 0.5 raw + 10',
                 f'convertValue {fl(c[5]):g}; last value {last:g}')
    good &= line(f'{cid}.f', abs(fl(c[4]) - 2000) <= DAC_TOL and abs(fl(c[2]) - 2000) <= DAC_TOL,
                 'peekLatestValue(34) and getLatestValue(34) in the task', f'2000 ±{DAC_TOL} (DAC2 level)',
                 f'peek {fv(fl(c[4]))}, latest {fv(fl(c[2]))}, DATA_IS_OK in {dv} of {runs} runs')
    b.step('STOP_CRIT')
    return good


# ----------------------------------------------------------------------------------------------------------- main


def blocked(reason, only=None):
    for cid, what in CASES:
        if only is None or cid in only:
            th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def run_profile(name, fn, b, first_case, fresh=True):
    t0 = time.monotonic()
    if fresh and b.reset() is None:
        th.line(first_case, 'FAIL', f'{name}: fresh boot', 'report reachable', 'firmware did not boot after the reset')
        return False
    try:
        ok = fn(b)
    except Exception as e:  # noqa: BLE001 - a step that never returns must not hide the remaining profiles
        th.line(first_case, 'FAIL', f'{name} stopped', 'every step acknowledged', f'{type(e).__name__}: {e}')
        ok = False
    print(f'== {name}: {time.monotonic() - t0:.0f} s, {len(b.log)} steps so far', flush=True)
    return ok


def main():
    t_start = time.monotonic()
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_tkd_')
    flash_log = os.path.join(out, 'target_task_data_flash.log')
    rc = th.flash(ENV, flash_log)
    if rc != 0:
        th.line('L0-TKD-01', 'FAIL', 'env TKD builds and flashes over the ST-Link', 'pio upload exit 0',
                f'exit {rc}, see {os.path.basename(flash_log)}')
        blocked('flash failed', [c for c, _ in CASES[1:]])
        return 1
    b = Bench()
    boot = b.wait_boot()
    good = line('L0-TKD-01', boot is not None, 'env TKD builds, flashes and answers over SWD',
                f'pio upload exit 0, magic {MAGIC:#010x}', 'no report' if boot is None else
                f"magic {boot['magic']:#010x}, boot {boot['boot_count']}")
    if boot is None:
        return 1
    good &= run_profile('PT task', pt, b, 'L0-TKD-02', fresh=False)
    good &= run_profile('PD1 data', pd1, b, 'L0-TKD-10')
    good &= run_profile('PD2 data after reset', pd2, b, 'L0-TKD-21')
    b.reset()  # leave the CUT with no task and no output running
    line('L0-TKD-25', 'INFO', 'module duration (flash, three profiles)', 'recorded',
         f'{time.monotonic() - t_start:.0f} s, {len(b.log)} steps')
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
