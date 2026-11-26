#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-HRI: env HRI (tests/firmware/hrtim_reinit) is flashed on the CUT; an up-down PWMA must keep its
##        switching frequency each time initUnit() runs again (Core#172).
##
## PWMA is up-down at 200 kHz, duty 0.5, started on PA8 (routed by the MUX to scope CH1). Its counter runs at twice
## the switching frequency, so PERxR of timer A is half the master period MPER. The host asks for REINITS further
## initUnit(PWMA) calls over SWD; after each, PERxR must equal its boot value and PA8 must still switch at 200 kHz.
## Before the fix each re-init doubles the frequency until _period_ckpsc()'s assert aborts: a re-init that does not
## return is recorded (L0-HRI-06) and the criteria it did not reach fail, rather than the test hanging.
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON, os.path.join(COMMON, 'spoc')]
import target_harness as th  # noqa: E402

ENV = 'HRI'
MAGIC = 0x48524931  # "HRI1"
LAYOUT = 1
FIELDS = ['magic', 'layout', 'req_seq', 'ack_seq', 'inits_started', 'inits_done', 'per_a', 'mper', 'period_drv',
          'stamp_ms']
F_SW = 200000.0
REINITS = 10  # before the fix, 200 kHz doubles each time: the assert aborts after about 8
TOL = 0.01  # switching frequency on the scope
ACK_TIMEOUT_S = 2.0
BOOT_TIMEOUT_S = 30.0
CUT_PIN = 'PA8'


def criteria():
    yield 'L0-HRI-02', 'boot init: PERxR(A) is half the master period (up-down)', 'MPER / 2 ±1'
    yield 'L0-HRI-03', 'boot init: PA8 switching frequency', f'{F_SW / 1e3:.0f} kHz ±{TOL:.0%}'
    for k in range(1, REINITS + 1):
        yield f'L0-HRI-04.{k:02d}', f're-init {k}: initUnit(PWMA) returns, PERxR(A) unchanged', 'boot PERxR'
        yield f'L0-HRI-05.{k:02d}', f're-init {k}: PA8 switching frequency', f'{F_SW / 1e3:.0f} kHz ±{TOL:.0%}'


def blocked(reason):
    th.line('L0-HRI-01', 'BLOCKED', 'env HRI builds and flashes over the ST-Link', 'pio upload exit 0', reason)
    for cid, what, expected in criteria():
        th.line(cid, 'BLOCKED', what, expected, reason)
    return 0


def report(addr):
    words = th.read_words(addr, len(FIELDS))
    return dict(zip(FIELDS, words)) if words else None


def wait_boot(addr):
    t0 = time.monotonic()
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        rep = report(addr)
        # After a reset RAM still holds the previous boot until startup clears it: a fresh boot has req_seq at 0
        # and its boot init done.
        if (rep and rep['magic'] == MAGIC and rep['layout'] == LAYOUT and rep['req_seq'] == 0
                and rep['ack_seq'] == 0 and rep['inits_done'] == 1):
            return rep
        time.sleep(0.5)
    return None


def reinit(addr, seq):
    th.write_word(addr + 4 * FIELDS.index('req_seq'), seq)
    t0 = time.monotonic()
    while time.monotonic() - t0 < ACK_TIMEOUT_S:
        rep = report(addr)
        if rep and rep['ack_seq'] == seq:
            return rep
        time.sleep(0.05)
    return None


def stopped_state(addr):
    """Why a re-init was not acknowledged: still running, halted in a re-init, or rebooted."""
    a = report(addr)
    time.sleep(0.5)
    b = report(addr)
    if not a or not b:
        return 'report unreadable over SWD'
    if b['req_seq'] == 0 and b['inits_done'] <= 1:
        return f"the CUT rebooted (inits_started {b['inits_started']}, inits_done {b['inits_done']} after reboot)"
    running = b['stamp_ms'] != a['stamp_ms']
    return (f"inits_started {b['inits_started']}, inits_done {b['inits_done']}, last PERxR(A) {b['per_a']}, "
            f"background task {'still runs' if running else 'stopped (fatal error, e.g. the period assert)'}")


