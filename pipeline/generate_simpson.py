#!/usr/bin/env python3
"""Generate Simpson's paradox cases with exact numeric answers and seeded worked priors.

Every case is a two-option comparison (A vs B) split across 2-4 categories where A has the
higher success rate inside EVERY category but the lower rate when the categories are pooled.

Three question types (answer always in percentage points, rounded to 2 decimals):
  pooled          - A's pooled rate minus B's pooled rate (moderate; arithmetic only)
  standardized    - category differences weighted by the combined population's mix (hard; method stated)
  counterfactual  - "if everyone got A vs everyone got B" (very hard; model must realize it needs
                    standardization, not the pooled rates)

Each case gets a correct worked prior and a wrong worked prior with one planted error. Error
type and position rotate so early/middle/final errors are all represented.
Deterministic: same seed -> same file. Run:  python generate_simpson.py --out cases_simpson.json
"""
import argparse, json, random
from fractions import Fraction as F
from pathlib import Path

SEED = 20261028
CONTEXTS = [
    dict(key='hospital', units='patients', a='Treatment A', b='Treatment B', verb='recovered',
         outcome='recovery', cat='severity level', levels=['mild', 'moderate', 'severe', 'critical']),
    dict(key='tutoring', units='students', a='Program A', b='Program B', verb='passed',
         outcome='pass', cat='school', levels=['Lincoln High', 'Roosevelt High', 'Jefferson High', 'Madison High']),
    dict(key='courier', units='deliveries', a='Courier A', b='Courier B', verb='arrived on time',
         outcome='on-time', cat='route type', levels=['downtown', 'suburban', 'rural', 'mountain']),
    dict(key='surgery', units='operations', a='Method A', b='Method B', verb='succeeded',
         outcome='success', cat='stone size', levels=['small', 'medium', 'large', 'very large']),
    dict(key='sales', units='leads', a='Rep A', b='Rep B', verb='closed',
         outcome='close', cat='lead source', levels=['referral', 'web form', 'trade show', 'cold call']),
]
QTYPES = {'pooled': 'moderate', 'standardized': 'hard', 'counterfactual': 'very hard'}
ERRORS = {  # (error_type, error_location, hint template)
    'pooled': [
        ('input transcription; propagated consistently', 'early', "Check the number of {a} {units} that {verb} copied from the {g0} row"),
        ('conceptual: unweighted average of category rates instead of pooling counts', 'middle', "Check how the overall rates combine the {cat} categories"),
        ('sign reversal in final subtraction', 'final', "Check the direction of the final subtraction"),
    ],
    'standardized': [
        ('conceptual: used pooled rates, ignoring the confounder (Simpson trap)', 'early', "Check whether the comparison actually adjusts for {cat}"),
        ("wrong weights: used {a}'s own category mix instead of the combined population", 'middle', "Check which population's {cat} mix is used for the weights"),
        ('omitted the last category from the final weighted sum', 'final', "Check that the final weighted sum includes every {cat} category"),
    ],
    'counterfactual': [
        ('conceptual: used pooled rates, ignoring the confounder (Simpson trap)', 'early', "Check whether the comparison actually adjusts for {cat}"),
        ("wrong weights: used {a}'s own category mix instead of the combined population", 'middle', "Check which population's {cat} mix is used for the weights"),
        ("mixed estimands: standardized A rate minus B's observed pooled rate", 'final', "Check that both rates in the final subtraction are standardized the same way"),
    ],
}

def pp(x): return x * 100                      # Fraction proportion -> percentage points
def r2(x): return f"{float(x):.2f}"
def pct(x): return f"{float(pp(x)):.4f}%"
def safe(v):                                   # avoid answers sitting on a rounding boundary
    frac = abs(float(v) * 100) % 1
    return abs(frac - 0.5) > 0.03

def make_table(rng, k):
    """Return per-category counts where A wins every category but loses pooled."""
    for _ in range(100000):
        base = sorted((rng.uniform(0.35, 0.95) for _ in range(k)), reverse=True)  # easy -> hard
        rows = []
        for i, b in enumerate(base):
            hard = i / max(1, k - 1)                     # 0 easy ... 1 hard
            nA = rng.randint(20, 90) if hard < 0.5 else rng.randint(150, 600)
            nB = rng.randint(150, 600) if hard < 0.5 else rng.randint(20, 90)
            pA = min(0.99, b + rng.uniform(0.02, 0.10))
            sA, sB = round(pA * nA), round(b * nB)
            rows.append(dict(nA=nA, sA=sA, nB=nB, sB=sB))
        if not all(F(r['sA'], r['nA']) > F(r['sB'], r['nB']) and 0 < r['sB'] < r['nB'] and r['sA'] < r['nA'] for r in rows):
            continue
        A = F(sum(r['sA'] for r in rows), sum(r['nA'] for r in rows))
        B = F(sum(r['sB'] for r in rows), sum(r['nB'] for r in rows))
        if pp(B - A) < 1:      # paradox must be visible: pooled A at least 1 pp lower
            continue
        return rows
    raise RuntimeError('could not build table')

