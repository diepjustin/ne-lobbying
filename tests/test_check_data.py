"""check_data.py is the test that the dedup contract held on real output."""

import csv

from check_data import check, duplicate_keys, main


def _write(path, rows, columns):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_duplicate_keys_are_found():
    rows = [{"a": "1", "b": "x"}, {"a": "1", "b": "x"}, {"a": "2", "b": "x"}]
    assert duplicate_keys(rows, ("a", "b")) == [(("1", "x"), 2)]
    assert duplicate_keys(rows, ("a", "b"))[0][1] == 2


def test_a_doubled_statewide_file_fails(tmp_path):
    """The bug this catches: --aggregate appended, so every rerun doubled it."""
    row = {"form": "B", "year": "2025", "category": "1. Compensation", "amount": "1.0"}
    _write(tmp_path / "expenses_statewide.csv", [row, row], list(row))
    report = check(tmp_path)
    assert report["ok"] is False
    assert report["files"]["expenses_statewide.csv"]["duplicate_keys"] == 1
    assert main([str(tmp_path)]) == 1


def test_clean_files_pass_and_absent_files_are_not_an_error(tmp_path):
    row = {"form": "B", "entity_id": "1", "year": "2025", "category": "c", "amount": "1.0"}
    _write(tmp_path / "expenses_lobbyist.csv", [row, {**row, "year": "2024"}], list(row))
    report = check(tmp_path)
    assert report["ok"] is True
    assert "expenses_principal.csv" not in report["files"]  # Form C not collected yet
    assert main([str(tmp_path)]) == 0


def test_positions_repeated_by_the_source_are_counted_not_failed(tmp_path):
    """The Legislature's page lists some registrations twice; the scraper
    copies the page. Report it for the caveat, do not fail the build on it."""
    cols = ["legislature", "bill", "registration_id", "position", "lobbyist"]
    same = {"legislature": "109", "bill": "LB6", "registration_id": "20659",
            "position": "Support", "lobbyist": "Kelley Plucker, LLC"}
    changed = {**same, "registration_id": "20227", "position": "Neutral"}
    _write(tmp_path / "bill_positions.csv",
           [same, same, changed, {**changed, "position": "Support"}], cols)
    info = check(tmp_path)["files"]["bill_positions.csv"]
    assert check(tmp_path)["ok"] is True
    assert info["rows"] == 4
    assert info["distinct_positions"] == 3
    assert info["identical_repeats_from_source"] == 1
