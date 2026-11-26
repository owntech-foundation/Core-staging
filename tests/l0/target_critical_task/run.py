#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-CTK: env CTK (tests/firmware/critical_task) is flashed on the CUT; task.createCritical() must refuse
##        the periods its interrupt source cannot produce and run the ones it accepts at their period (Core#174).
##
## HRTIM at 200 kHz (5 us): its 8-bit repetition counter reaches 256 periods, 1280 us. TIM6 at 10 MHz with a 16-bit
## auto-reload reaches 6553 us. One boot per source (only one critical task per boot): the out-of-range periods
## first, then the longest valid one, whose period is measured on-chip (runs against uptime) and on PA6 (toggled
## each run, routed by the MUX to scope CH1: frequency = 1 / (2 period)).
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON, os.path.join(COMMON, 'spoc')]
import target_harness as th  # noqa: E402

ENV = 'CTK'
MAGIC = 0x43544B31  # "CTK1"
LAYOUT = 1
FIELDS = ['magic', 'layout', 'req_source', 'req_period_us', 'req_seq', 'ack_seq', 'rc', 'started', 'runs',
          'stamp_runs', 'stamp_ms']
HRTIM, TIM6 = 0, 1
# (case, source, name, refused periods (the issue's example, then just past the limit), accepted period (the limit))
CASES = [('a', HRTIM, 'source_hrtim', [5000, 1285], 1280),
         ('b', TIM6, 'source_tim6', [10000, 6554], 6553)]
TOL = 0.01  # period, on-chip and on the scope
MEASURE_S = 4.0
BOOT_TIMEOUT_S = 30.0
CUT_PIN = 'PA6'


def criteria():
    for sub, _, name, refused, ok_p in CASES:
        for k, p in enumerate(refused):
            yield f'L0-CTK-02.{sub}{k + 1}', f'createCritical({p} us, {name}) is refused', 'returns -1'
        yield f'L0-CTK-03.{sub}', f'createCritical({ok_p} us, {name}) is accepted', 'returns 0'
        yield f'L0-CTK-04.{sub}', f'{name} task period on-chip ({ok_p} us)', f'{ok_p} us ±{TOL:.0%}'
        yield f'L0-CTK-05.{sub}', f'{name} task period on PA6 ({ok_p} us)', f'{ok_p} us ±{TOL:.0%}'


def blocked(reason):
    th.line('L0-CTK-01', 'BLOCKED', 'env CTK builds and flashes over the ST-Link', 'pio upload exit 0', reason)
    for cid, what, expected in criteria():
        th.line(cid, 'BLOCKED', what, expected, reason)
    return 0


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
        # After a reset RAM still holds the previous boot until startup clears it: a fresh boot has both
        # sequence counters at 0.
        if rep and rep['magic'] == MAGIC and rep['layout'] == LAYOUT and rep['req_seq'] == 0 and rep['ack_seq'] == 0:
            return rep
        time.sleep(0.5)
    return None


def request(addr, source, period, seq, timeout=2.0):
    th.write_word(addr + 4 * FIELDS.index('req_source'), source)
    th.write_word(addr + 4 * FIELDS.index('req_period_us'), period)
    th.write_word(addr + 4 * FIELDS.index('req_seq'), seq)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        rep = report(addr)
        if rep and rep['ack_seq'] == seq:
            return rep
        time.sleep(0.05)
    return None


def on_chip_period_us(addr):
    a = report(addr)
    time.sleep(MEASURE_S)
    b = report(addr)
    if not a or not b or b['stamp_runs'] == a['stamp_runs']:
        return None, 0
    runs = b['stamp_runs'] - a['stamp_runs']
    return (b['stamp_ms'] - a['stamp_ms']) * 1000.0 / runs, runs


def scope_period_us(scope, period_us):
    # Two toggles per period: a square wave at 1 / (2 period); about 4 of its cycles on screen.
    scope.set_timebase(8 * period_us * 1e-6 / 10)
    scope.run()
    time.sleep(1.5)
    vals = [v for v in (scope.measure('FREQuency', 1) for _ in range(5)) if v]
    if not vals:
        return None
    return 1e6 / (2 * statistics.median(vals))


