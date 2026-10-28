#!/usr/bin/env python3
"""Compare ways of predicting finishing order from earlier results.

Every model walks through events in date order. Before each event it scores
each couple (higher = expected to place better) using only earlier results,
then it sees the result. Settings are tuned on 2025 events; the table reports
2026 events with 6+ couples: rank correlation between predicted and actual
order, share of finalists picked (top 6), and winners picked.

Usage:
    python benchmark.py
"""

import datetime as dt
import itertools
import json
import math
import os
from collections import defaultdict

import numpy as np

import ratings as R

TRAIN, TEST = ('2025-01-01', '2026-01-01'), ('2026-01-01', '2100-01-01')


def load():
    """Events with, per couple: place, members, share of field beaten, share of judges' marks."""
    out = []
    for date, comp, ev, couples in R.load_events():
        rows = json.load(open(os.path.join(R.OUT, f'{comp}.json')))
        evc = next(e for e in rows['events'] if e['n'] == ev['n'])['c']
        n = len(couples)
        places = [p for _, p, _ in couples]
        cs = []
        for idx, place, ms in couples:
            beaten = (sum(q > place for q in places) + 0.5 * (sum(q == place for q in places) - 1)) / (n - 1)
            got = avail = 0
            for k, rec in enumerate(evc[idx][5]):
                if rec:
                    got += sum(rec)
                    avail += ev['j'][k] * len(rec)
            cs.append({'place': place, 'ms': ms, 'beaten': beaten, 'marks': got / avail if avail else None})
        out.append({'date': date, 'day': dt.date.fromisoformat(date).toordinal(), 'comp': comp,
                    'style': ev['st'], 'level': ev['lv'], 'c': cs})
    return out


# ---------------------------------------------------------------- models
# Each model: predict(event) -> list of scores, then update(event).

class FormGuide:
    """Average of a per-event signal (share beaten, share of marks, or both), optionally recency-weighted."""

    def __init__(self, signal='beaten', half_life=None, last_only=False):
        self.signal, self.half_life, self.last_only = signal, half_life, last_only
        self.h = defaultdict(list)

    def value(self, c):
        if self.signal == 'beaten':
            return c['beaten']
        if self.signal == 'marks':
            return c['marks']
        return c['beaten'] if c['marks'] is None else (c['beaten'] + c['marks']) / 2

    def person(self, key, day):
        h = self.h.get(key)
        if not h:
            return 0.5
        if self.last_only:
            return h[-1][1]
        if not self.half_life:
            return sum(v for _, v in h) / len(h)
        w = [0.5 ** ((day - d) / self.half_life) for d, _ in h]
        return sum(wi * v for wi, (_, v) in zip(w, h)) / sum(w)

    def predict(self, e):
        return [sum(self.person((e['style'], m), e['day']) for m in c['ms']) / len(c['ms']) for c in e['c']]

    def update(self, e):
        for c in e['c']:
            v = self.value(c)
            if v is None:
                continue
            for m in c['ms']:
                self.h[(e['style'], m)].append((e['day'], v))


class Elo:
    """Multi-player Elo: K x (share beaten - expected share), K by experience, starts by level."""

    def __init__(self, k_early=768, k_late=768, k_half=1, level_step=0, exp_half_life=None, pull=0.0):
        self.k_early, self.k_late, self.k_half = k_early, k_late, k_half
        self.level_step, self.exp_half_life, self.pull = level_step, exp_half_life, pull
        self.r, self.n, self.seen = {}, defaultdict(float), {}

    def start(self, level):
        return 1500 + self.level_step * (R.LEVEL_INDEX.get(level, 1) - 1)

    def team(self, e):
        out = []
        for c in e['c']:
            for m in c['ms']:
                self.r.setdefault((e['style'], m), self.start(e['level']))
            out.append(sum(self.r[(e['style'], m)] for m in c['ms']) / len(c['ms']))
        return out

    def predict(self, e):
        return self.team(e)

    def update(self, e):
        t = self.team(e)
        n = len(t)
        for i, c in enumerate(e['c']):
            exp = sum(R.beats(t[i], t[j]) for j in range(n) if j != i) / (n - 1)
            for m in c['ms']:
                key = (e['style'], m)
                if self.exp_half_life and key in self.seen:
                    self.n[key] *= 0.5 ** ((e['day'] - self.seen[key]) / self.exp_half_life)
                self.seen[key] = e['day']
                k = self.k_late + (self.k_early - self.k_late) / (1 + self.n[key] / self.k_half)
                self.r[key] += k * (c['beaten'] - exp)
                self.n[key] += 1
            if len(c['ms']) == 2 and self.pull:
                keys = [(e['style'], m) for m in c['ms']]
                mean = sum(self.r[k] for k in keys) / 2
                for k in keys:
                    self.r[k] = mean + (self.r[k] - mean) * (1 - self.pull)


