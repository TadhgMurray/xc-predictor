"""
build_record_books.py -- precompute the history pages (record_books.py).

    python racecast/build_record_books.py                 # incremental
    python racecast/build_record_books.py --force         # everything again
    python racecast/build_record_books.py --only state,otd
    python racecast/build_record_books.py --max-schools 500

Run from the PROJECT ROOT after 11_teams (team_season) -- the pipeline runs
it in the BACKGROUND (step 13h, bgstep), off the main chain: nothing else
reads record_books, so nothing waits on it.

★ FOUR PARTS, ONE TABLE (owner, 2026-10-10: "history and record books").
    state   /records/<state>: per (sport, pool, state) the top 100 athletes
            by best single race, the top 100 by best season, the best
            seasons per grade, and the all-time best team seasons.
    school  /school/<name>/records and the school page's Record book tab:
            top 10 per event (school_prs.schoolPrData, extended -- not a
            second PR engine), best by grade, most improved, best team
            seasons.
    course  the course page's records by decade and the record progression.
    otd     "On this day": the best-rated past race on each calendar day,
            per state and nationally.
  Each is one row per page in record_books (kind, key) -> ctx jsonb; the
  routes read one primary key.

★ INCREMENTAL, TWO WAYS (2026-10-10).
    state / course / otd are bulk passes, so they are gated as a whole on
      their inputs' SIGNATURE: the source tables' oids (a step-10 rebuild
      swaps in a new table, so a new oid) and their insert/update/delete
      counters. Nothing changed since the last build -> the part is skipped.
    school is per key: one GROUP BY over ranking_results gives every
      (school, sport) a fingerprint (rows, newest result id, rating sum); a
      school is rebuilt only when its fingerprint moved, biggest first, at
      most --max-schools per run. The rest wait for the next run, and their
      page says the record book is being compiled -- never a live scan.

! THE BOARDS' FILTERS, REUSED, NEVER RETYPED. Rows come from ranking_results
  and athlete_season, which already leave out condemned races
  (impossible_result), twins (result_twin), pro pools (isRankablePool) and
  rating outliers (build_ranking_results._outlierClause). The slice on top
  -- pool, sport, state, the US-only scope that keeps foreign athletes off
  US boards, the race-count floor -- is rankings._whereClauses fed by
  rankings.parseFilters, season_floor.floorSql, rankings.gradeKeySql and
  teams.parseFilters / teams.getTeamRankings: the same text the live
  boards run. tests/test_record_books.py pins that.

! THE STATE IS THE ATHLETE'S HOME STATE, not the meet's: athlete_season's
  state (build_ranking_results._homeStates moved it to the school's), the
  landing boards' own rule. A Michigan runner's PR at Arcadia is Michigan's.
"""

import argparse
import datetime
import json
import sys
import time
from decimal import Decimal

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import record_books as RB

# ★ bump when a ctx shape changes: every part's signature carries it, so the
#   next run rebuilds what the new templates expect
SIG_VERSION = "rb1"

_DDL = """
CREATE TABLE IF NOT EXISTS record_books (
    kind     text        NOT NULL,
    key      text        NOT NULL,
    ctx      jsonb       NOT NULL,
    -- the per-key fingerprint (school part) or the part's signature
    sig      text,
    built_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS record_books_meta (
    part     text        PRIMARY KEY,
    sig      text,
    n_rows   int,
    built_at timestamptz NOT NULL DEFAULT now()
);
"""

# what each bulk part reads; its signature is these tables'
PART_TABLES = {
    "state":  ("ranking_results", "athlete_season", "team_season"),
    "course": ("ranking_results", "meets"),
    "otd":    ("ranking_results",),
}

_RACE_KEYS = ("person_id", "name", "school", "state", "grade", "pool",
              "sport", "year", "rating", "result_id", "meet_id", "div_id",
              "event_id", "race_date", "time_seconds", "distance",
              "meet_name")
_SEASON_KEYS = ("person_id", "name", "school", "state", "grade", "grade_key",
                "pool", "sport", "year", "rating", "n_races", "best",
                "prev_rating", "prev_year", "gain")
_TEAM_KEYS = ("school", "state", "pool", "sport", "year", "rank", "points",
              "n_athletes", "top5_mean", "best_rating", "best_person_id",
              "best_name", "scope")


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:10]
    return v


