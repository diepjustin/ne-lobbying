"""Build ne-lobbying/index.html -- the dataset's landing page.

Not a search interface. Cross-source search lives in ../ne-connect/; this page
answers what a reporter following a "source project" link needs: what this
dataset is, how much of it exists yet, what will bite them, and where the
Legislature's own copy is.

Coverage is read from the scraper's checkpoint rather than asserted, because the
sweep is incomplete and the page must not imply otherwise. It also shows the
most-lobbied bills, which is the one thing this dataset can say that no other
Nebraska public record can.
"""

from __future__ import annotations

import collections
import csv
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "bill_positions.csv"
PROGRESS = ROOT / "data" / "scrape_progress.json"
DETAILS = ROOT / "data" / "principal_details.csv"
OUT = ROOT / "index.html"

# Every regular session runs to about LB1300 and LR500; specials to a few dozen.
PLANNED_BILLS = 6600 + 2600

STYLE = """
  :root {
    --bg:#fff; --panel:#f6f7f9; --border:#d9dde3; --text:#14181d; --muted:#626b76;
    --accent:#d00000; --accent-soft:#fdecec; --shadow:0 1px 3px rgba(0,0,0,.08);
    --support:#1c7f4e; --oppose:#b3261e; --neutral:#626b76;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#14171a; --panel:#1c2025; --border:#2c323a; --text:#e6e9ed;
      --muted:#949dab; --accent:#ff6b6b; --accent-soft:#2a1c1d;
      --shadow:0 1px 3px rgba(0,0,0,.4);
      --support:#4cc38a; --oppose:#ff8a80; --neutral:#949dab;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:82ch;margin:0 auto;padding:26px 20px 60px}
  h1{margin:0 0 6px;font-size:22px;letter-spacing:-.01em}
  h2{margin:30px 0 10px;font-size:15px;text-transform:uppercase;letter-spacing:.05em;
    color:var(--muted)}
  p{margin:0 0 12px}
  .lede{color:var(--muted)}
  .stats{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}
  .stat{background:var(--panel);border:1px solid var(--border);border-radius:4px;
    padding:8px 12px;min-width:126px;box-shadow:var(--shadow)}
  .stat b{display:block;font-size:18px;letter-spacing:-.02em;
    font-variant-numeric:tabular-nums}
  .stat span{color:var(--muted);font-size:12px}
  table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 16px}
  th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;
    letter-spacing:.04em}
  td.n{text-align:right;font-variant-numeric:tabular-nums}
  .s{color:var(--support)} .o{color:var(--oppose)} .x{color:var(--neutral)}
  .warn{background:var(--accent-soft);border-left:3px solid var(--accent);
    border-radius:3px;padding:10px 12px;margin:14px 0}
  .warn strong{color:var(--accent)}
  ul{margin:0 0 12px;padding-left:20px}
  li{margin-bottom:7px}
  a{color:var(--accent)}
  footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--border);
    color:var(--muted);font-size:13px}
"""


