#!/usr/bin/env python3
"""
layout_lab.py -- synthetic ranking_results / athlete_season, built in several
physical layouts, with the site's own board queries run against each.

    export XCP_DB_HOST=/tmp/pgtest XCP_DB_PORT=54329 XCP_DB_NAME=layout_lab \
           XCP_DB_USER=postgres XCP_DB_PASSWORD=x
    python scripts/layout_lab.py gen --persons 490000        # ~6M result rows (+ base)
    python scripts/layout_lab.py queries                     # -> layout_queries.json
    python scripts/layout_lab.py variant lean                # lab_lean (see LAYOUTS)
    python scripts/layout_lab.py bench base lean --index-use # times, buffers, equality
    python scripts/layout_lab.py build base lean --jobs 3    # the finish step's wall time
    python scripts/layout_lab.py variant partyear && python scripts/layout_lab.py partial
    python scripts/layout_lab.py drop [--all]                # clean the lab schemas

  Each layout is ~4.5 GB at 6M rows: build, bench and drop one at a time on
  a small disk. Results and the doc's numbers: docs/TABLE-LAYOUT-EXPERIMENTS.md.

★ WHAT IT IS FOR (docs/TABLE-LAYOUT-EXPERIMENTS.md). The question is whether
  the boards tables can be LAID OUT differently -- partitioned, ordered,
  narrower, fewer indexes -- so the boards read fewer pages and the pipeline's
  rebuild writes less, with every query returning exactly what it returns
  now. The data is synthetic (no production rows leave the server); the
  queries are not: `queries` calls rankings.getPerformanceRankings /
  getAbilityRankings / getPrRankings / countRows with a recording cursor and
  saves the exact SQL text each one sends.

★ ONE DATABASE, ONE SCHEMA PER LAYOUT. The baseline lives in `public` (the
  rankings module's probes -- _rowHasUnit, _hasEventKind -- read
  information_schema there); each variant is a schema `lab_<name>` holding
  its own ranking_results and athlete_season, and a query is run against a
  variant with `SET search_path = lab_<name>, public`, so the SQL text is
  byte-for-byte the one the site sends. athletes / results / results_tf (the
  name lookup) are shared in public.

! EQUALITY IS CHECKED ON THE FULL RESULT SET, IN ORDER: md5 over every row's
  text, and the row count. A layout that changes one row's position on one
  board fails.

! READ-ONLY OUTSIDE ITS OWN DATABASE. Point it at a scratch server only;
  `gen` drops and recreates public.ranking_results and friends.
"""
import argparse
import hashlib
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import psycopg2                                                    # noqa: E402

OUT_DIR = os.environ.get("LAYOUT_LAB_OUT", os.path.join(_ROOT, "scripts", "layout_lab_out"))


def dsn():
    e = os.environ
    return (f"host={e.get('XCP_DB_HOST', '/tmp/pgtest')} port={e.get('XCP_DB_PORT', '54329')} "
            f"user={e.get('XCP_DB_USER', 'postgres')} dbname={e.get('XCP_DB_NAME', 'layout_lab')}")


def connect(autocommit=True):
    c = psycopg2.connect(dsn())
    c.autocommit = autocommit
    with c.cursor() as cur:
        # the site's session: no JIT (database.py), default planner costs
        cur.execute("SET jit = off")
    return c


def run(conn, sql, params=None, quiet=False):
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(sql, params)
    dt = time.time() - t0
    if not quiet:
        print(f"  [{dt:7.1f}s] {' '.join(sql.split())[:110]}", flush=True)
    return dt


def one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def tune(conn):
    """dbfast.tuneSession's numbers (the build's own session)."""
    for k, v in (("work_mem", "256MB"), ("maintenance_work_mem", "1GB"),
                 ("max_parallel_workers_per_gather", "4"),
                 ("max_parallel_maintenance_workers", "4")):
        run(conn, f"SET {k} = '{v}'", quiet=True)


# ------------------------------------------------------------------ #
#  THE REAL SHAPES
# ------------------------------------------------------------------ #

# build_ranking_results._COLUMNS, with the types _migrateLive and the test
# fixtures give them, in the physical order the live table has (the newer
# columns were ALTERed on, so they sit last).
UNIT_COLS = ("division", "region", "conference", "league", "state_div",
             "section_div", "district", "county", "class", "area", "section")
RR_COLS = [
    ("sport", "text"), ("result_id", "bigint"), ("person_id", "bigint"),
    ("pool", "text"), ("speed_rating", "real"), ("race_date", "date"),
    ("year", "integer"), ("state", "text"), ("school", "text"),
    ("grade", "text"), ("meet_id", "bigint"), ("div_id", "bigint"),
    ("canon_meet_id", "bigint"), ("time_seconds", "real"),
    ("distance", "real"), ("event_id", "bigint"),
] + [(u, "text") for u in UNIT_COLS] + [("event_kind", "text"), ("mark", "real")]

AS_COLS = [
    ("person_id", "bigint"), ("pool", "text"), ("sport", "text"),
    ("year", "integer"), ("mean_rating", "real"), ("decayed_rating", "real"),
    ("best_rating", "real"), ("n_races", "integer"), ("first_race", "date"),
    ("last_race", "date"), ("state", "text"), ("school", "text"),
    ("grade", "text"),
] + [(u, "text") for u in UNIT_COLS]


def canonical_indexes():
    """build_ranking_results._CANONICAL_INDEXES, deduplicated by column list
    exactly as indexDefs does (rr_result_idx and idx_rr_result are one tree)."""
    import re
    src = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    start = src.index("_CANONICAL_INDEXES = {")
    end = src.index("\n}\n", start) + 2
    ns = {}
    exec(src[start:end], ns)                                        # noqa: S102
    out = {}
    for table, idx in ns["_CANONICAL_INDEXES"].items():
        seen, keep = set(), []
        for name, cols in idx:
            sig = re.sub(r"[\s()]", "", cols).lower()
            if sig in seen:
                continue
            seen.add(sig)
            keep.append((name, cols))
        out[table] = keep
    return out


def ddl(table, cols, schema="public", extra=""):
    body = ", ".join(f'"{c}" {t}' for c, t in cols)
    return f"CREATE TABLE {schema}.{table} ({body}){extra}"


# ------------------------------------------------------------------ #
#  1. THE SYNTHETIC CORPUS
# ------------------------------------------------------------------ #
#
# ★ SHAPED LIKE THE REAL ONE WHERE A LAYOUT CAN TELL THE DIFFERENCE:
#   pools hs 57% / ms 17% / college 17% / pro+elem 3% of people; XC ~60% of
#   rows; years 2005-2026 weighted toward the present (the corpus grew);
#   ratings normal per pool; distances clustered at the standard events with
#   the odd surveyed value (4999.9, 8046.72); states population-skewed with a
#   sliver of foreign codes; college rows split between the two feeds (the
#   negative result ids carry no state); field events with a mark and no
#   time; sprints with a time and no rating.
# ★ AND IN THE HEAP ORDER THE BUILD WRITES: one sport after the other (the
#   stream stages), rows of one meet together, meets in no useful order --
#   the row walk follows the results table, which is scrape order.
_STATES = ("CA TX NY PA OH IL MI FL NC GA NJ VA WA MN WI MA IN CO OR AZ MO TN "
           "MD IA KS KY UT SC AL LA OK CT NE ID NM AR NV MS ME NH WV MT HI RI "
           "DE SD ND AK VT WY DC").split()


