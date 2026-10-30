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
from us_state import isFiftyState                       # noqa: E402
from build_college_directory import loadDirectory, lookup, normName  # noqa: E402
from school_identity import loadLabels, teamState, assignedStates  # noqa: E402

# ★ FOUR CLASSES AND THE SENIOR SEASON (owner, 2026-10-30: the simpler rule).
#   A recruit's number is their high-school season in the sport the year
#   before college -- the senior season -- and nothing else; recruiting.py
#   counts only those (source 'hs').
CLASSES = 4              # recruiting classes kept, newest first
MIN_GAIN_PAIRS = 50      # linked pairs before the freshman gain is trusted
HS_LOOKBACK = 1          # stored years before the first college season: the senior season
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
    """The one query: first college seasons and their last HS season. The
    states are the seasons' own (the venues' mode) and the units the
    season's; resolveRows makes both the institution's. season_units says
    whether athlete_season carries the unit columns (older databases do
    not)."""
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
               p.state AS state,
               hs.mean_rating AS hs_rating, hs.year AS hs_year,
               hs.school AS hs_school, hs.state AS hs_state,
               p.row_division AS division, p.row_conference AS conference,
               p.row_region AS region
        FROM   pick p
        LEFT JOIN hs ON hs.person_id = p.person_id AND hs.sport = p.sport AND hs.rn = 1
    """
    # %(min_races)s is bound by the caller: floorSql reads it


# ★ WHICH SCHOOL, BY THE BOARDS' OWN RULE (owner, 2026-10-29: "in the
#   recruiting page the schools aren't correct (same school state thing)").
#   The state used to be the name's PRIMARY identity -- the biggest cluster
#   of athletes wearing the string, whatever their level -- so a college
#   whose name a bigger high school shares was filed in the high school's
#   state (Williams College as CA, Amherst College as WI) and two colleges
#   sharing a name merged into one row; the units came from whichever
#   college row of the name had the most votes, any state. Now:
#     the college   school_identity.teamState with the college pool -- the
#                   directory's state for the name, settled by the
#                   season's own state where the name is two colleges --
#                   the same verdict build_team_season keys the boards on;
#     the HS        the identity's assignment of THIS athlete to that
#                   school (school_athlete_state, else their home state),
#                   else teamState with the HS pool;
#     the units     school_unit's college row for that (school, state),
#                   this sport's first; the season's own units when the
#                   name has no college row there.
def collegeUnits(cur):
    """{(school, state): [(sport, division, conference, region), ...]}
    from school_unit's college rows, the most-voted first; {} when the
    table is missing."""
    cur.execute("SELECT to_regclass('public.school_unit') AS t")
    row = cur.fetchone()
    if (row["t"] if isinstance(row, dict) else row[0]) is None:
        return {}
    cur.execute("""SELECT school, state, sport, division, conference, region
                   FROM   school_unit WHERE is_college
                   ORDER  BY school, state, votes DESC NULLS LAST""")
    out = {}
    for r in cur.fetchall():
        r = tuple(r.values()) if isinstance(r, dict) else tuple(r)
        out.setdefault((r[0], r[1]), []).append(r[2:])
    return out


def pickUnit(units, school, state, sport):
    """(division, conference, region) for one college, or None."""
    rows = units.get((school, state)) or []
    for r in rows:
        if r[0] == sport:
            return r[1:]
    return rows[0][1:] if rows else None


def resolveRows(raw, team_state=None, assigned=None, units=None):
    """Stamp each raw row's college state, HS state, units and
    unit_college in place (see the note above). team_state is
    school_identity.teamState (None: states stay as stored); assigned is
    assignedStates' {(school, person_id): state}. Returns raw."""
    assigned = assigned or {}
    units = units or {}
    college_names = {sc for sc, _st in units}
    for r in raw:
        pool = r["pool"].split("|", 1)[0]
        if team_state and r.get("school"):
            r["state"] = team_state(r["school"], pool, r.get("state"))
        if r.get("hs_school"):
            st = assigned.get((r["hs_school"], r["person_id"]))
            if not st and team_state:
                st = team_state(r["hs_school"], "hs_" + pool.rsplit("_", 1)[-1], r.get("hs_state"))
            r["hs_state"] = st or r.get("hs_state")
        u = pickUnit(units, r.get("school"), r.get("state"), r.get("sport"))
        if u:
            r["division"], r["conference"], r["region"] = (
                u[0] or r.get("division"), u[1] or r.get("conference"), u[2] or r.get("region"))
        r["unit_college"] = u is not None or r.get("school") in college_names
    return raw


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