def main() -> int:
    if not POSITIONS.exists():
        print("no data/bill_positions.csv -- run scripts/lobby.py first")
        return 1

    with POSITIONS.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    names = {}
    if DETAILS.exists():
        with DETAILS.open(encoding="utf-8", newline="") as fh:
            names = {r["id"]: r["name"] for r in csv.DictReader(fh) if r.get("name")}

    stance = collections.Counter(r["position"] for r in rows)
    bills = {(r["legislature"], r["bill"]) for r in rows}
    principals = {r["principal_id"] for r in rows if r["principal_id"]}
    lobbyists = {r["lobbyist_id"] for r in rows if r["lobbyist_id"]}

    swept, sessions, prefixes, last_run = 0, [], [], ""
    if PROGRESS.exists():
        progress = json.loads(PROGRESS.read_text())
        done = progress.get("done", [])
        swept = len(done)
        sessions = sorted({t.split("/")[0] for t in done})
        prefixes = sorted({t.split("/")[1][:2] for t in done})
        last_run = progress.get("last_run", "")

    per_bill = collections.defaultdict(collections.Counter)
    for row in rows:
        per_bill[(row["legislature"], row["bill"])][row["position"]] += 1

    busiest = sorted(per_bill.items(), key=lambda kv: -sum(kv[1].values()))[:10]
    bill_rows = "".join(
        f"<tr><td>{bill}</td><td class='n'>{sum(c.values())}</td>"
        f"<td class='n s'>{c['Support']}</td>"
        f"<td class='n o'>{c['Oppose']}</td>"
        f"<td class='n x'>{c['Neutral']}</td></tr>"
        for (_leg, bill), c in busiest
    )

    top = collections.Counter(r["principal_id"] for r in rows if r["principal_id"]).most_common(10)
    principal_rows = "".join(
        f"<tr><td>{names.get(pid, 'principal ' + pid)}</td><td class='n'>{n}</td></tr>"
        for pid, n in top
    )

    pct = swept / PLANNED_BILLS if PLANNED_BILLS else 0

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nebraska Lobbying</title>
<meta name="description" content="Which interests registered support, opposition or neutrality on Nebraska legislative bills, from the Clerk of the Legislature's lobbyist reporting system.">
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
  <h1>Nebraska Lobbying</h1>
  <p class="lede">Which private interests registered <span class="s">support</span>,
  <span class="o">opposition</span> or <span class="x">neutrality</span> on
  specific Nebraska bills, from the
  <a href="https://nebraskalegislature.gov/lobbyist/">Clerk of the Legislature's
  lobbyist reporting system</a>. This is the only Nebraska public dataset that ties
  a private interest to a named piece of legislation. Cross-source search is in the
  <a href="../ne-connect/">Public Records Hub</a>.</p>

  <div class="stats">
    <div class="stat"><b>{len(rows):,}</b><span>registered positions</span></div>
    <div class="stat"><b>{len(bills):,}</b><span>bills with filings</span></div>
    <div class="stat"><b>{len(principals):,}</b><span>principals</span></div>
    <div class="stat"><b>{len(lobbyists):,}</b><span>lobbyists</span></div>
    <div class="stat"><b>{stance['Support']:,}</b><span>support</span></div>
    <div class="stat"><b>{stance['Oppose']:,}</b><span>oppose</span></div>
    <div class="stat"><b>{stance['Neutral']:,}</b><span>neutral</span></div>
  </div>

  <div class="warn">
    <strong>Collection is about {pct:.0%} complete.</strong> {swept:,} bill numbers
    swept ({'/'.join(prefixes) if prefixes else 'LB'} only, session
    {', '.join(sessions) if sessions else 'unknown'}) out of roughly
    {PLANNED_BILLS:,} across every session the system covers. Everything below is
    real; there is simply more of it not yet collected. Absence here is not
    evidence that an interest took no position.
  </div>

  <h2>Most-lobbied bills collected so far</h2>
  <table>
    <tr><th>Bill</th><th class="n">Positions</th><th class="n">Support</th>
    <th class="n">Oppose</th><th class="n">Neutral</th></tr>
    {bill_rows}
  </table>

  <h2>Most active principals</h2>
  <table>
    <tr><th>Principal</th><th class="n">Positions</th></tr>
    {principal_rows}
  </table>

  <h2>Read this before quoting it</h2>
  <ul>
    <li><strong>The site truncates names in its own markup.</strong> The bill table
    really does publish "Associated Beverage Distributors of&hellip;" &mdash; 28% of
    a sample arrived cut short, and the full name appears nowhere on that page.
    Every row here carries the numeric principal and lobbyist IDs, and names are
    resolved from each principal's own detail page.</li>
    <li><strong>Rows that look identical usually are not.</strong> One lobbyist can
    file more than once for the same principal on the same bill; the rows differ
    only by registration ID. Deduplicating on the visible columns destroys real
    records.</li>
    <li><strong>"Neutral" is a real position, and it is common.</strong> Registering
    neutral on a bill is not the same as not registering.</li>
    <li><strong>Electronic filings begin in 2015.</strong> Earlier documents were
    not filed electronically; the Clerk provides them on request.</li>
    <li><strong>A registered position is not a vote and not an outcome.</strong> It
    records what an interest told the Legislature it wanted.</li>
  </ul>

  <footer>
    Built {date.today().isoformat()}{f"; last collected {last_run}" if last_run else ""}.
    Method, caveats and open work are in the
    <a href="https://github.com/diepjustin/diepjustin.github.io/tree/main/ne-lobbying">project README</a>.
    No analytics, no tracking, nothing loads from a third party.
  </footer>
</div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"  {len(rows):,} positions, {pct:.0%} swept  ->  {OUT.name} ({len(html) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