def gen(args):
    conn = connect()
    tune(conn)
    P = args.persons
    run(conn, "SELECT setseed(0.4242)")
    for t in ("ranking_results", "athlete_season", "athletes", "results", "results_tf",
              "lab_school", "lab_person", "lab_season", "lab_rows"):
        run(conn, f"DROP TABLE IF EXISTS public.{t} CASCADE", quiet=True)
    states = "ARRAY[" + ",".join(f"'{s}'" for s in _STATES) + "]"
    # schools: 16k school-age, 1.2k colleges; state by population rank
    run(conn, f"""
        CREATE UNLOGGED TABLE lab_school AS
        SELECT s AS school_id,
               s >= 16000 AS college,
               CASE WHEN s >= 16000 THEN
                    (ARRAY['University of','State University','College of','Tech','A&M'])[1 + s % 5]
                    || ' ' || (ARRAY['Northfield','Ashland','Riverside','Glenwood','Fairview',
                                     'Harbor','Summit','Lakewood'])[1 + (s / 5) % 8] || ' ' || s
               ELSE (ARRAY['Lincoln','Washington','Central','North','South','East','West',
                           'Jefferson','Roosevelt','Kennedy','Madison','Franklin','Mountain View',
                           'Valley','Ridge','Lakeside','St. Mary''s','Holy Cross','Desert','Pine'])[1 + s % 20]
                    || ' ' || (ARRAY['High School','HS','Academy','Preparatory','Senior High',
                                     'Middle School'])[1 + (s / 20) % 6] || ' ' || s END AS school,
               CASE WHEN random() < 0.012 THEN (ARRAY['ON','BC','QC','PR','AB'])[1 + floor(random() * 5)::int]
                    ELSE ({states})[1 + floor(51 * power(random(), 2.2))::int] END AS state
        FROM generate_series(0, 17199) s""")
    # people: level, gender, home school (Zipf-ish), first season, ability
    run(conn, f"""
        CREATE UNLOGGED TABLE lab_person AS
        SELECT i,
               1000000 + i * 37 + (i % 11)                    AS person_id,
               CASE WHEN random() < 0.5 THEN 'm' ELSE 'f' END  AS g,
               CASE WHEN u < 0.57 THEN 'hs' WHEN u < 0.74 THEN 'ms'
                    WHEN u < 0.91 THEN 'college' WHEN u < 0.95 THEN 'pro'
                    ELSE 'elem' END                            AS lvl,
               floor(16000 * power(random(), 1.6))::int        AS hs_school,
               16000 + floor(1200 * power(random(), 1.4))::int AS col_school,
               2026 - floor(22 * power(random(), 1.7))::int    AS y0,
               1 + floor(random() * 4)::int                    AS nyears,
               (random() + random() + random() - 1.5) * 2      AS z
        FROM (SELECT i, random() AS u FROM generate_series(1, {P}) i) q""")
    # seasons: each person, each year of a short career, XC and/or TF
    run(conn, """
        CREATE UNLOGGED TABLE lab_season AS
        SELECT p.*, y.year, sp.sport,
               CASE WHEN p.lvl = 'college' THEN p.col_school ELSE p.hs_school END AS school_id,
               CASE WHEN p.lvl IN ('pro', 'elem') THEN p.lvl ELSE p.lvl END || '_' || p.g AS pool,
               1 + floor(-ln(1 - random()) * 4)::int AS nraces
        FROM lab_person p
        CROSS JOIN LATERAL generate_series(p.y0, LEAST(2026, p.y0 + p.nyears - 1)) y(year)
        CROSS JOIN LATERAL (VALUES ('XC'), ('TF')) sp(sport)
        WHERE (sp.sport = 'XC' AND random() < 0.78)
           OR (sp.sport = 'TF' AND y.year < 2026 AND random() < 0.55)""")
    # the rows: one per race. Event, distance, rating, time, mark, meet.
    pool_mean = ("CASE s.pool WHEN 'hs_m' THEN 150 WHEN 'hs_f' THEN 128 WHEN 'college_m' THEN 172 "
                 "WHEN 'college_f' THEN 150 WHEN 'ms_m' THEN 118 WHEN 'ms_f' THEN 104 "
                 "WHEN 'pro_m' THEN 185 WHEN 'pro_f' THEN 162 ELSE 90 END")
    run(conn, f"""
        CREATE UNLOGGED TABLE lab_rows AS
        WITH r AS (
            SELECT s.*, k, random() AS ue, random() AS ud, random() AS uf,
                   {pool_mean} + 12 * s.z + 4 * (random() + random() + random() - 1.5) * 2 AS rating,
                   floor(random() * 14)::int AS week
            FROM lab_season s CROSS JOIN LATERAL generate_series(1, s.nraces) k
        ), e AS (
            SELECT r.*,
              CASE WHEN sport = 'XC' THEN NULL
                   WHEN ue < 0.11 THEN (ARRAY['high_jump','pole_vault','long_jump','triple_jump',
                        'shot_put','discus','javelin','hammer','weight_throw'])[1 + floor(uf * 9)::int]
                   WHEN ue < 0.15 THEN 'hurdles'
                   WHEN ue < 0.155 AND lvl = 'college' THEN 'steeple'
                   ELSE NULL END AS event_kind
            FROM r
        ), d AS (
            SELECT e.*,
              CASE
                WHEN sport = 'XC' THEN CASE
                  WHEN lvl IN ('hs', 'pro') THEN (ARRAY[5000,5000,5000,5000,5000,5000,5000,5000,4828,4828,
                                                        4828,4000,4999.9,5001,3200,6000])[1 + floor(ud * 16)::int]
                  WHEN lvl IN ('ms', 'elem') THEN (ARRAY[3200,3200,3000,4000,2414,4023,3218,2000])[1 + floor(ud * 8)::int]
                  WHEN g = 'm' THEN (ARRAY[8000,8000,8000,8000,8000,10000,10000,6000,8047,8046.72])[1 + floor(ud * 10)::int]
                  ELSE (ARRAY[6000,6000,6000,6000,6000,6000,5000,5000,8000,4999.9])[1 + floor(ud * 10)::int] END
                WHEN event_kind = 'hurdles' THEN (CASE WHEN g = 'm' THEN ARRAY[110,300,400]
                                                 ELSE ARRAY[100,300,400] END)[1 + floor(ud * 3)::int]
                WHEN event_kind = 'steeple' THEN (ARRAY[3000,3000,2000])[1 + floor(ud * 3)::int]
                WHEN event_kind IS NOT NULL THEN NULL
                ELSE (ARRAY[100,100,200,200,400,400,800,800,800,1500,1600,1600,1609,3000,3200,3200,
                            5000,55,60,1000])[1 + floor(ud * 20)::int]
              END::real AS distance
            FROM e
        )
        SELECT d.sport, d.person_id, d.pool, d.year, d.school_id, d.lvl, d.g,
               d.y0, d.week, d.k, d.event_kind, d.distance,
               CASE WHEN d.distance >= 600 AND d.event_kind IS DISTINCT FROM 'hurdles'
                    THEN round(d.rating::numeric, 2)::real END AS speed_rating,
               CASE WHEN d.distance IS NOT NULL
                    THEN round((d.distance / (2.0 + d.rating / 75.0)
                                * power(d.distance / 5000.0, 0.07))::numeric, 2)::real END AS time_seconds,
               CASE WHEN d.event_kind IN ('high_jump','pole_vault','long_jump','triple_jump','shot_put',
                                          'discus','javelin','hammer','weight_throw')
                    THEN round((d.rating / 10.0 * (CASE d.event_kind WHEN 'high_jump' THEN 0.12
                         WHEN 'pole_vault' THEN 0.27 WHEN 'long_jump' THEN 0.45 WHEN 'triple_jump' THEN 0.9
                         WHEN 'shot_put' THEN 0.95 WHEN 'discus' THEN 2.9 WHEN 'javelin' THEN 3.1
                         WHEN 'hammer' THEN 3.3 ELSE 1.2 END))::numeric, 2)::real END AS mark
        FROM d""")
    cols = ", ".join(f'"{c}"' for c, _t in RR_COLS)
    run(conn, ddl("ranking_results", RR_COLS, extra=" WITH (autovacuum_enabled = false)"))
    # meet: (sport, year, week, state, school bucket); college rows split by feed
    run(conn, f"""
        INSERT INTO ranking_results ({cols})
        SELECT sport, result_id, person_id, pool, speed_rating, race_date, year,
               CASE WHEN tfrrs THEN NULL ELSE st END, school, grade, meet_id, div_id,
               CASE WHEN lvl = 'college' AND random() < 0.45 THEN meet_id / 3 END,
               time_seconds, distance,
               CASE WHEN sport = 'TF' THEN meet_id * 64 + (abs(hashtext(coalesce(event_kind, '') || distance::text)::bigint) % 64) END,
               CASE WHEN lvl = 'college' THEN (ARRAY['NCAA DI','NCAA DII','NCAA DIII','NAIA','NJCAA'])[1 + school_id % 5] END,
               CASE WHEN lvl = 'college' THEN (ARRAY['WEST','MOUNTAIN','SOUTH CENTRAL','MIDWEST','GREAT LAKES',
                                                     'SOUTHEAST','MID-ATLANTIC','NORTHEAST','SOUTH'])[1 + school_id % 9] END,
               CASE WHEN lvl = 'college' THEN 'CONF-' || (school_id % 60) END,
               CASE WHEN lvl IN ('hs', 'ms') AND school_id % 4 = 0 THEN 'LEAGUE ' || (school_id % 300) END,
               CASE WHEN lvl IN ('hs', 'ms') THEN st || '-D' || (school_id % 5) END,
               CASE WHEN lvl = 'hs' AND st IN ('CA', 'NY') THEN 'SEC' || (school_id % 10) || '-D' || (school_id % 5) END,
               CASE WHEN lvl IN ('hs', 'ms') THEN 'DISTRICT ' || (school_id % 40) END,
               CASE WHEN lvl IN ('hs', 'ms') THEN 'COUNTY ' || (school_id % 60) END,
               CASE WHEN lvl = 'hs' AND st IN ('TX', 'GA', 'AL', 'OK') THEN (1 + school_id % 6) || 'A' END,
               CASE WHEN lvl = 'hs' AND st = 'CA' THEN 'AREA ' || (school_id % 4) END,
               CASE WHEN lvl = 'hs' AND st IN ('CA', 'NY') THEN 'SECTION ' || (school_id % 10) END,
               event_kind, mark
        FROM (
            SELECT x.*, sc.state AS st, sc.school,
                   x.lvl = 'college' AND (x.person_id % 2 = 0) AS tfrrs,
                   CASE WHEN x.sport = 'XC' THEN make_date(x.year, 8, 20) + x.week * 7 + (x.k % 3)
                        ELSE make_date(x.year + 1, 2, 1) + x.week * 9 + (x.k % 4) END AS race_date,
                   abs(hashtext(x.sport || x.year || '-' || x.week || sc.state || (x.school_id % 9)
                                || (x.lvl = 'college')::text)::bigint) % 50000000 AS meet_id,
                   1 + (abs(hashtext(x.pool)::bigint) % 6) AS div_id,
                   CASE WHEN x.lvl = 'hs' THEN (ARRAY['9','10','11','12','12','11','Sr','10th'])
                                                [1 + LEAST(7, GREATEST(0, x.year - x.y0 + (x.person_id % 3)::int))]
                        WHEN x.lvl = 'ms' THEN (ARRAY['6','7','8','8'])[1 + LEAST(3, x.year - x.y0)]
                        WHEN x.lvl = 'college' THEN (ARRAY['FR-1','SO-2','JR-3','SR-4','SR'])[1 + LEAST(4, x.year - x.y0)]
                   END AS grade,
                   (CASE WHEN x.lvl = 'college' AND x.person_id % 2 = 0 THEN -1 ELSE 1 END)
                     * (row_number() OVER ()) AS result_id
            FROM lab_rows x JOIN lab_school sc ON sc.school_id = x.school_id
        ) q
        WHERE race_date <= DATE '2026-11-07'
        ORDER BY sport DESC, md5(meet_id::text), k""")
    run(conn, "DROP TABLE lab_rows")
    n = one(conn, "SELECT count(*) FROM ranking_results")[0]
    print(f"  ranking_results: {n:,} rows")
    # names: athletes for nearly everyone, a few only on their result rows
    run(conn, """CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
                                        last_name text, gender text)""")
    run(conn, """INSERT INTO athletes
                 SELECT person_id, person_id,
                        (ARRAY['Ava','Ben','Cora','Dan','Eli','Fay','Gus','Hana','Ivan','June'])[1 + i % 10],
                        'Runner' || i, upper(g)
                 FROM lab_person WHERE i % 97 <> 0""")
    run(conn, "CREATE INDEX ON athletes (person_id)")
    run(conn, "CREATE INDEX ON athletes (athlete_id)")
    for t in ("results", "results_tf"):
        run(conn, f"CREATE TABLE {t} (person_id bigint, athlete_name text)")
        run(conn, f"CREATE INDEX ON {t} (person_id)")
    run(conn, """INSERT INTO results SELECT person_id, 'Nameless ' || i
                 FROM lab_person WHERE i % 97 = 0""")
    run(conn, "ANALYZE athletes; ANALYZE results; ANALYZE results_tf")
    conn.close()
    build_layout("base", seed_only=True)