def slim(row, keys):
    """The fields a page shows, JSON-plain (Decimal -> float, date -> ISO)."""
    out = {}
    for k in keys:
        if k in row and row[k] is not None:
            v = row[k]
            out[k] = slim(v, _RACE_KEYS) if isinstance(v, dict) else _plain(v)
    return out


def _json(ctx):
    # ⚠ ensure_ascii=False: the cluster is SQL_ASCII (build_course_boards'
    #   note) -- an escaped accent in a name kills the INSERT
    from database import _Utf8Json
    return _Utf8Json(ctx, dumps=lambda o: json.dumps(o, default=str,
                                                     ensure_ascii=False))


# ------------------------------------------------------------------ #
#  THE BOARDS' FILTERS
# ------------------------------------------------------------------ #

def boardFilters(board, sport, pool, states=None, scope="usa", gender=None,
                 school=None):
    """A rankings filter dict from rankings.parseFilters -- the live board's
    own parser -- for one slice. pool='all' (+ gender) is the PR board's
    any-level shape, set after parsing because only that board accepts it.
    min_races is EXPLICIT: an all-time list holds the open season to the
    floor too (a one-race 2026 "season" is not a record)."""
    from rankings import parseFilters, POOLS
    args = {"board": board, "sport": sport,
            "pool": pool if pool in POOLS else "hs_m",
            "scope": scope, "min_races": "3", "limit": "200"}
    if states:
        args["state"] = ",".join(states)
    if school:
        args["school"] = school
    f, err = parseFilters(args)
    if err:
        raise ValueError(err)
    if pool == "all":
        f["pool"] = "all"
        f["gender"] = gender
    if school:
        f["school"] = [school]           # a name may hold a comma
    return f


def whereFor(f, params, with_dates):
    from rankings import _whereClauses
    return _whereClauses(f, params, with_dates)


# the athlete's HOME state on every race row (module docstring)
_HOME_SRC = """
        SELECT r.sport, r.result_id, r.person_id, r.pool, r.speed_rating,
               r.race_date, r.year, COALESCE(s.state, r.state) AS state,
               r.school, r.grade, r.meet_id, r.div_id, r.event_id,
               r.time_seconds, r.distance
        FROM   ranking_results r
        LEFT JOIN athlete_season s
               ON s.person_id = r.person_id AND s.pool = r.pool
              AND s.sport = r.sport AND s.year = r.year
        WHERE  r.pool = %(pool)s AND r.sport = %(sport)s
          AND  r.speed_rating IS NOT NULL"""


def stateRaceSql(f, n=RB.LIST_N):
    """Each athlete's best single race, top `n` per home state."""
    params = {"n": n}
    where = whereFor(f, params, True)
    return f"""
    WITH src AS ({_HOME_SRC}
    ),
    best AS (
        SELECT DISTINCT ON (person_id, state) *
        FROM   src
        WHERE  TRUE {where}
        ORDER  BY person_id, state, speed_rating DESC, result_id
    ),
    ranked AS (
        SELECT *, row_number() OVER (PARTITION BY state
                                     ORDER BY speed_rating DESC, result_id) AS rn
        FROM   best
    )
    SELECT sport, result_id, person_id, pool, speed_rating AS rating,
           to_char(race_date, 'YYYY-MM-DD') AS race_date, year, state,
           school, grade, meet_id, div_id, event_id, time_seconds, distance, rn
    FROM   ranked
    WHERE  rn <= %(n)s
    ORDER  BY state, rn""", params


def seasonSql(f, n, grades=None, partition="state"):
    """Each athlete's best season (athlete_season.mean_rating, the board's
    number), top `n` per `partition` -- and per grade when `grades` is
    given (the grade read through rankings.gradeKeySql, the board's own
    "Sr" == "12" == "SR-4"). The floor is season_floor.floorSql, explicit."""
    from rankings import gradeKeySql
    from season_floor import floorSql
    params = {"n": n, "min_races": f["min_races"]}
    where = whereFor(f, params, False)
    gk = ", grade_key" if grades else ""
    tail = ""
    if grades:
        params["grades"] = list(grades)
        tail = " AND grade_key = ANY(%(grades)s)"
    part = f"{partition}{gk}"
    return f"""
    WITH g AS (
        SELECT s.person_id, s.pool, s.sport, s.year,
               s.mean_rating AS rating, s.n_races, s.state, s.school,
               s.grade, {gradeKeySql('s')} AS grade_key
        FROM   athlete_season s
        WHERE  {floorSql(True)} {where}
    ),
    best AS (
        SELECT DISTINCT ON (person_id, {part}) *
        FROM   g
        ORDER  BY person_id, {part}, rating DESC, year
    ),
    ranked AS (
        SELECT *, row_number() OVER (PARTITION BY {part}
                                     ORDER BY rating DESC, person_id) AS rn
        FROM   best
    )
    SELECT * FROM ranked WHERE rn <= %(n)s{tail}
    ORDER  BY {part}, rn""", params


