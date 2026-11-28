#!/usr/bin/env python3
## SPDX-License-Identifier: LGPL-2.1
## @file coverage.py
## @brief The coverage matrix of the API regression suite (Core-staging#45): every public call against the tiers
##        that test it, generated from tests/catalogue.json; the CI check; the picture; the control-center tabs.
## @par Usage
##   python3 tests/common/coverage.py check [--summary FILE]
##       every public call has a matrix row (a catalogue test `covers` it, or tests/coverage_gaps.json gives its
##       gap reason); every `covers` names a public call. Exit 1 if not. Stale gap entries are warnings.
##   python3 tests/common/coverage.py render [--campaigns DIR ...] [--baseline DIR ...] [--write DIR] [--summary FILE]
##       writes DIR/coverage.svg (the picture), DIR/coverage.md (job summary, graduation report) and
##       DIR/matrix.json; default DIR tests/out/coverage, campaigns tests/out (read only).
##   control_center.py calls sections() for its Coverage overview, Coverage tab and Timing tab.
## @par Sources (nothing is typed twice)
##   public calls: tools/api_calls.py (the build tier's inventory of the user-facing headers);
##   build-only tier: the calls tests/firmware/api_compile/main.cpp makes, known_failures.txt;
##   bench tiers: catalogue `covers`, `tier`, `target_tier`, `known_failure`, and the newest campaign result of each
##   test (control_center.latest_results); baseline: campaigns at the `pre-zephyr-4.4` tag (or --baseline);
##   gaps: tests/coverage_gaps.json; timing marks: the newest campaign's timing.json against
##   tests/l0/target_timing/reference.json (evaluate.py's rule).
## Standard library only.
"""Coverage matrix: generated from the catalogue, checked in CI, drawn for the job summary and the control center."""
from pathlib import Path
import argparse,datetime as dt,html,importlib.util,json,re,subprocess,sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
GAPS=REPO/'tests/coverage_gaps.json'
API_COMPILE=REPO/'tests/firmware/api_compile/main.cpp'
KNOWN_BUILD=REPO/'tests/firmware/api_compile/known_failures.txt'
TIMING=REPO/'tests/l0/target_timing'
BASELINE_TAG='pre-zephyr-4.4'
sys.path.insert(0,str(HERE))
import catalogue  # noqa: E402

TIERS=catalogue.TIERS                       # build-only, per-PR, nightly, supervised
BENCH=TIERS[1:]
# Matrix state of a call in a tier, worst first. Colour is never alone: each has a symbol and a word.
STATES=[('regression','✗','regression'),('known','!','known failure'),('not_run','○','covered, not run yet'),
        ('covered','✓','covered'),('gap','·','gap')]
RANK={k:i for i,(k,_,_) in enumerate(STATES)}
SYM={k:s for k,s,_ in STATES}
WORD={k:w for k,_,w in STATES}
TIER_WORD={'build-only':'Build-only','per-PR':'Per-PR','nightly':'Nightly','supervised':'Supervised'}
e=html.escape


def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def api():
    """@brief tools/api_calls.py, the build tier's inventory of the public calls (the one parser of the headers)."""
    return _load('api_calls',REPO/'tools/api_calls.py')


def inventory():
    """@brief [(object, name, guard, overloads)] in header order, one entry per call name."""
    out,seen=[],{}
    for obj,calls in api().inventory().items():
        for name,guard in calls:
            k=f'{obj}.{name}'
            if k in seen:seen[k][3]+=1;continue
            seen[k]=[obj,name,guard,1];out.append(seen[k])
    return [tuple(x) for x in out]


def load_gaps(path=GAPS):
    if not path.exists():return {'reasons':{},'module_tickets':{},'calls':{},'drivers':{}}
    g=json.loads(path.read_text(encoding='utf-8'))
    for k in ('reasons','module_tickets','calls','drivers'):g.setdefault(k,{})
    return g


def build_tier(calls):
    """@brief {call: 'covered'|'known'|'missing'} in the build-only tier: called in the API compile application,
    minus the known failures (tests/firmware/api_compile/known_failures.txt, any configuration)."""
    a=api()
    src=a.strip_comments(API_COMPILE.read_text()) if API_COMPILE.exists() else ''
    known={}
    if KNOWN_BUILD.exists():
        for line in KNOWN_BUILD.read_text().split('\n'):
            p=line.split(None,2)
            if len(p)>=2 and not p[0].startswith('#'):known.setdefault(p[1],f'{p[0]}: {p[2] if len(p)>2 else ""}')
    out={}
    for obj,name,_,n in calls:
        k=f'{obj}.{name}'
        hits=len(re.findall(re.escape(obj)+r'\s*\.\s*'+name+r'\s*\(',src))
        out[k]=('known',known[k]) if k in known else (('covered','') if hits>=n else ('missing',f'{hits} of {n} overloads called in api_compile'))
    return out


def git(*a):
    try:return subprocess.run(['git',*a],cwd=REPO,capture_output=True,text=True,timeout=10).stdout.strip()
    except Exception:return ''


# ---------------------------------------------------------------- timing marks

def camel(s):
    return re.findall(r'[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])',s)


