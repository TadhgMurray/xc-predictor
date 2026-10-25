# Project: xc-predictor / racecast
# File:    build_recruit_projection.py
# Purpose: Who the MODEL thinks improves most (owner, 2026-09-16: "can we get
#          the underrated runners in the coaches recruits section -- underrated
#          as in they could progress the most in college type"). Pipeline step
#          after 10f (college_recruit); needs a trained model.
#
#     python racecast/build_recruit_projection.py
#     python racecast/build_recruit_projection.py --dry-run --limit 500
#     python racecast/build_recruit_projection.py --sport XC --weeks 52
#
# ★ WHY THE MODEL AND NOT A GAIN COLUMN. recruiting.py's gain_resid already
#   answers "improved more than people at their level normally do", and it is
#   a good column, but it is entirely BACKWARD-LOOKING: it reads two seasons
#   that happened. The question here is forward -- who has room left -- and
#   the network was built for it. feature_extraction emits a HORIZON twin at
#   HORIZON_GAP_MIN_WEEKS and up (44 weeks, just under a year) for exactly
#   this, so a projection a year out is a question the model was trained on
#   rather than an extrapolation off the end of its range.
#
# ★ THE RATIO IS THE ANSWER, NOT THE TIME. A speed rating is 100 * pool_mean
#   / adjusted_time -- a RECIPROCAL of time -- so a predicted time and the
#   model's own baseline for the same athlete give the rating multiplier
#   directly:
#
#       proj_rating = mean_rating * (baseline_norm / projected_norm)
#
#   Both numbers come out of the same prediction, on the same normalized
#   clock, so the pool mean cancels and nothing has to be looked up. Using
#   the model's OWN baseline rather than the athlete's published rating is
#   deliberate: the baseline is the anchor the network measured the
#   improvement against, so the ratio is self-consistent even where a
#   mean-of-ratings and a rating-of-mean-time would differ.
#
# ⚠ ONE YEAR, AND THAT IS A CEILING, NOT A CHOICE. HORIZON_GAP_MAX_WEEKS says
#   208, but the twin generator truncates to the last race at least
#   HORIZON_GAP_MIN_WEEKS before the target -- and an athlete who races
#   continuously always has one at about a year, so the gap almost never gets
#   further out. The calibration run confirmed it: sampling the WHOLE
#   validation split, the horizons top out at 52.1 weeks and the 104w+ band is
#   empty. So a year is as far as this has any measured right to speak, which
#   happens to be the useful distance anyway -- a senior projected a year out
#   is a freshman college season.
#
# ⚠ AND THE CALIBRATION IS WHY THIS IS PUBLISHABLE AT ALL. At 44-104w the
#   model's band covers 0.664 against a claimed 0.683, and sd(predicted) /
#   sd(actual) is 0.98 -- it is not shrinking everyone toward the pool mean,
#   which is the failure that would make this list noise however good the
#   coverage looked. Re-run scripts/diag_calibration.py after any retrain; if
#   spread falls, this table should stop being published.
#
# ! WHAT IS RANKED IS THE RESIDUAL, NOT THE GAIN. Everybody at 80 is
#   projected to gain more than everybody at 115, so ranking the raw
#   projection just re-sorts the bottom of the range -- the same trap
#   gain_resid exists to avoid. proj_resid is the projected gain less the
#   average projected gain for that starting rating, over its spread.
#
# Swap discipline as build_recruiting.py: a shadow table, then
# dbfast.swapTable under a bounded lock, so the live table is never empty.

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                  # noqa: E402

from database import getConn                            # noqa: E402
from dbfast import swapTable                            # noqa: E402
from season_floor import floorSql, DEFAULT_FLOOR        # noqa: E402

# the horizon, in weeks. See the ceiling note above before raising it.
PROJECT_WEEKS = 52

