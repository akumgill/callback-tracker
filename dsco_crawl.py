#!/usr/bin/env python3
"""Build a compact, public-results-only index of every DSCO competition.

DSCO (https://dsco-<comp>.web.app) keeps each competition in its own public
Firebase Realtime Database. This reads only what DSCO's own results pages show
(events, marks, saved results, competition name and date); it never reads
competitor registration records.

Names listed in opt_out.txt (one per line) are left out of the index.

Output, read by site/index.html:
    site/data/comps.json        list of competitions
    site/data/<comp>.json       per-competition results

Usage:
    python dsco_crawl.py             # new comps, plus re-checks of the last 30 days (daily job)
    python dsco_crawl.py --all       # re-fetch everything
    python dsco_crawl.py harvard26   # just these comps
    python dsco_crawl.py --all --cached   # rebuild from saved raw responses, no downloads
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'site', 'data')
# any DSCO site's app bundle lists every comp; read a few in case one goes away
APPS = ['https://dsco-harvard26.web.app', 'https://dsco-northeastern26.web.app', 'https://dsco-badc26.web.app']
SKIP = {'dev', 'demo', 'colombiamock24'}
FRESH_DAYS = 30
USER_AGENT = 'callback-tracker/1.0 (+https://github.com/akumgill/callback-tracker)'


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


CACHE = os.path.join(HERE, '.dsco_cache')
use_cache = False


def fb(db, path):
    """Read one node. With --cached, reuse raw responses saved by an earlier crawl."""
    url = f'{db}/{urllib.parse.quote(path)}.json'
    cached = os.path.join(CACHE, re.sub(r'\W+', '_', url.split('//', 1)[1]))
    if use_cache and os.path.exists(cached):
        return json.load(open(cached))
    text = get(url)
    os.makedirs(CACHE, exist_ok=True)
    with open(cached, 'w') as f:
        f.write(text)
    return json.loads(text)


def list_comps():
    """Comp key -> (database URL, project), parsed from the Firebase configs in DSCO's app bundle."""
    comps = {}
    for app in APPS:
        try:
            html = get(app)
            js = re.search(r'/assets/(index-[\w-]+\.js)', html).group(1)
            bundle = get(f'{app}/assets/{js}')
        except Exception as exc:
            print(f'{app}: unreadable ({exc})', file=sys.stderr)
            continue
        for m in re.finditer(r'(\w+):\{apiKey:"[^"]+",authDomain:"[^"]+"(?:,databaseURL:"([^"]+)")?,projectId:"([^"]+)"', bundle):
            key, db, project = m.groups()
            if key not in SKIP:
                comps.setdefault(key, (db or f'https://{project}-default-rtdb.firebaseio.com', project))
    if not comps:
        sys.exit('Could not read the competition list from any DSCO site')
    return comps


def opt_outs():
    path = os.path.join(HERE, 'opt_out.txt')
    if not os.path.exists(path):
        return set()
    return {' '.join(l.split()).lower() for l in open(path) if l.strip() and not l.startswith('#')}


def site_url(key, project):
    """The public site is usually dsco-<key>.web.app, sometimes <project>.web.app."""
    for url in (f'https://dsco-{key}.web.app', f'https://{project}.web.app'):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=20)
            return url
        except Exception:
            pass
    return f'https://dsco-{key}.web.app'


# ---------------------------------------------------------------- skating system

def skate_dance(rankings, couples):
    """Rules 5-8: judges' ordinal rankings for one dance -> {couple: place}."""
    n = len(rankings)
    maj = n // 2 + 1
    marks = {c: sorted(r.index(c) + 1 for r in rankings if c in r) for c in couples}
    places, left, p = {}, list(couples), 1
    while left:
        group, col, have = list(left), p, []
        while group and col <= len(couples):
            cnt = {c: sum(m <= col for m in marks[c]) for c in group}
            have = [c for c in group if cnt[c] >= maj]
            if not have:
                col += 1
                continue
            best = max(cnt[c] for c in have)
            have = [c for c in have if cnt[c] == best]
            if len(have) > 1:
                sums = {c: sum(m for m in marks[c] if m <= col) for c in have}
                low = min(sums.values())
                have = [c for c in have if sums[c] == low]
            if len(have) == 1 or col == len(couples):
                break
            group, col = have, col + 1
        winners = have or left  # incomplete rankings: whoever is left ties
        for c in winners:
            places[c] = p + (len(winners) - 1) / 2 if len(winners) > 1 else p
        left = [c for c in left if c not in winners]
        p += len(winners)
    return places


def skate_overall(dance_places, all_rankings):
    """Rules 9-11: per-dance places -> overall place for a multi-dance final."""
    couples = list(next(iter(dance_places.values())))
    total = {c: sum(dp[c] for dp in dance_places.values()) for c in couples}
    order = sorted(couples, key=lambda c: total[c])
    places, i = {}, 0
    while i < len(order):
        tied = [c for c in order if total[c] == total[order[i]] and c not in places]
        if not tied:
            i += 1
            continue
        p = i + 1
        while tied:
            win = rule10(tied, p, dance_places) or rule11(tied, p, all_rankings) or tied
            for c in win:
                places[c] = p
            tied = [c for c in tied if c not in win]
            p += len(win)
        i = p - 1
    return places


