"""An anet meet stored under the wrong year goes back to its own year (the
first level_conflict server run, 2026-09-29: meet 227716, a 2023 Middlesex
League race dated 2025-10-26, put four runners' own high school race on their
college seasons).

    python -m pytest -q tests/test_meet_date_fix.py

The detector is the athletes' grades (the id neighbourhood failed on the
real corpus: anet ids are not chronological). The arithmetic is pure and
tested here; the vote, the record, the move and the undo run on a real
Postgres when XCP_TWIN_TEST_DSN names one.
"""
import io
import math
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import meet_date_fix as MD                                       # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


# ---- the arithmetic ------------------------------------------------------ #

def test_the_binomial_tail():
    assert math.isclose(MD.binomTail(3, 0.5, 2), 0.5)
    assert math.isclose(MD.binomTail(4, 0.1, 3), 4 * 0.001 * 0.9 + 0.0001)
    assert MD.binomTail(5, 0.2, 0) == 1.0 and MD.binomTail(5, 0.2, 6) == 0.0
    assert MD.binomTail(5, 0.0, 1) == 0.0
    big = MD.binomTail(5000, 0.03, 2501)
    assert 0.0 <= big < 1e-300, "a meet of thousands neither overflows nor hangs"
    assert math.isclose(MD.binomTail(2001, 0.5, 1001), 0.5), "symmetric: half above the middle"


def test_the_noise_is_voters_off_their_own_meets_winner():
    tallies = [{0: 9, 1: 1},              # one voter a year off
               {-2: 10},                  # a wrong year: every voter together, no noise
               {0: 5},
               {0: 1},                    # a lone voter cannot disagree
               {0: 2, 1: 2}]              # no winner to be off from
    noise, seen = MD.noiseRates(tallies)
    assert seen == 10 + 10 + 5
    assert noise == {1: 1 / 25}


def test_the_minimum_is_where_the_corpus_expects_under_one_false_fix():
    noise = {1: 0.04}
    hist = {1: 40, 16: 60, 6: 5}
    n_min, expected = MD.minVoters(hist, noise)
    assert n_min == 2, "40 lone voters at 4% expect 1.6 false fixes; the rest almost none"
    assert expected < 1
    # brute force: the smallest n0 whose meets at or above it expect under one
    for n0 in range(1, 20):
        e = sum(m * MD.falseFix(n, noise) for n, m in hist.items() if n >= n0)
        if e < 1:
            assert n0 == n_min
            break
    # noisier grades ask for more voters; no noise asks for one
    assert MD.minVoters({1: 1000, 3: 1000, 5: 1000, 9: 1000}, {1: .1, -1: .1})[0] > 5
    assert MD.minVoters({1: 1000}, {})[0] == 1


def test_the_verdict():
    v = MD.vote({-2: 7, 0: 1}, 3)
    assert v["fix"] and (v["k"], v["for_fixed"], v["for_stored"], v["voters"]) == (-2, 7, 1, 8)
    v = MD.vote({0: 5}, 3)
    assert not v["fix"] and v["k"] == 0 and "stored year" in v["why"]
    v = MD.vote({0: 4, -1: 4}, 3)
    assert not v["fix"] and v["k"] is None and "split" in v["why"]
    v = MD.vote({-2: 2, 0: 2, -1: 1}, 3)
    assert not v["fix"] and "split" in v["why"], "a plurality is not a majority"
    v = MD.vote({-2: 2}, 3)
    assert not v["fix"] and v["k"] == -2 and "too few" in v["why"]
    v = MD.vote({}, 3)
    assert not v["fix"] and "no evidence" in v["why"]
    # the bar: a wrong year is as unanimous as a right one
    v = MD.vote({-2: 6, 0: 4}, 3, bar=0.9)
    assert not v["fix"] and v["k"] == -2 and "a mix" in v["why"]
    assert MD.vote({-2: 9, 0: 1}, 3, bar=0.9)["fix"]


def test_the_bar_is_what_right_year_meets_achieve():
    tallies = [{0: 15, 1: 1}] * 40 + [{0: 16}] * 59 + [{0: 1}] * 50 + [{-2: 8}]
    assert MD.measuredBar(tallies, 2) == 15 / 16, \
        "p1 over the meets backing their stored season; lone voters left out"
    assert MD.measuredBar([{0: 16}] * 199 + [{0: 9, 1: 7}], 2) == 1.0, \
        "one meet in two hundred below the rest is inside the 1 in 100 the bar lets go"
    assert MD.measuredBar([{-2: 8}], 2) is None


