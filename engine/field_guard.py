#!/usr/bin/env python3
"""
field_guard.py -- a whole field rated far above its own seasons is wrong,
whatever made it so. Pulled down to a believable peak.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P engine/field_guard.py                       # DRY RUN: every division it would pull down
    $P engine/field_guard.py --since 2015-01-01    # further back
    $P engine/field_guard.py --write               # apply (nightly 09c2, pipeline 09c2)
    $P engine/field_guard.py --race 285911/1138472 # one division, runner by runner

★ WHY (owner, 2026-11-05: "you need to find all things where this can
  happen ... and nuke it rn this cannot keep happening. I am sick of issues
  like these popping up like moles"). Each fault had its own cause and its
  own fix, and the next one was always another cause:

    Florida Horse Park 2026   the backfill and the solve on two pool scales  (+58%)
    Mt. SAC 2026 Race 53      a course-day's credit on a race run fast       (+7-9)
    Big Sky 2026, Buffalo Pk  a short 5000 (4% under the label) on a course
                              whose number a long-labelled George Kyte made
                              slow                                          (+7)
    Canutillo MS 2026         a '3000 m' run as about 2000 m                 (168)

  What they share is the evidence: the race's own runners, rated far above
  every other race of their season. That is checked here, on the ratings
  the site shows, after every term has been applied -- so it catches the
  next cause too.

★ THE RULE. Each runner's reference is the median of their OTHER cross
  country ratings that season (the same day's rows -- another feed's copy,
  another division of the meet -- are not "other races"), with MIN_REF of
  them. A division whose MIN_FIELD+ referenced runners sit a median GAP or
  more above their references, SHARE of them above by 2% or more, is pulled
  down to PEAK above: a field peaking for a championship rates a little
  above its season median, never a whole field GAP above it.

! ONE WAY ONLY (the field check's rule, owner 2026-10-15): a field can run
  under itself -- mud, heat, a tactical race -- and that is a real slow race.
! IDEMPOTENT: a pulled-down division reads PEAK the next night and is left
  alone; a full run that rewrites the ratings is guarded again by this step.
! LOGGED: field_guard_division holds every division moved, by how much.
  XCP_FIELD_GUARD=0 (nightly / pipeline) turns the step off.
"""
import argparse
import math
import os
import statistics
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIN_REF = 3          # other rated races that season before a runner testifies
MIN_FIELD = 15       # testifying runners before a division is judged
GAP = 0.05           # median log(rating / reference) that is not a peak
SHARE = 0.70         # ... with this share of them 2%+ above
UP = 0.02
PEAK = 0.02          # where a pulled-down division is left


def references(rows, min_ref=MIN_REF):
    """rows: (result_id, person, season, day, division, rating) ->
    {result_id: (division, log gap)} for each row whose runner has min_ref
    rated races that season on OTHER days. Pure."""
    by = defaultdict(list)
    for rid, pid, season, day, div, rating in rows:
        if pid is not None and rating and rating > 0:
            by[(pid, season)].append((rid, day, div, float(rating)))
    out = {}
    for races in by.values():
        if len(races) <= min_ref:
            continue
        for rid, day, div, rating in races:
            others = [r for _i, d, _v, r in races if d != day]
            if len(others) >= min_ref:
                out[rid] = (div, math.log(rating / statistics.median(others)))
    return out


def judge(gaps, min_field=MIN_FIELD, gap=GAP, share=SHARE, peak=PEAK):
    """{division: [log gaps]} -> {division: (median, n, share up, factor)}
    for the divisions to pull down; factor multiplies their ratings. Pure."""
    out = {}
    for div, gs in gaps.items():
        if len(gs) < min_field:
            continue
        med = statistics.median(gs)
        up = sum(1 for g in gs if g >= UP) / len(gs)
        if med >= gap and up >= share:
            out[div] = (med, len(gs), up, math.exp(-(med - peak)))
    return out


_ROWS = """
    SELECT r.result_id, r.person_id,
           left(r.date, 4)::int AS season,
           left(r.date, 10) AS day,
           r.source, r.meet_id, r.div_id, r.speed_rating
    FROM   results r
    WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
      AND  r.date >= %(since)s AND r.date < %(until)s
      AND  r.date ~ '^[0-9]{4}-'
"""

