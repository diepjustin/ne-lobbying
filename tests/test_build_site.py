"""build_positions_index() feeds ne-connect's fetch -- the per-principal
detail list a cross-source entity page shows inline instead of a link out.
"""

import build_site

ROW = {
    "legislature": "109", "bill": "LB1", "lobbyist": "Lowe, John",
    "lobbyist_id": "100711", "principal": "Associated Beverage Distributors of...",
    "principal_id": "2285", "position": "Neutral", "registration_id": "20841",
    "name_truncated": "True",
}


def _row(**overrides):
    return {**ROW, **overrides}


def test_positions_grouped_by_principal_id():
    index = build_site.build_positions_index([_row()])
    assert list(index["2285"][0]) == ["109", "LB1", "Neutral", "Lowe, John", "20841"]


def test_rows_missing_principal_id_are_skipped():
    index = build_site.build_positions_index([_row(principal_id="")])
    assert index == {}


def test_exact_duplicate_registration_is_not_double_counted():
    """The Legislature's own pages list some registrations twice (confirmed
    against a real sample, ~15% of rows) -- the natural key is (legislature,
    bill, registration_id, position), not the raw row."""
    index = build_site.build_positions_index([_row(), _row()])
    assert len(index["2285"]) == 1


def test_same_principal_two_bills_both_kept():
    index = build_site.build_positions_index([_row(), _row(bill="LB2", registration_id="20842")])
    assert len(index["2285"]) == 2


def test_two_principals_stay_separate():
    index = build_site.build_positions_index(
        [_row(), _row(principal_id="9999", registration_id="99999")]
    )
    assert set(index) == {"2285", "9999"}