def run_case(addr, scope, sub, source, name, refused, ok_p):
    rep = wait_boot(addr)
    if rep is None:
        for cid, what, expected in criteria():
            if cid.startswith(f'L0-CTK-0') and cid.split('.')[1].startswith(sub):
                th.line(cid, 'FAIL', what, expected, 'firmware did not boot')
        return False
    seq = rep['req_seq']
    good = True
    for k, p in enumerate(refused):
        seq += 1
        r = request(addr, source, p, seq)
        ok = r is not None and r['rc'] == -1
        th.line(f'L0-CTK-02.{sub}{k + 1}', th.judge(ok), f'createCritical({p} us, {name}) is refused', 'returns -1',
                'no acknowledgement' if r is None else f"returns {r['rc']}" + ('' if ok else ' (accepted)'))
        good &= ok
        if r is not None and r['rc'] == 0:
            # Accepted although out of range (before the fix): that is the task now running. Record its rate.
            per, runs = on_chip_period_us(addr)
            th.line(f'L0-CTK-06.{sub}{k + 1}', 'INFO', f'period actually run after createCritical({p} us, {name})',
                    'recorded', 'no runs' if per is None else f'{per:.1f} us ({runs} runs in {MEASURE_S:.0f} s)')
    seq += 1
    r = request(addr, source, ok_p, seq)
    ok = r is not None and r['rc'] == 0
    note = ''
    if r is not None and r['rc'] != 0 and r['started']:
        note = ' (a task accepted earlier in this boot still runs)'
    th.line(f'L0-CTK-03.{sub}', th.judge(ok), f'createCritical({ok_p} us, {name}) is accepted', 'returns 0',
            'no acknowledgement' if r is None else f"returns {r['rc']}{note}")
    good &= ok
    per, runs = on_chip_period_us(addr)
    ok_chip = ok and per is not None and abs(per - ok_p) <= TOL * ok_p
    th.line(f'L0-CTK-04.{sub}', th.judge(ok_chip), f'{name} task period on-chip ({ok_p} us)',
            f'{ok_p} us ±{TOL:.0%}', 'no runs' if per is None else f'{per:.1f} us ({runs} runs in {MEASURE_S:.0f} s)')
    sp = scope_period_us(scope, ok_p)
    ok_scope = ok and sp is not None and abs(sp - ok_p) <= TOL * ok_p
    th.line(f'L0-CTK-05.{sub}', th.judge(ok_scope), f'{name} task period on PA6 ({ok_p} us)',
            f'{ok_p} us ±{TOL:.0%}', 'invalid' if sp is None else f'{sp:.1f} us (median of 5 FREQuency readings)')
    return good and ok_chip and ok_scope


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT, needs the SPOC bench)')
    import rigol_mho
    from spoc import boards
    from mux_controller import MuxController
    mux_port = boards.find('mux')
    if not mux_port:
        return blocked('MUX Spin not found (bash tests/common/spoc_preflight.sh)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_ctk_')
    flash_log = os.path.join(out, 'target_critical_task_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-CTK-01', th.judge(rc == 0), 'env CTK builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        blocked('flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_ctk_report')
    if size != 4 * len(FIELDS):
        sys.exit(f'g_ctk_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
    scope = rigol_mho.RigolScope(rigol_mho.open_transport())
    scope.setup_channel(1, scale=1.0, offset=-1.65, probe=10)
    scope.set_acquire('NORMal')
    scope.set_edge_trigger(1, 1.65, sweep='AUTO')
    mux = MuxController(mux_port)
    mux.connect()
    good = True
    try:
        mux.disable_all()
        mux.route('ch1', CUT_PIN)
        for i, (sub, source, name, refused, ok_p) in enumerate(CASES):
            if i > 0:
                th.openocd('reset run', check=False)  # one critical task per boot
            good &= run_case(addr, scope, sub, source, name, refused, ok_p)
    finally:
        mux.disable_all()
        mux.close()
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