# * A COLLEGE, NOT MERELY A COLLEGE-POOLED SEASON (owner, 2026-09-17:
#   "recruit page has non-college teams"). The pool is the engine's verdict
#   about an ATHLETE, and it is sometimes wrong about a school: a post-grad
#   academy, a prep team, a road club and a plain high school whose runners
#   were mispooled all arrived here and got a recruiting profile. The old
#   gate asked only "is this a team name, and does it not say club", which
#   none of those fail.
#
# ! TWO WITNESSES, EITHER WILL DO. The college directory (the same one
#   school_identity resolves a college's state with) is authoritative but
#   not complete -- it thins out at NAIA and NJCAA -- so school_unit's own
#   is_college verdict, which is voted from championship meets, is accepted
#   as well. Requiring BOTH would drop real small colleges; requiring
#   neither is where we were.
def isCollege(school, state, directory, unit_college=False):
    if not school:
        return False
    if unit_college:
        return True
    return bool(directory) and lookup(directory, school, state) is not None


def keepSchool(school, state=None, directory=None, unit_college=False):
    if not (school and isTeamName(school) and "club" not in school.lower()):
        return False
    # ⚠ directory=None MEANS "NOT ASKED", NOT "NOTHING MATCHES". Callers that
    #   have no directory (tests, an old database) keep the pre-2026-09-17
    #   behaviour rather than silently dropping every school on earth.
    if directory is None and not unit_college:
        return True
    return isCollege(school, state, directory, unit_college)


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


def shapeRows(raw, factors, directory=None):
    """The database rows -> the table's rows, with the HS-scale numbers
    filled and the non-recruits dropped. Returns (rows, dropped) where
    dropped counts by reason."""
    dropped = {"grade": 0, "school": 0, "not_college": 0,
               "foreign": 0, "no_scale": 0}
    rows = []
    for r in raw:
        pool = r["pool"].split("|", 1)[0]
        if not (r["school"] and isTeamName(r["school"])
                and "club" not in r["school"].lower()):
            dropped["school"] += 1
            continue
        if not keepSchool(r["school"], r.get("state"), directory,
                          bool(r.get("unit_college"))):
            dropped["not_college"] += 1
            continue
        # ★ THE FIFTY STATES, ON BOTH ENDS (owner, 2026-09-17). The college
        #   must be American and, when we know where the recruit went to
        #   high school, so must they. A recruit with no linked HS season
        #   has no hs_state to judge, and is kept on the college's state
        #   alone rather than guessed at.
        if not isFiftyState(r.get("state")):
            dropped["foreign"] += 1
            continue
        if r.get("hs_state") and not isFiftyState(r.get("hs_state")):
            dropped["foreign"] += 1
            continue
        if not isRecruitSeason(r.get("grade"), pool):
            dropped["grade"] += 1
            continue
        factor = factors.get(pool)
        hs_equiv = None
        if factor:
            from pool_view import toHs
            hs_equiv = toHs(r["first_rating"], pool) or float(r["first_rating"]) * factor
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