def timing_call(row,names):
    """@brief The public call a timing row measures ('pwm.setAdcTrigInstant' -> 'spin.pwm.setAdcTriggerInstant'), or
    None for task-timing rows. The row's object is matched by its last part; its name exactly, else word by word
    as an abbreviation (each word a prefix of the call's word), one trailing variant word allowed (getLatestValueV)."""
    head,_,fn=row.partition('.')
    objs=[o for o in names if o.split('.')[-1]==head]
    if len(objs)!=1 or not fn:return None
    obj=objs[0]
    if fn in names[obj]:return f'{obj}.{fn}'
    r=camel(fn)
    for extra in (0,1):  # same word count first, then one trailing variant word
        hit=[n for n in names[obj] for c in [camel(n)]
             if len(r)==len(c)+extra and all(c[i].lower().startswith(r[i].lower()) for i in range(len(c)))]
        if hit:return f'{obj}.{hit[0]}' if len(hit)==1 else None
    return None


def timing(camps,names):
    """@brief The newest timing run (a campaign's timing.json) against reference.json.
    @return dict: campaign, run, reference name, rows [(row, call, stats, ref, verdict, note)], by_call."""
    ev=_load('timing_evaluate',TIMING/'evaluate.py')
    ref=ev.load_reference(str(TIMING/'reference.json'))
    against,ref_rows=ev.reference_rows(ref)
    refs=(ref or {}).get('refs',{})
    camp=next((c for c in camps if (c['dir']/'timing.json').exists()),None)
    run=json.loads((camp['dir']/'timing.json').read_text()) if camp else None
    rows=[]
    if run:
        for name,row,r,v,note in ev.evaluate(run,ref_rows):
            rows.append((name,timing_call(name,names),row,r,v,note))
    by_call={}
    for name in list(ref_rows)+[r[0] for r in rows]:
        c=timing_call(name,names)
        if c:by_call.setdefault(c,{'rows':set(),'mark':'timed'})['rows'].add(name)
    for name,c,row,r,v,note in rows:
        if c and v=='FAIL':by_call[c]['mark']='regressed'
        elif c and note.startswith('improved') and by_call[c]['mark']!='regressed':by_call[c]['mark']='improved'
    return {'campaign':camp,'run':run,'against':against,'refs':refs,'rows':rows,'by_call':by_call,
            'margin':ev.margin,'us':ev.us}


# ---------------------------------------------------------------- the matrix

def test_state(test,result,findings,base):
    """@brief (matrix state, note) of one test from its newest result (control_center states)."""
    import control_center as cc
    s=cc.state_of(test,result,findings)
    if s=='fail':
        b=base.get(test['id'])
        if b and any(r['verdict']=='FAIL' for r in b[1]):
            return 'known',f'fails on {BASELINE_TAG} too (fail→fail: mark it known_failure with its issue)'
        if b:return 'regression',f'passed on {BASELINE_TAG}, fails now'
        return 'regression','fails, not a known failure (no baseline run)'
    if s=='known':
        f=[cc.finding_for(r['id'],findings) for r in result[1] if r['verdict']=='FAIL']
        return 'known',', '.join(sorted({x['issue'] for x in f if x}))
    if s in ('pass','info'):return 'covered',''
    return 'not_run',{'blocked_run':'blocked in its last run','planned':'not implemented','blocked':'blocked'}.get(s,'not run yet')


def split_baseline(camps,baseline_dirs=()):
    """@brief (current campaigns, baseline campaigns): a campaign at the baseline tag's commit is baseline evidence."""
    sha=git('rev-parse','--short=7',BASELINE_TAG+'^{commit}')
    bdirs={Path(d).resolve() for d in baseline_dirs}
    isb=lambda c:(sha and c['commit'].startswith(sha)) or any(Path(c['dir']).resolve().is_relative_to(d) for d in bdirs)
    return [c for c in camps if not isb(c)],[c for c in camps if isb(c)],sha


def matrix(cat=None,camps=None,gaps=None,baseline_camps=None):
    """@brief The coverage matrix. camps: control_center.campaigns() (newest first), baseline excluded."""
    import control_center as cc
    from make_record import findings_register
    cat=cat or catalogue.load()
    camps=camps or [];baseline_camps=baseline_camps or []
    gaps=gaps if gaps is not None else load_gaps()
    calls=inventory()
    ids=[f'{o}.{n}' for o,n,_,_ in calls]
    names={}
    for o,n,_,_ in calls:names.setdefault(o,set()).add(n)
    findings=findings_register()
    variants={t['id']:t['variants'] for t in cat['tests'] if t.get('variants')}
    latest=cc.latest_results(camps,variants)
    base=cc.latest_results(baseline_camps,variants)
    build=build_tier(calls)
    tim=timing([c for c in camps if not c.get('reference')],names)
    tests={t['id']:(t,)+test_state(t,latest.get(t['id']),findings,base) for t in cat['tests'] if t.get('covers')}
    rows=[]
    for (obj,name,guard,n),cid in zip(calls,ids):
        tiers={}
        for tid,(t,st,note) in tests.items():
            if cid not in t['covers']:continue
            cur=t.get('tier','per-PR');tgt=t.get('target_tier',cur)
            c=tiers.setdefault(tgt,{'state':'covered','tests':[],'moving':[]})
            c['tests'].append({'id':tid,'state':st,'note':note,'tier':cur,'title':t['title']})
            if cur!=tgt:c['moving'].append(cur)
            if RANK[st]<RANK[c['state']]:c['state']=st
        gap=None if tiers else gaps['calls'].get(cid)
        state=min((c['state'] for c in tiers.values()),key=RANK.get) if tiers else 'gap'
        b,bnote=build[cid]
        rows.append({'id':cid,'object':obj,'name':name,'guard':guard,'overloads':n,'state':state,'tiers':tiers,
                     'gap':gap or ({'reason':'none','target_tier':'per-PR'} if not tiers else None),
                     'build':b,'build_note':bnote,'timing':tim['by_call'].get(cid,{}).get('mark')})
    drivers=[{'id':k,'object':'drivers (no wrapper)','name':k.split(':',1)[1],'header':k.split(':',1)[0],'guard':'',
              'overloads':1,'state':'gap','tiers':{},'gap':v,'build':None,'build_note':'','timing':None}
             for k,v in gaps['drivers'].items()]
    # Problems for the check.
    covered={r['id'] for r in rows if r['tiers']}
    prob={'missing':[r['id'] for r in rows if not r['tiers'] and r['id'] not in gaps['calls']],
          'unknown_covers':sorted({(tid,c) for tid,(t,_,_) in tests.items() for c in t['covers'] if c not in set(ids)}),
          'stale_gaps':sorted(c for c in gaps['calls'] if c in covered),
          'gone_gaps':sorted(c for c in gaps['calls'] if c not in set(ids)),
          'bad_gaps':sorted(f'{c}: {why}' for c,v in list(gaps['calls'].items())+list(gaps['drivers'].items())
                            for why in ([f'unknown reason {v.get("reason")}'] if v.get('reason') not in gaps['reasons'] else [])
                            +([f'target_tier must be one of {TIERS}'] if v.get('target_tier') not in TIERS else [])),
          'gone_drivers':sorted(k for k in gaps['drivers'] if not driver_declared(*k.split(':',1)))}
    return {'calls':rows,'drivers':drivers,'objects':list(dict.fromkeys(r['object'] for r in rows+drivers)),
            'gaps':gaps,'problems':prob,'timing':tim,'tier_campaigns':tier_campaigns(cat,camps),
            'baseline':bool(baseline_camps),'catalogue':cat.get('version',''),'head':git('log','-1','--format=%h')}


