#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-ADD: ADC data dispatch under a critical task, judged on the chip over SWD (Core#161, #162, #173, #175).
##
## env ADD (tests/firmware/data_dispatch) is flashed on the CUT. Boot 1, mode 1: ADC2 converts pins 34 (PA6, the
## DAC's sawtooth) and 35 every 5 us PWM period, one channel per trigger; a 100 us critical task has the scheduler
## dispatch the data, fetches pin 35 (counted) and never fetches pin 34.
##  - L0-ADD-02: no pin 35 sample lost: fetched = elapsed periods / 2, ±1 %, on time (.a) and with a 200 us
##    busy-wait every 50 runs that makes the next dispatch late (.b, Core#162).
##  - L0-ADD-03: peek(34) still follows the sawtooth with its buffer full and never fetched (Core#173).
##  - L0-ADD-04: the halfword just past each of pin 34's two buffers does not change (nothing written past the end,
##    Core#161/#173), read through adc_channel_buffers over SWD.
##  - L0-ADD-07 (INFO): background spins per second, what the dispatch leaves of the CPU (Core#161).
## Boot 2, mode 2: pins 24 (ADC1) and 34 (ADC2), a 5 ms TIM6 task: 1000+ samples per DMA buffer, more than the
## 4 KB heap (Core#175).
##  - L0-ADD-05: the task runs at 5 ms ±5 % and the background keeps running (no fault).
##  - L0-ADD-06 (INFO): which ADCs give values (NO_VALUE: left out for lack of memory).
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON]
import target_harness as th  # noqa: E402

ENV = 'ADD'
MAGIC = 0x41444431  # "ADD1"
LAYOUT = 1
FIELDS = ['magic', 'layout', 'req_mode', 'late_every', 'late_us', 'req_seq', 'ack_seq', 'rc', 'runs', 'run_ms',
          'fetched', 'empty_calls', 'max_per_call', 'late_count', 'peek34', 'peek24', 'saw', 'bg_count', 'bg_ms']
PWM_PERIOD_US = 5.0
CHANNELS = 2            # ADC2 converts one of pins 34, 35 per trigger
TASK_US = 100
LATE_EVERY, LATE_US = 50, 200
HEAP_TASK_US = 5000
CHANNELS_BUFFERS_SIZE = 32
NO_VALUE = -10000.0
BOOT_TIMEOUT_S = 30.0
WINDOW_S = 4.0
LOSS_TOL = 0.01


