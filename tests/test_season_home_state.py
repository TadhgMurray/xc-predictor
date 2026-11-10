"""A school-age season's state is the athlete's home when the season raced
there at all (data audit, 2026-11-10: Oliver Horton, Coronado HS in Colorado
Springs, #1 on Arizona's 2026 board off one Arizona invitational in a
three-race season). Needs a scratch Postgres: XCP_TWIN_TEST_DSN."""
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
DSN = os.environ.get("XCP_TWIN_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_the_season_takes_home_state():
    import psycopg2
    import build_ranking_results as B
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("""
            CREATE TEMP TABLE hs_season (person_id bigint, pool text, sport text,
                year int, mean_rating real, decayed_rating real, best_rating real,
                n_races int, first_race date, last_race date, state text,
                school text, grade text);
            CREATE TEMP TABLE hs_load (person_id bigint, pool text, sport text, year int,
                speed_rating real, race_date date, state text, school text,
                division text, grade text);
            -- Horton: five CO seasons' races, then 2026 = AZ, AZ, CO
            INSERT INTO hs_load SELECT 1, 'hs_m', 'XC', 2020 + g % 5, 140, DATE '2024-09-01',
                                       'CO', 'Coronado', NULL, '11' FROM generate_series(1, 20) g;
            INSERT INTO hs_load VALUES (1, 'hs_m', 'XC', 2026, 141, '2026-09-19', 'AZ', 'Coronado', NULL, '12'),
                                       (1, 'hs_m', 'XC', 2026, 142, '2026-09-25', 'AZ', 'Coronado', NULL, '12'),
                                       (1, 'hs_m', 'XC', 2026, 147, '2026-10-31', 'CO', 'Coronado', NULL, '12');
            -- a mover: three CO seasons, then a 2026 season all in AZ -- AZ
            INSERT INTO hs_load SELECT 2, 'hs_f', 'XC', 2023 + g % 3, 120, DATE '2024-09-01',
                                       'CO', 'Old HS', NULL, '10' FROM generate_series(1, 12) g;
            INSERT INTO hs_load SELECT 2, 'hs_f', 'XC', 2026, 121, DATE '2026-09-20',
                                       'AZ', 'New HS', NULL, '12' FROM generate_series(1, 4) g;
            -- a college season keeps its venues' mode
            INSERT INTO hs_load VALUES (3, 'hs_m', 'XC', 2024, 130, '2024-09-01', 'OR', 'X', NULL, '12'),
                                       (3, 'college_m', 'XC', 2026, 130, '2026-09-01', 'CA', 'U', NULL, 'FR'),
                                       (3, 'college_m', 'XC', 2026, 130, '2026-09-08', 'CA', 'U', NULL, 'FR'),
                                       (3, 'college_m', 'XC', 2026, 130, '2026-09-15', 'OR', 'U', NULL, 'FR');
        """)
        cur.execute(B._ATHLETE_SEASON_SQL.format(load_table="hs_load", season_table="hs_season",
                                                 shard_where="TRUE", shard_where_base="TRUE"))
        cur.execute("SELECT person_id, pool, year, state FROM hs_season WHERE year = 2026")
        got = {(p, pool): st for p, pool, _y, st in cur.fetchall()}
    finally:
        c.rollback()
        c.close()
    assert got[(1, "hs_m")] == "CO"
    assert got[(2, "hs_f")] == "AZ"
    assert got[(3, "college_m")] == "CA"
