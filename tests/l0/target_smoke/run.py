#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-TGT: env SMOKE is built and flashed over the ST-Link, then its report struct is read over SWD
##        without halting the CPU (tests/firmware/smoke). Proves the on-target plumbing every on-target test uses.
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'common'))
import target_harness as th  # noqa: E402

ENV = 'SMOKE'
MAGIC = 0x534D4B31  # "SMK1", tests/firmware/smoke/main.cpp
LAYOUT = 1
FIELDS = ['magic', 'layout', 'ticks', 'uptime_ms']
WAIT_S = 2.0
BOOT_TIMEOUT_S = 30.0
MIN_TICKS = 50  # 10 ms task: 200 expected in 2 s; a quarter rate still passes
CRITERIA = [('L0-TGT-01', 'env SMOKE builds and flashes over the ST-Link', 'pio upload exit 0'),
            ('L0-TGT-02', 'report struct read over SWD', f'magic {MAGIC:#010x}, layout {LAYOUT}, within {BOOT_TIMEOUT_S:.0f} s'),
            ('L0-TGT-03', 'firmware runs: background task advances', f'>= {MIN_TICKS} ticks in {WAIT_S:.0f} s')]


def result(i, verdict, measured):
    cid, what, expected = CRITERIA[i]
    th.line(cid, verdict, what, expected, measured)


def blocked(reason):
    for cid, what, expected in CRITERIA:
        th.line(cid, 'BLOCKED', what, expected, reason)
    return 0


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the Spin on the ST-Link)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_smoke_')
    flash_log = os.path.join(out, 'target_smoke_flash.log')
    rc = th.flash(ENV, flash_log)
    result(0, th.judge(rc == 0), f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        result(1, 'BLOCKED', 'flash failed')
        result(2, 'BLOCKED', 'flash failed')
        return 1
    addr, size = th.symbol(ENV, 'g_smoke_report')
    if size != 4 * len(FIELDS):
        sys.exit(f'g_smoke_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
    # MCUboot checks (and after an upload, copies) the image, then Zephyr starts: about 2 s on a Spin.
    t0 = time.monotonic()
    rep = None
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        words = th.read_words(addr, len(FIELDS))
        rep = dict(zip(FIELDS, words)) if words else None
        if rep is not None and rep['magic'] == MAGIC:
            break
        time.sleep(0.5)
    boot_s = time.monotonic() - t0
    ok = rep is not None and rep['magic'] == MAGIC and rep['layout'] == LAYOUT
    got = ('no SWD read' if rep is None else
           f"magic {rep['magic']:#010x}, layout {rep['layout']} at {addr:#010x}, {boot_s:.1f} s after the upload")
    result(1, th.judge(ok), got)
    if not ok:
        result(2, 'BLOCKED', 'no report to read')
        return 1
    time.sleep(WAIT_S)
    second = th.read_words(addr, len(FIELDS))
    later = dict(zip(FIELDS, second)) if second else None
    ticks = None if later is None else later['ticks'] - rep['ticks']
    ms = None if later is None else later['uptime_ms'] - rep['uptime_ms']
    ok = ticks is not None and ticks >= MIN_TICKS
    result(2, th.judge(ok), 'no SWD read' if ticks is None else f'{ticks} ticks, uptime +{ms} ms')
    idc = th.idcode()
    th.line('L0-TGT-04', 'INFO', 'MCU DBGMCU_IDCODE', '-', f'{idc:#010x}' if idc is not None else 'not read')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
