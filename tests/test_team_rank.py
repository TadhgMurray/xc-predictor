"""The team ranker's self-check, run as a test so its silence is checked.

The ranker races a meet with no times on any row. A finish check added to
the scorer for the race pages (2026-09-02) asked every row for a time, and
for four days every team board built empty while the self-check still
passed -- because nobody ran it. Now the build cannot go green without it.
"""
import io
import contextlib
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import team_rank          # noqa: E402
from meet_compile import scoreRows  # noqa: E402


def test_self_check_passes():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bad = team_rank._selfCheck()
    assert not bad, out.getvalue()


def test_rank_teams_with_no_times_scores_every_full_squad():
    def squad(school, ratings):
        return [{"school": school, "state": "OR", "rating": r,
                 "person_id": f"{school}-{i}", "name": f"{school} {i}"}
                for i, r in enumerate(ratings, start=1)]
    teams = team_rank.rankTeams(squad("A", [130, 129, 128, 127, 126])
                                + squad("B", [125, 124, 123, 122, 121]))
    assert [t["school"] for t in teams] == ["A", "B"]


def test_rows_that_carry_a_null_time_still_do_not_finish():
    rows = [{"school": "A", "time_seconds": None} for _ in range(5)]
    assert scoreRows(rows)["teams"] == []
    rows = [{"school": "A", "time_seconds": 900 + i} for i in range(5)]
    assert [t["school"] for t in scoreRows(rows)["teams"]] == ["A"]