def rule10(tied, p, dance_places):
    cnt = {c: sum(dp[c] <= p for dp in dance_places.values()) for c in tied}
    best = [c for c in tied if cnt[c] == max(cnt.values())]
    if len(best) > 1:
        sums = {c: sum(dp[c] for dp in dance_places.values() if dp[c] <= p) for c in best}
        best = [c for c in best if sums[c] == min(sums.values())]
    return best if len(best) == 1 else None


def rule11(tied, p, all_rankings):
    """All judges' marks across all dances treated as one dance."""
    maj = len(all_rankings) // 2 + 1
    marks = {c: [r.index(c) + 1 for r in all_rankings if c in r] for c in tied}
    for col in range(p, p + 10):
        cnt = {c: sum(m <= col for m in marks[c]) for c in tied}
        have = [c for c in tied if cnt[c] >= maj]
        if not have:
            continue
        have = [c for c in have if cnt[c] == max(cnt[c] for c in have)]
        if len(have) > 1:
            sums = {c: sum(m for m in marks[c] if m <= col) for c in have}
            have = [c for c in have if sums[c] == min(sums.values())]
        if len(have) == 1:
            return have
    return None


# ---------------------------------------------------------------- one event

def as_list(x):
    """Firebase returns arrays as lists or as dicts with numeric keys."""
    if isinstance(x, dict):
        return [x[k] for k in sorted((k for k in x if k.isdigit()), key=int)]
    return x or []


def build_event(name, ev, marks, saved):
    # rounds/marks/saved are parallel lists, final first; drop rounds that were never run
    rounds, marks, saved_rounds = as_list(ev.get('rounds')), as_list(marks), as_list(saved)
    keep = [i for i, r in enumerate(rounds) if r.get('entries')]
    if not keep or keep[0] != 0:
        return None
    rounds = [rounds[i] for i in keep]
    marks = [marks[i] if i < len(marks) else None for i in keep]
    saved_rounds = [saved_rounds[i] if i < len(saved_rounds) else None for i in keep]
    dances = ev.get('eventDances') or []
    entries = [{str(e['leadNumber']): e for e in (r.get('entries') or {}).values() if 'leadNumber' in e} for r in rounds]
    everyone = {}
    for ents in reversed(entries):
        everyone.update(ents)

    # rounds are stored final-first; flip to chronological for output
    chrono = list(range(len(rounds)))[::-1]
    judges = [len(next(iter((marks[i] or {}).values()), {})) if i < len(marks) else 0 for i in chrono]
    recalls = {c: [] for c in everyone}
    for i in chrono:
        rm = marks[i] if i < len(marks) else None
        for c in everyone:
            if i == 0 or c not in entries[i] or not rm:
                recalls[c].append(None)
            else:
                recalls[c].append([sum(c in map(str, lst) for lst in (rm.get(d) or {}).values()) for d in dances])

    # final
    place, final = {}, {}
    finalists = list(entries[0])
    fm = marks[0] or {}
    official = (saved or {}).get('finalResults') if isinstance(saved, dict) else None
    if finalists and official:
        for r in as_list(official):
            c = str(r['coupleNumber'])
            place[c] = r['overallPlacement']
            final[c] = r.get('danceResults')
    elif finalists and fm:
        saved0 = saved_rounds[0] or {}
        dance_places = {}
        for d in dances:
            stored = ((saved0 or {}).get('placements') or {}).get(d) if isinstance(saved0, dict) else None
            ranks = [[str(x) for x in r] for r in (fm.get(d) or {}).values()]
            if stored:
                dance_places[d] = {str(k): v for k, v in stored.items()}
            elif ranks:
                dance_places[d] = skate_dance(ranks, finalists)
        dance_places = {d: dp for d, dp in dance_places.items() if set(finalists) <= set(dp)}
        if dance_places:
            if len(dance_places) == 1:
                place.update(next(iter(dance_places.values())))
            else:
                all_ranks = [[str(x) for x in r] for d in dances for r in (fm.get(d) or {}).values()]
                place.update(skate_overall(dance_places, all_ranks))
            for c in finalists:
                final[c] = [dance_places[d].get(c) if d in dance_places else None for d in dances]

    # eliminated couples, ranked the way DSCO's results page does it: every couple in the
    # round by its total recalls there (ties share a place), minus the top N, where N is
    # the length of the recall list DSCO saved for that round
    for i in range(1, len(rounds)):
        sv = saved_rounds[i] if isinstance(saved_rounds[i], dict) else {}
        if sv.get('sumRecalls') and sv.get('placements') is not None:
            sums = list(sv['sumRecalls'].values())
            f = max(sums, key=lambda d: sum(d.values()))  # cumulative over the round's dances
            ranked = [(str(e['leadNumber']), f.get(str(e['leadNumber']), 0)) if e.get('leadNumber') else (None, None)
                      for e in rounds[i]['entries'].values()]
            skip = len(sv['placements'])
        else:
            k = chrono.index(i)
            ranked = [(c, sum(recalls[c][k] or [])) for c in entries[i]]
            skip = len(entries[i - 1])
        ranked.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
        prev = None
        for j, (c, score) in enumerate(ranked):
            p = prev[1] if prev and score == prev[0] else j + 1
            prev = (score, p)
            # couples who advanced already have a later-round place; DSCO's page hides anyone
            # else in the top N (a recall-list glitch), so give them their rank instead
            if c and (j >= skip or c not in entries[i - 1]):
                place.setdefault(c, p)

    couples = []
    for c, e in everyone.items():
        if c not in place:
            continue
        couples.append([int(c) if c.isdigit() else c, e.get('leadName', ''), e.get('followName', ''),
                        e.get('affiliation', ''), place[c], recalls[c], final.get(c)])
    couples.sort(key=lambda r: r[4])
    return {'n': name, 'lv': ev.get('eventLevel', ''), 'st': ev.get('style', ''), 'd': dances,
            'r': [rounds[i].get('roundName', '').replace(name, '').strip() for i in chrono], 'j': judges,
            # field size as DSCO's results page lists it: everyone in the first round
            'N': len(rounds[-1]['entries']), 'c': couples}


