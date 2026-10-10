"""createShadow's migrations must not run on athlete_season.

The 2026-09-01 pipeline lost step 10 after the full load: the #46
DROP NOT NULL on speed_rating ran against athlete_season, which has no such
column. Text check, no database.

★ THE MIGRATIONS MOVED (d3d8257): createShadow now calls _migrateLive(conn,
  like) first, and _migrateLive holds the ALTERs behind per-table guards.
  athlete_season gets the UNIT columns on purpose (2026-09-06: the ability
  board and the rank line filter that table too), so what has to hold is:
  every ranking_results-only ALTER sits under the ranking_results guard, the
  athlete_season branch adds unit columns and nothing else, and the
  migration runs before the shadow is dropped and shaped from the live table.

    python tests/test_shadow_migration.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "racecast", "build_ranking_results.py"),
              encoding="utf-8").read()


def _fn(name):
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 1)]


def test_shadow_migration():
    fails = []
    shadow = _fn("createShadow")
    mig = _fn("_migrateLive")
    rr_guard = 'if like == "ranking_results":'
    as_guard = 'if like == "athlete_season":'

    if "_migrateLive(conn, like)" not in shadow:
        fails.append("createShadow no longer migrates the live table")
    elif (shadow.index("_migrateLive(conn, like)")
          > shadow.index("DROP TABLE IF EXISTS {name}")):
        fails.append("the migration must run before the shadow is dropped "
                     "and shaped from the live table")

    if rr_guard not in mig:
        fails.append("_migrateLive has no ranking_results guard")
    else:
        g = mig.index(rr_guard)
        for stmt in ("ADD COLUMN IF NOT EXISTS {col}",
                     "ALTER COLUMN {col} DROP NOT NULL"):
            if mig.find(stmt) < g:
                fails.append(f"{stmt!r} runs before the ranking_results guard")
        for col in ('("distance", "real")', '("speed_rating", "time_seconds")'):
            if mig.find(col) < g:
                fails.append(f"{col} is migrated outside the ranking_results "
                             "guard")

    if as_guard in mig:
        a = mig.index(as_guard)
        branch = mig[a:mig.index(rr_guard, a)] if rr_guard in mig[a:] else mig[a:]
        if "DROP NOT NULL" in branch or "distance" in branch:
            fails.append("the athlete_season branch does more than add the "
                         "unit columns")
        if 'ADD COLUMN IF NOT EXISTS "{_u}"' not in branch:
            fails.append("the athlete_season branch no longer adds the unit "
                         "columns")

    assert not fails, "\n".join(fails)


if __name__ == "__main__":
    try:
        test_shadow_migration()
    except AssertionError as e:
        print("FAILED:")
        print("  - " + str(e).replace("\n", "\n  - "))
        sys.exit(1)
    print("test_shadow_migration: all checks passed")
