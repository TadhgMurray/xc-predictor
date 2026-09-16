# Project: xc-predictor / tests
# File:    test_crest_level_key.py
# Purpose: one (school, state) can hold TWO INSTITUTIONS -- Amherst College
#          and Amherst Regional High School -- so the crest key carries the
#          level (owner, 2026-09-16: "The anet pools should match our school
#          pools. If they don't, separate them", after "Amherst college
#          changed from actual to the falcons logo"). No database, no network.
#
#   python -m pytest -q tests/test_crest_level_key.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import school_logo as L                                        # noqa: E402
import scrape_school_logos as S                                # noqa: E402


def _labels():
    import school_identity as si
    si._LABELS.update({"loaded": True, "map": {"Amherst": "MA"},
                       "clusters": {"Amherst": {"MA": 1.0}}})


def test_an_empty_level_hashes_exactly_as_it_did():
    """Every crest already on disk must keep its file name, or the whole
    store is orphaned by a column."""
    assert L.fileFor("Amherst", "MA") == L.fileFor("Amherst", "MA", "")
    assert L.fileFor("Amherst", "MA") == L.fileFor("Amherst", "MA", None)
    assert L.fileFor("Amherst", "MA", "college") != L.fileFor("Amherst", "MA")
    assert L.fileFor("Amherst", "MA", "COLLEGE") == L.fileFor("Amherst", "MA", "college")


def test_the_level_wins_and_the_level_less_row_is_the_fallback():
    _labels()
    L._CRESTS.update({"loaded": True, "map": {"Amherst": [
        ("MA", "aaaaaaaa", "/x/shared.png", ""),
        ("MA", "bbbbbbbb", "/x/college.png", "college")]}})
    assert L.crestState("Amherst", "MA", level="college")[2] == "/x/college.png"
    # a level with no row of its own falls back, which is the old behaviour
    assert L.crestState("Amherst", "MA", level="hs")[2] == "/x/shared.png"
    assert L.crestState("Amherst", "MA")[2] == "/x/shared.png"
    # and the URL says which file it means
    assert L.logoUrl("Amherst", "MA", "college") == \
        "/img/school/Amherst.png?state=MA&level=college"
    assert L.logoUrl("Amherst", "MA") == "/img/school/Amherst.png?state=MA"


def test_the_query_fallback_picks_in_the_same_order_as_the_cache():
    """pickRow answers when the start-up cache never loaded, so the two
    must not disagree about which crest a mention gets."""
    rows = [{"state": "MA", "path": "shared.png", "level": "",
             "shared": False, "override": None},
            {"state": "MA", "path": "college.png", "level": "college",
             "shared": False, "override": None}]
    assert L.pickRow(rows, "MA", "college")["path"] == "college.png"
    assert L.pickRow(rows, "MA", "hs")["path"] == "shared.png"
    assert L.pickRow(rows, "MA")["path"] == "shared.png"
    assert L.pickRow([], "MA", "college") is None


def test_the_table_carries_the_level_in_its_key():
    assert "level      text NOT NULL DEFAULT ''" in S.DDL
    assert "PRIMARY KEY (school, state, level)" in S.DDL
    # ⚠ and no SQL comment inside the body: ensureTable turns every line of
    #   it into an ALTER, and a `--` line becomes a column named `--`
    body = S.DDL[S.DDL.index("("):]
    assert "--" not in body


def test_the_key_migration_is_savepointed_and_never_raises():
    """It runs at the top of every job, including read-only ones, so a probe
    it cannot read must leave the key alone rather than end the run -- and a
    failed ALTER must not poison the caller's transaction."""
    src = open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py")).read()
    body = src[src.index("def ensureLevelKey("):src.index("SHA_INDEX = ")]
    assert "SAVEPOINT school_logo_key" in body
    assert "ROLLBACK TO SAVEPOINT school_logo_key" in body
    assert "except Exception" in body

    class _Bad:
        def execute(self, sql, params=None):
            if "key_column_usage" in sql:
                raise RuntimeError("no information_schema here")
        def fetchone(self):
            return None
    assert S.ensureLevelKey(_Bad()) is False        # no raise, no migration


def test_the_writers_all_take_a_level():
    src = open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py")).read()
    assert "def writeFile(school, state, png, directory=None, level=None):" in src
    assert "name = fileFor(school, state, level)" in src
    assert "def storedKind(cur, school, state, level=None):" in src
    assert "ON CONFLICT (school, state, level) DO UPDATE" in src
    # the level-less row still counts when ranking, or "anet wins" wins
    # against nothing and replaces the crest it should have lost to
    ranked = src[src.index("def storedKind("):src.index("def record(")]
    assert "(level = %s OR level = '')" in ranked


def test_anet_files_a_mascot_under_its_own_teams_level():
    src = open(os.path.join(_ROOT, "scripts", "anet_teams.py")).read()
    assert "lv = anet_levels.get(team_id) or \"\"" in src
    assert 'if lv not in ("elem", "ms", "hs", "college"):' in src
    assert "writeFile(school, state, png, args.dir,\n                                             level=lv)" in src
    assert "level=lv)" in src[src.index("record(cur, school, state, name"):][:400]
    # and it may not REPLACE a crest on a two-institution pair
    assert "keep = args.keep_better or (school, state) in multi_level" in src


def test_the_damaged_pairs_are_findable_and_re_askable():
    """★ OWNER: "can we rescrape them?" A crest is never one-way, and what
    to re-ask is knowable exactly: a pair holding two institutions whose
    stored crest is anet's mascot filed under no level."""
    src = open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py")).read()
    body = src[src.index("def damagedPairs("):src.index("def targets(")]
    assert "HAVING count(*) >= 2" in body
    assert "l.kind = 'anet'" in body and "COALESCE(l.level, '') = ''" in body
    assert "NOT is_bucket" in body
    # --fix-multi re-asks exactly those PAIRS, not their names
    assert '"--fix-multi"' in src
    assert "pairs = damagedPairs(cur) if args.fix_multi else None" in src
    assert "if args.fix_multi:\n        args.redo = True" in src
    tgt = src[src.index("def targets("):src.index("def writeFile(")]
    assert "(w.school, w.state) IN (SELECT school, state FROM " in tgt