def crawl(key, db, project, hidden=frozenset()):
    meta = {k: fb(db, f'competitionMetadata/{k}') for k in ('fullName', 'startDate')}
    start = dt.datetime.fromtimestamp(meta['startDate'] / 1000, dt.timezone.utc).date() if meta.get('startDate') else None
    base = {'key': key, 'name': meta.get('fullName') or key, 'date': start and start.isoformat(),
            'site': site_url(key, project)}
    if start and start > dt.date.today():  # no results before the comp; don't download its entries
        return {**base, 'events': []}
    events = fb(db, 'events') or {}
    marks = fb(db, 'marks') or {}
    saved = fb(db, 'resultsSaved') or {}
    out = []
    for name, ev in events.items():
        if not isinstance(ev, dict) or name not in marks:
            continue
        try:
            e = build_event(name, ev, marks[name], saved.get(name))
        except Exception as exc:  # one malformed event shouldn't sink the comp
            print(f'  ! {name}: {exc}', file=sys.stderr)
            continue
        if e and e['c']:
            for c in e['c']:
                for i in (1, 2):
                    if ' '.join(c[i].split()).lower() in hidden:
                        c[i] = ''
            out.append(e)
    return {**base, 'events': sorted(out, key=lambda e: e['n'])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('comps', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--cached', action='store_true', help='rebuild from raw responses saved by the last crawl')
    args = ap.parse_args()
    global use_cache
    use_cache = args.cached
    os.makedirs(OUT, exist_ok=True)

    index_path = os.path.join(OUT, 'comps.json')
    index = {c['key']: c for c in json.load(open(index_path))} if os.path.exists(index_path) else {}
    comps = list_comps()
    hidden = opt_outs()
    today = dt.date.today()
    for key, (db, project) in comps.items():
        if args.comps and key not in args.comps:
            continue
        old = index.get(key)
        if old and not args.all and not args.comps and old.get('date') and \
                (today - dt.date.fromisoformat(old['date'])).days > FRESH_DAYS:
            continue
        try:
            data = crawl(key, db, project, hidden)
        except Exception as exc:
            print(f'{key}: skipped ({exc})', file=sys.stderr)
            continue
        with open(os.path.join(OUT, f'{key}.json'), 'w') as f:
            json.dump(data, f, separators=(',', ':'))
        index[key] = {'key': key, 'name': data['name'], 'date': data['date'], 'site': data['site'],
                      'events': len(data['events']), 'fetched': today.isoformat()}
        print(f'{key}: {data["name"]} ({data["date"]}), {len(data["events"])} events', flush=True)
        time.sleep(0.5)

    comps_list = sorted(index.values(), key=lambda c: c['date'] or '')
    with open(index_path, 'w') as f:
        json.dump(comps_list, f, indent=1)
    build_people(comps_list)


def build_people(comps_list):
    """people.json: [name, [affiliations], [comp indexes in comps.json]] for the search box."""
    people = {}
    for i, comp in enumerate(comps_list):
        path = os.path.join(OUT, f'{comp["key"]}.json')
        if not os.path.exists(path):
            continue
        for ev in json.load(open(path))['events']:
            for _, lead, follow, aff, *_ in ev['c']:
                for name in (lead, follow):
                    name = ' '.join(name.split())
                    if not name:
                        continue
                    p = people.setdefault(name, [set(), set()])
                    if aff:
                        p[0].add(aff)
                    p[1].add(i)
    rows = [[n, sorted(a), sorted(c)] for n, (a, c) in sorted(people.items())]
    with open(os.path.join(OUT, 'people.json'), 'w') as f:
        json.dump(rows, f, separators=(',', ':'))
    print(f'people.json: {len(rows)} names')


if __name__ == '__main__':
    main()
