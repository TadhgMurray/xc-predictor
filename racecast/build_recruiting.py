# Project: xc-predictor / racecast
# File:    build_recruiting.py
# Purpose: Who each college actually recruits (issue 282, the athlete
#          edition). Pipeline step 10f, after 10b (school identity) and
#          10d (school units); runs alone in a minute or two.
#
#     python racecast/build_recruiting.py            # write college_recruit
#     python racecast/build_recruiting.py --dry-run  # counts only
#
# ★ A RECRUIT IS A FIRST COLLEGE SEASON. For every athlete with a season
#   in a college pool, the earliest such season (any college pool, any
#   sport) names the school that recruited them; a season at a second
#   college later is a transfer and is not a recruiting fact about the
#   second school. One row per (recruit, sport): the fall recruits and
#   the spring recruits are two distributions, and an athlete who runs
#   both is in both.
#
# ★ THE NUMBER ON THE PAGE IS ON THE HIGH-SCHOOL SCALE, because the reader
#   is a high schooler holding their own rating. Two sources, in order:
#     1. the recruit's own last high-school season in the sport (the same
#        person_id, one or two stored years before the first college
#        season) -- the true "what they ran to get in";
#     2. when no HS season is linked, the first college season itself,
#        multiplied onto the HS scale by pool_view's factor and LESS THE
#        FRESHMAN GAIN: freshmen improve during the year, and the gain is
#        measured here from the recruits who have both numbers (the
#        median of hs_equiv - hs_rating per gender and sport, printed,
#        stored in college_recruit_meta). Below MIN_GAIN_PAIRS pairs the
#        gain is 0 and the log says so.
#   `source` says which; the page shows how many of a school's recruits
#   are linked so a reader can weigh it.
#
# ! NOT A RECRUIT: a first observed season graded JR-3 or SR-4 (the
#   corpus starts mid-career), a season that fails the boards' own race
#   floor (season_floor.floorSql), a "school" that is a club or
#   unattached (panels.isTeamName), and a class older than CLASSES
#   seasons back from the newest -- the level moves and old classes are
#   not what the coach recruits now.
#
# Swap discipline as build_school_units: a shadow table, indexed, then
# dbfast.swapTable under a bounded lock; the live table is never empty.

import argparse
import sys
import statistics

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                  # noqa: E402

from database import getConn                            # noqa: E402
from dbfast import swapTable                            # noqa: E402
from season_floor import floorSql, DEFAULT_FLOOR        # noqa: E402
from grade_label import gradeLabel                      # noqa: E402
from panels import isTeamName                           # noqa: E402

CLASSES = 6              # recruiting classes kept, newest first
MIN_GAIN_PAIRS = 50      # linked pairs before the freshman gain is trusted
HS_LOOKBACK = 2          # stored years before the first college season
_NOT_RECRUIT_GRADES = {"JR-3", "SR-4", "JR-5", "SR-5", "SR-6", "JR-6"}

_DDL = """
DROP TABLE IF EXISTS college_recruit_new;
CREATE TABLE college_recruit_new (
    person_id      bigint  NOT NULL,
    sport          text    NOT NULL,
    school         text    NOT NULL,
    state          text,
    gender         text    NOT NULL,
    first_year     integer NOT NULL,
    grade          text,
    first_rating   real    NOT NULL,
    hs_equiv       real,
    n_races        integer NOT NULL,
    hs_rating      real,
    hs_year        integer,
    hs_school      text,
    hs_state       text,
    recruit_rating real    NOT NULL,
    source         text    NOT NULL,
    division       text,
    conference     text,
    region         text,
    CONSTRAINT college_recruit_new_pkey PRIMARY KEY (person_id, sport)
);
DROP TABLE IF EXISTS college_recruit_meta_new;
CREATE TABLE college_recruit_meta_new (
    gender     text NOT NULL,
    sport      text NOT NULL,
    gain       real NOT NULL,
    n_pairs    integer NOT NULL,
    factor     real,
    since_year integer NOT NULL,
    built_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gender, sport)
)"""


