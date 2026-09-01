"""
A school that brought five or fewer runners to the original meet must not come
back with a full seven in the prediction.

The two cap sites (meetField, which the page shows, and _teamRosters, which the
model scores) are exercised against fake DB helpers, so this needs no database.
"""
import os
import sys

# ! engine TOO, since schoolSquad started delegating to _currentSquads --
#   which reads season_year.academicYear to decide whether the current season
#   is over. Same two paths test_predict_carry_forward.py sets up.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import predict                                                  # noqa: E402


# ------------------------------------------------------------------ #
# the pure helpers
# ------------------------------------------------------------------ #

def test_cap_table():
    # 0 = not at the original meet (a manually named school) -> rulebook seven
    assert predict.squadCap(0) == 7
    for n in (1, 2, 3, 4, 5):
        assert predict.squadCap(n) == n, n
    for n in (6, 7, 8, 25):
        assert predict.squadCap(n) == 7, n
    print("  cap table 0->7, 1..5->n, 6+->7 ................... OK")


def test_counts_ignore_schoolless_rows():
    originals = [{"school": "Alpha"}, {"school": "Alpha"},
                 {"school": None}, {"school": ""}, {"school": "Beta"}]
    c = predict.countsBySchool(originals)
    assert c == {"Alpha": 2, "Beta": 1}, c
    print("  countsBySchool ignores schoolless rows ........... OK")


# ------------------------------------------------------------------ #
# the two real call sites, with the database faked out
# ------------------------------------------------------------------ #

def _person(pid, school, name=None):
    return {"person_id": pid, "name": name or f"R{pid}", "school": school}


def _install(monkey_originals, monkey_squads, last_seen=None, gender=None):
    """Point predict's DB helpers at fixtures. Returns an undo.

    last_seen mirrors _lastKnownRatings: {person_id: {rating, n_races, year}}.
    Default is empty, which is the "no rating on record" case.
    """
    saved = (predict._exactField, predict._currentSquads,
             predict._currentSeason, predict._lastKnownRatings,
             predict._fieldGender)
    predict._exactField = lambda cur, m, d, s: monkey_originals
    predict._currentSquads = lambda cur, schools, s, y, gender=None: {
        k: v for k, v in monkey_squads.items() if k in schools}
    predict._currentSeason = lambda cur, s: 2026
    predict._lastKnownRatings = lambda cur, ids, sport: last_seen or {}
    predict._fieldGender = lambda cur, ids, sport: gender

    def undo():
        (predict._exactField, predict._currentSquads,
         predict._currentSeason, predict._lastKnownRatings,
         predict._fieldGender) = saved
    return undo


# Big brought seven; Small brought two individuals; Mid brought six.
ORIGINALS = ([_person(i, "Big") for i in range(1, 8)]
             + [_person(101, "Small"), _person(102, "Small")]
             + [_person(i, "Mid") for i in range(200, 206)])

# every school has a deep current squad -- ten each
SQUADS = {
    "Big":   [{"person_id": 1000 + i, "name": f"B{i}"} for i in range(10)],
    "Small": [{"person_id": 2000 + i, "name": f"S{i}"} for i in range(10)],
    "Mid":   [{"person_id": 3000 + i, "name": f"M{i}"} for i in range(10)],
}


def test_meetField_caps_the_small_school():
    undo = _install(ORIGINALS, SQUADS)
    try:
        out = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                when="thisyear")
    finally:
        undo()
    teams = {t["school"]: t for t in out["teams"]}

    assert len(teams["Small"]["runners"]) == 2, teams["Small"]["runners"]
    assert len(teams["Big"]["runners"]) == 7
    assert len(teams["Mid"]["runners"]) == 7, "six is a team one short"

    # ! CAPPED, NOT DISCARDED -- the rest must remain hand-addable. Small's
    #   dropped list is the 8 squad members past the cap PLUS the 2 originals
    #   who have no current-season row (the graduated-or-injured path).
    dropped_ids = {d["person_id"] for d in teams["Small"]["dropped"]}
    assert {2002 + i for i in range(8)} <= dropped_ids, dropped_ids
    assert {101, 102} <= dropped_ids, dropped_ids
    assert len(teams["Small"]["dropped"]) == 10, teams["Small"]["dropped"]
    print(f"  meetField: Small {len(teams['Small']['runners'])}, "
          f"Mid {len(teams['Mid']['runners'])}, "
          f"Big {len(teams['Big']['runners'])} .............. OK")