def schoolSeasonSql(f):
    """Every floored season one school has, for the grade and improvement
    lists (both are cut in Python: record_books.gradeBests / mostImproved)."""
    from rankings import gradeKeySql
    from season_floor import floorSql
    params = {"min_races": f["min_races"]}
    where = whereFor(f, params, False)
    return f"""
        SELECT s.person_id, s.pool, s.sport, s.year,
               s.mean_rating AS rating, s.n_races, s.state, s.school,
               s.grade, {gradeKeySql('s')} AS grade_key
        FROM   athlete_season s
        WHERE  {floorSql(True)} {where}""", params


def courseSql(f):
    """Per (course, distance): the record progression and each decade's
    three fastest, one gender at a time (f carries pool='all' + gender).

    ⚠ anet rows only (result_id > 0): `meets` is keyed in the anet id
      space, and a tfrrs meet id that collides with it would put a college
      race on the wrong course -- school_prs.runningSql's caution."""
    params = {"dn": RB.DECADE_N}
    where = whereFor(f, params, True)
    return f"""
    WITH rr AS (
        SELECT * FROM ranking_results
        WHERE  time_seconds > 0 AND time_seconds < 86400
          AND  distance IS NOT NULL AND result_id > 0 {where}
    ),
    src AS (
        SELECT m.course_name AS course, round(rr.distance)::int AS dist,
               rr.person_id, rr.result_id, rr.time_seconds,
               rr.speed_rating AS rating, rr.pool, rr.sport,
               to_char(rr.race_date, 'YYYY-MM-DD') AS race_date, rr.year,
               rr.school, rr.state, rr.grade, rr.meet_id, rr.div_id,
               count(*) OVER (PARTITION BY m.course_name,
                                           round(rr.distance)::int) AS n
        FROM   rr
        JOIN   meets m ON m.meet_id = rr.meet_id AND m.div_id = rr.div_id
        WHERE  NULLIF(btrim(m.course_name), '') IS NOT NULL
    ),
    prog AS (
        SELECT *, min(time_seconds) OVER (
                      PARTITION BY course, dist
                      ORDER BY race_date, time_seconds, result_id
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev
        FROM   src
    ),
    dec1 AS (
        SELECT DISTINCT ON (course, dist, left(race_date, 3), person_id) *
        FROM   src
        ORDER  BY course, dist, left(race_date, 3), person_id, time_seconds
    ),
    dec2 AS (
        SELECT *, row_number() OVER (PARTITION BY course, dist,
                                                  left(race_date, 3)
                                     ORDER BY time_seconds, result_id) AS dn
        FROM   dec1
    )
    SELECT 'p' AS part, course, dist, n, person_id, result_id, time_seconds,
           rating, pool, sport, race_date, year, school, state, grade,
           meet_id, div_id
    FROM   prog WHERE prev IS NULL OR time_seconds < prev
    UNION ALL
    SELECT 'd', course, dist, n, person_id, result_id, time_seconds,
           rating, pool, sport, race_date, year, school, state, grade,
           meet_id, div_id
    FROM   dec2 WHERE dn <= %(dn)s""", params


def otdSql(f):
    """The best-rated races on every calendar day, one per (day, state,
    year) and then the top few per (day, state). US scope by the board's
    own clause; the state here is the MEET's -- "on this day in
    California" is a race run there."""
    params = {"n": RB.OTD_N}
    where = whereFor(f, params, True)
    return f"""
    WITH c AS (
        SELECT sport, result_id, person_id, pool, speed_rating AS rating,
               to_char(race_date, 'YYYY-MM-DD') AS race_date,
               to_char(race_date, 'MM-DD') AS md, year, state, school, grade,
               meet_id, div_id, event_id, time_seconds, distance,
               row_number() OVER (PARTITION BY to_char(race_date, 'MM-DD'),
                                               state, year
                                  ORDER BY speed_rating DESC, result_id) AS yn
        FROM   ranking_results
        WHERE  speed_rating IS NOT NULL AND time_seconds > 0 {where}
    ),
    t AS (
        SELECT *, row_number() OVER (PARTITION BY md, state
                                     ORDER BY rating DESC, result_id) AS rn
        FROM   c WHERE yn = 1
    )
    SELECT * FROM t WHERE rn <= %(n)s""", params