def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def recruitSql(season_units):
    """The one query: first college seasons, their last HS season, the
    school's identity state and units. season_units says whether
    athlete_season carries the unit columns (older databases do not)."""
    own = ("s.division AS row_division, s.conference AS row_conference, s.region AS row_region"
           if season_units else
           "NULL::text AS row_division, NULL::text AS row_conference, NULL::text AS row_region")
    return f"""
        WITH college AS (
            SELECT s.person_id, s.pool, s.sport, s.year, s.school, s.state, s.grade,
                   s.mean_rating, s.n_races, {own}
            FROM   athlete_season s
            WHERE  s.pool IN ('college_m', 'college_f')
              AND  s.mean_rating IS NOT NULL AND s.school IS NOT NULL
        ),
        firsts AS (
            SELECT person_id, min(year) AS first_year FROM college GROUP BY person_id
        ),
        pick AS (
            SELECT c.*
            FROM   college c
            JOIN   firsts f ON f.person_id = c.person_id AND c.year = f.first_year
            WHERE  c.year >= %(since)s
              AND  {floorSql(False, 'c')}
        ),
        hs AS (
            SELECT h.person_id, h.sport, h.year, h.mean_rating, h.school, h.state,
                   row_number() OVER (PARTITION BY h.person_id, h.sport
                                      ORDER BY h.year DESC) AS rn
            FROM   athlete_season h
            JOIN   firsts f ON f.person_id = h.person_id
            WHERE  h.pool IN ('hs_m', 'hs_f') AND h.mean_rating IS NOT NULL
              AND  h.year < f.first_year AND h.year >= f.first_year - {int(HS_LOOKBACK)}
              AND  {floorSql(False, 'h')}
        )
        SELECT p.person_id, p.sport, p.school, p.pool, p.year AS first_year, p.grade,
               p.mean_rating AS first_rating, p.n_races,
               COALESCE(si.state, p.state) AS state,
               hs.mean_rating AS hs_rating, hs.year AS hs_year,
               hs.school AS hs_school, hs.state AS hs_state,
               COALESCE(u.division, p.row_division)     AS division,
               COALESCE(u.conference, p.row_conference) AS conference,
               COALESCE(u.region, p.row_region)         AS region
        FROM   pick p
        LEFT JOIN hs ON hs.person_id = p.person_id AND hs.sport = p.sport AND hs.rn = 1
        LEFT JOIN LATERAL (
            SELECT state FROM school_identity i
            WHERE  i.school = p.school AND i.is_primary
            ORDER  BY n_athletes DESC LIMIT 1) si ON true
        LEFT JOIN LATERAL (
            SELECT division, conference, region FROM school_unit u
            WHERE  u.school = p.school AND u.is_college
            ORDER  BY (u.sport = p.sport) DESC, u.votes DESC LIMIT 1) u ON true
    """
    # %(min_races)s is bound by the caller: floorSql reads it


def _factors():
    """{pool: multiplier onto the HS scale}, from pool_view; None when the
    site itself could not compute one (the row then carries no hs_equiv
    and is kept only when a HS season is linked)."""
    out = {}
    try:
        from pool_view import repFactor
        for pool in ("college_m", "college_f"):
            f = None
            for sport in ("XC", "TF"):
                f = repFactor(pool, sport)
                if f:
                    break
            out[pool] = float(f) if f else None
    except Exception as exc:                            # noqa: BLE001
        print(f"  factor: {type(exc).__name__}: {exc}")
        out = {"college_m": None, "college_f": None}
    return out


def isRecruitSeason(grade, pool):
    """A first observed season graded junior or senior is a mid-career
    arrival, not a recruit. Unknown grades pass."""
    label = gradeLabel(grade, pool) if grade else None
    return (label or "").upper() not in _NOT_RECRUIT_GRADES


def keepSchool(school):
    return bool(school) and isTeamName(school) and "club" not in school.lower()


def measureGain(rows):
    """{(gender, sport): (gain, n_pairs)}: the median of hs_equiv less the
    linked HS rating, per gender and sport; 0 under MIN_GAIN_PAIRS."""
    pairs = {}
    for r in rows:
        if r["hs_equiv"] is not None and r["hs_rating"] is not None:
            pairs.setdefault((r["gender"], r["sport"]), []).append(
                float(r["hs_equiv"]) - float(r["hs_rating"]))
    out = {}
    for g in ("m", "f"):
        for sp in ("XC", "TF"):
            v = pairs.get((g, sp), [])
            out[(g, sp)] = (statistics.median(v) if len(v) >= MIN_GAIN_PAIRS else 0.0, len(v))
    return out


