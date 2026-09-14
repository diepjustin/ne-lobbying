# Nebraska Lobbying

Scraper for the Nebraska Legislature's [lobbyist reporting application](https://nebraskalegislature.gov/lobbyist/),
built to feed [`ne-connect`](https://github.com/diepjustin/ne-connect). Own project, own caveats, per that
project's architecture note.

**Status: fully swept.** 33 tests. All 7 legislatures in `LEGISLATURES` (109,
108-3, 108, 107-3, 107, 106, 105) are complete: 291,078 position rows
(208,225 distinct by `legislature, bill, registration_id, position`; 82,853
are exact repeats the source itself lists twice — see caveats). Statewide
Form B/C totals cover 2015–2026 (408 rows). The per-entity sweep is done too:
12,636 Form B rows, 22,289 Form C rows, zero duplicate keys in any of the
three (`check_data.py`).

Getting here took two real bugs, both now fixed. First (10 Sep 2026): a
dropped connection or read timeout crashed the scraper outright instead of
retrying, so `sweep_all.sh` silently continued past a partial sweep — see
guard rails. Second (13 Sep 2026): `LEGISLATURES` itself listed `"107-1"` for
the 107th's special session; the site's own dropdown has no such session —
the real code is `"107-3"`, matching the `108-3`/`102-3`/`101-3`/`100-3`
pattern. Every bill lookup under the wrong code 500'd, which combined with
the first bug to silently cap a "complete" sweep at 3 of 7 legislatures. Both
are covered by the guard rails below and by `sweep_all.sh`'s exit-code
handling, which now flags (rather than silently absorbs) any stage that
doesn't exit 0/2/130.

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
  rather than guessing which the state meant. Of 291,078 rows sitewide, 208,225
  are distinct (legislature, bill, registration, position) and 82,853 are exact
  repeats from the source. `scripts/check_data.py` reports that count; subtract
  it before quoting a total number of positions.
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
- **A full sweep is tens of thousands of requests, roughly a day unattended**
  once pagination and rate-limit backoff are counted (the completed run took
  the position stage plus both expense stages, spread over ~27 hours partly
  because of heavy 429 throttling). Do not start one casually. Use
  `--max-number` to bound a test first.
- **A wrong session code fails loud, not quiet, but only per-bill.** Every
  legislature entry in `LEGISLATURES` should match a `value=` in the site's
  own Legislature dropdown (`view.php?link=view_bill_search`) exactly — a
  code the site doesn't recognize 500s on every bill number under it, which
  looks identical in the log to a broken server until you check whether other
  legislatures 500 too.

## Open work

- Back the CSVs up as a GitHub Release rather than commits (they are gitignored
  and exist only on one machine) — `PLAN.md` 0.5 in `ne-connect`.
- Compute `lobbying_coverage()`'s reported figures from `bill_positions.csv`'s
  actual distinct `(legislature, bill)` pairs rather than the resume
  checkpoint's `done` count, which is authoritative for resuming but not
  intended as a public coverage statistic.
- Registration detail per lobbyist-year, including the principal relationships
  on the detail pages.
- Feed `principal_id` into `ne-connect`'s resolution as a hard identifier — it
  is stronger evidence than any name match, and should short-circuit scoring.