def test_teamRosters_agrees_with_meetField():
    """If these disagree the page shows one lineup and the model scores another."""
    undo = _install(ORIGINALS, SQUADS)
    try:
        shown = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                  when="thisyear")
        scored = predict._teamRosters(
            None, schools=None,
            target={"mode": "rerun", "sport": "XC", "meet_id": 1})
    finally:
        undo()

    by_school_shown = {t["school"]: len(t["runners"]) for t in shown["teams"]}
    scored_counts = {}
    ids_to_school = {e["person_id"]: sch for sch, sq in SQUADS.items()
                     for e in sq}
    for e in scored:
        sch = ids_to_school[e["person_id"]]
        scored_counts[sch] = scored_counts.get(sch, 0) + 1

    assert scored_counts == {"Big": 7, "Mid": 7, "Small": 2}, scored_counts
    for sch, n in scored_counts.items():
        assert by_school_shown[sch] == n, (sch, by_school_shown[sch], n)
    print(f"  _teamRosters matches meetField: {scored_counts} ... OK")


def test_a_school_that_brought_one_gets_one():
    solo = [_person(1, "Solo")] + [_person(i, "Big") for i in range(10, 17)]
    undo = _install(solo, {**SQUADS, "Solo": [{"person_id": 9000 + i,
                                               "name": f"X{i}"}
                                              for i in range(10)]})
    try:
        out = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                when="thisyear")
    finally:
        undo()
    teams = {t["school"]: t for t in out["teams"]}
    assert len(teams["Solo"]["runners"]) == 1, teams["Solo"]["runners"]
    print("  one individual stays one, not seven ............... OK")


# ------------------------------------------------------------------ #
# The dropped list carries a last-known rating (owner, 2026-09-01)
# ------------------------------------------------------------------ #

class RatingCursor:
    """Answers _lastKnownRatings; records the ids it was asked for."""
    def __init__(self, ratings):
        self.ratings = ratings
        self.asked = None

    def execute(self, sql, params=None):
        self.asked = sorted((params or {}).get("ids") or [])

    def fetchall(self):
        return [{"person_id": p, "mean_rating": r[0], "n_races": r[1],
                 "year": r[2]}
                for p, r in sorted(self.ratings.items())]


def test_last_known_ratings_shape():
    cur = RatingCursor({101: (128.4, 9, 2025), 102: (117.44, 6, 2024)})
    out = predict._lastKnownRatings(cur, [102, 101, None, 101], "XC")
    assert cur.asked == [101, 102], cur.asked      # deduped, sorted, no None
    assert out[101] == {"rating": 128.4, "n_races": 9, "year": 2025}
    # ! ONE DECIMAL, the same round() _squadsForYear already applies, so a
    #   dropped runner and an active one are formatted alike.
    assert out[102]["rating"] == 117.4, out[102]
    print(f"  _lastKnownRatings: {out[101]} ....... OK")


def test_empty_ids_do_not_query():
    cur = RatingCursor({})
    assert predict._lastKnownRatings(cur, [], "XC") == {}
    assert predict._lastKnownRatings(cur, [None], "XC") == {}
    assert cur.asked is None, "queried the database for nothing"
    print("  no ids -> no query ................................ OK")


def test_dropped_runners_carry_their_last_rating():
    """The graduated/injured group had rating hardcoded to None."""
    undo = _install(ORIGINALS, SQUADS, last_seen={
        101: {"rating": 128.4, "n_races": 9, "year": 2025}})
    try:
        out = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                when="thisyear")
    finally:
        undo()

    dropped = {d["person_id"]: d
               for t in out["teams"] for d in t["dropped"]}
    assert dropped[101]["rating"] == 128.4, dropped[101]
    assert dropped[101]["rating_year"] == 2025, dropped[101]
    # ! AND ABSENCE STAYS ABSENCE -- someone with no rating anywhere must
    #   render blank, not zero.
    assert dropped[102]["rating"] is None, dropped[102]
    assert dropped[102]["rating_year"] is None, dropped[102]
    print("  dropped carry rating 128.4 from 2025; unknown stays None . OK")


# ------------------------------------------------------------------ #
# Gender: a boys race must not offer girls (owner, 2026-09-01)
# ------------------------------------------------------------------ #

class GenderCursor:
    """Answers _fieldGender from a {pool_letter: count} fixture."""
    def __init__(self, counts):
        self.counts = counts
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return [{"g": g, "n": n} for g, n in
                sorted(self.counts.items(), key=lambda kv: -kv[1])]


