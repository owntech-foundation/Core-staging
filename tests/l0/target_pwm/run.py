#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-PWM: the PWM module of the API regression suite (Core-staging#38; tiers and depth: #35). Every PwmHAL
##        call at effect depth (HRTIM registers read over SWD, return values, output pins sampled on the chip);
##        outputs, frequency, phase and switch convention at signal depth on the MHO984 through the SPOC MUX; latency
##        to effect on PA6 (marker) and PA8, which the MUX puts on CH1 and CH4, two independent nodes (#5).
##
## env PWM (tests/firmware/pwm) is a step executor: the host writes a step and its arguments over SWD and the
## firmware calls spin.pwm, samples the twelve HRTIM pins, and acknowledges. Expected values come from RM0440 and
## the docs (period = 32 f_HRTIM / f at CKPSC 0, 184 ps, 735 ps dead-time step, compare >= 0x60 at CKPSC 0), never
## from the driver's own arithmetic. Profiles, each on a fresh boot (the pre-init calls must come before initUnit):
##  P1 left-aligned 200 kHz: initUnit defaults, getters, duty, outputs, latency, setFrequency, phase shift, ADC
##     trigger, burst mode, period event, then the PWMA phase-shift question (#38) and the period event on TIMA.
##  P2 up-down, PWMx2, dead time, ADC edge, current mode, and the low-duty sweep near 0 and 1 (Core#167, #8).
##  P3 initFixedFrequency.  P4 initVariableFrequency and setFrequency within and below its range.
## Supervised rows (L0-PWM-20, -21: hand-held probes until #16) run only with tests/run_all.sh --supervised.
## Core#172 (up-down re-init) is L0-HRI (tests/l0/target_hrtim_reinit), part of this module's campaign.
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

ENV = 'PWM'
MAGIC = 0x50574D31  # "PWM1", tests/firmware/pwm/main.cpp
LAYOUT = 1
FIELDS = (['magic', 'layout', 'boot_count', 'ack', 'busy', 'step', 'err', 'cycles'] + [f'ret{i}' for i in range(16)]
          + [f'high{i}' for i in range(12)] + ['samples', 'cb_count', 'stamp_ms'])
STEP = dict(SET_FREQUENCY=1, INIT_UNIT=2, GETTERS=3, DUTY=4, DUTY_RAW=5, START_DUAL=6, STOP_DUAL=7, START_SINGLE=8,
            STOP_SINGLE=9, PHASE=10, MODULATION=11, CONVENTION=12, DEAD_TIME=13, MODE=14, EEV=15, ADC_EDGE=16,
            INIT_FIXED=17, INIT_VARIABLE=18, ADC_TRIGGER=20, ADC_DECIMATION=21, ADC_POSTSCALER=22, ADC_ENABLE=23,
            ADC_DISABLE=24, ADC_INSTANT=25, PE_CONFIGURE=30, PE_ENABLE=31, PE_DISABLE=32, PE_SET_REP=33, BM_INIT=40,
            BM_SET=41, BM_START=42, BM_STOP=43, BM_DEINIT=44, SAMPLE=50, LAT_DUTY=60, LAT_STOP=61, LAT_START=62)
PINS = ['PA8', 'PA9', 'PA10', 'PA11', 'PB12', 'PB13', 'PB14', 'PB15', 'PC8', 'PC9', 'PC6', 'PC7']
A, B, C, D = 0, 1, 2, 3
MASTER = 6
UNIT_PINS = {A: ('PA8', 'PA9'), B: ('PA10', 'PA11'), C: ('PB12', 'PB13'), D: ('PB14', 'PB15')}

# RM0440: f_HRCK = 32 x f_HRTIM (170 MHz) at CKPSC 0; dead-time step t_DTG = t_HRTIM / 8 at DTPRSC 0.
F_HRTIM = 170e6
PER_200K = 27200            # docs/R10: 200 kHz is 27200 ticks of 184 ps
DT_STEP_NS = 1e3 / (8 * 170)
CMP_MIN = {0: 0x60, 1: 0x30}  # RM0440 minimum compare (and period) per CKPSC
PER_MAX = {0: 0xFFDF, 1: 0xFFEF}
RES_PS = {0: 184, 1: 368}   # PwmHAL.h getResolutionPs table

HRTIM = 0x40016800
N_HR = 0x410 // 4
M_OFF = dict(MCR=0x00, MDIER=0x0C, MPER=0x14, MREP=0x18, MCMP1=0x1C, MCMP2=0x24, MCMP3=0x28, MCMP4=0x2C)
T_OFF = dict(CR=0x00, DIER=0x0C, PER=0x14, REP=0x18, CMP1=0x1C, CMP2=0x24, CMP3=0x28, CMP4=0x2C, DT=0x38,
             SET1=0x3C, RST1=0x40, SET2=0x44, RST2=0x48, EEF1=0x4C, RST=0x54, OUT=0x64, CR2=0x6C)
C_OFF = dict(CR2=0x04, OENR=0x14, ODSR=0x1C, BMCR=0x20, BMCMPR=0x28, BMPER=0x2C, EECR1=0x30, ADC1R=0x3C,
             ADC3R=0x44, ADCUR=0x7C, ADCPS1=0x80)

BOOT_TIMEOUT_S = 30.0
DUTY_TOL = 0.03      # on-chip pin fraction (4096 random samples) against the expected high time
SCOPE_F_TOL = 0.005  # frequency through the MUX
SCOPE_D_TOL = 0.02   # duty through the MUX (edges ~20-35 ns, #5)
PHASE_TOL_DEG = 2.0  # 28 ns at 200 kHz: MUX skew ~4 ns (L0-CAL), 10 ns screen samples
LOW_DUTIES = [0.0, 0.0001, 0.0005, 0.001, 0.0015, 0.002, 0.0024, 0.0025, 0.005]
HIGH_DUTIES = [0.9976, 0.999, 0.9999, 1.0]
CUT_MARKER = 'PA6'

CASES = [
    ('L0-PWM-01', 'env PWM builds, flashes and answers its steps'),
    ('L0-PWM-02', 'setFrequency + initUnit: HRTIM timer registers at their defaults'),
    ('L0-PWM-03', 'getters after initUnit return the unit\'s state'),
    ('L0-PWM-04', 'setDutyCycle / setDutyCycleRaw write CMP1'),
    ('L0-PWM-05', 'startDualOutput: outputs enabled, complementary pair at the duty on the pins'),
    ('L0-PWM-06', 'latency to effect: call -> pin (PA6 marker, PA8)'),
    ('L0-PWM-07', 'stopDualOutput, startSingleOutput, stopSingleOutput'),
    ('L0-PWM-08', 'setFrequency after initUnit: period, duty kept, frequency on the pin'),
    ('L0-PWM-09', 'setPhaseShift on PWMB and PWMC: reset source, compare, phase on the pins'),
    ('L0-PWM-10', 'setPhaseShift(PWMA, ...) leaves timer A untouched (Core-staging#61)'),
    ('L0-PWM-11', 'ADC trigger calls: routing, postscaler, instant, enable/disable'),
    ('L0-PWM-12', 'period event: repetition, callback rate, disable'),
    ('L0-PWM-13', 'burst mode: init, set, start, stop, deinit'),
    ('L0-PWM-14', 'pre-init calls: up-down, PWMx2, dead time, ADC edge'),
    ('L0-PWM-15', 'duty near 0 does not invert the outputs (Core#166/#167)'),
    ('L0-PWM-16', 'duty near 1 does not invert the outputs (Core#166/#167)'),
    ('L0-PWM-17', 'setMode / setEev: current mode'),
    ('L0-PWM-18', 'initFixedFrequency'),
    ('L0-PWM-19', 'initVariableFrequency, setFrequency within and below its range'),
    ('L0-PWM-20', 'SUPERVISED: dead time on direct probes'),
    ('L0-PWM-21', 'SUPERVISED: low-duty sweep on direct probes'),
]
SUPERVISED = {'L0-PWM-20', 'L0-PWM-21'}


