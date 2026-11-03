# Project: xc-predictor / engine
# File:    provisional_day.py
# Purpose: a race-day term for the cross country races the weekly solve has
#          not seen yet. Nightly step 09c, after 09b prices the new rows.
#
#     python engine/provisional_day.py            # DRY RUN: the backtest and the races
#     python engine/provisional_day.py --write    # what the nightly runs
#
# ★ WHY (owner, 2026-11-03, /race/xc/28639/1, the NESCAC championship three
#   days old: "the whole no course difficulty race day thing is killing me,
#   when will that be fixed?"). The race-day term is fitted only by the joint
#   solve, which runs with the weekly full pipeline. Every race since then
#   was priced by 09b at its course and "a typical day", and its "How this
#   was rated" said "Not available: the solve kept no race-day term". A
#   championship run fast or in mud read several points off until Sunday.
#
# ★ THE FIELD SAYS WHAT THE DAY WAS, the way fill_ratings' field anchor
#   already reads a race with no course. For each runner with rated races in
#   the year before this one, g_i = ln(rating priced at the course with no
#   day / their level coming in), the level being the median of those races
#   (above_level's year, the median so one great or awful day does not set
#   it). The race's g is the median g_i. A slow day reads low, a fast one high.
#
# ★ CALIBRATED ON THE SOLVE'S OWN DAYS, NOT SET BY HAND. A runner's level is
#   not their ability on a typical day (they improve through a season; a
#   median of a year is not the solve's form term), so g is offset from
#   -tilt * u by an amount nobody can state. The races of the last WINDOW
#   days that the solve DID fit are read the same way, and their median
#   (g + tilt * u_solved) is that offset, b. Then u = -(g - b) / tilt,
#   shrunk toward 0 by its noise against the spread of the solved days:
#   u * t / (t + s^2 / n), with s the race's own spread of g_i and t the
#   variance of the solved days. A field of six reads a little; a field of
#   two hundred reads almost all of it.
#
# ! ONLY WHEN IT BEATS NO TERM AT ALL. The same arithmetic is run on the
#   solved races and scored against the solve's u: written only when its
#   error is at most GATE times the error of leaving the day at 0 (what the
#   site does now), on at least CAL_MIN races. Otherwise the night writes
#   nothing and says why.
# ! THE RATING CARRIES IT AS THE SOLVE'S DOES: clipped at RACE_DAY_CAP and
#   only for the sports the go-live named (explain_rating.dayModes, fast-only
#   under ':fast'), times the runner's tilt.
# ! IDEMPOTENT. Each night the race's rows are re-priced from their
#   normalized_time (fill_ratings.venueRating) and the day applied once; the
#   provisional rows of race_day_effect are deleted and rewritten. The
#   weekly go-live replaces race_day_effect and every rating, so a
#   provisional term lives until the solve has the race, never after.
# ! ONLY RACES ON A FITTED COURSE. A race with no course is the field
#   anchor's (fill_ratings.fieldOffsets), which already reads course and day
#   together from the field.

import argparse
import math
import statistics
import sys
import time
from collections import namedtuple

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

WINDOW_DAYS = 60          # how far back a race is looked at, new or solved
LEVEL_DAYS = (45, 365)    # the two levels tried: this season's form, and a year (above_level)
QUANTILES = (0.5, 0.75)   # the field's middle, and its front (those who raced it)
MIN_FIELD = 5             # runners with a level before a race gets a term
CAL_MIN_FIELD = 20        # a solved race used for the calibration
CAL_MAX = 600             # solved races read (by a stable hash, the same ones nightly)
CAL_MIN = 40              # solved races needed before anything is written
GATE = 0.85               # held-out error at most this share of "no term"'s
NIGHTLY_TOL = 0.002       # a rating within 0.2% of the venue price is the nightly's own

Row = namedtuple("Row", "result_id person_id source meet_id div_id normalized_time "
                        "pool rating_pool race_date distance speed_rating")


# --------------------------------------------------------------------- #
#  THE ARITHMETIC (pure; tests/test_provisional_day.py)
# --------------------------------------------------------------------- #

