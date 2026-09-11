# Nebraska Lobbying

Scraper for the Nebraska Legislature's [lobbyist reporting application](https://nebraskalegislature.gov/lobbyist/),
built to feed [`ne-connect`](https://github.com/diepjustin/ne-connect). Own project, own caveats, per that
project's architecture note.

**Status: one legislature swept, expense sweep part way.** 33 tests. The 109th
Legislature (LB and LR) yielded 39,909 position rows; the six earlier sessions
in `LEGISLATURES` have not been started. Statewide Form B and Form C totals
cover 2015–2026 (408 rows). The per-entity Form B sweep reached 2,175 of 5,412
entity-years before the network dropped; Form C has not started. Both sweeps
died on network errors the scraper did not catch — fixed on 10 Sep 2026, see
guard rails — and resume from their checkpoints when `scripts/sweep_all.sh`
is next run.

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
- **Rows that look identical usually aren't — and some are.** One lobbyist can
  file more than once for the same principal on the same bill; the rows differ
  only by `registration_id`. A lobbyist can also change position on a bill, so
  one `registration_id` can carry a Neutral row and a Support row. Deduplicating
  on the visible columns destroys real records. But the Legislature's own bill
  pages also list some registrations twice, byte for byte (registration 20659
  appears as two identical rows on one page), and the scraper copies the page
  rather than guessing which the state meant. Of the 39,909 rows for the 109th,
  36,258 are distinct (legislature, bill, registration, position) and 3,651 are
  exact repeats from the source. `scripts/check_data.py` reports that count;
  subtract it before quoting a total number of positions.
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
./venv/bin/python scripts/expenses.py --aggregate           # statewide totals, ~24 requests
./venv/bin/python scripts/check_data.py                     # dedup contract on data/
scripts/sweep_all.sh                                        # everything, in order; hours
```

`sweep_all.sh` runs the position sweep, the statewide totals and the
per-entity expense sweep in that order, rerunning each until it reports
itself complete. Every scraper says how it ended through its exit status:

| exit | meaning | chain does |
|---|---|---|
| 0 | the requested set finished; progress file has `complete: true` | next stage |
| 2 | stopped cleanly on a 429 or a dropped connection, checkpoint saved | waits 5 min, reruns, up to 12 times |
| 130 | interrupted by hand | stops the chain |

To stop the chain, kill the running scraper, not the script: the scraper saves
its checkpoint and exits 130, and the chain ends with it.

`check_data.py` asserts that no expenses file carries a duplicate key
(statewide: form, year, category; per-entity: form, entity, year, category)
and exits 1 if one does. It runs before `build_site.py` in the chain. The
statewide file is rewritten on every run for exactly this reason: it used to
be appended, and each rerun of the chain doubled it.

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
- **A dropped connection gets the same treatment as a 429.** Both real sweeps
  died on network errors: the position sweep on a read timeout after the 109th
  Legislature, the Form B sweep on a connection error 2,175 entity-years in.
  Each left a traceback where a checkpoint should have been, and the old chain
  script read a vanished process as "finished", so the remaining six
  legislatures were never started. Now a `ConnectionError` or `Timeout` backs
  off and retries like a 429, then raises `Unreachable` (a `RateLimited`), and
  the progress file records whether the requested set was actually finished.
- **A full sweep is roughly 20,000 requests, near six hours** (~1,300 bills ×
  16 sessions, times pagination). Do not start one casually. Use
  `--max-number` to bound a test first.

## Open work

- Finish the sweeps: the six legislatures before the 109th, the rest of Form B,
  all of Form C. Roughly 18 hours of unattended requests; `sweep_all.sh`
  resumes each from its checkpoint.
- Back the CSVs up as a GitHub Release rather than commits (they are gitignored
  and exist only on one machine).
- Registration detail per lobbyist-year, including the principal relationships
  on the detail pages.
- Feed `principal_id` into `ne-connect`'s resolution as a hard identifier — it
  is stronger evidence than any name match, and should short-circuit scoring.
