#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-SAF: env SAF (tests/firmware/safety_report) is flashed on the CUT; when one Twist channel trips, the
##        Safety error thread must print "true" for that channel and "false" for the five others (Core-staging#47).
##
## The firmware trips each channel in turn (minimum threshold above any reading) and copies the error thread's
## console blocks into RAM through a printk hook; the host reads the first block printed entirely after each trip
## over SWD. The expected flags come from the channel the host asked to trip, not from the Safety API.
import os
import re
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON]
import target_harness as th  # noqa: E402

ENV = 'SAF'
MAGIC = 0x53414631  # "SAF1", tests/firmware/safety_report/main.cpp
LAYOUT = 1
FIELDS = ['magic', 'layout', 'cmd', 'cmd_seq', 'ack_seq', 'rc', 'tripped', 'errors', 'runs', 'blocks',
          'blocks_at_trip', 'chars', 'snap_seq'] + [f'peek{i}' for i in range(6)]
BLOCK_SIZE = 256
# Labels as the error thread prints them, in the firmware's CHANNELS order.
LABELS = ['I1', 'V1', 'I2', 'V2', 'Ihigh', 'Vhigh']
SUB = 'abcdef'
BOOT_TIMEOUT_S = 30.0
ACK_TIMEOUT_S = 6.0
BLOCK_TIMEOUT_S = 3.0


def blocked(reason):
    th.line('L0-SAF-01', 'BLOCKED', 'env SAF builds and flashes over the ST-Link', 'pio upload exit 0', reason)
    th.line('L0-SAF-02', 'BLOCKED', 'report struct read over SWD', f'magic {MAGIC:#010x}, rc 0', reason)
    for i, lab in enumerate(LABELS):
        th.line(f'L0-SAF-03.{SUB[i]}', 'BLOCKED', f'{lab} trips: error thread report', expected(i), reason)
    return 0


def expected(i):
    return f'{LABELS[i]} true, others false'


def report(addr):
    words = th.read_words(addr, len(FIELDS))
    if not words:
        return None
    rep = dict(zip(FIELDS, words))
    if rep['rc'] >= 1 << 31:
        rep['rc'] -= 1 << 32
    return rep


def read_block(addr):
    """The snapshot block (frozen once taken), as text; None on an SWD error."""
    words = th.read_words(addr + 4 * len(FIELDS), BLOCK_SIZE // 4)
    if not words:
        return None
    return struct.pack(f'<{len(words)}I', *words).split(b'\0', 1)[0].decode('ascii', 'replace')


def parse(text):
    """{label: True/False} from 'problem with <label> : true|false' lines."""
    return {m.group(1): m.group(2) == 'true' for m in re.finditer(r'problem with (\w+) : (true|false)', text)}


def floats(rep):
    return ', '.join(f'{lab} {struct.unpack("<f", struct.pack("<I", rep[f"peek{i}"]))[0]:.3g}'
                     for i, lab in enumerate(LABELS))


def main():
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target (reflashes the CUT)')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_safety_report_')
    flash_log = os.path.join(out, 'target_safety_report_flash.log')
    rc = th.flash(ENV, flash_log)
    th.line('L0-SAF-01', th.judge(rc == 0), 'env SAF builds and flashes over the ST-Link', 'pio upload exit 0',
            f'exit {rc}, see {os.path.basename(flash_log)}')
    if rc != 0:
        return blocked('flash failed') or 1
    addr, size = th.symbol(ENV, 'g_saf_report')
    if size != 4 * len(FIELDS) + BLOCK_SIZE:
        sys.exit(f'g_saf_report is {size} bytes, run.py expects {4 * len(FIELDS) + BLOCK_SIZE}')
    t0 = time.monotonic()
    rep = None
    while time.monotonic() - t0 < BOOT_TIMEOUT_S:
        rep = report(addr)
        if rep and rep['magic'] == MAGIC:
            break
        time.sleep(0.5)
    ok = bool(rep) and rep['magic'] == MAGIC and rep['layout'] == LAYOUT and rep['rc'] == 0
    th.line('L0-SAF-02', th.judge(ok), 'report struct read over SWD', f'magic {MAGIC:#010x}, layout {LAYOUT}, rc 0',
            'no SWD read' if not rep else f"magic {rep['magic']:#010x}, layout {rep['layout']}, rc {rep['rc']}, "
                                          f"{rep['runs']} task runs; readings {floats(rep)}")
    if not ok:
        return 1

    failed = False
    seq = rep['cmd_seq']
    for i, lab in enumerate(LABELS):
        seq += 1
        cid = f'L0-SAF-03.{SUB[i]}'
        th.write_word(addr + 4 * FIELDS.index('cmd'), i)
        th.write_word(addr + 4 * FIELDS.index('cmd_seq'), seq)
        t0 = time.monotonic()
        rep = None
        while time.monotonic() - t0 < ACK_TIMEOUT_S:
            rep = report(addr)
            if rep and rep['ack_seq'] == seq:
                break
            time.sleep(0.1)
        if not rep or rep['ack_seq'] != seq or not rep['tripped']:
            th.line(cid, 'FAIL', f'{lab} trips: error thread report', expected(i),
                    'no acknowledgement' if not rep or rep['ack_seq'] != seq else
                    f"Safety API did not flag {lab} alone: errors {rep['errors']:#04x}; readings {floats(rep)}")
            failed = True
            continue
        t0 = time.monotonic()
        now = rep
        while time.monotonic() - t0 < BLOCK_TIMEOUT_S:
            now = report(addr) or now
            if now['snap_seq'] == seq:
                break
            time.sleep(0.1)
        text = read_block(addr) if now['snap_seq'] == seq else None
        if text is None:
            th.line(cid, 'FAIL', f'{lab} trips: error thread report', expected(i),
                    f"no error block printed whole after the trip ({now['blocks'] - rep['blocks_at_trip']} blocks "
                    f"since, {now['chars']} printk characters seen)")
            failed = True
            continue
        got = parse(text)
        want = {l: l == lab for l in LABELS}
        ok = got == want
        shown = ', '.join(f'{l} {"true" if got[l] else "false"}' for l in LABELS if l in got)
        th.line(cid, th.judge(ok), f'{lab} trips: error thread report', expected(i), shown or repr(text))
        failed |= not ok
        with open(os.path.join(out, f'target_safety_report_{lab}.txt'), 'w') as f:
            f.write(text)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