def driver_declared(header,name):
    hs=list((REPO/'zephyr/modules').glob(f'*/zephyr/public_api/{header}'))
    return any(re.search(r'\b'+re.escape(name)+r'\s*\(',h.read_text(errors='replace')) for h in hs)


def tier_campaigns(cat,camps):
    """@brief {tier: (campaign, rows of that tier's tests)}: the newest campaign that ran a test of each bench tier
    (a test with no tier runs per-PR: the fix tests of the runner's campaign)."""
    tier_of={t['id']:t.get('tier','per-PR') for t in cat['tests']}
    out={}
    for c in camps:
        if c.get('reference'):continue
        by={}
        for r in c['rows']:
            t=tier_of.get(r['id'].split('.')[0])
            if t:by.setdefault(t,[]).append(r)
        for t,rs in by.items():
            if t not in out and not all(r['verdict']=='BLOCKED' for r in rs):out[t]=(c,rs)
    return out


# ---------------------------------------------------------------- counts

def cell_counts(m,obj,tier):
    """@brief {state: n, 'moving': n, 'total': n} for one object in one tier (build-only: compiled/known/missing).
    Bench tiers: calls with a test whose target is this tier, plus gaps that target it."""
    rows=[r for r in m['calls']+m['drivers'] if r['object']==obj]
    c={k:0 for k,_,_ in STATES};c['moving']=c['moving_ok']=0
    if tier=='build-only':
        for r in rows:
            if r['build'] is None:continue
            c[{'covered':'covered','known':'known','missing':'gap'}[r['build']]]+=1
    else:
        for r in rows:
            if tier in r['tiers']:
                st=r['tiers'][tier]['state'];c[st]+=1
                # current -> target: drawn hatched when covered (a not-run or failing call keeps its own mark)
                c['moving']+=bool(r['tiers'][tier]['moving']);c['moving_ok']+=bool(r['tiers'][tier]['moving']) and st=='covered'
            elif not r['tiers'] and r['gap'] and r['gap'].get('target_tier')==tier:c['gap']+=1
    c['total']=sum(c[k] for k,_,_ in STATES)
    return c


def obj_summary(m,obj):
    rows=[r for r in m['calls']+m['drivers'] if r['object']==obj]
    cov=sum(bool(r['tiers']) for r in rows)
    reasons={}
    for r in rows:
        if not r['tiers'] and r['gap']:reasons[r['gap']['reason']]=reasons.get(r['gap']['reason'],0)+1
    return {'total':len(rows),'covered':cov,'gaps':len(rows)-cov,'reasons':reasons,
            'timed':sum(r['timing'] is not None for r in rows),'regressed':sum(r['timing']=='regressed' for r in rows),
            'ticket':m['gaps']['module_tickets'].get(obj,''),
            'states':{k:sum(r['state']==k for r in rows) for k,_,_ in STATES}}


REASON_SHORT={'module':'module not built yet','second-spin':'second Spin','safety-graduation':'Safety pending graduation',
              'ownverter-only':'Ownverter-only','power-stage':'power stage','driver-unwrapped':'unwrapped driver call',
              'none':'no reason given'}


def reason_text(reasons,ticket=''):
    return ', '.join(f'{n} {REASON_SHORT.get(k,k)}'+(f' ({ticket})' if k=='module' and ticket else '')
                     for k,n in sorted(reasons.items(),key=lambda x:-x[1]))


def totals(m):
    api_rows=m['calls']
    return {'calls':len(api_rows),'declarations':sum(r['overloads'] for r in api_rows),
            'covered':sum(bool(r['tiers']) for r in api_rows),'gaps':sum(not r['tiers'] for r in api_rows),
            'drivers':len(m['drivers']),
            'states':{k:sum(r['state']==k for r in api_rows) for k,_,_ in STATES},
            'build':{k:sum(r['build']==k for r in api_rows) for k in ('covered','known','missing')},
            'timed':sum(r['timing'] is not None for r in api_rows),
            'regressed':sum(r['timing']=='regressed' for r in api_rows)}


