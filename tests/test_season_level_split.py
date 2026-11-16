"""04h's split (2026-11-16): `season_level.py --write --votes-only` (beside
grade_sanity) then `--write --from-votes` (after it) writes the SAME
athlete_season_level, byte for byte, as `--write` in one go -- with
grade_fix written only BETWEEN the two stages, as in the pipeline.

The corpus: both sports, word and number grades and their odd spellings,
grades that only grade_fix carries, NULL grades / person ids / dates,
races race_level decides and races only meets.level_mask decides (XC by
source, TF across a container's events), college_first_season both sides.

Needs a scratch Postgres (XCP_TEST_PG_DSN or XCP_TWIN_TEST_DSN); it DROPs
and creates results, results_tf, meets, meets_tf, race_level, grade_fix,
college_first_season, athlete_season_level and season_vote_raw there.

    XCP_TEST_PG_DSN=postgresql://postgres:pw@localhost/sl_split \\
        python -m pytest -q tests/test_season_level_split.py
"""
import contextlib
import os
import random
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest                                                     # noqa: E402

_DSN = os.environ.get("XCP_TEST_PG_DSN") or os.environ.get("XCP_TWIN_TEST_DSN")

# number, word, odd and junk spellings; None is a NULL grade
_GRADES = ["9", "10", "11", "12", "09", "07", "8", "12th", "Gr. 11", "FR",
           "SO", "JR", "SR", "Freshman", "Sophomore", "SO-2", "JR-3",
           "Senior", "-", "15", "2024", "", None]
# spellings only grade_fix carries (the refusal reads them through the kinds)
_FIX_ONLY = ["11th", "Junior", "06", "Fr.", "10th"]


def _seed(cur, seed=7):
    import level_graph
    rnd = random.Random(seed)
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, race_level, meets,
                             meets_tf, grade_fix, college_first_season,
                             athlete_season_level, season_vote_raw;
        CREATE TABLE results (result_id bigint, person_id bigint,
            meet_id bigint, div_id bigint, source text, date text,
            grade text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE meets (meet_id bigint, div_id bigint, source text,
                            level_mask int);
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event text,
                               level_mask int);
        CREATE TABLE race_level (race bigint PRIMARY KEY, level text);
        CREATE TABLE college_first_season (person_id bigint PRIMARY KEY,
                                           first_season int, first_date date);
    """)
    rid = 0
    races = {"results": [], "results_tf": []}
    for table in races:
        for meet in range(1, 61):
            for div in range(1, 4):
                src = rnd.choice(["anet", "tfrrs", None]) if table == "results" \
                    else rnd.choice(["anet", "tfrrs"])
                races[table].append((meet, div, src))
    rows = {"results": [], "results_tf": []}
    for pid in range(1, 401):
        base = rnd.randint(2019, 2025)
        g0 = rnd.choice(_GRADES)
        for _k in range(rnd.randint(1, 14)):
            table = rnd.choice(["results", "results_tf"])
            meet, div, src = rnd.choice(races[table])
            yr = base + rnd.randint(0, 2)
            date = f"{yr}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            if rnd.random() < 0.03:
                date = None
            grade = g0 if rnd.random() < 0.7 else rnd.choice(_GRADES)
            person = None if rnd.random() < 0.02 else pid
            rid += 1
            rows[table].append((rid, person, meet, div, src, date, grade))
    for table, rr in rows.items():
        cur.executemany(f"INSERT INTO {table} VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        rr)
    # race_level decides ~half the races; the mask the rest (some mixed,
    # some out of range, some open-only)
    for table in ("results", "results_tf"):
        for meet, div, src in races[table]:
            if rnd.random() < 0.5:
                cur.execute(
                    f"""INSERT INTO race_level
                        SELECT {level_graph._raceKeyExpr('x')}, %s
                        FROM (SELECT %s::bigint AS meet_id, %s::bigint AS div_id,
                                     %s::text AS source) x
                        ON CONFLICT DO NOTHING""",
                    (rnd.choice(["ms", "hs", "college", "pro"]), meet, div, src))
            masks = [2, 4, 8, 6, 12, 16, 24, 25, 1, 2048, None]
            if table == "results":
                cur.execute("INSERT INTO meets VALUES (%s,%s,%s,%s)",
                            (meet, div, src, rnd.choice(masks)))
            else:
                for ev in range(rnd.randint(1, 3)):
                    cur.execute("INSERT INTO meets_tf VALUES (%s,%s,%s,%s)",
                                (meet, div, f"e{ev}", rnd.choice(masks)))
    for pid in range(1, 401, 3):
        cur.execute("INSERT INTO college_first_season VALUES (%s, %s, %s)",
                    (pid, 2022, f"{rnd.randint(2020, 2025)}-09-01"))
    return rnd


def _writeGradeFix(cur, rnd):
    """What 04_grade_sanity writes -- here only between the two stages."""
    cur.execute("""DROP TABLE IF EXISTS grade_fix;
                   CREATE TABLE grade_fix (person_id bigint, season int,
                       grade text, level text, method text, trust text)""")
    for pid in range(1, 401, 2):
        for ay in range(2018, 2027):
            if rnd.random() < 0.4:
                g = rnd.choice(_GRADES[:-1] + _FIX_ONLY + _FIX_ONLY)
                cur.execute("INSERT INTO grade_fix VALUES (%s,%s,%s,%s,'m','high')",
                            (pid, ay, g, rnd.choice([None, "pro"])))


def _dump(cur):
    cur.execute("""SELECT count(*),
                          md5(string_agg(t::text, E'\\n'
                                         ORDER BY person_id, ay, sport))
                   FROM athlete_season_level t""")
    return cur.fetchone()


@pytest.fixture
def conn():
    if not _DSN:
        pytest.skip("set XCP_TEST_PG_DSN to a scratch Postgres")
    import psycopg2
    c = psycopg2.connect(_DSN)
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def fake_db(monkeypatch):
    """season_level.main's getConn, on the scratch database."""
    import psycopg2

    @contextlib.contextmanager
    def getConn():
        c = psycopg2.connect(_DSN)
        try:
            yield c
        finally:
            c.close()
    mod = types.ModuleType("database")
    mod.getConn = getConn
    monkeypatch.setitem(sys.modules, "database", mod)


