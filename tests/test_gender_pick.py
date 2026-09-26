"""One person's gender is decided the same way in both places, or the site
disagrees with the ratings it is displaying.

The engine pools an athlete from a LATERAL over `athletes`;
build_ranking_results pools them again from a temp table. If the two tie-break
differently, an athlete is rated in one pool and ranked in another.

    python tests/test_gender_pick.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = io.open(os.path.join(ROOT, "engine", "speed_ratings_db.py"),
             encoding="utf-8").read()
BR = io.open(os.path.join(ROOT, "racecast", "build_ranking_results.py"),
             encoding="utf-8").read()
BF = io.open(os.path.join(ROOT, "backfill", "backfill_normalize.py"),
             encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def norm(sql):
    """Whitespace-insensitive, so indentation is not a test failure."""
    return re.sub(r"\s+", " ", sql).strip().lower()


# 1. THE ENGINE. The per-row lateral became a table built once per stream
#    (2026-09-26); the rule is stated once, in _PACK_GENDER_SQL, and both
#    query builders join it.
em = re.search(r"_PACK_GENDER_SQL = \"\"\"(.*?)\"\"\"", DB, re.S)
ok(em is not None, "_PACK_GENDER_SQL not found in the engine")
lat = norm(em.group(1)) if em else ""
ok("group by a.athlete_id, a.gender" in lat, "the engine counts rows per gender")
ok("order by athlete_id, n desc, gender desc" in lat,
   "majority decides, ties to 'M' ('M' > 'F', so DESC)")
ok("a.gender in ('m', 'f')" in lat, "only M and F count")
ok("order by a.school" not in lat,
   "the alphabetical-by-school tie-break is gone -- it picked a gender by "
   "which school NAME sorted first")
ok(DB.count("LEFT JOIN tmp_pack_gender a") == 2,
   "both query builders join the one table")
ok("SELECT a.gender FROM athletes a" not in DB,
   "no per-row gender lateral is left in the engine")

# 2. THE SITE. Same rule, expressed as DISTINCT ON over a pre-aggregate.
m = re.search(r"_GENDER_TEMP_SQL = \"\"\"(.*?)\"\"\"", BR, re.S)
ok(m is not None, "_GENDER_TEMP_SQL not found")
tmp = norm(m.group(1)) if m else ""
ok("group by a.athlete_id, a.gender" in tmp,
   "the temp table counts rows per person per gender")
ok("order by person_id, n desc, gender desc" in tmp,
   "and orders majority-first, ties to 'M' -- the same rule as the lateral")
ok("order by person_id, school" not in tmp,
   "the old alphabetical ordering is gone here too")

# 3. THE BACKFILL. Third copy, and the one that matters most: the gender
#    picks the pool, the pool sets the scale normalized_time is written on,
#    and that value is frozen into the row.
m2 = re.search(r"def _loadGenders\(cur\):(.*?)out = \{aid", BF, re.S)
ok(m2 is not None, "_loadGenders not found")
bf = norm(m2.group(1)) if m2 else ""
ok("group by athlete_id, gender" in bf,
   "the backfill counts rows per person per gender")
ok("order by athlete_id, n desc, gender desc" in bf,
   "and uses the same majority-first, ties-to-'M' ordering")
ok("select athlete_id, gender from athletes\"" not in norm(BF),
   "the unordered SELECT is gone -- it resolved to whatever row the heap "
   "returned last, which is physical order, not a rule")

# 4. ALL THREE AGREE. Same evidence (count of `athletes` rows), same direction,
#    same tie-break letter -- stated as one check so a half-change fails.
ok(("n desc" in lat) == ("n desc" in tmp) == ("n desc" in bf) == True,
   "one of the three ranks by count and the others do not")
for name, sql in (("engine lateral", lat), ("ranking temp", tmp),
                  ("backfill", bf)):
    ok(sql.count("desc") == 2,
       f"{name}: both ordering keys must be DESC")
    ok("gender desc" in sql,
       f"{name}: ties must go to 'M'")

# 5. A merged person is still a merged person. The rule makes the merge land
#    on the better-evidenced side; it does not separate two people. If
#    something starts claiming otherwise, this test should be revisited.
ok("#93" in BR, "the note pointing at the un-merge issue is still there")

# 6. THE ROWS OUTRANK THE PROFILES (issue 164). All three readers prefer
#    person_gender when it exists, and the pack and the boards take a split
#    person's row by its own label.
PG = io.open(os.path.join(ROOT, "engine", "person_gender.py"),
             encoding="utf-8").read()
ok("def packGenderExpr" in PG and "def boardGenderExpr" in PG,
   "person_gender exposes the two gender expressions")
ok(DB.count("_pg.packGenderExpr(") == 2,
   "the pack's two queries take their gender from person_gender")
ok(DB.count("{_personGenderJoin()}") == 2,
   "and both join it")
ok(BR.count("_pg.boardGenderExpr(") == 2,
   "the boards' two queries take a split person's row by its label")
ok("LEFT JOIN {pg_table} pg" in BR and "_PG_EMPTY" in BR,
   "the boards' temp prefers person_gender and survives its absence")
ok("FROM person_gender" in BF, "the backfill overlays person_gender")
ok("split" in PG and "least(n_m, n_f) >= 5" in PG,
   "the split rule: both genders 5+ rows and the minority a third")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  the engine's two gender laterals agree ............ OK")
print("  majority of rows decides, ties to 'M' ............. OK")
print("  the site's temp table uses the identical rule ..... OK")
print("  the backfill uses it too, and no longer takes the")
print("    last heap row .................................... OK")
print("\nall gender-pick checks passed")
