#!/usr/bin/env python3
"""
measure_rank_churn.py -- how much of the boards tables a run really changes,
and what an incremental 10_rankings would get wrong. READ-ONLY.

    ( set -a; . /etc/xc-predictor.env; set +a; \\
      /srv/venv/bin/python -u scripts/measure_rank_churn.py )

    # the night-to-night measurement: save tonight, compare tomorrow
    ... scripts/measure_rank_churn.py --save /srv/xc-predictor/logs/churn_$(date +%F).tsv.gz
    ... scripts/measure_rank_churn.py --against /srv/xc-predictor/logs/churn_<last>.tsv.gz

★ WHY (owner, 2026-11-20: "what is the margin of error if the rankings only
  rebuild what changed?"). 10_rankings rebuilds ranking_results (~158M rows)
  and athlete_season (~26M) from scratch every run, ~40 min. An incremental
  build would re-derive only what changed; this script measures how much
  that is, and simulates the scheme in docs/MOE-INCREMENTAL-RANK-2026-11-20.md
  (rebuild a row when its rating moved >= theta, every row of an athlete
  with a new or corrected row, then the season aggregates and the ranks) to
  print its error against a full rebuild.

★ THE BASELINE ("what the boards were before"), in the order it is looked for:
    --against FILE   a snapshot this script wrote with --save: the sampled
                     rows of both tables, every column (as two hashes), the
                     top of every default board. The complete measurement.
    the go-live's backups  results_speed_rating_backup / results_tf_...
                     (engine/pair_golive.backupSpeedRatings): result_id ->
                     the rating before a go-live. ⚠ KEPT WHEN PRESENT, NOT
                     REFRESHED, so it can be older than last night: the
                     script prints the newest result it holds and how many
                     results came after it.
    run_prev_athlete  scripts/run_changes.py --snapshot, step 00b of a full
                     run: this season's athlete_season.mean_rating as the
                     run started.
    ranking_results_old / athlete_season_old  only if a swap left them.

! READ-ONLY, AND ONLY AccessShare. Plain SELECTs on tables and catalogue
  views, in short READ ONLY transactions committed after each section, so
  no lock is held between sections (a 10_rankings swap waiting for its
  ACCESS EXCLUSIVE never queues behind more than one statement).
  statement_timeout 10 min, lock_timeout 5 s. Nothing is created, not even a
  temp table; --save writes only the local file named.
! SAMPLED BY PERSON: person_id % --sample == 0 (default 100, 1%), the same
  people every night, all of their rows, so a season can be recomputed.
  Counts are printed as sampled and as x--sample estimates.
! NO NAMES, ONLY IDS AND COUNTS. Nothing secret is printed; the connection
  banner is config.py's (user@host/db, no password).
"""
import argparse
import datetime
import gzip
import json
import math
import os
import re
import sys
import time
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_APP", "measure_rank_churn")

POOLS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")
SPORTS = ("XC", "TF")
BACKUP = {"XC": ("results_speed_rating_backup", "results"),
          "TF": ("results_tf_speed_rating_backup", "results_tf")}
# a stored rating is np.round(x, 2) (joint_golive.buildLive); half a step is
# the smallest move that is a move and not float4 noise
MOVED = 0.005
BUCKETS = (0.005, 0.05, 0.1, 0.5, 2.0)
THETAS = (0.05, 0.1, 0.25, 0.5)
TOP_K = (25, 100)
CAND_FACTOR = 4
UNIT_COLS = ("division", "region", "conference", "league", "state_div",
             "section_div", "district", "county", "class", "area", "section")
ID_COLS = ("school", "state", "grade") + UNIT_COLS
RACE_COLS = ("race_date", "year", "meet_id", "div_id", "canon_meet_id",
             "time_seconds", "distance", "event_id", "event_kind", "mark")
AS_ID_COLS = ("state", "school", "grade") + UNIT_COLS


# ---- the season rule, read from the builder so the two cannot drift ------ #

def _builderConstants():
    """_DECAY_K, _SEASON_Q, _SEASON_OUTLIER_PTS from build_ranking_results.py's
    text (importing it pulls the whole engine in). Literals if unreadable."""
    want = {"_DECAY_K": 0.996, "_SEASON_Q": 0.80, "_SEASON_OUTLIER_PTS": 20.0}
    try:
        with open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")) as fh:
            text = fh.read()
        for k in want:
            m = re.search(rf"^{k}\s*=\s*([0-9.]+)", text, re.M)
            if m:
                want[k] = float(m.group(1))
    except OSError:
        pass
    return want["_DECAY_K"], want["_SEASON_Q"], want["_SEASON_OUTLIER_PTS"]


DECAY_K, SEASON_Q, OUTLIER_PTS = _builderConstants()
_ANCHOR = datetime.date(2100, 1, 1)

try:
    from pool_ceiling import ceilingFor
except Exception:                                              # noqa: BLE001
    ceilingFor = None
try:
    from season_floor import DEFAULT_FLOOR, OPEN_FROM
except Exception:                                              # noqa: BLE001
    DEFAULT_FLOOR, OPEN_FROM = 3, 2026
RACE_MARGIN = 10.0          # build_ranking_results.RACE_MARGIN


def q8(vals, q):
    """season_floor.seasonQuantileSql in Python: Hyndman-Fan type 8 over
    sorted vals."""
    n = len(vals)
    if n == 0:
        return None
    h = (n + 1.0 / 3) * q + 1.0 / 3
    if h >= n:
        return vals[-1]
    if h <= 1:
        return vals[0]
    f = int(math.floor(h))
    return vals[f - 1] + (h - f) * (vals[f] - vals[f - 1])