# ------------------------------------------------------------------ #
#  athlete_season, by the build's own aggregate
# ------------------------------------------------------------------ #

def season_sql(load_table, season_table):
    """build_ranking_results._ATHLETE_SEASON_SQL, unsharded, as the build
    formats it (imported, so the lab times the real statement)."""
    import build_ranking_results as B                               # noqa: E402
    return B._ATHLETE_SEASON_SQL.format(load_table=load_table, season_table=season_table,
                                        shard_where="TRUE", shard_where_base="TRUE")


def _unit_stamp(table):
    """The unit columns onto a season from its rows' (the build stamps them
    from school_unit; the lab takes the season's own rows' mode)."""
    sets = ", ".join(f'"{u}" = r."{u}"' for u in UNIT_COLS)
    sel = ", ".join(f'max("{u}") AS "{u}"' for u in UNIT_COLS)
    return (f"UPDATE {table} s SET {sets} FROM (SELECT person_id, pool, sport, year, {sel} "
            f"FROM ranking_results GROUP BY 1, 2, 3, 4) r "
            f"WHERE r.person_id = s.person_id AND r.pool = s.pool AND r.sport = s.sport "
            f"AND r.year = s.year")


# ------------------------------------------------------------------ #
#  2. THE LAYOUTS
# ------------------------------------------------------------------ #
#
# Each builds lab_<name>.ranking_results and lab_<name>.athlete_season from
# the baseline rows (public), timing the parts the pipeline pays for: the
# heap write, the index builds, ANALYZE, and a VACUUM (the build vacuums
# after the swap; index-only reads need the visibility map).

POOL_PARTS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")
# the canonical columns re-ordered so no fixed-width column waits on
# alignment padding behind a short text: 8-byte, then 4-byte, then varlena
RR_NARROW = ([c for c in RR_COLS if c[1] == "bigint"]
             + [c for c in RR_COLS if c[1] in ("real", "integer", "date")]
             + [c for c in RR_COLS if c[1] == "text"])
AS_NARROW = ([c for c in AS_COLS if c[1] == "bigint"]
             + [c for c in AS_COLS if c[1] in ("real", "integer", "date")]
             + [c for c in AS_COLS if c[1] == "text"])

# ★ THE TRIMMED INDEX SET (experiment d). Each drop names the index that
#   already answers its every query by a left prefix of its key.
# ! AS MEASURED, NOT AS RECOMMENDED: idx_rr_school and rr_school_sport_idx
#   are 43 MB each (btree deduplication) and have readers
#   (build_school_identity, the school page), so the doc keeps them and
#   retires rr_person_idx only; the large zero-scan trees are decided on
#   production's idx_scan (scripts/layout_probe.py).
TRIM_DROP = {
    "ranking_results": {
        "rr_person_idx": "idx_rr_person_cover (person_id) INCLUDE (...)",
        "idx_rr_school": "rr_school_idx (school, sport, speed_rating DESC)",
        "rr_school_sport_idx": "rr_school_idx (school, sport, speed_rating DESC)",
    },
    "athlete_season": {},
}
# ★ PARTIAL: rows a rating board can never return. Every reader of these
#   four (the performance candidates, _countLive, _rankInResults) says
#   `speed_rating IS NOT NULL`, so the planner can still use them.
# ⚠ NOT THE TIME INDEXES. They look the same (`time_seconds IS NOT NULL`
#   is in every PR candidate WHERE), but the PR board's per-distance walk
#   (rankings._prCandidatesPerDistance) asks `SELECT min(distance) ...
#   WHERE pool AND sport AND distance BETWEEN` with no time predicate, so a
#   partial rr_pr_sport_time_idx cannot serve it and the walk falls to a
#   bitmap scan: measured 10x the buffers on R1/R3 in the first lab run.
TRIM_PARTIAL = {
    "rr_pool_rating_idx": "speed_rating IS NOT NULL",
    "rr_pool_sport_rating_idx": "speed_rating IS NOT NULL",
    "rr_board_rating_idx": "speed_rating IS NOT NULL",
    "rr_perf_dist_rating_idx": "speed_rating IS NOT NULL",
}