def f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def s32(u):
    return u - (1 << 32) if u >= 1 << 31 else u


class Regs:
    """HRTIM1 register block read over SWD."""

    def __init__(self, words):
        self.w = words

    def m(self, name):
        return self.w[M_OFF[name] // 4]

    def t(self, u, name):
        return self.w[(0x80 * (u + 1) + T_OFF[name]) // 4]

    def c(self, name):
        return self.w[(0x380 + C_OFF[name]) // 4]

    def ckpsc(self, u):
        return self.t(u, 'CR') & 7

    def oen(self, u):
        """(output 1, output 2) enabled, from OENR (read: output enable status)."""
        v = self.c('OENR')
        return bool(v >> (2 * u) & 1), bool(v >> (2 * u + 1) & 1)

    def dead_time_ns(self, u):
        dt = self.t(u, 'DT')
        psc = 1 << (dt >> 10 & 7)
        rise = (dt & 0x1FF) * psc * DT_STEP_NS * (-1 if dt >> 9 & 1 else 1)
        fall = (dt >> 16 & 0x1FF) * psc * DT_STEP_NS * (-1 if dt >> 25 & 1 else 1)
        return rise, fall, dt >> 10 & 7


class Bench:
    def __init__(self):
        self.rep_addr, size = th.symbol(ENV, 'g_pwm_report')
        if size != 4 * len(FIELDS):
            sys.exit(f'g_pwm_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
        self.cmd_addr, _ = th.symbol(ENV, 'g_pwm_cmd')
        self.args_addr, _ = th.symbol(ENV, 'g_pwm_args')
        self.seq = 0
        self.log = []

    def report(self):
        w = th.read_words(self.rep_addr, len(FIELDS))
        return dict(zip(FIELDS, w)) if w else None

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

    def step(self, name, *args, timeout=5.0):
        """Runs one step; @return the report and the HRTIM registers after it, or (None, None) on no ack."""
        self.seq += 1
        a = list(args) + [0] * (4 - len(args))
        pairs = [(self.args_addr + 4 * i, a[i]) for i in range(4)]
        pairs.append((self.cmd_addr, (self.seq << 8) | STEP[name]))
        th.write_words(pairs)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            time.sleep(0.05)
            blocks = th.read_blocks([(self.rep_addr, len(FIELDS)), (HRTIM, N_HR)])
            if not blocks:
                continue
            rep = dict(zip(FIELDS, blocks[0]))
            if rep['ack'] == self.seq and rep['busy'] == 0:
                if rep['err']:
                    raise RuntimeError(f'step {name}{tuple(args)} refused by the firmware (err)')
                self.log.append((name, args, round(time.monotonic() - t0, 2)))
                return rep, Regs(blocks[1])
        raise TimeoutError(f'step {name}{tuple(args)} not acknowledged in {timeout} s')


def frac(rep, pin):
    return rep[f'high{PINS.index(pin)}'] / max(1, rep['samples'])


def line(cid, ok, what, expected, measured):
    verdict = ok if isinstance(ok, str) else th.judge(bool(ok))
    th.line(cid, verdict, what, expected, measured)
    return verdict in ('PASS', 'INFO')


def near(x, target, tol):
    return x is not None and abs(x - target) <= tol


def dt_ok(value_ns, target_ns):
    return abs(value_ns - target_ns) <= DT_STEP_NS + 1e-9


class Scope:
    """MHO984 through the SPOC MUX, CH1 and CH4 only: TP1-TP3 ties CH1 to CH2 and TP5 loads CH3, so CH1 and CH4 are
    the two independent nodes (#5)."""

    def __init__(self, scope, mux):
        self.s, self.mux, self.route = scope, mux, None
        for ch in (1, 4):
            scope.setup_channel(ch, scale=1.0, offset=-1.65, probe=10)
        scope.set_acquire('NORMal')

    def to(self, pin1, pin4=None):
        if (pin1, pin4) == self.route:
            return
        if pin4 is None:
            self.mux.route('ch1', pin1)
        else:
            self.mux.route_pair('ch1', pin1, 'ch4', pin4)
        self.route = (pin1, pin4)
        time.sleep(0.3)

    def capture(self, pin1, pin4=None, f=200e3, periods=5):
        """One stopped capture of ~periods PWM periods; @return {1: (t, v), 4: (t, v)}."""
        self.to(pin1, pin4)
        self.s.set_timebase(periods / f / 10)
        self.s.set_edge_trigger(1, 1.65, sweep='AUTO')
        # rigol_mho.single() waits ~0.15 s at us timebases: after a step or a route change the scope still shows
        # the previous record (seen on the bench). Run long enough for a fresh acquisition, then stop.
        self.s.write(':RUN')
        time.sleep(0.8)
        self.s.write(':STOP')
        time.sleep(0.1)
        out = {1: self.s.waveform(1)}
        if pin4 is not None:
            out[4] = self.s.waveform(4)
        return out

    def triggered(self, pin1, pin4, scale, offset, action):
        """Arms a NORMal trigger on CH1's rising edge, runs action() (which makes the edge), stops; waveforms."""
        self.to(pin1, pin4)
        self.s.set_timebase(scale, offset)
        self.s.set_edge_trigger(1, 1.65, sweep='NORMal')
        self.s.run()
        time.sleep(0.4)
        action()
        time.sleep(0.3)
        self.s.write(':STOP')
        time.sleep(0.1)
        return {1: self.s.waveform(1), 4: self.s.waveform(4)}


def sig(t, v):
    """(frequency, duty, switching, max volts) of one waveform."""
    switching = wf.is_switching(v, 1.0)
    return (wf.frequency(t, v) if switching else None, wf.duty(t, v) if switching else None, switching, max(v))


def fmt_sig(s):
    f, d, sw, vmax = s
    if not sw:
        return f'flat, max {vmax:.2f} V'
    return f'{f / 1e3:.2f} kHz, duty {d:.3f}' if f and d is not None else 'switching, no full period'


def judge_sig(cid, what, s, f, d):
    ok = s[2] and near(s[0], f, SCOPE_F_TOL * f) and near(s[1], d, SCOPE_D_TOL)
    return line(cid, ok, what, f'{f / 1e3:.0f} kHz ±{SCOPE_F_TOL:.1%}, duty {d:.3f} ±{SCOPE_D_TOL}', fmt_sig(s))


def judge_flat(cid, what, s):
    return line(cid, not s[2] and s[3] < 0.5, what, 'flat low (max < 0.5 V)', fmt_sig(s))


def phase(cap):
    t, ref = cap[1]
    _, other = cap[4]
    return wf.phase_deg(t, ref, other)


def phase_err(p, target):
    if p is None:
        return None
    return (p - target + 180) % 360 - 180


# ----------------------------------------------------------------------------------------------------------- P1


def p1(b, sc):
    good = True
    rep, r = b.step('SET_FREQUENCY', 200000)
    rep, r = b.step('INIT_UNIT', A)
    rep, r = b.step('GETTERS', A)
    cid = 'L0-PWM-02'
    good &= line(f'{cid}.a', r.m('MPER') == PER_200K and r.t(A, 'PER') == PER_200K,
                 'master and timer A period at 200 kHz', f'MPER = PERAR = {PER_200K}',
                 f"MPER {r.m('MPER')}, PERAR {r.t(A, 'PER')}")
    cr = r.t(A, 'CR')
    good &= line(f'{cid}.b', r.ckpsc(A) == 0 and cr >> 3 & 1 and cr >> 27 & 1,
                 'timer A: CKPSC 0, continuous, preload on', 'CKPSC 0, CONT 1, PREEN 1',
                 f'TIMACR {cr:#010x}: CKPSC {cr & 7}, CONT {cr >> 3 & 1}, PREEN {cr >> 27 & 1}, TREPU {cr >> 17 & 1}')
    rise, fall, psc = r.dead_time_ns(A)
    dten = r.t(A, 'OUT') >> 8 & 1
    good &= line(f'{cid}.c', dt_ok(rise, 100) and dt_ok(fall, 100) and dten,
                 'dead time 100 ns on both edges, enabled', f'100 ns ±{DT_STEP_NS:.2f} (one step), DTEN 1',
                 f'rise {rise:.1f} ns, fall {fall:.1f} ns (DTPRSC {psc}), DTEN {dten}')
    good &= line(f'{cid}.d', r.t(A, 'SET1') == 1 << 2 and r.t(A, 'RST1') == 1 << 3,
                 'output 1 set at the period, reset on CMP1 (left-aligned, PWMx1)', 'SETA1R PER, RSTA1R CMP1',
                 f"SETA1R {r.t(A, 'SET1'):#x}, RSTA1R {r.t(A, 'RST1'):#x}")
    good &= line(f'{cid}.e', bool(r.t(A, 'RST') >> 4 & 1), 'timer A resets on the master period',
                 'RSTAR.MSTPER 1', f"RSTAR {r.t(A, 'RST'):#x}")
    good &= line(f'{cid}.f', r.oen(A) == (False, False) and frac(rep, 'PA8') == 0 and frac(rep, 'PA9') == 0,
                 'outputs not enabled by initUnit, pins low', 'TA1OEN = TA2OEN = 0, PA8/PA9 low',
                 f"OEN {r.oen(A)}, PA8 {frac(rep, 'PA8'):.3f}, PA9 {frac(rep, 'PA9'):.3f}")

    cid = 'L0-PWM-03'
    ret = [rep[f'ret{i}'] for i in range(16)]
    good &= line(f'{cid}.a', ret[0] == PER_200K, 'getPeriod', f'{PER_200K} (PERAR)', ret[0])
    good &= line(f'{cid}.b', ret[3] == 5, 'getPeriodUs', '5 (27200 x 184 ps)', ret[3])
    udm, swp = r.t(A, 'CR2') >> 4 & 1, r.c('CR2') >> 16 & 1
    good &= line(f'{cid}.c', ret[7] == udm and ret[8] == 1 + swp and ret[9] == 0,
                 'getModulation, getSwitchConvention, getMode agree with the registers',
                 'left-aligned (UDM 0), PWMx1 (SWPA 0), voltage mode',
                 f'modulation {ret[7]} (UDM {udm}), convention PWMx{ret[8]} (SWPA {swp}), mode {ret[9]}')
    good &= line(f'{cid}.d', ret[6] == RES_PS[0], 'getResolutionPs at CKPSC 0', f'{RES_PS[0]} (PwmHAL.h table)',
                 ret[6])
    good &= line(f'{cid}.e', ret[1] == PER_MAX[0] and ret[2] == CMP_MIN[0], 'getPeriodMax, getPeriodMin at CKPSC 0',
                 f'{PER_MAX[0]:#x}, {CMP_MIN[0]:#x} (RM0440)', f'{ret[1]:#x}, {ret[2]:#x}')
    good &= line(f'{cid}.f', 0 < ret[4] <= 200000 <= ret[5], 'getFrequencyMin <= 200 kHz <= getFrequencyMax',
                 'both non-zero, bracketing the running frequency', f'min {ret[4]} Hz, max {ret[5]} Hz')
    good &= line(f'{cid}.g', s32(ret[11]) == -1, 'getAdcTrigger before setAdcTrigger',
                 'UNKNOWN_ADC (-1, PwmHAL.h)', s32(ret[11]))
    line(f'{cid}.h', 'INFO', 'defaults of getEev, getAdcEdgeTrigger, getPeriodEvntRep', 'recorded',
         f'EEV{ret[10]}, edge {ret[12]} (0 up, 1 down, 2 both), repetition {ret[13]}')

    cid = 'L0-PWM-04'
    rep, r = b.step('DUTY', A, f32(0.3))
    good &= line(f'{cid}.a', r.t(A, 'CMP1') == 8160, 'setDutyCycle(PWMA, 0.3)', 'CMP1AR 8160 (0.3 x 27200)',
                 r.t(A, 'CMP1'))
    rep, r = b.step('DUTY_RAW', A, 13600)
    good &= line(f'{cid}.b', r.t(A, 'CMP1') == 13600, 'setDutyCycleRaw(PWMA, 13600)', 'CMP1AR 13600', r.t(A, 'CMP1'))
    rep, r = b.step('DUTY', A, f32(0.3))
    good &= line(f'{cid}.c', r.t(A, 'CMP1') == 8160, 'setDutyCycle(PWMA, 0.3) again', 'CMP1AR 8160', r.t(A, 'CMP1'))

    cid = 'L0-PWM-05'
    rep, r = b.step('START_DUAL', A)
    hi, lo = 0.3 - 0.1 / 5, 0.7 - 0.1 / 5  # each output loses one 100 ns dead time per 5 us period
    good &= line(f'{cid}.a', r.oen(A) == (True, True), 'startDualOutput(PWMA) enables both outputs',
                 'TA1OEN = TA2OEN = 1', f"OENR {r.c('OENR'):#x}")
    pa8, pa9 = frac(rep, 'PA8'), frac(rep, 'PA9')
    good &= line(f'{cid}.b', near(pa8, hi, DUTY_TOL) and near(pa9, lo, DUTY_TOL) and pa8 + pa9 < 1,
                 'PA8/PA9 high time on the chip (complementary, dead time between)',
                 f'PA8 {hi:.2f}, PA9 {lo:.2f} ±{DUTY_TOL}, sum < 1', f'PA8 {pa8:.3f}, PA9 {pa9:.3f}')
    cap = sc.capture('PA8', 'PA9')
    s8, s9 = sig(*cap[1]), sig(*cap[4])
    good &= judge_sig(f'{cid}.c', 'PA8 on the scope (MUX CH1)', s8, 200e3, hi)
    good &= judge_sig(f'{cid}.d', 'PA9 on the scope (MUX CH4)', s9, 200e3, lo)
    ov = wf.overlap_fraction(cap[1][1], cap[4][1])
    good &= line(f'{cid}.e', ov <= 0.01, 'PA8 and PA9 never high together', 'overlap <= 1 % of samples',
                 f'{ov:.2%}')
    dr, df = wf.dead_times(cap[1][0], cap[1][1], cap[4][1])
    line(f'{cid}.f', 'INFO', 'dead times through the MUX (edges ~20-35 ns: indicative only, L0-PWM-20 is the measure)',
         'recorded', f"rise {dr * 1e9:.0f} ns, fall {df * 1e9:.0f} ns" if dr and df else 'no edge pair')

    good &= latency(b, sc)

    cid = 'L0-PWM-07'
    rep, r = b.step('STOP_DUAL', A)
    good &= line(f'{cid}.a', r.oen(A) == (False, False) and frac(rep, 'PA8') == 0 and frac(rep, 'PA9') == 0,
                 'stopDualOutput(PWMA): outputs disabled, pins low', 'OEN (0, 0), PA8/PA9 0',
                 f"OEN {r.oen(A)}, PA8 {frac(rep, 'PA8'):.3f}, PA9 {frac(rep, 'PA9'):.3f}")
    cap = sc.capture('PA8', 'PA9')
    good &= judge_flat(f'{cid}.b', 'PA8 on the scope after stopDualOutput', sig(*cap[1]))
    good &= judge_flat(f'{cid}.c', 'PA9 on the scope after stopDualOutput', sig(*cap[4]))
    rep, r = b.step('START_SINGLE', A, 0)
    cap = sc.capture('PA8', 'PA9')
    good &= line(f'{cid}.d', r.oen(A) == (True, False) and near(frac(rep, 'PA8'), hi, DUTY_TOL)
                 and frac(rep, 'PA9') == 0, 'startSingleOutput(PWMA, TIMING_OUTPUT1): only PA8 switches',
                 f'OEN (1, 0), PA8 {hi:.2f}, PA9 0', f"OEN {r.oen(A)}, PA8 {frac(rep, 'PA8'):.3f}, "
                 f"PA9 {frac(rep, 'PA9'):.3f}")
    good &= judge_sig(f'{cid}.e', 'PA8 on the scope, output 1 alone', sig(*cap[1]), 200e3, hi)
    good &= judge_flat(f'{cid}.f', 'PA9 on the scope, output 1 alone', sig(*cap[4]))
    rep, r = b.step('STOP_SINGLE', A, 0)
    good &= line(f'{cid}.g', r.oen(A) == (False, False) and frac(rep, 'PA8') == 0,
                 'stopSingleOutput(PWMA, TIMING_OUTPUT1)', 'OEN (0, 0), PA8 0',
                 f"OEN {r.oen(A)}, PA8 {frac(rep, 'PA8'):.3f}")
    rep, r = b.step('START_SINGLE', A, 1)
    cap = sc.capture('PA8', 'PA9')
    good &= line(f'{cid}.h', r.oen(A) == (False, True) and near(frac(rep, 'PA9'), lo, DUTY_TOL)
                 and frac(rep, 'PA8') == 0, 'startSingleOutput(PWMA, TIMING_OUTPUT2): only PA9 switches',
                 f'OEN (0, 1), PA9 {lo:.2f}, PA8 0', f"OEN {r.oen(A)}, PA8 {frac(rep, 'PA8'):.3f}, "
                 f"PA9 {frac(rep, 'PA9'):.3f}")
    good &= judge_sig(f'{cid}.i', 'PA9 on the scope, output 2 alone', sig(*cap[4]), 200e3, lo)
    good &= judge_flat(f'{cid}.j', 'PA8 on the scope, output 2 alone', sig(*cap[1]))
    rep, r = b.step('STOP_SINGLE', A, 1)
    good &= line(f'{cid}.k', r.oen(A) == (False, False) and frac(rep, 'PA9') == 0,
                 'stopSingleOutput(PWMA, TIMING_OUTPUT2)', 'OEN (0, 0), PA9 0',
                 f"OEN {r.oen(A)}, PA9 {frac(rep, 'PA9'):.3f}")
    b.step('START_DUAL', A)

    cid = 'L0-PWM-08'
    rep, r = b.step('SET_FREQUENCY', 400000)
    good &= line(f'{cid}.a', r.m('MPER') == PER_200K // 2 and r.t(A, 'PER') == PER_200K // 2,
                 'setFrequency(400 kHz) after initUnit: periods', f'MPER = PERAR = {PER_200K // 2}',
                 f"MPER {r.m('MPER')}, PERAR {r.t(A, 'PER')}")
    good &= line(f'{cid}.b', abs(r.t(A, 'CMP1') - 4080) <= 1, 'duty 0.3 kept at the new period', 'CMP1AR 4080 ±1',
                 r.t(A, 'CMP1'))
    cap = sc.capture('PA8', f=400e3)
    good &= judge_sig(f'{cid}.c', 'PA8 at 400 kHz on the scope', sig(*cap[1]), 400e3, 0.3 - 0.1 / 2.5)
    rep, r = b.step('SET_FREQUENCY', 200000)
    cap = sc.capture('PA8')
    good &= line(f'{cid}.d', r.t(A, 'PER') == PER_200K and abs(r.t(A, 'CMP1') - 8160) <= 1,
                 'setFrequency(200 kHz) back: period and duty', f'PERAR {PER_200K}, CMP1AR 8160 ±1',
                 f"PERAR {r.t(A, 'PER')}, CMP1AR {r.t(A, 'CMP1')}")
    good &= judge_sig(f'{cid}.e', 'PA8 back at 200 kHz on the scope', sig(*cap[1]), 200e3, hi)

    good &= phase_cases(b, sc, hi)
    good &= adc_cases(b)
    good &= burst_cases(b, hi)
    good &= period_event_master(b)
    good &= pwma_phase_cases(b, sc)
    good &= period_event_tima(b)
    return good


def latency(b, sc):
    """Call -> pin, PA6 marker on CH1 and PA8 on CH4. The marker rises just before the call (interrupts masked)."""
    cid = 'L0-PWM-06'
    good = True
    T = 5e-6
    for k, (d_from, d_to) in enumerate(((0.3, 0.6), (0.6, 0.3), (0.3, 0.6), (0.6, 0.3))):
        out = {}

        def act():
            out['rep'], _ = b.step('LAT_DUTY', A, f32(d_to))
        cap = sc.triggered(CUT_MARKER, 'PA8', 2e-6, 6e-6, act)
        t, vm = cap[1]
        _, v8 = cap[4]
        mr, _ = wf.edges(t, vm)
        r8, f8 = wf.edges(t, v8)
        t0 = mr[0] if mr else None
        pulses = [(r, next((f for f in f8 if f > r), None)) for r in r8]
        pulses = [(r, f - r) for r, f in pulses if f is not None]
        w_old, w_new = (d_from - 0.02) * T, (d_to - 0.02) * T
        tol = 0.05 * T
        kinds = ['old' if abs(w - w_old) <= tol else 'new' if abs(w - w_new) <= tol else 'glitch' for _, w in pulses]
        first_new = next((r for (r, _), k in zip(pulses, kinds) if k == 'new' and t0 is not None and r >= t0), None)
        lat = first_new - t0 if first_new is not None else None
        # The new duty must apply from a period start (preload), within one period of the call plus the call
        # itself; every pulse is either the old or the new width (no partial pulse), old ones before new ones.
        order_ok = 'old' not in kinds[kinds.index('new'):] if 'new' in kinds else False
        ok = t0 is not None and lat is not None and lat <= T + 1e-6 and 'glitch' not in kinds and order_ok
        good &= line(f'{cid}.{"abcd"[k]}', bool(ok), f'setDutyCycle {d_from} -> {d_to}: new width from a period start '
                     'within one period of the call, no partial pulse',
                     f'old {w_old * 1e6:.2f} us, new {w_new * 1e6:.2f} us ±0.25 us, first new pulse <= 6 us after the marker',
                     'no marker edge' if t0 is None else
                     f"widths {[round(w * 1e6, 2) for _, w in pulses]} us at {[round((r - t0) * 1e6, 2) for r, _ in pulses]}"
                     f" us; latency {'-' if lat is None else f'{lat * 1e6:.2f} us'}; call "
                     f"{out.get('rep', {}).get('cycles', '?')} cycles")

    out = {}

    def act_stop():
        out['rep'], _ = b.step('LAT_STOP', A)
    cap = sc.triggered(CUT_MARKER, 'PA8', 2e-6, 6e-6, act_stop)
    t, vm = cap[1]
    _, v8 = cap[4]
    mr, _ = wf.edges(t, vm)
    t0 = mr[0] if mr else None
    late = [v for tt, v in zip(t, v8) if t0 is not None and tt > t0 + 1e-6]
    r8, f8 = wf.edges(t, v8)
    last = max([x for x in r8 + f8 if t0 is not None and x >= t0], default=None)
    ok = t0 is not None and late and max(late) < 0.5
    good &= line(f'{cid}.e', bool(ok), 'stopDualOutput: PA8 low within 1 us of the call', 'PA8 < 0.5 V from +1 us',
                 'no marker edge' if t0 is None else
                 f"max after +1 us {max(late):.2f} V; last edge {'none' if last is None else f'{(last - t0) * 1e9:.0f} ns'}"
                 f" after the marker; call {out['rep']['cycles']} cycles")

    def act_start():
        out['rep'], _ = b.step('LAT_START', A)
    cap = sc.triggered(CUT_MARKER, 'PA8', 2e-6, 6e-6, act_start)
    t, vm = cap[1]
    _, v8 = cap[4]
    mr, _ = wf.edges(t, vm)
    t0 = mr[0] if mr else None
    r8, _ = wf.edges(t, v8)
    first = next((x for x in r8 if t0 is not None and x >= t0), None)
    ok = first is not None and first - t0 <= T + 1e-6
    good &= line(f'{cid}.f', bool(ok), 'startDualOutput: PA8 switches within one period of the call',
                 'first rising edge <= 6 us after the marker',
                 'no marker edge' if t0 is None else
                 f"first edge {'none' if first is None else f'{(first - t0) * 1e6:.2f} us'} after the marker; "
                 f"call {out['rep']['cycles']} cycles")
    return good


def phase_cases(b, sc, hi):
    cid = 'L0-PWM-09'
    good = True
    for u in (C, B):
        b.step('INIT_UNIT', u)
        b.step('DUTY', u, f32(0.3))
        b.step('START_DUAL', u)
    p = phase(sc.capture('PA8', 'PB12', periods=4))
    good &= line(f'{cid}.a', near(phase_err(p, 0), 0, PHASE_TOL_DEG), 'PWMC after initUnit: in phase with PWMA',
                 f'0° ±{PHASE_TOL_DEG}° (PB12 vs PA8)', 'no edges' if p is None else f'{p:.1f}°')
    rep, r = b.step('PHASE', C, 90)
    rst = r.t(C, 'RST')
    good &= line(f'{cid}.b', r.m('MCMP2') == 6800 and rst >> 6 & 1 and not rst >> 4 & 1,
                 'setPhaseShift(PWMC, 90): master CMP2 and the reset source', 'MCMP2R 6800, RSTCR MSTCMP2 not MSTPER',
                 f"MCMP2R {r.m('MCMP2')}, RSTCR {rst:#x}")
    p = phase(sc.capture('PA8', 'PB12', periods=4))
    good &= line(f'{cid}.c', near(phase_err(p, 90), 0, PHASE_TOL_DEG), 'PWMC 90° on the scope',
                 f'90° ±{PHASE_TOL_DEG}°', 'no edges' if p is None else f'{p:.1f}°')
    rep, r = b.step('PHASE', B, 180)
    rst = r.t(B, 'RST')
    good &= line(f'{cid}.d', r.t(A, 'CMP2') == 13600 and rst >> 20 & 1,
                 'setPhaseShift(PWMB, 180): timer A CMP2 and timer B reset source', 'CMP2AR 13600, RSTBR TIMACMP2',
                 f"CMP2AR {r.t(A, 'CMP2')}, RSTBR {rst:#x}")
    p = phase(sc.capture('PA8', 'PA10', periods=4))
    good &= line(f'{cid}.e', near(phase_err(p, 180), 0, PHASE_TOL_DEG), 'PWMB 180° on the scope (PA10 vs PA8)',
                 f'180° ±{PHASE_TOL_DEG}°', 'no edges' if p is None else f'{p:.1f}°')
    rep, r = b.step('PHASE', C, -90)
    p = phase(sc.capture('PA8', 'PB12', periods=4))
    good &= line(f'{cid}.f', r.m('MCMP2') == 20400 and near(phase_err(p, 270), 0, PHASE_TOL_DEG),
                 'setPhaseShift(PWMC, -90) is 270°', f'MCMP2R 20400, 270° ±{PHASE_TOL_DEG}°',
                 f"MCMP2R {r.m('MCMP2')}, {'no edges' if p is None else f'{p:.1f}°'}")
    rep, r = b.step('PHASE', C, 0)
    rst = r.t(C, 'RST')
    p = phase(sc.capture('PA8', 'PB12', periods=4))
    good &= line(f'{cid}.g', rst >> 4 & 1 and not rst >> 6 & 1 and near(phase_err(p, 0), 0, PHASE_TOL_DEG),
                 'setPhaseShift(PWMC, 0): back on the master period', f'RSTCR MSTPER, 0° ±{PHASE_TOL_DEG}°',
                 f"RSTCR {rst:#x}, {'no edges' if p is None else f'{p:.1f}°'}")
    return good


def pwma_phase_cases(b, sc):
    """setPhaseShift(PWMA, ...) must leave timer A alone: PWMA is the phase reference (spin_pwm.md).
    Before Core-staging#61 it cleared RSTAR.MSTPER, so timer A stopped resetting on the master period."""
    cid = 'L0-PWM-10'
    good = True
    _, r0 = b.step('SAMPLE')
    rst0 = r0.t(A, 'RST')
    p0 = phase(sc.capture('PA8', 'PB12', periods=4))
    for k, deg in enumerate((90, 288)):
        rep, r = b.step('PHASE', A, deg)
        p = phase(sc.capture('PA8', 'PB12', periods=4))
        err = phase_err(p, 0.0)
        good &= line(f'{cid}.{"ab"[k]}', r.t(A, 'RST') == rst0 and rst0 >> 4 & 1
                     and err is not None and abs(err) <= PHASE_TOL_DEG,
                     f'setPhaseShift(PWMA, {deg}) leaves timer A untouched: still reset on the master period, '
                     'PA8 in phase with PB12 (PWMC, 0°)',
                     f'RSTAR unchanged, MSTPER 1; 0° ±{PHASE_TOL_DEG}°',
                     f"RSTAR {rst0:#x} -> {r.t(A, 'RST'):#x}; MCMP1-4 {[r.m(f'MCMP{i}') for i in range(1, 5)]}; "
                     f"CMP2AR {r.t(A, 'CMP2')}; phase {'?' if p0 is None else f'{p0:.1f}'}° before -> "
                     f"{'?' if p is None else f'{p:.1f}'}° after")
    rep, r = b.step('SET_FREQUENCY', 200000)
    p = phase(sc.capture('PA8', 'PB12', periods=4))
    err = phase_err(p, 0.0)
    good &= line(f'{cid}.c', r.t(A, 'RST') >> 4 & 1 and err is not None and abs(err) <= PHASE_TOL_DEG,
                 'then setFrequency(200 kHz): timer A still reset on the master period, PA8 in phase with PB12',
                 f'RSTAR.MSTPER 1; 0° ±{PHASE_TOL_DEG}°',
                 f"RSTAR {r.t(A, 'RST'):#x}; phase {'?' if p is None else f'{p:.1f}'}°")
    return good


def adc_cases(b):
    cid = 'L0-PWM-11'
    good = True
    b.step('ADC_TRIGGER', A, 2)
    rep, r = b.step('GETTERS', A)
    good &= line(f'{cid}.a', s32(rep['ret11']) == 2, 'setAdcTrigger(PWMA, ADC_2) then getAdcTrigger', 'ADC_2 (2)',
                 s32(rep['ret11']))
    rep, r = b.step('ADC_DECIMATION', A, 4)
    psc = r.c('ADCPS1') >> 12 & 0x1F
    good &= line(f'{cid}.b', psc == 3, 'setAdcDecimation(PWMA, 4): ADC trigger 3 postscaler',
                 'ADCPS1.AD3PSC 3 (one trigger in 4)', psc)
    rep, r = b.step('ADC_ENABLE', A)
    good &= line(f'{cid}.c', bool(r.c('ADC3R') >> 11 & 1), 'enableAdcTrigger(PWMA): timer A CMP3 drives ADC trigger 3',
                 'ADC3R.AD3TAC3 1', f"ADC3R {r.c('ADC3R'):#x}, CMP3AR {r.t(A, 'CMP3')}, ADCUR {r.c('ADCUR'):#x}")
    rep, r = b.step('ADC_INSTANT', A, f32(0.25))
    good &= line(f'{cid}.d', r.t(A, 'CMP3') == 6800, 'setAdcTriggerInstant(PWMA, 0.25)', 'CMP3AR 6800 (0.25 x 27200)',
                 r.t(A, 'CMP3'))
    rep, r = b.step('ADC_POSTSCALER', A, 2)
    psc = r.c('ADCPS1') >> 12 & 0x1F
    good &= line(f'{cid}.e', psc == 2, 'setAdcTriggerPostScaler(PWMA, 2)', 'ADCPS1.AD3PSC 2', psc)
    rep, r = b.step('ADC_DISABLE', A)
    good &= line(f'{cid}.f', not r.c('ADC3R') >> 11 & 1, 'disableAdcTrigger(PWMA)', 'ADC3R.AD3TAC3 0',
                 f"ADC3R {r.c('ADC3R'):#x}")
    return good


def callback_rate(b, window=1.0):
    a = b.report()
    time.sleep(window)
    c = b.report()
    if not a or not c or c['stamp_ms'] == a['stamp_ms']:
        return None, a, c
    return (c['cb_count'] - a['cb_count']) / ((c['stamp_ms'] - a['stamp_ms']) / 1e3), a, c


def period_event_master(b):
    cid = 'L0-PWM-12'
    good = True
    rep, r = b.step('PE_CONFIGURE', MASTER, 10)
    good &= line(f'{cid}.a', r.m('MREP') == 9 and rep['ret0'] == 10,
                 'configurePeriodEvnt(MSTR, 10) and getPeriodEvntRep', 'MREP 9, returns 10',
                 f"MREP {r.m('MREP')}, returns {rep['ret0']}")
    b.step('PE_ENABLE', MASTER)
    time.sleep(0.3)
    rate, _, _ = callback_rate(b)
    good &= line(f'{cid}.b', near(rate, 20000, 200), 'enablePeriodEvnt(MSTR): callback every 10 periods',
                 '20 kHz ±1 %', 'background stopped' if rate is None else f'{rate:.0f} Hz')
    rep, r = b.step('PE_SET_REP', MASTER, 20)
    time.sleep(0.2)
    rate, _, _ = callback_rate(b)
    good &= line(f'{cid}.c', r.m('MREP') == 19 and rep['ret0'] == 20 and near(rate, 10000, 100),
                 'setPeriodEvntRep(MSTR, 20)', 'MREP 19, returns 20, 10 kHz ±1 %',
                 f"MREP {r.m('MREP')}, returns {rep['ret0']}, {'?' if rate is None else f'{rate:.0f}'} Hz")
    b.step('PE_DISABLE', MASTER)
    time.sleep(0.2)
    rate, _, _ = callback_rate(b, 0.5)
    good &= line(f'{cid}.d', rate == 0, 'disablePeriodEvnt(MSTR): no more callbacks', '0 Hz',
                 'background stopped' if rate is None else f'{rate:.0f} Hz')
    return good


def period_event_tima(b):
    """The documented unit argument (TIMA..TIMF). Last step of P1: if the interrupt flag is never cleared, the CPU
    stays in the interrupt and only a reset brings it back."""
    cid = 'L0-PWM-12'
    rep, r = b.step('PE_CONFIGURE', A, 10)
    ok_reg = r.t(A, 'REP') == 9 and rep['ret0'] == 10
    b.step('PE_ENABLE', A)
    time.sleep(0.3)
    rate, a, c = callback_rate(b)
    if rate is None and a and c:
        why = f"background stopped; callbacks {c['cb_count'] - a['cb_count']} in 1 s"
    else:
        why = f'{rate:.0f} Hz' if rate is not None else 'no SWD read'
    return line(f'{cid}.e', ok_reg and near(rate, 20000, 200),
                'configurePeriodEvnt(TIMA, 10) + enablePeriodEvnt(TIMA): callback every 10 periods, firmware alive',
                'REPAR 9, 20 kHz ±1 %, background running', f"REPAR {r.t(A, 'REP')}, returns {rep['ret0']}; {why}")


def burst_cases(b, hi):
    cid = 'L0-PWM-13'
    good = True
    rep, r = b.step('BM_INIT')
    good &= line(f'{cid}.a', bool(r.c('BMCR') & 1), 'initBurstMode', 'BMCR.BME 1', f"BMCR {r.c('BMCR'):#x}")
    rep, r = b.step('BM_SET', 2, 4)
    good &= line(f'{cid}.b', r.c('BMCMPR') == 1 and r.c('BMPER') == 3, 'setBurstMode(2, 4)', 'BMCMPR 1, BMPER 3',
                 f"BMCMPR {r.c('BMCMPR')}, BMPER {r.c('BMPER')}")
    rep, r = b.step('BM_START')
    pa8 = frac(rep, 'PA8')
    good &= line(f'{cid}.c', bool(r.c('BMCR') >> 31 & 1) and near(pa8, hi / 2, 0.04),
                 'startBurstMode: 2 of 4 periods idle', f'BMSTAT 1, PA8 high {hi / 2:.2f} ±0.04',
                 f"BMCR {r.c('BMCR'):#x}, PA8 {pa8:.3f}")
    rep, r = b.step('BM_STOP')
    pa8 = frac(rep, 'PA8')
    good &= line(f'{cid}.d', not r.c('BMCR') >> 31 & 1 and near(pa8, hi, DUTY_TOL), 'stopBurstMode',
                 f'BMSTAT 0, PA8 high {hi:.2f} ±{DUTY_TOL}', f"BMCR {r.c('BMCR'):#x}, PA8 {pa8:.3f}")
    rep, r = b.step('BM_DEINIT')
    good &= line(f'{cid}.e', not r.c('BMCR') & 1 and near(frac(rep, 'PA8'), hi, DUTY_TOL), 'deInitBurstMode',
                 'BMCR.BME 0, PA8 still switching', f"BMCR {r.c('BMCR'):#x}, PA8 {frac(rep, 'PA8'):.3f}")
    return good


# ----------------------------------------------------------------------------------------------------------- P2


def p2(b, sc):
    good = True
    b.step('SET_FREQUENCY', 200000)
    b.step('MODULATION', A, 1)
    b.step('CONVENTION', A, 2)
    b.step('DEAD_TIME', A, 200, 50)
    b.step('ADC_EDGE', A, 1)
    b.step('MODULATION', C, 1)
    b.step('MODE', D, 1)
    b.step('EEV', D, 5)
    for u in (A, C, D):
        b.step('INIT_UNIT', u)
    rep, r = b.step('GETTERS', A)
    cid = 'L0-PWM-14'
    good &= line(f'{cid}.a', rep['ret7'] == 1 and r.t(A, 'CR2') >> 4 & 1 and r.t(A, 'PER') == PER_200K // 2,
                 'setModulation(PWMA, UpDwn): up-down counter at half the period', 'getModulation UpDwn, UDM 1, '
                 f'PERAR {PER_200K // 2}', f"returns {rep['ret7']}, TIMACR2 {r.t(A, 'CR2'):#x}, PERAR {r.t(A, 'PER')}")
    good &= line(f'{cid}.b', rep['ret8'] == 2 and r.c('CR2') >> 16 & 1, 'setSwitchConvention(PWMA, PWMx2)',
                 'getSwitchConvention PWMx2, CR2.SWPA 1', f"returns PWMx{rep['ret8']}, CR2 {r.c('CR2'):#x}")
    rise, fall, psc = r.dead_time_ns(A)
    good &= line(f'{cid}.c', dt_ok(rise, 200) and dt_ok(fall, 50), 'setDeadTime(PWMA, 200, 50) before initUnit',
                 f'rise 200 ns, fall 50 ns ±{DT_STEP_NS:.2f}', f'rise {rise:.1f} ns, fall {fall:.1f} ns (DTPRSC {psc})')
    good &= line(f'{cid}.d', rep['ret12'] == 1, 'setAdcEdgeTrigger(PWMA, EdgeTrigger_down) then getAdcEdgeTrigger',
                 'EdgeTrigger_down', f"returns {rep['ret12']}, TIMACR2.ADROM {r.t(A, 'CR2') >> 10 & 3}")
    b.step('DUTY', A, f32(0.3))
    rep, r = b.step('START_DUAL', A)
    pa8, pa9 = frac(rep, 'PA8'), frac(rep, 'PA9')
    good &= line(f'{cid}.e', 0.25 <= pa9 <= 0.31 and 0.65 <= pa8 <= 0.71 and pa8 + pa9 < 1,
                 'PWMx2: the duty is on the low-side pin PA9', 'PA9 0.25-0.31, PA8 0.65-0.71 (dead times taken)',
                 f'PA8 {pa8:.3f}, PA9 {pa9:.3f}')
    cap = sc.capture('PA9', 'PA8')
    s9, s8 = sig(*cap[1]), sig(*cap[4])
    good &= line(f'{cid}.f', s9[2] and near(s9[0], 200e3, 1e3) and 0.25 <= (s9[1] or 0) <= 0.31,
                 'PA9 on the scope: 200 kHz switching, duty 0.3 less the dead time', '200 kHz ±0.5 %, duty 0.25-0.31',
                 fmt_sig(s9))
    line(f'{cid}.g', 'INFO', 'PA8 on the scope (complement)', 'recorded', fmt_sig(s8))

    rep, r = b.step('GETTERS', D)
    cid = 'L0-PWM-17'
    good &= line(f'{cid}.a', rep['ret9'] == 1 and rep['ret10'] == 5, 'setMode(PWMD, CURRENT_MODE), setEev(PWMD, EEV5)',
                 'getMode CURRENT_MODE, getEev EEV5', f"mode {rep['ret9']}, EEV{rep['ret10']}")
    set1, rst1 = r.t(D, 'SET1'), r.t(D, 'RST1')
    good &= line(f'{cid}.b', set1 == 1 << 6 and rst1 >> 3 & 1 and rst1 >> 25 & 1,
                 'current mode: output 1 set on CMP4, reset on CMP1 or external event 5', 'SETD1R CMP4, RSTD1R CMP1 | EEV5',
                 f'SETD1R {set1:#x}, RSTD1R {rst1:#x}, CMP1DR {r.t(D, "CMP1")}, CMP4DR {r.t(D, "CMP4")}')

    b.step('DUTY', C, f32(0.3))
    b.step('START_DUAL', C)
    good &= low_duty(b, sc)
    return good


def low_duty(b, sc):
    """#8 / Core#166: duty in (0, 0.0025) must not invert the outputs, nor duty near 1. PWMA is up-down PWMx2
    (duty on PA9, the Core#166 boost case), PWMC up-down PWMx1 (duty on PB12). A compare value under RM0440's
    minimum (0x60 at CKPSC 0) is outside the supported range: the fix maps it to 0."""
    good = True
    for cid, duties, low_side in (('L0-PWM-15', LOW_DUTIES, True), ('L0-PWM-16', HIGH_DUTIES, False)):
        for k, d in enumerate(duties):
            b.step('DUTY', A, f32(d))
            rep, r = b.step('DUTY', C, f32(d))
            for u, duty_pin, other in ((A, 'PA9', 'PA8'), (C, 'PB12', 'PB13')):
                cmp1 = r.t(u, 'CMP1')
                fd, fo = frac(rep, duty_pin), frac(rep, other)
                if low_side:
                    ok = fd <= 0.02 and fo >= 0.9 and (cmp1 == 0 or cmp1 >= CMP_MIN[0])
                    exp = f'{duty_pin} high <= 0.02, {other} >= 0.9, CMP1 0 or >= {CMP_MIN[0]:#x}'
                else:
                    ok = fd >= 0.9 and fo <= 0.02
                    exp = f'{duty_pin} high >= 0.9, {other} <= 0.02'
                name = 'PWMA (up-down, PWMx2)' if u == A else 'PWMC (up-down, PWMx1)'
                good &= line(f'{cid}.{"ac"[u // 2]}{k}', ok, f'{name} at duty {d}', exp,
                             f'{duty_pin} {fd:.3f}, {other} {fo:.3f}, CMP1 {cmp1}, '
                             f"swap {r.c('CR2') >> (16 + u) & 1}")
            if low_side and d in (0.0001, 0.001, 0.0024):
                cap = sc.capture('PA9', 'PB12')
                m9, m12 = wf.mean(cap[1][1]), wf.mean(cap[4][1])
                good &= line(f'{cid}.s{k}', m9 < 0.3 and m12 < 0.3,
                             f'duty {d}: duty pins PA9 and PB12 on the scope (MUX, DC level)',
                             'mean < 0.3 V (inverted: ~3.3 V)', f'PA9 {m9:.2f} V, PB12 {m12:.2f} V')
    return good


# ----------------------------------------------------------------------------------------------------------- P3, P4


def p3(b, sc):
    cid = 'L0-PWM-18'
    good = True
    b.step('INIT_FIXED', 100000)
    b.step('INIT_UNIT', A)
    b.step('DUTY', A, f32(0.5))
    b.step('START_DUAL', A)
    rep, r = b.step('GETTERS', A)
    good &= line(f'{cid}.a', r.m('MPER') == 54400 and r.t(A, 'PER') == 54400 and r.ckpsc(A) == 0,
                 'initFixedFrequency(100 kHz): periods', 'MPER = PERAR = 54400, CKPSC 0',
                 f"MPER {r.m('MPER')}, PERAR {r.t(A, 'PER')}, CKPSC {r.ckpsc(A)}")
    good &= judge_sig(f'{cid}.b', 'PA8 at 100 kHz on the scope', sig(*sc.capture('PA8', f=100e3)[1]), 100e3,
                      0.5 - 0.1 / 10)
    line(f'{cid}.c', 'INFO', 'getFrequencyMin / getFrequencyMax after initFixedFrequency', 'recorded',
         f"{rep['ret4']} / {rep['ret5']} Hz")
    return good


def p4(b, sc):
    cid = 'L0-PWM-19'
    good = True
    b.step('INIT_VARIABLE', 200000, 50000)
    b.step('INIT_UNIT', A)
    b.step('DUTY', A, f32(0.5))
    b.step('START_DUAL', A)
    rep, r = b.step('GETTERS', A)
    good &= line(f'{cid}.a', r.ckpsc(A) == 1 and r.t(A, 'PER') == 13600 and r.m('MPER') == 13600,
                 'initVariableFrequency(200 kHz, 50 kHz): prescaler for 50 kHz', 'CKPSC 1, MPER = PERAR = 13600',
                 f"CKPSC {r.ckpsc(A)}, MPER {r.m('MPER')}, PERAR {r.t(A, 'PER')}")
    good &= line(f'{cid}.b', rep['ret6'] == RES_PS[1], 'getResolutionPs at CKPSC 1', f'{RES_PS[1]} (PwmHAL.h table)',
                 rep['ret6'])
    good &= judge_sig(f'{cid}.c', 'PA8 at 200 kHz', sig(*sc.capture('PA8')[1]), 200e3, 0.48)
    for k, (f, per) in enumerate(((100000, 27200), (50000, 54400))):
        rep, r = b.step('SET_FREQUENCY', f)
        good &= line(f'{cid}.{"df"[k]}', r.t(A, 'PER') == per, f'setFrequency({f // 1000} kHz) within the range',
                     f'PERAR {per}', r.t(A, 'PER'))
        good &= judge_sig(f'{cid}.{"eg"[k]}', f'PA8 at {f // 1000} kHz', sig(*sc.capture('PA8', f=f)[1]), f,
                          0.5 - 0.1e-6 * f)
    rep, r = b.step('SET_FREQUENCY', 40000)
    good &= line(f'{cid}.h', r.t(A, 'PER') == 54400, 'setFrequency(40 kHz) below the minimal frequency is refused',
                 'PERAR stays 54400 (PwmHAL.h: cannot go below the initial minimum)', r.t(A, 'PER'))
    return good


# ----------------------------------------------------------------------------------------------------------- supervised


def tty_prompt(text):
    with open('/dev/tty', 'r+') as tty:
        tty.write('\n' + text + '\n  Press Enter when done (or type s to skip): ')
        tty.flush()
        return tty.readline().strip().lower() != 's'


def supervised(b, scope):
    """Hand-held probes on the CUT pins (until #16 makes them switchable): dead time and the ns low-duty sweep."""
    who = os.environ.get('CORE_SUPERVISOR') or th.subprocess.run(
        ['git', 'config', 'user.name'], capture_output=True, text=True).stdout.strip() or 'unknown'
    if not tty_prompt('L0-PWM-20/21 (supervised): unplug the SPOC BNC cables from scope CH2 and CH3. Probe PA8 with '
                      'CH2 and PA9 with CH3 directly at the CUT pins (x10 probes, ground springs to the Spin GND).'):
        for cid, what in CASES:
            if cid in SUPERVISED:
                th.line(cid, 'BLOCKED', what, '-', f'skipped by the supervisor ({who})')
        return True
    line('L0-PWM-20.0', 'INFO', 'supervised by', 'recorded', who)
    good = True
    for ch in (2, 3):
        scope.setup_channel(ch, scale=1.0, offset=-1.65, probe=10)
    b.reset()
    b.step('SET_FREQUENCY', 200000)
    b.step('INIT_UNIT', A)
    b.step('DUTY', A, f32(0.3))
    b.step('START_DUAL', A)
    for k, (rise, fall) in enumerate(((100, 100), (200, 50))):
        if k:
            b.step('DEAD_TIME', A, rise, fall)
        scope.set_timebase(1e-6)
        scope.set_edge_trigger(2, 1.65, sweep='AUTO')
        scope.single()
        t, v2 = scope.waveform(2)
        _, v3 = scope.waveform(3)
        dr, df = wf.dead_times(t, v2, v3)
        ok = dr is not None and df is not None and abs(dr * 1e9 - rise) <= 3 and abs(df * 1e9 - fall) <= 3
        good &= line(f'L0-PWM-20.{"ab"[k]}', ok, f'dead time {rise}/{fall} ns on PA8/PA9 (direct probes)',
                     f'{rise} / {fall} ns ±3 ns', 'no edge pair' if dr is None or df is None
                     else f'{dr * 1e9:.1f} / {df * 1e9:.1f} ns')
    b.reset()
    b.step('SET_FREQUENCY', 200000)
    b.step('MODULATION', A, 1)
    b.step('CONVENTION', A, 2)
    b.step('INIT_UNIT', A)
    b.step('START_DUAL', A)
    scope.set_timebase(1e-6)
    bad = []
    for raw in list(range(0, 130, 4)) + [13500, 13560, 13590, 13597, 13600]:
        b.step('DUTY_RAW', A, raw)
        scope.single()
        t, v3 = scope.waveform(3)
        if raw and raw < 0x60 and wf.mean(v3) > 0.3:
            bad.append(raw)
    good &= line('L0-PWM-21.a', not bad, 'PA9 (PWMx2 duty pin) never stuck high for CMP1 1..0x5F (direct probe)',
                 'mean < 0.3 V at each step', f'stuck high at CMP1 {bad}' if bad else 'none stuck')
    tty_prompt('Put the SPOC BNC cables back on scope CH2 and CH3.')
    return good


# ----------------------------------------------------------------------------------------------------------- main


def blocked(reason, only=None):
    for cid, what in CASES:
        if only is None or cid in only:
            th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def run_profile(name, fn, b, sc, cases):
    t0 = time.monotonic()
    if b.reset() is None:
        for cid in cases:
            th.line(cid, 'FAIL', f'{name}: fresh boot', 'report reachable', 'firmware did not boot after the reset')
        return False
    try:
        ok = fn(b, sc)
    except Exception as e:  # noqa: BLE001 - a step that never returns must not hide the remaining profiles
        th.line(cases[0], 'FAIL', f'{name} stopped', 'every step acknowledged', f'{type(e).__name__}: {e}')
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
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_pwm_')
    flash_log = os.path.join(out, 'target_pwm_flash.log')
    rc = th.flash(ENV, flash_log)
    if rc != 0:
        th.line('L0-PWM-01', 'FAIL', 'env PWM builds and flashes over the ST-Link', 'pio upload exit 0',
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
        good &= line('L0-PWM-01', boot is not None, 'env PWM builds, flashes and answers over SWD',
                     f'pio upload exit 0, magic {MAGIC:#010x}', 'no report' if boot is None else
                     f"magic {boot['magic']:#010x}, boot {boot['boot_count']}")
        if boot is None:
            return 1
        good &= run_profile('P1 left-aligned', p1, b, sc, ['L0-PWM-02'])
        good &= run_profile('P2 up-down, low duty', p2, b, sc, ['L0-PWM-14'])
        good &= run_profile('P3 fixed frequency', p3, b, sc, ['L0-PWM-18'])
        good &= run_profile('P4 variable frequency', p4, b, sc, ['L0-PWM-19'])
        if os.environ.get('CORE_SUPERVISED') == '1':
            mux.disable_all()
            sc.route = None
            good &= supervised(b, scope)
        else:
            blocked('supervised (hand-held probes until #16): tests/run_all.sh --l0 --target --supervised', SUPERVISED)
        b.reset()  # leave the CUT with no output running
    finally:
        mux.disable_all()
        mux.close()
    line('L0-PWM-22', 'INFO', 'module duration (flash, four profiles, scope)', 'recorded',
         f'{time.monotonic() - t_start:.0f} s, {len(b.log)} steps')
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
