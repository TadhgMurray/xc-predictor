#!/usr/bin/env python3
"""
rating_outliers.py -- a result too far from the athlete's NEIGHBOURING races
is data, not a performance.

    python engine/rating_outliers.py --dry-run
    python engine/rating_outliers.py --dry-run --since 2024
    python engine/rating_outliers.py --dry-run --person 29603084
    python engine/rating_outliers.py --write

★ THE OWNER'S RULE (2026-09-18): "If a result is more than 5-15 sigma away
  (from their season's median or mean or whatever) do not rate or rank."

★★ NEIGHBOURS, NOT THE CALENDAR SEASON (owner, 2026-09-29, Jack Moretta,
   athlete 29603084, Tufts). His 2026 cross country season opened with the
   Bobby Doyle Classic, 9 Aug 2026, 35:02.5 for 8046 m, rated 80.4 -- "not
   real (easy LR)", a five-mile road race run as a long run. Aldrich, 12 Sep
   2026, 97.3 -- "real". Around them: six 2025 XC races at 97.2-105.7 and
   three spring 2026 track 5000s at 102.9-104.6, one scale. The season rule
   missed it three ways at once:

       it grouped by substr(date, 1, 4)    -- two races in "2026 XC", nothing
                                              to measure against
       it needed MIN_RACES = 5 in that season -- so it had no opinion at all
       the slow side was off (SLOW_SIGMA None) -- "a slow race is a jog"

   A race is now judged against the SAME PERSON'S OTHER RATED RACES NEAREST
   IN TIME, in both sports, on both sides of it, across season boundaries.
   Bobby Doyle's ten neighbours have a median of 104.25; it sits 24 points,
   eight to nine spreads, under them.

⚠⚠ MEAN AND STANDARD DEVIATION CANNOT DO THIS JOB, and that is still the
   whole design. One huge outlier drags a mean towards itself and inflates
   the very standard deviation it is measured against -- a 10-sigma error
   among six races routinely scores under 2. MEDIAN AND MAD (x 1.4826, to
   read as a normal sigma) are unmoved by it.

★ THE NEIGHBOURHOOD IS THE K NEAREST RATED RACES BY DATE, K MEASURED.
  Too few neighbours and the median is noise; too many and it reaches back
  to a different athlete -- a freshman's races judging a senior's. So K is
  the one that best PREDICTS a race from its neighbours: on a hash sample of
  athletes, each race is left out, its K nearest give a median, and the K
  whose median lands closest to the race (median over athletes of each
  athlete's median |error|) wins. calibrate() prints the whole curve.

  ! THE K NEAREST BY DATE ARE ALWAYS WITHIN K ROWS EITHER SIDE in the
    athlete's date order -- in a sorted line the K nearest points form one
    run that touches the point -- so a window of K PRECEDING and K
    FOLLOWING holds them exactly, and no join is needed.

★ THE SPREAD HAS A MEASURED FLOOR, NOT MIN_SPREAD = 2.0. An athlete with
  near-identical neighbours has a MAD near zero, and every deviation would
  be infinite. The floor is the corpus's own race-to-race spread measured
  the same way: 1.4826 x the median, over athletes, of each athlete's median
  |race - neighbours' median|. So an athlete steadier than the typical one
  is judged on the typical spread, never a tighter one.

★ THE CUT IS WHERE THE CORPUS EXPECTS ALMOST NO GENUINE RACE PAST IT.
  On each side, the COUNT OF RACES INSIDE 2-3 sigma -- where genuine races
  are nearly everything -- is fitted as an exponential density and
  extrapolated outwards. The cut is the smallest z at which the genuine
  races expected past it are at most 1% of the rows actually past it (a 1%
  false discovery rate: of a hundred rows it hides, about one is a real
  race), kept inside the owner's 5-15. An exponential falls slower than a
  normal tail, so it over-counts genuine races: the cut errs high (5.75 on
  a clean normal corpus, where 5 would do). The dry run prints the whole
  distribution and the table the cut was read from.

★ BOTH SIDES NOW, AND THEY MEAN DIFFERENT THINGS.
    fast  a wrong distance, a wrong time, a merged person. Off the boards,
          out of predictions, marked on the page -- as before.
    slow  a jog, a road race run easy, a sick day. KEPT ON THE PAGE, greyed,
          and left out of every season number: the boards anti-join this
          table (build_ranking_results._outlierClause), so athlete_season --
          the header and season numbers on the athlete page -- is built
          without it, and the predictor's rating basis skips it too.

! THE SOLVE DOES NOT READ THIS, ON PURPOSE. The table is built from the
  solve's own ratings (step 09d, after 08 and 09b); feeding it back would
  make each run's ratings depend on the last run's flags. The joint solve
  already caps a slow residual's pull with its Huber weights
  (joint_solve.robustWeights, HUBER_SLOW = 2.5 robust scales).

! ALREADY-EXCLUDED ROWS ARE NOT NEIGHBOURS. result_twin and
  impossible_result rows are left out entirely (they rank nowhere already).
  Last run's FAST rows are judged again but are no one's neighbour: a wrong
  distance should not pull its neighbours' median. Slow rows stay
  neighbours -- they are real runs at their real distances, and a median
  shrugs one off.

Table:

    rating_outlier (result_id, sport, person_id, season, rating, med, sigma,
                    n_races, z, side, span_days, cut)

    n_races is now the number of NEIGHBOURS, season the race's own year,
    span_days the furthest neighbour's distance in days.

    rating_outlier_calib -- one row per --write: K, floor, both cuts and the
    curves they came from, for --person to explain against.
"""
import argparse
import json
import math
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 1.4826 * MAD estimates sigma for a normal distribution -- the standard
# constant, so "sigma" in the owner's sentence keeps its ordinary meaning.
MAD_TO_SIGMA = 1.4826

# ★ THE K THE CALIBRATION TRIES. The top is two full seasons of a racing
#   athlete; past it the neighbourhood is years wide for most of the corpus.
K_CANDIDATES = (3, 4, 5, 6, 8, 10, 12, 16)

# ! A MEDIAN OF TWO IS NOT ROBUST: one bad neighbour moves it halfway. Three
#   is the fewest a median can survive one bad value in, so a race with
#   fewer rated neighbours than that has no opinion formed about it.
MIN_NEIGHBOURS = 3

# ★ THE CUT'S TOLERANCE: genuine races expected past the cut, as a share of
#   the rows past it. See the module note.
FDR = 0.01

# The owner's words, 2026-09-18: "more than 5-15 sigma away".
OWNER_RANGE = (5.0, 15.0)

# ! THE TAIL FIT: 2 to 3 sigma, where genuine races are nearly all there is
#   (errors and jogs live further out). It needs 100 rows at 2 sigma -- the
#   fit's anchor then carries a 1/sqrt(100) = 10% counting error; with fewer
#   the side takes the top of the owner's range, the least opinion.
FIT_LO, FIT_HI = 2.0, 3.0
FIT_MIN_ROWS = 100

# The histogram's bin, in sigma. Also the step the cut is chosen on.
BIN = 0.25

# ! PERFORMANCE KNOBS, NOT STATISTICS. A shard is one hash partition of the
#   stage, sized so its window sort stays near work_mem and one shard is a
#   few minutes; the calibration sample is capped where its medians are
#   far steadier than the differences between the K it compares (300k
#   races is some ten thousand athletes with a full neighbourhood; 1.25M
#   cost 77 s on the test box and chose the same K).
#
#   MEASURED (2026-09-29, 4-core scratch box, 5M synthetic rated rows):
#       stage, both tables read once          13 s   ~2.6 us/row
#       judging, one connection, K = 6       ~16 us/row
#       judging, one connection, K = 16      ~25 us/row
#       judging, 4 connections, K = 16        69 s for 5M
SHARD_ROWS = 8_000_000
CAL_ROWS = 300_000

# --since judges races from that year on; their neighbours may be older,
# and this is how much older the stage reaches.
SINCE_MARGIN_YEARS = 2

TABLES = {"XC": "results", "TF": "results_tf"}
STAGE = "rating_outlier_stage"

DDL = """
CREATE TABLE IF NOT EXISTS rating_outlier (
    result_id bigint NOT NULL,
    sport     text   NOT NULL,
    person_id bigint,
    season    int,
    rating    double precision,
    med       double precision,
    sigma     double precision,
    n_races   int,
    z         double precision,
    side      text   NOT NULL,
    built     date   NOT NULL DEFAULT current_date,
    PRIMARY KEY (result_id, sport));
ALTER TABLE rating_outlier ADD COLUMN IF NOT EXISTS span_days int;
ALTER TABLE rating_outlier ADD COLUMN IF NOT EXISTS cut double precision;
CREATE TABLE IF NOT EXISTS rating_outlier_calib (
    built     timestamptz NOT NULL DEFAULT now(),
    k         int,
    floor     double precision,
    fast_cut  double precision,
    slow_cut  double precision,
    n_judged  bigint,
    detail    jsonb);
"""