# ------------------------------------------------------------------ #
#  LOOKUPS FOR THE ROWS THAT DISPLAY
# ------------------------------------------------------------------ #

def _chunks(seq, n=20000):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fillNames(cur, rows):
    """name on every row with a person, in bulk: school_prs._athleteInfo,
    then the feed's own name where no profile has one (school._fillRowNames)."""
    from school_prs import _athleteInfo
    from school import _fillRowNames
    ids = {r["person_id"] for r in rows if r.get("person_id")}
    info = {}
    for part in _chunks(ids):
        info.update(_athleteInfo(cur, part))
    for r in rows:
        if r.get("person_id") and not r.get("name"):
            r["name"] = (info.get(r["person_id"]) or {}).get("name")
    _fillRowNames(cur, rows)


def fillMeetNames(cur, rows):
    """meet_name per row: meets (XC) / meets_tf (TF) by meet id, tfrrs rows
    (negative result ids) from meets_tfrrs. A table that is not there is
    just no name."""
    want = {}
    for r in rows:
        if not r.get("meet_id"):
            continue
        src = ("meets_tfrrs" if (r.get("result_id") or 0) < 0
               else "meets" if r.get("sport") == "XC" else "meets_tf")
        want.setdefault(src, set()).add(r["meet_id"])
    names = {}
    for table, ids in want.items():
        for part in _chunks(ids):
            try:
                cur.execute("SAVEPOINT rb_meets")
                cur.execute(f"""SELECT DISTINCT ON (meet_id) meet_id, meet_name
                                FROM {table} WHERE meet_id = ANY(%s)
                                ORDER BY meet_id""", (part,))
                for m in cur.fetchall():
                    names[(table, m["meet_id"])] = m["meet_name"]
                cur.execute("RELEASE SAVEPOINT rb_meets")
            except Exception as exc:                    # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT rb_meets")
                print(f"  ! meet names from {table}: {type(exc).__name__}: {exc}",
                      flush=True)
    for r in rows:
        src = ("meets_tfrrs" if (r.get("result_id") or 0) < 0
               else "meets" if r.get("sport") == "XC" else "meets_tf")
        r["meet_name"] = names.get((src, r.get("meet_id")))


def fillBestRaces(cur, seasons, sport):
    """season['best'] = that season's best race (for its link), one bulk
    read of ranking_results by person."""
    ids = {s["person_id"] for s in seasons if s.get("person_id")}
    best = {}
    for part in _chunks(ids):
        cur.execute("""
            SELECT DISTINCT ON (person_id, pool, year)
                   person_id, pool, year, sport, result_id, meet_id, div_id,
                   event_id, to_char(race_date, 'YYYY-MM-DD') AS race_date,
                   time_seconds, distance, speed_rating AS rating
            FROM   ranking_results
            WHERE  sport = %s AND person_id = ANY(%s)
              AND  speed_rating IS NOT NULL
            ORDER  BY person_id, pool, year, speed_rating DESC, result_id
        """, (sport, part))
        for r in cur.fetchall():
            best[(r["person_id"], r["pool"], r["year"])] = dict(r)
    races = []
    for s in seasons:
        b = best.get((s.get("person_id"), s.get("pool"), s.get("year")))
        if b:
            s["best"] = b
            races.append(b)
    fillMeetNames(cur, races)


