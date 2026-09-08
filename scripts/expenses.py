"""Scrape Form B and Form C lobbying expense reports.

Bill positions say who wanted what. These say what it cost.

    Form B  Lobbyist Quarterly Expense Record   -- filed by the lobbyist
    Form C  Principal Quarterly Expense Record  -- filed by the principal

Both come from one POST endpoint, ``view.php?link=view_search&type=B|C``, taking
a year, quarter checkboxes, a set of entity IDs, and the expense categories to
report. It answers with a small table of category totals.

Two facts about that endpoint shape everything here.

**It aggregates whatever you select.** Ask for three lobbyists and you get their
combined total, not three rows. Per-entity figures therefore cost one request
per entity per year -- there is no bulk mode that breaks the total down.

**Which makes the full sweep unaffordable.** The pickers hold 2,095 lobbyists
and 2,093 principals over 27 years: 113,076 requests, roughly 78 hours against a
small government server. So the default scope is the entities that actually
appear elsewhere in the project, over the electronic-filing era -- about 12,000
requests. That is the same rule the project applies to the Secretary of State:
look up only what another dataset already points at.

The aggregate mode is separate and nearly free: one request per year per form
gives statewide totals, a real series in its own right (lobbyist compensation
reported statewide was $26,896,980.66 in 2025).

Usage:
    python scripts/expenses.py --aggregate                 # ~54 requests
    python scripts/expenses.py --entities --from-year 2015 # the scoped sweep
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lobby import DATA_DIR, Fetcher, RateLimited, _clean  # noqa: E402

# Confirmed against the live form. Form B reports what a lobbyist received and
# spent; Form C what a principal paid out. The lists differ by two fields.
CATEGORIES = {
    "B": [
        "Compensation_Receipt", "Reimbursement_Receipt", "Lines1plus2", "MiscExpense",
        "Legislature_Entertainment", "Executive_Entertainment", "Other_Entertainment",
        "TotalEntertainment", "Lodging", "Travel", "Office",
        "Lobbyist_Compensation_Exp", "Lobbyist_Reimbursement_Exp",
        "Legislature_Gift", "Executive_Gift", "Other_Gift", "TotalGifts",
        "Legislature_Admissions", "Executive_Admissions", "Other_Admissions",
        "TotalAdmissionGifts", "TotalExpenses",
    ],
    "C": [
        "TotalReceipts", "MiscExpense",
        "Legislature_Entertainment", "Executive_Entertainment", "Other_Entertainment",
        "TotalEntertainment", "Lodging", "Travel", "Office",
        "Lobbyist_Compensation_Exp", "Lobbyist_Reimbursement_Exp",
        "Legislature_Gift", "Executive_Gift", "Other_Gift", "TotalGifts",
        "Legislature_Admissions", "Executive_Admissions", "Other_Admissions",
        "TotalAdmissionGifts", "TotalExpenses",
    ],
}

ID_FIELD = {"B": "LobbyistID[]", "C": "PrincipalID[]"}
QUARTERS = ("FirstQuarter", "SecondQuarter", "ThirdQuarter", "FourthQuarter")

# Electronic filing begins in 2015; earlier documents were not filed this way.
FIRST_ELECTRONIC_YEAR = 2015

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_SCRIPT = re.compile(r"<script.*?</script>", re.S)
_MONEY = re.compile(r"-?\$[\d,]+(?:\.\d{2})?")

PROGRESS_PATH = DATA_DIR / "expenses_progress.json"


def parse_expense_report(page: str) -> dict:
    """Report page -> {category label: amount}.

    Receipts and expenses sit in different columns, so a row carries its figure
    in whichever one applies; take the first money-shaped cell rather than a
    fixed column index.
    """
    start = page.find('class="main-content')
    body = _SCRIPT.sub(" ", page[start:] if start > 0 else page)

    totals = {}
    for row in _ROW.findall(body):
        cells = [_clean(c) for c in _CELL.findall(row)]
        if len(cells) < 2 or not cells[0]:
            continue
        amount = next((c for c in cells[1:] if _MONEY.fullmatch(c.strip())), None)
        if amount is None:
            continue
        totals[html.unescape(cells[0])] = float(
            amount.replace("$", "").replace(",", "")
        )
    return totals


def report_body(form: str, year: int, entity_ids=None):
    """Form fields as (name, value) pairs -- the form repeats several names."""
    body = [("Year", str(year))]
    body += [(q, "Y") for q in QUARTERS]
    if entity_ids:
        body += [(ID_FIELD[form], str(i)) for i in entity_ids]
    else:
        body.append(("All", "Y"))
    body += [("Category[]", c) for c in CATEGORIES[form]]
    body.append(("submit", "Get Report"))
    return body


def fetch_report(fetcher: Fetcher, form: str, year: int, entity_ids=None) -> dict:
    page = fetcher.post(
        "view.php",
        report_body(form, year, entity_ids),
        params={"link": "view_search", "type": form},
    )
    return parse_expense_report(page)


def hub_entity_ids(form: str):
    """The IDs worth spending requests on.

    Form B is scoped to the lobbyists in bill_positions.csv, Form C to the
    principals. Both are entities the rest of the project can join to; the other
    ~1,500 in each picker have no presence anywhere else yet.
    """
    path = DATA_DIR / "bill_positions.csv"
    if not path.exists():
        return []
    column = "lobbyist_id" if form == "B" else "principal_id"
    with path.open(encoding="utf-8", newline="") as fh:
        return sorted({r[column] for r in csv.DictReader(fh) if r.get(column)})


def load_progress():
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text())
    return {"done": []}


def save_progress(progress):
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(progress, indent=2) + "\n")


def write_rows(path: Path, rows, columns):
    if not rows:
        return
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def scrape_aggregate(fetcher: Fetcher, years) -> int:
    """Statewide totals per year per form. Cheap, and quotable on its own."""
    rows = []
    for form in ("B", "C"):
        for year in years:
            totals = fetch_report(fetcher, form, year)
            for label, amount in totals.items():
                rows.append({"form": form, "year": year, "category": label, "amount": amount})
            print(f"  form {form} {year}: {len(totals)} categories", flush=True)
    write_rows(
        DATA_DIR / "expenses_statewide.csv", rows, ["form", "year", "category", "amount"]
    )
    return len(rows)


def scrape_entities(fetcher: Fetcher, years, forms=("B", "C")) -> int:
    """Per-entity totals, resumable. One request per entity-year, unavoidably."""
    progress = load_progress()
    done = set(progress["done"])
    written = 0

    for form in forms:
        ids = hub_entity_ids(form)
        out = DATA_DIR / ("expenses_lobbyist.csv" if form == "B" else "expenses_principal.csv")
        columns = ["form", "entity_id", "year", "category", "amount"]
        batch = []
        print(f"form {form}: {len(ids)} entities x {len(years)} years", flush=True)

        try:
            for entity_id in ids:
                for year in years:
                    token = f"{form}/{entity_id}/{year}"
                    if token in done:
                        continue
                    for label, amount in fetch_report(fetcher, form, year, [entity_id]).items():
                        # Most entity-years are empty; a row of zeroes for each
                        # would bury the ones that are not.
                        if amount:
                            batch.append({
                                "form": form, "entity_id": entity_id, "year": year,
                                "category": label, "amount": amount,
                            })
                    done.add(token)

                    if len(done) % 50 == 0:
                        progress["done"] = sorted(done)
                        save_progress(progress)
                        write_rows(out, batch, columns)
                        written += len(batch)
                        batch = []
                        print(
                            f"  {form}: {len(done):,} entity-years, "
                            f"{fetcher.requests_made:,} requests",
                            flush=True,
                        )
        except (KeyboardInterrupt, RateLimited) as exc:
            print(f"\nstopping -- {exc or 'interrupted'}", file=sys.stderr)
        finally:
            progress["done"] = sorted(done)
            progress["last_run"] = date.today().isoformat()
            save_progress(progress)
            write_rows(out, batch, columns)
            written += len(batch)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", action="store_true", help="statewide totals per year")
    parser.add_argument("--entities", action="store_true", help="per-entity totals (long)")
    parser.add_argument("--from-year", type=int, default=FIRST_ELECTRONIC_YEAR)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    parser.add_argument("--forms", nargs="+", choices=["B", "C"], default=["B", "C"])
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)

    if not args.aggregate and not args.entities:
        parser.error("choose --aggregate or --entities")

    years = list(range(args.from_year, args.to_year + 1))
    fetcher = Fetcher(delay=args.delay, refresh=args.refresh)

    written = 0
    if args.aggregate:
        written += scrape_aggregate(fetcher, years)
    if args.entities:
        written += scrape_entities(fetcher, years, tuple(args.forms))

    print(f"  rows written      {written:>8,}")
    print(f"  requests / cached {fetcher.requests_made:>8,} / {fetcher.cache_hits:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