# ---------------------------------------------------------------- picture (SVG, by hand)

SVG_STYLE='''<style>
/* plain colours (no CSS variables): the file also renders in image viewers and rsvg; dark mode by media query */
.cv-svg text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif} .cv-svg .cv-s{font-size:11px} .cv-svg .cv-h{font-weight:600;font-size:12px}
.cv-svg .cv-m{font-size:13px;font-family:ui-monospace,monospace} .cv-svg .cv-t{font-size:12px} .cv-svg .cv-not_run{stroke-width:1.5}
.cv-svg .cv-moving{fill:url(#cv-hatch-good)} .cv-svg .cv-gap{fill:url(#cv-hatch-gap)} .cv-svg .cv-hg,.cv-svg .cv-hn{stroke-width:2} .cv-svg .cv-bad,.cv-svg .cv-warnt{font-weight:600}
.cv-svg text{fill:#0b0b0b} .cv-svg .cv-bg,.cv-svg .cv-hgbg{fill:#fcfcfb} .cv-svg .cv-s,.cv-svg .cv-h{fill:#52514e} .cv-svg .cv-line{stroke:#e4e2dc}
.cv-svg .cv-covered{fill:#008300} .cv-svg .cv-not_run{fill:#fcfcfb;stroke:#008300} .cv-svg .cv-known{fill:#b07800} .cv-svg .cv-regression{fill:#d03b3a}
.cv-svg .cv-track{fill:#efede8} .cv-svg .cv-hg{stroke:#008300} .cv-svg .cv-hn{stroke:#c9c7c0} .cv-svg .cv-bad{fill:#d03b3a} .cv-svg .cv-warnt{fill:#b07800}
@media (prefers-color-scheme:dark){
.cv-svg text{fill:#f1f0ec} .cv-svg .cv-bg,.cv-svg .cv-hgbg{fill:#161615} .cv-svg .cv-s,.cv-svg .cv-h{fill:#b5b3ad} .cv-svg .cv-line{stroke:#34332f}
.cv-svg .cv-covered{fill:#3fb950} .cv-svg .cv-not_run{fill:#161615;stroke:#3fb950} .cv-svg .cv-known{fill:#e0a521} .cv-svg .cv-regression{fill:#f0605f}
.cv-svg .cv-track{fill:#2c2b28} .cv-svg .cv-hg{stroke:#3fb950} .cv-svg .cv-hn{stroke:#5a5953} .cv-svg .cv-bad{fill:#f0605f} .cv-svg .cv-warnt{fill:#e0a521}
}
</style>'''


