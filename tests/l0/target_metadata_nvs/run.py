#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.py
## @brief L0-NVS: the MetaData / NVS module of the API regression suite (Core-staging#42; tiers and depth: #35).
##        The 15 spin.metaData calls, the conversion-parameter store/retrieve paths (spin.data, shield.sensors) and
##        the nvs_storage version/free-space/clear calls, at effect depth: return codes and the bytes read back, in
##        the same boot, after an ST-Link reset and after a reflash (another firmware, then this one again).
##
## env NVS (tests/firmware/metadata_nvs) is a step executor: the host writes a step and its arguments over SWD and
## the firmware calls the API and publishes the return values and the bytes a getter wrote. Expected values come from
## docs/spin_metaDataAPI.md (field lengths, return codes, "survives a reset or a firmware reflash", clearAllMetaData
## leaves other modules' data), DataAPI.h / Sensors.h (store/retrieve return codes) and nvs_storage.h; never from the
## driver's own code.
##
## The CUT's NVS partition (0x0807F000, 4 kB: sensor calibration, safety thresholds, metadata) is shared with the
## other tests and with whoever uses the CUT. The module backs it up over SWD before writing anything and writes the
## same bytes back at the end (L0-NVS-13), whatever happened in between; the backup stays in the campaign folder.
## Values written carry a per-run tag, so a read-back can only pass on what this run wrote.
## Nightly tier: the module builds and flashes two firmwares (NVS, SMOKE) and flashes NVS twice.
import os
import random
import struct
import sys
import tempfile
import time
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(HERE, '..', '..', 'common')
sys.path[:0] = [COMMON]
import target_harness as th  # noqa: E402

