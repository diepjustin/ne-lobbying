# Nebraska Lobbying

Scraper for the Nebraska Legislature's [lobbyist reporting application](https://nebraskalegislature.gov/lobbyist/),
built to feed [`ne-connect`](../ne-connect/). Own project, own caveats, per that
project's architecture note.

**Status: bill positions working.** 10 tests. A 12-bill sample of the 109th
Legislature yielded 388 positions across 116 principals and 125 lobbyists.
Registration and quarterly expense forms (Form B/C) are not yet scraped.

**Why this source matters more than its size suggests.** It is the only Nebraska
public dataset that ties a private interest to a *specific bill*. Campaign
finance shows money moving near an election; contracts show money moving after
one. Only this shows who asked for what, by name, on the record.

## The data

`data/bill_positions.csv` — one row per lobbyist × principal × bill × filing:

| Column | Notes |
|---|---|
| `legislature`, `bill` | e.g. `109`, `LB8` |
| `lobbyist`, `lobbyist_id` | id is the reliable key |
| `principal`, `principal_id` | **id is the reliable key — see below** |
| `position` | Support / Oppose / Neutral |
| `registration_id` | which filing this row came from |
| `name_truncated` | true when the site cut the name short |

`data/lobbyists.csv`, `data/principals.csv` — the name rosters (206 and 282),
via `--roster`. Use these to resolve a truncated name from its id.

## Caveats worth knowing before you quote it

- **The site truncates names in its own markup.** The anchor text really is
  `Associated Beverage Distributors of...` — the full name appears nowhere on
  the page. **28% of a real 388-row sample came back truncated.** Join on
  `principal_id`, never on the name. A name-only scrape of this source produces
  quietly unusable data, which is why every row carries ids and a
  `name_truncated` flag.
- **Rows that look identical usually aren't.** One lobbyist can file more than
  once for the same principal on the same bill; the rows differ only by
  `registration_id`. Deduplicating on the visible columns destroys real records.
- **Most bills paginate.** Bills with filings typically run 3–6 pages. Reading
  only the first page — as the first version of this scraper did — silently
  drops most of the data. Fixing it roughly tripled the yield per bill.
- **"Neutral" is a real position and it is common** (147 of 388 in the sample,
  more than Oppose). A principal registering neutral on a bill is not the same
  as not registering.
- **Coverage is 2015 onward for electronic filings.** Earlier documents were not
  filed electronically; the Clerk provides them on request at lobby@leg.ne.gov.

## Running it

```
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/                          # no network
./venv/bin/python scripts/lobby.py --roster                 # name rosters
./venv/bin/python scripts/lobby.py --legislatures 109 --max-number 25
```

## Guard rails

**Read before starting a full sweep.**

- **The server rate-limits aggressively.** A 0.8s delay returned HTTP 429 after
  31 bills. The default is now **2.0 seconds**; raise it before you lower it.
  A 429 is handled as an instruction, not an error: the scraper honours
  `Retry-After`, backs off exponentially, and if it still can't proceed it stops
  cleanly with everything collected so far saved.
- **Every response is cached on disk** and never re-fetched without `--refresh`.
- **Progress is checkpointed per bill**, so an interrupted run resumes. This was
  proved the hard way — the run that hit the 429 kept all 31 bills of work.
- **A full sweep is roughly 20,000 requests, near six hours** (~1,300 bills ×
  16 sessions, times pagination). Do not start one casually. Use
  `--max-number` to bound a test first.

## Open work

- Form B / Form C expense totals (`view.php?link=view_search&type=B|C`).
- Registration detail per lobbyist-year, including the principal relationships
  on the detail pages.
- Full historical sweep back to the 105th Legislature.
- Feed `principal_id` into `ne-connect`'s resolution as a hard identifier — it
  is stronger evidence than any name match, and should short-circuit scoring.