def svg(m):
    """@brief The picture: module x tier; covered / gap / known failure / regression; current -> target; timing mark."""
    LW,CW,BW,SW,RH,TOP=210,150,104,370,34,78
    W=LW+CW*len(TIERS)+SW+16
    objs=m['objects']
    H=TOP+RH*(len(objs)+1)+92
    T=totals(m)
    o=[f'<svg class="cv-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
       f'aria-label="API coverage matrix: {T["covered"]} of {T["calls"]} public calls covered by a bench test, {T["gaps"]} gaps">',
       SVG_STYLE,'<defs>',
       '<pattern id="cv-hatch-good" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
       '<rect class="cv-hgbg" width="6" height="6"/><line class="cv-hg" x1="0" y1="0" x2="0" y2="6"/></pattern>',
       '<pattern id="cv-hatch-gap" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(135)">'
       '<rect class="cv-hgbg" width="6" height="6"/><line class="cv-hn" x1="0" y1="0" x2="0" y2="6"/></pattern>',
       '</defs>',f'<rect class="cv-bg" width="{W}" height="{H}" rx="8"/>',
       f'<text x="16" y="26" style="font-size:16px;font-weight:600">API coverage matrix</text>',
       f'<text class="cv-s" x="16" y="44">{T["calls"]} public calls ({T["declarations"]} declarations) · {T["covered"]} covered by a bench test · '
       f'{T["gaps"]} gaps · {T["drivers"]} unwrapped driver calls · {T["timed"]} timed · repository {e(m["head"])} · catalogue {e(str(m["catalogue"]))}</text>']
    y=TOP-8
    o.append(f'<text class="cv-h" x="16" y="{y}">Module</text>')
    for i,t in enumerate(TIERS):o.append(f'<text class="cv-h" x="{LW+CW*i}" y="{y}">{TIER_WORD[t]}</text>')
    o.append(f'<text class="cv-h" x="{LW+CW*len(TIERS)}" y="{y}">Calls: covered / gaps (reasons)</text>')
    def bar(x,yy,c,n,tip):
        parts=[];w0=0
        o.append(f'<g><title>{e(tip)}</title><rect class="cv-track" x="{x}" y="{yy}" width="{BW}" height="12" rx="3"/>')
        segs=[('regression',c['regression']),('known',c['known']),('not_run',c['not_run']),
              ('moving',c['moving_ok']),('covered',c['covered']-c['moving_ok']),('gap',c['gap'])]
        for k,v in segs:
            if not v or not n:continue
            w=max(3,BW*v/n)
            parts.append(f'<rect class="cv-{k}" x="{x+w0:.1f}" y="{yy}" width="{max(1,w-2):.1f}" height="12" rx="2"/>')
            w0+=w
        o.extend(parts);o.append('</g>')
    rows=objs+['all']
    for j,obj in enumerate(rows):
        yy=TOP+RH*j
        if obj=='all':
            o.append(f'<line class="cv-line" x1="16" x2="{W-16}" y1="{yy-6}" y2="{yy-6}"/>')
        s=obj_summary(m,obj) if obj!='all' else None
        label='all modules' if obj=='all' else obj
        o.append(f'<text class="cv-m" x="16" y="{yy+12}"{" style=\"font-weight:600\"" if obj=="all" else ""}>{e(label)}</text>')
        if s:
            mark=[]
            if s['ticket']:mark.append(s['ticket'])
            if s['timed']:mark.append(f'timed {s["timed"]}')
            o.append(f'<text class="cv-s" x="16" y="{yy+26}">{e(" · ".join(mark))}'
                     +(f' <tspan class="cv-bad">· timing regressed {s["regressed"]}</tspan>' if s['regressed'] else '')+'</text>')
        for i,t in enumerate(TIERS):
            if obj=='all':
                c={k:0 for k,_,_ in STATES};c['moving']=c['moving_ok']=0
                for ob in objs:
                    cc_=cell_counts(m,ob,t)
                    for k in c:c[k]+=cc_[k]
                c['total']=sum(c[k] for k,_,_ in STATES)
            else:c=cell_counts(m,obj,t)
            x=LW+CW*i
            if not c['total']:
                o.append(f'<text class="cv-s" x="{x}" y="{yy+11}">–</text>');continue
            ok=c['covered']+c['not_run']+c['known']+c['regression']
            tip=(f'{label}, {TIER_WORD[t]}: {c["total"]} calls; '+', '.join(f'{c[k]} {WORD[k]}' for k,_,_ in STATES if c[k])
                 +(f'; {c["moving"]} run at another tier now (current → target)' if c['moving'] else ''))
            bar(x,yy+1,c,c['total'],tip)
            txt=f'{ok}/{c["total"]}'
            o.append(f'<text class="cv-t" x="{x+BW+4}" y="{yy+12}">{txt}</text>')
            sub=[]
            if c['regression']:sub.append(f'<tspan class="cv-bad">{c["regression"]} regr.</tspan>')
            if c['known']:sub.append(f'<tspan class="cv-warnt">{c["known"]} known</tspan>')
            if c['moving']:
                cur=sorted({mv for r in m['calls'] if (obj=='all' or r['object']==obj) and t in r['tiers'] for mv in r['tiers'][t]['moving']})
                sub.append(f'{c["moving"]} {"/".join(cur)} → {t}')
            if c['not_run']:sub.append(f'{c["not_run"]} not run')
            if sub:o.append(f'<text class="cv-s" x="{x}" y="{yy+26}">{" · ".join(sub)}</text>')
        x=LW+CW*len(TIERS)
        if obj=='all':
            o.append(f'<text class="cv-t" x="{x}" y="{yy+12}" style="font-weight:600">{T["covered"]} / {T["gaps"]} (+{T["drivers"]} driver)</text>')
        else:
            o.append(f'<text class="cv-t" x="{x}" y="{yy+12}">{s["covered"]} / {s["gaps"]}</text>')
            if s['reasons']:o.append(f'<text class="cv-s" x="{x}" y="{yy+26}">{e(reason_text(s["reasons"]))}</text>')
    ly=TOP+RH*len(rows)+14
    leg=[('covered','covered (passed or ran)'),('moving','covered, runs at another tier now (current → target)'),
         ('not_run','covered, not run yet'),('known','known failure'),('regression','regression / new failure'),('gap','gap (reason at right)')]
    x=16
    for k,w in leg:
        o.append(f'<rect class="cv-{k}" x="{x}" y="{ly}" width="14" height="12" rx="2"/><text class="cv-s" x="{x+19}" y="{ly+10}">{e(w)}</text>')
        x+=28+len(w)*5.6
        if x>W-200:x=16;ly+=20
    ly+=24
    o.append(f'<text class="cv-s" x="16" y="{ly}">Bars count the calls whose test targets that tier (gaps: their target tier); n/m = calls with a test / calls in the cell. '
             'Build-only: called in tests/firmware/api_compile.</text>')
    o.append(f'<text class="cv-s" x="16" y="{ly+16}">Timing: "timed n" = calls with a cost-per-call row (L0-TIM-02); "timing regressed" = beyond max(10 %, 3σ) + 50 cycles '
             f'of the reference. Generated {dt.datetime.now().strftime("%Y-%m-%d %H:%M")} by tests/common/coverage.py.</text>')
    o.append('</svg>')
    return '\n'.join(o)


# ---------------------------------------------------------------- Markdown (job summary, graduation report)

def cell_md(c):
    if not c['total']:return '–'
    parts=[f'{SYM[k]}{c[k]}' for k in ('covered','not_run','known','regression','gap') if c[k]]
    s=' '.join(parts)
    if c['moving']:s+=f' ({c["moving"]}→)'
    return s


