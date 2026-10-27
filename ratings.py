#!/usr/bin/env python3
"""Dancer ratings from event placements (prototype).

Each event gives every couple a performance: the rating at which the Elo formula
expects a couple to beat exactly the share of the field they beat, given the
field's ratings going in. Beating a strong field is a higher performance than
beating a weak one. A dancer's rating in a style is the average of their
performances, each weighted by 2^(-age / HALF_LIFE), plus PRIOR_WEIGHT events'
worth of a starting rating for their first level. So recent results count most,
and partners who dance together get the same performances and converge on the
same rating once older separate results fade. A couple's strength is the average
of its two dancers.

Levels share one scale but start apart: couples who dance two adjacent levels at
one competition place about 20-35 placement-score points lower in the higher
one, so the starting rating rises LEVEL_STEP per level (Bronze = START).

Events are processed in date order, so a rating on any date only uses earlier
results. HALF_LIFE, PRIOR_WEIGHT and LEVEL_STEP were chosen on 2025 events and
checked on 2026 (--backtest), against each dancer's average placement score so
far as a baseline.

Outputs, read by site/index.html:
    site/data/ratings/<comp>.json   per event: field strength; per couple: expected place,
                                    performance, and each dancer's rating after the event
    site/data/ratings/boards.json   per style: dancers active in the past year

Usage:
    python ratings.py --backtest       # prediction accuracy vs the baseline
    python ratings.py                  # write the files above
"""

import argparse
import datetime as dt
import json
import math
import os
from collections import defaultdict

import numpy as np

from dsco_crawl import OUT, clean_school, fold, name_rules, person_key

STYLES = ('Standard', 'Smooth', 'Latin', 'Rhythm')
PROGRESSION_LEVELS = ('Newcomer', 'Bronze', 'Silver', 'Gold', 'Novice', 'Prechamp', 'Championship')
START, SCALE = 1500.0, 400.0
HALF_LIFE = 90.0      # days for a performance to count half as much
PRIOR_WEIGHT = 0.1    # events' worth of the starting rating
LEVEL_INDEX = {'Newcomer': 0, 'Bronze': 1, 'Silver': 2, 'Gold': 3, 'Novice': 4, 'Prechamp': 5,
               'Championship': 6, 'Syllabus': 2}
LEVEL_STEP = 400.0
MIN_COUPLES = 4
PROVISIONAL = 3       # events in a style before a rating counts as established
ACTIVE_DAYS = 365


def level_start(level):
    return START + LEVEL_STEP * (LEVEL_INDEX.get(level, 1) - 1)


def load_events():
    """[(date, comp key, event, [(index in event, place, [person keys])...])] in date order."""
    comps = json.load(open(os.path.join(OUT, 'comps.json')))
    alias, _, split, _ = name_rules()
    school_map = json.load(open(os.path.join(OUT, 'names.json'))).get('school', {})
    events = []
    for comp in comps:
        path = os.path.join(OUT, f'{comp["key"]}.json')
        if not comp.get('date') or not os.path.exists(path):
            continue
        for ev in json.load(open(path))['events']:
            if ev['st'] not in STYLES:
                continue
            couples = []
            for i, (_, lead, follow, aff, place, *_) in enumerate(ev['c']):
                school = clean_school(aff)
                school = school_map.get(fold(school), school) if school else ''
                members = [person_key(n, school, alias, split) for n in (lead, follow) if fold(n)]
                if members:
                    couples.append((i, place, members))
            if len(couples) >= MIN_COUPLES:
                events.append((comp['date'], comp['key'], ev, couples))
    events.sort(key=lambda e: (e[0], e[1], e[2]['n']))
    return events


