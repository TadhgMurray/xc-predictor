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


def test_no_month_only_seam_left():
    bad = re.compile(r"6, 2\) >= '08'|\.month >= 8\b|\[5:7\] >= \"08\"|ACADEMIC_START_MONTH, 1\)")
    hits = []
    for d in ("engine", "racecast", "scripts", "model", "backfill", "tools", "sources"):
        for base, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(base, f)
                    for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                        if bad.search(line) and "diag_weather_window" not in p:
                            hits.append(f"{p}:{n}")
    assert hits == []