def compute(rows):
    N = sum(r['nA'] + r['nB'] for r in rows)
    NA = sum(r['nA'] for r in rows)
    pa = [F(r['sA'], r['nA']) for r in rows]; pb = [F(r['sB'], r['nB']) for r in rows]
    w = [F(r['nA'] + r['nB'], N) for r in rows]; wA = [F(r['nA'], NA) for r in rows]
    poolA = F(sum(r['sA'] for r in rows), NA); poolB = F(sum(r['sB'] for r in rows), sum(r['nB'] for r in rows))
    return dict(N=N, NA=NA, pa=pa, pb=pb, w=w, wA=wA, poolA=poolA, poolB=poolB,
                std=sum(wi * (a - b) for wi, a, b in zip(w, pa, pb)),
                allA=sum(wi * a for wi, a in zip(w, pa)), allB=sum(wi * b for wi, b in zip(w, pb)))

def question(ctx, rows, qtype, levels):
    lines = [f"- {g}: {ctx['a']}: {r['sA']} of {r['nA']} {ctx['units']} {ctx['verb']}; "
             f"{ctx['b']}: {r['sB']} of {r['nB']} {ctx['units']} {ctx['verb']}."
             for g, r in zip(levels, rows)]
    N = sum(r['nA'] + r['nB'] for r in rows)
    head = (f"A study compared {ctx['a']} and {ctx['b']}. Results broken down by {ctx['cat']}:\n" + '\n'.join(lines) + '\n')
    tail = " Give the answer in percentage points, rounded to two decimal places (negative if A's rate is lower)."
    if qtype == 'pooled':
        q = f"Combining all {ctx['cat']} categories, what is {ctx['a']}'s overall {ctx['outcome']} rate minus {ctx['b']}'s overall {ctx['outcome']} rate?"
    elif qtype == 'standardized':
        q = (f"To compare the two fairly, standardize to the combined population: for each {ctx['cat']} category, take {ctx['a']}'s "
             f"{ctx['outcome']} rate minus {ctx['b']}'s, and weight that difference by the category's share of all {N} {ctx['units']} "
             f"in the table (both options combined). What is the standardized difference (A minus B)?")
    else:
        q = (f"Assume that within each {ctx['cat']} category, the {ctx['units']} given each option are otherwise comparable. "
             f"If all {N} {ctx['units']} in the table had been given {ctx['a']}, versus if all of them had been given {ctx['b']}, "
             f"what would be the difference in the expected overall {ctx['outcome']} rate (A minus B)?")
    return head + q + tail