def pcont(vals, q):
    """percentile_cont over sorted vals."""
    n = len(vals)
    if n == 0:
        return None
    x = (n - 1) * q
    lo = int(math.floor(x))
    hi = min(lo + 1, n - 1)
    return vals[lo] + (x - lo) * (vals[hi] - vals[lo])


def seasonAgg(rows):
    """rows: [(rating, date)] of one (person, pool, sport, year), rated only.
    -> (mean_rating, decayed_rating, best_rating, n_races) as the builder's
    _ATHLETE_SEASON_SQL computes them (float8 arithmetic, no ::real)."""
    if not rows:
        return None
    med = pcont(sorted(r for r, _d in rows), 0.5)
    kept = [(r, d) for r, d in rows if r >= med - OUTLIER_PTS]
    vals = sorted(r for r, _d in kept)
    num = den = 0.0
    for r, d in kept:
        w = DECAY_K ** ((_ANCHOR - d).days)
        num += r * w
        den += w
    return (q8(vals, SEASON_Q), (num / den) if den else None, vals[-1], len(vals))


def floorOk(year, n_races, mean):
    """season_floor.floorSql(False) with min_races = DEFAULT_FLOOR."""
    return mean is not None and (n_races >= DEFAULT_FLOOR or (year or 0) >= OPEN_FROM)


# ---- small statistics ------------------------------------------------------ #

def quantiles(xs, qs=(0.5, 0.9, 0.99)):
    if not xs:
        return [None] * len(qs)
    s = sorted(xs)
    return [s[min(len(s) - 1, int(q * len(s)))] for q in qs]


