# Callback Tracker

Search any collegiate ballroom dancer by name or school and see how they've placed and how many callbacks they've earned across competitions run on [DSCO](https://dsco-harvard26.web.app).

**Live site:** https://akumgill.github.io/callback-tracker/

For each dancer it shows:

- placement over time for each style, with one line per level (Bronze, Silver, Gold, ...)
- a grid of every dance at every competition: placement, and the share of judges' marks received in each round
- a table per competition with partner, place, marks per round and per dance, and a link to the DSCO results page
- approximate YCN proficiency points

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
- Names are matched exactly, so someone whose name is spelled differently at two competitions shows up twice.
- To be left out of the index, open an issue or add your name to `opt_out.txt`.
