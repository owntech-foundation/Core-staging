#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-TIM: cost of the Core API hot-path calls and critical-task timing on the CUT, DWT cycles read over
##        SWD (Core-staging#37; method of Core2 apf_quadrature ADR 0005).
##
## env TIM (tests/firmware/timing) is flashed on the CUT and runs (b0) the bare critical task, (a) one hot-path
## call per critical-task run, (b) the critical task with the scheduler's data dispatch. The report g_tim_report
## (tests/firmware/timing/timing_probe.h) is read over SWD; every row gets mean / sigma / worst in core cycles.
##  - L0-TIM-01: env TIM builds, flashes, starts its critical task and finishes.
##  - L0-TIM-02.<call>: cost per call of each hot-path PWM and DataAPI call (N samples, interrupts masked).
##  - L0-TIM-03: critical-task period and jitter (entry to entry), INFO.
##  - L0-TIM-04.<row>: interrupt overhead: entry latency, exit latency, total, with and without the dispatch;
##    the dispatch itself as their difference.
##  - L0-TIM-05.<row>: interrupts held off before the critical task (period event to interrupt taken), and the
##    longest interruption of the background by anything else.
##  - L0-TIM-06: PA6 marker on the scope: BLOCKED (needs the MHO984; not written yet).
## Verdicts come from evaluate.py: INFO while no reference exists. Writes timing.json and timing.md into
## $CORE_TEST_OUT.
import argparse
import json
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON, HERE]
import target_harness as th  # noqa: E402
import evaluate as ev  # noqa: E402

ENV = 'TIM'
MAGIC = 0x434D4954  # "TIMC"
LAYOUT = 1
HEADER = ['magic', 'layout', 'state', 'error', 'core_hz', 'req_seq', 'ack_seq', 'req_repeats', 'req_task_runs',
          'task_period_us', 'probe_overhead', 'hrtim_ckpsc', 'runs', 'row_count', 'rc', 'pwm_period_cycles']
MAX_ROWS, ROW_BYTES, NAME_LEN = 40, 56, 24
STATES = ['WAITING', 'TASK_BARE', 'SWITCH', 'CALLS', 'TASK', 'DONE', 'ERROR']
DONE, ERROR = 5, 6
BOOT_TIMEOUT_S = 30.0
RUN_TIMEOUT_S = 120.0


def header(addr):
    w = th.read_words(addr, len(HEADER))
    if not w:
        return None
    h = dict(zip(HEADER, w))
    if h['rc'] >= 1 << 31:
        h['rc'] -= 1 << 32
    return h


def rows(addr, count):
    """@return {name: stats or None} in firmware order."""
    nwords = count * ROW_BYTES // 4
    w = th.read_words(addr + 4 * len(HEADER), nwords)
    if not w:
        return None
    raw = struct.pack(f'<{nwords}I', *w)
    out = {}
    for i in range(count):
        name, n, lo, hi, _pad, total, total_sq = struct.unpack_from(f'<{NAME_LEN}s4I2Q', raw, i * ROW_BYTES)
        name = name.split(b'\0', 1)[0].decode()
        out[name] = ev.stats(n, total, total_sq, lo, hi)
    return out


def wait_boot(addr):
    t0 = time.monotonic()
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        h = header(addr)
        # RAM keeps the previous boot until startup clears it: a fresh boot has req_seq 0 and state WAITING.
        if h and h['magic'] == MAGIC and h['layout'] == LAYOUT and h['req_seq'] == 0 and h['state'] == 0:
            return h
        time.sleep(0.5)
    return None


def wait_done(addr):
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < RUN_TIMEOUT_S:
        h = header(addr)
        if h:
            last = h
            if h['state'] in (DONE, ERROR):
                return h
        time.sleep(0.5)
    return last


def derived(run_rows):
    """@brief The scheduler's data dispatch: entry latency with it minus without it (means; sigma combined)."""
    a, b = run_rows.get('task0.entryLatency'), run_rows.get('task.entryLatency')
    if not a or not b:
        return None
    return {'n': min(a['n'], b['n']), 'mean': round(b['mean'] - a['mean'], 2),
            'sigma': round((a['sigma'] ** 2 + b['sigma'] ** 2) ** 0.5, 2),
            'min': max(0, b['min'] - a['min']), 'worst': max(0, b['worst'] - a['min'])}


def criterion(name):
    """@return the criterion id of a row (sub-ids carry no dot: record.md splits on the first one)."""
    sub = name.replace('.', '_')
    if name.startswith(('pwm.', 'data.')) and name != 'data.dispatch':
        return f'L0-TIM-02.{sub}'
    if name.endswith('.period'):
        return f'L0-TIM-03.{sub}'
    if name.endswith(('irqHeldOff', 'otherIrq')):
        return f'L0-TIM-05.{sub}'
    if name == 'probe.empty':
        return f'L0-TIM-02.{sub}'
    return f'L0-TIM-04.{sub}'


