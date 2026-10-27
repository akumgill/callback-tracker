# Callback Tracker

Search any collegiate ballroom dancer by name or school and see how they've placed and how many callbacks they've earned across competitions run on [DSCO](https://dsco-harvard26.web.app).

**Live site:** https://akumgill.github.io/callback-tracker/

For each dancer it shows:

- placement over time for each style, with one line per level (Bronze, Silver, Gold, ...)
- a grid of every dance at every competition: placement, and the share of judges' marks received in each round
- a table per competition with partner, place, marks per round and per dance, and a link to the DSCO results page
- YCN proficiency points by style and level, and the lowest level the dancer can still enter in each dance

- a rating per style (prototype), with rank among active dancers at the same level and the expected place in each event

Each school also has a team page: results by competition, the team's placement trend by level, and its roster.

## Rating (prototype)

`ratings.py` runs after each crawl. It is a multi-player Elo: every event is a ranking of couples, each dancer has one rating per style, and a couple's rating is the average of its two dancers'. After an event each dancer moves by K × (share of the field actually beaten − share expected from the ratings).

- **Levels share one scale but start apart.** Couples who dance two adjacent levels at one competition place about 20–35 placement-score points lower in the higher one, so a dancer's first rating depends on their level: Newcomer 1,100, Bronze 1,500, Silver 1,900, Gold 2,300 (400 per level, chosen by backtest).
- **New dancers move faster.** K starts at 768 and settles toward 256 as a dancer gains events in the style.
- **Ranks** count dancers active in the past year with at least 3 events in the style.

Settings were chosen on 2025 events and tested on 2026 (`python ratings.py --backtest`): the rating predicts finishing order with a rank correlation of 0.615, against 0.513 for each dancer's average placement score so far, and picks 74.6% of finalists (67.5% for the baseline). A typical established dancer's rating moves about 90 points per competition. It only sees DSCO competitions, and it measures how judges ranked couples against each other, not absolute skill.

## How it works

`dsco_crawl.py` reads each DSCO competition's public results (the same data DSCO's results pages show: events, judges' marks and saved results) and writes a compact index to `site/data/`. It uses only the Python standard library.

A GitHub Action runs it every morning. A competition is re-checked for 30 days after it starts and then left alone. Upcoming competitions only have their dates checked. New results are committed, and the site is redeployed to GitHub Pages.

Placements match DSCO's results pages: finals use DSCO's stored skating-system results, and couples eliminated earlier are ranked by recalls in the round where they went out. The field size is the number of couples in the first round.

**Placement score** is the share of the other couples a dancer finished ahead of: (couples − place) ÷ (couples − 1). 1st place is 100% and last is 0%.

## Run it locally

```bash
python dsco_crawl.py               # new competitions, plus re-checks of the last 30 days
python dsco_crawl.py --all         # re-fetch everything
python -m http.server -d site 8000 # then open http://localhost:8000
```

## Notes

- This project isn't affiliated with DSCO. Competitions scored elsewhere (O2CM, for example) aren't included.
- Spellings that differ only by accents, capitalization or apostrophes count as one dancer (Faria and Faría, for example). A dancer stays one person across schools, since transfers are common and DSCO records one school per couple. For anything else, `names.txt` merges other spellings or splits two dancers who share a name.
- To fix a result, merge or split a dancer, or be left out, use **Request a correction or removal** on any dancer or team page (it opens a GitHub issue form). `opt_out.txt` and `names.txt` hold the fixes.

## For results publishers

Callback Tracker reads only DSCO's public results data (the same data its results pages show), once a day, and re-checks a competition for 30 days after it starts. Every request identifies itself as `callback-tracker/1.0 (+https://github.com/akumgill/callback-tracker)`. It follows robots.txt rules for `User-agent: callback-tracker` on every host it reads, never signs in, and never reads registration records. Each competition links back to its original results.

To stop it, change how your results are used, or have something removed, [open a request](https://github.com/akumgill/callback-tracker/issues/new?template=correction.yml).