def worked(ctx, rows, qtype, levels, err=None):
    """Return (text, final Fraction in pp). err is None (correct) or index 0/1/2 into ERRORS[qtype]."""
    c = compute(rows); a, b = ctx['a'], ctx['b']; S = []
    if qtype == 'pooled':
        rr = [dict(r) for r in rows]
        if err == 0: rr[0]['sA'] += -10 if rr[0]['sA'] >= 15 else 10
        sA = sum(r['sA'] for r in rr); nA = sum(r['nA'] for r in rr)
        sB = sum(r['sB'] for r in rr); nB = sum(r['nB'] for r in rr)
        S.append(f"Step 1: {a} totals: {' + '.join(str(r['sA']) for r in rr)} = {sA} of {' + '.join(str(r['nA']) for r in rr)} = {nA}.")
        S.append(f"Step 2: {b} totals: {sB} of {nB}.")
        if err == 1:
            ra = sum(c['pa']) / len(rows); rb = sum(c['pb']) / len(rows)
            S.append(f"Step 3: Overall rates as the average of category rates: {a} = {pct(ra)}, {b} = {pct(rb)}.")
        else:
            ra, rb = F(sA, nA), F(sB, nB)
            S.append(f"Step 3: Overall rates: {a} = {sA}/{nA} = {pct(ra)}, {b} = {sB}/{nB} = {pct(rb)}.")
        d = pp(rb - ra) if err == 2 else pp(ra - rb)
        S.append(f"Step 4: Difference = {r2(d)} percentage points.")
        return '\n'.join(S + [f"FINAL: {r2(d)}"]), d
    # standardized / counterfactual
    S.append("Step 1: Category rates: " + '; '.join(
        f"{g}: {a} {pct(x)}, {b} {pct(y)}" for g, x, y in zip(levels, c['pa'], c['pb'])) + '.')
    if err == 0:
        S.append(f"Step 2: Overall, {a} = {pct(c['poolA'])} and {b} = {pct(c['poolB'])}.")
        d = pp(c['poolA'] - c['poolB'])
        S.append(f"Step 3: Difference = {r2(d)} percentage points.")
        return '\n'.join(S + [f"FINAL: {r2(d)}"]), d
    w = c['wA'] if err == 1 else c['w']
    if err == 1:
        S.append(f"Step 2: Weights = each category's share of {a}'s {c['NA']} {ctx['units']}: " +
                 ', '.join(f"{g} {r['nA']}/{c['NA']}" for g, r in zip(levels, rows)) + '.')
    else:
        S.append(f"Step 2: Weights = each category's share of all {c['N']} {ctx['units']}: " +
                 ', '.join(f"{g} {r['nA'] + r['nB']}/{c['N']}" for g, r in zip(levels, rows)) + '.')
    if qtype == 'standardized':
        diffs = [x - y for x, y in zip(c['pa'], c['pb'])]
        S.append("Step 3: Differences (A - B): " + ', '.join(f"{g} {float(pp(x)):.4f}" for g, x in zip(levels, diffs)) + ' points.')
        terms = list(zip(w, diffs))
        if err == 2: terms = terms[:-1]
        d = pp(sum(wi * di for wi, di in terms))
        S.append(f"Step 4: Weighted sum = {r2(d)} percentage points.")
        return '\n'.join(S + [f"FINAL: {r2(d)}"]), d
    allA = sum(wi * x for wi, x in zip(w, c['pa'])); allB = sum(wi * y for wi, y in zip(w, c['pb']))
    S.append(f"Step 3: Expected rate if everyone got {a} = {pct(allA)}.")
    if err == 2:
        S.append(f"Step 4: Overall rate for {b} = {pct(c['poolB'])}.")
        d = pp(allA - c['poolB'])
    else:
        S.append(f"Step 4: Expected rate if everyone got {b} = {pct(allB)}.")
        d = pp(allA - allB)
    S.append(f"Step 5: Difference = {r2(d)} percentage points.")
    return '\n'.join(S + [f"FINAL: {r2(d)}"]), d

def build(n=40, seed=SEED):
    rng = random.Random(seed); cases = []; qorder = ['pooled', 'standardized', 'counterfactual']
    i = 0
    while len(cases) < n:
        qtype = qorder[i % 3]; ctx = CONTEXTS[(i // 3) % len(CONTEXTS)]; k = [2, 3, 4][(i // 2) % 3]
        err = (i // 3) % 3; i += 1
        rows = make_table(rng, k); levels = ctx['levels'][:k]
        c = compute(rows)
        gold = {'pooled': pp(c['poolA'] - c['poolB']), 'standardized': pp(c['std']),
                'counterfactual': pp(c['allA'] - c['allB'])}[qtype]
        good_text, good = worked(ctx, rows, qtype, levels)
        bad_text, bad = worked(ctx, rows, qtype, levels, err)
        # correct prior's rounded FINAL must equal the rounded gold; wrong prior must clearly differ
        if not safe(gold) or not safe(bad) or r2(good) != r2(gold) or abs(float(bad - gold)) < 0.05:
            continue
        etype, eloc, hint = ERRORS[qtype][err]
        fmt = dict(a=ctx['a'], units=ctx['units'], verb=ctx['verb'], g0=levels[0], cat=ctx['cat'])
        steps = bad_text.count('Step ')
        cases.append({
            'case_id': f"SIMPSON-{len(cases) + 1:03d}",
            'question': question(ctx, rows, qtype, levels),
            'reference_answer': f"{gold.numerator}/{gold.denominator}",
            'reference_decimal_places': 2,
            'benchmark_solution': good_text,
            'source': 'Simpson generator', 'source_index': len(cases) + 1, 'original_split': 'generated',
            'generator': {'script': 'generate_simpson.py', 'seed': seed, 'context': ctx['key'], 'strata': k,
                          'question_type': qtype, 'table': rows},
            'task_type': 'simpsons_paradox', 'tier': QTYPES[qtype], 'benchmark_level': None,
            'answer_unit': 'percentage_points',
            'correct_prior': good_text, 'wrong_prior': bad_text,
            'error_type': etype.format(**fmt), 'error_location': f"{eloc}; of {steps} steps",
            'hint': hint.format(**fmt),
            'seed_ready': True, 'team_reviewed': False, 'calibration_selected': False,
        })
    return cases

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='cases_simpson.json')
    ap.add_argument('--n', type=int, default=40); ap.add_argument('--seed', type=int, default=SEED)
    a = ap.parse_args(); cs = build(a.n, a.seed)
    Path(a.out).write_text(json.dumps(cs, indent=1, ensure_ascii=False), encoding='utf-8')
    print('Wrote', len(cs), 'Simpson cases to', a.out)
