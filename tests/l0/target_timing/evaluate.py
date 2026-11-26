#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file evaluate.py
## @brief L0-TIM verdicts: a timing run (timing.json, written by run.py) against the committed reference
##        (reference.json), with the rule of Core-staging#35 (Timing).
##
## Rule, per row, on the mean in core cycles:
##   margin = max(10 % of the reference mean, 3 sigma of the reference) + 50 cycles
##   mean > reference + margin  -> REGRESSION   (FAIL)
##   mean < reference - margin  -> IMPROVED: check it (INFO: suspicious until explained)
##   otherwise                  -> PASS
## While no reference exists, or for a row the reference lacks, every verdict is INFO. Rows marked info-only
## (the period, the probe itself, gaps from other interrupts) are always INFO. The reference never moves by
## itself: only --update-reference writes it, with a reason, and the change is committed.
##
## @par Usage
##   evaluate.py RUN.json                                   # table + verdicts against reference.json
##   evaluate.py RUN.json --update-reference --name main --reason "first reference run (#37)"
##   evaluate.py --compare BASE.json RUN.json               # RUN against BASE, the rule's reading as INFO
## Standard library only.
import argparse
import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.join(HERE, 'reference.json')
FORMAT = 1
REL, SIGMAS, ABS = 0.10, 3.0, 50.0
# Rows reported but never judged: not a cost (period, other interrupts) or the probe itself.
INFO_ONLY = ('probe.empty', 'task0.period', 'task.period', 'task0.otherIrq', 'task.otherIrq')


def stats(n, total, total_sq, lo, hi):
    """@brief mean / sigma (population) / min / worst of a row from its sums."""
    if n == 0:
        return None
    mean = total / n
    var = max(0.0, total_sq / n - mean * mean)
    return {'n': n, 'mean': round(mean, 2), 'sigma': round(math.sqrt(var), 2), 'min': lo, 'worst': hi}


def margin(ref):
    return max(REL * ref['mean'], SIGMAS * ref['sigma']) + ABS


def verdict(name, row, ref):
    """@return (verdict, note) for one row; ref is the reference row or None."""
    if row is None:
        return 'INFO', 'no sample'
    if name in INFO_ONLY:
        return 'INFO', 'reported, not judged'
    if ref is None:
        return 'INFO', 'no reference yet'
    m = margin(ref)
    d = row['mean'] - ref['mean']
    if d > m:
        return 'FAIL', f'regression: +{d:.0f} cycles > margin {m:.0f}'
    if d < -m:
        return 'INFO', f'improved: check it ({d:.0f} cycles, margin {m:.0f})'
    return 'PASS', f'{d:+.0f} cycles within ±{m:.0f}'


def load_reference(path=REFERENCE):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        ref = json.load(f)
    if ref.get('format') != FORMAT:
        sys.exit(f'evaluate: {path} has format {ref.get("format")}, expected {FORMAT}')
    return ref


def reference_rows(ref, name=None):
    """@return (entry name, rows) the verdicts use: `against` unless name is given; (None, {}) without one."""
    if not ref:
        return None, {}
    key = name or ref.get('against', 'main')
    entry = ref.get('refs', {}).get(key)
    return (key, entry['rows']) if entry else (None, {})


def evaluate(run, ref_rows):
    """@return list of (name, row, ref_row, verdict, note) in the run's row order."""
    out = []
    for name, row in run['rows'].items():
        r = ref_rows.get(name)
        v, note = verdict(name, row, r)
        out.append((name, row, r, v, note))
    return out


def us(cycles, core_hz):
    return cycles * 1e6 / core_hz


def table(run, results, title):
    """@brief Markdown: row -> mean / sigma / worst cycles -> us -> % of the task period, verdict."""
    hz = run['core_hz'] or 170000000
    period_us = run.get('task_period_us', 100)
    lines = [f'### {title}', '',
             f"Run `{run.get('firmware', '?')}`, {run['repeats']} samples per call, {run['task_runs']} task runs, "
             f"{hz / 1e6:.0f} MHz; probe pair {run['probe_overhead']} cycles (included in the call rows).", '',
             '| Row | Mean | σ | Worst | Mean µs | % of period | Reference mean | Verdict |',
             '|---|---:|---:|---:|---:|---:|---:|---|']
    for name, row, ref, v, note in results:
        if row is None:
            lines.append(f'| `{name}` | – | – | – | – | – | – | {v}: {note} |')
            continue
        pct = 100.0 * us(row['mean'], hz) / period_us
        lines.append(f"| `{name}` | {row['mean']:.1f} | {row['sigma']:.1f} | {row['worst']} | "
                     f"{us(row['mean'], hz):.3f} | {pct:.2f} | {ref['mean'] if ref else '–'} | {v}: {note} |")
    return '\n'.join(lines) + '\n'


def update_reference(run, name, reason, path=REFERENCE):
    if not reason or not reason.strip():
        sys.exit('evaluate: --update-reference needs --reason "why the reference moves" (it is committed with it)')
    ref = load_reference(path) or {'format': FORMAT, 'unit': 'core cycles',
                                   'rule': 'regression if mean > ref + max(10 %, 3 sigma) + 50 cycles; '
                                           'improved (check it) if faster by the same margin (Core-staging#35)',
                                   'against': 'main', 'refs': {}}
    ref['refs'][name] = {
        'firmware': run.get('firmware'), 'ref_sha': run.get('ref_sha'), 'harness_sha': run.get('harness_sha'),
        'date': dt.date.today().isoformat(), 'reason': reason.strip(), 'core_hz': run['core_hz'],
        'repeats': run['repeats'], 'task_runs': run['task_runs'], 'probe_overhead': run['probe_overhead'],
        'rows': {k: v for k, v in run['rows'].items() if v is not None}}
    with open(path, 'w') as f:
        json.dump(ref, f, indent=1, sort_keys=False)
        f.write('\n')
    print(f'evaluate: reference "{name}" written to {os.path.relpath(path)}: commit it with its reason.')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run', nargs='?', help='timing.json of the run to judge')
    ap.add_argument('--reference', default=REFERENCE)
    ap.add_argument('--against', help='reference entry to judge against (default: the file\'s "against")')
    ap.add_argument('--update-reference', action='store_true', help='write RUN as reference entry --name')
    ap.add_argument('--name', default='main', help='reference entry written by --update-reference')
    ap.add_argument('--reason', help='why the reference moves (required with --update-reference)')
    ap.add_argument('--compare', nargs=2, metavar=('BASE', 'RUN'), help='judge RUN against BASE')
    ap.add_argument('--out', help='also write the markdown table here')
    a = ap.parse_args()
    if a.compare:
        base, run = (json.load(open(p)) for p in a.compare)
        # A comparison is a finding, not a gate: every verdict is INFO, the rule's reading kept in the note.
        results = [(n, r, b, 'INFO', note if v == 'INFO' else f'{v} by the rule: {note}')
                   for n, r, b, v, note in evaluate(run, {k: v for k, v in base['rows'].items() if v})]
        md = table(run, results,
                   f"{run.get('firmware', a.compare[1])} against {base.get('firmware', a.compare[0])}")
    elif a.run:
        run = json.load(open(a.run))
        if a.update_reference:
            update_reference(run, a.name, a.reason, a.reference)
            return 0
        key, rows = reference_rows(load_reference(a.reference), a.against)
        md = table(run, evaluate(run, rows), f'against reference "{key}"' if key else 'no reference yet (INFO)')
    else:
        ap.error('give RUN.json or --compare BASE RUN')
    print(md)
    if a.out:
        with open(a.out, 'w') as f:
            f.write(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
