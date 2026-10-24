"""engine/data_checks.py (pipeline 04g1): the pairs of facts that should
agree. Pure parts here; the queries on a scratch Postgres when offered."""
import contextlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "tests"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import _env  # noqa: E402,F401
import data_checks as DC  # noqa: E402


def test_a_field_off_its_level_at_a_corroborated_distance_is_pinned():
    # Hole in the Wall 2022: varsity 5K stored (overridden) 4506, the field
    # reads ~10% under its own level; other divisions of the meet ran 5000
    row = (1, 2, "anet", 4506.0, 302, -0.11, 0.02, 0.85)
    assert DC.fieldCandidates([row]) == [row]
    act, d, ev = DC.fieldVerdict(row, {1: [4506.0, 5000.0]})
    assert (act, d) == ("pin", 5000.0) and "another division" in ev


def test_uncorroborated_or_mixed_fields_are_only_listed():
    # Al Shirley women: +30% vs level at a real 5000 -- the course, not the
    # distance; 4000 would be the arithmetic, no division runs it: review
    row = (3, 4, "anet", 5000.0, 10, 0.30, 0.9, 0.0)
    assert DC.fieldVerdict(row, {3: [5000.0]})[0] == "review"
    # a field split both ways is not one race's error
    assert DC.fieldCandidates([(5, 6, "anet", 5000.0, 40, 0.16, 0.5, 0.3)]) == []
    # too few runners, or too small a gap
    assert DC.fieldCandidates([(5, 6, "anet", 5000.0, 5, 0.4, 1.0, 0.0),
                               (5, 7, "anet", 5000.0, 50, 0.07, 1.0, 0.0)]) == []
    # no stored distance to fix
    assert DC.fieldVerdict((7, 8, "tfrrs", None, 30, -0.3, 0, 1), {})[0] == "review"
    # Al Shirley again, its venue's other races ALSO high: the course, not the label
    assert DC.fieldVerdict(row, {3: [5000.0]}, venue=(6, 0.27))[0] == "review"


def test_a_6k_that_was_a_5k_is_pinned_when_its_venue_runs_true():
    """owner, 2026-10-23: Miner Invitational 2007, 'just a 5k listed as a 6k'"""
    row = (9, 1, "tfrrs", 6000.0, 60, 0.20, 0.95, 0.0)
    act, d, ev = DC.fieldVerdict(row, {("tfrrs", 9): []}, venue=(5, 0.01))
    assert (act, d) == ("pin", 5000.0) and "standard distance below" in ev
    # unknown venue, or a venue that reads high: listed only
    assert DC.fieldVerdict(row, {("tfrrs", 9): []})[0] == "review"
    assert DC.fieldVerdict(row, {("tfrrs", 9): []}, venue=(5, 0.12))[0] == "review"
    # never upward
    low = (9, 2, "tfrrs", 5000.0, 60, -0.20, 0.0, 0.95)
    assert DC.fieldVerdict(low, {("tfrrs", 9): []}, venue=(5, 0.0))[0] == "review"


def test_tfrrs_pins_have_their_own_dict():
    text = DC.replaceBlock("X = 1\n", DC.block({(1, 2): 5000.0, ("tfrrs", 9, 1): 5000.0}))
    assert DC.readPins(text) == {(1, 2): 5000.0, ("tfrrs", 9, 1): 5000.0}
    ns = {"_DISTANCE_OVERRIDES_BY_SPORT": {"XC": {}}}
    exec(DC.block(DC.readPins(text)), ns)
    assert ns["_DISTANCE_OVERRIDES_BY_SPORT"]["XC"] == {(1, 2): 5000.0}     # tfrrs never here
    assert ns["_DISTANCE_PIN_FIELD_TFRRS"] == {(9, 1): 5000.0}


def test_pins_are_sticky_and_reach_the_backfill_dict():
    text = "X = 1\n"
    one = DC.replaceBlock(text, DC.block({(1, 2): 5000.0}))
    assert DC.readPins(one) == {(1, 2): 5000.0}
    merged = dict(DC.readPins(one))
    merged.setdefault((3, 4), 8000.0)
    two = DC.replaceBlock(one, DC.block(merged))
    assert DC.readPins(two) == {(1, 2): 5000.0, (3, 4): 8000.0}
    assert two.count(DC.BLOCK_START) == 1
    ns = {"_DISTANCE_OVERRIDES_BY_SPORT": {"XC": {(1, 2): 4506.0}}}
    exec(DC.block(merged), ns)
    xc = ns["_DISTANCE_OVERRIDES_BY_SPORT"]["XC"]
    assert xc[(3, 4)] == 8000.0 and xc[(1, 2)] == 4506.0   # hand override wins


def test_parser_disagreements():
    pairs = [("880y", 804.67, 880.0), ("800m", 800.0, 800.0),
             ("100m", None, 100.0), ("Mile", 1609.34, 1609.34), ("x", 1000.0, None)]
    assert [p[0] for p in DC.parserIssues(pairs)] == ["880y", "x"]


def test_the_checks_on_a_database():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS dc_test")
        c.execute("CREATE DATABASE dc_test")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + " dbname=dc_test"
    cx = psycopg2.connect(test_dsn)
    try:
        with cx.cursor() as c:
            c.execute("""
                CREATE TABLE results (result_id bigint, meet_id bigint, div_id bigint,
                    source text, person_id bigint, athlete_id bigint, speed_rating real,
                    rating_pool text DEFAULT 'hs_m');
                CREATE TABLE results_tf (person_id bigint, speed_rating real, event_short text,
                    rating_pool text DEFAULT 'hs_m');
                CREATE TABLE meets (meet_id bigint, div_id bigint, distance real, course_name text);
                CREATE TABLE meets_tfrrs (meet_id bigint, sport text, venue_name text,
                    division_distances jsonb);
                CREATE TABLE athletes (athlete_id bigint, gender text);
                -- 20 runners rated ~100 in 5 races each; meet 9 div 91 reads ~10% low
                INSERT INTO results SELECT g*10+k, 100+k, 1000+k, 'anet', g, g,
                    100 + (g % 3) FROM generate_series(1,20) g, generate_series(1,5) k;
                INSERT INTO results SELECT 9000+g, 9, 91, 'anet', g, g, 89
                    FROM generate_series(1,20) g;
                -- the same runners in another pool, on that pool's scale (2026-10-19:
                -- one career mean over pools read a children's 2 miles as a mile)
                INSERT INTO results SELECT 50000+g*10+k, 500+k, 5000+k, 'anet', g, g,
                    200, 'ms_m' FROM generate_series(1,20) g, generate_series(1,5) k;
                INSERT INTO meets VALUES (9, 91, 4506), (9, 92, 5000);
                INSERT INTO athletes SELECT g, 'M' FROM generate_series(1,20) g;
                INSERT INTO results_tf VALUES (1, 100, '880y'), (1, 100, '800m');
            """)
            issues, pins, rejects = DC.raceField(c)
            assert pins == {(9, 91): 5000.0}, (issues, pins)
            assert rejects == {(9, 91)}
            got = DC.parser(c)
            assert [g[2] for g in got] == []          # the SQL reads yards now
            assert DC.personGender(c) == []
        cx.rollback()
    finally:
        cx.close()
        with admin.cursor() as c:
            c.execute("DROP DATABASE IF EXISTS dc_test")
        admin.close()
