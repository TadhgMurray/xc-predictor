"""createShadow's migrations must not run on athlete_season.

The 2026-09-01 pipeline lost step 10 after the full load: the #46
DROP NOT NULL on speed_rating ran against athlete_season, which has no such
column. Text check, no database.

    python tests/test_shadow_migration.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "racecast", "build_ranking_results.py"),
              encoding="utf-8").read()
i = SRC.index("def createShadow(")
body = SRC[i:SRC.index("\ndef ", i + 1)]
guard = 'if like == "ranking_results":'
fails = []
if guard not in body:
    fails.append("createShadow has no ranking_results guard")
else:
    g = body.index(guard)
    for stmt in ("ADD COLUMN IF NOT EXISTS distance",
                 "ALTER COLUMN speed_rating DROP NOT NULL",
                 'ADD COLUMN IF NOT EXISTS "{_u}"'):
        if body.find(stmt) < g:
            fails.append(f"{stmt!r} runs before the guard")
    if body.index("DROP TABLE IF EXISTS {name}") < g:
        fails.append("the shadow DROP sits inside the guard")
for f in fails:
    print("  FAIL " + f)
if fails:
    sys.exit(1)
print("  ranking_results migrations never touch athlete_season ... OK")