def mad(xs):
    """1.4826 * median absolute deviation: a robust sd."""
    if not xs:
        return 0.0
    m = statistics.median(xs)
    return 1.4826 * statistics.median([abs(x - m) for x in xs])


def quantile(xs, q):
    """percentile_cont: linear interpolation."""
    v = sorted(xs)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def raceReading(pairs, tilts, q=0.5):
    """[(priced rating with no day, level)], [tilts] -> (g, s, n, tilt): the
    field's q-quantile log ratio, its robust spread, the runners read and
    the field's median tilt; None under MIN_FIELD."""
    gs = [math.log(r / lv) for r, lv in pairs if r and lv and r > 0 and lv > 0]
    if len(gs) < MIN_FIELD:
        return None
    return quantile(gs, q), mad(gs), len(gs), statistics.median(tilts) if tilts else 1.0


def shrunk(g, s, n, tilt, b, t):
    """The day the reading says (log time, + = slow), shrunk toward 0 by its
    noise against the spread t of real days."""
    tilt = tilt if tilt and tilt > 0.3 else 1.0
    raw = -(g - b) / tilt
    noise = (s / tilt) ** 2 / max(n, 1)
    return raw * t / (t + noise)


def calibrate(solved):
    """solved: [(g, s, n, tilt, u_solved)] -> {"b", "t", "alpha", "beta"} or
    None. b: the offset of a field's level from the solve's ability; t: the
    variance of real days; then u = alpha + beta * shrunk(...), fitted by
    least squares, so a reading that tracks the days only partly is
    trusted only that far."""
    if len(solved) < CAL_MIN:
        return None
    b = statistics.median(g + tilt * u for g, _s, _n, tilt, u in solved)
    us = [u for *_x, u in solved]
    t = max(statistics.pvariance(us), 1e-6)
    xs = [shrunk(g, s, n, tilt, b, t) for g, s, n, tilt, _u in solved]
    mx, my = statistics.fmean(xs), statistics.fmean(us)
    sxx = sum((x - mx) ** 2 for x in xs)
    beta = (sum((x - mx) * (y - my) for x, y in zip(xs, us)) / sxx) if sxx > 0 else 0.0
    beta = max(0.0, min(beta, 1.5))
    return {"b": b, "t": t, "alpha": my - beta * mx, "beta": beta}


def dayFrom(g, s, n, tilt, cal):
    """The provisional day (log time, + = slow) for one race."""
    return cal["alpha"] + cal["beta"] * shrunk(g, s, n, tilt, cal["b"], cal["t"])


def backtest(solved, cal):
    """(rmse of the provisional day against the solve's, rmse of 0), in sample."""
    if not solved or not cal:
        return None, None
    e1 = [(dayFrom(g, s, n, tilt, cal) - u) ** 2 for g, s, n, tilt, u in solved]
    e0 = [u * u for *_x, u in solved]
    return math.sqrt(sum(e1) / len(e1)), math.sqrt(sum(e0) / len(e0))


def heldOut(solved):
    """(rmse, rmse of 0) with two folds: each half calibrated on the other,
    so the score is of races the calibration never saw."""
    folds = (solved[0::2], solved[1::2])
    e1, e0 = [], []
    for test, train in ((folds[0], folds[1]), (folds[1], folds[0])):
        cal = calibrate(train) if len(train) >= CAL_MIN // 2 else None
        if cal is None:
            return None, None
        for g, s, n, tilt, u in test:
            e1.append((dayFrom(g, s, n, tilt, cal) - u) ** 2)
            e0.append(u * u)
    return math.sqrt(sum(e1) / len(e1)), math.sqrt(sum(e0) / len(e0))


def passes(rmse, rmse0, n_cal, gate=GATE):
    return (rmse is not None and rmse0 is not None and n_cal >= CAL_MIN
            and rmse0 > 0 and rmse <= gate * rmse0)


