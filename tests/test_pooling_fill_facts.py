"""Pooling review, 2026-10-25 (docs/REVIEW-POOLING-2026-10.md, P1 and
section 7): every caller pools a row from the same facts the engine does.

    python -m pytest -q tests/test_pooling_fill_facts.py

Three faults, all read from the code:

  (a) fill_ratings priced every row the solve left out WITHOUT the five team
      facts (team level, club with pros, team_id 0, team_pool, unattached
      race ceiling), then stamped rating_pool, which the boards trust. An
      unattached grade-11 row the engine pools pro came out hs_m.
  (b) speed_ratings.poolOf raised NameError for a row with no person_id.
  (c) The athlete page said "not ranked" for a no_evidence season that a
      team level had placed and the boards ranked.
"""
import collections
import os
import sys
from datetime import date

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
for _p in ("backfill", "racecast", "scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import speed_ratings as S                                         # noqa: E402
import fill_ratings as fr                                         # noqa: E402

CLUB, SCHOOL, PRO_TEAM = 501, 502, 503


@pytest.fixture
def facts(monkeypatch):
    """The engine's loaders, replaced by small fixtures."""
    monkeypatch.setattr(S, "loadAnetLevels",
                        lambda: {CLUB: "club", SCHOOL: "hs", PRO_TEAM: "club"})
    monkeypatch.setattr(S, "loadProTeams", lambda: {PRO_TEAM})
    monkeypatch.setattr(S, "loadClubPros", lambda: ({}, {}))
    # person 7 raced mostly for the club in ACADEMIC year 2025 (Aug 2025 -
    # Jul 2026), and not in 2024
    monkeypatch.setattr(S, "loadClubMajority", lambda: {(7, 2025)})
    monkeypatch.setattr(S, "loadUnattachedRaceLevel",
                        lambda: {("XC", 9001): "hs"})
    monkeypatch.setattr(fr, "proAbilityFor", lambda pid, season: None)
    return S


# ------------------------------------------------------------ rowTeamFacts

def test_no_team_is_flagged(facts):
    lvl, pros, no_team, team_pro, top = S.rowTeamFacts(
        0, None, "Unattached", 1, date(2025, 10, 1), "XC", 1)
    assert no_team and not team_pro and lvl is None and top is None


def test_the_unattached_race_ceiling_is_looked_up(facts):
    assert S.rowTeamFacts(0, None, "Unattached", 1, date(2025, 10, 1),
                          "XC", 9001)[4] == "hs"


def test_club_gate_reads_the_academic_year_of_the_row(facts):
    """March 2026 is academic 2025, the club season: the club level stands.
    March 2025 is academic 2024: no club majority, so it is dropped."""
    assert S.rowTeamFacts(CLUB, None, "Club", 7, date(2026, 3, 1),
                          "TF", 1)[0] == "club"
    assert S.rowTeamFacts(CLUB, None, "Club", 7, date(2025, 3, 1),
                          "TF", 1)[0] is None


def test_a_tfrrs_unattached_row_with_a_ceiling_is_unattached(facts):
    """pool_resolve.unattachedEntry, applied in rowTeamFacts for every caller:
    no team id at all, but the race ceiling map holds the row."""
    got = S.rowTeamFacts(None, None, "Unattached", 1, date(2025, 10, 1),
                         "XC", 9001)
    assert got[2] is True and got[4] == "hs"


def test_no_team_columns_means_every_team_fact_off(facts):
    assert S.rowTeamFacts(0, None, "Unattached", 1, date(2025, 10, 1), "XC",
                          1, have_team=False)[:4] == (None, False, False, False)


def test_census_counts_what_fired(facts):
    c = collections.Counter()
    S.rowTeamFacts(0, None, "Unattached", 1, date(2025, 10, 1), "XC", 9001,
                   census=c)
    S.rowTeamFacts(PRO_TEAM, None, "Squad", 2, date(2025, 10, 1), "XC", 2,
                   census=c)
    assert c["no_team_pro"] == 1 and c["unattached_race_hs"] == 1
    assert c["team_pool_pro"] == 1


# ------------------------------------------------------------- fill_ratings

Row = collections.namedtuple(
    "Row", "result_id person_id grade gender source school date season_level "
           "grade_untrusted fixed_grade fixed_level grade_verdict is_pro "
           "college_first team_id team_slug")
OldRow = collections.namedtuple(
    "OldRow", "result_id person_id grade gender source school date "
              "season_level grade_untrusted fixed_grade fixed_level "
              "grade_verdict is_pro college_first")


def _row(team_id, school="Unattached", grade="11", rid=1, pid=1,
         d="2025-10-04"):
    return Row(rid, pid, grade, "M", "anet", school, d, None, False, None,
               None, None, False, None, team_id, None)


def test_fill_pools_an_unattached_row_as_the_engine_does(facts):
    """★ (a). Grade 11, team_id 0, no race ceiling: the engine says pro. The
    fill used to say hs_m, price it and stamp it."""
    assert fr._rowPool(_row(0), "XC") == "pro_m"


def test_fill_follows_the_race_ceiling(facts):
    assert fr._rowPool(_row(0, rid=9001), "XC") == "hs_m"


def test_fill_pools_a_team_pool_professional_team_pro(facts):
    assert fr._rowPool(_row(PRO_TEAM, school="Squad"), "XC") == "pro_m"


def test_fill_keeps_a_school_row_where_it_was(facts):
    assert fr._rowPool(_row(SCHOOL, school="Some High"), "XC") == "hs_m"


def test_fill_without_team_columns_is_unchanged(facts):
    """A row from a query without the team columns pools as before."""
    old = OldRow(1, 1, "11", "M", "anet", "Unattached", "2025-10-04", None,
                 False, None, None, None, False, None)
    assert fr._rowPool(old, "XC") == "hs_m"


def test_the_engine_and_the_fill_agree_row_for_row(facts, monkeypatch):
    """The point of sharing rowTeamFacts: the pack's poolOf and the fill's
    _rowPool give one answer for the same row."""
    for name in ("loadSeasonLevels",):
        monkeypatch.setattr(S, name, lambda: ({}, {}))
    monkeypatch.setattr(S, "loadGradeUntrusted", lambda: {})
    monkeypatch.setattr(S, "loadProSeasons", lambda: set())
    monkeypatch.setattr(S, "proAbilityFor", lambda gkey: None)
    monkeypatch.setattr(S, "loadCollegeFirst", lambda: {})
    monkeypatch.setattr(S, "loadUpperclassFirst", lambda: {})
    for team, school, rid in ((0, "Unattached", 1), (0, "Unattached", 9001),
                              (PRO_TEAM, "Squad", 2), (SCHOOL, "Some High", 3),
                              (CLUB, "Club", 4)):
        row = _row(team, school=school, rid=rid, pid=7, d="2026-03-01")
        d = date(2026, 3, 1)
        lvl, pros, nt, tp, top = S.rowTeamFacts(team, None, school, 7, d,
                                                "XC", rid)
        engine = S.poolOf("11", "M", "anet", school, "XC", merge=True,
                          person_id=7, season=d.year, race_date=d,
                          team_level=lvl, team_has_pros=pros, no_team=nt,
                          team_pro=tp, race_top_level=top)
        assert fr._rowPool(row, "XC") == engine, (team, school, rid)


def test_the_fill_query_carries_the_team_columns():
    for sport in ("XC", "TF"):
        sql = fr._sqlFor(sport)
        assert "NULL::bigint AS team_id, NULL::text AS team_slug" in sql


# ---------------------------------------------------------------- poolOf

def test_poolof_without_a_person_does_not_raise(monkeypatch):
    """★ (b). ay and grade_verdict were unbound when person_id was None."""
    assert S.poolOf("10", "M", "anet", "Some High", "XC") == "hs_m|XC"


# -------------------------------------------------------------- app page

def test_a_placed_no_evidence_season_is_not_marked_unranked():
    """★ (c). A no_evidence season whose rows carry a pool was placed by
    something else (a team level) and ranks; one without a pool was
    dropped."""
    pytest.importorskip("flask")
    import app as A
    placed = {"pool": "hs_m"}
    dropped = {"pool": None}
    A._attach_season_verdicts({("2026", "XC"): placed, ("2025", "XC"): dropped},
                              [{"season": 2026, "method": "no_evidence"},
                               {"season": 2025, "method": "no_evidence"}])
    assert "board_note" not in placed
    assert dropped["board_note"] == "no_evidence"