# ★ TRANSFERS (owner, 2026-10-29: "can we also have stats for transfers?
#   so it's not just a hs page"). The other way onto a college roster. A
#   transfer is the season an athlete FIRST appears at a second college
#   after seasons at a first, in one sport: stints of one institution in
#   year order, and each change of institution is one row -- from the last
#   season at the old school to the first at the new. Ratings stay on the
#   college scale (both ends are college seasons). An institution is the
#   directory's own name where it knows the spelling ("Wake Forest" and
#   "Wake Forest University" are one), else the normalised string, with
#   the state teamState gives it.
# ! NOT A TRANSFER, all three being how a linking error looks:
#     - an athlete who goes BACK to a school (A, B, A): a name variant the
#       directory missed, or two people on one id -- the whole history is
#       dropped, not just the return;
#     - a "new" school in the same stored year as the old one (two schools
#       in one season is a mislink, not a move);
#     - either school failing the recruits' own college gate.
def transferSql(season_units):
    own = ("s.division AS row_division, s.conference AS row_conference, s.region AS row_region"
           if season_units else
           "NULL::text AS row_division, NULL::text AS row_conference, NULL::text AS row_region")
    return f"""
        SELECT s.person_id, s.pool, s.sport, s.year, s.school, s.state, s.grade,
               s.mean_rating, s.n_races, {own}
        FROM   athlete_season s
        WHERE  s.pool IN ('college_m', 'college_f')
          AND  s.mean_rating IS NOT NULL AND s.school IS NOT NULL
          AND  s.year >= %(from_year)s
          AND  {floorSql(False, 's')}
    """


def stints(seasons, inst):
    """[(institution, [season, ...]), ...] in year order: one person's
    seasons in one sport, consecutive seasons at one institution merged."""
    out = []
    for se in sorted(seasons, key=lambda x: x["year"]):
        k = inst(se)
        if out and out[-1][0] == k:
            out[-1][1].append(se)
        else:
            out.append((k, [se]))
    return out


def findTransfers(seasons, inst):
    """[(last season at the old school, first season at the new)] for one
    person and sport; [] when the history goes back to a school."""
    st = stints(seasons, inst)
    keys = [k for k, _ in st]
    if len(set(keys)) != len(keys):
        return []
    out = []
    for (_ka, a), (_kb, b) in zip(st, st[1:]):
        if b[0]["year"] > a[-1]["year"]:
            out.append((a[-1], b[0]))
    return out


def shapeTransfers(raw, since, inst, gate=None):
    """The college seasons -> the transfer rows whose arrival is in the
    classes kept. gate(season) says whether a season's school is a college
    (None: every school is). Returns (rows, dropped)."""
    by = {}
    for r in raw:
        by.setdefault((r["person_id"], r["sport"]), []).append(r)
    dropped = {"back": 0, "same_year": 0, "not_college": 0, "foreign": 0, "old": 0}
    rows = []
    for (_pid, _sp), seasons in by.items():
        st = stints(seasons, inst)
        if len(st) < 2:
            continue
        keys = [k for k, _ in st]
        if len(set(keys)) != len(keys):
            dropped["back"] += 1
            continue
        pairs = findTransfers(seasons, inst)
        dropped["same_year"] += len(st) - 1 - len(pairs)
        for a, b in pairs:
            if int(b["year"]) < since:
                dropped["old"] += 1
                continue
            if gate and not (gate(a) and gate(b)):
                dropped["not_college"] += 1
                continue
            if not isFiftyState(b.get("state")):
                dropped["foreign"] += 1
                continue
            pool = b["pool"].split("|", 1)[0]
            rows.append({
                "person_id": b["person_id"], "sport": b["sport"],
                "gender": pool.rsplit("_", 1)[-1], "year": b["year"], "grade": b.get("grade"),
                "school": b["school"], "state": b.get("state"),
                "to_rating": float(b["mean_rating"]), "n_races": b["n_races"],
                "from_school": a["school"], "from_state": a.get("state"),
                "from_year": a["year"], "from_rating": float(a["mean_rating"]),
                "from_division": a.get("division"),
                "division": b.get("division"), "conference": b.get("conference"),
                "region": b.get("region"),
            })
    return rows, dropped


_T_DDL = """
DROP TABLE IF EXISTS college_transfer_new;
CREATE TABLE college_transfer_new (
    person_id     bigint  NOT NULL,
    sport         text    NOT NULL,
    gender        text    NOT NULL,
    year          integer NOT NULL,
    grade         text,
    school        text    NOT NULL,
    state         text,
    to_rating     real    NOT NULL,
    n_races       integer NOT NULL,
    from_school   text    NOT NULL,
    from_state    text,
    from_year     integer NOT NULL,
    from_rating   real    NOT NULL,
    from_division text,
    division      text,
    conference    text,
    region        text,
    CONSTRAINT college_transfer_new_pkey PRIMARY KEY (person_id, sport, year)
)"""