def nightlyPriced(rated, priced, tol=NIGHTLY_TOL, share=0.8):
    """Were this race's ratings written by the nightly (at the venue price)
    rather than by the solve? [(stored rating, venue price)] -> bool."""
    pairs = [(a, b) for a, b in zip(rated, priced) if a and b]
    if not pairs:
        return False
    same = sum(abs(a / b - 1.0) <= tol for a, b in pairs)
    return same >= share * len(pairs)


# --------------------------------------------------------------------- #
#  THE DATABASE
# --------------------------------------------------------------------- #

_ROWS_SQL = """
    SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id, r.normalized_time,
           split_part(k.pool, '|', 1) AS pool, r.rating_pool, k.race_date::date AS race_date,
           COALESCE(dov.distance, m.distance) AS distance, r.speed_rating
    FROM   ranking_results k
    JOIN   results r ON r.result_id = k.result_id
    LEFT   JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id AND m.source = r.source
    LEFT   JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
    WHERE  k.sport = 'XC' AND k.race_date >= current_date - %(window)s
      AND  r.normalized_time > 0 AND r.speed_rating > 0
"""

_RACES_SQL = """
    SELECT DISTINCT r.source, r.meet_id, r.div_id
    FROM   ranking_results k JOIN results r ON r.result_id = k.result_id
    WHERE  k.sport = 'XC' AND k.race_date >= current_date - {window}
"""

_LEVEL_SQL = """
    SELECT p.pid, p.day, p.pool, l.short, l.long
    FROM   unnest(%(pids)s::bigint[], %(days)s::date[], %(pools)s::text[]) AS p(pid, day, pool)
    CROSS  JOIN LATERAL (
           SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY k.speed_rating)
                      FILTER (WHERE k.race_date >= p.day - %(short)s) AS short,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY k.speed_rating) AS long
           FROM   ranking_results k
           WHERE  k.person_id = p.pid AND k.sport = 'XC'
             AND  k.race_date < p.day AND k.race_date >= p.day - %(long)s
             AND  k.speed_rating > 0
             AND  split_part(k.pool, '|', 1) = p.pool) l
"""


def _solveCutoff(cur):
    """The newest race day the last solve fitted: a race after it is new to
    the solve; one on or before it was the solve's to rate (with a term or
    without one) and is never re-priced here."""
    cur.execute("""SELECT max(race_date) FROM race_day_effect e
                   WHERE NOT COALESCE((to_jsonb(e) ->> 'provisional')::boolean, false)""")
    return cur.fetchone()[0]


def _dayRows(cur):
    """{key: (u, n, provisional)} for race_day_effect's rows in the window,
    keyed (canonical_id, distance_m, date) and (course_name, distance_m, date)."""
    cur.execute("""
        SELECT canonical_id, course_name, distance_m, race_date, day_effect, n_rows,
               COALESCE((to_jsonb(e) ->> 'provisional')::boolean, false)
        FROM   race_day_effect e
        WHERE  race_date >= current_date - %(w)s - 7""", {"w": WINDOW_DAYS})
    out = {}
    for cid, cname, dm, day, u, n, prov in cur.fetchall():
        if dm is None:
            continue
        v = (float(u), int(n or 0), bool(prov))
        if cid is not None:
            out.setdefault((int(cid), int(dm), day), v)
        if cname:
            out.setdefault((cname, int(dm), day), v)
    return out


def _levels(cur, wanted):
    """{(person, day, pool): (level over LEVEL_DAYS[0], over LEVEL_DAYS[1])}."""
    wanted = sorted(wanted)
    out = {}
    for i in range(0, len(wanted), 20000):
        chunk = wanted[i:i + 20000]
        cur.execute(_LEVEL_SQL, {"short": LEVEL_DAYS[0], "long": LEVEL_DAYS[1],
                                 "pids": [w[0] for w in chunk],
                                 "days": [w[1] for w in chunk],
                                 "pools": [w[2] for w in chunk]})
        for pid, day, pool, short, long_ in cur.fetchall():
            if short or long_:
                out[(int(pid), day, pool)] = (float(short) if short else None,
                                              float(long_) if long_ else None)
    return out


