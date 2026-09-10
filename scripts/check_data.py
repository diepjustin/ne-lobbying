"""Assert the dedup contract on this project's outputs.

Every scraper here must be idempotent: rerunning never adds a row it already
has. This script is the check that it held. It runs before build_site.py in
the chain and in CI, and exits 1 on any duplicate key.

    expenses_statewide.csv   (form, year, category)             rewritten each run
    expenses_lobbyist.csv    (form, entity_id, year, category)  appended per token
    expenses_principal.csv   (form, entity_id, year, category)  appended per token

bill_positions.csv is reported, not asserted. The Legislature's own bill pages
list some registrations twice, byte for byte -- registration 20659 sits in two
identical rows on a single cached page -- and a lobbyist who changes position
on a bill appears once per position. The scraper copies the page; it does not
decide which rows the state meant. Cross-run duplication is prevented upstream
by the per-bill token, so the count printed here is a caveat for whoever
quotes a position total, not a failure.

Usage:
    python scripts/check_data.py            # data/ under this project
    python scripts/check_data.py DIR        # any directory of the same files
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

EXPENSE_KEYS = {
    "expenses_statewide.csv": ("form", "year", "category"),
    "expenses_lobbyist.csv": ("form", "entity_id", "year", "category"),
    "expenses_principal.csv": ("form", "entity_id", "year", "category"),
}
POSITION_KEY = ("legislature", "bill", "registration_id", "position")


def read_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def duplicate_keys(rows, key_columns):
    """[(key, count)] for every key that appears more than once."""
    counts = Counter(tuple(r[c] for c in key_columns) for r in rows)
    return sorted((k, n) for k, n in counts.items() if n > 1)


def check(data_dir: Path = None) -> dict:
    """Check every output present. Returns a report; `ok` is the verdict."""
    data_dir = Path(data_dir or DATA_DIR)
    report = {"ok": True, "files": {}}

    for name, key in EXPENSE_KEYS.items():
        path = data_dir / name
        if not path.exists():
            continue  # Form C is collected after Form B; absent is not wrong
        rows = read_rows(path)
        dups = duplicate_keys(rows, key)
        report["files"][name] = {"rows": len(rows), "duplicate_keys": len(dups), "sample": dups[:3]}
        if dups:
            report["ok"] = False

    positions = data_dir / "bill_positions.csv"
    if positions.exists():
        rows = read_rows(positions)
        identical = sum(n - 1 for n in Counter(tuple(r.items()) for r in rows).values() if n > 1)
        report["files"]["bill_positions.csv"] = {
            "rows": len(rows),
            "distinct_positions": len({tuple(r[c] for c in POSITION_KEY) for r in rows}),
            "identical_repeats_from_source": identical,
        }
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = check(Path(argv[0]) if argv else None)
    for name, info in report["files"].items():
        print(f"  {name:24} " + "  ".join(f"{k} {v:,}" for k, v in info.items() if k != "sample"))
        for key, n in info.get("sample", []):
            print(f"      duplicate x{n}: {key}")
    if not report["ok"]:
        print("check_data: duplicate keys found -- the dedup contract is broken", file=sys.stderr)
        return 1
    print("check_data: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