def _indexes(table, kind):
    out = []
    for name, cols in canonical_indexes()[table]:
        if kind == "trim" and name in TRIM_DROP.get(table, {}):
            continue
        if kind == "trim" and name in TRIM_PARTIAL:
            cols = f"{cols} WHERE {TRIM_PARTIAL[name]}"
        out.append((name, cols))
    return out


def _index_sql(rel, iname, icols, ixw, only=False):
    where = ""
    if " WHERE " in icols:
        icols, where = icols.split(" WHERE ", 1)
        where = f" WHERE {where}"
    return f"CREATE INDEX {iname} ON {'ONLY ' if only else ''}{rel} {icols}{ixw}{where}"


# ★ THE LAYOUTS, ONE LINE EACH.
#   part   none | pool (LIST) | poolyear (LIST pool -> RANGE year, old/cur)
#   order  the heap order the load writes: none (the build's: sport, then
#          meet by meet in scrape order), board (pool, sport, year, rating
#          desc), person (person_id, sport, date)
#   cols   canon (the live column order) | narrow (8-byte, 4-byte, then text)
#   idx    canon | trim (TRIM_DROP gone, TRIM_PARTIAL partial)
#   ixff   btree fillfactor: 90 (Postgres' default) | 100 (build-once)
LAYOUTS = {
    "base":     dict(part="none", order="none", cols="canon", idx="canon", ixff=90),
    "part":     dict(part="pool", order="none", cols="canon", idx="canon", ixff=90),
    "partyear": dict(part="poolyear", order="none", cols="canon", idx="canon", ixff=90),
    "clus":     dict(part="none", order="board", cols="canon", idx="canon", ixff=90),
    "pclus":    dict(part="none", order="person", cols="canon", idx="canon", ixff=90),
    "narrow":   dict(part="none", order="none", cols="narrow", idx="canon", ixff=90),
    "ixff":     dict(part="none", order="none", cols="canon", idx="canon", ixff=100),
    "trim":     dict(part="none", order="none", cols="canon", idx="trim", ixff=90),
    # the combinations
    "lean":     dict(part="none", order="none", cols="narrow", idx="trim", ixff=100),
    "leanclus": dict(part="none", order="board", cols="narrow", idx="trim", ixff=100),
    "partclus": dict(part="pool", order="board", cols="canon", idx="canon", ixff=90),
    # ★ NOT A LAYOUT: the plain table with extended statistics on the
    #   columns the boards filter together. Tells apart what partitioning
    #   gains by pruning from what it gains by giving each pool its own
    #   statistics (the planner's row estimates).
    "xstats":   dict(part="none", order="none", cols="canon", idx="canon", ixff=90, xstats=True),
}
# CREATE STATISTICS for the xstats layout: the board filters' column groups
XSTATS = {
    "ranking_results": [("pool", "sport", "distance"), ("pool", "sport", "year"),
                        ("pool", "sport", "state"), ("pool", "event_kind")],
    "athlete_season": [("pool", "sport", "year"), ("pool", "sport", "state")],
}
_ORDER = {
    ("ranking_results", "board"): " ORDER BY pool, sport, year, speed_rating DESC NULLS LAST",
    ("ranking_results", "person"): " ORDER BY person_id, sport, race_date",
    ("athlete_season", "board"): " ORDER BY pool, sport, year, mean_rating DESC NULLS LAST",
    ("athlete_season", "person"): " ORDER BY person_id",
}


def _create_parts(conn, sch, table, cols, part, ff, prefix):
    run(conn, ddl(table, cols, sch, " PARTITION BY LIST (pool)"))
    for p in POOL_PARTS:
        if part == "poolyear" and table == "ranking_results":
            run(conn, f"CREATE TABLE {sch}.{prefix}_{p} PARTITION OF {sch}.{table} "
                      f"FOR VALUES IN ('{p}') PARTITION BY RANGE (year)")
            run(conn, f"CREATE TABLE {sch}.{prefix}_{p}_old PARTITION OF {sch}.{prefix}_{p} "
                      f"FOR VALUES FROM (MINVALUE) TO (2025){ff}")
            run(conn, f"CREATE TABLE {sch}.{prefix}_{p}_cur PARTITION OF {sch}.{prefix}_{p} "
                      f"FOR VALUES FROM (2025) TO (MAXVALUE){ff}")
        else:
            run(conn, f"CREATE TABLE {sch}.{prefix}_{p} PARTITION OF {sch}.{table} "
                      f"FOR VALUES IN ('{p}'){ff}")
    run(conn, f"CREATE TABLE {sch}.{prefix}_other PARTITION OF {sch}.{table} DEFAULT{ff}")


def _leaves(conn, sch, table):
    with conn.cursor() as cur:
        cur.execute("""
            WITH RECURSIVE tree AS (
                SELECT %s::regclass AS rel
                UNION ALL
                SELECT i.inhrelid::regclass FROM pg_inherits i JOIN tree ON i.inhparent = tree.rel)
            SELECT rel::text FROM tree JOIN pg_class c ON c.oid = tree.rel WHERE c.relkind = 'r'""",
                    (f"{sch}.{table}",))
        return [r[0] for r in cur.fetchall()]


def _parallel(jobs, n, workers=1):
    """Run (label, [sql, ...]) jobs on n connections at once.
    Returns (wall seconds, summed busy seconds)."""
    import concurrent.futures as cf

    def do(job):
        c = connect()
        tune(c)
        run(c, f"SET max_parallel_maintenance_workers = {workers}", quiet=True)
        t = 0.0
        for sql in job[1]:
            t += run(c, sql, quiet=True)
        c.close()
        return t
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=n) as ex:
        busy = sum(ex.map(do, jobs))
    return time.time() - t0, busy


def build_layout(name, seed_only=False):
    """lab_<name>: tables, load, indexes, analyze, vacuum -- all timed."""
    L = LAYOUTS[name]
    conn = connect()
    tune(conn)
    sch = "public" if name == "base" else f"lab_{name}"
    rep = {"layout": name, "config": L, "steps": {}}
    T = rep["steps"]
    if name == "base" and not seed_only:
        raise SystemExit("base is built by gen")
    if name != "base":
        run(conn, f"DROP SCHEMA IF EXISTS {sch} CASCADE")
        run(conn, f"CREATE SCHEMA {sch}")
    ff = " WITH (autovacuum_enabled = false)"
    ixw = f" WITH (fillfactor = {L['ixff']})" if L["ixff"] != 90 else ""
    for table, canon, narrow, prefix in (("ranking_results", RR_COLS, RR_NARROW, "rr"),
                                         ("athlete_season", AS_COLS, AS_NARROW, "as")):
        cols = narrow if L["cols"] == "narrow" else canon
        clist = ", ".join(f'"{c}"' for c, _t in cols)
        if name == "base":
            if table == "athlete_season":
                # the season table: the build's own aggregate, then the units
                run(conn, ddl("athlete_season", AS_COLS, extra=ff))
                T["as_aggregate"] = run(conn, season_sql("public.ranking_results",
                                                         "public.athlete_season"))
                T["as_units"] = run(conn, _unit_stamp("public.athlete_season"))
                run(conn, "VACUUM FULL public.athlete_season")   # the UPDATE's dead rows out
        else:
            if L["part"] == "none":
                run(conn, ddl(table, cols, sch, ff))
            else:
                _create_parts(conn, sch, table, cols, L["part"], ff, prefix)
            order = _ORDER.get((table, L["order"]), "")
            T[f"{prefix}_load"] = run(conn, f"INSERT INTO {sch}.{table} ({clist}) "
                                            f"SELECT {clist} FROM public.{table}{order}")
        if L.get("xstats"):
            for i, grp in enumerate(XSTATS[table]):
                run(conn, f"CREATE STATISTICS {sch}.{prefix}_xs{i} (mcv, ndistinct, dependencies) "
                          f"ON {', '.join(grp)} FROM {sch}.{table}")
        # indexes, one at a time (the serial cost: what each tree costs)
        t_idx = 0.0
        for iname, icols in _indexes(table, L["idx"]):
            nm = iname if name == "base" else f"lab_{name}_{iname}"
            t_idx += run(conn, _index_sql(f"{sch}.{table}", nm, icols, ixw))
        T[f"{prefix}_index"] = t_idx
        T[f"{prefix}_analyze"] = run(conn, f"ANALYZE {sch}.{table}")
    conn.autocommit = True
    T["vacuum"] = run(conn, f"VACUUM {sch}.ranking_results") + run(conn, f"VACUUM {sch}.athlete_season")
    rep["sizes"] = sizes(conn, sch)
    conn.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"layout_{name}.json"), "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    s = rep["sizes"]
    print(f"  {name}: rr heap {s['ranking_results']['heap'] / 2**20:.0f} MB, "
          f"indexes {s['ranking_results']['indexes'] / 2**20:.0f} MB; "
          f"as heap {s['athlete_season']['heap'] / 2**20:.0f} MB, "
          f"indexes {s['athlete_season']['indexes'] / 2**20:.0f} MB; "
          + ", ".join(f"{k} {v:.1f}s" for k, v in T.items() if v is not None), flush=True)
    return rep