def _tableExists(cur, name):
    # ! BY THE SEARCH PATH, as every query here names its tables: on the box
    #   that is public; in a test's own schema it is that schema, and a
    #   leftover table in public must not answer for it
    cur.execute("SELECT to_regclass(%s)", (name,))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def _hasCol(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def _one(cur):
    row = cur.fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else list(row.values())[0]


class _Clock:
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        print(f"    [{time.time() - self.t0:8.1f}s] {self.label}", flush=True)
        return False


# ---------------------------------------------------------------------- #
# the arithmetic, in Python -- the SQL below does the same per row
# ---------------------------------------------------------------------- #

def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def neighbourZ(rating, near, floor):
    """(median, local spread, spread used, z) of one race against its
    neighbours' ratings -- the rule, in one place a test can read."""
    med = median(near)
    s_local = MAD_TO_SIGMA * median([abs(x - med) for x in near])
    sigma = max(s_local, floor)
    return med, s_local, sigma, (rating - med) / sigma


def pickK(curve):
    """curve: [(K, persons, median error)] -> the K with the smallest error;
    on a tie the larger K, whose median survives more bad neighbours."""
    ok = [c for c in curve if c[2] is not None]
    if not ok:
        return None
    return min(ok, key=lambda c: (c[2], -c[0]))[0]


def survival(hist, c):
    """Rows at or past c sigma on one side. hist: {bin: count}, bin b
    holding |z| in [b*BIN, (b+1)*BIN)."""
    return sum(v for b, v in hist.items() if b * BIN >= c - 1e-9)


def band(hist, a, b):
    """Rows with |z| in [a, b) on one side."""
    return sum(v for k, v in hist.items() if a - 1e-9 <= k * BIN < b - 1e-9)


def genuineTail(hist):
    """(n1, n2, lam, G) -- the genuine races' tail, fitted as an exponential
    DENSITY on the two halves of [FIT_LO, FIT_HI); G(c) is the count it
    expects at or past c.

    ⚠ A DENSITY, NOT A SURVIVAL COUNT (measured on the test corpus,
      2026-09-29). Fitted through "rows past 2" and "rows past 3", the
      tail was mostly the outliers themselves -- every jog at 9 sigma is
      also past 2 and past 3 -- so it fell x2.5 per sigma instead of a
      normal's x17, and the slow cut rose to 8.5 BECAUSE there were jogs to
      catch. Counting only the rows INSIDE the band keeps what the fit
      measures to the band."""
    mid = (FIT_LO + FIT_HI) / 2.0
    half = mid - FIT_LO
    n1, n2 = band(hist, FIT_LO, mid), band(hist, mid, FIT_HI)
    if n1 <= 0 or n2 <= 0:
        return n1, n2, math.inf, lambda c: 0.0
    # ! A BAND THAT DOES NOT FALL IS NOT A TAIL: an outer half as full as
    #   the inner means the fit cannot see the genuine races, so it expects
    #   every row in range to be one (lam -> 0, G stays at n1-scale).
    lam = max(math.log(n1 / n2) / half, 1e-9)
    per = 1.0 - math.exp(-lam * half)

    def G(c):
        return n1 * math.exp(-lam * (c - FIT_LO)) / per
    return n1, n2, lam, G


def deriveCut(hist, lo=OWNER_RANGE[0], hi=OWNER_RANGE[1], fdr=FDR):
    """The cut for one side, and the table it was read from.

    Returns (cut, why, rows) where rows are (c, observed past c, genuine
    expected past c)."""
    n1, n2, lam, G = genuineTail(hist)
    grid = [lo + i * BIN for i in range(int(round((hi - lo) / BIN)) + 1)]
    if n1 < FIT_MIN_ROWS:
        return hi, (f"{n1} rows in [{FIT_LO:g}, "
                    f"{(FIT_LO + FIT_HI) / 2:g}) sigma, under the "
                    f"{FIT_MIN_ROWS} a fit needs: the top of the range"), []
    rows = []
    cut = None
    for c in grid:
        seen = survival(hist, c)
        genuine = G(c)
        rows.append((c, seen, genuine))
        # ! NOTHING PAST c IS NOTHING FALSELY HIDDEN, so that is a cut too.
        if cut is None and (seen == 0 or genuine <= fdr * seen):
            cut = c
    fall = math.exp(lam) if lam != math.inf else float("inf")
    why = (f"genuine tail fitted on {n1:,} + {n2:,} rows in "
           f"[{FIT_LO:g}, {FIT_HI:g}) sigma: falls x{fall:.2f} per sigma")
    if cut is None:
        return hi, why + f"; no cut in range meets {fdr:.0%}: the top", rows
    return cut, why, rows


# ---------------------------------------------------------------------- #
# the stage: every rated row once, hash-partitioned by person
# ---------------------------------------------------------------------- #

_DATE_RE = "^(19|20)[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"


def _day(col):
    # ! NO ::date ON THE TEXT. A 2025-02-30 would abort the whole pass;
    #   the first of the month plus the day never can (Feb 30 -> Mar 2).
    return (f"((substr({col}, 1, 7) || '-01')::date "
            f"+ (substr({col}, 9, 2)::int - 1))")


def _estimateRows(cur):
    n = 0
    for table in TABLES.values():
        if _tableExists(cur, table):
            cur.execute("SELECT reltuples::bigint FROM pg_class "
                        "WHERE oid = %s::regclass", (f"public.{table}",))
            est = _one(cur) or 0
            if est <= 0:
                cur.execute(f"SELECT count(*) FROM {table}")
                est = _one(cur) or 0
            n += est
    return n


def _sourceSelect(cur, sport, since, persons, sample_mod, prior_fast):
    table = TABLES[sport]
    if not (_tableExists(cur, table) and _hasCol(cur, table, "speed_rating")):
        return None
    bit = 0 if sport == "XC" else 1
    d = _day("r.date")
    where = ["r.speed_rating IS NOT NULL", "r.person_id IS NOT NULL",
             f"r.date ~ '{_DATE_RE}'"]
    # ★ RELAYS ARE NOT ONE RUNNER'S RACE, on either side of the judgement.
    if _hasCol(cur, table, "is_relay"):
        where.append("COALESCE(r.is_relay, 0) = 0")
    for flag in ("result_twin", "impossible_result"):
        if _tableExists(cur, flag):
            where.append(f"NOT EXISTS (SELECT 1 FROM {flag} x WHERE "
                         f"x.sport = '{sport}' AND x.result_id = r.result_id)")
    if since:
        where.append(f"r.date >= '{int(since) - SINCE_MARGIN_YEARS}'")
    if persons:
        where.append("r.person_id = ANY(%(persons)s)")
    if sample_mod and sample_mod > 1:
        # ! %% -- this statement is executed WITH parameters, so a bare %
        #   is a psycopg2 format error.
        where.append(f"abs(hashint8(r.person_id)) %% {int(sample_mod)} = 0")
    nb = "true"
    if prior_fast:
        nb = (f"NOT EXISTS (SELECT 1 FROM rating_outlier po WHERE po.side = "
              f"'fast' AND po.sport = '{sport}' AND po.result_id = r.result_id)")
    judge = f"{d} >= DATE '{int(since)}-01-01'" if since else "true"
    return f"""
        SELECT r.result_id * 2 + {bit}, '{sport}', r.result_id, r.person_id,
               {d}, r.speed_rating::real, {nb}, {judge}
        FROM   {table} r
        WHERE  {' AND '.join(where)}"""


def buildStage(cur, since=None, persons=None, sample_mod=None, shards=None,
               name=STAGE, verbose=True):
    """Create `name`, hash-partitioned by person_id into `shards` unlogged
    partitions, and fill it with every rated row of both sports once.
    Returns the number of shards."""
    prior_fast = (_tableExists(cur, "rating_outlier")
                  and _hasCol(cur, "rating_outlier", "side"))
    if shards is None:
        if persons:
            shards = 1
        else:
            est = _estimateRows(cur) // max(int(sample_mod or 1), 1)
            shards = max(1, math.ceil(est / SHARD_ROWS))
    cur.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    cur.execute(f"""CREATE TABLE {name} (
                        key bigint, sport text, result_id bigint,
                        person_id bigint, d date, rating real,
                        nb boolean, judge boolean)
                    PARTITION BY HASH (person_id)""")
    for i in range(shards):
        cur.execute(f"CREATE UNLOGGED TABLE {name}_{i} PARTITION OF {name} "
                    f"FOR VALUES WITH (MODULUS {shards}, REMAINDER {i})")
    parts = [s for s in (_sourceSelect(cur, sp, since, persons, sample_mod,
                                       prior_fast) for sp in ("XC", "TF")) if s]
    if not parts:
        return shards
    cur.execute(f"INSERT INTO {name} (key, sport, result_id, person_id, d, "
                f"rating, nb, judge) " + "\nUNION ALL\n".join(parts),
                {"persons": list(persons or [])})
    for i in range(shards):
        cur.execute(f"ANALYZE {name}_{i}")
    if verbose:
        cur.execute(f"SELECT count(*), count(*) FILTER (WHERE judge), "
                    f"count(*) FILTER (WHERE NOT nb) FROM {name}")
        n, nj, nf = cur.fetchone()
        print(f"    stage: {n:,} rated rows ({nj:,} judged, {nf:,} last run's "
              f"fast rows kept out of every neighbourhood) in {shards} "
              f"shard(s)", flush=True)
    return shards


# ---------------------------------------------------------------------- #
# the neighbourhood, set-based
# ---------------------------------------------------------------------- #

def _mid(arr, n):
    """The median of a SORTED SQL array of n elements, by index."""
    return f"(({arr})[({n} + 1) / 2] + ({arr})[{n} / 2 + 1]) / 2.0"


def nearSql(src, k, extra_where="", judged_only=True):
    """Per judged row of `src`: `o`, its K nearest rated neighbours by date,
    each coded gap_days * 1000 + its index in the window arrays rs / ds /
    ks, nearest first.

    ★ ONE WINDOW, ONE SMALL SORT. The window gathers the K rows either side
      (the K nearest are always among them); the lateral keeps the K
      nearest of those 2K, excluding the row itself. Nothing is joined to
      the 200M-row tables: the stage was read once. On a tie in days the
      earlier row wins -- the code orders by the index after the gap.

    ⚠ OFFSET 0 ON EVERY LATERAL, AND IT IS THE DIFFERENCE BETWEEN 16 AND
      132 SECONDS PER MILLION ROWS (measured 2026-09-29). A lateral with no
      FROM is pulled up into its parent, and its ARRAY(...) subquery is
      then pasted into every place the column is read -- the sort ran four
      times per row. OFFSET 0 is Postgres's own fence against that."""
    return f"""
        SELECT w.key, w.sport, w.result_id, w.person_id, w.d, w.rating,
               w.rs, w.ds, w.ks, sel.o, cardinality(sel.o) AS n
        FROM (
            SELECT s.*,
                   array_agg(s.d)      FILTER (WHERE s.nb) OVER win AS ds,
                   array_agg(s.rating) FILTER (WHERE s.nb) OVER win AS rs,
                   array_agg(s.key)    FILTER (WHERE s.nb) OVER win AS ks
            FROM   {src} s
            WINDOW win AS (PARTITION BY s.person_id ORDER BY s.d, s.key
                           ROWS BETWEEN {int(k)} PRECEDING
                                    AND {int(k)} FOLLOWING)
        ) w
        CROSS JOIN LATERAL (
            SELECT ARRAY(SELECT abs(w.ds[i] - w.d)::bigint * 1000 + i
                         FROM   generate_subscripts(w.rs, 1) i
                         WHERE  w.ks[i] <> w.key
                         ORDER  BY 1 LIMIT {int(k)}) AS o
            OFFSET 0) sel
        WHERE {'w.judge AND' if judged_only else ''}
              cardinality(sel.o) >= {MIN_NEIGHBOURS} {extra_where}"""


def zSql(src, k, floor, extra_where="", detail=False):
    """nearSql plus median, MAD and z, as array indexing on sorted arrays
    (percentile_cont per row cost twice as much). The floor is a literal:
    it was measured before this runs."""
    more = ("""
               , ARRAY(SELECT n.rs[(x % 1000)::int] FROM unnest(n.o) x) AS near
               , ARRAY(SELECT n.ds[(x % 1000)::int] FROM unnest(n.o) x) AS near_d
               , ARRAY(SELECT n.ks[(x % 1000)::int] FROM unnest(n.o) x) AS near_k"""
            if detail else "")
    sigma = f"GREATEST({MAD_TO_SIGMA} * a.mad, {float(floor)})"
    return f"""
        SELECT n.key, n.sport, n.result_id, n.person_id, n.d, n.rating, n.n,
               (n.o[n.n] / 1000)::int AS span, m.med,
               {MAD_TO_SIGMA} * a.mad AS s_local, {sigma} AS sigma,
               (n.rating - m.med) / {sigma} AS z {more}
        FROM ({nearSql(src, k, extra_where)}) n
        CROSS JOIN LATERAL (
            SELECT ARRAY(SELECT n.rs[(x % 1000)::int] FROM unnest(n.o) x
                         ORDER BY 1) AS s
            OFFSET 0) srt
        CROSS JOIN LATERAL (
            SELECT {_mid('srt.s', 'n.n')} AS med OFFSET 0) m
        CROSS JOIN LATERAL (
            SELECT {_mid('q.dv', 'n.n')} AS mad
            FROM (SELECT ARRAY(SELECT abs(x - m.med) FROM unnest(srt.s) x
                               ORDER BY 1) AS dv
                  OFFSET 0) q) a"""


# ---------------------------------------------------------------------- #
# calibration: K, then the floor
# ---------------------------------------------------------------------- #

def _medOfFirst(near, k):
    """SQL: the median of the first k entries of `near` (nearest first)."""
    return (f"(SELECT {_mid('q.s', k)} FROM (SELECT ARRAY(SELECT x FROM "
            f"unnest(({near})[1:{k}]) x ORDER BY x) AS s OFFSET 0) q)")


def calibrate(cur, src, k_fixed=None, verbose=True):
    """K and the floor from `src` (one partition of the stage: a hash
    sample of athletes). Returns {k, floor, curve, persons}. With k_fixed
    the curve is still printed and the floor is measured at k_fixed.

    ! EVERY STAGED RACE, NOT ONLY THE JUDGED ONES: under --since the judged
      rows are one year of it, and the floor is the corpus's, not a year's."""
    cur.execute(f"SELECT count(*) FROM {src}")
    n = _one(cur) or 0
    mod = max(1, round(n / CAL_ROWS))
    kmax = max(K_CANDIDATES)
    samp = f"AND abs(hashint8(w.person_id)) % {mod} = 0" if mod > 1 else ""
    cur.execute("DROP TABLE IF EXISTS ro_cal")
    # the neighbours' ratings, nearest first
    cur.execute(f"""CREATE TEMP TABLE ro_cal AS
        SELECT z.person_id, z.rating, z.n,
               ARRAY(SELECT z.rs[(u.x % 1000)::int]
                     FROM unnest(z.o) WITH ORDINALITY u(x, j)
                     ORDER BY u.j) AS near
        FROM ({nearSql(src, kmax, samp, judged_only=False)}) z""")
    # ★ EVERY K ON THE SAME RACES: those with all kmax neighbours, so the
    #   curve compares neighbourhoods and not samples.
    cur.execute(f"""
        WITH dv AS (
            SELECT kk.k, c.person_id,
                   abs(c.rating - {_medOfFirst('c.near', 'kk.k')}) AS ad
            FROM   ro_cal c CROSS JOIN unnest(%(ks)s::int[]) kk(k)
            WHERE  c.n >= %(kmax)s
        ), per AS (
            SELECT k, person_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ad) AS m
            FROM dv GROUP BY 1, 2)
        SELECT k, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY m)
        FROM per GROUP BY k ORDER BY k""",
                {"ks": list(K_CANDIDATES), "kmax": kmax})
    curve = [(int(k), int(p), float(e) if e is not None else None)
             for k, p, e in cur.fetchall()]
    k = k_fixed or pickK(curve) or max(K_CANDIDATES)
    # ★ THE FLOOR AT THE CHOSEN K, over every race that has K neighbours.
    cur.execute(f"""
        WITH per AS (
            SELECT person_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(
                       rating - {_medOfFirst('near', int(k))})) AS m
            FROM ro_cal WHERE n >= {int(k)} GROUP BY person_id)
        SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY m)
        FROM per""")
    persons, med_err = cur.fetchone()
    floor = MAD_TO_SIGMA * float(med_err) if med_err is not None else None
    cur.execute("DROP TABLE IF EXISTS ro_cal")
    if verbose:
        print(f"    calibration sample: 1 in {mod} athletes of {src} "
              f"({n:,} staged races)")
        print(f"    {'K':>4} {'athletes':>10} {'median |race - nbr median|':>28}")
        for kk, p, e in curve:
            mark = "  <- K" if kk == k else ""
            print(f"    {kk:>4} {p:>10,} {e if e is None else round(e, 3):>28}{mark}")
        print(f"    floor = 1.4826 x {med_err if med_err is None else round(float(med_err), 3)}"
              f" over {persons:,} athletes = "
              f"{floor if floor is None else round(floor, 3)} rating points")
    return {"k": k, "floor": floor, "curve": curve, "persons": persons,
            "sample_mod": mod}


# ---------------------------------------------------------------------- #
# the full pass
# ---------------------------------------------------------------------- #

CAND = "rating_outlier_cand"


def _judgeShard(cur, part, k, floor, keep_from):
    """One partition: z for every judged row into a temp table, its
    histogram back, its far rows into CAND. Returns (hist, n)."""
    hist = {"fast": {}, "slow": {}}
    cur.execute("DROP TABLE IF EXISTS ro_z")
    cur.execute(f"CREATE TEMP TABLE ro_z AS "
                f"SELECT sport, result_id, person_id, d, rating, n, span, "
                f"med, s_local, sigma, z FROM ({zSql(part, k, floor)}) q")
    cur.execute(f"""SELECT z >= 0, floor(abs(z) / {BIN})::int, count(*)
                    FROM ro_z GROUP BY 1, 2""")
    n = 0
    for fast, b, c in cur.fetchall():
        h = hist["fast" if fast else "slow"]
        h[b] = h.get(b, 0) + c
        n += c
    cur.execute(f"INSERT INTO {CAND} SELECT * FROM ro_z WHERE abs(z) >= %s",
                (keep_from,))
    cur.execute("DROP TABLE ro_z")
    return hist, n


def scanAll(cur, shards, k, floor, keep_from, name=STAGE, connect=None,
            streams=1, verbose=True):
    """Every shard: z for every judged row. Returns ({'fast': hist,
    'slow': hist}, n_judged); rows at |z| >= keep_from are left in CAND
    for the cut to choose from.

    ★ SHARDS SIDE BY SIDE (the pipeline's own XCP_STREAMS). A window is not
      parallel in Postgres, so one statement is one core; `streams`
      connections take a shard each. The stage is committed first so they
      can see it -- it is unlogged scratch, dropped at the end either way.
      connect() is a context manager yielding a connection."""
    hist = {"fast": {}, "slow": {}}
    total = 0
    cur.execute(f"DROP TABLE IF EXISTS {CAND}")
    cur.execute(f"""CREATE UNLOGGED TABLE {CAND} (
                       sport text, result_id bigint, person_id bigint, d date,
                       rating double precision, n int, span int,
                       med double precision, s_local double precision,
                       sigma double precision, z double precision)""")

    def merge(got, n, i, secs):
        nonlocal total
        for side in ("fast", "slow"):
            for b, c in got[side].items():
                hist[side][b] = hist[side].get(b, 0) + c
        total += n
        if verbose:
            print(f"    [{secs:8.1f}s] shard {i + 1}/{shards}: "
                  f"{n:,} races judged", flush=True)

    if connect is None or streams <= 1 or shards <= 1:
        for i in range(shards):
            t0 = time.time()
            got, n = _judgeShard(cur, f"{name}_{i}", k, floor, keep_from)
            merge(got, n, i, time.time() - t0)
        return hist, total

    cur.connection.commit()

    def work(i):
        from pg_guard import guard
        t0 = time.time()
        with connect() as wconn:
            with wconn.cursor() as wcur:
                guard(wcur, verbose=False)
                got, n = _judgeShard(wcur, f"{name}_{i}", k, floor, keep_from)
            wconn.commit()
        return got, n, i, time.time() - t0

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(streams, shards)) as pool:
        for got, n, i, secs in pool.map(work, range(shards)):
            merge(got, n, i, secs)
    return hist, total