def test_a_single_gender_field_is_named():
    for counts, want in (({"M": 120}, "M"), ({"F": 90}, "F")):
        assert predict._fieldGender(GenderCursor(counts), [1, 2], "XC") == want
    print("  a clean single-gender field is named ............... OK")


def test_a_few_mispooled_rows_do_not_break_it():
    """Issue #52 says some athlete_season rows carry the wrong pool. Demanding
    unanimity would return None for a real boys race and re-open the leak."""
    got = predict._fieldGender(GenderCursor({"M": 118, "F": 2}), [1], "XC")
    assert got == "M", got
    print("  118 M + 2 mispooled F still reads as M ............. OK")


def test_a_genuinely_mixed_field_is_not_forced():
    """All-races at a meet with both: there is no one right answer, and
    filtering to either would delete half the field."""
    assert predict._fieldGender(GenderCursor({"M": 60, "F": 55}), [1],
                                "XC") is None
    assert predict._fieldGender(GenderCursor({}), [1], "XC") is None
    assert predict._fieldGender(GenderCursor({"M": 5}), [], "XC") is None
    print("  an even field, an empty one, and no ids -> None .... OK")


def test_squad_queries_filter_on_the_pool_letter():
    cur = FakeCursorSQL()
    predict._squadsForYear(cur, ["Alpha"], "XC", 2025, gender="F")
    assert "upper(right(s.pool, 1)) = %(gender)s" in cur.sql, cur.sql
    assert cur.params["gender"] == "F"

    plain = FakeCursorSQL()
    predict._squadsForYear(plain, ["Alpha"], "XC", 2025)
    assert "right(s.pool" not in plain.sql, plain.sql
    print("  _squadsForYear filters on pool only when asked ..... OK")


def test_schoolSquad_filters_too():
    """The gender filter survives the delegation to _currentSquads.

    ⚠ schoolSquad NO LONGER HAS A QUERY OF ITS OWN (2026-09-01). It was a
      second implementation of "who runs for this school now", with a hard
      `year = current` and none of the carry-forward the field uses -- so all
      preseason the meet's own teams came back full and adding any team
      reported it empty. What this test still has to prove is that routing
      through _currentSquads did not drop the gender on the way.
    """
    cur = FakeCursorSQL()
    predict.schoolSquad(cur, "Alpha", "XC", season_year=2026, gender="M")

    first_sql, first_params = cur.queries[0]
    assert "upper(right(s.pool, 1)) = %(gender)s" in first_sql, first_sql
    assert first_params["gender"] == "M"
    # and it really is the shared path, not a copy that happens to match
    assert first_params.get("schools") == ["Alpha"], first_params

    # ★ AND THE EMPTY ANSWER IS EXPLAINED. This fake returns no rows, so the
    #   other-gender count fires -- which is the whole point of it: a boys
    #   race correctly finds nobody at an all-girls school, and "has not
    #   raced this season" was a false explanation of a true result.
    # FOUR, not two: _currentSquads reads the current season and then falls
    # back to the previous one when a school has no rows (#82), and the
    # other-gender count repeats that pair without the filter.
    gendered = ["upper(right(s.pool, 1))" in q for q, _ in cur.queries]
    assert gendered == [True, True, False, False], (gendered,
                                                    len(cur.queries))
    print("  schoolSquad filters on pool ........................ OK")
    print("  and counts the other side when it finds nobody ..... OK")


class FakeCursorSQL:
    """Records EVERY statement, not just the last.

    ⚠ IT USED TO KEEP ONLY THE LAST, and that silently changed what the
      schoolSquad test asserted the moment schoolSquad grew a second query
      (the other-gender count). The assertion moved from the gendered lookup
      to the ungendered fallback without anyone touching it.
    """
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params or {}))

    # The last statement, for the callers that only ever make one.
    @property
    def sql(self):
        return self.queries[-1][0] if self.queries else None

    @property
    def params(self):
        return self.queries[-1][1] if self.queries else {}

    def fetchall(self):
        return []


# ------------------------------------------------------------------ #
# A rating is POOL-RELATIVE, so the top seven cannot be picked by comparing
# raw ratings across pools (owner, 2026-09-01).
# ------------------------------------------------------------------ #

def _row(pid, pool, rating):
    return {"person_id": pid, "school": "K12", "pool": pool,
            "name": f"R{pid}", "rating": rating, "n_races": 5}


