"""No network. Fixtures are trimmed from real cached responses."""

import json

import pytest
from lobby import (
    Fetcher,
    load_progress,
    parse_bill_positions,
    parse_roster,
    save_progress,
)

# Real markup, trimmed: a header row, two rows that differ ONLY by
# RegistrationID, and a row whose principal name the site truncated.
BILL_PAGE = """
<div class="main-content">
<table>
<tr><th><a href="?page=1&sort=lobby">Lobbyist</a></th><th>Principal</th>
    <th>Position</th><th>View</th></tr>
<tr><td><a href="view.php?link=view_lobbyist&id=2208">American Communications Group, Inc.</a></td>
    <td><a href="view.php?link=view_principal&id=2446">Civic Nebraska</a></td>
    <td>Oppose</td>
    <td><a href="view.php?link=view_form&form=formd&RegistrationID=21409">View</a></td></tr>
<tr><td><a href="view.php?link=view_lobbyist&id=2208">American Communications Group, Inc.</a></td>
    <td><a href="view.php?link=view_principal&id=2446">Civic Nebraska</a></td>
    <td>Oppose</td>
    <td><a href="view.php?link=view_form&form=formd&RegistrationID=20452">View</a></td></tr>
<tr><td><a href="view.php?link=view_lobbyist&id=100711">Lowe, John</a></td>
    <td><a href="view.php?link=view_principal&id=2285">Associated Beverage Distributors of...</a></td>
    <td>Neutral</td>
    <td><a href="view.php?link=view_form&form=formd&RegistrationID=99">View</a></td></tr>
</table>
<a href="?page=2">2</a><a href="?page=3">3</a>
</div>
"""

EMPTY_PAGE = '<div class="main-content"><p>No records found</p></div>'


def test_ids_are_captured_not_just_names():
    """Names alone are not enough -- see test_truncated_names_are_flagged."""
    rows, _ = parse_bill_positions(BILL_PAGE)
    assert rows[0]["lobbyist_id"] == "2208"
    assert rows[0]["principal_id"] == "2446"
    assert rows[0]["registration_id"] == "21409"


def test_rows_differing_only_by_registration_are_both_kept():
    """Two filings, not one duplicate. Collapsing them would be data loss."""
    rows, _ = parse_bill_positions(BILL_PAGE)
    civic = [r for r in rows if r["principal_id"] == "2446"]
    assert len(civic) == 2
    assert {r["registration_id"] for r in civic} == {"21409", "20452"}


def test_truncated_names_are_flagged():
    """The site truncates in the markup; the full name is nowhere on the page.

    28% of a real 388-row sample came back truncated, so this is the norm, not
    an edge case. The flag tells a consumer to resolve via principal_id.
    """
    rows, _ = parse_bill_positions(BILL_PAGE)
    truncated = [r for r in rows if r["name_truncated"]]
    assert len(truncated) == 1
    assert truncated[0]["principal"].endswith("...")
    assert truncated[0]["principal_id"] == "2285"  # the only usable identifier
    assert all(not r["name_truncated"] for r in rows if r["principal_id"] == "2446")


def test_last_page_is_detected():
    _, last_page = parse_bill_positions(BILL_PAGE)
    assert last_page == 3


def test_header_row_is_not_a_position():
    rows, _ = parse_bill_positions(BILL_PAGE)
    assert all(r["lobbyist"] != "Lobbyist" for r in rows)
    assert len(rows) == 3


def test_bill_with_no_filings_yields_nothing():
    rows, last_page = parse_bill_positions(EMPTY_PAGE)
    assert rows == []
    assert last_page == 1


def test_roster_drops_the_blank_label():
    """Principal id 2969 has an empty name in their system -- a real record."""
    payload = json.dumps(
        [{"value": "2969", "label": ""}, {"value": "1431", "label": "3M"},
         {"value": "1443", "label": " AARP "}]
    )
    entries = parse_roster(payload)
    assert entries == [{"id": "1431", "name": "3M"}, {"id": "1443", "name": "AARP"}]


# --- politeness and resumability --------------------------------------------


def test_cache_hit_makes_no_request(tmp_path):
    fetcher = Fetcher(delay=0, cache_dir=tmp_path)
    key = list(tmp_path.glob("*")) or None
    assert key is None

    class Boom:
        headers = {}

        def get(self, *a, **k):
            raise AssertionError("should not hit the network")

    # Prime the cache by hand, then prove the fetcher never calls out.
    import hashlib
    from lobby import BASE

    url = f"{BASE}/view.php"
    # The key includes the request body, so a GET and a POST to the same URL
    # cannot collide -- every expense report is a POST to one endpoint.
    digest = hashlib.sha256(
        f"{url}?{sorted({'a': '1'}.items())}|None".encode()
    ).hexdigest()[:20]
    (tmp_path / f"{digest}.html").write_text("cached body", encoding="utf-8")

    fetcher.session = Boom()
    assert fetcher.get("view.php", {"a": "1"}) == "cached body"
    assert fetcher.cache_hits == 1
    assert fetcher.requests_made == 0


def test_progress_round_trips(tmp_path):
    path = tmp_path / "progress.json"
    assert load_progress(path) == {"done": [], "positions": 0}
    save_progress({"done": ["109/LB1"], "positions": 7}, path)
    assert load_progress(path)["done"] == ["109/LB1"]


def test_default_delay_is_conservative():
    """0.8s tripped a 429 after 31 bills. Do not lower this casually."""
    from lobby import DEFAULT_DELAY

    assert DEFAULT_DELAY >= 2.0


def test_windowed_pager_is_followed_past_the_first_window():
    """The pager only ever shows the next few pages, never the true last.

    Trusting page 1's highest link capped 52 busy bills at exactly 75 rows --
    15 per page times the 5 pages page 1 happened to advertise. parse must
    report what THIS page links to, so the fetcher can extend the ceiling as it
    goes rather than stopping at the first window.
    """
    window_1 = '<div class="main-content"><table><tr><th>Lobbyist</th></tr></table>' \
               '<a href="?page=2">2</a><a href="?page=5">5</a></div>'
    window_2 = '<div class="main-content"><table><tr><th>Lobbyist</th></tr></table>' \
               '<a href="?page=6">6</a><a href="?page=9">9</a></div>'
    assert parse_bill_positions(window_1)[1] == 5
    assert parse_bill_positions(window_2)[1] == 9


def test_page_cap_exists_so_a_broken_pager_cannot_loop_forever():
    from lobby import MAX_PAGES_PER_BILL

    assert MAX_PAGES_PER_BILL >= 20
