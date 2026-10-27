#!/usr/bin/env python3
"""Dancer ratings from event placements (prototype).

A multi-player Elo: each event is a ranking of couples, and every dancer has one
rating per style. Levels share the scale, but a dancer starts higher the higher
the level they first appear at, since each level up is a stronger field. A
couple's rating is the average of its two dancers'. After an event,
each dancer moves by K x (share of the field actually beaten - share expected
from the ratings). Events are processed in date order, so a rating on any date
only uses earlier results.

K starts high for a dancer's first events and shrinks as they gain experience,
so newcomers find their level quickly and established ratings swing less. The
K settings were chosen on 2025 events and checked on 2026 (--backtest), against
each dancer's average placement score so far as a baseline.

Outputs, read by site/index.html:
    site/data/ratings/<comp>.json   per event: field strength; per couple: expected
                                    place and each dancer's rating after the event
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

from dsco_crawl import OUT, clean_school, fold, name_rules, person_key

STYLES = ('Standard', 'Smooth', 'Latin', 'Rhythm')
PROGRESSION_LEVELS = ('Newcomer', 'Bronze', 'Silver', 'Gold', 'Novice', 'Prechamp', 'Championship')
START, SCALE = 1500.0, 400.0
# K shrinks with experience: new dancers move fast, established ones settle.
# K(n) = K_LATE + (K_EARLY - K_LATE) / (1 + n / K_HALF), n = earlier events in the style
K_EARLY, K_LATE, K_HALF = 768.0, 256.0, 3.0
# Levels are different fields: couples who dance two adjacent levels at one competition place
# about 20-35 placement-score points lower in the higher one. So a dancer's first rating in a
# style starts LEVEL_STEP higher for each level up (Bronze = START). 400 predicted 2025 best.
LEVEL_INDEX = {'Newcomer': 0, 'Bronze': 1, 'Silver': 2, 'Gold': 3, 'Novice': 4, 'Prechamp': 5,
               'Championship': 6, 'Syllabus': 2}
LEVEL_STEP = 400.0
MIN_COUPLES = 4
PROVISIONAL = 3   # events in a style before a rating counts as established
ACTIVE_DAYS = 365


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


def level_start(level):
    return START + LEVEL_STEP * (LEVEL_INDEX.get(level, 1) - 1)


def k_of(n):
    return K_LATE + (K_EARLY - K_LATE) / (1 + n / K_HALF)


def run(events, since='2026-01-01', k=k_of):
    rating = {}
    count = defaultdict(int)
    prior = defaultdict(list)            # placement scores so far, for the baseline
    scores = defaultdict(list)
    per_comp = defaultdict(dict)         # comp -> event name -> {'f': field, 'c': {index: [exp, r_lead, r_follow]}}
    last = {}                            # (style, person) -> (date, level, rating, events)
    for date, comp, ev, couples in events:
        style, n = ev['st'], len(couples)
        for _, _, ms in couples:
            for m in ms:
                rating.setdefault((style, m), level_start(ev['lv']))
        team = [sum(rating[(style, m)] for m in ms) / len(ms) for _, _, ms in couples]
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
        field = sum(team) / n
        out = per_comp[comp].setdefault(ev['n'], {'f': round(field), 'c': {}})
        for i, (idx, place, ms) in enumerate(couples):
            beaten = (sum(q > place for q in places) + 0.5 * (sum(q == place for q in places) - 1)) / (n - 1)
            share_expected = (n - expected[i]) / (n - 1)
            after = []
            for m in ms:
                key = (style, m)
                change = k(count[key]) * (beaten - share_expected)
                if count[key] >= 5:  # swing per competition for established dancers
                    scores['_swing'].append((key, comp, change))
                rating[key] += change
                count[key] += 1
                prior[key].append(beaten * 100)
                after.append(round(rating[key]))
                level = ev['lv'] if ev['lv'] in PROGRESSION_LEVELS else (last.get(key, (None, ''))[1])
                last[key] = (date, level, rating[key], count[key])
            out['c'][idx] = [round(expected[i], 1), *after]
    return scores, per_comp, last


def boards(last, today):
    """Per style, dancers active in the past year: [key, rating, events, last date, level], best first."""
    cutoff = (today - dt.timedelta(days=ACTIVE_DAYS)).isoformat()
    out = {s: [] for s in STYLES}
    for (style, person), (date, level, r, n) in last.items():
        if date >= cutoff:
            out[style].append([person, round(r), n, date, level])
    for s in out:
        out[s].sort(key=lambda row: -row[1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--backtest', action='store_true')
    args = ap.parse_args()
    events = load_events()
    scores, per_comp, last = run(events)
    if args.backtest:
        mean = lambda v: sum(v) / len(v)
        print(f'{len(events)} events; scored {len(scores["rating"])} from 2026 with 6+ couples')
        print(f'Order (Spearman): rating {mean(scores["rating"]):.3f} vs baseline {mean(scores["baseline"]):.3f}')
        print(f'Finalists picked: rating {mean(scores["finals_rating"]):.1%} vs baseline {mean(scores["finals_baseline"]):.1%}')
        per_comp_swing = defaultdict(float)
        for key, comp, change in scores['_swing']:
            per_comp_swing[(key, comp)] += change
        sw = sorted(abs(v) for v in per_comp_swing.values())
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
        json.dump({'asOf': latest, 'provisional': PROVISIONAL, 'activeDays': ACTIVE_DAYS,
                   'styles': boards(last, dt.date.fromisoformat(latest))}, f, separators=(',', ':'))
    print(f'ratings: {len(per_comp)} competitions, {len(last)} dancer-style ratings, as of {latest}')


if __name__ == '__main__':
    main()