_LOG_DDL = """
CREATE TABLE IF NOT EXISTS field_guard_division (
    source text, meet_id bigint, div_id bigint, day text,
    gap real, n int, share_up real, factor real, rows_moved int,
    at timestamptz NOT NULL DEFAULT now())
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=None,
                    help="first date read (default: 150 days ago; a season needs its own races)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--race", help="MEET/DIV: the division runner by runner")
    a = ap.parse_args(argv)
    import datetime
    since = a.since or (datetime.date.today() - datetime.timedelta(days=150)).isoformat()
    # a season's races start before the window: read its whole calendar year
    since = since[:4] + "-01-01" if not a.since else since
    from database import getConn
    t0 = time.time()
    if a.race:
        # ★ ONE RACE IS ONE SEASON (owner, 2026-11-08: --race with --since
        #   2004 printed twenty-two seasons' lists and buried the one line).
        #   Read the race's own year, print that division only, write nothing.
        m, d = (int(x) for x in a.race.strip("/").split("/")[-2:])
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT min(left(date, 4)) FROM results WHERE meet_id = %s AND div_id = %s"
                        " AND date ~ '^[0-9]{4}-'", (m, d))
            yr = cur.fetchone()[0]
            conn.rollback()
        if not yr:
            print(f"[field guard] no dated results for /race/xc/{m}/{d}")
            return 1
        a.write, a.show = False, 0
        guardYear(getConn, a, f"{yr}-01-01", f"{int(yr) + 1}-01-01", t0)
        return 0
    # ★ ONE SEASON AT A TIME: a runner's reference never leaves their season,
    #   so the full pipeline's all-years run never holds the table at once
    total = 0
    for year in range(int(since[:4]), datetime.date.today().year + 1):
        lo = max(since, f"{year}-01-01")
        total += guardYear(getConn, a, lo, f"{year + 1}-01-01", t0)
    if a.write:
        print(f"[field guard] done: {total:,} divisions pulled down "
              f"({(time.time() - t0) / 60:.1f} min)")
    return 0


def guardYear(getConn, a, lo, hi, t0):
    """One season: read, judge, print, and (--write) pull down. Returns the
    number of divisions it moved (or would move)."""
    since = lo
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_ROWS, {"since": lo, "until": hi})
            raw = cur.fetchall()
        conn.rollback()
        rows = [(rid, pid, season, day, (src, int(meet), int(div)), rating)
                for rid, pid, season, day, src, meet, div, rating in raw]
        day_of = {r[4]: r[3] for r in rows}
        refs = references(rows)
        gaps = defaultdict(list)
        for _rid, (div, g) in refs.items():
            gaps[div].append(g)
        hit = judge(gaps)
        print(f"[field guard] {len(rows):,} rated cross country rows since {since}, "
              f"{len(refs):,} with {MIN_REF}+ other races that season, "
              f"{sum(1 for g in gaps.values() if len(g) >= MIN_FIELD):,} divisions judged "
              f"({time.time() - t0:.0f} s)")
        if a.race:
            m, d = (int(x) for x in a.race.strip("/").split("/")[-2:])
            found = False
            for div, gs in gaps.items():
                if div[1] == m and div[2] == d:
                    found = True
                    med = statistics.median(gs) if gs else 0
                    print(f"  {div[0]} {m}/{d}: {len(gs)} runners with references, median "
                          f"{100 * med:+.1f}% vs their own seasons, "
                          f"{100 * sum(1 for g in gs if g >= UP) / max(len(gs), 1):.0f}% up 2%+ "
                          f"-> {'PULLED DOWN' if div in hit else 'left alone'}"
                          + ("" if len(gs) >= MIN_FIELD else f" (under {MIN_FIELD}: not judged)"))
            if not found:
                print(f"  {m}/{d}: no runner in it has {MIN_REF}+ other rated races that season"
                      f" -- not judged")
            return 0
        print(f"[field guard] {len(hit):,} divisions whose field rated {100 * GAP:.0f}%+ above "
              f"its own seasons ({100 * SHARE:.0f}% of runners up), to be pulled down to "
              f"+{100 * PEAK:.0f}%")
        for div, (med, n, up, f) in sorted(hit.items(), key=lambda x: -x[1][0])[:a.show]:
            print(f"  {100 * med:+5.1f}%  {day_of.get(div)}  /race/xc/{div[1]}/{div[2]}  "
                  f"({div[0]}, {n} runners, {100 * up:.0f}% up)  x{f:.3f}")
        if not a.write:
            if hit:
                print("DRY RUN -- --write pulls them down")
            return len(hit)
        if not hit:
            return 0
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            cur.execute(_LOG_DDL)
            cur.execute("CREATE TEMP TABLE fg (source text, meet_id bigint, div_id bigint, "
                        "day text, f float8) ON COMMIT DROP")
            execute_values(cur, "INSERT INTO fg VALUES %s",
                           [(d[0], d[1], d[2], day_of.get(d), v[3]) for d, v in hit.items()])
            cur.execute("""UPDATE results r SET speed_rating = round((r.speed_rating * fg.f)::numeric, 2)
                           FROM fg WHERE r.source = fg.source AND r.meet_id = fg.meet_id
                             AND r.div_id = fg.div_id AND r.speed_rating > 0""")
            moved = cur.rowcount
            execute_values(cur, """INSERT INTO field_guard_division
                                   (source, meet_id, div_id, day, gap, n, share_up, factor, rows_moved)
                                   VALUES %s""",
                           [(d[0], d[1], d[2], day_of.get(d), v[0], v[1], v[2], v[3], None)
                            for d, v in hit.items()])
        conn.commit()
        print(f"[field guard] written: {len(hit):,} divisions, {moved:,} ratings pulled down "
              f"({(time.time() - t0) / 60:.1f} min)")
    return len(hit)


if __name__ == "__main__":
    sys.exit(main())
