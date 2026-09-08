"""Scraper for the Nebraska Legislature's lobbyist reporting application.

Source: https://nebraskalegislature.gov/lobbyist/

The site looks like a JavaScript app but is not: search pages are plain GET
forms, detail pages are server-rendered, and the name roster comes from a small
JSON endpoint. No viewstate, no POST, no session. Recon notes:

    roster     ajax.php?functionName=allArchiveSearch&LORP={L|P}&term=
               -> [{"value": "<id>", "label": "<name>"}]  (206 lobbyists,
                  283 principals as of Sep 2026)

    detail     view.php?link=view_lobbyist&id={id}&Year={yyyy}
               -> name, address, phone, contact-change history, forms by year

    positions  view.php?link=view_bill_search&Legislature={n}&Prefix=LB
                        &Number={n}&Suffix=&submit=Search
               -> table of Lobbyist | Principal | Position | View

`positions` is the reason this project exists. It is the only place in any
Nebraska public dataset that ties a private interest to a specific bill, which
makes it the one source that can answer "who lobbied on the bill that became
the contract" rather than merely "who spent money near an election."

Guard rails, because this is a small government server:
  - one request per second by default, and every response cached on disk
  - a cached response is never re-fetched without --refresh
  - progress is checkpointed, so an interrupted run resumes instead of restarting
  - a full sweep is ~1,300 bills x 16 sessions ~= 20,000 requests, near six
    hours. Do not start one casually; use --limit to test first.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

BASE = "https://nebraskalegislature.gov/lobbyist"
USER_AGENT = (
    "ne-lobbying-scraper/0.1 "
    "(https://github.com/diepjustin/diepjustin.github.io; contact: sdiepxj367@gmail.com)"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
PROGRESS_PATH = DATA_DIR / "scrape_progress.json"

# The server returns 429 well before you would expect: a 0.8s delay tripped it
# after 31 bills. Two seconds has held. Raise it before you lower it.
DEFAULT_DELAY = 2.0
MAX_RETRIES = 4

# Bills between checkpoints. Small enough that `kill` costs at most a minute of
# work, large enough not to rewrite the progress file constantly.
FLUSH_EVERY = 25

# Safety stop for the windowed pager below. The busiest real bill sits far under
# this; the cap only exists so a pager that never terminates cannot loop forever.
MAX_PAGES_PER_BILL = 60

# Session codes from the Legislature select, newest first. 110 (2027-2028) has
# not convened, so it is listed but not swept by default.
LEGISLATURES = ["109", "108-3", "108", "107-1", "107", "106", "105"]
FUTURE_LEGISLATURES = ["110"]

# How far to count per session. There is no way to detect the real ceiling: a
# bill number that never existed returns a page byte-identical to a real bill
# with no lobbying filings (23,299 bytes, no table, in both cases). So the cap
# is a judgement, not a discovery.
#
# Nebraska numbers bills continuously across a two-year Legislature, reaching
# roughly LB1300. Resolutions (LR) run lower -- a few hundred -- but they are
# NOT negligible: LR20 carries 16 lobbying positions and LR300 carries 10,
# including the state and Lincoln chambers. Skipping them would lose real
# lobbying activity on constitutional amendments and interim studies.
#
# Special sessions -- the codes carrying a dash -- run to a few dozen at most,
# so sweeping them to 1300 would waste ~2,500 requests apiece.
SESSION_MAX = {"LB": 1300, "LR": 500}
SPECIAL_SESSION_MAX = 50


def session_max_number(legislature: str, prefix: str = "LB") -> int:
    if "-" in legislature:
        return SPECIAL_SESSION_MAX
    return SESSION_MAX.get(prefix, 1300)

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_SCRIPT = re.compile(r"<script.*?</script>", re.S)
_ENTITY_LINK = re.compile(r'view\.php\?link=view_(lobbyist|principal)&(?:amp;)?id=(\d+)')
_REGISTRATION = re.compile(r'RegistrationID=(\d+)')
_PAGE = re.compile(r'[?&]page=(\d+)')
_PHONE = re.compile(r'^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$')
_ZIP = re.compile(r'^\d{5}(-\d{4})?$')
_FORMS_HEADING = re.compile(r'^20\d\d Forms$')


class RateLimited(Exception):
    """The server asked us to stop. It is not an error; it is an instruction."""


@dataclass
class Fetcher:
    """Polite, cached HTTP. Every network access in this project goes through it."""

    delay: float = DEFAULT_DELAY
    refresh: bool = False
    cache_dir: Path = None
    session: requests.Session = field(default_factory=requests.Session)
    requests_made: int = 0
    cache_hits: int = 0
    rate_limit_waits: int = 0

    def __post_init__(self):
        self.cache_dir = Path(self.cache_dir or CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session.headers["User-Agent"] = USER_AGENT

    def get(self, path: str, params: dict = None) -> str:
        url = f"{BASE}/{path}"
        key = hashlib.sha256(
            f"{url}?{sorted((params or {}).items())}".encode("utf-8")
        ).hexdigest()[:20]
        cached = self.cache_dir / f"{key}.html"

        if cached.exists() and not self.refresh:
            self.cache_hits += 1
            return cached.read_text(encoding="utf-8")

        # Sleep BEFORE the request, not after, so a cache hit costs nothing and
        # a resumed run does not idle through everything it already has.
        if self.requests_made:
            time.sleep(self.delay)

        for attempt in range(MAX_RETRIES):
            response = self.session.get(url, params=params, timeout=45)
            if response.status_code != 429:
                response.raise_for_status()
                self.requests_made += 1
                cached.write_text(response.text, encoding="utf-8")
                return response.text

            # Honour Retry-After when offered, else back off exponentially.
            wait = float(response.headers.get("Retry-After") or self.delay * (2 ** (attempt + 1)))
            self.rate_limit_waits += 1
            print(f"    429 -- waiting {wait:.0f}s (attempt {attempt + 1})", file=sys.stderr)
            time.sleep(wait)

        raise RateLimited(f"still rate limited after {MAX_RETRIES} attempts: {url}")


def _clean(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def parse_roster(payload: str):
    """Roster JSON -> [{"id", "name"}], dropping blank labels.

    The principal roster contains an entry with an empty label (id 2969) --
    a real record in their system with no name, not a parse failure.
    """
    entries = json.loads(payload)
    return [
        {"id": e["value"], "name": e["label"].strip()}
        for e in entries
        if e.get("label", "").strip()
    ]


def parse_bill_positions(page: str):
    """Bill-search page -> (rows, last_page_number).

    IDs are extracted, not just names, for two reasons that both bite hard:

    1. **The site truncates names in the markup.** The anchor text really is
       "Associated Beverage Distributors of..." -- the full name is nowhere on
       the page. Only principal id 2285 identifies it, so a name-only scrape
       silently produces unjoinable rows.

    2. **Rows that look identical are not.** One lobbyist can file twice for the
       same principal on the same bill; the rows differ only by RegistrationID.
       Dropping it would look like deduplication and would actually be data loss.

    Returns [] when a bill has no filings, which is the common case.
    """
    start = page.find('class="main-content')
    body = _SCRIPT.sub(" ", page[start:] if start > 0 else page)

    positions = []
    for row in _ROW.findall(body):
        cells = _CELL.findall(row)
        if len(cells) < 3:
            continue
        text = [_clean(c) for c in cells]
        if text[0] in ("Lobbyist", ""):
            continue  # header row

        ids = dict.fromkeys(("lobbyist", "principal"), "")
        for kind, value in _ENTITY_LINK.findall(row):
            if not ids[kind]:
                ids[kind] = value
        registration = _REGISTRATION.search(row)

        positions.append(
            {
                "lobbyist": text[0],
                "lobbyist_id": ids["lobbyist"],
                "principal": text[1],
                "principal_id": ids["principal"],
                "position": text[2],
                "registration_id": registration.group(1) if registration else "",
                "name_truncated": text[1].endswith("..."),
            }
        )

    pages = [int(n) for n in _PAGE.findall(body)]
    return positions, max(pages) if pages else 1


def parse_entity_detail(page: str):
    """Lobbyist or principal detail page -> full name and contact block.

    This page is the ONLY place the untruncated name exists. The bill-position
    table cuts names off ("Associated Beverage Distributors of..."), and the
    roster endpoint is both incomplete (25 of 116 ids seen in one sample) and
    sometimes stale -- id 2590 is "Time Warner Cable" there and "Charter
    Communications Operating, L..." in the positions table, because the entity
    was renamed and the id outlived the name.

    Anchored on whichever terminator comes first -- "View Contact Changes:" or
    the "{year} Forms" heading -- then walked backwards to the section label,
    rather than counting lines from the top: the page shares a long nav block
    with every other page on the site, so any fixed offset breaks the first time
    the nav changes.

    Both terminators are needed. A principal with no contact-change history has
    no "View Contact Changes:" line at all, and anchoring on it alone silently
    failed for 29 of 116 real principals -- Chief Industries among them.
    """
    start = page.find('class="main-content')
    body = _SCRIPT.sub(" ", page[start:] if start > 0 else page)
    lines = [line for line in (_clean(x) for x in re.split(r"<[^>]+>", body)) if line]

    detail = {"name": "", "kind": "", "address": "", "city": "", "state": "", "zip": "",
              "phone": "", "years_with_forms": []}
    detail["years_with_forms"] = sorted(set(re.findall(r"\b(20\d{2}) Forms\b", body)))

    terminators = [
        i for i, line in enumerate(lines)
        if line == "View Contact Changes:" or _FORMS_HEADING.match(line)
    ]
    if not terminators:
        return detail
    anchor = min(terminators)

    label = next(
        (i for i in range(anchor - 1, -1, -1) if lines[i] in ("Lobbyist", "Principal")),
        None,
    )
    if label is None:
        return detail

    detail["kind"] = lines[label].lower()
    block = lines[label + 1 : anchor]
    if not block:
        return detail

    detail["name"] = block[0]
    rest = [b for b in block[1:] if b != ","]
    if rest and _PHONE.match(rest[-1]):
        detail["phone"] = rest.pop()
    if rest and _ZIP.match(rest[-1]):
        detail["zip"] = rest.pop()
    if rest and len(rest[-1]) == 2 and rest[-1].isalpha():
        detail["state"] = rest.pop().upper()
    if rest:
        detail["city"] = rest.pop()
    detail["address"] = " ".join(rest)
    return detail


def fetch_entity_detail(fetcher: Fetcher, kind: str, entity_id: str):
    """kind is 'lobbyist' or 'principal'."""
    page = fetcher.get("view.php", {"link": f"view_{kind}", "id": entity_id})
    detail = parse_entity_detail(page)
    detail["id"] = entity_id
    return detail


def fetch_roster(fetcher: Fetcher, lorp: str):
    return parse_roster(
        fetcher.get("ajax.php", {"functionName": "allArchiveSearch", "LORP": lorp, "term": ""})
    )


def fetch_bill_positions(fetcher: Fetcher, legislature: str, prefix: str, number: int):
    """All positions on one bill, following pagination.

    Most bills with any filings run to 3-6 pages. Reading only the first, as
    this did originally, silently drops the majority of them.
    """
    def page_params(page_number):
        params = {
            "link": "view_bill_search",
            "Legislature": legislature,
            "Prefix": prefix,
            "Number": number,
            "Suffix": "",
            "submit": "Search",
        }
        if page_number > 1:
            params["page"] = page_number
        return params

    rows, last_page = parse_bill_positions(fetcher.get("view.php", page_params(1)))

    # The pager is WINDOWED: page 1 links to 2-5, page 5 links to 6-9, and so on.
    # Trusting page 1's highest link stopped every busy bill at exactly 5 pages --
    # 52 bills landed on precisely 75 rows and none above it, which is a ceiling,
    # not a coincidence. So re-read the pager on every page and keep going until a
    # page comes back empty.
    page_number = 1
    while page_number < last_page and page_number < MAX_PAGES_PER_BILL:
        page_number += 1
        more, seen_last = parse_bill_positions(
            fetcher.get("view.php", page_params(page_number))
        )
        if not more:
            break
        rows.extend(more)
        last_page = max(last_page, seen_last)
    return rows


def load_progress(path: Path = None):
    path = Path(path or PROGRESS_PATH)
    return json.loads(path.read_text()) if path.exists() else {"done": [], "positions": 0}


def save_progress(progress: dict, path: Path = None):
    path = Path(path or PROGRESS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, indent=2) + "\n")


def scrape_positions(
    legislatures, max_number=None, prefixes=("LB",), delay=DEFAULT_DELAY,
    refresh=False, out_dir: Path = None, progress_path: Path = None, cache_dir: Path = None,
):
    """Sweep bills, collecting lobbyist positions. Resumable.

    Stops early and cleanly on Ctrl-C, having saved everything collected so far
    -- a six-hour scrape that loses its work on interrupt is a six-hour scrape
    nobody runs twice.
    """
    out_dir = Path(out_dir or DATA_DIR)
    fetcher = Fetcher(delay=delay, refresh=refresh, cache_dir=cache_dir)

    # A plain `kill` sends SIGTERM, which by default terminates without running
    # the finally block -- so the checkpoint never gets written and the work
    # since the last flush is gone. Turning it into KeyboardInterrupt routes it
    # through the same clean shutdown as Ctrl-C. Learned by killing a run and
    # losing ~90 bills.
    def _stop(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _stop)
    except ValueError:
        pass  # not on the main thread (tests); nothing to install
    progress = load_progress(progress_path)
    done = set(progress["done"])
    rows = []

    try:
        for legislature in legislatures:
            for prefix in prefixes:
                ceiling = max_number or session_max_number(legislature, prefix)
                for number in range(1, ceiling + 1):
                    token = f"{legislature}/{prefix}{number}"
                    if token in done:
                        continue
                    found = fetch_bill_positions(fetcher, legislature, prefix, number)
                    for position in found:
                        position.update(
                            {"legislature": legislature, "bill": f"{prefix}{number}"}
                        )
                        rows.append(position)
                    done.add(token)

                    # Flush often. At 100 the first checkpoint was ~4.5 minutes
                    # in, so a run killed before then looked like it had done
                    # nothing at all -- and the unflushed work really was lost.
                    if len(done) % FLUSH_EVERY == 0:
                        progress["done"] = sorted(done)
                        save_progress(progress, progress_path)
                        _append_rows(out_dir / "bill_positions.csv", rows)
                        print(
                            f"  {legislature} {prefix}{number}: "
                            f"{len(done):,} bills, {len(rows):,} rows this batch, "
                            f"{fetcher.requests_made:,} requests",
                            flush=True,
                        )
                        rows = []
    except KeyboardInterrupt:
        print("\ninterrupted -- saving what was collected", file=sys.stderr)
    except RateLimited as exc:
        # Not a failure: the server set a boundary. Stop, keep the work, and
        # let the next run resume from the checkpoint.
        print(f"\nstopping politely -- {exc}", file=sys.stderr)
    finally:
        progress["done"] = sorted(done)
        progress["positions"] = progress.get("positions", 0) + len(rows)
        progress["last_run"] = date.today().isoformat()
        save_progress(progress, progress_path)
        _append_rows(out_dir / "bill_positions.csv", rows)

    return {
        "rows_collected": len(rows),
        "bills_checked": len(done),
        "requests_made": fetcher.requests_made,
        "cache_hits": fetcher.cache_hits,
        "rate_limit_waits": fetcher.rate_limit_waits,
    }


def _append_rows(path: Path, rows):
    if not rows:
        return
    columns = [
        "legislature", "bill", "lobbyist", "lobbyist_id",
        "principal", "principal_id", "position", "registration_id", "name_truncated",
    ]
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def _scrape_principal_details(args) -> int:
    """Resolve every principal_id in bill_positions.csv to its full name."""
    positions_path = DATA_DIR / "bill_positions.csv"
    if not positions_path.exists():
        print("no bill_positions.csv -- scrape positions first", file=sys.stderr)
        return 1

    with positions_path.open(encoding="utf-8", newline="") as fh:
        ids = sorted({r["principal_id"] for r in csv.DictReader(fh) if r["principal_id"]})

    fetcher = Fetcher(delay=args.delay, refresh=args.refresh)
    out_path = DATA_DIR / "principal_details.csv"
    columns = ["id", "kind", "name", "address", "city", "state", "zip", "phone"]

    details = []
    try:
        for entity_id in ids:
            details.append(fetch_entity_detail(fetcher, "principal", entity_id))
    except (KeyboardInterrupt, RateLimited) as exc:
        print(f"\nstopping -- {exc or 'interrupted'}; saving what was collected", file=sys.stderr)
    finally:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(details)

    named = sum(1 for d in details if d["name"])
    print(f"  principals resolved   {named:>6,} of {len(ids):,}")
    print(f"  requests / cache hits {fetcher.requests_made:>6,} / {fetcher.cache_hits:,}")
    print(f"  -> {out_path.name}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legislatures", nargs="+", default=["109"])
    parser.add_argument(
        "--all", action="store_true",
        help="sweep every session in LEGISLATURES (long -- read the guard rails)",
    )
    parser.add_argument(
        "--max-number", type=int, default=None,
        help="highest bill number to check; default is per-session (see session_max_number)",
    )
    parser.add_argument("--prefixes", nargs="+", default=["LB"])
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    parser.add_argument("--roster", action="store_true", help="fetch the name roster and exit")
    parser.add_argument(
        "--principal-details", action="store_true",
        help="fetch full names/addresses for every principal_id seen in bill_positions.csv",
    )
    args = parser.parse_args(argv)

    if args.roster:
        fetcher = Fetcher(delay=args.delay, refresh=args.refresh)
        for lorp, label in (("L", "lobbyists"), ("P", "principals")):
            entries = fetch_roster(fetcher, lorp)
            path = DATA_DIR / f"{label}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["id", "name"])
                writer.writeheader()
                writer.writerows(entries)
            print(f"  {label:12} {len(entries):>6,}  -> {path.name}")
        return 0

    if args.principal_details:
        return _scrape_principal_details(args)

    legislatures = LEGISLATURES if args.all else args.legislatures
    summary = scrape_positions(
        legislatures, args.max_number, prefixes=tuple(args.prefixes),
        delay=args.delay, refresh=args.refresh,
    )
    for label, value in summary.items():
        print(f"  {label:16} {value:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
