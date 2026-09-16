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
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import predict                                                  # noqa: E402


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


class LevelCursor:
    """Answers _fieldLevels' one query from a {level: count} fixture."""

    def __init__(self, counts):
        self.counts = counts

    def execute(self, sql, params=None):
        assert "athlete_season" in sql, sql

    def fetchall(self):
        return [{"lvl": k, "n": v} for k, v in self.counts.items()]


def test_only_a_clear_majority_level_counts():
    """⚠ 10% WAS FAR TOO LENIENT (owner, 2026-09-16: "make it more than 10%,
    maybe more like 50-70%"). A college championship with 12% mis-pooled rows
    passed BOTH levels at 0.10, and a filter that keeps both filters nothing
    -- while the middle schoolers the threshold exists to remove are exactly
    the rows in that 12%.
    """
    assert 0.5 <= predict._LEVEL_MIN_SHARE <= 0.7, predict._LEVEL_MIN_SHARE

    def levels(counts):
        return predict._fieldLevels(LevelCursor(counts), [1, 2], "XC")

    # the real case: a college field with a slice of mis-pooled rows
    assert levels({"college": 880, "ms": 120}) == {"college"}
    # and at 0.10 that returned {"college", "ms"} -- the regression
    assert "ms" not in levels({"college": 880, "ms": 120})
    # a genuinely mixed open meet narrows to NOTHING, which is the right
    # answer: there is no single level to pick.
    assert levels({"college": 520, "hs": 480}) == set()
    # a clean high school race is still filtered
    assert levels({"hs": 1000}) == {"hs"}
    # no rows at all -> no filter, never a crash
    assert levels({}) == set()


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
    assert "_fieldLevels(" in tr, tr
    # ! AND IT VOTES ON THE LINEUP, not on the whole entry list. See
    #   test_the_level_is_read_off_the_lineup_not_the_archive.
    assert "_lineupIds(originals)" in tr, tr


class SeasonCursor:
    """Answers _fieldLevels' query from {person_id: [(year, pool), ...]},
    applying the DISTINCT ON / ORDER BY the real query asks for."""

    def __init__(self, career):
        self.career = career
        self.rows = []

    def execute(self, sql, params=None):
        assert "DISTINCT ON (s.person_id)" in sql, sql
        assert "ORDER  BY s.person_id, s.year DESC" in sql, sql
        yr = (params or {}).get("yr")
        ids = set((params or {}).get("ids") or [])
        counts = {}
        for pid, seasons in self.career.items():
            if pid not in ids:
                continue
            got = [s for s in seasons if yr is None or s[0] <= yr]
            if not got:
                continue
            # newest season wins -- one row per athlete
            pool = max(got)[1]
            lvl = pool.split("|")[0].split("_")[0]
            counts[lvl] = counts.get(lvl, 0) + 1
        self.rows = [{"lvl": k, "n": v} for k, v in counts.items()]

    def fetchall(self):
        return self.rows


def test_the_level_is_read_off_the_lineup_not_the_archive():
    """⚠ THE BUG THAT PUT SEVENTH GRADERS IN A D3 CHAMPIONSHIP (owner,
    2026-09-16: "it's bcs when you expand it it adds them all, and most are
    hsers, so it's able to pass the 60%").

    _fieldLevels counted EVERY athlete_season row over every year. A D3
    sophomore carries four hs_m rows and one college_m row, so a field of
    nothing but college runners came back about 80% `hs` -- and at 60% that
    does not merely fail to filter, it filters to the WRONG level. Every
    shared name then resolved to its high school.

    Two fixes, and the test needs both: ONE ROW PER ATHLETE (their most
    recent), and the vote taken over the LINEUP rather than the entry list.
    """
    # twelve college sophomores, each with four years of high school behind
    # them -- the archive is 48 hs rows against 12 college ones
    career = {}
    for pid in range(1, 13):
        career[pid] = [(2022, "hs_m"), (2023, "hs_m"), (2024, "hs_m"),
                       (2025, "hs_m"), (2026, "college_m")]
    got = predict._fieldLevels(SeasonCursor(career), list(career), "XC", 2026)
    assert got == {"college"}, got

    # ...and running the 2024 edition of the meet reads 2024's levels
    got = predict._fieldLevels(SeasonCursor(career), list(career), "XC", 2024)
    assert got == {"hs"}, got


def test_the_vote_is_seven_per_school():
    """Owner: "the 60% shoild be for current top 7 me thinks". One school
    entering forty in an open race must not outvote twenty schools of seven.
    """
    rows = ([{"person_id": i, "school": "Big"} for i in range(1, 41)]
            + [{"person_id": 100 + i, "school": "Small"} for i in range(7)])
    ids = predict._lineupIds(rows)
    assert len([i for i in ids if i < 100]) == predict.MAX_PER_TEAM, ids
    assert len(ids) == 2 * predict.MAX_PER_TEAM, ids
    # an unattached runner has no school and is never capped away
    ids = predict._lineupIds([{"person_id": 1, "school": None},
                              {"person_id": 2, "school": ""}])
    assert ids == [1, 2], ids


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
