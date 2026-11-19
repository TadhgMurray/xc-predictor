"""pro_flag's school veto and team seasons built on side connections give
the same tables as on the one connection (2026-11-19, engine/pro_flag.py
_Sides). Needs a scratch Postgres (XCP_TWIN_TEST_DSN); the fixture is
test_pro_school_veto's, committed for the side connections to see and
dropped after."""
import os
import sys
from contextlib import contextmanager

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "tests"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest                                                     # noqa: E402


def test_side_connections_build_the_same_tables():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import pro_flag as PF
    import test_pro_school_veto as V

    levels = {V.HERRIMAN: "hs", V.OTHER_HS: "hs", 7: "hs", V.NIKE: "club",
              V.USA_TEAM: "club", 42: "college"}

    @contextmanager
    def connect():
        c = psycopg2.connect(dsn)
        try:
            yield c
        finally:
            c.close()

    setup = psycopg2.connect(dsn)
    try:
        V._seed(setup.cursor())
        setup.commit()

        def tables(cur):
            out = []
            for t in ("tmp_school_season", "tmp_team_season"):
                cur.execute(f"SELECT * FROM {t} ORDER BY 1, 2")
                out.append(cur.fetchall())
            return out

        with connect() as old:
            cur = old.cursor()
            PF.buildSchoolSeasons(cur, team_levels=levels, pro_teams=set())
            PF.buildTeamSeasons(cur)
            want = tables(cur)
        assert want[0] and want[1]

        with connect() as new:
            cur = new.cursor()
            sides = PF._Sides(team=True, team_levels=levels, pro_teams=set(),
                              connect=connect)
            try:
                sides.school.into(cur)
                PF._schoolSeasonFinish(cur)
                PF.buildTeamSeasons(cur, sides=sides)
            finally:
                sides.close()
            assert tables(cur) == want
            # read the way propagate reads it: indexed and ANALYZEd on main
            cur.execute("SELECT count(*) FROM pg_indexes "
                        "WHERE tablename = 'tmp_school_season'")
            assert cur.fetchone()[0] == 1

            # a side that fails fails the step, on main
            bad = PF._SideBuild("bad", "tmp_bad",
                                lambda c: c.execute("SELECT 1/0"), connect)
            with pytest.raises(RuntimeError, match="tmp_bad"):
                bad.into(cur)
    finally:
        setup.rollback()
        setup.cursor().execute(
            "DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, "
            "grade_fix, athlete_season, pro_athlete_season")
        setup.commit()
        setup.close()