def shapeRows(raw, factors):
    """The database rows -> the table's rows, with the HS-scale numbers
    filled and the non-recruits dropped. Returns (rows, dropped) where
    dropped counts by reason."""
    dropped = {"grade": 0, "school": 0, "no_scale": 0}
    rows = []
    for r in raw:
        pool = r["pool"].split("|", 1)[0]
        if not keepSchool(r["school"]):
            dropped["school"] += 1
            continue
        if not isRecruitSeason(r.get("grade"), pool):
            dropped["grade"] += 1
            continue
        factor = factors.get(pool)
        hs_equiv = float(r["first_rating"]) * factor if factor else None
        if hs_equiv is None and r["hs_rating"] is None:
            dropped["no_scale"] += 1
            continue
        rows.append({
            "person_id": r["person_id"], "sport": r["sport"], "school": r["school"],
            "state": r["state"], "gender": pool.rsplit("_", 1)[-1],
            "first_year": r["first_year"], "grade": r.get("grade"),
            "first_rating": float(r["first_rating"]), "hs_equiv": hs_equiv,
            "n_races": r["n_races"],
            "hs_rating": float(r["hs_rating"]) if r["hs_rating"] is not None else None,
            "hs_year": r["hs_year"], "hs_school": r["hs_school"], "hs_state": r["hs_state"],
            "division": r["division"], "conference": r["conference"], "region": r["region"],
        })
    gains = measureGain(rows)
    for r in rows:
        if r["hs_rating"] is not None:
            r["recruit_rating"], r["source"] = r["hs_rating"], "hs"
        else:
            gain = gains[(r["gender"], r["sport"])][0]
            r["recruit_rating"], r["source"] = r["hs_equiv"] - gain, "college"
    return rows, dropped, gains


_COLS = ("person_id", "sport", "school", "state", "gender", "first_year", "grade",
         "first_rating", "hs_equiv", "n_races", "hs_rating", "hs_year", "hs_school",
         "hs_state", "recruit_rating", "source", "division", "conference", "region")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--classes", type=int, default=CLASSES)
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.athlete_season') AS t")
            if cur.fetchone()["t"] is None:
                print("  athlete_season missing -- run 10_rankings first.")
                return
            cur.execute("""SELECT max(year) AS y FROM athlete_season
                           WHERE pool IN ('college_m', 'college_f')""")
            newest = cur.fetchone()["y"]
            if newest is None:
                print("  no college seasons -- college_recruit left as it was.")
                return
            since = int(newest) - int(args.classes) + 1
            season_units = _hasColumn(cur, "athlete_season", "division")
            cur.execute("SELECT to_regclass('public.school_unit') AS t")
            if cur.fetchone()["t"] is None:
                print("  school_unit missing -- divisions will be blank (run 10d).")
            print(f"  classes {since}..{newest} (stored years), "
                  f"season unit columns: {'yes' if season_units else 'no'}")
            cur.execute(recruitSql(season_units), {"since": since, "min_races": DEFAULT_FLOOR})
            raw = [dict(r) for r in cur.fetchall()]
        conn.rollback()
        print(f"  first college seasons: {len(raw):,}")

        factors = _factors()
        print(f"  HS-scale factors: {factors}")
        rows, dropped, gains = shapeRows(raw, factors)
        print(f"  recruits kept: {len(rows):,}  dropped: {dropped}")
        linked = sum(1 for r in rows if r["source"] == "hs")
        print(f"  linked to a high-school season: {linked:,} of {len(rows):,}")
        for (g, sp), (gain, n) in sorted(gains.items()):
            note = "" if n >= MIN_GAIN_PAIRS else f"  (under {MIN_GAIN_PAIRS} pairs: 0 applied)"
            print(f"  freshman gain {g}/{sp}: {gain:+.2f} over {n:,} pairs{note}")
        if args.dry_run or not rows:
            if not rows:
                print("  NOTHING TO WRITE -- college_recruit left as it was.")
            return

        with conn.cursor() as cur:
            cur.execute(_DDL)
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO college_recruit_new ({', '.join(_COLS)}) VALUES %s",
                [tuple(r[c] for c in _COLS) for r in rows], page_size=5000)
            cur.execute("CREATE INDEX idx_college_recruit_new_school "
                        "ON college_recruit_new (school, state)")
            cur.execute("CREATE INDEX idx_college_recruit_new_pool "
                        "ON college_recruit_new (gender, sport, recruit_rating DESC)")
            for (g, sp), (gain, n) in gains.items():
                pool = f"college_{g}"
                cur.execute("""INSERT INTO college_recruit_meta_new
                               (gender, sport, gain, n_pairs, factor, since_year)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (g, sp, gain, n, factors.get(pool), since))
            conn.commit()
            swapTable(conn, "college_recruit", renames=[
                ("college_recruit_new_pkey", "college_recruit_pkey"),
                ("idx_college_recruit_new_school", "idx_college_recruit_school"),
                ("idx_college_recruit_new_pool", "idx_college_recruit_pool")])
            swapTable(conn, "college_recruit_meta", renames=[
                ("college_recruit_meta_new_pkey", "college_recruit_meta_pkey")])
    print(f"  college_recruit: {len(rows):,} rows written.")


if __name__ == "__main__":
    main()
