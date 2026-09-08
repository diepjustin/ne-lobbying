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