def scope_freq(scope):
    # About 4 cycles of 200 kHz on screen; a doubled frequency (before the fix) still shows enough cycles.
    scope.set_timebase(4 / F_SW / 10)
    scope.run()
    time.sleep(1.0)
    vals = [v for v in (scope.measure('FREQuency', 1) for _ in range(5)) if v]
    return statistics.median(vals) if vals else None


def judge_freq(cid, what, scope):
    f = scope_freq(scope)
    ok = f is not None and abs(f - F_SW) <= TOL * F_SW
    th.line(cid, th.judge(ok), what, f'{F_SW / 1e3:.0f} kHz ±{TOL:.0%}',
            'invalid' if f is None else f'{f / 1e3:.2f} kHz (median of 5 FREQuency readings)')
    return ok


def run(addr, scope):
    rep = wait_boot(addr)
    if rep is None:
        for cid, what, expected in criteria():
            th.line(cid, 'FAIL', what, expected, 'firmware did not boot')
        return False
    per0, mper = rep['per_a'], rep['mper']
    ok = abs(per0 - mper / 2) <= 1
    th.line('L0-HRI-02', th.judge(ok), 'boot init: PERxR(A) is half the master period (up-down)', 'MPER / 2 ±1',
            f"PERxR(A) {per0}, MPER {mper}, driver period {rep['period_drv']}")
    good = ok
    good &= judge_freq('L0-HRI-03', 'boot init: PA8 switching frequency', scope)
    for k in range(1, REINITS + 1):
        r = reinit(addr, k)
        if r is None:
            why = stopped_state(addr)
            th.line('L0-HRI-06', 'INFO', 'where the firmware stopped', 'recorded', f're-init {k} not acknowledged: {why}')
            for j in range(k, REINITS + 1):
                th.line(f'L0-HRI-04.{j:02d}', 'FAIL', f're-init {j}: initUnit(PWMA) returns, PERxR(A) unchanged',
                        'boot PERxR', f'not reached: re-init {k} did not return')
                th.line(f'L0-HRI-05.{j:02d}', 'FAIL', f're-init {j}: PA8 switching frequency',
                        f'{F_SW / 1e3:.0f} kHz ±{TOL:.0%}', f'not reached: re-init {k} did not return')
            return False
        ok = r['per_a'] == per0
        th.line(f'L0-HRI-04.{k:02d}', th.judge(ok), f're-init {k}: initUnit(PWMA) returns, PERxR(A) unchanged',
                f'boot PERxR {per0}', f"PERxR(A) {r['per_a']}, MPER {r['mper']}, driver period {r['period_drv']}")
        good &= ok
        good &= judge_freq(f'L0-HRI-05.{k:02d}', f're-init {k}: PA8 switching frequency', scope)
    return good


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT, needs the SPOC bench)')
    import rigol_mho
    from spoc import boards
    from mux_controller import MuxController
    mux_port = boards.find('mux')
    if not mux_port:
        return blocked('MUX Spin not found (bash tests/common/spoc_preflight.sh)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_hri_')
    flash_log = os.path.join(out, 'target_hrtim_reinit_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-HRI-01', th.judge(rc == 0), 'env HRI builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        blocked('flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_hri_report')
    if size != 4 * len(FIELDS):
        sys.exit(f'g_hri_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
    scope = rigol_mho.RigolScope(rigol_mho.open_transport())
    scope.setup_channel(1, scale=1.0, offset=-1.65, probe=10)
    scope.set_acquire('NORMal')
    scope.set_edge_trigger(1, 1.65, sweep='AUTO')
    mux = MuxController(mux_port)
    mux.connect()
    try:
        mux.disable_all()
        mux.route('ch1', CUT_PIN)
        good = run(addr, scope)
    finally:
        mux.disable_all()
        mux.close()
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