# the normalized distance every projection is asked at, so two athletes'
# projections are comparable. XC's own anchor; TF's 5000 for the same reason.
PROJECT_DISTANCE = 5000.0

# a starting-rating band for the expected-projection curve, and the
# population a band needs before its average is one. Same numbers
# recruiting.py bands the historical gain with, so the two columns are
# talking about the same slices.
PROJ_BAND = 5
MIN_BAND_N = 20

# athletes per model call. The network is batched internally; this bounds the
# history query and the memory a batch holds.
CHUNK = 400

# a projection wider than this is not a projection
MAX_SIGMA_PCT = 25.0

# ★ THE CHUNKS ON SEVERAL PROCESSES (owner, 2026-10-25: "you can
#   multithread/core as necessary. You have freedom as long as it remains
#   accurate and working"). 10f2 had grown from 50 minutes to 8,668 s and was
#   the tail of the whole night, every second of it the same loop: CHUNK
#   athletes, one _predictTimes -- their history read, a feature vector each
#   in Python, the network -- then the next CHUNK, one after another in one
#   process. The chunks do not depend on each other, so they are handed out
#   to XCP_PROJ_WORKERS processes and their results put back in chunk order.
#
# ! THE SAME CHUNKS, SO THE SAME NUMBERS. A worker is given exactly the CHUNK
#   rows the loop would have taken (rows[i:i + CHUNK]), and _predictTimes
#   sorts and batches the network's input WITHIN its call -- so every runner
#   is in the same encoder batch, padded to the same length, as before.
#
# ! SPAWNED, NOT FORKED, AND AT THE SAME TORCH THREAD COUNT. Measured
#   2026-10-25 on a random-weight XCPredictor (torch 2.x CPU): a forked child
#   using 3 threads after its parent had run the network HUNG (libgomp is not
#   fork-safe), and 2 threads gave different bits from 3. A spawned child
#   loads the model itself through _loadModel, which sets the thread count by
#   the same rule as this process (XCP_TORCH_THREADS, else cores // 8), and
#   its outputs were bit-for-bit the parent's.
#
# ⚠ EACH WORKER HOLDS THREE CONNECTIONS (its own cursor, and one history
#   reader per sport -- _historyStart's readers cut to one each in a worker,
#   which reads the same batches of the same ids, only not two at once), and
#   about the memory of the old single process: torch, the model and
#   feature_extraction with corrections. Six is 18 connections against a
#   max_connections of 100 that the site's workers (up to 48) and 13j's
#   record-book workers (beside this step, 8 + 1) also draw on, and 6 x 3
#   torch threads leaves the box's other cores to the steps in line.
#   1 runs the loop in this process, exactly as before.
PROJ_WORKERS_MAX = 6
PROJ_WORKER_CONNS = 3

# ★ EVERY SEASON SINCE THIS ONE (owner, 2026-09-22: "can you do beyond just
#   2026. Like do since 2020"). A past season is projected AS OF ITS END --
#   see seasonAsOf -- so the recruiting search can ask of the class of 2021
#   what the model would have said about them then.
SINCE_YEAR = 2020

_DDL = """
DROP TABLE IF EXISTS recruit_projection_new;
CREATE TABLE recruit_projection_new (
    person_id      bigint  NOT NULL,
    sport          text    NOT NULL,
    pool           text    NOT NULL,
    year           integer NOT NULL,
    base_rating    real    NOT NULL,
    proj_rating    real    NOT NULL,
    proj_gain      real    NOT NULL,
    proj_gain_pct  real,
    proj_sigma_pct real,
    proj_resid     real,
    band           real,
    band_n         integer,
    horizon_weeks  integer NOT NULL,
    n_races        integer NOT NULL,
    as_of          date,
    model_id       text,
    CONSTRAINT recruit_projection_new_pkey PRIMARY KEY (person_id, sport, year)
);
"""

_INDEX = """
CREATE INDEX recruit_projection_new_rank_idx
    ON recruit_projection_new (pool, sport, year, proj_resid DESC);
"""