def spearman(xs, ys):
    """Spearman correlation with average ranks for ties."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r, i = [0.0] * len(v), 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def beats(a, b):
    """Chance a couple rated `a` finishes ahead of one rated `b`."""
    return 1 / (1 + 10 ** ((b - a) / SCALE))


def performance(team, share):
    """Per couple, the rating whose mean chance of beating each other couple equals its share
    of the field beaten (bisection, all couples at once)."""
    n = len(team)
    lo, hi = np.full(n, team.min() - 2000.0), np.full(n, team.max() + 2000.0)
    others = ~np.eye(n, dtype=bool)
    for _ in range(32):
        mid = (lo + hi) / 2
        p = 1 / (1 + 10 ** ((team[None, :] - mid[:, None]) / SCALE))
        got = (p * others).sum(axis=1) / (n - 1)
        lo, hi = np.where(got < share, mid, lo), np.where(got < share, hi, mid)
    return (lo + hi) / 2


class Ratings:
    def __init__(self):
        self.history = defaultdict(list)   # (style, person) -> [(day, performance)]
        self.start = {}

    def rating(self, key, day):
        weight, total = PRIOR_WEIGHT, PRIOR_WEIGHT * self.start[key]
        for d, perf in self.history[key]:
            w = 0.5 ** ((day - d) / HALF_LIFE)
            weight += w
            total += w * perf
        return total / weight


def run(events, since='2026-01-01'):
    r = Ratings()
    prior = defaultdict(list)          # placement scores so far, for the baseline
    scores = defaultdict(list)
    per_comp = defaultdict(dict)       # comp -> event -> {'f': field, 'c': {index: [exp, perf, ratings after...]}}
    last = {}                          # (style, person) -> (date, level, events)
    swing = {}                         # (style, person, comp) -> [rating before, rating after], 5+ events before
    for date, comp, ev, couples in events:
        day = dt.date.fromisoformat(date).toordinal()
        style, n = ev['st'], len(couples)
        for _, _, ms in couples:
            for m in ms:
                r.start.setdefault((style, m), level_start(ev['lv']))
        team = np.array([sum(r.rating((style, m), day) for m in ms) / len(ms) for _, _, ms in couples])
        places = [p for _, p, _ in couples]
        expected = [1 + sum(beats(team[j], team[i]) for j in range(n) if j != i) for i in range(n)]
        if date >= since and n >= 6:
            base = [-(sum(sum(prior[(style, x)]) / len(prior[(style, x)]) if prior[(style, x)] else 50.0
                          for x in ms) / len(ms)) for _, _, ms in couples]
            scores['rating'].append(spearman(expected, places))
            scores['baseline'].append(spearman(base, places))
            finalists = {i for i, p in enumerate(places) if p <= 6}
            for key, pred in (('finals_rating', expected), ('finals_baseline', base)):
                top = set(sorted(range(n), key=lambda i: pred[i])[:6])
                scores[key].append(len(finalists & top) / max(1, min(6, len(finalists))))
        beaten = np.array([(sum(q > p for q in places) + 0.5 * (sum(q == p for q in places) - 1)) / (n - 1)
                           for p in places])
        perf = performance(team, (beaten * (n - 1) + 0.5) / n)  # +0.5 keeps winners and last places finite
        out = per_comp[comp].setdefault(ev['n'], {'f': round(float(team.mean())), 'c': {}})
        for i, (idx, place, ms) in enumerate(couples):
            after = []
            for m in ms:
                key = (style, m)
                count = last.get(key, (None, None, 0))[2]
                if count >= 5:
                    swing.setdefault((key, comp), [r.rating(key, day), None])
                r.history[key].append((day, float(perf[i])))
                prior[key].append(beaten[i] * 100)
                now = r.rating(key, day)
                if (key, comp) in swing:
                    swing[(key, comp)][1] = now
                after.append(round(now))
                level = ev['lv'] if ev['lv'] in PROGRESSION_LEVELS else last.get(key, (None, None, 0))[1]
                last[key] = (date, level, count + 1)
            out['c'][idx] = [round(expected[i], 1), round(float(perf[i])), *after]
    scores['swing'] = [abs(b - a) for a, b in swing.values()]
    return scores, per_comp, last, r


def boards(last, r, today):
    """Per style, dancers active in the past year: [key, rating, events, last date, level], best first."""
    cutoff = (today - dt.timedelta(days=ACTIVE_DAYS)).isoformat()
    day = today.toordinal()
    out = {s: [] for s in STYLES}
    for (style, person), (date, level, n) in last.items():
        if date >= cutoff:
            out[style].append([person, round(r.rating((style, person), day)), n, date, level])
    for s in out:
        out[s].sort(key=lambda row: -row[1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--backtest', action='store_true')
    args = ap.parse_args()
    events = load_events()
    scores, per_comp, last, r = run(events)
    if args.backtest:
        mean = lambda v: sum(v) / len(v)
        sw = sorted(scores['swing'])
        print(f'{len(events)} events; scored {len(scores["rating"])} from 2026 with 6+ couples')
        print(f'Order (Spearman): rating {mean(scores["rating"]):.3f} vs baseline {mean(scores["baseline"]):.3f}')
        print(f'Finalists picked: rating {mean(scores["finals_rating"]):.1%} vs baseline {mean(scores["finals_baseline"]):.1%}')
        print(f'Rating change per competition, dancers with 5+ events: median {sw[len(sw) // 2]:.0f}, '
              f'90th percentile {sw[int(len(sw) * 0.9)]:.0f}')
        return
    folder = os.path.join(OUT, 'ratings')
    os.makedirs(folder, exist_ok=True)
    for comp, evs in per_comp.items():
        with open(os.path.join(folder, f'{comp}.json'), 'w') as f:
            json.dump(evs, f, separators=(',', ':'))
    latest = max(e[0] for e in events)
    with open(os.path.join(folder, 'boards.json'), 'w') as f:
        json.dump({'asOf': latest, 'provisional': PROVISIONAL, 'activeDays': ACTIVE_DAYS, 'halfLife': HALF_LIFE,
                   'styles': boards(last, r, dt.date.fromisoformat(latest))}, f, separators=(',', ':'))
    print(f'ratings: {len(per_comp)} competitions, {len(last)} dancer ratings, as of {latest}')


if __name__ == '__main__':
    main()
