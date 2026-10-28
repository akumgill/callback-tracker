#!/usr/bin/env python3
"""Sanity checks after a crawl. Exits non-zero, failing the update, if the data looks broken.

Compares the freshly built files with the last committed ones, so a DSCO format change
or a failed crawl stops the update (and GitHub emails the repo owner) instead of
publishing an empty or shrunken index.

Usage:
    python check_data.py
"""

import json
import os
import subprocess
import sys

from dsco_crawl import OUT

STYLES = ('Standard', 'Smooth', 'Latin', 'Rhythm')


def committed(path):
    """The version of a data file in the last commit, or None."""
    try:
        out = subprocess.run(['git', 'show', f'HEAD:{path}'], capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    except Exception:
        return None


def main():
    problems = []
    comps = json.load(open(os.path.join(OUT, 'comps.json')))
    before = {c['key']: c for c in committed('site/data/comps.json') or []}

    had = sum(1 for c in before.values() if c.get('events'))
    has = sum(1 for c in comps if c.get('events'))
    if has < had:
        problems.append(f'competitions with results dropped from {had} to {has}')

    for c in comps:
        prev = before.get(c['key'])
        if prev and prev.get('events') and c['events'] < 0.9 * prev['events']:
            problems.append(f'{c["key"]}: events dropped from {prev["events"]} to {c["events"]}')
        if not c['events']:
            continue
        for ev in json.load(open(os.path.join(OUT, f'{c["key"]}.json')))['events']:
            places = [cp[4] for cp in ev['c']]
            # a winner exists (min below 2 allows a shared 1st, e.g. 1.5) and every place is a number from 1 up
            if not places or min(places) >= 2 or any(not isinstance(p, (int, float)) or p < 1 for p in places):
                problems.append(f'{c["key"]} / {ev["n"]}: placements look wrong ({sorted(places)[:5]}...)')
            if len(ev['r']) != len(ev['j']) or any(len(cp[5]) != len(ev['r']) for cp in ev['c']):
                problems.append(f'{c["key"]} / {ev["n"]}: rounds and marks don’t line up')

    people = json.load(open(os.path.join(OUT, 'people.json')))
    people_before = committed('site/data/people.json')
    if people_before and len(people) < 0.95 * len(people_before):
        problems.append(f'dancers dropped from {len(people_before)} to {len(people)}')

    boards = json.load(open(os.path.join(OUT, 'ratings', 'boards.json')))
    for style in STYLES:
        if not boards['styles'].get(style):
            problems.append(f'no ratings for {style}')

    if problems:
        print('Data checks failed; nothing will be committed or deployed:')
        print('\n'.join(f'  - {p}' for p in problems[:30]))
        sys.exit(1)
    print(f'Data checks passed: {has} competitions with results, {len(people)} dancers.')


if __name__ == '__main__':
    main()
