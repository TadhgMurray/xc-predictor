#!/usr/bin/env python3
"""
audit_sources.py -- does every source land everything a race needs, and
land it right?

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/audit_sources.py                 # this season
    /srv/venv/bin/python scripts/audit_sources.py --since 2026-08-01 --show 15

★ WHY (owner, 2026-11-02: "make sure all the parsers are getting all needed
  info (like distance!), and make sure that they all get the correct
  things"). Two AthleticLIVE bugs that day -- a race with no distance though
  its document stated 5000 m, and a two-day meet dated day 1 throughout --
  were both a field the parser never read, and nothing counted them.

  Per source (athletic.net's own scrape, AthleticLIVE, timers, tfrrs,
  uploads), cross country and track:

  1. CAPTURE   rows, and the share missing what a rating needs: a time, a
               normalised time (no distance, no pool), a grade, a school, a
               place, a date
  2. ORDER     races where a runner placed BEHIND another has a time more
               than ORDER_SLACK seconds FASTER: a time read from the wrong
               column, a place from another race, mm:ss read as seconds
  3. PACE      cross country races whose winner or median is not a running
               pace at the stored distance (PACE_FAST / PACE_SLOW s per m)
  4. TWINS     the same runner in the same meet in two feeds (athletic.net
               and AthleticLIVE): their times should agree to the tenth.
               A race where they do not is one parser reading the other's
               race wrong -- the one check that compares against a source
               rather than against ourselves.

  The worst races of each check are listed with their racecast link, so
  each can be opened beside the source page. --requeue puts the athletic.net
  meets of checks 2 and 3 back in its queue (its saver rewrites time_seconds
  on a re-scrape). Otherwise READ ONLY.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ALIVE_BASE = 3_000_000_000_000      # sources/athleticlive RESULT_BASE / DIV_BASE
ORDER_SLACK = 1.0                   # seconds: ties and hand-timing noise
PACE_FAST = 0.160                   # s per metre: a 13:20 5K winner
PACE_SLOW = 0.55                    # s per metre: a 45:50 5K median
TWIN_TOL = 0.25                     # seconds: two feeds of one race
SENTINEL = 99999                    # anet's non-finish times are 999999

_FEED = f"""CASE WHEN r.source = 'anet' AND r.result_id >= {ALIVE_BASE} THEN 'athleticlive'
                 WHEN r.source = 'anet' THEN 'athletic.net'
                 ELSE r.source END"""


def capture(cur, table, since):
    """[(feed, rows, no time, not normalised, no grade, no school, no place)]"""
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = %s AND column_name IN ('place', 'status')""", (table,))
    have = {r[0] for r in cur.fetchall()}
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = %s AND column_name IN ('is_field', 'is_relay')""", (table,))
    kinds = {r[0] for r in cur.fetchall()}
    # a field mark and a relay are not a runner's time: track counts running events only
    running = "".join(f" AND COALESCE(r.{k}, 0) = 0" for k in sorted(kinds))
    place = "r.place" if "place" in have else "NULL::int"
    status = "r.status IS NULL" if "status" in have else "TRUE"
    cur.execute(f"""
        SELECT {_FEED} AS feed, count(*),
               count(*) FILTER (WHERE COALESCE(r.time_seconds, 0) <= 0),
               count(*) FILTER (WHERE r.time_seconds > 0 AND r.time_seconds < {SENTINEL}
                                  AND {status} AND r.normalized_time IS NULL),
               count(*) FILTER (WHERE COALESCE(btrim(r.grade), '') = ''),
               count(*) FILTER (WHERE COALESCE(btrim(r.school), '') IN ('', 'Unknown')),
               count(*) FILTER (WHERE {place} IS NULL OR {place} <= 0)
        FROM   {table} r
        WHERE  r.date >= %s{running}
        GROUP  BY 1 ORDER BY 2 DESC""", (since,))
    return cur.fetchall()


def orderBreaks(rows, slack=ORDER_SLACK):
    """rows [(race key, place, time)] -> {race: breaks}: a runner placed
    behind another with a time more than `slack` faster. Pure."""
    by = {}
    for key, place, t in rows:
        if place and place > 0 and t and 0 < t < SENTINEL:
            by.setdefault(key, []).append((int(place), float(t)))
    out = {}
    for key, v in by.items():
        v.sort()
        best_behind = None
        n = 0
        for _p, t in reversed(v):            # from last place forward
            if best_behind is not None and t > best_behind + slack:
                n += 1
            best_behind = t if best_behind is None else min(best_behind, t)
        if n:
            out[key] = n
    return out


def paceVerdict(times, meters, fast=PACE_FAST, slow=PACE_SLOW):
    """None when the race's times are a running pace at `meters`, else why.
    Pure."""
    t = sorted(float(x) for x in times if x and 0 < float(x) < SENTINEL)
    if not t or not meters or meters <= 0:
        return None
    if t[0] / meters < fast:
        return f"winner {t[0] / meters * 1000:.0f} s/km"
    med = t[len(t) // 2]
    if med / meters > slow:
        return f"median {med / meters * 1000:.0f} s/km"
    return None


def twinDiffs(rows, tol=TWIN_TOL):
    """rows [(meet, alive div, anet div, athlete, alive time, anet time)] ->
    {(meet, anet div): (pairs, off, median diff)} for the races where the
    two feeds' times of the same runner differ by more than `tol`. Pure."""
    import statistics
    by = {}
    for meet, _adiv, ndiv, _ath, ta, tn in rows:
        if ta and tn and 0 < ta < SENTINEL and 0 < tn < SENTINEL:
            by.setdefault((meet, ndiv), []).append(float(tn) - float(ta))
    out = {}
    for k, d in by.items():
        off = sum(1 for x in d if abs(x) > tol)
        if off:
            out[k] = (len(d), off, statistics.median(d))
    return out