@pytest.mark.parametrize("seed", [7, 11, 2026])
def test_the_split_writes_what_one_go_writes(conn, fake_db, seed):
    import season_level as SL
    # one go, grade_fix already there (today's 04h)
    with conn.cursor() as cur:
        rnd = _seed(cur, seed)
        _writeGradeFix(cur, random.Random(seed * 31))
    conn.commit()
    SL.main(live=True, mode="all")
    with conn.cursor() as cur:
        one = _dump(cur)
        cur.execute("SELECT count(*) FILTER (WHERE refused IS NOT NULL), "
                    "count(*) FILTER (WHERE level IS NOT NULL), "
                    "count(DISTINCT sport) FROM athlete_season_level")
        refused, decided, sports = cur.fetchone()
    conn.commit()
    assert one[0] > 300 and refused > 0 and decided > 0 and sports == 3

    # the split: votes with NO grade_fix (grade_sanity has not finished),
    # grade_fix written, then the resolve
    with conn.cursor() as cur:
        cur.execute("DROP TABLE athlete_season_level, grade_fix")
    conn.commit()
    SL.main(live=True, mode="votes")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM season_vote_raw")
        assert cur.fetchone()[0] > 0
        _writeGradeFix(cur, random.Random(seed * 31))
    conn.commit()
    SL.main(live=True, mode="resolve")
    with conn.cursor() as cur:
        split = _dump(cur)
        cur.execute("SELECT to_regclass('public.season_vote_raw')")
        assert cur.fetchone()[0] is None          # used, dropped
    conn.commit()
    assert split == one


def test_the_classified_votes_are_row_for_row_the_one_go_votes(conn, fake_db):
    import season_level as SL
    with conn.cursor() as cur:
        _seed(cur, 5)
        _writeGradeFix(cur, random.Random(5))
    conn.commit()
    SL.main(live=True, mode="votes")
    q = ("SELECT person_id, ay, sport, level, n, n_school, n_class "
         "FROM tmp_season_vote ORDER BY 1, 2, 3, 4, 5, 6, 7")
    with conn.cursor() as cur:
        SL._collectVotes(cur)
        cur.execute(q)
        direct = cur.fetchall()
        SL.votesFromRaw(cur)
        cur.execute(q)
        assert cur.fetchall() == direct and len(direct) > 100
    conn.rollback()


def test_an_emptied_or_unfinished_vote_table_is_collected_in_line(conn, fake_db):
    """An unlogged table comes back EMPTY after a crash: the resolve must
    not read that as 'no votes'."""
    import season_level as SL
    with conn.cursor() as cur:
        _seed(cur, 3)
        _writeGradeFix(cur, random.Random(3))
    conn.commit()
    SL.main(live=True, mode="all")
    with conn.cursor() as cur:
        one = _dump(cur)
    conn.commit()
    SL.main(live=True, mode="votes")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE season_vote_raw")   # what crash recovery does
        assert not SL.rawVotesReady(cur)
    conn.commit()
    SL.main(live=True, mode="resolve")
    with conn.cursor() as cur:
        assert _dump(cur) == one
    conn.commit()
    # no table at all (the votes stage never ran): in line too
    SL.main(live=True, mode="resolve")
    with conn.cursor() as cur:
        assert _dump(cur) == one
    conn.commit()