class FadingPerformance:
    """Rating = fading average of per-event performance ratings (the live model)."""

    def __init__(self, half_life=90, prior=0.1, level_step=400):
        self.half_life, self.prior, self.level_step = half_life, prior, level_step
        self.h, self.s = defaultdict(list), {}

    def rating(self, key, day):
        w0, tot = self.prior, self.prior * self.s[key]
        for d, p in self.h[key]:
            w = 0.5 ** ((day - d) / self.half_life)
            w0 += w
            tot += w * p
        return tot / w0

    def team(self, e):
        out = []
        for c in e['c']:
            for m in c['ms']:
                self.s.setdefault((e['style'], m), 1500 + self.level_step * (R.LEVEL_INDEX.get(e['level'], 1) - 1))
            out.append(sum(self.rating((e['style'], m), e['day']) for m in c['ms']) / len(c['ms']))
        return out

    def predict(self, e):
        return self.team(e)

    def update(self, e):
        t = np.array(self.team(e))
        n = len(t)
        share = np.array([(c['beaten'] * (n - 1) + 0.5) / n for c in e['c']])
        perf = R.performance(t, share)
        for i, c in enumerate(e['c']):
            for m in c['ms']:
                self.h[(e['style'], m)].append((e['day'], float(perf[i])))


class OpenSkill:
    """Weng-Lin Plackett-Luce (OpenSkill) with level-based starting means."""

    def __init__(self, beta=25 / 6, sigma0=25 / 3, tau=25 / 300, level_step=0.0):
        self.beta, self.sigma0, self.tau, self.level_step = beta, sigma0, tau, level_step
        self.mu, self.sig = {}, {}

    def ensure(self, e):
        for c in e['c']:
            for m in c['ms']:
                k = (e['style'], m)
                if k not in self.mu:
                    self.mu[k] = 25 + self.level_step * (R.LEVEL_INDEX.get(e['level'], 1) - 1)
                    self.sig[k] = self.sigma0

    def predict(self, e):
        self.ensure(e)
        return [sum(self.mu[(e['style'], m)] for m in c['ms']) for c in e['c']]

    def update(self, e):
        self.ensure(e)
        st = e['style']
        for c in e['c']:
            for m in c['ms']:
                self.sig[(st, m)] = math.sqrt(self.sig[(st, m)] ** 2 + self.tau ** 2)
        teams = [(sum(self.mu[(st, m)] for m in c['ms']), sum(self.sig[(st, m)] ** 2 for m in c['ms'])) for c in e['c']]
        ranks = [c['place'] for c in e['c']]
        cc = math.sqrt(sum(s2 + self.beta ** 2 for _, s2 in teams))
        ex = [math.exp(mu / cc) for mu, _ in teams]
        n = len(teams)
        sum_q = [sum(ex[i] for i in range(n) if ranks[i] >= ranks[q]) for q in range(n)]
        a = [sum(1 for r in ranks if r == ranks[q]) for q in range(n)]
        for i, c in enumerate(e['c']):
            omega = delta = 0.0
            for q in range(n):
                if ranks[q] <= ranks[i]:
                    quot = ex[i] / sum_q[q]
                    delta += quot * (1 - quot) / a[q]
                    omega += ((1 - quot) if q == i else -quot) / a[q]
            s2 = teams[i][1]
            omega *= s2 / cc
            delta *= s2 / cc ** 2 * (math.sqrt(s2) / cc)
            for m in c['ms']:
                k = (st, m)
                share = self.sig[k] ** 2 / s2
                self.mu[k] += share * omega
                self.sig[k] *= math.sqrt(max(1 - share * delta, 1e-4))


# ---------------------------------------------------------------- evaluation

def evaluate(model, events, window):
    """Mean rank correlation, finalists picked, winners picked; plus raw predictions for blends."""
    rho, fin, win, preds = [], [], [], []
    for e in events:
        if e['date'] >= window[1]:
            break
        p = model.predict(e)
        if e['date'] >= window[0] and len(e['c']) >= 6:
            places = [c['place'] for c in e['c']]
            rho.append(R.spearman([-x for x in p], places))
            finalists = {i for i, pl in enumerate(places) if pl <= 6}
            top = sorted(range(len(p)), key=lambda i: -p[i])
            fin.append(len(finalists & set(top[:6])) / max(1, min(6, len(finalists))))
            win.append(places[top[0]] == min(places))
            preds.append((e, p))
        model.update(e)
    mean = lambda v: sum(v) / len(v)
    return mean(rho), mean(fin), mean(win), preds