def candidates(cur, pool, sport, year, min_races=None, limit=None):
    """The athletes to project: this season's rated seasons in `pool`, past
    the boards' own race floor.

    ! THE SAME FLOOR THE SEARCH USES. A projection for somebody the search
      will not show is work nobody reads, and a projection built off one race
      is a projection off one race.

    ! floorSql TAKES "DID THE READER SET THIS", NOT THE NUMBER, and the
      number is bound separately -- both branches of it read %(min_races)s,
      so leaving that unbound is a failed query either way. `min_races=None`
      means the board's own floor, which open seasons are exempt from; a
      value means it applies to every row.
    """
    explicit = min_races is not None
    cur.execute(f"""
        SELECT s.person_id, s.mean_rating, s.n_races
        FROM   athlete_season s
        WHERE  s.pool = %(pool)s AND s.sport = %(sport)s AND s.year = %(year)s
          AND  s.mean_rating IS NOT NULL
          AND  {floorSql(explicit)}
        ORDER  BY s.mean_rating DESC NULLS LAST, s.person_id
        {"LIMIT %(limit)s" if limit else ""}
    """, {"pool": pool, "sport": sport, "year": year, "limit": limit,
          "min_races": int(min_races) if explicit else DEFAULT_FLOOR})
    return [dict(r) for r in cur.fetchall()]


def targetFor(sport, weeks=PROJECT_WEEKS, today=None, history_before=None):
    """predict.py's `manual` target, `weeks` out from today.

    ! NO COURSE AND NO WEATHER. The projection is about the athlete, so
      everything about the race is held at the neutral value the model was
      trained to see when a target's conditions were unknown.

    `history_before` (a date) cuts the athlete's history there instead of at
    the target -- a past season's projection sees only what was known when
    that season ended. See predict._predictTimes.
    """
    day = (today or dt.date.today()) + dt.timedelta(weeks=weeks)
    t = {"mode": "manual", "sport": sport, "date": day.isoformat(),
         "distance": PROJECT_DISTANCE}
    if history_before is not None:
        t["history_before"] = history_before.isoformat()
    return t


def seasonAsOf(year, current, today=None):
    """The date a season's projection is made on.

    ★ THE CURRENT SEASON: TODAY, exactly as before -- its history runs up to
      now and the target is a year out.
    ★ A PAST SEASON: THE DAY IT ENDED. Both sports store a season under the
      academic year it opens in (season_year.ACADEMIC_START_MONTH, August), so
      season Y is over by 1 August of Y+1: the whole senior XC season and the
      track season after it are known, and nothing past it is. The target is
      a year after that, which is the freshman college season -- the same
      question the current season is asked.
    """
    from season_year import ACADEMIC_START_MONTH
    today = today or dt.date.today()
    if current is None or year >= current:
        return today
    return min(today, dt.date(year + 1, ACADEMIC_START_MONTH, 1))


def seasonsFor(current, since=SINCE_YEAR, only=None):
    """The seasons to project: `only`, or since..current inclusive."""
    if only:
        return [only]
    if not current:
        return []
    return list(range(min(since, current), current + 1))


def modelId():
    """Which checkpoint made a row: its file's size and mtime.

    ! A PAST SEASON IS CARRIED FORWARD ONLY UNDER THE SAME MODEL. Its history
      is frozen at its as_of, so re-running it on the same checkpoint
      reproduces the same projection at the cost of one more pass of the
      network per athlete -- six seasons of it is most of a night. A retrain
      changes the id and every season is recomputed.
    """
    import os
    from predict import MODEL_PATH
    try:
        st = os.stat(MODEL_PATH)
    except OSError:
        return None
    return f"{st.st_size}:{int(st.st_mtime)}"