def ensureColumn(conn):
    """race_day_effect.provisional, for a table the go-live wrote before the
    column existed (a catalogue-only change; a short lock, never a wait)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.race_day_effect')")
        if cur.fetchone()[0] is None:
            conn.commit()
            return False
        cur.execute("SET LOCAL lock_timeout = '5s'")
        try:
            cur.execute("ALTER TABLE race_day_effect "
                        "ADD COLUMN IF NOT EXISTS provisional boolean NOT NULL DEFAULT false")
            conn.commit()
            return True
        except Exception as exc:                          # noqa: BLE001
            conn.rollback()
            print(f"  race_day_effect busy ({type(exc).__name__}); provisional days skipped tonight")
            return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--race", action="append", default=[],
                    help="MEET/DIV: say how this race was classified, and why")
    args = ap.parse_args()
    t0 = time.time()

    import psycopg2.extras
    from database import getConn
    from explain_rating import dayModes, appliedDay
    from fill_ratings import venueMap, rowDifficulty, venueRating, rowDistance
    from conversions import _tilt

    mode = dayModes().get("XC")
    if not mode:
        print("[provisional day] cross country ratings carry no race-day term "
              "(the go-live named none): nothing to do")
        return 0

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.race_day_effect')")
            if cur.fetchone()[0] is None:
                print("[provisional day] no race_day_effect yet (the go-live writes it)")
                return 0
            days = _dayRows(cur)
            cutoff = _solveCutoff(cur)
            cur.execute(_ROWS_SQL, {"window": WINDOW_DAYS})
            rows = [Row(*r) for r in cur.fetchall()]
        conn.commit()
        races, cells = venueMap(conn, "XC", _RACES_SQL.format(window=int(WINDOW_DAYS)))
        print(f"[provisional day] {len(rows):,} rated rows in the last {WINDOW_DAYS} days, "
              f"{len(days):,} race days with a term", flush=True)

        # ★ ONE DAY PER COURSE-DAY, as the solve files it (race_day_effect's
        #   key, joint_golive.writeRaceDays): every division and both feeds'
        #   copies of a race on one course, distance and date share the day.
        #   A runner in two copies is read once (their first row).
        by_race = {}
        for r in rows:
            d = rowDifficulty(r, "XC", races, cells)
            if d is None or r.distance is None:
                continue
            dm = int(round(float(r.distance) / 100.0)) * 100
            cid, champ = races.get((r.source, r.meet_id, r.div_id)) or (None, None)
            champ_key = champ if champ and (champ, dm) in cells else None
            key = (champ_key or int(cid), dm, r.race_date)
            g = by_race.setdefault(key, {"cid": cid, "champ": champ_key, "dm": dm,
                                         "d": d, "rows": [], "seen": set(),
                                         "races": set()})
            g["rows"].append(r)
            g["races"].add((r.meet_id, r.div_id))

        # ★ A RACE PRICED AT ITS COURSE, NO DAY: the reading's numerator,
        #   and the test of who wrote the stored ratings. Only the races
        #   read (the sampled solved ones and every unsolved one): pricing
        #   two million rows is a fixed point each.
        def price(g):
            g["priced"], g["field"], seen = {}, [], set()
            for r in g["rows"]:
                rv = venueRating(float(r.normalized_time), r.pool, "XC", g["d"],
                                 rowDistance(r, "XC"))
                if rv is None:
                    continue
                g["priced"][r.result_id] = (rv, r)
                if r.person_id is not None and r.person_id not in seen and r.pool:
                    seen.add(r.person_id)         # a runner in both feeds' copies, once
                    g["field"].append((r, rv))

        # ★ NEW = THE NIGHTLY PRICED IT, NOT "DATED AFTER THE SOLVE" (owner's
        #   dry run, 2026-11-03: the last solved day was Oct 31, so the
        #   NESCAC championship of Oct 31 -- scraped after that solve, priced
        #   by 09b, no day -- was skipped as the solve's). A race the solve
        #   rated carries its own numbers (era, form, day); one 09b priced
        #   sits at the venue price to the rounding (nightlyPriced), or
        #   carries last night's provisional day.
        # --race: why a race was (not) given a provisional day
        for want in args.race:
            try:
                wm, wd = (int(x) for x in want.strip("/").split("/")[-2:])
            except ValueError:
                print(f"  --race {want!r}: want MEET/DIV")
                continue
            hits = [(k, g) for k, g in by_race.items() if (wm, wd) in g["races"]]
            if not hits:
                inwin = [r for r in rows if r.meet_id == wm and r.div_id == wd]
                if inwin:
                    print(f"  race {wm}/{wd}: {len(inwin)} rated rows in the window, but no "
                          f"fitted course found for them (venueMap/course_difficulties)")
                    continue
                # which of the row query's conditions drops this race
                with conn.cursor() as c2:
                    c2.execute("""
                        SELECT r.source, count(*), count(*) FILTER (WHERE r.normalized_time > 0),
                               count(*) FILTER (WHERE r.speed_rating > 0), count(k.result_id),
                               min(k.race_date)::text, max(k.race_date)::text, min(left(r.date, 10))
                        FROM   results r
                        LEFT   JOIN ranking_results k ON k.result_id = r.result_id AND k.sport = 'XC'
                        WHERE  r.meet_id = %s AND r.div_id = %s GROUP BY 1""", (wm, wd))
                    got = c2.fetchall()
                conn.commit()
                print(f"  race {wm}/{wd}: no rated rows in the last {WINDOW_DAYS} days")
                for src, n, nt, sr, kb, d0, d1, rd in got:
                    print(f"    {src}: {n} rows, {nt} normalised, {sr} rated, {kb} on the boards"
                          f" (board date {d0}..{d1}, results date {rd})")
            for k, g in hits:
                hit = days.get(k)
                price(g)
                stored = [(float(r.speed_rating), rv) for rv, r in g["priced"].values()]
                same = sum(abs(a / b - 1) <= NIGHTLY_TOL for a, b in stored if a and b)
                print(f"  race {wm}/{wd}: course-day key {k}, difficulty {g['d']:+.4f}, "
                      f"{len(g['rows'])} rows over {len(g['races'])} race(s)")
                print(f"    race_day_effect: {('provisional ' if hit[2] else 'SOLVED ') + f'{hit[0]:+.4f}' if hit else 'no row'}"
                      f"; stored ratings at the nightly's venue price: {same}/{len(stored)}"
                      + (f" (e.g. stored {stored[0][0]:.2f} vs venue {stored[0][1]:.2f})" if stored else ""))
        solved, new = [], []
        for key, g in by_race.items():
            hit = days.get(key)
            if hit and not hit[2]:
                if len({r.person_id for r in g["rows"]}) >= CAL_MIN_FIELD:
                    solved.append((key, g, hit[0]))
                continue
            price(g)
            stored = [(float(r.speed_rating), rv) for rv, r in g["priced"].values()]
            if (hit and hit[2]) or nightlyPriced([a for a, _b in stored], [b for _a, b in stored]):
                new.append((key, g))
        # the same solved races every night: by a stable hash of the race
        import zlib
        solved.sort(key=lambda x: zlib.crc32(f"{x[0][0]}/{x[0][1]}/{x[0][2]}".encode()))
        solved = solved[:CAL_MAX]
        for _key, g, _u in solved:
            price(g)
        solved = [x for x in solved if len(x[1]["field"]) >= CAL_MIN_FIELD]

        wanted = {(int(r.person_id), key[2], r.pool)
                  for key, g, *_u in solved + [(k, g) for k, g in new]
                  for r, _rv in g["field"]}
        with conn.cursor() as cur:
            levels = _levels(cur, wanted)
        conn.commit()
        print(f"  {len(new):,} races the nightly priced with no solved day (last solved day "
              f"{cutoff}), {len(solved):,} solved races to calibrate on, "
              f"{len(levels):,} runners' levels", flush=True)

        def reading(key, g, which, q):
            pairs, tilts = [], []
            for r, rv in g["field"]:
                tilts.append(_tilt(rv, r.pool))
                lv = levels.get((int(r.person_id), key[2], r.pool))
                if lv and lv[which]:
                    pairs.append((rv, lv[which]))
            return raceReading(pairs, tilts, q)

        # ★ FOUR READINGS, THE BEST ON RACES IT NEVER SAW. The level over this
        #   season's form or over a year, the field's middle or its front;
        #   each calibrated on half the solved races and scored on the other
        #   half (heldOut), and the one closest to the solve's own days kept.
        best = None
        for which, back in enumerate(LEVEL_DAYS):
            for q in QUANTILES:
                cal_rows = [(*got, u) for key, g, u in solved
                            if (got := reading(key, g, which, q))]
                ho, ho0 = heldOut(cal_rows)
                cal = calibrate(cal_rows)
                if ho is None or cal is None:
                    print(f"  level {back} d, field q{q:.2f}: too few solved races ({len(cal_rows)})")
                    continue
                print(f"  level {back} d, field q{q:.2f}: {len(cal_rows):,} solved races, "
                      f"held-out error {ho * 100:.2f}% vs no term {ho0 * 100:.2f}%  "
                      f"(slope {cal['beta']:.2f})")
                if best is None or ho < best[0]:
                    best = (ho, ho0, which, q, cal, len(cal_rows))
        ok = best is not None and passes(best[0], best[1], best[5])
        if best:
            print(f"  kept: level {LEVEL_DAYS[best[2]]} d, field q{best[3]:.2f}")
        if not ok:
            print(f"  NOT WRITTEN: the provisional day must beat no term by "
                  f"{(1 - GATE):.0%} on races it never saw, with {CAL_MIN}+ solved races")
        cal = best[4] if best else None

        out_days, out_ratings = [], []
        shown = []
        for key, g in new:
            got = reading(key, g, best[2], best[3]) if best else None
            if not got:
                continue
            gg, sd, n, tilt = got
            u = dayFrom(gg, sd, n, tilt, cal) if cal else 0.0
            a = appliedDay(u, mode)
            for rid, (rv, r) in g["priced"].items():
                out_ratings.append((rid, round(rv * math.exp(_tilt(rv, r.pool) * a), 2),
                                    r.rating_pool or r.pool))
            out_days.append((g["champ"] or None, g["cid"], g["dm"], key[2], round(u, 5), n))
            shown.append((abs(a), key, g, u, n))
        shown.sort(key=lambda x: -x[0])
        for _a, key, g, u, n in shown[:args.show]:
            meet, div = sorted(g["races"])[0]
            more = f", {len(g['races'])} races" if len(g["races"]) > 1 else ""
            print(f"    /race/xc/{meet}/{div}  {key[2]}  {key[1]} m  "
                  f"{'slow' if u > 0 else 'fast'} {abs(u) * 100:.2f}%  "
                  f"({n} runners with a level{more})")
        tail = "" if not ok else "" if args.write else "  -- DRY RUN: --write writes them"
        print(f"  {len(out_days):,} races, {len(out_ratings):,} ratings{tail}")

        if not (args.write and ok):
            return 0
        if not ensureColumn(conn):
            return 0
        from speed_ratings_db import saveResultSpeedRatings, loadCanonicalNames
        names = loadCanonicalNames()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM race_day_effect WHERE provisional")
            psycopg2.extras.execute_values(cur, """
                INSERT INTO race_day_effect (course_name, canonical_id, distance_m, race_date,
                                             day_effect, n_rows, course_effect, provisional)
                VALUES %s""", [
                (champ or f"XC:{names.get(str(cid), cid)}", cid, dm, day, u, n, None, True)
                for champ, cid, dm, day, u, n in out_days])
        conn.commit()
    saveResultSpeedRatings("XC", iter(out_ratings), mode="update")
    print(f"[provisional day] written: {len(out_days):,} race days, {len(out_ratings):,} "
          f"ratings ({(time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
