"""The precomputed rank line (311) against a REAL Postgres: the build's own
statement, on a fixture covering the floor, the open season, a non-US row,
a tie, every unit scope and a team, checked against an independent Python
computation of the same ranks.

    XCP_TEST_DSN='host=/var/tmp port=5433 user=postgres dbname=postgres' \
        python -m pytest -q tests/test_season_ranks_sql.py

★ WHY A DATABASE TEST. tests/test_season_ranks.py pins the statement's
  SHAPE by source, which cannot catch a window that partitions on the
  wrong thing -- the failure mode that puts an athlete at the wrong rank
  with no error at all. This one runs it. Skipped when no DSN is given,
  so the offline suite is unaffected; any throwaway Postgres will do.
"""
import contextlib
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _d))

DSN = os.environ.get("XCP_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set XCP_TEST_DSN to a throwaway Postgres")

UNIT_COLS = ("state_div", "class", "section", "section_div", "area", "league",
             "division", "region", "conference")

# person, pool, sport, year, rating, races, school, state, then UNIT_COLS
ROWS = [
    # hs_m XC 2025 in California: one section, two divisions, a league each
    (1,  "hs_m", "XC", 2025, 170.0, 8, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
    (2,  "hs_m", "XC", 2025, 165.0, 8, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
    (3,  "hs_m", "XC", 2025, 160.0, 8, "Newbury Park", "CA", "1", None, "CIF-SS", "2", "Coast",  "MVL", None, None, None),
    (4,  "hs_m", "XC", 2025, 155.0, 2, "Newbury Park", "CA", "1", None, "CIF-SS", "2", "Coast",  "MVL", None, None, None),  # under the floor
    (5,  "hs_m", "XC", 2025, 150.0, 8, "Lone Peak",    "UT", "5", None, "Region", "A", None,     "LPL", None, None, None),
    (6,  "hs_m", "XC", 2025, 145.0, 8, None,           None, None, None, None,   None, None,    None,  None, None, None),  # no school, no state
    (7,  "hs_m", "XC", 2025, 140.0, 8, "St Kilda",     "XX", None, None, None,   None, None,    None,  None, None, None),  # outside the US
    (8,  "hs_m", "XC", 2025, 160.0, 8, "Great Oak",    "CA", None, "1", "CIF-SS", "1", "Inland", "SWL", None, None, None),  # a tie, and class not state_div
    # an open season carries no floor, so a one-race row still ranks
    (9,  "hs_m", "XC", 2026, 158.0, 1, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
    (10, "hs_m", "XC", 2026, 162.0, 5, "Newbury Park", "CA", "1", None, "CIF-SS", "2", "Coast",  "MVL", None, None, None),
    # college: the units lead and carry no state
    (11, "college_m", "XC", 2025, 190.0, 8, "Stanford", "CA", None, None, None, None, None, None, "NCAA DI",   "WEST", "PAC-12"),
    (12, "college_m", "XC", 2025, 185.0, 8, "Tufts",    "MA", None, None, None, None, None, None, "NCAA DIII", "EAST", "NESCAC"),
    (13, "college_m", "XC", 2025, 188.0, 8, "Williams", "MA", None, None, None, None, None, None, "NCAA DIII", "EAST", "NESCAC"),
    # another sport and another pool share the year and must not mix
    (1,  "hs_m", "TF", 2025, 171.0, 8, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
    (2,  "hs_f", "XC", 2025, 150.0, 8, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
    # a season with no rating is nobody's rank
    (14, "hs_m", "XC", 2025, None,  8, "Great Oak",    "CA", "1", None, "CIF-SS", "1", "Inland", "SWL", None, None, None),
]
FLOOR, OPEN_FROM = 3, 2026


def _rows():
    cols = ["person_id", "pool", "sport", "year", "mean_rating", "n_races", "school", "state"] + list(UNIT_COLS)
    return [dict(zip(cols, r)) for r in ROWS if r[4] is not None]


def _expected(us_states):
    """The same ranks, computed in Python from the board's rules."""
    rows = _rows()
    for r in rows:
        r["ranked"] = r["n_races"] >= FLOOR or r["year"] >= OPEN_FROM
        r["us"] = r["state"] in us_states
        r["sd"] = r["state_div"] or r["class"]

    def place(row, keys, only=lambda r: True):
        if not row["ranked"] or not only(row):
            return None
        peers = [r for r in rows if r["ranked"] and only(r)
                 and all(r[k] == row[k] for k in ["pool", "sport", "year", "us"] + keys)]
        peers.sort(key=lambda r: (-r["mean_rating"], r["person_id"]))
        return peers.index(row) + 1

    want = {}
    for r in rows:
        has_state = bool(r["state"])
        want[(r["person_id"], r["pool"], r["sport"], r["year"])] = {
            "nation": place(r, [], lambda x: x["us"]) if r["us"] else None,
            "nation_total": (len([x for x in rows if x["ranked"] and x["us"]
                                  and (x["pool"], x["sport"], x["year"]) == (r["pool"], r["sport"], r["year"])])
                             if (r["us"] and r["ranked"]) else None),
            "state_rank": place(r, ["state"]) if has_state else None,
            "state_div": place(r, ["state", "sd"]) if (has_state and r["sd"]) else None,
            "section": place(r, ["state", "section"]) if (has_state and r["section"]) else None,
            "section_div": place(r, ["state", "section", "section_div"])
                           if (has_state and r["section"] and r["section_div"]) else None,
            "area": place(r, ["state", "section", "area"]) if (has_state and r["section"] and r["area"]) else None,
            "league": place(r, ["state", "league"]) if (has_state and r["league"]) else None,
            "division": place(r, ["division"]) if r["division"] else None,
            "region": place(r, ["region"]) if r["region"] else None,
            "conference": place(r, ["conference"]) if r["conference"] else None,
            "team": (1 + len([x for x in rows if x["school"] == r["school"] and x["sport"] == r["sport"]
                              and x["year"] == r["year"] and x["mean_rating"] > r["mean_rating"]]))
                    if r["school"] else None,
        }
    return want


def test_every_scope_matches_a_hand_computation():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DSN)
    db = types.ModuleType("database")

    @contextlib.contextmanager
    def _conn():
        yield conn
    db.getConn = _conn
    db.initPool = lambda *a, **k: None
    sys.modules["database"] = db

    import build_season_ranks as B
    from rankings import US_STATES

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS athlete_season, season_rank_new CASCADE")
        cur.execute(f"""CREATE TABLE athlete_season (
            person_id bigint, pool text, sport text, year int, mean_rating real,
            n_races int, school text, state text,
            {', '.join(c + ' text' for c in UNIT_COLS)})""")
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO athlete_season (person_id, pool, sport, year, mean_rating, n_races, "
                 f"school, state, {', '.join(UNIT_COLS)}) VALUES %s", ROWS)
        # the build's own statement, and the key it puts on the result
        cur.execute(B.rankSql(True), {"us": list(US_STATES)})
        cur.execute("ALTER TABLE season_rank_new ADD CONSTRAINT season_rank_new_pkey "
                    "PRIMARY KEY (person_id, pool, sport, year)")
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM season_rank_new")
        got = {(r["person_id"], r["pool"], r["sport"], r["year"]): dict(r) for r in cur.fetchall()}

    want = _expected(set(US_STATES))
    assert set(got) == set(want)
    bad = [f"{key} {col}: stored {got[key][col]!r}, expected {expect!r}"
           for key, w in sorted(want.items()) for col, expect in w.items()
           if got[key][col] != expect]
    assert not bad, "\n".join(bad)

    # the facts worth naming, so a failure says which rule broke
    assert got[(1, "hs_m", "XC", 2025)]["nation"] == 1
    assert got[(4, "hs_m", "XC", 2025)]["nation"] is None      # under the floor: on no board
    assert got[(4, "hs_m", "XC", 2025)]["team"] == 2           # the team line has no floor
    assert got[(9, "hs_m", "XC", 2026)]["nation"] == 2         # an open season ranks on one race
    assert got[(7, "hs_m", "XC", 2025)]["nation"] is None      # outside the US
    assert got[(7, "hs_m", "XC", 2025)]["state_rank"] == 1     # but still ranked in its own state
    assert got[(3, "hs_m", "XC", 2025)]["nation"] == 3         # the tie breaks on person_id,
    assert got[(8, "hs_m", "XC", 2025)]["nation"] == 4         # exactly as the board orders it
    assert got[(8, "hs_m", "XC", 2025)]["state_div"] == 4      # class stands in for state_div, and the tie still breaks on id
    assert got[(13, "college_m", "XC", 2025)]["division"] == 1  # DIII is its own board
    assert got[(11, "college_m", "XC", 2025)]["nation"] == 1
    conn.close()
