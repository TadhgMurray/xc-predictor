"""A school NAME is not a school.

    python -m pytest -q tests/test_school_level_filter.py

Owner, 2026-09-16: "when you press add entire roster it adds the entire
roster, including ppl not at that school just at a school with same name...
for rn it should just only include ppl who are in the same pool (so in this
case college)."

★ athlete_season KEYS ON THE BARE NAME. Amherst (MA) is Amherst College AND
  Amherst Regional; Manchester, Knox, Carthage and Houghton are each a
  college and a high school. So "everyone racing for Amherst" is two
  different teams, and at the D3 championships that put middle schoolers in
  a college championship -- and let them win it, because a 13-year-old's
  normalized time flatters them against an 8K field.

! THE LEVEL IS IN THE POOL, which every row already carries: hs_m, ms_f,
  college_m. school_identity has a richer notion of this and splitting a
  K-12 properly is the real fix; the pool is the part that is ON the row
  being filtered, which is what a filter needs.

⚠ AND IT IS READ OFF THE RACE, NOT ASSUMED. A college championship comes
  back {"college"}; a genuinely mixed meet comes back with several and
  narrows to none of them, because there is no one answer to narrow to.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def body(src, name):
    i = src.index(f"def {name}(")
    return src[i:src.index("\ndef ", i + 10)]


def test_the_level_comes_off_the_field_like_the_gender_does():
    pred = read("racecast", "predict.py")
    fn = body(pred, "_fieldLevels")
    assert "split_part(split_part(s.pool, '|', 1), '_', 1)" in fn, fn
    # ⚠ A SUPERMAJORITY, NOT PURITY. Issue #52 says some athlete_season rows
    #   carry the wrong pool, so demanding one level exactly would hand back
    #   every level and filter nothing -- the same trap _fieldGender avoids.
    assert "_LEVEL_MIN_SHARE" in fn, fn
    assert "_LEVEL_MIN_SHARE = 0.10" in pred


def test_every_squad_path_can_narrow_by_level():
    pred = read("racecast", "predict.py")
    for fn in ("_squadsForYear", "_currentSquads", "schoolSquad"):
        assert "levels" in body(pred, fn), fn
    # the clause itself, and it must be bound rather than interpolated
    sq = body(pred, "_squadsForYear")
    assert "= ANY(%(levels)s)" in sq, sq
    assert '"levels": sorted(levels) if levels else None' in sq, sq


def test_the_field_derives_it_and_hands_it_back():
    """! THE PAGE NEEDS IT TOO. Adding a team by hand has no race to read a
    level off, so the field it is being added to lends its own."""
    pred = read("racecast", "predict.py")
    mf = body(pred, "meetField")
    assert "_fieldLevels(cur" in mf, mf
    assert '"levels": sorted(levels)' in mf, mf
    # and the prediction path derives the same thing, for a request that
    # sends no explicit field
    tr = body(pred, "_teamRosters")
    assert "_fieldLevels(cur, ids, sport)" in tr, tr


def test_the_squad_endpoint_takes_it_and_validates_it():
    app = read("racecast", "app.py")
    i = app.index('@app.route("/api/predict/squad"')
    route = app[i:app.index("@app.route(", i + 10)]
    assert '_LEVELS = {"hs", "ms", "college", "elem", "pro"}' in route, route
    # ! A WHITELIST, not whatever the caller sent: it reaches a SQL ANY()
    assert "if v in _LEVELS" in route, route
    assert "levels=levels" in route, route


def test_the_page_sends_the_level_with_every_squad_fetch():
    js = re.sub(r"/\*.*?\*/", "",
                read("racecast", "static", "predictions.js"), flags=re.S)
    # both of them: "add from squad" and "add the whole squad"
    assert js.count('set("levels"') == 2, js.count('set("levels"')
    assert js.count("state.field && state.field.levels") == 2