def build_cost(args):
    """The pipeline's finish step for ranking_results, per layout, as WALL
    time with `--jobs` statements at a time (build_ranking_results runs
    _INDEX_JOBS=3 index builds at once): UNLOGGED load -> SET LOGGED ->
    indexes -> ANALYZE -> VACUUM. A partitioned layout's unit of work is a
    leaf: each is set logged and indexed on its own connection; the parent's
    indexes are then created ON ONLY and every leaf's tree ATTACHed, which
    is a catalogue step."""
    rep = {}
    for name in args.names:
        L = LAYOUTS[name]
        conn = connect()
        tune(conn)
        sch = f"lab_b_{name}"
        run(conn, f"DROP SCHEMA IF EXISTS {sch} CASCADE", quiet=True)
        run(conn, f"CREATE SCHEMA {sch}", quiet=True)
        cols = RR_NARROW if L["cols"] == "narrow" else RR_COLS
        clist = ", ".join(f'"{c}"' for c, _t in cols)
        ff = " WITH (autovacuum_enabled = false)"
        r = {}
        if L["part"] == "none":
            run(conn, ddl("ranking_results", cols, sch, ff).replace("CREATE TABLE", "CREATE UNLOGGED TABLE"))
        else:
            _create_parts(conn, sch, "ranking_results", cols, L["part"], ff, "rr")
            for leaf in _leaves(conn, sch, "ranking_results"):
                run(conn, f"ALTER TABLE {leaf} SET UNLOGGED", quiet=True)
        # ★ THE STREAM'S ORDER, ALWAYS: the COPY lands rows as the row walk
        #   reads them. A board-ordered heap is made by the step that
        #   already rewrites the whole heap -- SET LOGGED -- becoming an
        #   ordered INSERT into a LOGGED table instead (one sort added).
        order = _ORDER.get(("ranking_results", L["order"]), "")
        r["load"] = run(conn, f"INSERT INTO {sch}.ranking_results ({clist}) "
                              f"SELECT {clist} FROM public.ranking_results")
        leaves = _leaves(conn, sch, "ranking_results")
        ixw = f" WITH (fillfactor = {L['ixff']})" if L["ixff"] != 90 else ""
        idx = _indexes("ranking_results", L["idx"])
        if L["part"] == "none":
            rel = f"{sch}.ranking_results"
            if order:
                t0 = time.time()
                run(conn, ddl("ranking_results_s", cols, sch, ff), quiet=True)
                run(conn, f"INSERT INTO {sch}.ranking_results_s ({clist}) "
                          f"SELECT {clist} FROM {rel}{order}", quiet=True)
                run(conn, f"DROP TABLE {rel}", quiet=True)
                run(conn, f"ALTER TABLE {sch}.ranking_results_s RENAME TO ranking_results", quiet=True)
                r["relog_ordered"] = time.time() - t0
            else:
                r["set_logged"] = run(conn, f"ALTER TABLE {rel} SET LOGGED")
            jobs = [(n, [_index_sql(rel, f"b_{name}_{n}", c, ixw)]) for n, c in idx]
        elif order:
            # each leaf rewritten in board order into a logged sibling, side
            # by side; the siblings are what gets indexed (in a real build
            # they would be ATTACHed in place of the leaves -- see `partial`)
            jobs0 = []
            for leaf in leaves:
                s_ = f"{leaf}_s"
                jobs0.append((leaf, [f"CREATE TABLE {s_} (LIKE {leaf}) WITH (autovacuum_enabled = false)",
                                     f"INSERT INTO {s_} SELECT * FROM {leaf}{order}"]))
            r["relog_ordered"], _busy = _parallel(jobs0, args.jobs)
            jobs = [(f"{leaf}_s.{n}", [_index_sql(f"{leaf}_s", f"{leaf.split('.')[-1]}_s_{n}", c, ixw)])
                    for leaf in leaves for n, c in idx]
        else:
            r["set_logged"], _busy = _parallel(
                [(leaf, [f"ALTER TABLE {leaf} SET LOGGED"]) for leaf in leaves], args.jobs)
            jobs = [(f"{leaf}.{n}", [_index_sql(leaf, f"{leaf.split('.')[-1]}_{n}", c, ixw)])
                    for leaf in leaves for n, c in idx]
        r["index_wall"], r["index_busy"] = _parallel(jobs, args.jobs, args.workers)
        if L["part"] != "none" and not order:
            # the parent's indexes: every leaf already has its tree, so
            # CREATE INDEX on the parent only matches and attaches them
            t0 = time.time()
            for n, c in idx:
                run(conn, _index_sql(f"{sch}.ranking_results", f"b_{name}_{n}", c, ixw), quiet=True)
            r["attach"] = time.time() - t0
        conn.autocommit = True
        r["analyze"] = run(conn, f"ANALYZE {sch}.ranking_results")
        r["vacuum"] = run(conn, f"VACUUM {sch}.ranking_results")
        r["total_wall"] = sum(v for k, v in r.items() if k != "index_busy")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s", (sch,))
            r["index_objects"] = cur.fetchone()[0]
        rep[name] = {k: round(v, 1) for k, v in r.items()}
        print(f"  {name}: " + ", ".join(f"{k} {v}" for k, v in rep[name].items()), flush=True)
        run(conn, f"DROP SCHEMA {sch} CASCADE", quiet=True)
        conn.close()
    with open(os.path.join(OUT_DIR, f"build_cost_{'_'.join(args.names)}.json"), "w") as fh:
        json.dump(rep, fh, indent=1)


# ★ THE REAL BUILDER, SWITCH BY SWITCH (2026-11-08: the plan's steps 1 and 2
#   as shipped behind XCP_RR_LEAN / XCP_RR_ORDERED). Not the lab's own
#   re-statement of a layout: build_ranking_results' createShadow, relog
#   (SET LOGGED or the ordered rewrite) and buildIndexes (with its
#   verifyIndexes) run on the base rows, exactly as `--stage finish` runs
#   them; the built shadows are then moved into lab_<name>, where `bench`
#   reads them like any other layout. flag_off is the control: today's
#   build, which must equal base.
FLAG_LAYOUTS = {"flag_off": (False, False), "flag_lean": (True, False),
                "flag_ordered": (False, True), "flag_both": (True, True)}