def fmt(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def tenth(x):
    """What rv() prints ("%.1f")."""
    return None if x is None else "%.1f" % x


class Tally:
    """Per (sport, pool) churn of one rating column."""

    def __init__(self):
        self.n = self.new = self.same = 0
        self.ge = [0] * len(BUCKETS)
        self.flip = 0
        self.d = []          # signed moves, for the distribution

    def add(self, new, old, present):
        self.n += 1
        if not present or old is None:
            self.new += 1
            return
        d = float(new) - float(old)
        if abs(d) < 1e-6:
            self.same += 1
        for i, b in enumerate(BUCKETS):
            if abs(d) >= b:
                self.ge[i] += 1
        if tenth(new) != tenth(old):
            self.flip += 1
        self.d.append(d)


def printTallies(title, tallies, sample):
    print(f"\n== {title}")
    head = (f"  {'sport':5s} {'pool':10s} {'rows':>9s} {'new':>7s} {'same':>8s}"
            + "".join(f" {'>=' + str(b):>8s}" for b in BUCKETS)
            + f" {'tenth':>8s} {'med d':>7s} {'p50|d|':>7s} {'p90|d|':>7s} {'p99|d|':>7s}")
    print(head)
    tot = Tally()
    for (sport, pool), t in sorted(tallies.items()):
        _row(sport, pool, t)
        tot.n += t.n; tot.new += t.new; tot.same += t.same; tot.flip += t.flip
        tot.ge = [a + b for a, b in zip(tot.ge, t.ge)]
        tot.d.extend(t.d)
    _row("all", "", tot)
    cmp_ = tot.n - tot.new
    if cmp_:
        print(f"  of {cmp_:,} compared rows: "
              + ", ".join(f"{100.0 * g / cmp_:.1f}% moved >= {b}" for b, g in zip(BUCKETS, tot.ge))
              + f"; {100.0 * tot.flip / cmp_:.1f}% print a different tenth")
    print(f"  (sampled 1/{sample}: multiply the counts by {sample} for the whole table)")
    return tot


def _row(sport, pool, t):
    absd = [abs(x) for x in t.d]
    q = quantiles(absd)
    med = quantiles(t.d, (0.5,))[0]
    print(f"  {sport:5s} {pool[:10]:10s} {t.n:>9,} {t.new:>7,} {t.same:>8,}"
          + "".join(f" {g:>8,}" for g in t.ge)
          + f" {t.flip:>8,} {fmt(med, 3):>7s} {fmt(q[0], 3):>7s} {fmt(q[1], 3):>7s} {fmt(q[2], 3):>7s}")


# ---- the database ---------------------------------------------------------- #

def regclass(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return bool(cur.fetchone()[0])


def columns(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""", (table,))
    return {r[0] for r in cur.fetchall()}


def hashExpr(alias, cols, have):
    use = [c for c in cols if c in have]
    if not use:
        return "''"
    parts = ", ".join(f"COALESCE({alias}.\"{c}\"::text, '~')" for c in use)
    return f"left(md5(concat_ws('|', {parts})), 8)"


def section(conn, label, fn):
    """One read-only transaction per section; an error is printed (its class
    and message, never a DSN) and the next section runs."""
    t0 = time.time()
    try:
        out = fn()
        conn.commit()
        print(f"  [{label}: {time.time() - t0:.1f}s]")
        return out
    except Exception as exc:                                   # noqa: BLE001
        conn.rollback()
        print(f"  [{label}: skipped -- {type(exc).__name__}: {str(exc).strip()[:200]}]")
        return None


def tableFacts(cur, names):
    """Catalogue numbers only: no scan."""
    cur.execute("""SELECT c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid),
                          s.n_tup_ins, s.n_tup_upd, s.n_tup_del,
                          GREATEST(s.last_analyze, s.last_autoanalyze),
                          GREATEST(s.last_vacuum, s.last_autovacuum)
                   FROM pg_class c
                   LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                   WHERE c.relname = ANY(%s) AND c.relkind IN ('r', 'p')
                     AND c.relnamespace = 'public'::regnamespace
                   ORDER BY c.relname""", (list(names),))
    return cur.fetchall()


# the tables 10_rankings reads besides the results themselves
INPUT_TABLES = ("results", "results_tf", "meets", "meets_tf", "meets_tfrrs",
                "dist_override", "distance_pin", "grade_fix", "pro_athlete_season",
                "athlete_season_level", "person_gender", "athletes", "result_twin",
                "wheelchair_person", "wheelchair_result", "rating_outlier",
                "impossible_result", "college_first_season", "school_name_alias",
                "school_pin", "school_unit", "school_identity")
_TS_COLS = ("created_at", "updated_at", "first_seen", "added_at", "written_at",
            "flagged_at", "scraped_at")
_SMALL = 20_000_000


def inputCounts(cur, since):
    """Per input table: estimated rows and, for a table under 20M rows with a
    timestamp column, the rows stamped at or after `since` (a scan)."""
    out = {}
    for name, n, size, ins, upd, dele, ana, vac in tableFacts(cur, INPUT_TABLES):
        rec = {"rows": int(n), "bytes": int(size), "ins": ins, "upd": upd, "del": dele}
        if since is not None and 0 <= n < _SMALL:
            have = columns(cur, name)
            ts = [c for c in _TS_COLS if c in have]
            if ts:
                cur.execute(f'SELECT count(*) FROM "{name}" WHERE "{ts[0]}" >= %s', (since,))
                rec["since"] = (ts[0], int(cur.fetchone()[0]))
        out[name] = rec
    return out


# ---- reading the rows ------------------------------------------------------ #

def rrRowsSql(have, sport, where, backup):
    """The sampled rows of one sport, person by person, season by season,
    with the old rating from the backup when there is one."""
    old = "b.speed_rating, (b.result_id IS NOT NULL)" if backup else "NULL::float8, false"
    join = f"LEFT JOIN {backup} b ON b.result_id = rr.result_id" if backup else ""
    return f"""
        SELECT rr.person_id, rr.pool, rr.year, rr.race_date, rr.result_id,
               rr.speed_rating, {hashExpr('rr', ID_COLS, have)},
               {hashExpr('rr', RACE_COLS, have)}, {old}
        FROM ranking_results rr {join}
        WHERE rr.sport = '{sport}' AND {where}
        ORDER BY rr.person_id, rr.pool, rr.year, rr.result_id"""


def streamRows(conn, sql, params, name):
    with conn.cursor(name=name) as cur:
        cur.itersize = 50_000
        cur.execute(sql, params)
        for row in cur:
            yield row


def asDate(v):
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


# ---- the snapshot file ----------------------------------------------------- #

def saveSnapshot(path, meta, rr, seasons, boards):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt") as fh:
        fh.write("#meta\t" + json.dumps(meta) + "\n")
        for key, v in rr.items():
            sport, rid = key
            fh.write("r\t" + "\t".join(str(x) for x in (sport, rid) + v) + "\n")
        for key, v in seasons.items():
            fh.write("s\t" + "\t".join(str(x) for x in key + v) + "\n")
        fh.write("#boards\t" + json.dumps(boards) + "\n")
    os.replace(tmp, path)


def _num(x):
    return None if x in ("None", "") else float(x)


def loadSnapshot(path):
    meta, rr, seasons, boards = {}, {}, {}, {}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("#meta\t"):
                meta = json.loads(line[6:])
            elif line.startswith("#boards\t"):
                boards = json.loads(line[8:])
            elif line.startswith("r\t"):
                _r, sport, rid, pid, pool, rating, h1, h2 = line.split("\t")
                rr[(sport, int(rid))] = (int(pid), pool, _num(rating), h1, h2)
            elif line.startswith("s\t"):
                (_s, pid, pool, sport, year, mean, dec, best, n, last, h) = line.split("\t")
                seasons[(int(pid), pool, sport, int(year))] = (
                    _num(mean), _num(dec), _num(best), int(n), last, h)
    return meta, rr, seasons, boards


# ---- the work -------------------------------------------------------------- #

def collectSport(conn, have, sport, sample, base, backup, sim, tallies, other):
    """Walk one sport's sampled rows. tallies: speed_rating churn per pool;
    other: non-rating churn (snapshot only); sim: per theta, the incremental
    scheme against the full rebuild. Returns the live rows keyed for --save
    and the recomputed full seasons (for the check against athlete_season)."""
    keep = {}
    full_seasons = {}
    sql = rrRowsSql(have, sport, "rr.person_id %% %(n)s = 0", backup)
    person, rows = None, []

    def flush():
        if person is None:
            return
        simulatePerson(sport, person, rows, sim, full_seasons)

    for (pid, pool, year, date, rid, rating, h1, h2, b_old, b_has) in streamRows(
            conn, sql, {"n": sample}, f"churn_{sport.lower()}"):
        if pid != person:
            flush()
            person, rows = pid, []
        keep[(sport, rid)] = (pid, pool, rating, h1, h2)
        if base is not None:
            o = base.get((sport, rid))
            has = o is not None
            old = o[2] if has else None
            changed_other = has and (o[0] != pid or o[1] != pool or o[3] != h1 or o[4] != h2)
            if has:
                t = other.setdefault((sport, pool), [0, 0, 0, 0, 0])
                t[0] += 1
                t[1] += o[0] != pid
                t[2] += o[1] != pool
                t[3] += o[3] != h1
                t[4] += o[4] != h2
        else:
            has, old, changed_other = bool(b_has), b_old, False
        if rating is not None:
            tallies.setdefault((sport, pool), Tally()).add(rating, old, has)
        rows.append((pool, year, asDate(date), rating, old, has, changed_other,
                     float(rating) if rating is not None else None))
    flush()
    return keep, full_seasons


def simulatePerson(sport, person, rows, sim, full_seasons):
    """One sampled person's rows of one sport, under every theta."""
    person_dirty = any((not has and r is not None) or chg
                       for _p, _y, _d, r, _o, has, chg, _f in rows)
    groups = {}
    for pool, year, date, rating, old, has, chg, f in rows:
        if rating is None:
            continue
        groups.setdefault((pool, year), []).append((f, date, old, has))
    for (pool, year), g in groups.items():
        full = seasonAgg([(f, d) for f, d, _o, _h in g])
        full_seasons[(person, pool, sport, year)] = full
        ceil = (ceilingFor(pool) + RACE_MARGIN) if ceilingFor else None
        for theta, s in sim.items():
            hyb, rebuilt, flips = [], 0, 0
            for f, d, old, has in g:
                if person_dirty or not has or old is None or abs(f - float(old)) >= theta:
                    hyb.append((f, d))
                    rebuilt += 1
                else:
                    hyb.append((float(old), d))
                    flips += tenth(float(old)) != tenth(f)
                    if ceil is not None and abs(f - ceil) < theta:
                        s["near_rail"] += 1
            s["rows"] += len(g)
            s["rebuilt"] += rebuilt
            s["stale_flip"] += flips
            s["seasons"] += 1
            s["touched"] += rebuilt > 0
            s["athletes_dirty"] += person_dirty
            h = seasonAgg(hyb)
            if full is None or h is None:
                continue
            err = abs(h[0] - full[0])
            s["mean_err"].append(err)
            s["mean_flip"] += tenth(h[0]) != tenth(full[0])
            s["n_flip"] += h[3] != full[3]
            s["best_err"] = max(s["best_err"], abs(h[2] - full[2]))
            s["board"].append((sport, pool, year, full[0], h[0], full[3], h[3]))


def newSim():
    return {t: {"rows": 0, "rebuilt": 0, "stale_flip": 0, "seasons": 0, "touched": 0,
                "athletes_dirty": 0, "mean_err": [], "mean_flip": 0, "n_flip": 0,
                "best_err": 0.0, "near_rail": 0, "board": []} for t in THETAS}


def rankShift(entries, sample):
    """Sampled seasons on each (sport, pool, year) board: rank by the full
    mean against rank by the incremental one, times the sample rate."""
    by = {}
    for sport, pool, year, fm, hm, fn, hn in entries:
        by.setdefault((sport, pool, year), []).append((fm, hm, fn, hn, year))
    shifts = []
    for _k, es in by.items():
        full = [e for e in es if floorOk(e[4], e[2], e[0])]
        hyb = [e for e in es if floorOk(e[4], e[3], e[1])]
        rf = {id(e): i for i, e in enumerate(sorted(full, key=lambda e: -e[0]))}
        rh = {id(e): i for i, e in enumerate(sorted(hyb, key=lambda e: -e[1]))}
        for e in full:
            if id(e) in rh:
                shifts.append(abs(rf[id(e)] - rh[id(e)]) * sample)
    return shifts


def printSim(sim, sample):
    print("\n== THE INCREMENTAL SCHEME AGAINST A FULL REBUILD (sampled people)")
    print("   rebuild a row when its rating moved >= theta, every row of a person"
          " with a new or changed row;\n   stale rows keep last night's rating;"
          " every season and rank recomputed from the rows.")
    print(f"  {'theta':>6s} {'rows rebuilt':>13s} {'seasons touched':>16s} {'stale tenth':>12s}"
          f" {'season |err| p50/p99/max':>26s} {'mean tenth':>11s} {'n_races':>8s}"
          f" {'best max':>9s} {'near rail':>10s} {'rank shift p50/p99/max (est.)':>30s}")
    for theta, s in sim.items():
        q = quantiles(s["mean_err"], (0.5, 0.99))
        mx = max(s["mean_err"]) if s["mean_err"] else None
        rs = rankShift(s["board"], sample)
        rq = quantiles(rs, (0.5, 0.99))
        rmax = max(rs) if rs else None
        pct = (100.0 * s["rebuilt"] / s["rows"]) if s["rows"] else 0.0
        spct = (100.0 * s["touched"] / s["seasons"]) if s["seasons"] else 0.0
        print(f"  {theta:>6.2f} {pct:>12.1f}% {spct:>15.1f}% {s['stale_flip']:>12,}"
              f" {fmt(q[0], 4):>8s}/{fmt(q[1], 4)}/{fmt(mx, 4):<7s} {s['mean_flip']:>11,}"
              f" {s['n_flip']:>8,} {s['best_err']:>9.3f} {s['near_rail']:>10,}"
              f" {str(rq[0]):>12s}/{rq[1]}/{rmax}")
    print("  stale tenth: rows the scheme left stale whose printed rating (%.1f) differs;")
    print("  mean tenth: seasons whose printed mean differs; n_races: seasons where")
    print("  the 20-point outlier rule kept a different set; near rail: stale rows")
    print("  within theta of their pool's raceCeiling (their board place could flip).")


# ---- boards ---------------------------------------------------------------- #

def currentYears(cur):
    cur.execute("SELECT sport, max(year) FROM athlete_season GROUP BY sport")
    return {s: y for s, y in cur.fetchall()}


def perfTop(cur, pool, sport, year, m):
    yr = "AND year = %(y)s" if year is not None else ""
    cur.execute(f"""SELECT result_id, person_id, speed_rating FROM ranking_results
                    WHERE pool = %(p)s AND sport = %(s)s {yr} AND speed_rating IS NOT NULL
                    ORDER BY speed_rating DESC LIMIT %(m)s""",
                {"p": pool, "s": sport, "y": year, "m": m})
    return [(int(r), int(p), float(v)) for r, p, v in cur.fetchall()]


def abilityTop(cur, pool, sport, year, m):
    yr = "AND s.year = %(y)s" if year is not None else ""
    cur.execute(f"""SELECT s.person_id, s.year, s.mean_rating, s.n_races FROM athlete_season s
                    WHERE s.pool = %(p)s AND s.sport = %(s)s {yr}
                      AND (s.n_races >= %(f)s OR s.year >= %(o)s) AND s.mean_rating IS NOT NULL
                    ORDER BY s.mean_rating DESC NULLS LAST, s.person_id LIMIT %(m)s""",
                {"p": pool, "s": sport, "y": year, "m": m, "f": DEFAULT_FLOOR, "o": OPEN_FROM})
    return [(int(p), int(y), float(v), int(n)) for p, y, v, n in cur.fetchall()]


def boardsNow(cur, kmax):
    years = currentYears(cur)
    out = {}
    for sport in SPORTS:
        for pool in POOLS:
            for scope, yr in (("all", None), ("season", years.get(sport))):
                if scope == "season" and yr is None:
                    continue
                k = f"performance|{pool}|{sport}|{scope}"
                out[k] = [[r, p, v] for r, p, v in perfTop(cur, pool, sport, yr, kmax * CAND_FACTOR)]
                k = f"ability|{pool}|{sport}|{scope}"
                out[k] = [[p, y, v, n] for p, y, v, n in abilityTop(cur, pool, sport, yr, kmax * CAND_FACTOR)]
    return out, years


def compareOrders(new_keys, old_keys, k):
    """Membership and position changes in the top k."""
    nk, ok = new_keys[:k], old_keys[:k]
    out_ = len(set(nk) - set(ok))
    moved = sum(1 for i, x in enumerate(nk) if i >= len(ok) or ok[i] != x)
    pos_old = {x: i for i, x in enumerate(old_keys)}
    shift = [abs(i - pos_old[x]) for i, x in enumerate(nk) if x in pos_old]
    return out_, moved, (sum(shift) / len(shift)) if shift else 0.0


def printBoardChurn(title, rows):
    print(f"\n== {title}")
    print(f"  {'board':44s}" + "".join(f" {'top' + str(k) + ' new':>10s} {'moved':>6s} {'avg|dpos|':>9s}"
                                       for k in TOP_K))
    for key, per_k in rows:
        print(f"  {key[:44]:44s}" + "".join(f" {a:>10,} {b:>6,} {c:>9.2f}" for a, b, c in per_k))


def boardChurnBackup(conn, cur, boards, years):
    """Performance boards: the same candidates ordered by their backed-up
    ratings (rows the backup lacks rank as new). Ability boards, this
    season: run_prev_athlete's means."""
    rows = []
    have_prev = regclass(cur, "run_prev_athlete")
    for key, cand in ((k, c) for k, c in boards.items() if c):
        board, pool, sport, scope = key.split("|")
        if board == "performance":
            bk = BACKUP[sport][0]
            if not regclass(cur, bk):
                continue
            ids = [r for r, _p, _v in cand]
            cur.execute(f"SELECT result_id, speed_rating FROM {bk} WHERE result_id = ANY(%s)", (ids,))
            old = {int(r): float(v) for r, v in cur.fetchall() if v is not None}
            new_keys = [r for r, _p, _v in cand]
            old_keys = [r for r, _p, _v in sorted(cand, key=lambda c: -old.get(c[0], -1e9))]
        else:
            if scope != "season" or not have_prev:
                continue
            ids = [p for p, _y, _v, _n in cand]
            cur.execute("""SELECT person_id, mean_rating FROM run_prev_athlete
                           WHERE person_id = ANY(%s) AND pool = %s AND sport = %s AND year = %s""",
                        (ids, pool, sport, years.get(sport)))
            old = {int(p): float(v) for p, v in cur.fetchall() if v is not None}
            new_keys = [p for p, _y, _v, _n in cand]
            old_keys = [p for p, _y, _v, _n in sorted(cand, key=lambda c: -old.get(c[0], -1e9))]
        rows.append((key, [compareOrders(new_keys, old_keys, k) for k in TOP_K]))
    return rows


def boardChurnSnapshot(boards, old_boards):
    rows = []
    for key, cand in ((k, c) for k, c in boards.items() if c):
        if key not in old_boards:
            continue
        if key.startswith("performance"):
            nk = [c[0] for c in cand]
            okk = [c[0] for c in old_boards[key]]
        else:
            nk = [(c[0], c[1]) for c in cand]
            okk = [(c[0], c[1]) for c in old_boards[key]]
        rows.append((key, [compareOrders(nk, okk, k) for k in TOP_K]))
    return rows


def topOfBoardSim(conn, cur, boards, backup_of, base):
    """The incremental scheme at the TOP of each board, exactly: the
    candidates' own rows, old ratings from the baseline, both orders."""
    print("\n== THE SCHEME AT THE TOP OF EACH BOARD (exact, the candidates' own rows)")
    print(f"  {'board':44s} {'theta':>6s}" + "".join(f" {'top' + str(k) + ' wrong pos':>14s} {'members':>8s}"
                                                       for k in TOP_K))
    for key, cand in ((k, c) for k, c in boards.items() if c):
        board, pool, sport, scope = key.split("|")
        if board == "performance":
            ids = [c[0] for c in cand]
            old = oldRatings(cur, sport, ids, backup_of, base)
            if old is None:
                continue
            for theta in THETAS:
                hyb = []
                for r, _p, v in cand:
                    o = old.get(r)
                    hyb.append((v if o is None or abs(v - o) >= theta else o, r))
                full_keys = [r for r, _p, _v in cand]
                hyb_keys = [r for _v, r in sorted(hyb, key=lambda x: -x[0])]
                _print_top(key, theta, full_keys, hyb_keys)
        else:
            persons = sorted({c[0] for c in cand})
            ys = {(c[0], c[1]) for c in cand}
            cur.execute("""SELECT person_id, year, race_date, result_id, speed_rating
                           FROM ranking_results WHERE person_id = ANY(%s) AND pool = %s
                             AND sport = %s AND speed_rating IS NOT NULL""", (persons, pool, sport))
            got = cur.fetchall()
            old = oldRatings(cur, sport, [r for _p, _y, _d, r, _v in got], backup_of, base)
            if old is None:
                continue
            by = {}
            for p, y, d, r, v in got:
                if (p, y) in ys:
                    by.setdefault((p, y), []).append((float(v), asDate(d), old.get(r)))
            dirty = {p for (p, _y), g in by.items() for _v, _d, o in g if o is None}
            for theta in THETAS:
                full, hyb = [], []
                for (p, y), g in by.items():
                    f = seasonAgg([(v, d) for v, d, _o in g])
                    h = seasonAgg([(v if (p in dirty or o is None or abs(v - o) >= theta) else o, d)
                                   for v, d, o in g])
                    if f and floorOk(y, f[3], f[0]):
                        full.append((f[0], (p, y)))
                    if h and floorOk(y, h[3], h[0]):
                        hyb.append((h[0], (p, y)))
                full_keys = [k for _v, k in sorted(full, key=lambda x: (-x[0], x[1]))]
                hyb_keys = [k for _v, k in sorted(hyb, key=lambda x: (-x[0], x[1]))]
                _print_top(key, theta, full_keys, hyb_keys)


def _print_top(key, theta, full_keys, hyb_keys):
    cells = []
    for k in TOP_K:
        wrong = sum(1 for i, x in enumerate(full_keys[:k]) if i >= len(hyb_keys) or hyb_keys[i] != x)
        members = len(set(full_keys[:k]) - set(hyb_keys[:k]))
        cells.append(f" {wrong:>14,} {members:>8,}")
    print(f"  {key[:44]:44s} {theta:>6.2f}" + "".join(cells))


def oldRatings(cur, sport, ids, backup_of, base):
    if base is not None:
        return {r: base[(sport, r)][2] for r in ids
                if (sport, r) in base and base[(sport, r)][2] is not None}
    bk = backup_of.get(sport)
    if not bk:
        return None
    cur.execute(f"SELECT result_id, speed_rating FROM {bk} WHERE result_id = ANY(%s)", (list(ids),))
    return {int(r): float(v) for r, v in cur.fetchall() if v is not None}


# ---- main ------------------------------------------------------------------ #

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=100,
                    help="people with person_id %% N = 0 (default 100: 1%%)")
    ap.add_argument("--save", metavar="FILE", help="write tonight's snapshot (.tsv.gz) for --against")
    ap.add_argument("--against", metavar="FILE", help="compare with a snapshot --save wrote")
    ap.add_argument("--no-boards", action="store_true", help="skip the top-of-board sections")
    ap.add_argument("--sports", default="XC,TF")
    a = ap.parse_args()
    sports = [s for s in a.sports.split(",") if s in SPORTS]
    sample = max(1, a.sample)

    from database import getConn
    print(f"[churn] {datetime.datetime.now():%Y-%m-%d %H:%M}  sample 1/{sample}  "
          f"season rule: k={DECAY_K} q={SEASON_Q} outlier={OUTLIER_PTS}")
    base = old_meta = old_seasons = old_boards = None
    if a.against:
        old_meta, base, old_seasons, old_boards = loadSnapshot(a.against)
        print(f"[churn] baseline: {a.against} (taken {old_meta.get('taken_at')}, "
              f"sample 1/{old_meta.get('sample')}, {len(base):,} rows)")
        if int(old_meta.get("sample", sample)) != sample:
            print("  ! the snapshot's sample differs; using its rate")
            sample = int(old_meta["sample"])

    with getConn() as conn:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '10min'")
            cur.execute("SET lock_timeout = '5s'")
            cur.execute("SET idle_in_transaction_session_timeout = '15min'")
            cur.execute("SET default_transaction_read_only = on")
        conn.commit()
        try:
            run(conn, a, sports, sample, base, old_meta, old_seasons, old_boards)
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                for s in ("statement_timeout", "lock_timeout",
                          "idle_in_transaction_session_timeout", "default_transaction_read_only"):
                    cur.execute(f"RESET {s}")
            conn.commit()


def run(conn, a, sports, sample, base, old_meta, old_seasons, old_boards):
    cur = conn.cursor()

    # -- 0. what is there --------------------------------------------------- #
    def facts():
        names = ("ranking_results", "athlete_season", "ranking_results_old", "athlete_season_old",
                 "results_speed_rating_backup", "results_tf_speed_rating_backup",
                 "run_prev_athlete", "board_size", "board_seek")
        print("\n== TABLES (catalogue estimates)")
        for name, n, size, ins, upd, dele, ana, vac in tableFacts(cur, names):
            print(f"  {name:34s} ~{n:>13,} rows {size / 2**30:8.2f} GB  analysed {ana or '-'}")
        info = {}
        for sport, (bk, src) in BACKUP.items():
            if not regclass(cur, bk):
                print(f"  {bk}: absent")
                continue
            cur.execute(f"SELECT max(result_id) FROM {bk}")
            mx = cur.fetchone()[0]
            seen = after = None
            if mx is not None and "scraped_at" in columns(cur, src):
                cur.execute(f"SELECT scraped_at FROM {src} WHERE result_id = %s", (mx,))
                r = cur.fetchone()
                seen = r[0] if r else None
                cur.execute(f"SELECT count(*) FROM {src} WHERE result_id > %s", (mx,))
                after = cur.fetchone()[0]
            info[sport] = bk
            print(f"  {bk}: newest result id {mx} (scraped {seen or '?'}); "
                  f"{after if after is not None else '?'} results after it in {src}")
        if regclass(cur, "run_prev_meta"):
            cur.execute("SELECT taken_at FROM run_prev_meta LIMIT 1")
            r = cur.fetchone()
            print(f"  run_prev_athlete taken at {r[0] if r else '?'} (step 00b of the last full run)")
        for sport, (_bk, src) in BACKUP.items():
            if not regclass(cur, src):
                continue
            cur.execute(f"SELECT max(result_id) FROM {src}")
            max_ids[sport] = cur.fetchone()[0]
            then = ((old_meta or {}).get("max_result_id") or {}).get(sport)
            if then is not None:
                cur.execute(f"SELECT count(*) FROM {src} WHERE result_id > %s", (then,))
                print(f"  {src}: {cur.fetchone()[0]:,} results added since the snapshot "
                      f"(id > {then})")
        return info
    max_ids = {}
    backup_of = section(conn, "tables", facts) or {}
    use_backup = base is None

    have = section(conn, "columns", lambda: columns(cur, "ranking_results")) or set()
    if not have:
        print("ranking_results not found; nothing to measure")
        return

    # -- 1-2. rows, and the simulation ------------------------------------- #
    tallies, other, sim = {}, {}, newSim()
    keep_rr, full_seasons = {}, {}
    for sport in sports:
        bk = backup_of.get(sport) if use_backup else None
        if use_backup and not bk:
            print(f"  {sport}: no baseline for the ratings (no backup, no --against); "
                  "rows counted, nothing compared")

        def one(sport=sport, bk=bk):
            k, full = collectSport(conn, have, sport, sample, base, bk, sim, tallies, other)
            keep_rr.update(k)
            full_seasons.update(full)
        section(conn, f"rows {sport}", one)

    src = "the snapshot" if base is not None else "the go-live backup"
    printTallies(f"speed_rating ON THE BOARDS' ROWS, live against {src}", tallies, sample)
    if base is not None:
        print("\n== OTHER COLUMNS OF THE SAME ROWS (snapshot)")
        print(f"  {'sport':5s} {'pool':10s} {'compared':>9s} {'person':>8s} {'pool':>8s}"
              f" {'school/state/grade/units':>25s} {'date/race/time/distance':>24s}")
        for (sport, pool), t in sorted(other.items()):
            print(f"  {sport:5s} {pool[:10]:10s} {t[0]:>9,} {t[1]:>8,} {t[2]:>8,} {t[3]:>25,} {t[4]:>24,}")
        live_keys = {k for k in keep_rr}
        gone = [k for k, v in base.items() if v[0] % sample == 0 and k not in live_keys
                and k[0] in sports]
        print(f"  rows in the snapshot and not on the boards now: {len(gone):,} "
              f"(~{len(gone) * sample:,} in all)")
    printSim(sim, sample)

    # -- 3. athlete_season ------------------------------------------------- #
    def seasons():
        as_have = columns(cur, "athlete_season")
        cur.execute(f"""SELECT person_id, pool, sport, year, mean_rating, decayed_rating,
                               best_rating, n_races, last_race, {hashExpr('a', AS_ID_COLS, as_have)}
                        FROM athlete_season a WHERE person_id %% %s = 0
                          AND sport = ANY(%s)""", (sample, sports))
        live = {(int(p), pl, s, int(y)): (m, d, b, int(n), str(l), h)
                for p, pl, s, y, m, d, b, n, l, h in cur.fetchall()}
        print(f"\n== athlete_season, sampled seasons: {len(live):,} (~{len(live) * sample:,})")
        # ! THE SIMULATION'S SEASON RULE, CHECKED AGAINST THE BUILDER'S OWN
        #   OUTPUT: the recompute from the live rows must land on the live
        #   mean (float4: within 1e-3), or the simulation measures nothing.
        ok = bad = 0
        worst = 0.0
        for k, f in full_seasons.items():
            v = live.get(k)
            if f is None or v is None or v[0] is None:
                continue
            e = abs(float(v[0]) - f[0])
            worst = max(worst, e)
            ok += e < 1e-3
            bad += e >= 1e-3
        print(f"  season rule check: {ok:,} recomputed means equal the live ones, "
              f"{bad:,} differ (worst {worst:.4f})")
        if old_seasons is not None:
            names = ("mean_rating", "decayed_rating", "best_rating")
            cmp_, new_, chg = 0, 0, {"n_races": 0, "last_race": 0, "identity": 0}
            moves = {n: Tally() for n in names}
            for k, v in live.items():
                o = old_seasons.get(k)
                if o is None:
                    new_ += 1
                    continue
                cmp_ += 1
                for i, n in enumerate(names):
                    if v[i] is not None:
                        moves[n].add(v[i], o[i], True)
                chg["n_races"] += v[3] != o[3]
                chg["last_race"] += v[4] != o[4]
                chg["identity"] += v[5] != o[5]
            gone = sum(1 for k in old_seasons if k not in live and k[2] in sports)
            print(f"  compared {cmp_:,}, new {new_:,}, gone {gone:,}; changed: "
                  + ", ".join(f"{k} {v:,}" for k, v in chg.items()))
            for n in names:
                printTallies(f"athlete_season.{n} (snapshot)", {("all", n): moves[n]}, sample)
        elif regclass(cur, "run_prev_athlete"):
            cur.execute("""SELECT person_id, pool, sport, year, mean_rating FROM run_prev_athlete
                           WHERE person_id %% %s = 0""", (sample,))
            t = {}
            prev = {(int(p), pl, s, int(y)): m for p, pl, s, y, m in cur.fetchall()}
            year_of = {}
            for (_p, _pl, s, y) in prev:
                year_of[s] = max(y, year_of.get(s, y))
            for k, v in live.items():
                if k[3] != year_of.get(k[2]) or v[0] is None or not float(v[0]) > 0:
                    continue
                o = prev.get(k)
                t.setdefault((k[2], k[1]), Tally()).add(v[0], o, o is not None)
            printTallies("athlete_season.mean_rating THIS SEASON, live against run_prev_athlete",
                         t, sample)
        return live
    live_seasons = section(conn, "athlete_season", seasons)

    # -- 4. boards ---------------------------------------------------------- #
    boards = years = None
    if not a.no_boards or a.save:
        got = section(conn, "boards", lambda: boardsNow(cur, max(TOP_K)))
        if got:
            boards, years = got
    if boards and not a.no_boards:
        if old_boards:
            rows = boardChurnSnapshot(boards, old_boards)
            title = "TOP OF THE DEFAULT BOARDS, live against the snapshot (full rebuild vs full rebuild)"
        else:
            rows = section(conn, "board churn", lambda: boardChurnBackup(conn, cur, boards, years)) or []
            title = ("TOP OF THE DEFAULT BOARDS, the same candidates by their old ratings "
                     "(performance: the backup; ability this season: run_prev_athlete)")
        printBoardChurn(title, rows)
        section(conn, "top-of-board simulation",
                lambda: topOfBoardSim(conn, cur, boards, backup_of if use_backup else {}, base))

    # -- 5. inputs ---------------------------------------------------------- #
    since = None
    if old_meta:
        since = old_meta.get("taken_at")
    else:
        def prevTaken():
            if regclass(cur, "run_prev_meta"):
                cur.execute("SELECT taken_at FROM run_prev_meta LIMIT 1")
                r = cur.fetchone()
                return r[0] if r else None
            return None
        since = section(conn, "baseline time", prevTaken)
    inputs = section(conn, "inputs", lambda: inputCounts(cur, since)) or {}
    print(f"\n== THE BUILD'S INPUTS (catalogue; 'since' counts rows stamped at or after {since or '-'})")
    print(f"  {'table':24s} {'rows (est.)':>14s} {'then':>14s} {'since':>22s}")
    for name, rec in sorted(inputs.items()):
        then = ((old_meta or {}).get("inputs") or {}).get(name, {}).get("rows")
        s = rec.get("since")
        print(f"  {name:24s} {rec['rows']:>14,} {format(then, ',') if then is not None else '-':>14} "
              f"{(s[0] + ' ' + format(s[1], ',')) if s else '-':>22s}")

    # -- save ---------------------------------------------------------------- #
    if a.save:
        def taken():
            cur.execute("SELECT now()")
            return cur.fetchone()[0]
        now = section(conn, "now", taken)
        # the board candidates' own rows too, so tomorrow's top-of-board
        # simulation has their old ratings
        extra = set()
        for key, cand in (boards or {}).items():
            _b, _pool, sport, _scope = key.split("|")
            for c in cand:
                extra.add((sport, c[1] if key.startswith("performance") else c[0]))

        def candRows():
            for sport in sports:
                ids = sorted(p for s, p in extra if s == sport and p % sample != 0)
                for i in range(0, len(ids), 5000):
                    cur.execute(f"""SELECT result_id, person_id, pool, speed_rating,
                                           {hashExpr('rr', ID_COLS, have)}, {hashExpr('rr', RACE_COLS, have)}
                                    FROM ranking_results rr WHERE sport = %s AND person_id = ANY(%s)""",
                                (sport, ids[i:i + 5000]))
                    for r, p, pool, v, h1, h2 in cur.fetchall():
                        keep_rr[(sport, int(r))] = (int(p), pool, v, h1, h2)
        section(conn, "candidate rows", candRows)
        meta = {"taken_at": str(now), "sample": sample, "sports": sports,
                "inputs": inputs, "max_result_id": max_ids, "version": 1}
        seas = {k: v for k, v in (live_seasons or {}).items()}
        saveSnapshot(a.save, meta, keep_rr, seas, boards or {})
        print(f"\n[churn] saved {len(keep_rr):,} rows, {len(seas):,} seasons, "
              f"{len(boards or {})} boards to {a.save}")
    cur.close()


if __name__ == "__main__":
    main()