_T_COLS = ("person_id", "sport", "gender", "year", "grade", "school", "state", "to_rating",
           "n_races", "from_school", "from_state", "from_year", "from_rating", "from_division",
           "division", "conference", "region")


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
            # ! RATED SEASONS ONLY: `newest` sets the whole build's window
            #   (since = newest - classes + 1), so an unrated sprint year on
            #   the end would shift every class by one.
            cur.execute("""SELECT max(year) AS y FROM athlete_season
                           WHERE pool IN ('college_m', 'college_f')
                             AND mean_rating IS NOT NULL""")
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
            cur.execute(transferSql(season_units),
                        {"from_year": since - int(args.classes), "min_races": DEFAULT_FLOOR})
            t_raw = [dict(r) for r in cur.fetchall()]
            units = collegeUnits(cur)
            loadLabels(getConn)
            assigned = assignedStates(cur, [(r["hs_school"], r["person_id"])
                                            for r in raw if r.get("hs_school")])
        conn.rollback()
        print(f"  first college seasons: {len(raw):,}; college seasons for transfers: "
              f"{len(t_raw):,}; college unit rows: {len(units):,}")
        resolveRows(raw, teamState, assigned, units)

        with conn.cursor() as cur:
            directory = loadDirectory(cur, "state")
            names = loadDirectory(cur, "name")
        conn.rollback()
        if not directory:
            print("  college_directory missing -- the college gate falls back "
                  "to school_unit alone (run the directory build).")
        else:
            print(f"  college directory: {len(directory):,} names")

        factors = _factors()
        print(f"  HS-scale factors: {factors}")
        rows, dropped, gains = shapeRows(raw, factors, directory)
        print(f"  recruits kept: {len(rows):,}  dropped: {dropped}")
        linked = sum(1 for r in rows if r["source"] == "hs")
        print(f"  linked to a high-school season: {linked:,} of {len(rows):,}")
        for (g, sp), (gain, n) in sorted(gains.items()):
            note = "" if n >= MIN_GAIN_PAIRS else f"  (under {MIN_GAIN_PAIRS} pairs: 0 applied)"
            print(f"  freshman gain {g}/{sp}: {gain:+.2f} over {n:,} pairs{note}")

        resolveRows(t_raw, teamState, None, units)
        college_names = {sc for sc, _st in units}

        def inst(se):
            return ((names and lookup(names, se["school"], se.get("state")))
                    or normName(se["school"]), se.get("state"))

        def gate(se):
            return (isTeamName(se["school"]) and "club" not in se["school"].lower()
                    and keepSchool(se["school"], se.get("state"), directory,
                                   se["school"] in college_names))
        transfers, t_dropped = shapeTransfers(t_raw, since, inst, gate)
        print(f"  transfers kept: {len(transfers):,}  dropped: {t_dropped}")
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
            cur.execute(_T_DDL)
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO college_transfer_new ({', '.join(_T_COLS)}) VALUES %s "
                "ON CONFLICT DO NOTHING",
                [tuple(r[c] for c in _T_COLS) for r in transfers], page_size=5000)
            cur.execute("CREATE INDEX idx_college_transfer_new_to "
                        "ON college_transfer_new (school, state)")
            cur.execute("CREATE INDEX idx_college_transfer_new_from "
                        "ON college_transfer_new (from_school, from_state)")
            conn.commit()
            swapTable(conn, "college_transfer", renames=[
                ("college_transfer_new_pkey", "college_transfer_pkey"),
                ("idx_college_transfer_new_to", "idx_college_transfer_to"),
                ("idx_college_transfer_new_from", "idx_college_transfer_from")])
    print(f"  college_recruit: {len(rows):,} rows written; "
          f"college_transfer: {len(transfers):,}.")


if __name__ == "__main__":
    main()