def flaggedRows(cur, fast_cut, slow_cut):
    cur.execute(f"""
        SELECT result_id, sport, person_id,
               extract(year FROM d)::int, rating, med, sigma, n, z,
               CASE WHEN z > 0 THEN 'fast' ELSE 'slow' END, span
        FROM {CAND}
        WHERE z >= %s OR z <= -%s
        ORDER BY abs(z) DESC""", (fast_cut, slow_cut))
    return [tuple(r) for r in cur.fetchall()]


def write(cur, rows, calib):
    cur.execute(DDL)
    cur.execute("DELETE FROM rating_outlier")
    cut = {"fast": calib["fast_cut"], "slow": calib["slow_cut"]}
    cur.executemany("""
        INSERT INTO rating_outlier (result_id, sport, person_id, season,
                                    rating, med, sigma, n_races, z, side,
                                    span_days, cut)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id, sport) DO NOTHING
    """, [r + (cut[r[9]],) for r in rows])
    cur.execute("""INSERT INTO rating_outlier_calib
                       (k, floor, fast_cut, slow_cut, n_judged, detail)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (calib["k"], calib["floor"], calib["fast_cut"],
                 calib["slow_cut"], calib.get("n_judged"),
                 json.dumps(calib.get("detail") or {})))
    return len(rows)


# ---------------------------------------------------------------------- #
# the rows the neighbourhood cannot judge (cross country)
# ---------------------------------------------------------------------- #
# ★ WHERE THE MAIN RULE HAS NO OPINION (owner, 2026-11-07, the board audit:
#   Navarrete 169.2 with one other race at 71.9, Floris 14:46 in a girls'
#   pool, Monahan, a 9:12 3000 m in a field of two, adults and college men
#   on the middle school boards). The neighbourhood needs MIN_NEIGHBOURS
#   other races; most of those rows had 0-2. Three narrow rules, each a
#   side of its own so the page can say which:
#     thin   1-2 other races within THIN_DAYS, and this one THIN_GAP (log)
#            above the best of them: a wrong distance or another person's
#            row -- treated as 'fast' on the page
#     field  a division with under FIELD_MIN rated rows (nothing to read
#            the day from), and the row above the runner's own level by
#            FIELD_GAP, or the runner has no other race to say
#     level  a middle school or younger row in a race of LEVEL_MIN_M or
#            more, or in a division most of whose rated rows are college:
#            an adult or a college runner under a youth grade; or one with
#            NO grade in an open race (a quarter or more of its rated
#            rows high school or college) -- its level is a
#            guess from a club's name (Cole Hoff, "Bowerman Track", 14:06 at
#            the Bowerman 5K, owner 2026-11-07)
#   Rank-only, like every row here: the page keeps it and says why.
THIN_DAYS = 365
THIN_GAP = 0.30          # 35% above the best of their only other races
FIELD_MIN = 3
FIELD_GAP = 0.10
LEVEL_MIN_M = 6000
MIXED_MIN = 3            # rows of each gender (or older rows) to call a list mixed
MIXED_SHARE_INV = 10     # the smaller gender at least a tenth of the field
OPEN_SHARE_INV = 20      # older runners at least a twentieth of the field

EXTRA_SQL = r"""
    WITH x AS (
        SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id,
               left(r.date, 10)::date AS d, r.speed_rating AS s,
               split_part(COALESCE(r.rating_pool, ''), '|', 1) AS pool,
               (btrim(COALESCE(r.grade, '')) ~ '[0-9A-Za-z]') AS graded,
               __GENDER__ AS g,
               -- a graduation year for a grade, read as the grade it means in
               -- this academic year (July-June): class of 2010 in fall 2008
               -- is 11th grade
               CASE WHEN btrim(COALESCE(r.grade, '')) ~ '^(19|20)[0-9]{2}$'
                    THEN 12 - (btrim(r.grade)::int
                               - (extract(year FROM left(r.date, 10)::date - 182)::int + 1))
               END AS grad_grade
        FROM   results r
        __GENDER_JOIN__
        WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
          AND  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'),
    fld AS (
        SELECT source, meet_id, div_id, count(*) AS n,
               count(*) FILTER (WHERE pool LIKE 'college%%') AS n_college,
               count(*) FILTER (WHERE pool LIKE 'college%%' OR pool LIKE 'hs%%'
                                  OR pool LIKE 'pro%%') AS n_older,
               count(*) FILTER (WHERE g = 'M') AS n_m,
               count(*) FILTER (WHERE g = 'F') AS n_f
        FROM   x GROUP BY 1, 2, 3),
    -- ! UP TO SIX RACES, NOT THREE (owner's audit, 2026-11-09: Amy Soliz,
    --   Whiteface (TX), 163.6 against three other races at a median of 91,
    --   unflagged: four rows was past this rule and short of the
    --   neighbourhood's). The thin test below still asks for one or two
    --   other races within THIN_DAYS of this one.
    few AS (
        SELECT person_id FROM x GROUP BY 1 HAVING count(*) BETWEEN 2 AND 6),
    dist AS (
        SELECT div_id,
               CASE WHEN distance < 50 THEN distance * 1609.34 ELSE distance END AS m
        FROM   meets WHERE distance > 0),
    cand AS (
        SELECT x.*, f.n, f.n_college, f.n_older, f.n_m, f.n_f, dm.m
        FROM   x
        JOIN   fld f ON f.source = x.source AND f.meet_id = x.meet_id AND f.div_id = x.div_id
        LEFT   JOIN dist dm ON x.source = 'anet' AND dm.div_id = x.div_id
        WHERE  f.n < %(field_min)s
           OR  (least(f.n_m, f.n_f) >= %(mixed_min)s
                AND least(f.n_m, f.n_f) * %(mixed_share)s >= f.n_m + f.n_f)
           OR  x.person_id IN (SELECT person_id FROM few)
           OR  (x.pool IN ('ms_m', 'ms_f', 'elem_m', 'elem_f')
                AND (dm.m >= %(level_m)s OR f.n_college * 2 > f.n OR NOT x.graded
                     OR x.grad_grade BETWEEN 9 AND 12)))
    SELECT c.result_id, c.person_id, extract(year FROM c.d)::int, c.s,
           o.k, o.best, o.med, c.n, c.pool, c.m, c.n_college, c.graded, c.n_older,
           c.n_m, c.n_f,
           -- an ungraded youth row on a profile with high school or college
           -- races before it (David Bowdoin, 2009: a track mile, ms_m, on a
           -- profile that ran high school in 1994)
           (NOT c.graded AND c.pool IN ('ms_m', 'ms_f', 'elem_m', 'elem_f') AND EXISTS (
                SELECT 1 FROM results z
                WHERE  z.person_id = c.person_id AND z.result_id <> c.result_id
                  AND  z.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' AND left(z.date, 10)::date < c.d
                  AND  split_part(COALESCE(z.rating_pool, ''), '|', 1) ~ '^(hs|college)_')) AS older_before,
           c.grad_grade
    FROM   cand c
    CROSS  JOIN LATERAL (
        -- ! THE TABLE, NOT x: x is materialised (read three times), and a
        --   lateral on it has no index -- one scan per candidate
        SELECT count(*) AS k, max(y.speed_rating) AS best,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY y.speed_rating) AS med
        FROM   results y
        WHERE  y.person_id = c.person_id AND y.result_id <> c.result_id
          AND  y.speed_rating > 0 AND y.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          AND  abs(left(y.date, 10)::date - c.d) <= %(days)s) o
"""


def extraSide(s, k, best, med, n, pool, metres, n_college, graded=True, n_older=0,
              older_before=False, n_m=0, n_f=0, grad_grade=None):
    """The side for one candidate row of EXTRA_SQL, or None. Pure."""
    s = float(s)
    # ★ MEN AND WOMEN IN ONE RESULT LIST (owner's audit, 2026-11-09: the
    #   Coast-to-Coast Battle in Beantown "Combined 5k", its men at 15:31
    #   rated as college women; the Steve Reeder and Sagebrush
    #   invitationals, Utah State's women at the men's 7000 m and three of
    #   the top five college women all time). A combined list carries one
    #   distance and one division gender for two races: neither can be
    #   trusted for any row in it. MIXED_MIN rows of each and the smaller
    #   side MIXED_SHARE or more of the field -- a girl or two in a boys'
    #   race is not a combined list.
    n_m, n_f = int(n_m or 0), int(n_f or 0)
    if min(n_m, n_f) >= MIXED_MIN and min(n_m, n_f) * MIXED_SHARE_INV >= n_m + n_f:
        return "mixed"
    # ★ AN UNGRADED YOUTH ROW IN AN OPEN RACE (same audit: the Bowerman 5K
    #   "Adults and Youth" again -- Cole Hoff, a professional, 14:03 as a
    #   middle schooler; ten older runners in 107 was under the quarter and
    #   the tenth below). MIXED_MIN or more high school, college or pro rows
    #   and one in twenty of the field: the race is open, and no grade is no
    #   evidence of the youth level.
    if (pool in ("ms_m", "ms_f", "elem_m", "elem_f") and not graded and n
            and (n_older or 0) >= MIXED_MIN and (n_older or 0) * OPEN_SHARE_INV >= n):
        return "level"
    # ! AND AN UNGRADED YOUTH ROW NOTHING VOUCHES FOR (owner's audit,
    #   2026-11-09: the Bowerman 5K at Nike, 14:06 by 'Bowerman Track' and
    #   'ARC' with no grade and no other race, ranked as middle schoolers;
    #   a 2009 track mile, no grade, on a profile with 1994 high school
    #   races). No grade and no other race within THIN_DAYS in a race older
    #   runners ran too, or high school or college races before it, is no
    #   evidence of the youth level. (A one-race ungraded kid in a middle
    #   school race of middle schoolers stays.)
    # ★ A GRADUATION YEAR THAT SAYS HIGH SCHOOL (data audit, 2026-11-10:
    #   Scott Compton, Bentonville, grade "2010" on every 2008 row -- class of
    #   2010, an 11th grader that fall -- rated as a middle schooler, 16:25
    #   for 5000 m, #6, #15 and #19 on the middle school boys' all-time board
    #   and #2 by season). grade_sanity nulls a year for a grade (normGrade
    #   reads 1-12 only), so the season was placed with no grade at all; the
    #   year itself is the grade, read against the season it was run in.
    if pool in ("ms_m", "ms_f", "elem_m", "elem_f") and grad_grade is not None \
            and 9 <= int(grad_grade) <= 12:
        return "level"
    if pool in ("ms_m", "ms_f", "elem_m", "elem_f") and (
            (metres and float(metres) >= LEVEL_MIN_M) or (n and n_college * 2 > n)
            or (not graded and ((n and n_older * 4 >= n) or (not k and n and n_older * 10 >= n)
                                or older_before))):
        return "level"
    if k and 1 <= k <= 2 and best and math.log(s / float(best)) > THIN_GAP:
        return "thin"
    if n is not None and n < FIELD_MIN and (
            not k or (med and math.log(s / float(med)) > FIELD_GAP)):
        return "field"
    return None


# ★ WHO IS IN A LIST IS THE PERSON'S GENDER, NOT THE ROW'S POOL (data audit,
#   2026-11-10: the Coast-to-Coast Battle in Beantown "Combined 5k Women",
#   2022 -- 89 rows rated college_f, among them Boston University's and
#   Northeastern's men at 15:31-16:00, #9, #15 and #20 on the all-time college
#   women's board -- was never called mixed). A row's pool follows the
#   division's gender label wherever the person's own gender does not
#   overrule it, so a men's field filed under "Women" reads as women by pool.
#   person_gender is the runner's own (their profiles, all their races); the
#   pool's suffix speaks only for a person it has nothing on.
_POOL_GENDER = ("CASE right(split_part(COALESCE(r.rating_pool, ''), '|', 1), 2) "
                "WHEN '_m' THEN 'M' WHEN '_f' THEN 'F' END")


def extraSql(cur):
    """EXTRA_SQL with the gender each row is counted as: the person's
    (person_gender) where that table exists and knows them, else the pool's."""
    if _tableExists(cur, "person_gender"):
        return (EXTRA_SQL.replace("__GENDER_JOIN__",
                                  "LEFT JOIN person_gender pg ON pg.person_id = r.person_id")
                         .replace("__GENDER__", f"COALESCE(pg.gender, {_POOL_GENDER})"))
    return EXTRA_SQL.replace("__GENDER_JOIN__", "").replace("__GENDER__", _POOL_GENDER)


def extraRows(cur):
    """[(result_id, 'XC', person_id, season, rating, med, side)] for the
    rows the neighbourhood cannot judge."""
    cur.execute(extraSql(cur), {"field_min": FIELD_MIN, "level_m": LEVEL_MIN_M,
                                "days": THIN_DAYS, "mixed_min": MIXED_MIN,
                                "mixed_share": MIXED_SHARE_INV})
    out = []
    for (rid, pid, yr, s, k, best, med, n, pool, metres, n_col, graded, n_old,
         n_m, n_f, older, grad_grade) in cur.fetchall():
        side = extraSide(s, k, best, med, n, pool, metres, n_col, graded, n_old,
                         bool(older), n_m, n_f, grad_grade)
        if side:
            out.append((rid, "XC", pid, yr, float(s), med and float(med), side))
    return out


# ★ THE SAME TIME, RATED FAR APART (owner's audit, 2026-11-09: two Pacific
#   Coast Shock Waves girls at 177.8 and 177.6 -- 15:19 for '4699 m' in a
#   field whose median ran 24:27 -- while the same girl's 15:21 two weeks
#   earlier rated 141). One runner, the same time to the second within a
#   month (within COPY_TOL), cannot be a quarter better in one race than the other: one of the
#   two races has the wrong distance, and the high one is the board's. The
#   high row is flagged 'fast' when EVERY same-time race nearby rated more
#   than COPY_GAP below it. Only rows rated COPY_MIN or more are looked at:
#   the boards' end, where a wrong distance does its damage.
COPY_GAP = 0.18          # log: about 20% above its same-time twin
COPY_TOL = 0.003         # "the same time": within 0.3% (15:19.2 and 15:21.3)
COPY_MIN = 125.0
COPY_SQL = """
    WITH a AS (
        SELECT result_id, person_id, left(date, 10)::date AS d, speed_rating AS s,
               time_seconds AS t
        FROM   results
        WHERE  speed_rating >= %(min_s)s AND person_id IS NOT NULL AND time_seconds > 0
          AND  date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
    SELECT a.result_id, a.person_id, extract(year FROM a.d)::int, a.s, max(b.speed_rating)
    FROM   a
    JOIN   results b ON b.person_id = a.person_id AND b.result_id <> a.result_id
                    AND b.speed_rating > 0 AND b.time_seconds > 0
                    AND abs(b.time_seconds - a.t) < %(tol)s * a.t
                    AND b.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    AND abs(left(b.date, 10)::date - a.d) <= 31
    GROUP  BY 1, 2, 3, 4
    HAVING ln(a.s / max(b.speed_rating)) > %(gap)s
"""


def copySide(s, twin_best, gap=COPY_GAP):
    """'fast' when a row rates more than `gap` (log) above the best of its
    same-time races nearby. Pure."""
    if not s or not twin_best:
        return None
    return "fast" if math.log(float(s) / float(twin_best)) > gap else None


def copyRows(cur):
    """[(result_id, 'XC', person_id, season, rating, twin, 'fast')]."""
    cur.execute(COPY_SQL, {"min_s": COPY_MIN, "gap": COPY_GAP, "tol": COPY_TOL})
    return [(rid, "XC", pid, yr, float(s), float(tw), "fast")
            for rid, pid, yr, s, tw in cur.fetchall() if copySide(s, tw)]


# ★ ABOVE EVERYTHING ELSE THEY RAN (owner's audit, 2026-11-09: Morgan
#   Thompson, 7:38.2 for 2574 m in 2018, rated 168.4 -- 51 other cross
#   country races, none above 133.8 -- #1 on the middle school girls'
#   board). The neighbourhood missed it: with K = 3 its neighbours were
#   107.0, 128.2 and 133.8, the spread came out 8.3 and the z +4.85, under
#   the +8 cut -- one noisy neighbour hides a 27% jump. Her own career is
#   the decider: a race more than CAREER_GAP (log, about 20%) above the BEST
#   of every other race within CAREER_DAYS either side, with CAREER_MIN or
#   more of them, is a wrong time or distance. A real breakout is followed
#   or preceded by races near it; a race a fifth better than anything in
#   four years is not run. Rows rated COPY_MIN or more: the boards' end.
CAREER_GAP = 0.18
CAREER_DAYS = 730
CAREER_MIN = 3
CAREER_SQL = """
    WITH a AS (
        SELECT result_id, person_id, left(date, 10)::date AS d, speed_rating AS s
        FROM   results
        WHERE  speed_rating >= %(min_s)s AND person_id IS NOT NULL
          AND  date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
    SELECT a.result_id, a.person_id, extract(year FROM a.d)::int, a.s, o.best, o.k
    FROM   a
    CROSS  JOIN LATERAL (
        SELECT max(b.speed_rating) AS best, count(*) AS k
        FROM   results b
        WHERE  b.person_id = a.person_id AND b.result_id <> a.result_id
          AND  b.speed_rating > 0 AND b.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          AND  abs(left(b.date, 10)::date - a.d) <= %(days)s) o
    WHERE  o.k >= %(k)s AND ln(a.s / o.best) > %(gap)s
"""


def careerSide(s, best, k, gap=CAREER_GAP, k_min=CAREER_MIN):
    """'fast' when a row rates more than `gap` (log) above the best of its
    `k` other races nearby, k_min or more. Pure."""
    if not s or not best or (k or 0) < k_min:
        return None
    return "fast" if math.log(float(s) / float(best)) > gap else None


def careerRows(cur):
    """[(result_id, 'XC', person_id, season, rating, best, 'fast')]."""
    cur.execute(CAREER_SQL, {"min_s": COPY_MIN, "days": CAREER_DAYS,
                             "k": CAREER_MIN, "gap": CAREER_GAP})
    return [(rid, "XC", pid, yr, float(s), float(best), "fast")
            for rid, pid, yr, s, best, k in cur.fetchall() if careerSide(s, best, k)]


# ★ A WHOLE FIELD RATED FAR ABOVE ITSELF (data audit, 2026-11-10: Whitney
#   Point Invitational 2018, "Modified Boys" and "Modified Girls" at
#   "3000 m" -- 9:23.6 and 10:24.1 winners, four rows on the middle school
#   all-time top 25 -- whose runners rated a median +46 and +38 points above
#   their own other races; a short course under a 3000 m label, which
#   wrong_distance did not catch because its runners had too few other
#   races that season to testify one by one). Whatever made it -- a wrong
#   distance, a short course, a day term the division did not run -- the
#   evidence is the same and needs no cause: the field's own runners, each
#   against the mean of their OTHER races that season in the same pool, sit
#   a median RACE_GAP (log) or more above themselves, RACE_SHARE of them up.
#   One runner can have the race of their life; a field of RACE_MIN cannot.
#   Every rated row of the division is 'race': kept on the page, off the
#   boards. Rank-only, like everything here: the solve never reads it.
#
#   ! THE MEAN OF THE OTHER DAYS, IN LOG, BY WINDOW SUMS: one pass and two
#     sorts over the cross country table instead of a lookup per row. A
#     runner's one bad reference is one vote among RACE_MIN; the field's
#     median is what decides.
#   ! THE SAME POOL ONLY: an 8th grader's ninth-grade races are on another
#     pool's scale, and a ratio across scales is not a gap.
#   ! THE SEASON IS JULY TO JUNE, so a February 2021 college race and the
#     spring high school seasons of that year sit with the fall before.
#   ! LAST RUN'S FLAGGED ROWS ARE NO ONE'S REFERENCE, but every row is still
#     judged: a division flagged last night is judged again tonight on the
#     same evidence and stays flagged, or is cleared.
RACE_MIN = 8             # runners with a reference before a field is judged
RACE_GAP = 0.08          # median log(rating / own other races): about 11 points at 140
RACE_SHARE = 0.75        # ... and this share of them 2% or more up
RACE_UP = 0.02
RACE_REF_MIN = 1         # other days that season, same pool, for a reference
RACE_SQL = """
    WITH x AS (
        SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id,
               left(r.date, 10)::date AS d,
               extract(year FROM left(r.date, 10)::date - 182)::int AS yr,
               split_part(COALESCE(r.rating_pool, ''), '|', 1) AS pool,
               r.speed_rating AS s, ln(r.speed_rating) AS ls,
               __OK__ AS ok
        FROM   results r
        WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
          AND  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' __NOT_FLAGGED__),
    w AS (
        SELECT x.*,
               sum(ls)  FILTER (WHERE ok) OVER (PARTITION BY person_id, pool, yr)    AS s_all,
               count(*) FILTER (WHERE ok) OVER (PARTITION BY person_id, pool, yr)    AS n_all,
               sum(ls)  FILTER (WHERE ok) OVER (PARTITION BY person_id, pool, yr, d) AS s_day,
               count(*) FILTER (WHERE ok) OVER (PARTITION BY person_id, pool, yr, d) AS n_day
        FROM   x),
    g AS (
        SELECT source, meet_id, div_id,
               ls - (s_all - COALESCE(s_day, 0)) / (n_all - n_day) AS gap
        FROM   w
        WHERE  n_all - n_day >= %(ref_min)s),
    fld AS (
        SELECT source, meet_id, div_id, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS med,
               avg((gap >= %(up)s)::int) AS share
        FROM   g GROUP BY 1, 2, 3
        HAVING count(*) >= %(min)s)
    SELECT x.result_id, x.person_id, x.yr, x.s, f.n, f.med, f.share
    FROM   fld f
    JOIN   x ON x.source = f.source AND x.meet_id = f.meet_id AND x.div_id = f.div_id
    WHERE  f.med >= %(gap)s AND f.share >= %(share)s
"""


def raceSide(n, med, share, n_min=RACE_MIN, gap=RACE_GAP, share_min=RACE_SHARE):
    """'race' when a division's n_min+ referenced runners sit a median `gap`
    (log) or more above their own other races, share_min of them up. Pure."""
    if (n or 0) < n_min or med is None or share is None:
        return None
    return "race" if float(med) >= gap and float(share) >= share_min else None


def raceSql(cur):
    """RACE_SQL with last run's flags (no one's reference) and the rows
    already excluded everywhere (twins, impossible races) left out."""
    ok, flagged = "true", ""
    if _tableExists(cur, "rating_outlier"):
        ok = ("NOT EXISTS (SELECT 1 FROM rating_outlier po WHERE po.sport = 'XC' "
              "AND po.result_id = r.result_id)")
    for t in ("result_twin", "impossible_result"):
        if _tableExists(cur, t):
            flagged += (f"\n          AND NOT EXISTS (SELECT 1 FROM {t} q WHERE "
                        f"q.sport = 'XC' AND q.result_id = r.result_id)")
    return RACE_SQL.replace("__OK__", ok).replace("__NOT_FLAGGED__", flagged)


def raceRows(cur):
    """[(result_id, 'XC', person_id, season, rating, field median gap, 'race')]."""
    cur.execute(raceSql(cur), {"ref_min": RACE_REF_MIN, "up": RACE_UP,
                               "min": RACE_MIN, "gap": RACE_GAP, "share": RACE_SHARE})
    return [(rid, "XC", pid, yr, float(s), float(med), "race")
            for rid, pid, yr, s, n, med, share in cur.fetchall()
            if raceSide(n, med, share)]


# ★ A DAY THE DIVISION DID NOT RUN (data audit, 2026-11-10: the Kentucky
#   state meet, Nov 2 1991, "Day slow 8.3%". Its three girls' races are
#   stored at 3000 m with winners at 14:44-15:15 and its Boys A at 4000 m
#   with a 15:54 winner -- longer races under shorter labels, so four of
#   six divisions read slow, and the venue-day term followed the majority.
#   The two 5000 m boys' races carry that +8.3% in every rating: Jeremy
#   Kazlauskas 15:39 rated #2 all time among high school boys, three
#   teammates of the field in the top 25). The solve's own day guard
#   (joint_solve.raceDayDivisions) reads each division's day off its rows
#   and writes the ones far from their venue-day to race_day_suspect_division
#   -- and those rows still RECEIVE the venue-day in their rating. A
#   division whose own day ran DAY_GAP (log) or more FASTER than the day it
#   was credited is rated that much too high: off the boards. One that ran
#   slower is rated low, which is safe, and stays.
#   ! READ, NOT REFIT: the table is the last go-live's; nothing in the solve
#     changes. Absent table, no rows.
DAY_GAP = 0.05
DAY_SQL = """
    SELECT r.result_id, r.person_id,
           extract(year FROM left(r.date, 10)::date)::int, r.speed_rating,
           COALESCE(s.log_excess, s.excess)
    FROM   race_day_suspect_division s
    JOIN   results r ON r.meet_id = s.meet_id AND r.div_id = s.div_id
                    AND sign(r.result_id) = sign(COALESCE(s.sample_result_id, r.result_id))
    WHERE  s.sport = 'XC' AND r.speed_rating > 0 AND r.person_id IS NOT NULL
      AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
      AND  COALESCE(s.log_excess, s.excess) <= -%(gap)s
"""


def daySide(excess, gap=DAY_GAP):
    """'day' when a division ran `gap` (log) or more faster than the day term
    its ratings carry. Pure."""
    if excess is None:
        return None
    return "day" if float(excess) <= -gap else None


def dayRows(cur):
    """[(result_id, 'XC', person_id, season, rating, excess, 'day')]."""
    if not _tableExists(cur, "race_day_suspect_division"):
        return []
    cur.execute(DAY_SQL, {"gap": DAY_GAP})
    return [(rid, "XC", pid, yr, float(s), float(ex), "day")
            for rid, pid, yr, s, ex in cur.fetchall() if daySide(ex)]


# ★ ABOVE BOTH OF THE RUNNER'S RACES EITHER SIDE OF IT (data audit,
#   2026-11-10: Adrian Suarez, HEB Zone meet Dec 3 2019, 10:05.3 for 3200 m
#   in an 8th-grade B race -- second place 13:47.7 -- rated 159.6, his only
#   other races three weeks either side at 13:59 and 12:54, #1 by season and
#   #4 by race on the middle school boys' all-time boards). Two other races
#   is under the neighbourhood's MIN_NEIGHBOURS and the career rule's
#   CAREER_MIN, and his jump (24%) is under the thin rule's 30%. Two races
#   within CLOSE_DAYS, in the same pool, BOTH more than CAREER_GAP (log)
#   below it: a fifth faster than everything they ran within two months is
#   a wrong time or a wrong course, not a breakthrough -- a breakthrough
#   holds for the next race. The boards' end only (COPY_MIN), as career.
CLOSE_DAYS = 60
CLOSE_MIN = 2
CLOSE_SQL = """
    WITH a AS (
        SELECT result_id, person_id, left(date, 10)::date AS d, speed_rating AS s,
               split_part(COALESCE(rating_pool, ''), '|', 1) AS pool
        FROM   results
        WHERE  speed_rating >= %(min_s)s AND person_id IS NOT NULL
          AND  date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
    SELECT a.result_id, a.person_id, extract(year FROM a.d)::int, a.s, o.best, o.k
    FROM   a
    CROSS  JOIN LATERAL (
        SELECT max(b.speed_rating) AS best, count(*) AS k
        FROM   results b
        WHERE  b.person_id = a.person_id AND b.result_id <> a.result_id
          AND  b.speed_rating > 0 AND b.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          AND  left(b.date, 10)::date <> a.d
          AND  split_part(COALESCE(b.rating_pool, ''), '|', 1) = a.pool
          AND  abs(left(b.date, 10)::date - a.d) <= %(days)s) o
    WHERE  o.k >= %(k)s AND ln(a.s / o.best) > %(gap)s
"""


def closeRows(cur):
    """[(result_id, 'XC', person_id, season, rating, best, 'fast')]."""
    cur.execute(CLOSE_SQL, {"min_s": COPY_MIN, "days": CLOSE_DAYS,
                            "k": CLOSE_MIN, "gap": CAREER_GAP})
    return [(rid, "XC", pid, yr, float(s), float(best), "fast")
            for rid, pid, yr, s, best, k in cur.fetchall()
            if careerSide(s, best, k, k_min=CLOSE_MIN)]


# ★ TWO RACES IN ONE LIST, BY THE TIMES THEMSELVES (owner's server read,
#   2026-11-10, scripts/board_suspects.py on the Coast-to-Coast Battle in
#   Beantown "Combined 5k Women", 2022: 88 rows "college_f F", 1 "college_m
#   M" -- the men's persons were MADE from this women's list, so their own
#   gender says F and no gender count can see them; #9, #15, #20 on the
#   all-time college women's board). The list says it itself: 57 men from
#   15:31 to 17:47, then nobody for 7%, then the women from 19:04. A race
#   thins out at its tail, never in its middle: a gap of GAP2_MIN (log) or
#   more between consecutive finishers with GAP2_SIDE of the field on both
#   sides of it is two races. Every row is 'mixed': one distance and one
#   label for two fields.
GAP2_N = 30            # finishers before a list is read for two races
GAP2_MIN = 0.05        # the gap, log time: about a minute at 19:00
GAP2_SIDE = 0.20       # of the field, and GAP2_SIDE_MIN rows, on each side
GAP2_SIDE_MIN = 10
GAP2_SQL = """
    WITH t AS (
        SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id,
               left(r.date, 10) AS day, r.speed_rating AS s,
               row_number() OVER w AS rn,
               count(*) OVER (PARTITION BY r.source, r.meet_id, r.div_id) AS n,
               ln(r.time_seconds) - ln(lag(r.time_seconds) OVER w) AS gap
        FROM   results r
        WHERE  r.speed_rating > 0 AND r.time_seconds > 0 AND r.person_id IS NOT NULL
          AND  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        WINDOW w AS (PARTITION BY r.source, r.meet_id, r.div_id
                     ORDER BY r.time_seconds, r.result_id)),
    cut AS (
        SELECT DISTINCT source, meet_id, div_id
        FROM   t
        WHERE  n >= %(n)s AND gap >= %(gap)s
          AND  rn - 1     >= greatest(%(side_min)s, n * %(side)s)
          AND  n - rn + 1 >= greatest(%(side_min)s, n * %(side)s))
    SELECT t.result_id, t.person_id, extract(year FROM t.day::date)::int, t.s
    FROM   t JOIN cut USING (source, meet_id, div_id)
"""


# ★ AND BY FIRST NAMES, WHICH NEED NO GENDER FROM THE LIST ITSELF (owner,
#   2026-11-10: "any other way tho this is kind of specific"). A person
#   made from one combined list carries that list's label as their gender,
#   but their first name carries the corpus's: Aksel, Malav, Henry, Tucker
#   are men's names in every other race on file. A first name shared by
#   NAME_MIN or more people of known gender, NAME_SURE of them one gender,
#   speaks for a row; a list with MIXED_MIN rows of each and the smaller a
#   MIXED_SHARE_INV-th of the named rows is two fields: 'mixed'. Names that
#   go both ways (Taylor, Jordan) say nothing. Split people are left out of
#   the name table.
NAME_MIN = 50
NAME_SURE = 0.97
NAMES_SQL = """
    WITH fn AS (
        SELECT lower(split_part(btrim(an.name), ' ', 1)) AS f,
               avg((pg.gender = 'M')::int) AS m
        FROM   athlete_named an
        JOIN   person_gender pg ON pg.person_id = an.person_id
        WHERE  NOT COALESCE(pg.split, false) AND an.name ~ '^[A-Za-z]'
        GROUP  BY 1 HAVING count(*) >= %(name_min)s),
    x AS (
        SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id,
               left(r.date, 10) AS day, r.speed_rating AS s,
               CASE WHEN fn.m >= %(sure)s THEN 'M' WHEN fn.m <= 1 - %(sure)s THEN 'F' END AS g
        FROM   results r
        LEFT   JOIN athlete_named an ON an.person_id = r.person_id
        LEFT   JOIN fn ON fn.f = lower(split_part(btrim(COALESCE(an.name, r.athlete_name, '')), ' ', 1))
        WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
          AND  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'),
    dv AS (
        SELECT source, meet_id, div_id
        FROM   x GROUP BY 1, 2, 3
        HAVING least(count(*) FILTER (WHERE g = 'M'), count(*) FILTER (WHERE g = 'F')) >= %(min)s
           AND least(count(*) FILTER (WHERE g = 'M'), count(*) FILTER (WHERE g = 'F')) * %(share)s
               >= count(*) FILTER (WHERE g IS NOT NULL))
    SELECT x.result_id, x.person_id, extract(year FROM x.day::date)::int, x.s
    FROM   x JOIN dv USING (source, meet_id, div_id)
"""


def nameMixSide(n_mname, n_fname, n_min=MIXED_MIN, share_inv=MIXED_SHARE_INV):
    """'mixed' when a list holds n_min+ rows of each gender's first names and
    the smaller side is a share_inv-th of the named rows. Pure."""
    lo = min(int(n_mname or 0), int(n_fname or 0))
    return "mixed" if lo >= n_min and lo * share_inv >= (n_mname or 0) + (n_fname or 0) else None


def nameMixRows(cur):
    """[(result_id, 'XC', person_id, season, rating, None, 'mixed')]; empty
    where the name or gender table is not built."""
    if not (_tableExists(cur, "athlete_named") and _tableExists(cur, "person_gender")):
        return []
    cur.execute(NAMES_SQL, {"name_min": NAME_MIN, "sure": NAME_SURE,
                            "min": MIXED_MIN, "share": MIXED_SHARE_INV})
    return [(rid, "XC", pid, yr, float(s), None, "mixed")
            for rid, pid, yr, s in cur.fetchall()]


def twoRacesSide(n, gap, before, after, n_min=GAP2_N, gap_min=GAP2_MIN,
                 side=GAP2_SIDE, side_min=GAP2_SIDE_MIN):
    """'mixed' when a gap of gap_min between consecutive finishers has
    `before` finishers ahead of it and `after` behind, each at least side of
    the field (and side_min). Pure."""
    if (n or 0) < n_min or gap is None or gap < gap_min:
        return None
    need = max(side_min, n * side)
    return "mixed" if before >= need and after >= need else None


def twoRaceRows(cur):
    """[(result_id, 'XC', person_id, season, rating, None, 'mixed')]."""
    cur.execute(GAP2_SQL, {"n": GAP2_N, "gap": GAP2_MIN, "side": GAP2_SIDE,
                           "side_min": GAP2_SIDE_MIN})
    return [(rid, "XC", pid, yr, float(s), None, "mixed")
            for rid, pid, yr, s in cur.fetchall()]


# ★ THE MEET'S OTHER RACES SAY WHICH LABEL IS WRONG (owner's server read,
#   2026-11-10: Whitney Point 2018 -- Jalen Robinson's "other races: 0",
#   so no runner-by-runner reference exists, and the Modified boys' 3000 m
#   top three ran a 5000 m-equivalent 17% FASTER than the varsity boys' at
#   the same meet that day; middle schoolers do not outrun the high
#   school's varsity by a sixth. Kentucky 1991 -- the girls' "3000 m" top
#   three a 5000 m-equivalent 60% slower than the boys', Boys A "4000 m"
#   27% slower than Boys AAA: four short labels, the majority of the day's
#   rows, so the solve's day guard kept THEM and set the 5000 m boys aside).
#   Each division with SIB_MIN_N rated rows gets its third-fastest time as a
#   5000 m equivalent (t * (5000 / d) ^ SIB_K) -- the leaders, not the tail,
#   which JV and open fields differ in -- and is compared only where the
#   order is certain:
#     fast  a division FASTER by SIB_FAST than every division that must be
#           faster than it: older, same gender (middle school vs high
#           school, high school vs college), or men of its level or older
#           for a women's division. Its label is longer than the course:
#           every row 'race'.
#     slow  a division SLOWER by SIB_SLOW_F than the same level's men (a
#           women's division) or SIB_SLOW than the fastest division of its
#           own gender and level: a label shorter than the course. When the
#           slow divisions hold a majority of the meet-day's rows, the
#           venue-day followed them (the guard's majority rule), and the
#           OTHER divisions' rows carry a slow day they did not run: 'day'.
#           The slow rows themselves are rated low, which is safe.
#   ! anet only: the meets table holds its distances. A distance outside
#     SIB_RANGE, or a level the pools cannot name, is not compared.
SIB_K = 1.06
SIB_MIN_N = 5
SIB_FAST = 0.05
SIB_SLOW = 0.25
SIB_SLOW_F = 0.40
SIB_RANGE = (1500.0, 12000.0)
_LEVEL_ORDER = {"elem": 0, "ms": 1, "hs": 2, "college": 3}
SIB_SQL = """
    WITH x AS (
        SELECT r.source, r.meet_id, r.div_id, left(r.date, 10) AS day,
               split_part(split_part(COALESCE(r.rating_pool, ''), '|', 1), '_', 1) AS lvl,
               right(split_part(COALESCE(r.rating_pool, ''), '|', 1), 2) AS sfx,
               r.time_seconds AS ts
        FROM   results r
        WHERE  r.source = 'anet' AND r.speed_rating > 0 AND r.time_seconds > 0
          AND  r.meet_id IS NOT NULL AND r.div_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'),
    dv AS (
        SELECT source, meet_id, div_id, day,
               mode() WITHIN GROUP (ORDER BY lvl) AS lvl,
               mode() WITHIN GROUP (ORDER BY sfx) AS sfx,
               count(*) AS n,
               (array_agg(ts ORDER BY ts))[3] AS t3
        FROM   x GROUP BY 1, 2, 3, 4
        HAVING count(*) >= %(min_n)s),
    d AS (
        SELECT div_id,
               max(CASE WHEN distance < 50 THEN distance * 1609.34 ELSE distance END) AS m
        FROM   meets WHERE distance > 0 GROUP BY 1)
    SELECT dv.source, dv.meet_id, dv.div_id, dv.day, dv.lvl, dv.sfx, dv.n, dv.t3, d.m
    FROM   dv JOIN d ON d.div_id = dv.div_id
    WHERE  d.m BETWEEN %(lo)s AND %(hi)s
"""


def _e5(t3, metres, k=SIB_K):
    return math.log(float(t3)) + k * math.log(5000.0 / float(metres))


def siblingVerdicts(divs, fast=SIB_FAST, slow=SIB_SLOW, slow_f=SIB_SLOW_F):
    """divs: [(source, meet_id, div_id, day, lvl, sfx, n, t3, metres)].
    Returns {(source, meet_id, div_id): 'race' | 'day'}. Pure.

    'race' for a division faster than every division that must be faster;
    'day' for the sound divisions of a meet-day whose too-slow divisions
    hold the majority of its rows."""
    by = {}
    for src, meet, div, day, lvl, sfx, n, t3, m in divs:
        if lvl not in _LEVEL_ORDER or sfx not in ("_m", "_f") or not t3 or not m:
            continue
        by.setdefault((src, meet, day), []).append(
            (div, _LEVEL_ORDER[lvl], sfx, int(n), _e5(t3, m)))
    out = {}
    for (src, meet, day), ds in by.items():
        if len(ds) < 2:
            continue
        slow_divs = set()
        for div, lv, sfx, n, e in ds:
            # must be faster: the same gender older, and (for a women's
            # division) the men of its level or older -- each relation by its
            # own fastest division, so one wrong label among them (the
            # Modified Boys beside the Modified Girls) cannot hide another
            older = [e2 for d2, lv2, sfx2, _n2, e2 in ds
                     if d2 != div and sfx2 == sfx and lv2 > lv]
            men = [e2 for d2, lv2, sfx2, _n2, e2 in ds
                   if d2 != div and sfx == "_f" and sfx2 == "_m" and lv2 >= lv]
            if any(g and e < min(g) - fast for g in (older, men)):
                out[(src, meet, div)] = "race"
                continue
            peers = [e2 for d2, lv2, sfx2, _n2, e2 in ds
                     if d2 != div and lv2 == lv and sfx2 == sfx]
            men_lv = [e2 for d2, lv2, sfx2, _n2, e2 in ds
                      if d2 != div and lv2 == lv and sfx == "_f" and sfx2 == "_m"]
            if (peers and e - min(peers) > slow) or (men_lv and e - min(men_lv) > slow_f):
                slow_divs.add(div)
        total = sum(n for _d, _l, _s, n, _e in ds)
        bad = sum(n for d, _l, _s, n, _e in ds if d in slow_divs)
        if slow_divs and bad * 2 > total:
            for div, _lv, _sfx, _n, _e in ds:
                if div not in slow_divs and (src, meet, div) not in out:
                    out[(src, meet, div)] = "day"
    return out


def siblingRows(cur):
    """[(result_id, 'XC', person_id, season, rating, None, side)] for every
    rated row of the divisions siblingVerdicts names."""
    cur.execute(SIB_SQL, {"min_n": SIB_MIN_N, "lo": SIB_RANGE[0], "hi": SIB_RANGE[1]})
    verdict = siblingVerdicts(cur.fetchall())
    if not verdict:
        return []
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS ro_sibling "
                "(source text, meet_id bigint, div_id bigint, side text)")
    cur.execute("TRUNCATE ro_sibling")
    cur.executemany("INSERT INTO ro_sibling VALUES (%s, %s, %s, %s)",
                    [(s, m, d, side) for (s, m, d), side in verdict.items()])
    cur.execute("""
        SELECT r.result_id, r.person_id, extract(year FROM left(r.date, 10)::date)::int,
               r.speed_rating, v.side
        FROM   ro_sibling v
        JOIN   results r ON r.source = v.source AND r.meet_id = v.meet_id AND r.div_id = v.div_id
        WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
          AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'""")
    return [(rid, "XC", pid, yr, float(s), None, side)
            for rid, pid, yr, s, side in cur.fetchall()]


def writeExtra(cur, rows):
    cur.executemany("""
        INSERT INTO rating_outlier (result_id, sport, person_id, season, rating, med, side)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id, sport) DO NOTHING""", rows)
    return len(rows)


def printExtra(rows, show):
    from collections import Counter
    by = Counter(r[6] for r in rows)
    print(f"\n  rows the neighbourhood cannot judge: {len(rows):,} "
          f"({', '.join(f'{k} {v:,}' for k, v in sorted(by.items()))})")
    for side in ("mixed", "level", "thin", "field", "race", "day"):
        top = sorted((r for r in rows if r[6] == side), key=lambda r: -r[4])[:show]
        what = {"race": "the field's median log gap over itself",
                "day": "the division's day less the day it carries"}.get(
                    side, "their other races' median")
        for r in top:
            print(f"    {side:<5} result {r[0]}  /athlete/{r[2]}  {r[3]}  rating {r[4]:.1f}"
                  f"  ({what} {r[5] if r[5] is None else round(r[5], 3 if side in ('race', 'day') else 1)})")


def dropStage(cur, name=STAGE):
    """The scratch tables of a pass: the stage (and its partitions) and,
    for the main pass, the candidate table."""
    cur.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    if name == STAGE:
        cur.execute(f"DROP TABLE IF EXISTS {CAND}")


# ---------------------------------------------------------------------- #
# the report
# ---------------------------------------------------------------------- #

def printDistribution(hist, total, bars):
    print(f"\n  z distribution over {total:,} judged races "
          f"(z = (race - neighbours' median) / spread):")
    print(f"    {'|z| >=':>7} {'fast':>12} {'slow':>12}   {'share':>9}")
    for c in (1, 2, 3, 4) + tuple(bars) + (20, 30):
        f, s = survival(hist["fast"], c), survival(hist["slow"], c)
        pct = 100.0 * (f + s) / total if total else 0.0
        print(f"    {c:>7g} {f:>12,} {s:>12,}   {pct:8.4f}%")


def printCut(side, cut, why, rows):
    print(f"\n  {side} cut: {cut:g} sigma -- {why}")
    if rows:
        print(f"    {'c':>6} {'past c':>10} {'genuine expected':>18}")
        for c, seen, g in rows:
            if abs(c - round(c)) < 1e-9:
                print(f"    {c:>6g} {seen:>10,} {g:>18.2f}"
                      + ("   <- cut" if abs(c - cut) < 1e-9 else ""))


def printSamples(rows, show):
    for side in ("fast", "slow"):
        got = [r for r in rows if r[9] == side][:show]
        if not got:
            continue
        print(f"\n  the {len(got)} furthest {side}:")
        print(f"    {'result':>12} {'sport':<5} {'person':>10} {'yr':>5} "
              f"{'rating':>8} {'median':>8} {'sigma':>6} {'n':>3} "
              f"{'span d':>6} {'z':>7}")
        for r in got:
            print(f"    {r[0]:>12} {r[1]:<5} {str(r[2]):>10} {r[3]:>5} "
                  f"{r[4]:>8.2f} {r[5]:>8.2f} {r[6]:>6.2f} {r[7]:>3} "
                  f"{str(r[10]):>6} {r[8]:>7.1f}")


def latestCalib(cur):
    if not _tableExists(cur, "rating_outlier_calib"):
        return None
    cur.execute("""SELECT k, floor, fast_cut, slow_cut FROM rating_outlier_calib
                   ORDER BY built DESC LIMIT 1""")
    row = cur.fetchone()
    if not row:
        return None
    row = list(row.values()) if isinstance(row, dict) else list(row)
    return {"k": row[0], "floor": row[1], "fast_cut": row[2],
            "slow_cut": row[3]}


def explain(cur, person, k, floor, fast_cut, slow_cut):
    """Every race of one athlete: its neighbours, their median, the spread
    and z. Returns the rows (for tests)."""
    name = STAGE + "_explain"
    buildStage(cur, persons=[person], shards=1, name=name, verbose=False)
    cur.execute(f"SELECT key, sport, result_id, d, rating, judge FROM {name} "
                f"ORDER BY d, key")
    races = cur.fetchall()
    by_key = {r[0]: r for r in races}
    cur.execute(zSql(f"{name}_0", k, floor, detail=True))
    cols = [c[0] for c in cur.description]
    got = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    print(f"\n  athlete {person}: K = {k}, floor = {floor:.2f}, cuts "
          f"+{fast_cut:g} fast / -{slow_cut:g} slow\n")
    out = []
    for key, sport, rid, d, rating, _judge in races:
        z = got.get(key)
        if z is None:
            print(f"  {d} {sport:<2} {rid:>12} {rating:7.2f}   "
                  f"fewer than {MIN_NEIGHBOURS} neighbours: no opinion")
            out.append({"result_id": rid, "sport": sport, "z": None,
                        "side": None})
            continue
        side = ("fast" if z["z"] >= fast_cut else
                "slow" if z["z"] <= -slow_cut else None)
        print(f"  {d} {sport:<2} {rid:>12} {rating:7.2f}   median "
              f"{z['med']:7.2f}  spread {z['s_local']:5.2f} -> "
              f"{z['sigma']:5.2f}  z {z['z']:+6.2f}"
              + (f"   ** {side.upper()} **" if side else ""))
        nbrs = sorted(zip(z["near_d"], z["near_k"], z["near"]))
        print("        neighbours: " + ", ".join(
            f"{nd} {by_key[nk][1]} {nr:.1f}" for nd, nk, nr in nbrs))
        out.append({"result_id": rid, "sport": sport, "z": z["z"],
                    "med": z["med"], "sigma": z["sigma"], "n": z["n"],
                    "side": side})
    dropStage(cur, name)
    return out


# ---------------------------------------------------------------------- #

def run(cur, since=None, k=None, floor=None, fast_sigma=None,
        slow_sigma=None, bars=(5, 6, 8, 10, 15), show=15, shards=None,
        connect=None, streams=1, verbose=True):
    """The whole pass. Returns (flagged rows, calib); the scratch tables
    are left for dropStage. With connect and streams > 1 the shards are
    judged side by side (see scanAll)."""
    t_all = time.time()
    with _Clock("stage: both tables read once"):
        shards = buildStage(cur, since=since, shards=shards,
                            verbose=verbose)
    cal = {"k": k, "floor": floor}
    if k is None or floor is None:
        with _Clock("calibration: K and the floor"):
            got = calibrate(cur, f"{STAGE}_0", k_fixed=k, verbose=verbose)
        cal["k"] = got["k"]
        if floor is None:
            cal["floor"] = got["floor"]
        cal["detail"] = {"curve": got["curve"], "persons": got["persons"],
                         "sample_mod": got["sample_mod"]}
    if cal["floor"] is None:
        cal["floor"] = 1.0
        print("    ! no athlete had K neighbours to measure a floor on; "
              "using 1 rating point")
    keep_from = min([OWNER_RANGE[0]] + [x for x in (fast_sigma, slow_sigma)
                                        if x is not None])
    with _Clock(f"judging every race against its {cal['k']} nearest"):
        hist, total = scanAll(cur, shards, cal["k"], cal["floor"], keep_from,
                              connect=connect, streams=streams,
                              verbose=verbose)
    cal["n_judged"] = total
    fcut, fwhy, frows = deriveCut(hist["fast"])
    scut, swhy, srows = deriveCut(hist["slow"])
    cal["fast_cut"] = fast_sigma if fast_sigma is not None else fcut
    cal["slow_cut"] = slow_sigma if slow_sigma is not None else scut
    cal.setdefault("detail", {})
    cal["detail"].update({"fast_derived": fcut, "slow_derived": scut,
                          "hist": {s: {str(b): v for b, v in h.items()}
                                   for s, h in hist.items()}})
    rows = flaggedRows(cur, cal["fast_cut"], cal["slow_cut"])
    if verbose:
        printDistribution(hist, total, bars)
        printCut("fast", fcut, fwhy, frows)
        printCut("slow", scut, swhy, srows)
        for side, given in (("fast", fast_sigma), ("slow", slow_sigma)):
            if given is not None:
                print(f"  ! {side} cut overridden: {given:g}")
        printSamples(rows, show)
        nf = sum(1 for r in rows if r[9] == "fast")
        ns = len(rows) - nf
        print(f"\n  flagged: {nf:,} fast (at {cal['fast_cut']:g}), "
              f"{ns:,} slow (at {cal['slow_cut']:g}) of {total:,} judged "
              f"-- K = {cal['k']}, floor = {cal['floor']:.2f}")
        print(f"    [{time.time() - t_all:8.1f}s] total")
    return rows, cal


def _personArg(v):
    """A person id, or the token a page shows for one (public_ids)."""
    try:
        return int(v)
    except ValueError:
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        import public_ids as _pi
        got = _pi.decode(v)
        if not got:
            raise argparse.ArgumentTypeError(f"not a person id or token: {v}")
        return got[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", type=int, default=None,
                    help="judge only races from this year on (their "
                         f"neighbours may be up to {SINCE_MARGIN_YEARS} "
                         "years older). The pipeline judges everything.")
    ap.add_argument("--person", type=_personArg, default=None,
                    help="explain one athlete: every race, its neighbours, "
                         "their median, the spread and z. Uses the last "
                         "--write's K, floor and cuts unless given.")
    ap.add_argument("--sigma", default="5,6,8,10,15",
                    help="report the count past each of these bars")
    ap.add_argument("--fast-sigma", type=float, default=None,
                    help="the fast cut; default: derived from the corpus")
    ap.add_argument("--slow-sigma", type=float, default=None,
                    help="the slow cut; default: derived from the corpus")
    ap.add_argument("--k", type=int, default=None,
                    help="neighbours per race; default: measured")
    ap.add_argument("--floor", type=float, default=None,
                    help="spread floor in rating points; default: measured")
    ap.add_argument("--shards", type=int, default=None,
                    help=f"hash partitions of the stage; default: one per "
                         f"{SHARD_ROWS:,} rows")
    ap.add_argument("--streams", type=int,
                    default=int(os.environ.get("XCP_STREAMS", "4")),
                    help="shards judged side by side, one connection each "
                         "(default XCP_STREAMS, else 4)")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()
    if not (args.write or args.dry_run or args.person):
        ap.error("pass --dry-run, --write or --person")
    bars = tuple(float(x) for x in args.sigma.split(",") if x.strip())

    from database import getConn
    from pg_guard import guard
    with getConn() as conn:
        with conn.cursor() as cur:
            # ! A READ-ONLY DIAGNOSTIC MUST NOT BE ABLE TO FILL THE DISK.
            #   diag_indoor_level did exactly that on 2026-09-18, under a
            #   running scrape. Bounds this connection only.
            guard(cur)
            if args.person:
                stored = latestCalib(cur) or {}
                k = args.k or stored.get("k")
                floor = args.floor if args.floor is not None else stored.get("floor")
                if k is None or floor is None:
                    print("  no stored calibration (run --write once): "
                          "measuring on a 1-in-64 sample of athletes")
                    buildStage(cur, sample_mod=64, shards=1,
                               name=STAGE + "_cal")
                    got = calibrate(cur, f"{STAGE}_cal_0", k_fixed=k)
                    dropStage(cur, STAGE + "_cal")
                    k = got["k"]
                    floor = floor if floor is not None else got["floor"]
                fc = args.fast_sigma or stored.get("fast_cut") or OWNER_RANGE[0]
                sc = args.slow_sigma or stored.get("slow_cut") or OWNER_RANGE[0]
                explain(cur, args.person, k, floor, fc, sc)
                conn.rollback()
                return
            # ! THE SCRATCH GOES WHATEVER HAPPENS. With streams the stage is
            #   committed (the workers must see it), so a rollback alone
            #   would leave several GB of unlogged tables behind.
            try:
                rows, cal = run(cur, since=args.since, k=args.k,
                                floor=args.floor, fast_sigma=args.fast_sigma,
                                slow_sigma=args.slow_sigma, bars=bars,
                                show=args.show, shards=args.shards,
                                connect=getConn, streams=args.streams)
                extra = extraRows(cur)
                extra += copyRows(cur)
                extra += careerRows(cur)
                extra += closeRows(cur)
                extra += raceRows(cur)
                extra += dayRows(cur)
                extra += nameMixRows(cur)
                extra += twoRaceRows(cur)
                extra += siblingRows(cur)
                printExtra(extra, args.show)
                if args.write:
                    if args.since:
                        print("  ! --since judged only part of the corpus; "
                              "the table is replaced with that part")
                    print(f"  wrote {write(cur, rows, cal):,} rows to "
                          f"rating_outlier")
                    print(f"  wrote {writeExtra(cur, extra):,} more (thin, field, level, mixed, "
                          f"same-time copies, career, close, race, day)")
                    dropStage(cur)
                    conn.commit()
                    print("  committed. The boards (step 10) and the season "
                          "numbers built from them leave every flagged row "
                          "out; the athlete page keeps the row and marks it.")
                else:
                    conn.rollback()
                    print("  DRY RUN -- nothing written.")
            finally:
                conn.rollback()
                dropStage(cur)
                conn.commit()


if __name__ == "__main__":
    main()