def teamRows(cur, sport, pool, state, school=None, n=RB.TEAM_N):
    """team_season through teams.parseFilters + getTeamRankings: the team
    board's all-time span for one state (or one school's seasons in it,
    ranked in that field)."""
    import teams as T
    from rankings import US_STATES
    args = {"sport": sport, "pool": pool, "limit": str(n), "sort": "rating"}
    if state and state in US_STATES:
        args["state"] = state
    if school:
        args["school"] = school
    f, err = T.parseFilters(args)
    if err:
        return []
    if school:
        f["school"] = [school]
    try:
        cur.execute("SAVEPOINT rb_teams")
        rows = [dict(r) for r in T.getTeamRankings(cur, f)]
        T.fillBestRunner(cur, rows)
        cur.execute("RELEASE SAVEPOINT rb_teams")
    except Exception as exc:                            # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT rb_teams")
        print(f"  ! teams {sport} {pool} {state} {school or ''}: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return []
    return rows


# ------------------------------------------------------------------ #
#  SIGNATURES (the incremental gate)
# ------------------------------------------------------------------ #

def tableSig(cur, tables):
    """'v|name:oid:ins:upd:del|...' -- changes when a table is swapped in
    (new oid) or written to. Statistics reset on a restart only costs one
    unneeded rebuild."""
    cur.execute("""
        SELECT c.relname, c.oid::bigint AS oid,
               COALESCE(s.n_tup_ins, 0) AS i, COALESCE(s.n_tup_upd, 0) AS u,
               COALESCE(s.n_tup_del, 0) AS d
        FROM   pg_class c
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE  c.relname = ANY(%s) AND c.relkind IN ('r', 'p')
          AND  c.relnamespace = 'public'::regnamespace
        ORDER  BY c.relname""", (list(tables),))
    parts = [f"{r['relname']}:{r['oid']}:{r['i']}:{r['u']}:{r['d']}"
             for r in cur.fetchall()]
    return "|".join([SIG_VERSION] + parts)


def lastSig(cur, part):
    cur.execute("SELECT sig FROM record_books_meta WHERE part = %s", (part,))
    row = cur.fetchone()
    return row["sig"] if row else None


def writePart(conn, cur, kind, rows, sig, replace=True):
    """rows: [(key, ctx)] or [(key, ctx, sig)]. replace=True swaps the whole
    kind in one transaction (readers see the old rows until the commit);
    False upserts (the per-key school part)."""
    import psycopg2.extras
    if replace:
        cur.execute("DELETE FROM record_books WHERE kind = %s", (kind,))
    for part in _chunks(rows, 500):
        psycopg2.extras.execute_values(cur, """
            INSERT INTO record_books (kind, key, ctx, sig) VALUES %s
            ON CONFLICT (kind, key) DO UPDATE
               SET ctx = EXCLUDED.ctx, sig = EXCLUDED.sig, built_at = now()
        """, [(kind, r[0], _json(r[1]), r[2] if len(r) > 2 else sig)
              for r in part])
    if replace:
        cur.execute("""
            INSERT INTO record_books_meta (part, sig, n_rows) VALUES (%s, %s, %s)
            ON CONFLICT (part) DO UPDATE
               SET sig = EXCLUDED.sig, n_rows = EXCLUDED.n_rows, built_at = now()
        """, (kind, sig, len(rows)))
    conn.commit()


# ------------------------------------------------------------------ #
#  THE PARTS
# ------------------------------------------------------------------ #

def _byState(rows):
    out = {}
    for r in rows:
        out.setdefault(r.get("state"), []).append(r)
    return out


def buildStates(conn, cur, levels, sports=("XC", "TF")):
    from rankings import US_STATES
    out = []
    for sport in sports:
        for level in levels:
            for g in ("m", "f"):
                pool = RB.poolFor(level, g)
                t0 = time.time()
                f_perf = boardFilters("performance", sport, pool, US_STATES)
                sql, p = stateRaceSql(f_perf)
                cur.execute(sql, p)
                races = [dict(r) for r in cur.fetchall()]
                f_ab = boardFilters("ability", sport, pool, US_STATES)
                sql, p = seasonSql(f_ab, RB.LIST_N)
                cur.execute(sql, p)
                seasons = [dict(r) for r in cur.fetchall()]
                grades = RB.LEVELS[level][2]
                sql, p = seasonSql(f_ab, RB.GRADE_N, grades=grades)
                cur.execute(sql, p)
                graded = [dict(r) for r in cur.fetchall()]
                fillNames(cur, races + seasons + graded)
                fillMeetNames(cur, races)
                fillBestRaces(cur, seasons + graded, sport)
                races_by, seasons_by = _byState(races), _byState(seasons)
                graded_by = _byState(graded)
                for st in US_STATES:
                    teams = teamRows(cur, sport, pool, st)
                    ctx = {
                        "sport": sport, "pool": pool, "level": level,
                        "gender": g, "state": st,
                        "races": [slim(r, _RACE_KEYS)
                                  for r in races_by.get(st, [])],
                        "seasons": [slim(r, _SEASON_KEYS)
                                    for r in seasons_by.get(st, [])],
                        "grades": [(gk, words, [slim(r, _SEASON_KEYS)
                                                for r in rows_g])
                                   for gk, words, rows_g in RB.gradeBests(
                                       graded_by.get(st, []), grades,
                                       RB.GRADE_N)],
                        "teams": [slim(t, _TEAM_KEYS) for t in teams],
                    }
                    if ctx["races"] or ctx["seasons"] or ctx["teams"]:
                        out.append((RB.stateKey(sport, pool, st), ctx))
                print(f"  state {sport} {pool}: {len(races):,} races, "
                      f"{len(seasons):,} seasons, {len(graded):,} graded "
                      f"[{time.time() - t0:.0f}s]", flush=True)
    return out


def buildCourses(conn, cur):
    by = {}
    for g in ("m", "f"):
        t0 = time.time()
        f = boardFilters("performance", "XC", "all", scope="all", gender=g)
        sql, p = courseSql(f)
        cur.execute(sql, p)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["gender"] = g.upper()
            by.setdefault(r["course"], []).append(r)
        print(f"  course {g}: {len(rows):,} rows over "
              f"{len({r['course'] for r in rows}):,} courses "
              f"[{time.time() - t0:.0f}s]", flush=True)
    allrows = [r for rows in by.values() for r in rows]
    fillNames(cur, allrows)
    for r in allrows:
        r["sport"] = "XC"
    fillMeetNames(cur, allrows)
    out = []
    for course, rows in by.items():
        ctx = courseCtx(course, rows)
        if ctx["dists"]:
            out.append((RB.courseKey(course), ctx))
    return out


def courseCtx(course, rows, min_n=10):
    """One course's stored ctx from courseSql's rows: its most-raced
    distances (RB.COURSE_DISTS), each with a progression and decade bests
    per gender. Pure; the test drives it."""
    n_by = {}
    for r in rows:
        n_by[r["dist"]] = max(n_by.get(r["dist"], 0), int(r.get("n") or 0))
    dists = [d for d, n in sorted(n_by.items(), key=lambda kv: (-kv[1], kv[0]))
             if n >= min_n][:RB.COURSE_DISTS]
    out = []
    for d in dists:
        genders = {}
        for g in ("M", "F"):
            mine = [r for r in rows if r["dist"] == d and r["gender"] == g]
            prog = RB.progression([r for r in mine if r["part"] == "p"])
            decs = RB.decadeBests([r for r in mine if r["part"] == "d"])
            if prog or decs:
                genders[g] = {
                    "progression": [slim(r, _RACE_KEYS + ("by", "until"))
                                    for r in prog],
                    "decades": [(dec, [slim(r, _RACE_KEYS) for r in rs])
                                for dec, rs in decs],
                }
        if genders:
            out.append({"dist": d, "label": RB.distanceLabel(d),
                        "n": n_by[d], "genders": genders})
    return {"course": course, "dists": out}


def buildOtd(conn, cur):
    """[(key, ctx)] for every (MM-DD, state) and (MM-DD, US)."""
    cands = {}
    for sport in ("XC", "TF"):
        for pool in ("hs_m", "hs_f"):
            t0 = time.time()
            f = boardFilters("performance", sport, pool)
            sql, p = otdSql(f)
            cur.execute(sql, p)
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                # ! a NULL state (a tfrrs row) is national only, never a
                #   second copy under the national key
                if r.get("state"):
                    cands.setdefault((r["md"], r["state"]), []).append(r)
                cands.setdefault((r["md"], None), []).append(r)
            print(f"  otd {sport} {pool}: {len(rows):,} candidates "
                  f"[{time.time() - t0:.0f}s]", flush=True)
    keep = {}
    for key, rows in cands.items():
        keep[key] = otdKeep(rows)
    allrows = [r for rows in keep.values() for r in rows]
    fillNames(cur, allrows)
    fillMeetNames(cur, allrows)
    return [(RB.otdKey(md, st), {"rows": [slim(r, _RACE_KEYS) for r in rows]})
            for (md, st), rows in keep.items() if rows]


def otdKeep(rows, n=RB.OTD_N):
    """Best first, one per year (so a page can always find a PAST year),
    one per athlete, `n` kept."""
    rows = sorted(rows, key=lambda r: -(r.get("rating") or 0))
    seen_y, seen_p, out = set(), set(), []
    for r in rows:
        y = str(r.get("race_date") or "")[:4]
        if y in seen_y or r.get("person_id") in seen_p:
            continue
        seen_y.add(y)
        seen_p.add(r.get("person_id"))
        out.append(r)
        if len(out) >= n:
            break
    return out


# ---- schools ------------------------------------------------------ #

def schoolFingerprints(cur):
    """{(school, sport): (fingerprint, n_rows)} in one pass."""
    cur.execute("""
        SELECT school, sport, count(*) AS n, max(result_id) AS mx,
               round(sum(COALESCE(speed_rating, 0))::numeric, 1) AS sm
        FROM   ranking_results
        WHERE  NULLIF(btrim(school), '') IS NOT NULL
        GROUP  BY school, sport""")
    return {(r["school"], r["sport"]): (f"{SIG_VERSION}:{r['n']}:{r['mx']}:{r['sm']}",
                                         int(r["n"]))
            for r in cur.fetchall()}


def schoolClusters(cur):
    """{school: [(state, is_primary)]} from school_identity; {} before it."""
    cur.execute("SELECT to_regclass('public.school_identity') AS t")
    if cur.fetchone()["t"] is None:
        return {}
    cur.execute("SELECT school, state, is_primary FROM school_identity")
    out = {}
    for r in cur.fetchall():
        out.setdefault(r["school"], []).append((r["state"], bool(r["is_primary"])))
    return out


def changedSchools(fps, stored, clusters, is_team, cap):
    """[(school, sport, state, primary, fp)] to rebuild, biggest first.
    A key is stale when its stored sig differs from the fingerprint (or it
    was never built). Pure; the test drives it."""
    todo = []
    for (school, sport), (fp, n) in fps.items():
        if not is_team(school):
            continue
        cl = clusters.get(school) or [(None, True)]
        primary = next((s for s, p in cl if p), cl[0][0])
        for state, _p in cl:
            key = RB.schoolKey(school, state, sport)
            if stored.get(key) != fp:
                todo.append((n, school, sport, state, primary, fp))
    todo.sort(key=lambda t: (-t[0], t[1], t[2], t[3] or ""))
    if cap:
        todo = todo[:cap]
    return [t[1:] for t in todo]


def schoolBook(cur, school, state, primary, sport):
    """One school's record book ctx (module docstring, part 'school')."""
    from school_prs import schoolPrData
    from rankings import POOLS
    data = schoolPrData(cur, school, sport, per_table=RB.SCHOOL_EVENT_N,
                        state=state, primary=primary)
    events = []
    for sec in data["sections"]:
        tables = {g: [slim(r, _RACE_KEYS + ("speed_rating", "mark", "date",
                                            "course_name", "event_short"))
                      for r in sec["tables"][g]] for g in ("M", "F")}
        if tables["M"] or tables["F"]:
            ev = {k: sec.get(k) for k in ("label", "dist_note", "kind",
                                          "distance", "on_board", "n")}
            ev["tables"] = tables
            events.append(ev)

    f = boardFilters("ability", sport, "all", scope="all" if not state else "usa",
                     states=[state] if state else None, school=school)
    sql, p = schoolSeasonSql(f)
    cur.execute(sql, p)
    seasons = [dict(r) for r in cur.fetchall()]
    fillNames(cur, seasons)

    by_pool = {}
    for s in seasons:
        by_pool.setdefault(s.get("pool"), []).append(s)
    grades, improved, teams = [], [], []
    for pool in sorted(by_pool, key=_poolSort):
        level = (pool or "").split("_")[0]
        if level not in RB.LEVELS:
            continue
        gb = RB.gradeBests(by_pool[pool], RB.LEVELS[level][2], RB.SCHOOL_GRADE_N)
        if gb:
            grades.append({"pool": pool, "grades": gb})
        mi = RB.mostImproved(by_pool[pool])
        if mi:
            improved.append({"pool": pool, "rows": mi})
        if pool in POOLS:
            t = teamRows(cur, sport, pool, state, school=school,
                         n=RB.SCHOOL_TEAM_N)
            if t:
                teams.append({"pool": pool, "rows": t})
    shown = ([r for b in grades for _g, _w, rs in b["grades"] for r in rs]
             + [r for b in improved for r in b["rows"]])
    fillBestRaces(cur, shown, sport)
    return {
        "school": school, "state": state, "sport": sport,
        "events": events,
        "grades": [{"pool": b["pool"],
                    "grades": [(g, w, [slim(r, _SEASON_KEYS) for r in rs])
                               for g, w, rs in b["grades"]]} for b in grades],
        "improved": [{"pool": b["pool"],
                      "rows": [slim(r, _SEASON_KEYS) for r in b["rows"]]}
                     for b in improved],
        "teams": [{"pool": b["pool"],
                   "rows": [slim(t, _TEAM_KEYS) for t in b["rows"]]}
                  for b in teams],
    }


def _poolSort(pool):
    level, _, g = (pool or "").partition("_")
    order = ("hs", "college", "ms", "elem")
    return (order.index(level) if level in order else 9, {"m": 0, "f": 1}.get(g, 2))


def buildSchools(conn, cur, cap, force=False):
    from panels import isTeamName
    t0 = time.time()
    fps = schoolFingerprints(cur)
    clusters = schoolClusters(cur)
    stored = {}
    if not force:
        cur.execute("SELECT key, sig FROM record_books WHERE kind = 'school'")
        stored = {r["key"]: r["sig"] for r in cur.fetchall()}
    todo = changedSchools(fps, stored, clusters, isTeamName, cap)
    print(f"  school: {len(fps):,} (school, sport) fingerprints, "
          f"{len(todo):,} pages to build (cap {cap or 'none'}) "
          f"[{time.time() - t0:.0f}s]", flush=True)
    batch, built, failed = [], 0, 0
    for i, (school, sport, state, primary, fp) in enumerate(todo):
        try:
            cur.execute("SAVEPOINT rb_school")
            ctx = schoolBook(cur, school, state, primary, sport)
            cur.execute("RELEASE SAVEPOINT rb_school")
        except Exception as exc:                        # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT rb_school")
            failed += 1
            print(f"  ! {school} ({state}) {sport}: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        batch.append((RB.schoolKey(school, state, sport), ctx, fp))
        built += 1
        if len(batch) >= 100:
            writePart(conn, cur, "school", batch, None, replace=False)
            batch = []
        if (i + 1) % 500 == 0:
            print(f"    {i + 1:,}/{len(todo):,} schools "
                  f"({(time.time() - t0) / 60:.1f} min)", flush=True)
    if batch:
        writePart(conn, cur, "school", batch, None, replace=False)
    print(f"  school: {built:,} built, {failed:,} failed "
          f"[{(time.time() - t0) / 60:.1f} min]", flush=True)
    return built


# ------------------------------------------------------------------ #

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="state,school,course,otd",
                    help="comma list of parts (state,school,course,otd)")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when the inputs have not changed")
    ap.add_argument("--max-schools", type=int, default=4000,
                    help="school pages per run, biggest changed first "
                         "(0 = no cap); the rest wait for the next run")
    ap.add_argument("--levels", default=",".join(RB.BUILD_LEVELS),
                    help="state lists for these levels (hs,ms,college)")
    args = ap.parse_args(argv)
    parts = [p.strip() for p in args.only.split(",") if p.strip()]
    bad = [p for p in parts if p not in RB.KINDS]
    if bad:
        sys.exit(f"unknown part(s): {', '.join(bad)}")
    levels = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    if any(lv not in RB.LEVELS for lv in levels):
        sys.exit(f"--levels must be from {', '.join(RB.LEVELS)}")

    import psycopg2.extras
    from database import getConn
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            from dbfast import tuneSession
            tuneSession(conn)
        except Exception:                               # noqa: BLE001
            conn.rollback()
        cur.execute(_DDL)
        conn.commit()
        for part in parts:
            p0 = time.time()
            if part == "school":
                buildSchools(conn, cur, args.max_schools, force=args.force)
                continue
            sig = tableSig(cur, PART_TABLES[part])
            if part == "state":
                sig += f"|levels={','.join(levels)}"
            if not args.force and sig == lastSig(cur, part):
                print(f"  {part}: inputs unchanged since the last build, skipped",
                      flush=True)
                continue
            if part == "state":
                rows = buildStates(conn, cur, levels)
            elif part == "course":
                rows = buildCourses(conn, cur)
            else:
                rows = buildOtd(conn, cur)
            writePart(conn, cur, part, rows, sig)
            print(f"  {part}: {len(rows):,} pages written "
                  f"[{(time.time() - p0) / 60:.1f} min]", flush=True)
    print(f"done [{(time.time() - t0) / 60:.1f} min]", flush=True)


if __name__ == "__main__":
    main()
