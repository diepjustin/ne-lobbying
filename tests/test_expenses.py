"""No network. Markup is trimmed from real captured report responses."""

import pytest
from expenses import CATEGORIES, ID_FIELD, parse_expense_report, report_body

# Real shape: the label, then receipts and expenses in separate columns, so a
# row's figure appears in whichever one applies.
REPORT = """
<div class="main-content"><table>
<tr><th>Category</th><th>Receipts</th><th>Expenses</th></tr>
<tr><td>1. Compensation received by Lobbyist</td><td>$192,500.00</td><td></td></tr>
<tr><td>2. Reimbursement of Expenses</td><td>$226,938.58</td><td></td></tr>
<tr><td>13. Total (Sum of 4, 5d, 6, 7, 8, 9)</td><td></td><td>$2,296.47</td></tr>
</table></div>
"""

EMPTY = '<div class="main-content"><p>Please select a category</p></div>'


def test_amount_is_found_in_whichever_column_holds_it():
    totals = parse_expense_report(REPORT)
    assert totals["1. Compensation received by Lobbyist"] == 192500.00
    assert totals["2. Reimbursement of Expenses"] == 226938.58
    assert totals["13. Total (Sum of 4, 5d, 6, 7, 8, 9)"] == 2296.47


def test_header_and_moneyless_rows_are_skipped():
    assert "Category" not in parse_expense_report(REPORT)
    assert parse_expense_report(EMPTY) == {}


def test_body_repeats_the_names_the_form_repeats():
    """Category[] and the ID field appear many times; a dict cannot say that."""
    body = report_body("B", 2025, ["2614", "2204"])
    assert body.count(("Category[]", "Compensation_Receipt")) == 1
    assert len([1 for k, _ in body if k == "Category[]"]) == len(CATEGORIES["B"])
    assert [v for k, v in body if k == ID_FIELD["B"]] == ["2614", "2204"]
    assert ("Year", "2025") in body
    assert ("All", "Y") not in body  # explicit IDs, so not the statewide report


def test_no_ids_means_the_statewide_report():
    body = report_body("C", 2024)
    assert ("All", "Y") in body
    assert not [1 for k, _ in body if k == ID_FIELD["C"]]


def test_the_two_forms_report_different_categories():
    # Form B reports what a lobbyist was paid; Form C what a principal paid out.
    assert "Compensation_Receipt" in CATEGORIES["B"]
    assert "Compensation_Receipt" not in CATEGORIES["C"]
    assert "TotalReceipts" in CATEGORIES["C"]


@pytest.mark.parametrize("form", ["B", "C"])
def test_every_form_has_a_total(form):
    assert "TotalExpenses" in CATEGORIES[form]


# --- the dedup contract ------------------------------------------------------


class _StubFetcher:
    """Every report is REPORT (three categories), or every call stops."""

    requests_made = cache_hits = rate_limit_waits = network_retries = 0

    def __init__(self, stop_with=None):
        self.stop_with = stop_with
        self.calls = 0

    def post(self, path, data, params=None):
        self.calls += 1
        if self.stop_with is not None:
            raise self.stop_with
        return REPORT


def _rows(path):
    import csv

    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_statewide_totals_are_rewritten_not_appended(tmp_path):
    """The chain reruns --aggregate every pass; appending doubled the file
    (816 rows where 408 belonged). One pass, any number of runs."""
    from expenses import scrape_aggregate

    first = scrape_aggregate(_StubFetcher(), [2024, 2025], out_dir=tmp_path)
    scrape_aggregate(_StubFetcher(), [2024, 2025], out_dir=tmp_path)
    rows = _rows(tmp_path / "expenses_statewide.csv")
    assert first == 2 * 2 * 3  # forms x years x categories in REPORT
    assert len(rows) == first
    assert len({(r["form"], r["year"], r["category"]) for r in rows}) == first


def test_a_rewrite_with_nothing_collected_keeps_the_last_good_file(tmp_path):
    from expenses import write_rows

    path = tmp_path / "out.csv"
    write_rows(path, [{"a": "1"}], ["a"], mode="w")
    write_rows(path, [], ["a"], mode="w")
    assert _rows(path) == [{"a": "1"}]


def test_entity_sweep_marks_each_form_complete(tmp_path, monkeypatch):
    import expenses

    monkeypatch.setattr(expenses, "hub_entity_ids", lambda form: ["2614"])
    summary = expenses.scrape_entities(
        _StubFetcher(), [2025], ("B", "C"), out_dir=tmp_path, progress_path=tmp_path / "p.json",
    )
    saved = expenses.load_progress(tmp_path / "p.json")
    assert summary["outcome"] == "complete"
    assert saved["complete"] == {"B": True, "C": True}
    assert saved["done"] == ["B/2614/2025", "C/2614/2025"]
    assert len(_rows(tmp_path / "expenses_lobbyist.csv")) == 3
    assert len(_rows(tmp_path / "expenses_principal.csv")) == 3


def test_entity_sweep_stopped_on_form_b_does_not_go_on_to_form_c(tmp_path, monkeypatch):
    """Told to stop, the sweep stops; asking again for the next form is the
    opposite of polite. And the progress file says which form is unfinished."""
    import expenses
    from lobby import RateLimited

    monkeypatch.setattr(expenses, "hub_entity_ids", lambda form: ["2614"])
    fetcher = _StubFetcher(stop_with=RateLimited("429"))
    summary = expenses.scrape_entities(
        fetcher, [2025], ("B", "C"), out_dir=tmp_path, progress_path=tmp_path / "p.json",
    )
    saved = expenses.load_progress(tmp_path / "p.json")
    assert summary["outcome"] == "stopped"
    assert saved["complete"] == {"B": False}
    assert "C" not in saved["complete"]
    assert fetcher.calls == 1


def test_resumed_entity_sweep_adds_only_what_was_missing(tmp_path, monkeypatch):
    import expenses

    monkeypatch.setattr(expenses, "hub_entity_ids", lambda form: ["2614", "2204"])
    progress = tmp_path / "p.json"
    expenses.save_progress({"done": ["B/2614/2025"], "complete": {"B": False}}, progress)
    fetcher = _StubFetcher()
    expenses.scrape_entities(fetcher, [2025], ("B",), out_dir=tmp_path, progress_path=progress)
    assert fetcher.calls == 1  # 2614 was already done; only 2204 fetched
    rows = _rows(tmp_path / "expenses_lobbyist.csv")
    assert {r["entity_id"] for r in rows} == {"2204"}
    assert expenses.load_progress(progress)["complete"] == {"B": True}