def test_a_one_year_vote_that_is_the_months_norm_is_a_convention():
    by_month = {9: 20, 10: 24, 11: 20, 7: 3}
    one_year = {(7, 1): 3, (10, -1): 1}
    conv = MD.conventions(by_month, one_year)
    assert set(conv) == {(7, 1)}, "every July meet a year ahead: the season seam"
    assert conv[(7, 1)][:2] == (3, 3)
    # a month that is mostly one-year votes is flagged even when it is big
    assert (10, 1) in MD.conventions({9: 500, 10: 500, 11: 500}, {(10, 1): 300})


def test_the_day_moves_by_whole_years_and_the_guards():
    assert MD.shiftDay("2025-10-26", -2) == "2023-10-26"
    assert MD.shiftDay("2024-02-29", -1) == "2023-02-28", "as Postgres does"
    assert MD.seasonOf("2025-10-26") == 2025 and MD.seasonOf("2026-04-01") == 2025
    r = MD.finish(dict(k=-2, first="2025-10-26", last="2025-10-26", held=None),
                  "2026-09-29")
    assert (r["fixed"], r["fixed_last"], r["held"]) == ("2023-10-26", "2023-10-26", None)
    r = MD.finish(dict(k=1, first="2026-03-01", last="2026-03-01", held=None),
                  "2026-09-29")
    assert "future" in r["held"]
    r = MD.finish(dict(k=-1, first="2024-07-30", last="2024-08-02", held=None),
                  "2026-09-29")
    assert "two seasons" in r["held"]


# ---- the wiring ----------------------------------------------------------- #

def test_it_runs_first_and_is_not_always_run():
    sh = read("deploy", "run_pipeline.sh")
    assert 'step 00_meet_dates    "$PY" -u engine/meet_date_fix.py --write' in sh
    assert sh.index("step 00_meet_dates") < sh.index("step 01_season_year") \
        < sh.index("step 04c_twins")
    always = [l for l in sh.splitlines() if l.startswith("_ALWAYS=")][0]
    assert "00_meet_dates" not in always, "a --from run keeps last run's dates"


def test_the_id_neighbourhood_is_gone():
    src = read("engine", "meet_date_fix.py")
    assert "def neighbourDays" not in src and "def survey" not in src


def test_only_anet_rows_inside_the_stored_days_move():
    sql = MD._moveSql("results", "date", "AND f.status = 'applied'",
                      "stored_first", "stored_last", "f.years",
                      "AND t.source = 'anet'")
    assert "make_interval(years => f.years)" in sql
    assert "BETWEEN f.stored_first AND f.stored_last" in sql, "idempotent"
    assert "AND t.source = 'anet'" in sql, "a tfrrs row with the same id is not this meet"
    assert "|| substr(t.date::text, 11)" in sql
    as_date = MD._moveSql("meets", "meet_date", "", "a", "b", "1", "", True)
    assert "::date" in as_date and "to_char" not in as_date


# ---- on a real Postgres, when one is offered ------------------------------ #
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=<yours>"

def _pg():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(dsn)


FALL = ((9, 9), (9, 23), (10, 7), (10, 21), (11, 4), (11, 11))
MIDDLESEX, WRONG_BY_ONE, SPLIT, TOO_FEW, SUNFAIR = 227716, 7200, 7000, 7100, 244560
MIX = 7400
JULY = (7301, 7302, 7303)
TF_WRONG = 90999


