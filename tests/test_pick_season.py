"""panels.pickSeason: an in-season sport shows its current season once it is
on pace (owner, 2026-09-26: "whichever season is closer")."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import panels as P  # noqa: E402

ROWS = [("2026", 90_000), ("2025", 1_000_000), ("2024", 950_000)]


def test_late_september_xc_on_pace_is_the_current_season():
    # by Sept 26 2025 the 2025 season had 300k rows; 2026 has 90k (30%)
    got = P.pickSeason(ROWS, "XC", dt.date(2026, 9, 26), lambda yr, c: 300_000)
    assert got == "2026"


def test_in_season_the_current_season_wins_even_behind_pace():
    # owner, 2026-10-08: in season, the season under way is the board,
    # however far behind last year's pace its scraping is
    got = P.pickSeason(ROWS, "XC", dt.date(2026, 10, 8), lambda yr, c: 800_000)
    assert got == "2026"


def test_in_season_an_old_newest_season_falls_to_the_old_rule():
    # the newest season on file is not the one under way (no 2026 rows yet)
    rows = [("2025", 90_000), ("2024", 1_000_000)]
    got = P.pickSeason(rows, "XC", dt.date(2026, 8, 3), lambda yr, c: 800_000)
    assert got == "2024"


def test_out_of_season_spring_leagues_do_not_count():
    # April: XC is out of season, so the whole-season rule stands
    got = P.pickSeason(ROWS, "XC", dt.date(2026, 4, 10), lambda yr, c: 1)
    assert got == "2025"


def test_the_cutoff_is_the_same_date_in_the_busiest_season():
    seen = {}
    # a newest season that is not the one under way (2027, none on file yet)
    P.pickSeason(ROWS, "XC", dt.date(2027, 9, 26),
                 lambda yr, c: seen.setdefault("c", (yr, c)) and 1)
    assert seen["c"] == ("2025", "2026-09-26")