def test_one_pool_is_left_exactly_as_it_came():
    """The common case must not change at all -- no factor fetched, no
    reordering, nothing to go wrong."""
    rows = [_row(1, "hs_m", 130), _row(2, "hs_m", 120), _row(3, "hs_m", 110)]
    out = predict._bestFirst(rows, "XC")
    assert out is rows, "the very list, not a copy -- nothing was done to it"
    print("  a single-pool squad is untouched .................. OK")


def _stubPoolView(factor_of):
    """Put a fake `pool_view` in sys.modules and return an undo.

    ⚠ IMPORTING THE REAL ONE PULLS IN THE DATABASE, and every test in this
      file is deliberately DB-free. _bestFirst imports repFactor lazily and
      only when a school actually spans pools, so a stub module is enough --
      and it keeps the test honest about which factor was asked for.
    """
    import sys
    import types
    saved = sys.modules.get("pool_view")
    mod = types.ModuleType("pool_view")
    mod.repFactor = lambda pool, sport: factor_of(pool)
    sys.modules["pool_view"] = mod

    def undo():
        if saved is None:
            sys.modules.pop("pool_view", None)
        else:
            sys.modules["pool_view"] = saved
    return undo


def test_a_cross_pool_squad_is_compared_on_one_scale():
    """An ms_m 130 is not an hs_m 130. Sorting on the raw number puts the
    school's best eighth-graders above its varsity, and squadCap then takes
    seven of them."""
    # An ms rating is worth much less on the HS scale; hs IS the scale.
    undo = _stubPoolView(lambda p: {"ms_m": 0.62, "hs_m": 1.0}[p])
    try:
        rows = [_row(1, "ms_m", 130), _row(2, "hs_m", 110),
                _row(3, "ms_m", 125), _row(4, "hs_m", 95)]
        out = predict._bestFirst(rows, "XC")
    finally:
        undo()

    # 130*0.62 = 80.6, 125*0.62 = 77.5 -- both below the HS runners.
    assert [r["person_id"] for r in out] == [2, 4, 1, 3], \
        [(r["person_id"], r["pool"], r["rating"]) for r in out]
    # ⚠ THE DISPLAYED NUMBER IS UNCHANGED. Only the order moved; a runner's
    #   rating is still their own pool's, as everywhere else on the site.
    assert out[2]["rating"] == 130, out[2]
    print("  a cross-pool squad is ordered on one scale ........ OK")


def test_a_missing_factor_leaves_the_order_alone():
    """hsFactor returns None when it cannot compute one, and its docstring
    says callers must never read that as 1.0. Converting part of a list and
    not the rest is worse than converting none of it."""
    undo = _stubPoolView(lambda p: None if p == "ms_m" else 1.0)
    try:
        rows = [_row(1, "ms_m", 130), _row(2, "hs_m", 110)]
        out = predict._bestFirst(rows, "XC")
    finally:
        undo()
    assert out is rows, "no factor, no reordering"
    print("  an unavailable factor changes nothing ............. OK")


def test_a_missing_pool_is_not_treated_as_comparable():
    undo = _stubPoolView(lambda p: 1.0)
    try:
        rows = [_row(1, None, 130), _row(2, "hs_m", 110)]
        out = predict._bestFirst(rows, "XC")
    finally:
        undo()
    assert out is rows, "a row with no pool cannot be converted"
    print("  a row with no pool stops the conversion ........... OK")


if __name__ == "__main__":
    for fn in [test_cap_table,
               test_counts_ignore_schoolless_rows,
               test_meetField_caps_the_small_school,
               test_teamRosters_agrees_with_meetField,
               test_a_school_that_brought_one_gets_one,
               test_last_known_ratings_shape,
               test_empty_ids_do_not_query,
               test_dropped_runners_carry_their_last_rating,
               test_a_single_gender_field_is_named,
               test_a_few_mispooled_rows_do_not_break_it,
               test_a_genuinely_mixed_field_is_not_forced,
               test_squad_queries_filter_on_the_pool_letter,
               test_schoolSquad_filters_too,
               test_one_pool_is_left_exactly_as_it_came,
               test_a_cross_pool_squad_is_compared_on_one_scale,
               test_a_missing_factor_leaves_the_order_alone,
               test_a_missing_pool_is_not_treated_as_comparable]:
        fn()
    print("\nall squad-cap tests passed")