def blocked(reason):
    for cid, what in (('L0-TIM-01', 'env TIM builds, flashes and runs'),
                      ('L0-TIM-02', 'cost per call of the hot-path PWM and DataAPI calls'),
                      ('L0-TIM-03', 'critical-task period and jitter'),
                      ('L0-TIM-04', 'critical-task interrupt overhead'),
                      ('L0-TIM-05', 'interrupts held off before the critical task'),
                      ('L0-TIM-06', 'PA6 task marker on the scope')):
        th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true', help='flash and run on the CUT (else BLOCKED unless CORE_TARGET=1)')
    ap.add_argument('--repeats', type=int, default=100, help='samples per call in (a)')
    ap.add_argument('--task-runs', type=int, default=10000, help='critical-task runs timed in (b0) and (b)')
    a = ap.parse_args()
    if not a.run and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_timing_')
    flash_log = os.path.join(out, 'target_timing_flash.log')
    rc = th.flash(ENV, flash_log)
    if rc != 0:
        th.line('L0-TIM-01', 'FAIL', 'env TIM builds, flashes and runs', 'pio upload exit 0, state DONE',
                f'upload exit {rc}, see {os.path.basename(flash_log)}')
        blocked('flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_tim_report')
    if size != 4 * len(HEADER) + MAX_ROWS * ROW_BYTES:
        sys.exit(f'g_tim_report is {size} bytes, run.py expects {4 * len(HEADER) + MAX_ROWS * ROW_BYTES}')
    if wait_boot(addr) is None:
        th.line('L0-TIM-01', 'FAIL', 'env TIM builds, flashes and runs', 'state DONE', 'firmware did not boot')
        return 1
    th.write_word(addr + 4 * HEADER.index('req_repeats'), a.repeats)
    th.write_word(addr + 4 * HEADER.index('req_task_runs'), a.task_runs)
    th.write_word(addr + 4 * HEADER.index('req_seq'), 1)
    t0 = time.monotonic()
    h = wait_done(addr)
    ok = h is not None and h['state'] == DONE and h['rc'] == 0
    state = STATES[h['state']] if h and h['state'] < len(STATES) else '?'
    th.line('L0-TIM-01', th.judge(ok), 'env TIM builds, flashes and runs', 'upload exit 0, createCritical 0, state DONE',
            'no SWD read' if h is None else
            f"state {state}, createCritical {h['rc']}, {h['runs']} task runs, {time.monotonic() - t0:.1f} s")
    if not ok:
        return 1
    run_rows = rows(addr, h['row_count'])
    if run_rows is None:
        th.line('L0-TIM-02', 'FAIL', 'report rows read over SWD', 'read', 'no SWD read')
        return 1
    run_rows['data.dispatch'] = derived(run_rows)
    run = {'format': ev.FORMAT, 'firmware': f'env {ENV} at {th.git_state()}',
           'ref_sha': os.environ.get('TIMING_REF_SHA') or th.git_state(),
           'harness_sha': os.environ.get('TIMING_HARNESS_SHA'),
           'core_hz': h['core_hz'], 'task_period_us': h['task_period_us'], 'repeats': h['req_repeats'],
           'task_runs': h['req_task_runs'], 'probe_overhead': h['probe_overhead'],
           'pwm_period_cycles': h['pwm_period_cycles'], 'hrtim_ckpsc': h['hrtim_ckpsc'], 'rows': run_rows}
    with open(os.path.join(out, 'timing.json'), 'w') as f:
        json.dump(run, f, indent=1)

    key, ref_rows = ev.reference_rows(ev.load_reference())
    results = ev.evaluate(run, ref_rows)
    md = ev.table(run, results, f'against reference "{key}"' if key else 'no reference yet (INFO)')
    with open(os.path.join(out, 'timing.md'), 'w') as f:
        f.write(md)
    print(md, flush=True)
    hz = h['core_hz'] or 170000000
    nominal = h['task_period_us'] * hz / 1e6
    for name, row, ref, v, note in results:
        if row is None:
            th.line(criterion(name), 'INFO', name, 'mean / sigma / worst (cycles)', 'no sample')
            continue
        measured = (f"mean {row['mean']:.1f}, sigma {row['sigma']:.1f}, worst {row['worst']}, min {row['min']} "
                    f"cycles ({row['mean'] * 1e6 / hz:.3f} us, n {row['n']})")
        if name.endswith('.period'):
            measured += f"; error {(row['mean'] - nominal) / nominal * 1e6:+.0f} ppm of {nominal:.0f}"
        expected = (f"mean within reference {ref['mean']} ± (max(10 %, 3 sigma) + 50)"
                    if ref and name not in ev.INFO_ONLY else 'recorded (INFO until a reference exists)')
        th.line(criterion(name), v, name, expected, f'{measured}; {note}')
    th.line('L0-TIM-06', 'BLOCKED', 'PA6 task marker (high from task entry to exit) on the scope',
            'period and pulse width on the MHO984', 'needs scope: PA6 is driven, the SPOC read is not written yet')
    return 1 if any(v == 'FAIL' for *_, v, _n in results) else 0


if __name__ == '__main__':
    sys.exit(main())
