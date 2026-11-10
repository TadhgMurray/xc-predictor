"""One season clock, opening Aug 15 (owner, 2026-11-01, Cael Grotenhuis's
early-August summer races). No copy of the old month-only seam is left."""
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import season_year as S  # noqa: E402


def test_the_seam_is_august_15():
    assert S.seasonYearFromIso("TF", "2026-08-08") == 2025      # Guardian Mile: the season closing
    assert S.seasonYearFromIso("TF", "2026-08-15") == 2026
    assert S.seasonYearFor("XC", datetime.date(2026, 8, 20)) == 2026
    assert S.seasonStart(2026) == datetime.date(2026, 8, 15)
    assert "substring(r.date, 6, 5) >= '08-15'" in S.seasonYearSqlInt(None, "r.date")


# scripts whose August dates are calendar windows (weather, race-day
# timing), not the season seam
CALENDAR_WINDOWS = ("diag_weather_window", "diag_race_day_inflation", "diag_sport_gap")


def test_no_month_only_seam_left():
    bad = re.compile(r"6, 2\) >= '08'|\.month >= 8\b|\[5:7\] >= \"08\"|ACADEMIC_START_MONTH, 1\)|\}-08-01\"")
    hits = []
    for d in ("engine", "racecast", "scripts", "model", "backfill", "tools", "sources"):
        for base, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(base, f)
                    for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                        if bad.search(line) and not any(k in p for k in CALENDAR_WINDOWS):
                            hits.append(f"{p}:{n}")
    assert hits == []


def test_cross_country_opens_august_1():
    """owner, 2026-11-10 (Tyler Connelly's Kennedale Wildcat Run, Aug 14 2026,
    filed in his 2025 season under "Grade 12"): XC's season opens Aug 1;
    track keeps Aug 15 (Grotenhuis's summer meets close the season)."""
    assert S.seasonYearFromIso("XC", "2026-08-14") == 2026
    assert S.seasonYearFromIso("XC", "2026-07-31") == 2025
    assert S.seasonYearFromIso("TF", "2026-08-14") == 2025
    assert S.seasonStart(2026, "XC") == datetime.date(2026, 8, 1)
    assert "substring(r.date, 6, 5) >= '08-01'" in S.seasonYearSqlInt("XC", "r.date")
    assert "substring(r.date, 6, 5) >= '08-15'" in S.seasonYearSqlInt("TF", "r.date")


# the only sport-less season keys left: a person's first collegiate race,
# which is a date with no row behind it (college_first_season.first_date)
PERSON_LEVEL = ("first_date",)


def test_every_row_reader_passes_its_sport():
    """A reader that keys a ROW's season with no sport would put an Aug 1-14
    cross country race in the old season while the boards put it in the new
    one -- the two-clock bug season_year exists to prevent."""
    bad = re.compile(r"seasonYearSqlInt\(None|seasonYearSql\(None|seasonYearFor\(None|"
                     r"seasonYearFromIso\(None")
    hits = []
    for d in ("engine", "racecast", "scripts", "backfill"):
        for base, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith(".py") or f == "season_year.py":
                    continue
                p = os.path.join(base, f)
                for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                    if bad.search(line) and not any(k in line for k in PERSON_LEVEL):
                        hits.append(f"{p}:{n}")
    assert hits == []