def link(source, meet, div, sport="xc"):
    return f"https://racecast.co/race/{sport}/{meet}/{div}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="first date (default: this season's first day)")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--requeue", action="store_true",
                    help="put the athletic.net meets of the ORDER and PACE lists back in its "
                         "queue: a re-scrape rewrites their times from the source")
    a = ap.parse_args()
    since = a.since
    if not since:
        import datetime
        from season_year import academicYear, seasonStart
        since = seasonStart(academicYear(datetime.date.today())).isoformat()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '60min'")

        print(f"== 1. CAPTURE since {since}: share of rows missing each field")
        for table, sport in (("results", "XC"), ("results_tf", "TF")):
            print(f"\n  {sport}  {'feed':<14}{'rows':>11}{'no time':>9}{'not norm':>10}"
                  f"{'no grade':>10}{'no school':>11}{'no place':>10}")
            for feed, n, nt, nn, ng, ns, np_ in capture(cur, table, since):
                pct = lambda x: f"{100.0 * x / n:.1f}%" if n else "-"   # noqa: E731
                print(f"      {str(feed):<14}{n:>11,}{pct(nt):>9}{pct(nn):>10}{pct(ng):>10}"
                      f"{pct(ns):>11}{pct(np_) if sport == 'XC' else '-':>10}")
        print("\n  not norm = a finish time with no normalised time: no distance, no pool, "
              "or not yet normalised (tonight's 05 takes new rows)")

        print(f"\n== 2. ORDER: a runner placed behind another more than {ORDER_SLACK:.0f}s faster (XC)")
        cur.execute(f"""SELECT {_FEED}, r.meet_id, r.div_id, r.place, r.time_seconds
                        FROM results r
                        WHERE r.date >= %s AND r.place > 0 AND r.time_seconds > 0
                          AND r.status IS NULL""", (since,))
        rows = cur.fetchall()
        feed_of = {(m, d): f for f, m, d, _p, _t in rows}
        breaks = orderBreaks([((m, d), p, t) for _f, m, d, p, t in rows])
        per = {}
        for k in breaks:
            per[feed_of[k]] = per.get(feed_of[k], 0) + 1
        races = {}
        for k, f in feed_of.items():
            races[f] = races.get(f, 0) + 1
        for f in sorted(races):
            print(f"  {f:<14} {per.get(f, 0):>6,} of {races[f]:>7,} races")
        for (m, d), n in sorted(breaks.items(), key=lambda x: -x[1])[:a.show]:
            print(f"    {n:>4} out of order  {feed_of[(m, d)]:<13} {link(None, m, d)}")

        print(f"\n== 3. PACE: XC races whose times are not a running pace at their distance")
        cur.execute(f"""SELECT {_FEED}, r.meet_id, r.div_id,
                               COALESCE(dov.distance, m.distance), array_agg(r.time_seconds)
                        FROM results r
                        JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                                    AND m.source = r.source
                        LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
                        WHERE r.date >= %s AND r.time_seconds > 0 AND r.status IS NULL
                        GROUP BY 1, 2, 3, 4 HAVING count(*) >= 5""", (since,))
        bad, nodist = [], {}
        for f, m, d, dist, times in cur.fetchall():
            if not dist or float(dist) <= 0:
                nodist[f] = nodist.get(f, 0) + 1
                continue
            why = paceVerdict(times, float(dist))
            if why:
                bad.append((f, m, d, float(dist), why))
        for f in sorted(nodist):
            print(f"  {f:<14} {nodist[f]:>6,} races with NO distance")
        print(f"  {len(bad):,} races off pace at their stored distance")
        for f, m, d, dist, why in bad[:a.show]:
            print(f"    {f:<13} {dist:>6.0f} m  {why:<18} {link(None, m, d)}")

        if a.requeue:
            again = sorted({m for (m, d) in breaks if feed_of[(m, d)] == "athletic.net"}
                           | {m for f, m, _d, _dist, _w in bad if f == "athletic.net"})
            for m in again:
                cur.execute("""INSERT INTO meet_queue (meet_id, sport, source, scraped, priority)
                               VALUES (%s, 'XC', 'anet', 0, 1)
                               ON CONFLICT (meet_id, sport, source)
                               DO UPDATE SET scraped = 0, priority = 1""", (m,))
            conn.commit()
            print(f"\n  {len(again)} athletic.net meets back in its queue (ORDER and PACE)")
        print(f"\n== 4. TWINS: one runner, one meet, athletic.net and AthleticLIVE (XC)")
        cur.execute(f"""SELECT l.meet_id, l.div_id, n.div_id, l.athlete_id,
                               l.time_seconds, n.time_seconds
                        FROM results l
                        JOIN results n ON n.meet_id = l.meet_id AND n.athlete_id = l.athlete_id
                                      AND n.source = 'anet' AND n.result_id < {ALIVE_BASE}
                        WHERE l.source = 'anet' AND l.result_id >= {ALIVE_BASE}
                          AND l.date >= %s""", (since,))
        pairs = cur.fetchall()
        diffs = twinDiffs(pairs)
        print(f"  {len(pairs):,} runners in both feeds; {len(diffs):,} races where the times "
              f"disagree by more than {TWIN_TOL}s")
        for (m, d), (n, off, med) in sorted(diffs.items(), key=lambda x: -x[1][1])[:a.show]:
            print(f"    {off:>4}/{n:<4} off, median {med:+.1f}s  {link(None, m, d)}")
        conn.rollback()


if __name__ == "__main__":
    main()