def builder_variant(name, log=print):
    """lab_<name> built by the real finish step under its switches; returns
    {step: seconds} and the sizes. The stream's COPY is stood in for by an
    INSERT of the base rows in the base heap's (the stream's) order."""
    import build_ranking_results as B                               # noqa: E402
    lean, ordered = FLAG_LAYOUTS[name]
    saved = {k: os.environ.get(k) for k in ("XCP_RR_LEAN", "XCP_RR_ORDERED")}
    os.environ["XCP_RR_LEAN"] = "1" if lean else "0"
    os.environ["XCP_RR_ORDERED"] = "1" if ordered else "0"
    sch = f"lab_{name}"
    conn = connect(autocommit=False)
    tune(conn)
    T = {}
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {sch} CASCADE")
            cur.execute(f"CREATE SCHEMA {sch}")
        conn.commit()
        for like, load in (("ranking_results", B._LOAD_TABLE),
                           ("athlete_season", B._LOAD_SEASON)):
            p = "rr" if like == "ranking_results" else "as"
            B.createShadow(conn, load, like)
            with conn.cursor() as cur:
                cur.execute("""SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass
                               AND attnum > 0 AND NOT attisdropped ORDER BY attnum""",
                            (f"public.{like}",))
                cols = ", ".join(f'"{r[0]}"' for r in cur.fetchall())
                t0 = time.time()
                cur.execute(f"INSERT INTO {load} ({cols}) SELECT {cols} FROM public.{like}")
            conn.commit()
            T[f"{p}_load"] = time.time() - t0
            t0 = time.time()
            B.relog(conn, load, like)
            T[f"{p}_relog"] = time.time() - t0
            t0 = time.time()
            B.buildIndexes(conn, load, like)             # verifyIndexes + ANALYZE inside
            T[f"{p}_index"] = time.time() - t0
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE public.{load} SET SCHEMA {sch}")
                cur.execute(f"ALTER TABLE {sch}.{load} RENAME TO {like}")
            conn.commit()
        conn.autocommit = True
        T["vacuum"] = (run(conn, f"VACUUM (ANALYZE) {sch}.ranking_results", quiet=True)
                       + run(conn, f"VACUUM (ANALYZE) {sch}.athlete_season", quiet=True))
        rep = {"layout": name, "lean": lean, "ordered": ordered,
               "steps": {k: round(v, 1) for k, v in T.items()}, "sizes": sizes(conn, sch)}
    finally:
        conn.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    s = rep["sizes"]
    log(f"  {name}: rr heap {s['ranking_results']['heap'] / 2**20:.0f} MB, "
        f"indexes {s['ranking_results']['indexes'] / 2**20:.0f} MB; "
        f"as heap {s['athlete_season']['heap'] / 2**20:.0f} MB, "
        f"indexes {s['athlete_season']['indexes'] / 2**20:.0f} MB; "
        + ", ".join(f"{k} {v}s" for k, v in rep["steps"].items()))
    return rep


def sizes(conn, sch):
    """heap and index bytes of the two tables in a schema, partitions summed."""
    out = {}
    with conn.cursor() as cur:
        for t in ("ranking_results", "athlete_season"):
            cur.execute("""
                WITH RECURSIVE tree AS (
                    SELECT %s::regclass AS rel
                    UNION ALL
                    SELECT i.inhrelid::regclass FROM pg_inherits i JOIN tree ON i.inhparent = tree.rel)
                SELECT sum(pg_relation_size(rel)), sum(pg_indexes_size(rel)),
                       sum(CASE WHEN c.relkind = 'r' THEN c.reltuples END)
                FROM tree JOIN pg_class c ON c.oid = tree.rel""", (f"{sch}.{t}",))
            heap, idx, n = cur.fetchone()
            cur.execute("""
                WITH RECURSIVE tree AS (
                    SELECT %s::regclass AS rel
                    UNION ALL
                    SELECT i.inhrelid::regclass FROM pg_inherits i JOIN tree ON i.inhparent = tree.rel)
                SELECT regexp_replace(ic.relname, '^lab_[a-z]+_', ''), sum(pg_relation_size(ic.oid))
                FROM tree JOIN pg_index x ON x.indrelid = tree.rel
                JOIN pg_class ic ON ic.oid = x.indexrelid
                GROUP BY 1 ORDER BY 2 DESC""", (f"{sch}.{t}",))
            per = {r[0]: int(r[1]) for r in cur.fetchall()}
            out[t] = {"heap": int(heap or 0), "indexes": int(idx or 0), "rows": int(n or 0),
                      "per_index": per}
    return out


# ------------------------------------------------------------------ #
#  3. THE QUERIES THE SITE SENDS
# ------------------------------------------------------------------ #

BOARDS = [
    # performance: default, by year, both sports, distance, state, deep page
    ("P1 perf hs_m XC", "board=performance&pool=hs_m&sport=XC"),
    ("P2 perf hs_f XC 2025", "board=performance&pool=hs_f&sport=XC&year=2025"),
    ("P3 perf college_m both", "board=performance&pool=college_m&sport=both"),
    ("P4 perf hs_m TF 1600", "board=performance&pool=hs_m&sport=TF&distance=1600"),
    ("P5 perf hs_m XC CA 2024", "board=performance&pool=hs_m&sport=XC&state=CA&year=2024"),
    ("P6 perf ms_f XC offset 500", "board=performance&pool=ms_f&sport=XC&offset=500"),
    ("P7 perf college_f XC all-scope", "board=performance&pool=college_f&sport=XC&scope=all"),
    ("P8 perf hs_f TF 2023 sort=date", "board=performance&pool=hs_f&sport=TF&year=2023&sort=date"),
    # ability: the default board, by year, best, state, the live event window, deep
    ("A1 ability hs_m both", "board=ability&pool=hs_m&sport=both"),
    ("A2 ability hs_f XC 2025", "board=ability&pool=hs_f&sport=XC&year=2025"),
    ("A3 ability college_m XC best", "board=ability&pool=college_m&sport=XC&sort=best"),
    ("A4 ability hs_m XC TX", "board=ability&pool=hs_m&sport=XC&state=TX"),
    ("A5 ability hs_m XC 2025 dist>=3000", "board=ability&pool=hs_m&sport=XC&year=2025&dist_min=3000"),
    ("A6 ability ms_m TF 2024 offset 200", "board=ability&pool=ms_m&sport=TF&year=2024&offset=200"),
    ("A7 ability hs_f both division", "board=ability&pool=college_f&sport=XC&division=NCAA DI"),
    # PR: clock at a distance, by year, all pools, a field event, a sprint
    ("R1 pr hs_m XC 5000", "board=pr&pool=hs_m&sport=XC&distance=5000"),
    ("R2 pr hs_f TF 1600 2025", "board=pr&pool=hs_f&sport=TF&distance=1600&year=2025"),
    ("R3 pr all TF 800 m", "board=pr&pool=all&sport=TF&distance=800&gender=m"),
    ("R4 pr college_m shot_put", "board=pr&pool=college_m&sport=TF&event=shot_put"),
    ("R5 pr hs_f TF 100", "board=pr&pool=hs_f&sport=TF&distance=100"),
    ("R6 pr college_f XC 6000 offset 100", "board=pr&pool=college_f&sport=XC&distance=6000&offset=100"),
]


class _Args(dict):
    def getlist(self, k):
        v = super().get(k)
        return [] if v is None else (v if isinstance(v, list) else [v])

    def get(self, k, default=None, type=None):                  # noqa: A002
        v = super().get(k)
        if isinstance(v, list):
            v = v[0] if v else None
        if v is None:
            return default
        return type(v) if type else v


class _Rec:
    """A cursor that runs every statement and keeps its exact text."""
    def __init__(self, cur):
        self._cur, self.sql = cur, []

    def execute(self, sql, params=None):
        text = self._cur.mogrify(sql, params).decode()
        # only the reads of the two tables (not SAVEPOINT / SET LOCAL / board_size)
        if text.lstrip().upper().startswith(("SELECT", "WITH")) and (
                "ranking_results" in text or "athlete_season" in text):
            self.sql.append(text)
        self._cur.execute(sql, params)

    def __getattr__(self, n):
        return getattr(self._cur, n)