def projectRows(rows, preds):
    """[(row, proj_rating, proj_gain, pct, sigma_pct)] for the rows that
    produced a usable prediction.

    ★ THE MULTIPLIER IS baseline / projected, BOTH NORMALIZED. See the header:
      a rating is a reciprocal of time, so that ratio IS the rating ratio,
      and the pool mean cancels out of it.
    """
    out = []
    for row, pred in zip(rows, preds):
        base = pred.get("baseline")
        proj = pred.get("normalized")
        rating = row.get("mean_rating")
        sigma = pred.get("sigma_pct")
        if not base or not proj or base <= 0 or proj <= 0 or not rating:
            continue
        if sigma is not None and float(sigma) > MAX_SIGMA_PCT:
            continue
        mult = float(base) / float(proj)
        proj_rating = float(rating) * mult
        out.append((row, proj_rating, proj_rating - float(rating),
                    mult - 1.0,
                    float(sigma) if sigma is not None else None))
    return out


def addResiduals(projected, band=PROJ_BAND, min_band_n=MIN_BAND_N):
    """Put proj_resid on each tuple: the projected gain less what is normal
    at that starting rating, over that band's spread.

    ! THE BAND IS THE STARTING RATING, which is the whole point. An 80 and a
      115 are not drawn from one distribution of improvements, so one average
      cannot judge both -- and ranking the raw projection would just
      rediscover the bottom of the range.
    """
    import statistics
    groups = {}
    for item in projected:
        row, _pr, gain, _pct, _sig = item
        b = round(float(row["mean_rating"]) / band) * band
        groups.setdefault(b, []).append(gain)
    stats = {}
    for b, gains in groups.items():
        if len(gains) < min_band_n:
            continue
        sd = statistics.stdev(gains) if len(gains) > 1 else 0.0
        stats[b] = (statistics.fmean(gains), sd, len(gains))
    out = []
    for item in projected:
        row, pr, gain, pct, sig = item
        b = round(float(row["mean_rating"]) / band) * band
        mu_sd_n = stats.get(b)
        resid = None
        if mu_sd_n and mu_sd_n[1] > 0:
            resid = (gain - mu_sd_n[0]) / mu_sd_n[1]
        out.append((row, pr, gain, pct, sig, resid, b,
                    mu_sd_n[2] if mu_sd_n else None))
    return out


def projectChunk(cur, part, target, predict):
    """projectRows for one chunk of candidates: `predict` is
    predict._predictTimes (or a stand-in with its signature). The one unit of
    work, in this process or in a worker."""
    preds = predict(cur, [r["person_id"] for r in part], target)
    return projectRows(part, preds)


