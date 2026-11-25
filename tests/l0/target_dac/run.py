#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-DAC: env DAC (tests/firmware/dac) is flashed on the CUT; for each level the host asks over SWD,
##        communication.analog.setAnalogCommValue() must put it in DAC2's output register and on PA6.
##
## PA6 reaches scope CH1 through the MUX (mux1 input 6). Levels 512..3584 keep inside the output buffer's linear
## range (about 0.2 V to VREF+ - 0.2 V); level 0 is recorded only. Expected voltage: n * 2.048 V / 4096 (VREFBUF).
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON, os.path.join(COMMON, 'spoc')]
import target_harness as th  # noqa: E402

ENV = 'DAC'
MAGIC = 0x44414331  # "DAC1", tests/firmware/dac/main.cpp
LAYOUT = 1
FIELDS = ['magic', 'layout', 'cmd', 'cmd_seq', 'ack_seq', 'applied', 'ticks']
LEVELS = [512, 1024, 2048, 3072, 3584]
VREF = 2.048  # VREFBUF: the DAC and the ADCs share it (DacHAL.cpp VREF)
# Scope DC gain through the x10 probe (about +3 % measured on the bench, 2026-11-26) plus the buffered DAC's offset:
# 30 mV + 3 % of the expected voltage.
TOL_ABS_V, TOL_REL = 0.030, 0.03


def tol(v):
    return TOL_ABS_V + TOL_REL * v
DAC2_DOR1 = 0x50000C2C  # STM32G474 DAC2 base 0x50000C00, DOR1 offset 0x2C
CUT_PIN = 'PA6'
BOOT_TIMEOUT_S = 30.0
N = 5
SUB = 'abcde'


def blocked(reason):
    for cid, what in (('L0-DAC-01', 'env DAC builds and flashes over the ST-Link'),
                      ('L0-DAC-02', 'report struct read over SWD')):
        th.line(cid, 'BLOCKED', what, '-', reason)
    for i, n in enumerate(LEVELS):
        th.line(f'L0-DAC-03.{SUB[i]}', 'BLOCKED', f'DAC2 output register at level {n}', f'{n}', reason)
        th.line(f'L0-DAC-04.{SUB[i]}', 'BLOCKED', f'PA6 mean voltage at level {n}', f'{n * VREF / 4096:.3f} V ±{tol(n * VREF / 4096) * 1000:.0f} mV', reason)
    return 0


def command(addr, level, seq, timeout=2.0):
    """Writes cmd then cmd_seq; returns the report once ack_seq == seq, or None."""
    th.write_word(addr + 4 * FIELDS.index('cmd'), level)
    th.write_word(addr + 4 * FIELDS.index('cmd_seq'), seq)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        words = th.read_words(addr, len(FIELDS))
        if words:
            rep = dict(zip(FIELDS, words))
            if rep['ack_seq'] == seq:
                return rep
        time.sleep(0.05)
    return None


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT, needs the SPOC bench)')
    import rigol_mho
    from spoc import boards
    from mux_controller import MuxController
    mux_port = boards.find('mux')
    if not mux_port:
        return blocked('MUX Spin not found (bash tests/common/spoc_preflight.sh)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_dac_')
    flash_log = os.path.join(out, 'target_dac_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-DAC-01', th.judge(rc == 0), 'env DAC builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        return blocked('flash failed') or 1
    addr, size = th.symbol(ENV, 'g_dac_report')
    if size != 4 * len(FIELDS):
        sys.exit(f'g_dac_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
    t0 = time.monotonic()
    rep = None
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        words = th.read_words(addr, len(FIELDS))
        rep = dict(zip(FIELDS, words)) if words else None
        if rep and rep['magic'] == MAGIC:
            break
        time.sleep(0.5)
    ok = bool(rep) and rep['magic'] == MAGIC and rep['layout'] == LAYOUT
    th.line('L0-DAC-02', th.judge(ok), 'report struct read over SWD', f'magic {MAGIC:#010x}, layout {LAYOUT}',
            'no SWD read' if not rep else f"magic {rep['magic']:#010x}, layout {rep['layout']}, "
                                          f"{time.monotonic() - t0:.1f} s after the upload")
    if not ok:
        return 1

    scope = rigol_mho.RigolScope(rigol_mho.open_transport())
    mux = MuxController(mux_port)
    mux.connect()
    try:
        return levels(addr, rep, scope, mux)
    finally:
        mux.disable_all()
        mux.close()


def levels(addr, rep, scope, mux):
    """Each level: command it, read DAC2's output register over SWD, measure PA6 on CH1. @return exit code."""
    mux.disable_all()
    mux.route('ch1', CUT_PIN)
    scope.setup_channel(1, scale=0.5, offset=-1.0, probe=10)
    scope.set_acquire('NORMal')
    scope.set_timebase(1e-3)
    scope.set_edge_trigger(1, 1.0, sweep='AUTO')
    scope.run()
    time.sleep(1.5)

    failed = False
    pts = []
    seq = rep['cmd_seq']
    for i, n in enumerate(LEVELS + [0]):
        seq += 1
        got = command(addr, n, seq)
        dor = th.read_words(DAC2_DOR1, 1)
        dor = dor[0] & 0xFFF if dor else None
        time.sleep(1.0)
        vals = [v for v in (scope.measure('VAVG', 1) for _ in range(N)) if v is not None]
        v = statistics.median(vals) if vals else None
        expected = n * VREF / 4096
        if n == 0:
            th.line('L0-DAC-05', 'INFO', 'PA6 mean voltage at level 0 (below the buffer\'s linear range)', 'recorded',
                    'not measured' if v is None else f'{v:.3f} V, DOR {dor}')
            continue
        ok_dor = got is not None and dor == n
        th.line(f'L0-DAC-03.{SUB[i]}', th.judge(ok_dor), f'DAC2 output register at level {n}', f'{n}',
                'no acknowledgement from the firmware' if got is None else f'DOR {dor}')
        ok_v = v is not None and abs(v - expected) <= tol(expected)
        th.line(f'L0-DAC-04.{SUB[i]}', th.judge(ok_v), f'PA6 mean voltage at level {n}',
                f'{expected:.3f} V ±{tol(expected) * 1000:.0f} mV',
                'invalid' if v is None else f'{v:.3f} V ({(v - expected) * 1000:+.0f} mV; {len(vals)} readings, '
                                            f'spread {(max(vals) - min(vals)) * 1000:.0f} mV)')
        failed |= not (ok_dor and ok_v)
        if v is not None:
            pts.append((n, v))
    if len(pts) >= 2:
        xs, ys = zip(*pts)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        slope = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x in xs)
        offset = my - slope * mx
        th.line('L0-DAC-06', 'INFO', 'PA6 voltage against level: least-squares fit', 'recorded',
                f'{slope * 4096:.3f} V full scale (ideal {VREF:.3f}), offset {offset * 1000:+.0f} mV')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