def queries(args):
    from urllib.parse import parse_qs
    os.environ.setdefault("XCP_DB_PASSWORD", "x")
    import rankings as RK
    out = []
    conn = connect(autocommit=False)
    for label, qs in BOARDS:
        a = _Args({k: (v if len(v) > 1 else v[0]) for k, v in parse_qs(qs).items()})
        f, err = RK.parseFilters(a)
        if err:
            raise SystemExit(f"{label}: {err}")
        with conn.cursor() as raw:
            rec = _Rec(raw)
            fn = {"performance": RK.getPerformanceRankings, "pr": RK.getPrRankings,
                  "ability": RK.getAbilityRankings}[f["board"]]
            rows = fn(rec, f)
            # the board query that produced the page is the LAST statement
            out.append({"label": label, "qs": qs, "sql": rec.sql[-1], "n": len(rows),
                        "kind": "board"})
            if f["board"] != "pr" or f.get("event"):
                rec2 = _Rec(raw)
                try:
                    RK.countRows(rec2, f, timeout_ms=0)
                    if rec2.sql:
                        out.append({"label": label.split()[0] + "c count", "qs": qs,
                                    "sql": rec2.sql[-1], "kind": "count"})
                except Exception as exc:                          # noqa: BLE001
                    print(f"  count {label}: {exc}")
                    conn.rollback()
        conn.rollback()
        print(f"  {label}: {len(rows)} rows")
    # the page lookups: athlete, school, race (stampRowsHs), season roster
    with conn.cursor() as cur:
        cur.execute("SELECT person_id FROM public.athlete_season WHERE pool = 'hs_m' "
                    "ORDER BY mean_rating DESC NULLS LAST LIMIT 1 OFFSET 777")
        pid = cur.fetchone()[0]
        cur.execute("SELECT school, count(*) FROM public.ranking_results WHERE pool = 'hs_f' "
                    "GROUP BY 1 ORDER BY 2 DESC LIMIT 1 OFFSET 40")
        school = cur.fetchone()[0]
        cur.execute("SELECT array_agg(result_id) FROM (SELECT result_id FROM public.ranking_results "
                    "WHERE meet_id = (SELECT meet_id FROM public.ranking_results WHERE sport = 'XC' "
                    "AND year = 2025 LIMIT 1 OFFSET 1000) LIMIT 300) q")
        rids = cur.fetchone()[0]
    lit = lambda v: conn.cursor().mogrify("%s", (v,)).decode()     # noqa: E731
    pages = [
        ("X1 athlete page rows", f"SELECT sport, race_date, year, distance, time_seconds, speed_rating, "
         f"pool, meet_id, result_id FROM ranking_results WHERE person_id = {lit(pid)} "
         f"ORDER BY race_date, result_id"),
        ("X2 record badges (cover)", f"SELECT person_id, sport, race_date, year, distance, time_seconds, "
         f"speed_rating, event_kind FROM ranking_results WHERE person_id = ANY({lit([pid, pid + 37, pid + 74, pid + 111])}) "
         f"ORDER BY person_id, race_date, distance, time_seconds"),
        ("X3 race page stamp", f"SELECT result_id, pool, speed_rating FROM ranking_results "
         f"WHERE sport = 'XC' AND result_id = ANY({lit(rids)}) ORDER BY result_id"),
        ("X4 school page", f"SELECT result_id, person_id, speed_rating, year FROM ranking_results "
         f"WHERE school = {lit(school)} AND sport = 'TF' ORDER BY speed_rating DESC NULLS LAST, result_id LIMIT 100"),
        ("X5 roster races", f"SELECT count(DISTINCT meet_id) FROM ranking_results "
         f"WHERE school = {lit(school)} AND sport = 'XC' AND year = 2025"),
        ("X6 athlete seasons", f"SELECT pool, sport, year, mean_rating, best_rating, n_races FROM athlete_season "
         f"WHERE person_id = {lit(pid)} ORDER BY sport, year, pool"),
        ("X7 school roster", f"SELECT person_id, year, mean_rating FROM athlete_season "
         f"WHERE school = {lit(school)} AND sport = 'XC' AND year = 2025 ORDER BY mean_rating DESC NULLS LAST, person_id"),
    ]
    for label, sql in pages:
        out.append({"label": label, "qs": "", "sql": sql, "kind": "page"})
    conn.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "layout_queries.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"  {len(out)} statements -> {OUT_DIR}/layout_queries.json")


# ------------------------------------------------------------------ #
#  4. THE BENCHMARK
# ------------------------------------------------------------------ #

def planIndexes(node):
    """The sorted index names a plan walks, with lab_<x>_ / shadow prefixes
    taken off, so the same tree compares equal across layouts."""
    import re
    out = set()

    def walk(n):
        if "Index Name" in n:
            out.add(re.sub(r"^(lab_[a-z]+_|b_[a-z]+_)?(ranking_results|athlete_season)?(_new)*_?", "",
                           n["Index Name"]))
        for c in n.get("Plans", []):
            walk(c)
    walk(node)
    return sorted(out)