def markdown(m,problems=True):
    T=totals(m)
    L=['### API coverage matrix','',
       f'**{T["covered"]} of {T["calls"]} public calls** ({T["declarations"]} declarations) are covered by a bench test; '
       f'**{T["gaps"]} are gaps**, each with its reason; plus {T["drivers"]} public driver calls with no HAL wrapper. '
       f'Build-only tier: {T["build"]["covered"]} compile and link in every configuration'
       +(f', {T["build"]["known"]} known failures' if T['build']['known'] else '')
       +(f', {T["build"]["missing"]} not called in `tests/firmware/api_compile`' if T['build']['missing'] else '')+'. '
       f'Timing: {T["timed"]} calls timed'+(f', **{T["regressed"]} regressed**' if T['regressed'] else '')+'.','',
       '| Module | '+' | '.join(TIER_WORD[t] for t in TIERS)+' | Covered | Gaps (reasons) | Timing |',
       '|---|'+'---|'*len(TIERS)+'---:|---|---|']
    for obj in m['objects']:
        s=obj_summary(m,obj)
        tm=(f'{s["timed"]} timed' if s['timed'] else '')+(f', **{s["regressed"]} regressed**' if s['regressed'] else '')
        L.append(f'| `{obj}`{" "+s["ticket"] if s["ticket"] else ""} | '+' | '.join(cell_md(cell_counts(m,obj,t)) for t in TIERS)
                 +f' | {s["covered"]}/{s["total"]} | {s["gaps"]}'+(f' ({reason_text(s["reasons"])})' if s['reasons'] else '')+f' | {tm} |')
    L+=['',f'{SYM["covered"]} covered · {SYM["not_run"]} covered, not run yet · {SYM["known"]} known failure · {SYM["regression"]} regression · '
        f'{SYM["gap"]} gap (counted in its target tier) · (n→) runs at another tier now, current → target. '
        'Picture: `coverage.svg`. Generated by `tests/common/coverage.py`.','']
    reg=[(r['id'],x) for r in m['calls'] for t in r['tiers'].values() for x in t['tests'] if x['state']=='regression']
    if reg:
        L+=['**Regressions / new failures**','']+[f'- `{c}`: {x["id"]} ({x["note"]})' for c,x in reg]+['']
    moved=[(r['id'],x) for r in m['calls'] if r['timing'] in ('regressed','improved')
           for x in [m['timing']['by_call'][r['id']]]]
    if moved:
        L+=['**Timing moved beyond the threshold**','']+[f'- `{c}`: {x["mark"]} ({", ".join(sorted(x["rows"]))})' for c,x in moved]+['']
    if problems:L+=check_lines(m)
    return '\n'.join(L)+'\n'


def check_lines(m):
    p=m['problems'];L=[]
    if p['missing']:
        L+=[f'**{len(p["missing"])} public calls have no matrix row** (add a test that `covers` them in `tests/catalogue.json`, '
            'or their gap reason in `tests/coverage_gaps.json`):','']+[f'- `{c}`' for c in p['missing']]+['']
    if p['unknown_covers']:
        L+=['**`covers` names a call that is not public** (tools/api_calls.py list):','']+[f'- {t}: `{c}`' for t,c in p['unknown_covers']]+['']
    if p['bad_gaps']:L+=['**Malformed gap entries**:','']+[f'- {x}' for x in p['bad_gaps']]+['']
    if p['stale_gaps']:L+=[f'Gap entries for calls a test now covers (remove them from `tests/coverage_gaps.json`): '+', '.join(f'`{c}`' for c in p['stale_gaps']),'']
    if p['gone_gaps']:L+=['Gap entries for calls that are no longer public: '+', '.join(f'`{c}`' for c in p['gone_gaps']),'']
    if p['gone_drivers']:L+=['Driver gap entries not declared in their header any more: '+', '.join(f'`{c}`' for c in p['gone_drivers']),'']
    if not (p['missing'] or p['unknown_covers'] or p['bad_gaps']):L+=['**Coverage check: every public call has a matrix row.**','']
    return L


# ---------------------------------------------------------------- control center

def _ago(ts):
    d=dt.datetime.now()-dt.datetime.fromtimestamp(ts)
    return f'{d.days} d ago' if d.days else (f'{d.seconds//3600} h ago' if d.seconds>=3600 else f'{d.seconds//60} min ago')


def _link(c):
    return f'{e(c["dir"].name)}/report.html' if c['report'] else f'{e(c["dir"].name)}/record.md'


def overview_html(m):
    T=totals(m);n=T['calls']
    st=T['states']
    segs=[('pass',st['covered']),('not_run',st['not_run']),('known',st['known']),('fail',st['regression']),('planned',st['gap'])]
    bar='<div class="bar" role="img" aria-label="coverage">'+''.join(
        f'<span class="seg s-{k}" style="flex:{v}" title="{v}"></span>' for k,v in segs if v)+'</div>'
    txt=(f'{n} public calls: ✓ {st["covered"]} covered · ○ {st["not_run"]} not run yet · ! {st["known"]} known failure · '
         f'✗ {st["regression"]} regression · · {st["gap"]} gaps · {T["timed"]} timed'+(f' · {T["regressed"]} timing regressed' if T['regressed'] else ''))
    return f'<button class="ov" data-go="coverage"><b>API coverage</b>{bar}<span class="sub">{e(txt)}</span></button>'