def f32(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


def report(addr):
    words = th.read_words(addr, len(FIELDS))
    rep = dict(zip(FIELDS, words)) if words else None
    if rep is not None and rep['rc'] >= 1 << 31:
        rep['rc'] -= 1 << 32
    return rep


def wait_boot(addr):
    t0 = time.monotonic()
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        rep = report(addr)
        # After a reset RAM keeps the previous boot until startup clears it: a fresh boot has req_seq 0.
        if rep and rep['magic'] == MAGIC and rep['layout'] == LAYOUT and rep['req_seq'] == 0 and rep['ack_seq'] == 0:
            return rep
        time.sleep(0.5)
    return None


def start(addr, mode, timeout=3.0):
    th.write_word(addr + 4 * FIELDS.index('req_mode'), mode)
    th.write_word(addr + 4 * FIELDS.index('req_seq'), 1)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rep = report(addr)
        if rep and rep['ack_seq'] == 1:
            return rep
        time.sleep(0.1)
    return None


def window(addr):
    """Two reports WINDOW_S apart (None if either SWD read failed)."""
    a = report(addr)
    time.sleep(WINDOW_S)
    b = report(addr)
    return (a, b) if a and b else (None, None)


def loss_case(cid, what, addr):
    a, b = window(addr)
    if a is None or b['run_ms'] == a['run_ms']:
        th.line(cid, 'FAIL', what, f'fetched = periods / {CHANNELS} ±{LOSS_TOL:.0%}', 'no task runs between the reads')
        return False, None
    expected = (b['run_ms'] - a['run_ms']) * 1000.0 / PWM_PERIOD_US / CHANNELS
    got = b['fetched'] - a['fetched']
    ratio = got / expected
    ok = abs(ratio - 1) <= LOSS_TOL
    th.line(cid, th.judge(ok), what, f'fetched = periods / {CHANNELS} ±{LOSS_TOL:.0%}',
            f"{got} of {expected:.0f} ({ratio:.3f}); {b['runs'] - a['runs']} runs, {b['late_count'] - a['late_count']} "
            f"late, most per fetch {b['max_per_call']}, empty fetches {b['empty_calls'] - a['empty_calls']}")
    return ok, (a, b)


def buffer_ends(addr_bufs):
    """Addresses just past pin 34's two buffers: ADC2 (index 1), channel rank 1 (index 0)."""
    w = th.read_words(addr_bufs, 1)
    if not w or not w[0]:
        return None
    per_adc = th.read_words(w[0] + 4 * 1, 1)
    if not per_adc or not per_adc[0]:
        return None
    pair_ptr = th.read_words(per_adc[0], 1)
    if not pair_ptr or not pair_ptr[0]:
        return None
    pair = th.read_words(pair_ptr[0], 2)
    if not pair or not all(pair):
        return None
    return [p + 2 * CHANNELS_BUFFERS_SIZE for p in pair]


def halfword(addr):
    base = addr & ~3
    w = th.read_words(base, 1)
    if not w:
        return None
    return (w[0] >> (16 * ((addr - base) // 2))) & 0xFFFF


def dispatch_boot(addr, rep):
    good = True
    bufs, _ = th.symbol(ENV, '_ZL19adc_channel_buffers')
    r = start(addr, 1)
    ok = r is not None and r['rc'] == 0
    th.line('L0-ADD-01.a', th.judge(ok), 'mode 1: 100 us HRTIM task with scheduler-managed dispatch starts',
            'createCritical returns 0', 'no acknowledgement' if r is None else f"returns {r['rc']}")
    if not ok:
        return False
    time.sleep(1.0)
    ends = buffer_ends(bufs)
    before = [halfword(e) for e in ends] if ends else None

    ok, ab = loss_case('L0-ADD-02.a', 'pin 35: no sample lost, dispatch on time', addr)
    good &= ok
    if ab:
        a, b = ab
        rate = (b['bg_count'] - a['bg_count']) / max(1e-3, (b['bg_ms'] - a['bg_ms']) / 1000.0)
        th.line('L0-ADD-07', 'INFO', 'background spins per second (CPU left by the task and the dispatch)',
                'recorded', f'{rate:.0f} /s')

    peeks, saws = [], []
    for _ in range(8):
        rr = report(addr)
        if rr:
            peeks.append(f32(rr['peek34']))
            saws.append(rr['saw'])
        time.sleep(0.15)
    distinct = len({round(p) for p in peeks})
    near = sum(1 for p, s in zip(peeks, saws) if abs(p - s) <= 150)
    ok = distinct >= 4 and near >= len(peeks) - 1
    th.line('L0-ADD-03', th.judge(ok), 'peek(34) follows the sawtooth, its buffer full and never fetched',
            '>= 4 distinct values in 8 reads, each within 150 LSB of the DAC level',
            f"{distinct} distinct; {near}/{len(peeks)} near the DAC level; e.g. peek {peeks[-1]:.0f} for DAC {saws[-1]}"
            if peeks else 'no SWD read')
    good &= ok

    after = [halfword(e) for e in ends] if ends else None
    for k, sub in enumerate('ab'):
        if ends is None or before is None or after is None or before[k] is None or after[k] is None:
            th.line(f'L0-ADD-04.{sub}', 'FAIL', f'nothing written past the end of pin 34 buffer {k}',
                    'halfword past the end unchanged', 'buffer not found over SWD (adc_channel_buffers)')
            good = False
            continue
        ok = before[k] == after[k]
        th.line(f'L0-ADD-04.{sub}', th.judge(ok), f'nothing written past the end of pin 34 buffer {k}',
                'halfword past the end unchanged over the test',
                f'{ends[k]:#010x}: {before[k]:#06x} -> {after[k]:#06x}')
        good &= ok

    th.write_word(addr + 4 * FIELDS.index('late_us'), LATE_US)
    th.write_word(addr + 4 * FIELDS.index('late_every'), LATE_EVERY)
    time.sleep(1.0)
    ok, _ = loss_case('L0-ADD-02.b', f'pin 35: no sample lost, a {LATE_US} us late dispatch every {LATE_EVERY} runs',
                      addr)
    good &= ok
    return good


def heap_boot(addr):
    rep = wait_boot(addr)
    if rep is None:
        th.line('L0-ADD-05', 'FAIL', 'mode 2: 5 ms TIM6 task with DMA buffers larger than the heap keeps running',
                f'{HEAP_TASK_US} us ±5 %, background alive', 'firmware did not boot after the reset')
        return False
    r = start(addr, 2)
    th.line('L0-ADD-01.b', th.judge(r is not None and r['rc'] == 0),
            'mode 2: 5 ms TIM6 task with scheduler-managed dispatch starts', 'createCritical returns 0',
            'no acknowledgement' if r is None else f"returns {r['rc']}")
    time.sleep(1.0)
    a, b = window(addr)
    if a is None:
        th.line('L0-ADD-05', 'FAIL', 'mode 2: 5 ms TIM6 task with DMA buffers larger than the heap keeps running',
                f'{HEAP_TASK_US} us ±5 %, background alive', 'no SWD read')
        return False
    runs = b['runs'] - a['runs']
    per = (b['run_ms'] - a['run_ms']) * 1000.0 / runs if runs else None
    alive = b['bg_ms'] > a['bg_ms']
    ok = per is not None and abs(per - HEAP_TASK_US) <= 0.05 * HEAP_TASK_US and alive
    th.line('L0-ADD-05', th.judge(ok), 'mode 2: 5 ms TIM6 task with DMA buffers larger than the heap keeps running',
            f'{HEAP_TASK_US} us ±5 %, background alive',
            ('task stopped' if per is None else f'{per:.0f} us ({runs} runs in {WINDOW_S:.0f} s)') +
            f", background {'alive' if alive else 'stopped'}, total runs {b['runs']}")
    p24, p34 = f32(b['peek24']), f32(b['peek34'])
    th.line('L0-ADD-06', 'INFO', 'mode 2: which ADCs give values', 'recorded',
            f"ADC1 pin 24: {'NO_VALUE (left out)' if p24 == NO_VALUE else f'{p24:.0f}'}; "
            f"ADC2 pin 34: {'NO_VALUE (left out)' if p34 == NO_VALUE else f'{p34:.0f}'}")
    return ok


def blocked(reason):
    for cid, what in (('L0-ADD-01', 'env ADD builds and flashes over the ST-Link'),
                      ('L0-ADD-02', 'no ADC sample lost under scheduler dispatch'),
                      ('L0-ADD-03', 'peek() follows an unfetched channel'),
                      ('L0-ADD-04', 'nothing written past a full channel buffer'),
                      ('L0-ADD-05', 'a task whose DMA buffers exceed the heap keeps running')):
        th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_add_')
    flash_log = os.path.join(out, 'target_data_dispatch_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-ADD-01', th.judge(rc == 0), 'env ADD builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        blocked('flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_add_report')
    if size != 4 * len(FIELDS):
        sys.exit(f'g_add_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
    rep = wait_boot(addr)
    if rep is None:
        th.line('L0-ADD-02.a', 'FAIL', 'pin 35: no sample lost, dispatch on time', '-', 'firmware did not boot')
        return 1
    good = dispatch_boot(addr, rep)
    th.openocd('reset run', check=False)  # one critical task per boot
    good &= heap_boot(addr)
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
