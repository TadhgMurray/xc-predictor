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
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", type=int, default=None,
                    help="judge only races from this year on (their "
                         f"neighbours may be up to {SINCE_MARGIN_YEARS} "
                         "years older). The pipeline judges everything.")
    ap.add_argument("--person", type=int, default=None,
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
                if args.write:
                    if args.since:
                        print("  ! --since judged only part of the corpus; "
                              "the table is replaced with that part")
                    print(f"  wrote {write(cur, rows, cal):,} rows to "
                          f"rating_outlier")
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
