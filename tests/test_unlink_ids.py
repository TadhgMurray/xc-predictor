"""unlink.py numbers split people above every minted range (2026-09-29): max()+1
fell inside the DirectAthletics mint range, where a later mint could reuse it."""
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def test_split_base_is_above_every_mint():
    import link_tfrrs_rows as L
    src = open(os.path.join(_ROOT, "engine", "unlink.py")).read()
    base = int(re.search(r"SPLIT_BASE = ([0-9_]+)", src).group(1).replace("_", ""))
    top = max(L.MINT_BASE.values()) + L.MINT_MAX_NATIVE
    assert base >= top, (base, top)
    assert "WHERE person_id >= %(b)s" in src and "max(int(cur.fetchone()[0]) + 1, SPLIT_BASE)" in src


def _season(season, cls, school, n, name):
    return (season, cls, None if cls is None else "x", school, n, name)


def test_class_year_only_from_school_rows():
    import unlink
    q = unlink.classYearsSql()
    assert "unattached" in q and "{SCHOOL_SHAPED}" not in q and "{ONLY}" not in q
    assert "IN (SELECT person_id FROM rj_ids)" in unlink.classYearsSql("rj_ids")


def test_rejoin_pairs():
    import unlink
    # Jack Falkowski (owner, 2026-10-12): school seasons class of 2025; the
    # unattached season now carries no class year -> one person, rejoined
    hs = [_season(y, 2025, "Christian Brothers Academy", 6, "Jack Falkowski")
          for y in (2022, 2023, 2024, 2025)]
    un = [_season(2026, None, "Unattached", 4, "Jack Falkowski")]
    got = unlink.rejoinPairs({10: hs, 20: un}, [(10, 20, [2026])])
    assert got == [(10, 20, [2026])]
    # a different name on the split side -> left apart
    other = [_season(2026, None, "Unattached", 4, "Brett Smith")]
    assert unlink.rejoinPairs({10: hs, 20: other}, [(10, 20, [2026])]) == []
    # a school-graded split the class years still separate -> left apart
    kid = [_season(2026, 2031, "Some Middle School", 4, "Jack Falkowski")]
    assert unlink.rejoinPairs({10: hs, 20: kid}, [(10, 20, [2026])]) == []
    # one side gone -> nothing
    assert unlink.rejoinPairs({10: hs}, [(10, 20, [2026])]) == []