def projWorkers():
    """How many processes project at once: XCP_PROJ_WORKERS, else the
    smaller of PROJ_WORKERS_MAX and the cores less four (the site and
    Postgres keep those). 1 is the old in-process loop."""
    raw = (os.environ.get("XCP_PROJ_WORKERS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, min(PROJ_WORKERS_MAX, (os.cpu_count() or 1) - 4))


def warmLevels():
    """Load normalize_distance's school_level_graph lookup now, on one thread.

    ⚠ IT WAS FIRST LOADED INSIDE THE HISTORY READERS (found 2026-10-25).
      toCommonScale -> storedPool -> poolFor -> levelForSchool reaches
      loadGraphLevels, which sets its dict to {} and THEN fills it -- and the
      first _predictTimes runs four reader threads at once, so a reader that
      finished its query while another was filling read a half-empty lookup
      and fell back to the pickle for those schools. That was one chunk a run
      in the single process; with a worker per process it would be one chunk
      per worker. Loaded here first, every chunk sees the whole table.
    ! A FAILURE IS SURVIVED as loadGraphLevels survives it (the pickle alone),
      exactly what the readers would have done."""
    try:
        from normalize_distance import loadGraphLevels
        loadGraphLevels()
    except Exception as exc:                            # noqa: BLE001
        print(f"  school_level_graph not preloaded ({exc})")


def _workerInit(hook=None):
    """Set up one spawned worker. `hook` ("module:function") runs first --
    the tests' way to put a stand-in database and model in the child; the
    pipeline passes none.

    ! THREE POOLED CONNECTIONS KEPT, NOT FIVE. database's pool opens MIN_CONN
      at once and CLOSES any returned past MIN_CONN, so it is set to what a
      chunk holds at its peak: no idle extras, and no reconnect per chunk.
    ! ONE HISTORY READER PER SPORT. _queueStart's readers take the same
      batches off the same queue in the same order whatever their number, so
      every id's rows come from the same query; only the overlap goes, and
      the workers are the overlap now.
    """
    import atexit
    import importlib
    if hook:
        mod, fn = hook.split(":")
        getattr(importlib.import_module(mod), fn)()
    import database
    import predict
    database.MIN_CONN = PROJ_WORKER_CONNS
    for key in list(predict.READ_WORKERS):
        if key[0] == "hist":
            predict.READ_WORKERS[key] = 1
    atexit.register(database.closePool)
    warmLevels()


def _workerChunk(part, target):
    """projectChunk in a worker, on a connection of its own. getConn rolls
    back on the way out, so no read stays open between chunks."""
    from predict import _predictTimes
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return projectChunk(cur, part, target, _predictTimes)


def startWorkers(n, hook=None):
    """A spawn-context process pool of `n` workers, or None for n <= 1 (the
    in-process loop). See PROJ_WORKERS_MAX for why spawn."""
    if n <= 1:
        return None
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    # a spawned child is not given -u; its lines go to the step's log as
    # they happen, like this process's
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    # ! ProcessPoolExecutor, NOT multiprocessing.Pool: a worker the kernel
    #   kills (out of memory) breaks the pool and the step FAILS, where
    #   Pool would wait for that chunk's result forever.
    return ProcessPoolExecutor(max_workers=n,
                               mp_context=multiprocessing.get_context("spawn"),
                               initializer=_workerInit, initargs=(hook,))


def buildOne(cur, pool, sport, year, min_races, weeks, limit, chunk=CHUNK,
             verbose=True, as_of=None, past=False, workers=None):
    """Every projection for one (pool, sport, year), residuals included.

    `as_of` is the day the projection is made (seasonAsOf); for a `past`
    season the history is cut there too. `workers` is a startWorkers pool;
    None projects in this process, on `cur`.
    """
    from predict import _predictTimes
    rows = candidates(cur, pool, sport, year, min_races, limit)
    if verbose:
        print(f"  {pool}/{sport} {year}: {len(rows):,} candidates"
              + (f" (as of {as_of})" if past else ""))
    target = targetFor(sport, weeks, today=as_of,
                       history_before=as_of if past else None)
    parts = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]
    if workers is None:
        done = (projectChunk(cur, part, target, _predictTimes)
                for part in parts)
    else:
        # ! THIS PROCESS ONLY WAITS NOW, so its read ends here rather than
        #   when the slice is written: an open transaction is what CREATE
        #   INDEX CONCURRENTLY waits on (see main's COMMIT AS IT GOES).
        cur.connection.commit()
        # map hands results back in the order of `parts`, whichever worker
        # finished first
        done = workers.map(_workerChunk, parts, [target] * len(parts))
    projected = []
    for k, (part, got) in enumerate(zip(parts, done)):
        projected.extend(got)
        if verbose and k % 10 == 9:
            print(f"    {k * chunk + len(part):,}/{len(rows):,} "
                  f"({len(projected):,} usable)", flush=True)
    if verbose:
        print(f"    {len(projected):,} of {len(rows):,} projected")
    return addResiduals(projected)


def _liveHasModelId(cur):
    """Whether the live table is one this version wrote (it has model_id).

    ! A TABLE FROM BEFORE 2026-09-22 HOLDS ONE SEASON AND NO model_id, so
      nothing in it can be proved to come from this checkpoint: every season
      is computed on the first run after the change.
    """
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'recruit_projection'
                     AND column_name = 'model_id'""")
    return cur.fetchone() is not None


def _carryForward(cur, pool, sport, year, mid):
    """Copy one past (pool, sport, year) from the live table; rows copied."""
    cur.execute("""
        INSERT INTO recruit_projection_new
        SELECT person_id, sport, pool, year, base_rating, proj_rating,
               proj_gain, proj_gain_pct, proj_sigma_pct, proj_resid, band,
               band_n, horizon_weeks, n_races, as_of, model_id
        FROM   recruit_projection
        WHERE  pool = %s AND sport = %s AND year = %s AND model_id = %s
        ON CONFLICT DO NOTHING
    """, (pool, sport, year, mid))
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sport", default=None, help="XC or TF (default: both)")
    ap.add_argument("--year", type=int, default=None,
                    help="one season only (stored year)")
    ap.add_argument("--since", type=int, default=SINCE_YEAR,
                    help=f"first season to project (default {SINCE_YEAR})")
    ap.add_argument("--refresh-past", action="store_true",
                    help="recompute past seasons even when the live table "
                         "already holds them under this model")
    ap.add_argument("--weeks", type=int, default=PROJECT_WEEKS)
    ap.add_argument("--min-races", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="candidates per pool, for a quick look")
    a = ap.parse_args()

    from predict import modelStatus
    status = modelStatus()
    if not status["available"]:
        # ! NOT A FAILURE. An untrained model is an expected state of this
        #   repo, and the search falls back to the rating sort when the table
        #   is absent, so this exits clean rather than landing in the
        #   pipeline's FAILED list every run.
        print(f"recruit_projection: no model, skipped -- {status['reason']}")
        return 0

    sports = [a.sport.upper()] if a.sport else ["XC", "TF"]
    mid = modelId()
    today = dt.date.today()
    n_workers = projWorkers()
    if n_workers <= 1:
        warmLevels()                # see warmLevels; the workers do their own
    pool_box = []                   # the worker pool, started on first need
    try:
        return _run(a, sports, mid, today, n_workers, pool_box)
    finally:
        for ex in pool_box:
            if ex is not None:
                ex.shutdown(wait=True, cancel_futures=True)


def _run(a, sports, mid, today, n_workers, pool_box):
    """main's body, with the worker pool's lifetime held by main."""
    from predict import _currentSeason

    def workers():
        # ! STARTED ONCE AND ONLY WHEN A SLICE IS COMPUTED: a run that
        #   carries every past season forward and has one current slice
        #   pays one start, and a carry-only run pays none.
        if not pool_box:
            pool_box.append(startWorkers(n_workers))
            if pool_box[0] is not None:
                print(f"  projecting on {n_workers} worker processes "
                      f"(XCP_PROJ_WORKERS)", flush=True)
        return pool_box[0]

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not a.dry_run:
                cur.execute(_DDL)
                # ★ COMMIT AS IT GOES (2026-09-26). This ran as ONE transaction
                #   for its whole length -- 678 minutes on 2026-09-25 -- and
                #   every CREATE INDEX CONCURRENTLY in the database waits for
                #   older transactions to end: 11b_indexes sat 626 minutes
                #   behind it. The shadow table is nobody's until the swap, so
                #   committing each slice into it changes nothing a reader sees.
                conn.commit()
            carry = (not a.dry_run and not a.refresh_past and mid is not None
                     and _liveHasModelId(cur))
            total = 0
            built = set()                   # (sport, year) this run decided
            for sport in sports:
                current = _currentSeason(cur, sport)
                years = seasonsFor(current, a.since, a.year)
                if not years:
                    print(f"  {sport}: no current season, skipped")
                    continue
                for year in years:
                    built.add((sport, year))
                    past = current is not None and year < current
                    as_of = seasonAsOf(year, current, today)
                    for pool in ("hs_m", "hs_f"):
                        # ★ A PAST SEASON UNDER THE SAME MODEL IS COPIED, NOT
                        #   RECOMPUTED -- see modelId.
                        if carry and past:
                            n = _carryForward(cur, pool, sport, year, mid)
                            if n:
                                print(f"  {pool}/{sport} {year}: {n:,} carried "
                                      f"forward (same model)")
                                total += n
                                continue
                        got = buildOne(cur, pool, sport, year, a.min_races,
                                       a.weeks, a.limit, as_of=as_of,
                                       past=past, workers=workers())
                        conn.commit()           # end the read before the write
                        if a.dry_run:
                            for item in sorted(
                                    (g for g in got if g[5] is not None),
                                    key=lambda g: -g[5])[:10]:
                                row, pr, gain, _pct, sig, resid, band, n = item
                                print(f"      {row['person_id']:>9}  "
                                      f"{row['mean_rating']:6.1f} -> {pr:6.1f} "
                                      f"({gain:+5.1f})  resid {resid:+5.2f}  "
                                      f"band {band:g} n={n}")
                            total += len(got)
                            continue
                        psycopg2.extras.execute_values(cur, """
                            INSERT INTO recruit_projection_new
                              (person_id, sport, pool, year, base_rating,
                               proj_rating, proj_gain, proj_gain_pct,
                               proj_sigma_pct, proj_resid, band, band_n,
                               horizon_weeks, n_races, as_of, model_id)
                            VALUES %s
                            ON CONFLICT DO NOTHING
                        """, [(row["person_id"], sport, pool, year,
                               row["mean_rating"], pr, gain, pct, sig, resid,
                               band, n, a.weeks, row["n_races"], as_of, mid)
                              for row, pr, gain, pct, sig, resid, band, n
                              in got])
                        conn.commit()
                        total += len(got)
            # ! A NARROW RUN MUST NOT DELETE THE REST. The table swaps whole,
            #   so `--year 2021` or `--sport XC` would otherwise publish that
            #   one slice and drop every other season. What this run did not
            #   decide is kept exactly as the live table has it.
            if not a.dry_run and _liveHasModelId(cur):
                cur.execute("""SELECT DISTINCT sport, year
                               FROM recruit_projection""")
                keep = [(r["sport"], r["year"]) for r in cur.fetchall()
                        if (r["sport"], r["year"]) not in built]
                for sport, year in keep:
                    cur.execute("""
                        INSERT INTO recruit_projection_new
                        SELECT person_id, sport, pool, year, base_rating,
                               proj_rating, proj_gain, proj_gain_pct,
                               proj_sigma_pct, proj_resid, band, band_n,
                               horizon_weeks, n_races, as_of, model_id
                        FROM   recruit_projection
                        WHERE  sport = %s AND year = %s
                        ON CONFLICT DO NOTHING""", (sport, year))
                    print(f"  {sport} {year}: {cur.rowcount:,} kept as they "
                          f"were (not asked for this run)")
                    total += cur.rowcount
            if a.dry_run:
                print(f"recruit_projection: {total:,} rows would be written "
                      f"(nothing changed).")
                conn.rollback()
                return 0
            if not total:
                # ! AN EMPTY BUILD IS NOT A SWAP. Publishing nothing would
                #   take the column off every page; leaving the old table is
                #   the honest failure.
                print("recruit_projection: NOTHING TO WRITE -- "
                      "left as it was.")
                conn.rollback()
                return 1
            cur.execute(_INDEX)
        swapTable(conn, "recruit_projection", renames=[
            ("recruit_projection_new_pkey", "recruit_projection_pkey"),
            ("recruit_projection_new_rank_idx", "recruit_projection_rank_idx")])
    print(f"  recruit_projection: {total:,} rows written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
