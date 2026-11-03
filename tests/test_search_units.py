"""Conferences and divisions in search (owner, 2026-11-03: "can you search
for conferences/divisions and such?")."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import search_index as SI   # noqa: E402
import unit_board as UB     # noqa: E402


def test_one_row_per_conference_whatever_its_spelling():
    rows = SI.unitRows("conference", True, [
        ("MOUNTAIN WEST", "CO", "Colorado State"),
        ("MOUNTAIN WEST", "NM", "New Mexico"),
        ("MTN WEST", "UT", "Utah State"),
        ("NESCAC", "MA", "Williams"),
        ("NESCAC", "CT", "Wesleyan"),
        ("GNAC", "WA", "Seattle Pacific"),
        ("GNAC", "OR", "Western Oregon"),
        ("Gnac Combined Events", "WA", "Western Washington"),   # the board's own fold
    ])
    by = {r[1].upper(): r for r in rows}
    assert len(rows) == 3, rows
    assert by["GNAC"][7] == 3
    mw = next(r for r in rows if "mountain" in r[4].lower())
    assert mw[0] == "unit" and mw[3].startswith("/unit/conference/MOUNTAIN%20WEST")
    assert "mtn west" in mw[4].lower()              # the other spelling finds it
    assert mw[7] == 3 and "3 schools" in mw[2]
    assert "NESCAC" in by and by["NESCAC"][3].startswith("/unit/conference/NESCAC")


def test_high_school_units_stay_per_state_and_carry_it():
    rows = SI.unitRows("league", False, [
        ("MIDDLESEX LEAGUE", "MA", "Lexington"),
        ("MIDDLESEX LEAGUE", "NJ", "Somewhere"),
    ])
    assert len(rows) == 2
    assert all("state=" in r[3] and r[3].startswith("/unit/league/") for r in rows)


def test_a_kind_with_no_page_keeps_the_board_link():
    rows = SI.unitRows("county", False, [("ESSEX", "MA", "Lynn")])
    assert rows[0][3].startswith("/rankings?")


def test_logo_for_link_reads_the_unit_back():
    assert UB.logoForLink("/school/X") is None
    assert UB.logoForLink("/unit/conference/") is None
    UB.logoForLink("/unit/conference/NESCAC?pool=college_m")   # no crash without a cache


def test_the_page_and_the_dropdown_know_units():
    a = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert '"school", "unit")' in a and "_stampSearchMarks(rows)" in a
    t = open(os.path.join(ROOT, "racecast", "templates", "search.html"), encoding="utf-8").read()
    assert "('unit','Conferences')" in t
    js = open(os.path.join(ROOT, "racecast", "static", "topbar-search.js"), encoding="utf-8").read()
    assert "sr-mark" in js