def bench(args):
    qs = json.load(open(os.path.join(OUT_DIR, "layout_queries.json")))
    if args.only:
        qs = [q for q in qs if q["label"].split()[0] in args.only]
    conn = connect()
    res = {}
    if args.index_use:
        run(conn, "SELECT pg_stat_reset()", quiet=True)
    for v in args.variants:
        sch = "public" if v == "base" else f"lab_{v}"
        run(conn, f"SET search_path = {sch}, public", quiet=True)
        res[v] = {}
        for q in qs:
            # the result, in full and in order
            with conn.cursor() as cur:
                cur.execute(q["sql"])
                rows = cur.fetchall()
            h = hashlib.md5(repr(rows).encode()).hexdigest()
            times, bufs, plan = [], None, None
            for i in range(args.reps + 1):
                with conn.cursor() as cur:
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + q["sql"])
                    pj = cur.fetchone()[0][0]
                if i == 0:
                    continue                                    # warm-up run
                times.append(pj["Execution Time"] + pj.get("Planning Time", 0))
                top = pj["Plan"]
                bufs = (top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0))
                plan = pj
            times.sort()
            res[v][q["label"]] = {"ms": round(times[len(times) // 2], 2), "buffers": bufs,
                                  "rows": len(rows), "md5": h,
                                  "plan_ms": round(plan.get("Planning Time", 0), 2),
                                  "indexes": planIndexes(plan["Plan"])}
            if args.plans:
                with open(os.path.join(OUT_DIR, f"plan_{v}_{q['label'].split()[0]}.txt"), "w") as fh:
                    with conn.cursor() as cur:
                        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + q["sql"])
                        fh.write("\n".join(r[0] for r in cur.fetchall()))
        print(f"  {v}: done", flush=True)
    if args.index_use:
        # which trees the workload touched (the first variant's schema)
        time.sleep(1.5)
        sch0 = "public" if args.variants[0] == "base" else f"lab_{args.variants[0]}"
        with conn.cursor() as cur:
            cur.execute("SELECT pg_stat_clear_snapshot()")
            cur.execute("""SELECT relname, indexrelname, idx_scan, pg_relation_size(indexrelid)
                           FROM pg_stat_user_indexes
                           WHERE schemaname = %s AND relname IN ('ranking_results', 'athlete_season')
                           ORDER BY relname, idx_scan DESC""", (sch0,))
            use = cur.fetchall()
        print("\n  index use over the workload (scans, MB):")
        for t, i, n, b in use:
            print(f"    {t:16s} {i:28s} {n:6d} {b / 2**20:8.0f}")
        with open(os.path.join(OUT_DIR, f"index_use_{args.variants[0]}.json"), "w") as fh:
            json.dump([list(u) for u in use], fh, indent=1)
    conn.close()
    path = os.path.join(OUT_DIR, f"bench_{'_'.join(args.variants)}.json")
    with open(path, "w") as fh:
        json.dump(res, fh, indent=1)
    # the table: per query, each variant's ms / buffers, and equality to the first
    base = args.variants[0]
    # ★ AND THE TREES EACH PLAN WALKED, against the first variant's: `i`
    #   after the equality mark when a query used a different set of
    #   indexes (names with their schema/shadow prefixes taken off)
    print(f"\n{'query':40s}" + "".join(f"{v:>20s}" for v in args.variants))
    bad = 0
    for q in qs:
        lab = q["label"]
        line = f"{lab[:40]:40s}"
        for v in args.variants:
            r = res[v][lab]
            same = r["md5"] == res[base][lab]["md5"]
            same_ix = r["indexes"] == res[base][lab]["indexes"]
            bad += not same
            line += f"{r['ms']:>8.1f}ms {r['buffers']:>7}b {'=' if same else 'X'}{' ' if same_ix else 'i'}"
        print(line)
    print(f"\n  {bad} result differences against {base}" if bad else
          f"\n  every result identical to {base}, row for row")
    return res


# ------------------------------------------------------------------ #
#  5. THE CURRENT-SEASON SWAP (experiment a, the pipeline half)
# ------------------------------------------------------------------ #

def partial(args):
    """THE PARTITION SWAP (experiment a, the pipeline half). Needs
    `variant partyear`.

    1. CHANGE DETECTION. A full or nightly run changes rows in any season
       (the solve re-rates everything; relinks, distance pins and stragglers
       reach back years), so "rebuild only the current season" is not safe
       on its own. What IS safe: stream everything as now, then compare each
       new leaf with the live one and re-index / swap only the leaves that
       differ. The comparison is an order-free fingerprint per leaf --
       count(*) and sum(hashtext(row::text)) -- one sequential read each
       side, no sort. Its cost is measured here against the leaf's index
       builds, which is what it saves when the leaf is unchanged.
    2. THE SWAP of one leaf: the new leaf built beside the live one with a
       CHECK that matches its bound (so ATTACH skips the validating scan)
       and every index the parent has (so ATTACH only matches them), then
       DETACH old + ATTACH new in one short transaction."""
    conn = connect()
    tune(conn)
    sch = "lab_partyear"
    rep = {"leaves": {}}
    cols = ", ".join(f'"{c}"' for c, _t in RR_COLS)
    # the fingerprint of every leaf, timed, against its index builds
    leaves = _leaves(conn, sch, "ranking_results")
    fp_total = 0.0
    for leaf in leaves:
        t0 = time.time()
        fp = one(conn, f"SELECT count(*), sum(hashtext(t::text)::bigint) FROM {leaf} t")
        dt = time.time() - t0
        fp_total += dt
        rep["leaves"][leaf] = {"rows": fp[0], "fingerprint_s": round(dt, 2)}
    rep["fingerprint_all_s"] = round(fp_total, 1)
    # one leaf changed: the current season of each pool rebuilt and swapped
    t0 = time.time()
    total = {"load": 0.0, "set_logged": 0.0, "check": 0.0, "index": 0.0, "analyze": 0.0}
    for p in POOL_PARTS:
        new = f"{sch}.rr_{p}_cur_new"
        run(conn, f"DROP TABLE IF EXISTS {new}", quiet=True)
        run(conn, f"CREATE UNLOGGED TABLE {new} (LIKE {sch}.rr_{p}_cur INCLUDING DEFAULTS "
                  f"INCLUDING CONSTRAINTS) WITH (autovacuum_enabled = false)", quiet=True)
        total["load"] += run(conn, f"INSERT INTO {new} ({cols}) SELECT {cols} FROM public.ranking_results "
                                   f"WHERE pool = '{p}' AND year >= 2025", quiet=True)
        total["set_logged"] += run(conn, f"ALTER TABLE {new} SET LOGGED", quiet=True)
        # the bound as a CHECK first, so ATTACH skips its validating scan
        total["check"] += run(conn, f"ALTER TABLE {new} ADD CONSTRAINT rr_{p}_cur_bound CHECK "
                                    f"(pool IS NOT NULL AND pool = '{p}' AND year IS NOT NULL "
                                    f"AND year >= 2025)", quiet=True)
        for iname, icols in _indexes("ranking_results", "canon"):
            total["index"] += run(conn, _index_sql(new, f"rr_{p}_cur_new_{iname}", icols, ""),
                                  quiet=True)
        total["analyze"] += run(conn, f"ANALYZE {new}", quiet=True)
    rep["rebuild_current"] = {k: round(v, 1) for k, v in total.items()}
    swap = []
    for p in POOL_PARTS:
        with conn.cursor() as cur:
            t1 = time.time()
            cur.execute("BEGIN")
            cur.execute("SET LOCAL lock_timeout = '3s'")
            cur.execute(f"ALTER TABLE {sch}.rr_{p} DETACH PARTITION {sch}.rr_{p}_cur")
            cur.execute(f"ALTER TABLE {sch}.rr_{p} ATTACH PARTITION {sch}.rr_{p}_cur_new "
                        f"FOR VALUES FROM (2025) TO (MAXVALUE)")
            cur.execute("COMMIT")
            swap.append(time.time() - t1)
            cur.execute(f"DROP TABLE {sch}.rr_{p}_cur")
            cur.execute(f"ALTER TABLE {sch}.rr_{p}_cur_new RENAME TO rr_{p}_cur")
    rep["swap_ms_each"] = [round(s * 1000, 1) for s in swap]
    # every parent index still valid, every leaf's tree attached
    rep["invalid_indexes"] = one(conn, """
        SELECT count(*) FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'lab_partyear' AND NOT x.indisvalid""")[0]
    n_cur = one(conn, "SELECT count(*) FROM public.ranking_results WHERE year >= 2025 AND pool = ANY(%s)",
                (list(POOL_PARTS),))[0]
    n_all = one(conn, "SELECT count(*) FROM public.ranking_results")[0]
    rep["rows_rebuilt"], rep["rows_total"] = n_cur, n_all
    rep["wall_s"] = round(time.time() - t0, 1)
    print(json.dumps(rep, indent=1))
    with open(os.path.join(OUT_DIR, "partial_swap.json"), "w") as fh:
        json.dump(rep, fh, indent=1)


def drop(args):
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'lab\\_%'")
        names = [s for (s,) in cur.fetchall()]
    for s in names:
        if args.keep and s in [f"lab_{k}" for k in args.keep]:
            continue
        run(conn, f"DROP SCHEMA {s} CASCADE")
    if args.all:
        for t in ("ranking_results", "athlete_season", "athletes", "results", "results_tf",
                  "lab_school", "lab_person", "lab_season", "lab_rows"):
            run(conn, f"DROP TABLE IF EXISTS public.{t} CASCADE")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--persons", type=int, default=450000)
    v = sub.add_parser("variant")
    v.add_argument("names", nargs="+", choices=[k for k in LAYOUTS if k != "base"] + list(FLAG_LAYOUTS))
    sub.add_parser("queries")
    b = sub.add_parser("bench")
    b.add_argument("variants", nargs="+")
    b.add_argument("--reps", type=int, default=3)
    b.add_argument("--plans", action="store_true")
    b.add_argument("--only", nargs="*")
    b.add_argument("--index-use", action="store_true")
    c = sub.add_parser("build")
    c.add_argument("names", nargs="+", choices=list(LAYOUTS))
    c.add_argument("--jobs", type=int, default=3)
    c.add_argument("--workers", type=int, default=1)
    sub.add_parser("partial")
    d = sub.add_parser("drop")
    d.add_argument("--all", action="store_true")
    d.add_argument("--keep", nargs="*")
    a = ap.parse_args()
    if a.cmd == "gen":
        gen(a)
    elif a.cmd == "variant":
        for n in a.names:
            if n in FLAG_LAYOUTS:
                rep = builder_variant(n)
                os.makedirs(OUT_DIR, exist_ok=True)
                with open(os.path.join(OUT_DIR, f"layout_{n}.json"), "w") as fh:
                    json.dump(rep, fh, indent=1, default=str)
            else:
                build_layout(n)
    elif a.cmd == "queries":
        queries(a)
    elif a.cmd == "bench":
        bench(a)
    elif a.cmd == "build":
        build_cost(a)
    elif a.cmd == "partial":
        partial(a)
    elif a.cmd == "drop":
        drop(a)


if __name__ == "__main__":
    main()
