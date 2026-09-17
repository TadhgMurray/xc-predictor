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

    class _Cur:
        def __init__(self, row, fail=None):
            self.row, self.fail, self.sql = row, fail, []
        def execute(self, sql, params=None):
            self.sql.append(sql)
            if self.fail and self.fail in sql:
                raise RuntimeError("boom")
        def fetchone(self):
            return self.row

    # ⚠ THE CONSTRAINT'S REAL NAME. A table restored from a dump can carry
    #   any pkey name, and then a DROP of the assumed one finds nothing, the
    #   ADD fails because a key already exists, and every INSERT afterwards
    #   dies on "no unique constraint matching the ON CONFLICT".
    odd = _Cur(("school_logo_pk_from_a_dump", 2))
    assert S.ensureLevelKey(odd) is True
    assert any('DROP CONSTRAINT "school_logo_pk_from_a_dump"' in q for q in odd.sql)
    assert any("ADD PRIMARY KEY (school, state, level)" in q for q in odd.sql)
    assert S.ensureLevelKey(_Cur(("anything", 3))) is False      # already done
    # a failure rolls back to the savepoint and reports it did nothing
    broke = _Cur(None, fail="ADD PRIMARY KEY")
    assert S.ensureLevelKey(broke) is False
    assert "ROLLBACK TO SAVEPOINT school_logo_key" in broke.sql[-1]


def test_the_repair_migrates_before_it_reads_the_level():
    """damagedPairs reads `level`; on a table made before that column
    existed the query is an UndefinedColumn, which would end the run before
    the repair it was asked for."""
    src = open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py")).read()
    body = src[src.index("            # ! THE MIGRATION FIRST."):]
    assert body.index("ensureTable(cur, DDL)") < body.index("damagedPairs(cur)")
    assert body.index("ensureLevelKey(cur)") < body.index("damagedPairs(cur)")


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


def test_the_site_never_requires_a_column_the_scraper_has_not_added_yet():
    """⚠ THIS TOOK EVERY CREST OFF THE SITE (2026-09-17). `level` is added by
    scrape_school_logos.ensureTable/ensureLevelKey -- the SCRAPER. Between a
    deploy and the next scrape run the live table has no such column, so a
    reader that SELECTs it throws, loadCrests' own except swallows it, the
    start-up cache loads EMPTY, and the whole site draws no crests at all.

    A reader must never require a column its own process cannot create."""
    src = open(os.path.join(_ROOT, "racecast", "school_logo.py")).read()
    assert "def hasLevelColumn(cur, force=False):" in src
    # both readers ask first
    load = src[src.index("def loadCrests("):src.index("def crestPath(")]
    assert 'hasLevelColumn(cur, True)' in load and '"\'\'"' in load.replace("'''", "")
    row = src[src.index("def logoRow("):src.index("def pickRow(")]
    assert "hasLevelColumn(cur)" in row
    # ...and neither has a bare COALESCE(level left in it
    for body in (load, row):
        for line in body.split("\n"):
            if "COALESCE(level" in line:
                assert "hasLevelColumn" in body, line

    class _Cur:
        """The live table as it is between the deploy and the scrape."""
        def __init__(self, has_level):
            self.has_level, self.out = has_level, []
            self.connection = type("C", (), {"rollback": lambda s: None})()
        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                self.out = [("school_logo",)]
            elif "information_schema" in sql:
                self.out = [(1,)] if self.has_level else []
            elif "school_logo" in sql:
                if "COALESCE(level" in sql and not self.has_level:
                    raise RuntimeError('column "level" does not exist')
                self.out = [("A", "MA", "p.png", "anet", "u", False, None, "")]
            else:
                self.out = []
        def fetchone(self): return self.out[0] if self.out else None
        def fetchall(self): return self.out

    import school_logo as SL
    SL._HAS_LEVEL.update({"at": 0.0, "ok": False})
    assert SL.hasLevelColumn(_Cur(False), True) is False     # no raise
    SL._HAS_LEVEL.update({"at": 0.0, "ok": False})
    assert SL.hasLevelColumn(_Cur(True), True) is True
    # and logoRow still answers on a table without the column
    SL._HAVE.update({"at": 0.0, "ok": True})
    SL._HAS_LEVEL.update({"at": 0.0, "ok": False})
    got = SL.logoRow(_Cur(False), "A", "MA")
    assert got is not None and got["level"] == ""