ENV = 'NVS'
OTHER_ENV = 'SMOKE'
SMOKE_MAGIC = 0x534D4B31  # "SMK1", tests/firmware/smoke/main.cpp
MAGIC = 0x4E565331  # "NVS1", tests/firmware/metadata_nvs/main.cpp
LAYOUT = 1
N_RET = 8
N_BYTES = 32
FILL = 0xA5
FIELDS = (['magic', 'layout', 'boot_count', 'ack', 'busy', 'step', 'err'] + [f'ret{i}' for i in range(N_RET)]
          + [f'out{i}' for i in range(N_BYTES // 4)] + ['stamp_ms'])
STEP = dict(SET_SPIN_SERIAL=1, GET_SPIN_SERIAL=2, SET_SHIELD_SERIAL=3, GET_SHIELD_SERIAL=4, SET_SPIN_VERSION=5,
            GET_SPIN_VERSION=6, SET_SHIELD_VERSION=7, GET_SHIELD_VERSION=8, SET_SPIN_PASSWORD=9, GET_SPIN_PASSWORD=10,
            SET_SHIELD_PASSWORD=11, GET_SHIELD_PASSWORD=12, SET_EXTRA=13, GET_EXTRA=14, CLEAR_META=15, NVS_INFO=20,
            NVS_CLEAR_ALL=21, NVS_STORE=22, NVS_RETRIEVE=23, DATA_ENABLE=30, DATA_SET_LINEAR=31, DATA_STORE=32, DATA_RETRIEVE=33, DATA_GET=34,
            SENS_ENABLE=40, SENS_SET_LINEAR=41, SENS_STORE=42, SENS_RETRIEVE=43, SENS_GET=44)

# docs/spin_metaDataAPI.md (MetaDataAPI.h constants)
SERIAL_LEN = 13
PASSWORD_LEN = 10
EXTRA_COUNT = 5
EXTRA_MAX = 16
# Fixed-length text fields: (name, set step, get step, length)
TEXT_FIELDS = [('SpinSerialNumber', 'SET_SPIN_SERIAL', 'GET_SPIN_SERIAL', SERIAL_LEN),
               ('ShieldSerialNumber', 'SET_SHIELD_SERIAL', 'GET_SHIELD_SERIAL', SERIAL_LEN),
               ('SpinPassword', 'SET_SPIN_PASSWORD', 'GET_SPIN_PASSWORD', PASSWORD_LEN),
               ('ShieldPassword', 'SET_SHIELD_PASSWORD', 'GET_SHIELD_PASSWORD', PASSWORD_LEN)]
VERSION_FIELDS = [('SpinVersion', 'SET_SPIN_VERSION', 'GET_SPIN_VERSION'),
                  ('ShieldVersion', 'SET_SHIELD_VERSION', 'GET_SHIELD_VERSION')]
EXTRA_LENGTHS = [1, 7, 16, 5, 3]  # one per slot, the maximum included

NVS_ADDR = 0x0807F000  # zephyr/boards/owntech/spin/spin.dts storage_partition
NVS_SIZE = 0x1000
DATA_PIN = 51          # PA2: ADC1 channel 3, not a Twist sensor
ADC_1 = 1
NOT_ADC_PIN = 3        # a Spin GND pin: no ADC channel
DOC_PIN_NOT_FOUND = -5000  # DataAPI.h store/retrieveConversionParametersFromMemory "-5000: pin not found"
V_HIGH_DT_GAIN = 0x3CF57710  # twist_v1_4_2.overlay default-gain of V_HIGH (offset 0)

BOOT_TIMEOUT_S = 30.0

CASES = [
    ('L0-NVS-01', 'env NVS builds, flashes and answers; the CUT\'s NVS partition backed up'),
    ('L0-NVS-02', 'nvs_storage versions and free space'),
    ('L0-NVS-03', 'metaData argument errors: -2 (size) and -3 (slot), nothing written'),
    ('L0-NVS-04', 'clearAllMetaData: every field reads "nothing stored" (-1)'),
    ('L0-NVS-05', 'every metaData setter, then its getter: return codes and bytes read back'),
    ('L0-NVS-06', 'conversion parameters: store, change, retrieve (spin.data and shield.sensors)'),
    ('L0-NVS-07', 'NVS free space falls when a new entry is written'),
    ('L0-NVS-08', 'after an ST-Link reset: metaData and conversion parameters survive'),
    ('L0-NVS-09', 'after a reflash (another firmware, then this one): metaData and parameters survive'),
    ('L0-NVS-10', 'clearAllMetaData leaves other modules\' NVS data (conversion parameters)'),
    ('L0-NVS-11', 'nvs_storage_clear_all_stored_data: everything gone, NVS usable again'),
    ('L0-NVS-12', 'store/retrieve on a pin with no ADC channel: the documented error'),
    ('L0-NVS-13', 'the CUT\'s NVS partition written back byte for byte'),
    ('L0-NVS-14', 'module duration'),
    ('L0-NVS-15', 'a store on the CUT\'s NVS as found (before the module clears it)'),
    ('L0-NVS-16', 'nvs_storage_store_data / retrieve_data called directly'),
]


def f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def bits2f(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xFFFFFFFF))[0]


def s32(u):
    return u - (1 << 32) if u >= 1 << 31 else u


def line(cid, ok, what, expected, measured):
    verdict = ok if isinstance(ok, str) else th.judge(bool(ok))
    th.line(cid, verdict, what, expected, measured)
    return verdict in ('PASS', 'INFO')


class Bench:
    def __init__(self):
        self.rep_addr, size = th.symbol(ENV, 'g_nvs_report')
        if size != 4 * len(FIELDS):
            sys.exit(f'g_nvs_report is {size} bytes, run.py expects {4 * len(FIELDS)}')
        self.cmd_addr, _ = th.symbol(ENV, 'g_nvs_cmd')
        self.args_addr, _ = th.symbol(ENV, 'g_nvs_args')
        self.in_addr, _ = th.symbol(ENV, 'g_nvs_in')
        self.seq = 0
        self.steps = 0

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
        """ST-Link reset, as the harness does (tests/common/target_harness.py)."""
        th.openocd('reset run', check=False)
        time.sleep(0.5)
        return self.wait_boot()

    def step(self, name, *args, data=b'', timeout=10.0):
        """Runs one step; @return (ret list (signed), out bytes)."""
        self.seq += 1
        a = list(args) + [0] * (4 - len(args))
        pairs = []
        if data:
            buf = bytes(data) + bytes(N_BYTES - len(data))
            pairs += [(self.in_addr + 4 * i, struct.unpack_from('<I', buf, 4 * i)[0]) for i in range(N_BYTES // 4)]
        pairs += [(self.args_addr + 4 * i, a[i] & 0xFFFFFFFF) for i in range(4)]
        pairs.append((self.cmd_addr, (self.seq << 8) | STEP[name]))
        th.write_words(pairs)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            time.sleep(0.05)
            rep = self.report()
            if rep and rep['ack'] == self.seq and rep['busy'] == 0:
                if rep['err']:
                    raise RuntimeError(f'step {name}{tuple(args)} refused by the firmware (err)')
                self.steps += 1
                ret = [s32(rep[f'ret{i}']) for i in range(N_RET)]
                out = b''.join(struct.pack('<I', rep[f'out{i}']) for i in range(N_BYTES // 4))
                return ret, out
        raise TimeoutError(f'step {name}{tuple(args)} not acknowledged in {timeout} s')


def untouched(out, start=0):
    return all(b == FILL for b in out[start:])


def show(b):
    return b.decode('ascii', 'replace') if all(32 <= c < 127 for c in b) else b.hex()


# ----------------------------------------------------------------------------------------------------------- values


class Values:
    """What this run writes: tagged with a random run id, so a stale value from an earlier run cannot pass."""

    def __init__(self, rng):
        tag = ''.join(rng.choice('0123456789ABCDEF') for _ in range(6))
        self.tag = tag
        self.text = {'SpinSerialNumber': f'SPN-{tag}-01'.encode(), 'ShieldSerialNumber': f'SHD-{tag}-02'.encode(),
                     'SpinPassword': f'PS{tag}01'.encode(), 'ShieldPassword': f'PH{tag}02'.encode()}
        assert [len(self.text[n]) for n, _, _, ln in TEXT_FIELDS] == [ln for _, _, _, ln in TEXT_FIELDS]
        self.version = {'SpinVersion': tuple(rng.randrange(1, 250) for _ in range(3)),
                        'ShieldVersion': tuple(rng.randrange(1, 250) for _ in range(3))}
        self.extra = [bytes(rng.randrange(256) for _ in range(n)) for n in EXTRA_LENGTHS]
        self.data = (f32(1.0 + rng.random()), f32(-rng.random()))     # pin 51 gain, offset
        self.sens = (f32(0.05 + rng.random()), f32(rng.random() - 0.5))  # V_HIGH gain, offset


def read_all(b):
    """Every metaData getter with a buffer of exactly the documented size (16 for the extra slots)."""
    got = {}
    for name, _, get, ln in TEXT_FIELDS:
        ret, out = b.step(get, ln)
        got[name] = (ret[0], out[:ln], untouched(out, ln))
    for name, _, get in VERSION_FIELDS:
        ret, _ = b.step(get)
        got[name] = (ret[0], tuple(ret[1:4]))
    for i in range(EXTRA_COUNT):
        ret, out = b.step('GET_EXTRA', i, EXTRA_MAX)
        got[f'Extra{i}'] = (ret[0], out[:max(ret[0], 0)], untouched(out, max(ret[0], 0)))
    return got


def judge_all(v, got):
    """@return list of mismatches between got and the values v (expected return codes from the docs)."""
    bad = []
    for name, _, _, ln in TEXT_FIELDS:
        rc, data, rest = got[name]
        if rc != ln or data != v.text[name] or not rest:
            bad.append(f'get{name} {rc} {show(data)!r}')
    for name, _, _ in VERSION_FIELDS:
        rc, ver = got[name]
        if rc != 0 or ver != v.version[name]:
            bad.append(f'get{name} {rc} {ver}')
    for i in range(EXTRA_COUNT):
        rc, data, rest = got[f'Extra{i}']
        if rc != len(v.extra[i]) or data != v.extra[i] or not rest:
            bad.append(f'getExtraData({i}) {rc} {data.hex()}')
    return bad


def enable_channels(b):
    r1, _ = b.step('DATA_ENABLE', DATA_PIN, ADC_1)
    r2, _ = b.step('SENS_ENABLE', ADC_1)
    return r1[0], r2[0]


def params(b):
    d, _ = b.step('DATA_GET', DATA_PIN)
    s, _ = b.step('SENS_GET')
    return (d[0], d[1] & 0xFFFFFFFF, d[2] & 0xFFFFFFFF), (s[0], s[1] & 0xFFFFFFFF, s[2] & 0xFFFFFFFF)


def fmt_p(p):
    return f'type {p[0]}, gain {bits2f(p[1]):.6g}, offset {bits2f(p[2]):.6g}'


# ----------------------------------------------------------------------------------------------------------- phases


def first_boot(b, v):
    good = True
    ret, _ = b.step('NVS_INFO')
    cur, in_nvs0, free0 = ret[0], ret[1], ret[2]
    line('L0-NVS-02.0', 'INFO', 'NVS after the clear of L0-NVS-15.b and the raw entry of L0-NVS-16', 'recorded',
         f'current version {cur}, version in NVS {in_nvs0}, free {free0} B')

    # L0-NVS-03: argument errors (docs: -2 size/buffer, -3 slot); a refused getter writes nothing.
    cid = 'L0-NVS-03'
    bad = []
    for name, sset, get, ln in TEXT_FIELDS:
        for n in (ln - 1, ln + 1):
            r, _ = b.step(sset, n, data=b'X' * n)
            if r[0] != -2:
                bad.append(f'set{name}(size {n}) {r[0]}')
        r, out = b.step(get, ln - 1)
        if r[0] != -2 or not untouched(out):
            bad.append(f'get{name}(buffer {ln - 1}) {r[0]}' + ('' if untouched(out) else ' wrote bytes'))
    for args, exp, what in (((EXTRA_COUNT, 4), -3, 'setExtraData(5, 4 B)'),
                            ((0, EXTRA_MAX + 1), -2, 'setExtraData(0, 17 B)')):
        r, _ = b.step('SET_EXTRA', *args, data=b'Y' * args[1])
        if r[0] != exp:
            bad.append(f'{what} {r[0]}')
    for args, exp, what in (((EXTRA_COUNT, EXTRA_MAX), -3, 'getExtraData(5, 16)'),
                            ((0, EXTRA_MAX - 1), -2, 'getExtraData(0, 15)')):
        r, out = b.step('GET_EXTRA', *args)
        if r[0] != exp or not untouched(out):
            bad.append(f'{what} {r[0]}')
    good &= line(f'{cid}.a', not bad, 'wrong sizes, short buffers and slot 5 are refused',
                 'set/get serial and password at length +/-1: -2; extra slot 5: -3; 17 B: -2; 15 B buffer: -2; '
                 'refused getters write nothing', '; '.join(bad) or 'all as documented')

    # L0-NVS-04: clear, then every getter says "nothing stored".
    cid = 'L0-NVS-04'
    r, _ = b.step('CLEAR_META')
    good &= line(f'{cid}.a', r[0] == 0, 'clearAllMetaData returns 0', '0', r[0])
    got = read_all(b)
    bad = [f'{k} {g[0]}' for k, g in got.items() if g[0] != -1]
    bad += [f'{k} wrote bytes' for k, g in got.items() if len(g) == 3 and not g[2]]
    bad += [f'{k} wrote {g[1]}' for k, g in got.items() if len(g) == 2 and g[1] != (0xEE, 0xEE, 0xEE)]
    good &= line(f'{cid}.b', not bad, 'every getter after clearAllMetaData', '-1 ("nothing stored yet"), '
                 'output untouched', '; '.join(bad) or 'all -1, nothing written')

    # L0-NVS-05: set everything, read it back.
    cid = 'L0-NVS-05'
    ret, _ = b.step('NVS_INFO')
    free_before = ret[2]
    bad = []
    for name, sset, _, ln in TEXT_FIELDS:
        r, _ = b.step(sset, ln, data=v.text[name])
        if r[0] != 0:
            bad.append(f'set{name} {r[0]}')
    for name, sset, _ in VERSION_FIELDS:
        r, _ = b.step(sset, *v.version[name])
        if r[0] != 0:
            bad.append(f'set{name} {r[0]}')
    for i, d in enumerate(v.extra):
        r, _ = b.step('SET_EXTRA', i, len(d), data=d)
        if r[0] != 0:
            bad.append(f'setExtraData({i}) {r[0]}')
    good &= line(f'{cid}.a', not bad, 'every setter with a valid value', '0', '; '.join(bad) or 'all 0')
    bad = judge_all(v, read_all(b))
    good &= line(f'{cid}.b', not bad, 'every getter returns the bytes just written',
                 'serials 13, passwords 10, extra slots their length (bytes read); versions 0; bytes equal; '
                 'nothing written past them', '; '.join(bad) or f'all equal (run tag {v.tag})')
    r, out = b.step('GET_SPIN_SERIAL', 20)
    good &= line(f'{cid}.c', r[0] == SERIAL_LEN and out[:SERIAL_LEN] == v.text['SpinSerialNumber'] and
                 untouched(out, SERIAL_LEN), 'getSpinSerialNumber with a 20-byte buffer',
                 '13 bytes read, buffer untouched past them (not null-terminated, docs)', f'{r[0]}, {show(out[:r[0] if r[0] > 0 else 0])!r}')
    new = f'SPN-{v.tag}-XY'.encode()
    r1, _ = b.step('SET_SPIN_SERIAL', SERIAL_LEN, data=new)
    r2, out = b.step('GET_SPIN_SERIAL', SERIAL_LEN)
    good &= line(f'{cid}.d', r1[0] == 0 and r2[0] == SERIAL_LEN and out[:SERIAL_LEN] == new,
                 'setSpinSerialNumber over a stored value', 'the new value read back', f'{r1[0]}, {r2[0]} {show(out[:SERIAL_LEN])!r}')
    v.text['SpinSerialNumber'] = new
    ret, _ = b.step('NVS_INFO')
    good &= line('L0-NVS-02.a', ret[0] >= 1, 'nvs_storage_get_current_version', 'a layout version >= 1', ret[0])
    good &= line('L0-NVS-02.b', ret[1] == ret[0], 'nvs_storage_get_version_in_nvs after a store',
                 'equal to the current version (NVS content tagged, R12)', f'{ret[1]} (current {ret[0]})')
    good &= line('L0-NVS-02.c', 0 < ret[2] <= NVS_SIZE, 'nvs_storage_get_free_space', f'0 < free <= {NVS_SIZE} B',
                 f'{ret[2]} B')
    data_bytes = SERIAL_LEN * 2 + PASSWORD_LEN * 2 + 6 + sum(EXTRA_LENGTHS)
    good &= line('L0-NVS-07.a', ret[2] <= free_before - data_bytes, 'free space after writing the 11 metadata fields',
                 f'falls by at least the {data_bytes} B written', f'{free_before} -> {ret[2]} B')

    # L0-NVS-06: conversion parameters in the same boot.
    cid = 'L0-NVS-06'
    rd, rs = enable_channels(b)
    good &= line(f'{cid}.0', rd == 0 and rs == 0, f'enableAcquisition(pin {DATA_PIN}, ADC_1), enableSensor(V_HIGH, ADC_1)',
                 '0, 0', f'{rd}, {rs}')
    pd, ps = params(b)
    good &= line(f'{cid}.a', ps[1] == V_HIGH_DT_GAIN and ps[2] == 0, 'V_HIGH parameters before any retrieve',
                 f'the shield devicetree default (gain {bits2f(V_HIGH_DT_GAIN):.6g}, offset 0; R11)', fmt_p(ps))
    line(f'{cid}.b', 'INFO', f'pin {DATA_PIN} parameters before any retrieve', 'recorded', fmt_p(pd))
    b.step('DATA_SET_LINEAR', DATA_PIN, *v.data)
    b.step('SENS_SET_LINEAR', *v.sens)
    r1, _ = b.step('DATA_STORE', DATA_PIN)
    r2, _ = b.step('SENS_STORE')
    good &= line(f'{cid}.c', r1[0] == 0 and r2[0] == 0, 'storeConversionParametersInMemory, storeParametersInMemory',
                 '0, 0', f'{r1[0]}, {r2[0]}')
    b.step('DATA_SET_LINEAR', DATA_PIN, f32(1.0), f32(0.0))
    b.step('SENS_SET_LINEAR', f32(1.0), f32(0.0))
    pd, ps = params(b)
    changed = pd[1:] == (f32(1.0), 0) and ps[1:] == (f32(1.0), 0)
    r1, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    r2, _ = b.step('SENS_RETRIEVE')
    pd, ps = params(b)
    good &= line(f'{cid}.d', changed and r1[0] == 0 and r2[0] == 0 and pd[1:] == v.data and ps[1:] == v.sens,
                 'set (1, 0) in RAM, then retrieve: the stored parameters come back',
                 f'retrieve 0, 0; pin {DATA_PIN} gain {bits2f(v.data[0]):.6g} offset {bits2f(v.data[1]):.6g}; '
                 f'V_HIGH gain {bits2f(v.sens[0]):.6g} offset {bits2f(v.sens[1]):.6g} (bit-exact)',
                 f'(1, 0) applied: {changed}; retrieve {r1[0]}, {r2[0]}; pin {fmt_p(pd)}; V_HIGH {fmt_p(ps)}')
    return good


def after_restart(b, v, cid, what):
    """Checks after a reset or a reflash: metaData read back, parameters at defaults until retrieved."""
    good = True
    bad = judge_all(v, read_all(b))
    good &= line(f'{cid}.a', not bad, f'every metaData getter {what}', 'the values this run wrote (run tag), '
                 'documented return codes', '; '.join(bad) or f'all equal (run tag {v.tag})')
    rd, rs = enable_channels(b)
    pd0, ps0 = params(b)
    r1, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    r2, _ = b.step('SENS_RETRIEVE')
    pd, ps = params(b)
    # Shield sensors load their stored parameters when the sensor list is built ("Parameters stored in permanent
    # storage are automatically retrieved at board boot", Sensors.cpp, setTwistSensorsUserCalibrationFactors); a
    # spin.data pin keeps its defaults until retrieveConversionParametersFromMemory.
    good &= line(f'{cid}.b', rd == 0 and rs == 0 and ps0[1:] == v.sens and pd0[1:] != v.data and r1[0] == 0
                 and r2[0] == 0 and pd[1:] == v.data and ps[1:] == v.sens, f'conversion parameters {what}',
                 'V_HIGH already at its stored values (loaded at boot); pin 51 not until retrieved; retrieve 0, 0; '
                 'then the stored values (bit-exact)',
                 f'before: pin {fmt_p(pd0)}, V_HIGH {fmt_p(ps0)}; retrieve {r1[0]}, {r2[0]}; '
                 f'after: pin {fmt_p(pd)}, V_HIGH {fmt_p(ps)}')
    ret, _ = b.step('NVS_INFO')
    good &= line(f'{cid}.c', ret[1] == ret[0] and ret[0] >= 1, f'nvs_storage_get_version_in_nvs {what}',
                 'the current version', f'{ret[1]} (current {ret[0]})')
    return good


def clear_meta_keeps_others(b, v):
    cid = 'L0-NVS-10'
    good = True
    r, _ = b.step('CLEAR_META')
    got = read_all(b)
    bad = [f'{k} {g[0]}' for k, g in got.items() if g[0] != -1]
    good &= line(f'{cid}.a', r[0] == 0 and not bad, 'clearAllMetaData after the reflash', '0, then every getter -1',
                 f'{r[0]}; ' + ('; '.join(bad) or 'all -1'))
    b.step('DATA_SET_LINEAR', DATA_PIN, f32(1.0), f32(0.0))
    b.step('SENS_SET_LINEAR', f32(1.0), f32(0.0))
    r1, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    r2, _ = b.step('SENS_RETRIEVE')
    pd, ps = params(b)
    good &= line(f'{cid}.b', r1[0] == 0 and r2[0] == 0 and pd[1:] == v.data and ps[1:] == v.sens,
                 'conversion parameters after clearAllMetaData', 'still retrieved: 0, 0 and the stored values (docs: '
                 '"without touching any other module\'s data")', f'{r1[0]}, {r2[0]}; pin {fmt_p(pd)}; V_HIGH {fmt_p(ps)}')
    boot = b.reset()
    if boot is None:
        return line(f'{cid}.c', False, 'reset after clearAllMetaData', 'firmware boots', 'no report')
    got = read_all(b)
    bad = [f'{k} {g[0]}' for k, g in got.items() if g[0] != -1]
    good &= line(f'{cid}.c', not bad, 'after a reset, the cleared fields stay cleared', 'every getter -1',
                 '; '.join(bad) or 'all -1')
    return good


def clear_all(b, v):
    cid = 'L0-NVS-11'
    good = True
    enable_channels(b)
    r, _ = b.step('NVS_CLEAR_ALL')
    good &= line(f'{cid}.a', r[0] == 0, 'nvs_storage_clear_all_stored_data', '0', r[0])
    r1, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    r2, _ = b.step('GET_SPIN_VERSION')
    good &= line(f'{cid}.b', r1[0] < 0 and r2[0] < 0, 'right after the clear, nothing is retrieved',
                 'retrieveConversionParametersFromMemory < 0, getSpinVersion < 0', f'{r1[0]}, {r2[0]}')
    r3, _ = b.step('SET_SPIN_VERSION', 7, 7, 7)
    r4, _ = b.step('GET_SPIN_VERSION')
    line(f'{cid}.c', 'INFO', 'a store right after the clear, without a reset (nvs_storage.h says nothing)',
         'recorded', f'setSpinVersion {r3[0]}, getSpinVersion {r4[0]} {tuple(r4[1:4])}')
    if b.reset() is None:
        return line(f'{cid}.d', False, 'reset after the clear', 'firmware boots', 'no report')
    enable_channels(b)
    ret, _ = b.step('NVS_INFO')
    got = read_all(b)
    bad = [f'{k} {g[0]}' for k, g in got.items() if g[0] != -1]
    r1, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    good &= line(f'{cid}.d', ret[1] == 0 and not bad and r1[0] == -1, 'after a reset: NVS empty',
                 'version in NVS 0; every metaData getter -1; retrieve -1 ("persistent memory is empty", DataAPI.h)',
                 f'version in NVS {ret[1]}, free {ret[2]} B; ' + ('; '.join(bad) or 'getters all -1') +
                 f'; retrieve {r1[0]}')
    b.step('DATA_SET_LINEAR', DATA_PIN, *v.data)
    r1, _ = b.step('DATA_STORE', DATA_PIN)
    r2, _ = b.step('SET_SPIN_VERSION', *v.version['SpinVersion'])
    if b.reset() is None:
        return line(f'{cid}.e', False, 'reset after storing again', 'firmware boots', 'no report')
    enable_channels(b)
    r3, _ = b.step('DATA_RETRIEVE', DATA_PIN)
    pd, _ = params(b)
    r4, _ = b.step('GET_SPIN_VERSION')
    good &= line(f'{cid}.e', r1[0] == 0 and r2[0] == 0 and r3[0] == 0 and pd[1:] == v.data and r4[0] == 0 and
                 tuple(r4[1:4]) == v.version['SpinVersion'], 'store again after the clear, reset, read back',
                 'store 0, setSpinVersion 0; after the reset retrieve 0 with the values, getSpinVersion 0 with the value',
                 f'store {r1[0]}, set {r2[0]}; retrieve {r3[0]} pin {fmt_p(pd)}; get {r4[0]} {tuple(r4[1:4])}')
    return good


def unknown_pin(b):
    cid = 'L0-NVS-12'
    r1, _ = b.step('DATA_STORE', NOT_ADC_PIN)
    r2, _ = b.step('DATA_RETRIEVE', NOT_ADC_PIN)
    return line(f'{cid}.a', r1[0] == DOC_PIN_NOT_FOUND and r2[0] == DOC_PIN_NOT_FOUND,
                f'store/retrieveConversionParameters…(pin {NOT_ADC_PIN}, no ADC channel)',
                f'{DOC_PIN_NOT_FOUND} "pin not found" (DataAPI.h)', f'{r1[0]}, {r2[0]}')


# ----------------------------------------------------------------------------------------------------------- partition


def dump_partition(path):
    r = th.openocd(f'dump_image {path} {NVS_ADDR:#x} {NVS_SIZE}', check=False)
    if r.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) != NVS_SIZE:
        return None
    return open(path, 'rb').read()


def write_partition(path):
    """Halts the CUT, erases the NVS partition and programs back only the double-words of the backup that are not
    erased (0xFF), verifies the whole partition against the backup, resets.

    Programming 0xFF over an erased double-word is not neutral on the STM32G4: the double-word reads 0xFF but can no
    longer be programmed (its ECC is written), so a later NVS write there fails with -EIO. A plain
    `flash write_image erase` of the 4 kB backup would leave every free slot in that state.
    """
    data = open(path, 'rb').read()
    chunks, i = [], 0
    while i < len(data):
        if data[i:i + 8] == b'\xff' * 8:
            i += 8
            continue
        j = i
        while j < len(data) and data[j:j + 8] != b'\xff' * 8:
            j += 8
        chunks.append((i, data[i:j]))
        i = j
    with tempfile.TemporaryDirectory() as d:
        cmds = ['reset halt', f'flash erase_address {NVS_ADDR:#x} {NVS_SIZE:#x}']
        for k, (off, blob) in enumerate(chunks):
            p = os.path.join(d, f'c{k}.bin')
            open(p, 'wb').write(blob)
            cmds.append(f'flash write_image {p} {NVS_ADDR + off:#x} bin')
        cmds += [f'verify_image {path} {NVS_ADDR:#x} bin', 'reset run']
        r = th.openocd(*cmds, check=False)
    return r.returncode == 0, (r.stdout + r.stderr)[-600:]


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def used_bytes(b):
    return sum(1 for x in b if x != 0xFF)


# ----------------------------------------------------------------------------------------------------------- main


def blocked(reason, only=None):
    for cid, what in CASES:
        if only is None or cid in only:
            th.line(cid, 'BLOCKED', what, '-', reason)
    return 0


def main():
    t_start = time.monotonic()
    if '--run' not in sys.argv and os.environ.get('CORE_TARGET') != '1':
        return blocked('on-target: run tests/run_all.sh --l0 --target --nightly (reflashes the CUT, writes its NVS)')
    if '--run' not in sys.argv and os.environ.get('CORE_NIGHTLY') != '1':
        return blocked('nightly tier (two firmwares, three flashes): tests/run_all.sh --l0 --target --nightly')
    out = os.environ.get('CORE_TEST_OUT') or tempfile.mkdtemp(prefix='target_metadata_nvs_')
    backup = os.path.join(out, 'nvs_partition_before.bin')
    # 1. Back up the partition before anything writes it (the CUT still runs its previous firmware).
    before = dump_partition(backup)
    if before is None:
        th.line('L0-NVS-01', 'FAIL', 'NVS partition backed up over SWD', f'{NVS_SIZE} B read at {NVS_ADDR:#x}',
                'SWD read failed: nothing written')
        blocked('no backup of the NVS partition: the module writes nothing without one', [c for c, _ in CASES[1:]])
        return 1
    line('L0-NVS-01.0', 'INFO', 'NVS partition as found (backup in the campaign folder)', 'recorded',
         f'sha256 {sha(before)}, {used_bytes(before)} B not erased, {os.path.basename(backup)}')
    good = True
    b = None
    try:
        rc = th.flash(ENV, os.path.join(out, 'target_metadata_nvs_flash1.log'))
        if rc != 0:
            th.line('L0-NVS-01', 'FAIL', 'env NVS builds and flashes over the ST-Link', 'pio upload exit 0',
                    f'exit {rc}, see target_metadata_nvs_flash1.log')
            blocked('flash failed', [c for c, _ in CASES[1:12]])
            good = False
        else:
            b = Bench()
            boot = b.wait_boot()
            good &= line('L0-NVS-01', boot is not None, 'env NVS builds, flashes and answers over SWD',
                         f'pio upload exit 0, magic {MAGIC:#010x}', 'no report' if boot is None else
                         f"magic {boot['magic']:#010x}, boot {boot['boot_count']}")
            if boot is not None:
                good &= run_phases(b, out)
    except Exception as e:  # noqa: BLE001 - whatever happens, the partition goes back below
        th.line('L0-NVS-01', 'FAIL', 'module stopped', 'every step acknowledged', f'{type(e).__name__}: {e}')
        good = False
    finally:
        good &= restore(backup, before, out)
    line('L0-NVS-14', 'INFO', 'module duration (backup, 3 flashes of 2 firmwares, resets, write-back)', 'recorded',
         f'{time.monotonic() - t_start:.0f} s, {b.steps if b else 0} steps')
    return 0 if good else 1


FLASH_SR = 0x40022010  # RM0440 FLASH_SR, then FLASH_CR, FLASH_ECCR
RAW_ID = 0x7F01        # a key in no category of nvs_storage.h: used by nothing else


def as_found(b):
    """A store on the partition as the CUT had it, then the module clears it and works on an empty NVS."""
    cid = 'L0-NVS-15'
    good = True
    ret, _ = b.step('NVS_INFO')
    r1, _ = b.step('NVS_STORE', RAW_ID, 8, data=bytes(range(1, 9)))
    sr = th.read_words(FLASH_SR, 1)
    r2, _ = b.step('SET_SHIELD_VERSION', 9, 9, 9)
    r3, _ = b.step('GET_SHIELD_VERSION')
    good &= line(f'{cid}.a', r1[0] >= 0 and r2[0] == 0 and r3[0] == 0 and tuple(r3[1:4]) == (9, 9, 9),
                 'nvs_storage_store_data and setShieldVersion on the NVS as found, read back',
                 'store >= 0, setShieldVersion 0, getShieldVersion 0 (9, 9, 9)',
                 f'version in NVS {ret[1]}, free {ret[2]} B; nvs_storage_store_data {r1[0]}'
                 f'{" (-EIO)" if r1[0] == -5 else ""}, FLASH_SR {sr[0] if sr else 0:#010x} after it; '
                 f'setShieldVersion {r2[0]}, getShieldVersion {r3[0]} {tuple(r3[1:4])}')
    r, _ = b.step('NVS_CLEAR_ALL')
    boot = b.reset()
    ret, _ = b.step('NVS_INFO') if boot else ([None] * 3, b'')
    good &= line(f'{cid}.b', r[0] == 0 and boot is not None and ret[1] == 0,
                 'nvs_storage_clear_all_stored_data, reset: the module goes on with an empty NVS',
                 'clear 0; after the reset version in NVS 0', f'clear {r[0]}; ' +
                 ('no boot' if boot is None else f'version in NVS {ret[1]}, free {ret[2]} B'))
    # L0-NVS-16: the driver calls themselves, on the empty NVS (nvs_storage.h: store "0 on success",
    # retrieve "number of bytes read").
    data = bytes(range(10, 26))
    r1, _ = b.step('NVS_STORE', RAW_ID, len(data), data=data)
    r2, o = b.step('NVS_RETRIEVE', RAW_ID, N_BYTES)
    good &= line('L0-NVS-16.a', r1[0] == 0, 'nvs_storage_store_data(16 B) on an empty NVS', '0 (nvs_storage.h)',
                 r1[0])
    good &= line('L0-NVS-16.b', r2[0] == len(data) and o[:len(data)] == data and untouched(o, len(data)),
                 'nvs_storage_retrieve_data of that entry', '16 (bytes read) and the bytes',
                 f'{r2[0]}, {o[:max(r2[0], 0)].hex()}')
    r3, _ = b.step('NVS_RETRIEVE', RAW_ID, 8)
    r4, _ = b.step('NVS_STORE', RAW_ID, 0)
    r5, _ = b.step('NVS_RETRIEVE', RAW_ID, N_BYTES)
    good &= line('L0-NVS-16.c', r3[0] < 0 and r5[0] < 0, 'retrieve with a buffer too small; retrieve after a '
                 '0-byte store (delete)', 'negative, negative', f'{r3[0]} (store of 0 B: {r4[0]}), {r5[0]}')
    return good


def run_phases(b, out):
    v = Values(random.Random())
    good = True
    t0 = time.monotonic()
    good &= as_found(b)
    good &= first_boot(b, v)
    print(f'== first boot: {time.monotonic() - t0:.0f} s, {b.steps} steps', flush=True)
    boot0 = b.report()['boot_count']
    boot = b.reset()
    good &= line('L0-NVS-08.0', boot is not None and boot['boot_count'] == boot0 + 1, 'ST-Link reset',
                 f'the firmware boots again (boot count {boot0 + 1})', 'no report' if boot is None else
                 f"boot count {boot['boot_count']}")
    if boot is None:
        return False
    good &= after_restart(b, v, 'L0-NVS-08', 'after an ST-Link reset')
    # Reflash: another firmware, then this one again.
    rc = th.flash(OTHER_ENV, os.path.join(out, 'target_metadata_nvs_flash2_smoke.log'))
    smoke_addr, _ = th.symbol(OTHER_ENV, 'g_smoke_report')
    other = None
    for _ in range(20):
        w = th.read_words(smoke_addr, 1)
        other = w[0] if w else None
        if other == SMOKE_MAGIC:
            break
        time.sleep(0.5)
    rc2 = th.flash(ENV, os.path.join(out, 'target_metadata_nvs_flash3.log'))
    boot = b.wait_boot() if rc2 == 0 else None
    good &= line('L0-NVS-09.0', rc == 0 and other == SMOKE_MAGIC and boot is not None,
                 f'reflash: env {OTHER_ENV} flashed and running, then env {ENV} again',
                 f'both uploads exit 0; {OTHER_ENV} report magic {SMOKE_MAGIC:#010x}; {ENV} answers',
                 f'uploads {rc}, {rc2}; {OTHER_ENV} magic {other:#010x}' if other is not None else
                 f'uploads {rc}, {rc2}; {OTHER_ENV} report unreadable')
    if boot is None:
        return False
    good &= after_restart(b, v, 'L0-NVS-09', 'after a reflash')
    good &= clear_meta_keeps_others(b, v)
    good &= unknown_pin(b)
    good &= clear_all(b, v)
    return good


def restore(backup, before, out):
    ok, log = write_partition(backup)
    after = dump_partition(os.path.join(out, 'nvs_partition_after.bin'))
    same = after == before
    th.line('L0-NVS-13', th.judge(ok and same), 'the CUT\'s NVS partition written back byte for byte',
            f'erase + program + verify exit 0; {NVS_SIZE} B read back equal to the backup (sha256 {sha(before)})',
            ('equal' if same else ('unreadable' if after is None else
                                   f'DIFFERENT: sha256 {sha(after)}, {sum(x != y for x, y in zip(after, before))} B differ'))
            + ('' if ok else f'; OpenOCD: {" ".join(log.split())[-300:]}'))
    if not (ok and same):
        print(f'!! NVS partition NOT restored: the backup is {backup}; write it back with OpenOCD '
              f'"flash write_image erase {backup} {NVS_ADDR:#x} bin"', flush=True)
    return ok and same


if __name__ == '__main__':
    sys.exit(main())