def coverage_tab_html(m):
    p=['<p class="q"><b>Which public calls does a test cover, in which tier, and why is each gap a gap?</b></p>',
       '<div class="note">Generated from tests/catalogue.json (covers, tier, target_tier) against the public headers '
       '(tools/api_calls.py) and tests/coverage_gaps.json; results are the newest campaign of each test. '
       'Regenerate: python3 tests/common/coverage.py render. CI: coverage.yml fails when a public call has no row.</div>',
       '<h2>Latest campaign of each tier</h2><div class="wrap"><table><tr><th>Tier</th><th>Campaign</th><th>Results of this tier\'s tests</th><th>Calls covered (target tier)</th></tr>']
    T=totals(m)
    for t in TIERS:
        if t=='build-only':
            camp=(f'GitHub-hosted (<code>api-build.yml</code>, <code>examples.yml</code>): every call in '
                  '<code>tests/firmware/api_compile/main.cpp</code>, in none/twist/ownverter/twist130')
            res=f'{T["build"]["covered"]} compile'+(f' · {T["build"]["known"]} known failures' if T['build']['known'] else '')+(f' · {T["build"]["missing"]} not called' if T['build']['missing'] else '')
            cov=f'{T["build"]["covered"]}/{T["calls"]}'
        else:
            c=m['tier_campaigns'].get(t)
            if c:
                camp_,rs=c
                camp=(f'<a href="{_link(camp_)}">{e(camp_["dir"].name)}</a><div class="sub">'
                      f'{dt.datetime.fromtimestamp(camp_["when"]).strftime("%Y-%m-%d %H:%M")} · {_ago(camp_["when"])} · {e(camp_["commit"])}</div>')
                res=' · '.join(f'{sum(r["verdict"]==v for r in rs)} {v}' for v in ('PASS','FAIL','INFO','BLOCKED') if any(r["verdict"]==v for r in rs))
            else:camp,res='<span class="sub">no campaign yet</span>',''
            n_=sum(t in r['tiers'] for r in m['calls'])
            g_=sum(1 for r in m['calls'] if not r['tiers'] and r['gap'] and r['gap'].get('target_tier')==t)
            cov=f'{n_} covered · {g_} gaps target it'
        p.append(f'<tr><td>{TIER_WORD[t]}</td><td>{camp}</td><td>{res}</td><td>{cov}</td></tr>')
    p.append('</table></div>')
    if not m['baseline']:
        p.append(f'<div class="note">No campaign at {BASELINE_TAG} found: a new FAIL shows as a regression until the baseline runs '
                 '(pass→fail = regression, fail→fail = known failure, #35).</div>')
    p.append('<h2>Picture</h2><div class="wrap" style="max-width:100%">'+svg(m).replace('<svg class="cv-svg"','<svg class="cv-svg" style="max-width:100%;height:auto"',1)+'</div>')
    p.append('<h2>Calls by module</h2>')
    reasons=m['gaps']['reasons']
    for obj in m['objects']:
        s=obj_summary(m,obj)
        rows=[r for r in m['calls']+m['drivers'] if r['object']==obj]
        head=(f'{s["covered"]}/{s["total"]} covered'+(f' · {s["gaps"]} gaps ({e(reason_text(s["reasons"],s["ticket"]))})' if s['gaps'] else '')
              +(f' · {s["timed"]} timed' if s['timed'] else '')+(f' · <b>{s["regressed"]} timing regressed</b>' if s['regressed'] else ''))
        p.append(f'<details><summary><code>{e(obj)}</code> <span class="sub">{head}</span></summary><div class="wrap"><table>'
                 '<tr><th>Call</th><th>State</th><th>Tests (current → target tier)</th><th>Build</th><th>Timing</th></tr>')
        for r in rows:
            if r['tiers']:
                tl='<br>'.join(f'{e(x["id"])} <span class="sub">{e(x["tier"])}'+(f' → {e(tg)}' if x['tier']!=tg else '')+
                               f' · {SYM[x["state"]]} {e(WORD[x["state"]])}'+(f': {e(x["note"])}' if x['note'] else '')+'</span>'
                               for tg,c in r['tiers'].items() for x in c['tests'])
            else:
                g=r['gap'] or {}
                why=reasons.get(g.get('reason'),g.get('reason',''))
                if g.get('reason')=='module' and m['gaps']['module_tickets'].get(r['object']):why+=f' ({m["gaps"]["module_tickets"][r["object"]]})'
                tl=(f'<span class="sub">gap → {e(g.get("target_tier",""))}'+(f' · {e(g["method"])}' if g.get('method') else '')+
                    f': {e(why)}'+(f'; {e(g["note"])}' if g.get('note') else '')+'</span>')
            b={'covered':'✓','known':'! known','missing':'· not called'}.get(r['build'],'–')
            tm={'timed':'timed','regressed':'<b>regressed</b>','improved':'improved: check it'}.get(r['timing'],'')
            nm=e(r['name'])+(f' <span class="sub">×{r["overloads"]}</span>' if r['overloads']>1 else '')+(f'<div class="sub">{e(r["guard"])}</div>' if r['guard'] else '')+(f'<div class="sub">{e(r["header"])}</div>' if r.get('header') else '')
            p.append(f'<tr><td><code>{nm}</code></td><td>{SYM[r["state"]]} {e(WORD[r["state"]])}</td><td>{tl}</td><td>{b}</td><td>{tm}</td></tr>')
        p.append('</table></div></details>')
    pr=check_lines(m)
    p.append('<h2>Coverage check</h2>'+''.join(f'<p class="sub">{re.sub(r"`([^`]+)`",r"<code>\1</code>",e(x)).replace("**","")}</p>' for x in pr if x))
    return '\n'.join(p)