def tune(make, grid, events):
    """Best settings by 2025 rank correlation."""
    best = None
    for params in grid:
        score = evaluate(make(*params), events, TRAIN)[0]
        if best is None or score > best[0]:
            best = (score, params)
    return best


def zscores(p):
    a = np.array(p, dtype=float)
    return (a - a.mean()) / (a.std() or 1)


def blend_eval(pred_sets, weights):
    """Rank correlation etc. of a weighted sum of z-scored predictions (aligned event lists)."""
    rho, fin, win = [], [], []
    for parts in zip(*pred_sets):
        e = parts[0][0]
        score = sum(w * zscores(p) for w, (_, p) in zip(weights, parts))
        places = [c['place'] for c in e['c']]
        rho.append(R.spearman([-x for x in score], places))
        finalists = {i for i, pl in enumerate(places) if pl <= 6}
        top = list(np.argsort(-score))
        fin.append(len(finalists & set(top[:6])) / max(1, min(6, len(finalists))))
        win.append(places[top[0]] == min(places))
    mean = lambda v: sum(v) / len(v)
    return mean(rho), mean(fin), mean(win)


def main():
    events = load()
    models = {
        'Average placement score (baseline)': (lambda: FormGuide('beaten'), [()]),
        'Last result only': (lambda: FormGuide('beaten', last_only=True), [()]),
        'Recent placement score (fading)': (lambda h: FormGuide('beaten', h), [(h,) for h in (60, 120, 240, 480)]),
        'Average share of judges’ marks': (lambda h: FormGuide('marks', h), [(None,), (120,), (240,)]),
        'Placement + marks': (lambda h: FormGuide('both', h), [(None,), (120,), (240,)]),
        'Elo, constant K': (lambda k: Elo(k, k), [(k,) for k in (256, 512, 768, 1024)]),
        'Elo, experience K + level starts': (lambda e, l, s: Elo(e, l, 3, s),
                                             list(itertools.product((768, 1024), (256, 384), (300, 400)))),
        'Elo + experience fading': (lambda l, h: Elo(768, l, 3, 400, h), list(itertools.product((256, 384), (120, 180, 365)))),
        'Elo + partner pull': (lambda p: Elo(768, 256, 3, 400, None, p), [(0.2,), (0.35,)]),
        'Fading performance average (live)': (lambda h, w: FadingPerformance(h, w, 400),
                                              list(itertools.product((60, 90, 180), (0.05, 0.1, 0.25)))),
        'OpenSkill + level starts': (lambda b, st: OpenSkill(beta=b, level_step=st),
                                     list(itertools.product((1.0, 2.0, 4.17), (0.0, 4.0, 8.0)))),
    }
    results, test_preds, train_preds = [], {}, {}
    for name, (make, grid) in models.items():
        score, params = tune(make, grid, events)
        rho, fin, win, preds = evaluate(make(*params), events, TEST)
        _, _, _, tpreds = evaluate(make(*params), events, TRAIN)
        test_preds[name], train_preds[name] = preds, tpreds
        results.append((rho, fin, win, name, params, score))
        print(f'{name:38} 2025 {score:.3f} | 2026 order {rho:.3f}, finalists {fin:.1%}, winners {win:.1%}  {params}',
              flush=True)

    # combinations: equal-weight average of the 3 best on 2025, and a 2025-tuned weighted blend
    ranked = sorted(results, key=lambda r: -r[5])
    top3 = [r[3] for r in ranked[:3]]
    eq = blend_eval([test_preds[n] for n in top3], [1, 1, 1])
    print(f'{"Average of 3 best on 2025":38} 2026 order {eq[0]:.3f}, finalists {eq[1]:.1%}, winners {eq[2]:.1%}  {top3}')
    pool = [r[3] for r in ranked[:5]]
    best = None
    for w in itertools.product((0, 0.5, 1, 2), repeat=len(pool)):
        if not any(w):
            continue
        s = blend_eval([train_preds[n] for n in pool], w)[0]
        if best is None or s > best[0]:
            best = (s, w)
    bl = blend_eval([test_preds[n] for n in pool], best[1])
    print(f'{"Blend weighted on 2025":38} 2026 order {bl[0]:.3f}, finalists {bl[1]:.1%}, winners {bl[2]:.1%}  '
          f'{dict(zip(pool, best[1]))}')


if __name__ == '__main__':
    main()