def _load(cur):
    """A small corpus shaped like anet's.

      background   seasons 2016-2025, six autumn meets each (ids NOT in date
                   order), four runners of every class 2017-2029 racing all
                   four of their high school seasons; one grade typed a year
                   high at 40 of the 60 meets (the noise), and 40 small meets
                   with one graded runner each (so a lone voter is not enough)
      227716       the review's meet: 2025-10-26, its runners graded as in
                   the autumn of 2023 -> FIX, k = -2
      7200         2025-10-04, graded as in 2024 -> FIX, k = -1 (a one-year
                   error in October is no convention)
      7301-7303    July meets graded for the coming school year -> k = +1
                   every one: the season seam, held
      7000         half its voters say 2024, half 2023 -> split, stays
      7400         six say 2023, four the stored 2025 -> a 60% majority under
                   the bar a right-year meet reaches (15/16): a mix, stays
      7100         one voter saying two years back -> too few, stays
      244558-60    the Sunfair Invitational of 1999 and its season, uploaded
                   late with ids above every 2025 meet -> the grades agree
                   with the stored 1999, stays
      TF           each runner's spring meet, and 90999: 2026-04-18 graded
                   as in the spring of 2024 -> FIX, k = -2
    """
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, meets, meets_tf_meta, meet_date_fix;
        CREATE TABLE results (result_id bigint, person_id bigint, source text,
          meet_id bigint, date text, grade text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text, meet_date text);
        CREATE TABLE meets_tf_meta (meet_id bigint, meet_name text, meet_date text,
          source text);""")
    xc, tf, mt, tfm = [], [], [], []
    rid = [0]

    def row(out, pid, mid, day, grade, source="anet"):
        rid[0] += 1
        out.append((rid[0], pid, source, mid, day, grade))

    def meet(mid, day, name=None, ungraded=3):
        mt.append((mid, mid, name or f"Meet {mid}", day))
        for _ in range(ungraded):
            row(xc, None, mid, day, None)

    def pid(c, i):
        return c * 100 + i

    j = 0
    for s in range(2025, 2015, -1):                        # ids against the calendar
        for mon, dd in FALL:
            mid, day = 240000 + j, f"{s}-{mon:02d}-{dd:02d}"
            meet(mid, day)
            for c in range(s + 1, s + 5):
                for i in range(4):
                    g = s + 13 - c
                    if j < 40 and c == s + 2 and i == j % 4:
                        g += 1                             # the noise
                    row(xc, pid(c, i), mid, day, str(g))
            # the lone voter at a small meet the same season
            if j < 40:
                lone, lday = 6000 + j, f"{s}-10-15"
                meet(lone, lday)
                row(xc, pid(s + 1, j % 4), lone, lday, "12")
            j += 1
        # each runner's spring track meet
        tday = f"{s + 1}-04-15"
        tfm.append((90000 + s, f"Spring Relays {s + 1}", tday, "anet"))
        for c in range(s + 1, s + 5):
            for i in range(4):
                row(tf, pid(c, i), 90000 + s, tday, str(s + 13 - c))

    # the review's meet: the Middlesex League's juniors and seniors of 2023
    meet(MIDDLESEX, "2025-10-26", "Middlesex League Championship")
    for c in (2024, 2025):
        for i in range(4):
            row(xc, pid(c, i), MIDDLESEX, "2025-10-26", str(2023 + 13 - c))
    row(xc, 777, MIDDLESEX, "2025-10-26", "SO-2", "tfrrs")            # not anet
    # a year off, in October
    meet(WRONG_BY_ONE, "2025-10-04", "Columbus Day Invitational")
    for c in range(2025, 2029):
        for i in range(4):
            row(xc, pid(c, i), WRONG_BY_ONE, "2025-10-04", str(2024 + 13 - c))
    # July meets graded for the coming school year
    for mid, y in zip(JULY, (2022, 2023, 2024)):
        day = f"{y}-07-15"
        meet(mid, day, f"Summer Series {y}")
        for c in range(y + 1, y + 4):
            for i in range(2):
                row(xc, pid(c, i), mid, day, str(y + 13 - c))
    # a split vote: four say 2024, four say 2023
    meet(SPLIT, "2024-10-12", "Split Decision XC")
    for c in (2025, 2026, 2027, 2028):
        row(xc, pid(c, 0), SPLIT, "2024-10-12", str(2024 + 13 - c))
    for c, i in ((2025, 1), (2026, 1), (2027, 1), (2025, 2)):
        row(xc, pid(c, i), SPLIT, "2024-10-12", str(2024 + 13 - c - 1))
    # a mix: six graded as in 2023, four as in 2025 -- a majority, not a year
    meet(MIX, "2025-10-11", "Two Meets One Id")
    for c, i in ((2024, 0), (2024, 1), (2024, 2), (2025, 0), (2025, 1), (2025, 2)):
        row(xc, pid(c, i), MIX, "2025-10-11", str(2023 + 13 - c))
    for c in (2026, 2027, 2028, 2029):
        row(xc, pid(c, 1), MIX, "2025-10-11", str(2025 + 13 - c))
    # one voter, two years back
    meet(TOO_FEW, "2024-10-19", "Tiny Dual")
    row(xc, pid(2026, 3), TOO_FEW, "2024-10-19", "9")
    # the Sunfair Invitational of 1999, and its season, uploaded late
    for mid, day, name in ((244558, "1998-10-17", "Sunfair Invitational 1998"),
                           (244559, "1999-09-25", "Valley Opener 1999"),
                           (SUNFAIR, "1999-10-16", "Sunfair Invitational")):
        meet(mid, day, name)
        s = int(day[:4])
        for c in (2000, 2001):
            for i in range(3):
                row(xc, 2000 * 100 + (c - 2000) * 10 + i, mid, day, str(s + 13 - c))
    # track: a spring meet under 2026, graded as the spring of 2024
    tfm.append((TF_WRONG, "Wrong Year Relays", "2026-04-18", "anet"))
    for c in range(2024, 2028):
        for i in range(2):
            row(tf, pid(c, i), TF_WRONG, "2026-04-18", str(2023 + 13 - c))
    cur.executemany("INSERT INTO results VALUES (%s, %s, %s, %s, %s, %s)", xc)
    cur.executemany("INSERT INTO results_tf VALUES (%s, %s, %s, %s, %s, %s)", tf)
    cur.executemany("INSERT INTO meets VALUES (%s, %s, %s, %s)", mt)
    cur.executemany("INSERT INTO meets_tf_meta VALUES (%s, %s, %s, %s)", tfm)


def _dates(cur, meet_id, source="anet", table="results"):
    cur.execute(f"SELECT DISTINCT date FROM {table} WHERE meet_id = %s AND source = %s",
                (meet_id, source))
    return sorted(r[0] for r in cur.fetchall())


def _one(cur, sport, meet_id):
    found = MD.examine(cur, (sport,), meet=meet_id)
    rows = [r for r in found[sport][1] if r["meet_id"] == meet_id]
    return rows[0] if rows else None


def test_the_grade_vote_on_postgres(capsys):
    conn = _pg()
    try:
        cur = conn.cursor()
        _load(cur)
        found = MD.examine(cur)
        shape, rows = found["XC"]
        assert shape["n_min"] == 2, "40 lone voters at the corpus's noise are not enough"
        assert set(shape["noise"]) == {1, 2}, "the typos a year high; the mix's minority"
        assert shape["few"] == 1, "meet 7100, one voter"
        assert shape["bar"] == 15 / 16, "a background meet with one typo"
        assert found["TF"][0]["bar"] == 1.0
        by = {r["meet_id"]: r for r in rows}
        assert set(by) == {MIDDLESEX, WRONG_BY_ONE, *JULY}

        # 227716: stored in 2025, its runners' grades say 2023
        m = by[MIDDLESEX]
        assert m["apply"] and m["k"] == -2
        assert (m["first"], m["fixed"], m["fixed_last"]) == \
            ("2025-10-26", "2023-10-26", "2023-10-26")
        assert (m["for_fixed"], m["for_stored"], m["voters"]) == (8, 0, 8)
        assert m["n"] == 11, "every anet row moves, graded or not; not the tfrrs one"
        assert m["name"] == "Middlesex League Championship"
        # a one-year error in October is fixed
        assert by[WRONG_BY_ONE]["apply"] and by[WRONG_BY_ONE]["k"] == -1
        assert by[WRONG_BY_ONE]["fixed"] == "2024-10-04"
        # the July meets are the season seam
        for mid in JULY:
            assert by[mid]["k"] == 1 and not by[mid]["apply"]
            assert "convention" in by[mid]["why"]
        assert set(shape["conv"]) == {(7, 1)}

        # track: the same vote
        tf = {r["meet_id"]: r for r in found["TF"][1]}
        assert set(tf) == {TF_WRONG}
        assert tf[TF_WRONG]["apply"] and tf[TF_WRONG]["k"] == -2
        assert tf[TF_WRONG]["fixed"] == "2024-04-18"

        # what stays, and why
        old = _one(cur, "XC", SUNFAIR)
        assert not old["apply"] and old["k"] == 0 and "stored year" in old["why"], \
            "an old season with a new id keeps its date"
        assert old["voters"] == 6 >= shape["n_min"]
        few = _one(cur, "XC", TOO_FEW)
        assert not few["apply"] and few["k"] == -2
        assert few["why"] == "too few graded voters (1 < 2)"
        split = _one(cur, "XC", SPLIT)
        assert not split["apply"] and split["k"] is None and "split" in split["why"]
        assert (split["for_stored"], split["voters"]) == (4, 8)
        mix = _one(cur, "XC", MIX)
        assert not mix["apply"] and mix["k"] == -2 and "a mix" in mix["why"]
        assert (mix["for_fixed"], mix["for_stored"], mix["voters"]) == (6, 4, 10)

        MD.printReport(found)
        out = capsys.readouterr().out
        assert "minimum voters = 2" in out and "Middlesex League Championship" in out
        assert "FIX: the grades agree" in out and "convention" in out
        MD.printReport(MD.examine(cur, ("XC",), meet=424242), meet=424242)
        assert "meet 424242: no graded voter" in capsys.readouterr().out
    finally:
        conn.rollback()
        conn.close()


def test_record_apply_and_undo_on_postgres():
    conn = _pg()
    try:
        cur = conn.cursor()
        _load(cur)
        found = MD.examine(cur)
        assert MD.record(cur, found) == 3
        conn.commit()
        MD.apply(conn)
        assert _dates(cur, MIDDLESEX) == ["2023-10-26"]
        assert _dates(cur, MIDDLESEX, "tfrrs") == ["2025-10-26"], "only anet rows move"
        assert _dates(cur, WRONG_BY_ONE) == ["2024-10-04"]
        assert _dates(cur, TF_WRONG, table="results_tf") == ["2024-04-18"]
        assert _dates(cur, SUNFAIR) == ["1999-10-16"], "the historic upload stays"
        assert _dates(cur, SPLIT) == ["2024-10-12"] and _dates(cur, TOO_FEW) == ["2024-10-19"]
        assert _dates(cur, MIX) == ["2025-10-11"]
        assert _dates(cur, JULY[0]) == ["2022-07-15"], "a convention is not moved"
        cur.execute("SELECT meet_date FROM meets WHERE meet_id = %s", (MIDDLESEX,))
        assert cur.fetchone()[0] == "2023-10-26", "the meet's own date moves too"
        cur.execute("SELECT meet_date FROM meets_tf_meta WHERE meet_id = %s", (TF_WRONG,))
        assert cur.fetchone()[0] == "2024-04-18"
        cur.execute("SELECT years, voters, for_fixed, for_stored, n_rows "
                    "FROM meet_date_fix WHERE sport = 'XC' AND meet_id = %s", (MIDDLESEX,))
        assert cur.fetchone() == (-2, 8, 8, 0, 11)
        # idempotent: the vote now agrees with the date, and nothing moves again
        found = MD.examine(cur)
        assert {r["meet_id"] for r in found["XC"][1] if r["apply"]} == set()
        assert MD.record(cur, found) == 0
        cur.execute("UPDATE results SET date = '2025-10-26' WHERE meet_id = %s "
                    "AND source = 'anet' AND person_id = 202400", (MIDDLESEX,))
        conn.commit()                                  # a re-scrape writes it back
        MD.apply(conn)
        assert _dates(cur, MIDDLESEX) == ["2023-10-26"], "the record re-applies it"
        # undo: back to the stored year, and no later run re-applies it
        MD.undo(conn, "XC", MIDDLESEX)
        assert _dates(cur, MIDDLESEX) == ["2025-10-26"]
        cur.execute("SELECT status FROM meet_date_fix WHERE meet_id = %s", (MIDDLESEX,))
        assert cur.fetchone()[0] == "reverted"
        found = MD.examine(cur)
        assert MIDDLESEX in {r["meet_id"] for r in found["XC"][1] if r["apply"]}
        assert MD.record(cur, found) == 0
        conn.commit()
        MD.apply(conn)
        assert _dates(cur, MIDDLESEX) == ["2025-10-26"], "a reverted meet stays as stored"
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS results, results_tf, meets, "
                    "meets_tf_meta, meet_date_fix")
        conn.commit()
        conn.close()


def test_a_table_from_the_first_cut_still_serves():
    """The first cut's meet_date_fix had neighbour_day; the new rows leave it
    empty and every statement names its columns."""
    conn = _pg()
    try:
        cur = conn.cursor()
        _load(cur)
        cur.execute("""CREATE TABLE meet_date_fix (sport text NOT NULL,
            meet_id bigint NOT NULL, meet_name text, stored_first text NOT NULL,
            stored_last text NOT NULL, fixed_first text NOT NULL,
            fixed_last text NOT NULL, years int NOT NULL, neighbour_day text,
            voters int, for_fixed int, for_stored int, n_rows int,
            status text NOT NULL DEFAULT 'applied',
            found_at timestamptz NOT NULL DEFAULT now(),
            changed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (sport, meet_id))""")
        assert MD.record(cur, MD.examine(cur)) == 3
        conn.commit()
        MD.apply(conn)
        assert _dates(cur, MIDDLESEX) == ["2023-10-26"]
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS results, results_tf, meets, "
                    "meets_tf_meta, meet_date_fix")
        conn.commit()
        conn.close()