def timing_tab_html(m):
    t=m['timing'];run=t['run']
    if not run:
        return '<h2>Cost per call</h2><p class="sub">No campaign with a timing run (timing.json) yet.</p>'
    c=t['campaign'];hz=run.get('core_hz') or 170000000;per=run.get('task_period_us',100)
    other=[k for k in t['refs'] if k!=t['against']]
    p=[f'<h2>Cost per call and task timing</h2><p class="sub">Newest timing run: <a href="{_link(c)}">{e(c["dir"].name)}</a> '
       f'({dt.datetime.fromtimestamp(c["when"]).strftime("%Y-%m-%d %H:%M")}, {_ago(c["when"])}) · {e(run.get("firmware",""))} · '
       f'{run.get("repeats")} samples per call, {run.get("task_runs")} task runs, {hz/1e6:.0f} MHz · against reference '
       f'<code>{e(str(t["against"]))}</code> (tests/l0/target_timing/reference.json). A call regresses beyond '
       'max(10 %, 3σ) + 50 cycles of the reference; faster by the same margin is "improved: check it".</p>',
       '<div class="wrap"><table><tr><th>Row</th><th>Call</th><th>Mean</th><th>σ</th><th>Worst</th><th>Mean µs</th>'
       f'<th>% of {per} µs</th><th>Ref. {e(str(t["against"]))}</th>'+''.join(f'<th>Ref. {e(k)}</th>' for k in other)+'<th>Verdict</th></tr>']
    for name,call,row,ref,v,note in t['rows']:
        cls={'FAIL':' style="color:var(--bad);font-weight:600"','PASS':''}.get(v,'')
        if note.startswith('improved'):cls=' style="color:var(--warn);font-weight:600"'
        if row is None:
            p.append(f'<tr><td><code>{e(name)}</code></td><td>{e(call or "")}</td><td colspan="6">no sample</td><td{cls}>{e(v)}: {e(note)}</td></tr>');continue
        us_=t['us'](row['mean'],hz)
        oth=''.join(f'<td>{t["refs"][k]["rows"].get(name,{}).get("mean","–")}</td>' for k in other)
        p.append(f'<tr><td><code>{e(name)}</code></td><td>{"<code>"+e(call)+"</code>" if call else "<span class=sub>not a call</span>"}</td>'
                 f'<td>{row["mean"]:.1f}</td><td>{row["sigma"]:.1f}</td><td>{row["worst"]}</td><td>{us_:.3f}</td><td>{100*us_/per:.2f}</td>'
                 f'<td>{ref["mean"] if ref else "–"}</td>{oth}<td{cls}>{e(v)}: {e(note)}</td></tr>')
    p.append('</table></div>')
    return '\n'.join(p)


def sections(cat,camps):
    """@brief For control_center.py: {'overview': html, 'tabs': [(id, title, html)], 'append': {tab id: html}}."""
    cur,base,_=split_baseline(camps)
    m=matrix(cat,cur,baseline_camps=base)
    timing_html=timing_tab_html(m)
    has_timing=any(t['id']=='timing' for t in cat['tabs'])
    return {'overview':overview_html(m),'tabs':[('coverage','Coverage',coverage_tab_html(m))]
            +([] if has_timing else [('timing','Timing',timing_html)]),
            'append':{'timing':timing_html} if has_timing else {}}


# ---------------------------------------------------------------- CLI

def all_campaigns(dirs):
    import control_center as cc
    out=[]
    for d in dirs:
        if Path(d).is_dir():out+=cc.campaigns(Path(d))
    return sorted(out,key=lambda c:-c['when'])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['check','render'])
    ap.add_argument('--campaigns',action='append',type=Path,help='campaign folder(s) to read (default tests/out)')
    ap.add_argument('--baseline',action='append',type=Path,default=[],help=f'baseline campaign folder(s) ({BASELINE_TAG})')
    ap.add_argument('--write',type=Path,default=REPO/'tests/out/coverage',help='where render writes its files')
    ap.add_argument('--summary',help='append the Markdown report (e.g. $GITHUB_STEP_SUMMARY)')
    a=ap.parse_args()
    try:cat=catalogue.load()
    except ValueError as ex:print(ex,file=sys.stderr);return 2
    if a.command=='check':
        m=matrix(cat,[],baseline_camps=[])
        lines=check_lines(m);report='### Coverage check\n\n'+'\n'.join(lines)+'\n'
        print(report)
        p=m['problems']
        for c in p['missing']:print(f'::error file=tests/coverage_gaps.json::{c} has no matrix row: a catalogue test must cover it, or coverage_gaps.json give its gap reason')
        for t,c in p['unknown_covers']:print(f'::error file=tests/catalogue.json::{t} covers {c}, which is not a public call')
        for x in p['bad_gaps']:print(f'::error file=tests/coverage_gaps.json::{x}')
        for c in p['stale_gaps']:print(f'::warning file=tests/coverage_gaps.json::{c} is covered by a test now: remove its gap entry')
        for c in p['gone_gaps']+p['gone_drivers']:print(f'::warning file=tests/coverage_gaps.json::{c} is not declared any more: remove its gap entry')
        T=totals(m)
        print(f'{T["calls"]} public calls: {T["covered"]} covered, {T["gaps"]} gaps; {T["drivers"]} unwrapped driver calls',file=sys.stderr)
        if a.summary:
            with open(a.summary,'a') as f:f.write(report+'\n'+markdown(m,problems=False)+'\n')
        return 1 if (p['missing'] or p['unknown_covers'] or p['bad_gaps']) else 0
    cur,base,_=split_baseline(all_campaigns(a.campaigns or [REPO/'tests/out']),a.baseline)
    base+=all_campaigns(a.baseline)
    m=matrix(cat,cur,baseline_camps=base)
    a.write.mkdir(parents=True,exist_ok=True)
    (a.write/'coverage.svg').write_text(svg(m),encoding='utf-8')
    md_=markdown(m);(a.write/'coverage.md').write_text(md_,encoding='utf-8')
    slim={k:m[k] for k in ('calls','drivers','objects','problems','baseline','catalogue','head')}
    slim['totals']=totals(m)
    slim['tiers']={obj:{t:cell_counts(m,obj,t) for t in TIERS} for obj in m['objects']}
    (a.write/'matrix.json').write_text(json.dumps(slim,indent=1,default=list)+'\n',encoding='utf-8')
    if a.summary:
        with open(a.summary,'a') as f:f.write(md_+'\n')
    print(a.write/'coverage.svg')
    return 0


if __name__=='__main__':sys.exit(main())
