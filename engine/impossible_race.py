#!/usr/bin/env python3
"""
impossible_race.py -- the races no runner could have run. Pipeline step 06c.

    python engine/impossible_race.py --write      # build and swap the tables
    python engine/impossible_race.py              # count and list, no writes

★ THE OWNER'S RULE (2026-09-14): a time faster than the world record allows
  is a wrong distance or a wrong time, never a performance -- and it is
  the RACE that is wrong, not the one row that happened to be fast enough
  to show it. So one impossible row condemns the whole race: every row of
  that (source, meet, division[, event]) is written to `impossible_result`
  and from then on

    the pack   leaves those rows out of the solve (speed_ratings_db),
    the fill   never prices them (fill_ratings, via the board query),
    the boards never rank them (build_ranking_results._SQL),

  so a race that beat the record has no ratings at all, and its athletes'
  pages show a dash where a 190 used to sit. College pools are exempt
  ("every but college"): a college race carries professionals and
  near-record fields, and the anchor gate handles its scale problems.
  A row faster than the FLOOR but not the open record is a mis-pooled row,
  not a wrong race: that row alone is written, and its race keeps its
  ratings (see judge).

★ AND THE FLOOR IS ONE SCALE FOR EVERYBODY (owner, 2026-09-16: "undo all
  the indiv rows overrides, and then redo them so they don't catch college
  ahtlets -- do it by hs-equivalent scale not own pool scale"). It used to
  be the row's own pool's floor, read off the rating_pool the last go-live
  wrote, which made the test depend on the pooling -- and the rows it
  catches are the ones the pooling got wrong. See record_pace.poolFactor
  for the ratchet that produced, and why the two questions are separate.

★ THE UNDO IS THE RERUN. Both tables are built as _new and swapped, so
  every verdict is recomputed from the current data and the current rules
  on every run -- there is no accumulated state to unwind. Nothing else
  persists a condemnation: the pack, the fill and the boards all read the
  table live, and normalized_time is never cleared. So one `--write` with
  the new floor both undoes the old row list and writes the new one, and
  the line it prints says how many rows came back.

HOW A ROW'S POOL IS KNOWN HERE. This runs before the pack, so the pool is
the rating_pool the last go-live wrote on the row; a row that has none yet
(a first run, a row the solve never saw) counts as college only when it
came from tfrrs, the college feed. The pool now decides ONLY the exemption
-- never how hard the floor is. The distance is the loader's own
(dist_override, then the meet's, then the tfrrs division blob for XC; the
event's metres for TF), the sex the athlete's.

CHEAP BY CONSTRUCTION. No record pace is slower than SLOWEST_RECORD_PACE
(the men's 10k at 157 s/km... the women's at 173), so SQL keeps only rows
under PREFILTER_PACE s/km -- a few thousand out of 61.6M -- and Python
judges those by sex and distance. The races' full fields are then read by
key. Two tables, built as _new and swapped under a lock timeout
(dbfast.swapTable), so the site never waits on this step:

    impossible_race   (sport, source, meet_id, div_id, event_id, n_rows,
                       n_impossible, fastest_pace, record_pace,
                       sample_result_id, sample_time, sample_distance)
    impossible_result (sport, result_id)      -- every row of those races
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                    # noqa: E402
from record_pace import (SLOWEST_RECORD_PACE, PACE_FLOOR_SLACK,  # noqa: E402
                         POOL_PACE_FACTOR, exemptPool, impossiblePace,
                         ownPoolFactor, poolFactor, recordPace)

# SQL keeps every row faster than this (s/km); a margin over the slowest
# record pace so a rounding in the SQL arithmetic cannot lose a candidate.
# ... times the widest pool factor (record_pace.POOL_PACE_FACTOR: an
# elementary pool's floor sits 35% outside the open record)
PREFILTER_PACE = float(int(SLOWEST_RECORD_PACE * max(POOL_PACE_FACTOR.values())) + 10)

# ★ RATED DISTANCE ROWS ONLY, AT A DISTANCE A RACE CAN BE (run 23,
#   2026-09-14: the first cut judged every timed row and condemned 5.8M
#   track rows -- the 55m, 60m and 75m dashes, whose event names the
#   loader's parser reads as kilometres ("75" < 100 -> 75,000 m), a 7 s
#   "75 km" at 0 s/km; and 4,828,032 m cross country "5ks", a distance
#   stored in the wrong unit). The rule is about TIMES: a row the backfill
#   normalised (a distance event the engine rates) whose distance is one
#   a race is run over. A wrong unit on the distance is the ballooned-
#   distance census's business and the pace band's, not this rule's.
XC_DIST = (1000, 20000)
TF_DIST = (800, 15000)

_DDL = """
CREATE TABLE {race} (
    sport            text    NOT NULL,
    source           text,
    meet_id          bigint,
    div_id           bigint,
    event_id         bigint,
    n_rows           int     NOT NULL,
    n_impossible     int     NOT NULL,
    fastest_pace     real,
    record_pace      real,
    sample_result_id bigint,
    sample_time      real,
    sample_distance  real,
    -- 'record': a row beat the open world record. 'own_races': the field
    -- ran far faster than its own runners' other races that month
    -- (own_race_shift.py) -- the wrong distance a record cannot catch.
    reason           text,
    shift            real,
    implied_distance real
);
CREATE TABLE {result} (
    sport     text   NOT NULL,
    result_id bigint NOT NULL,
    PRIMARY KEY (sport, result_id)
);
"""


def _hasColumn(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s AND column_name = %s""",
                (table, col))
    return cur.fetchone() is not None


def raceKey(sport, row):
    """(source, meet_id, div_id, event_id): event_id only means something
    on the track. Pure."""
    return (row["source"], row["meet_id"], row["div_id"],
            row["event_id"] if sport == "TF" else None)


def poolIsCollege(rating_pool, source):
    """The exemption, as this step can know it: the pool the last go-live
    wrote, else the feed (tfrrs is the college feed). Pure."""
    if rating_pool:
        return exemptPool(rating_pool)
    return (source or "") == "tfrrs"


def judge(sport, rows):
    """({race key: [(result_id, time, dist, pace, record)]} of the rows that
    beat the OPEN record, [(result_id, race key, time, dist, pace, floor)]
    of the rows that only beat their POOL's floor) in a pool the rule
    covers. rows: dicts with source, meet_id, div_id, event_id, result_id,
    time_seconds, distance, gender, rating_pool. Pure.

    ★ TWO VERDICTS, NOT ONE (2026-09-15, the first write: 2,645 track
      races condemned, most of them open 1500s run in 3:54 by adults the
      club pooling had filed as elementary schoolers). A time faster than
      the open world record is a fact about the RACE -- its distance or
      its clock is wrong -- so the whole race goes. A time faster than the
      floor but not the record is a fact about the ROW. Dropping that row
      alone keeps the race for everyone who belongs in it.

    ⚠ AND THE FLOOR IS THE SAME FOR EVERY POOL NOW (poolFactor). Those
      2,645 races are the argument for it: the 3:54 1500s were real races
      by real adults, condemned because the pooling had called the adults
      elementary schoolers. The race was never wrong. Judged on the
      hs-equivalent scale a 3:54 1500 is simply a 3:54 1500, and the
      mis-pooling is pool_resolve's to fix -- with the row still there to
      fix it from."""
    races, rows_out = {}, []
    for r in rows:
        if poolIsCollege(r.get("rating_pool"), r.get("source")):
            continue
        t, d = r.get("time_seconds"), r.get("distance")
        factor = poolFactor(r.get("rating_pool"))
        if not impossiblePace(t, d, r.get("gender"), slack=PACE_FLOOR_SLACK * factor):
            continue
        pace = float(t) / (float(d) / 1000.0)
        key = raceKey(sport, r)
        if impossiblePace(t, d, r.get("gender")):                       # the open record
            races.setdefault(key, []).append(
                (r["result_id"], float(t), float(d), pace, recordPace(d, r.get("gender"))))
        else:                                                           # the pool's floor only
            rows_out.append((r["result_id"], key, float(t), float(d), pace,
                             recordPace(d, r.get("gender")) * factor))
    return races, rows_out


def ownFloorOnly(sport, rows):
    """The rows the OLD rule would have deleted and this one keeps: faster
    than their own pool's floor, slower than the hs-equivalent one, and
    not record-beating. Report only -- nothing reads it to judge a row.
    Pure; same row dicts as judge."""
    out = []
    for r in rows:
        if poolIsCollege(r.get("rating_pool"), r.get("source")):
            continue
        t, d, g = r.get("time_seconds"), r.get("distance"), r.get("gender")
        own = ownPoolFactor(r.get("rating_pool"))
        if own <= poolFactor(r.get("rating_pool")):
            continue                              # nothing changed for this pool
        if not impossiblePace(t, d, g, slack=PACE_FLOOR_SLACK * own):
            continue                              # the old rule did not catch it either
        if impossiblePace(t, d, g, slack=PACE_FLOOR_SLACK * poolFactor(r.get("rating_pool"))):
            continue                              # the new rule still catches it
        out.append((r["result_id"], r.get("rating_pool"), float(t), float(d)))
    return out


def candidateSql(cur, sport):
    """Rows under PREFILTER_PACE s/km with the loader's distance, the
    athlete's sex and the row's rating pool."""
    from speed_ratings_db import _eventMetersSql
    table = "results" if sport == "XC" else "results_tf"
    rp = "r.rating_pool" if _hasColumn(cur, table, "rating_pool") else "NULL::text"
    gender = """(SELECT a.gender FROM athletes a
                 WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                   AND a.gender IN ('M', 'F')
                 GROUP BY a.gender ORDER BY count(*) DESC, a.gender DESC LIMIT 1)"""
    if sport == "XC":
        dist = """COALESCE(dov.distance, m.distance,
                           (mt.division_distances -> r.div_id::text ->> 'distance')::real,
                           mt.distance)::real"""
        return f"""
            SELECT r.result_id, r.source, r.meet_id, r.div_id, NULL::bigint AS event_id,
                   r.time_seconds::real AS time_seconds, d.distance, {rp} AS rating_pool,
                   {gender} AS gender
            FROM   results r
            LEFT JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
            LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id AND mt.sport = 'XC'
            LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
            CROSS JOIN LATERAL (SELECT {dist} AS distance) d
            WHERE  r.normalized_time IS NOT NULL
              AND  r.time_seconds > 0 AND d.distance BETWEEN {XC_DIST[0]} AND {XC_DIST[1]}
              AND  r.time_seconds / (d.distance / 1000.0) < {PREFILTER_PACE}"""
    dist = f"COALESCE(m.distance_meters::real, {_eventMetersSql('r')})"
    return f"""
        SELECT r.result_id, r.source, r.meet_id, r.div_id, r.event_id,
               r.time_seconds::real AS time_seconds, d.distance, {rp} AS rating_pool,
               {gender} AS gender
        FROM   results_tf r
        LEFT JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                            AND m.event_id = r.event_id AND m.source = r.source
        CROSS JOIN LATERAL (SELECT {dist} AS distance) d
        WHERE  r.normalized_time IS NOT NULL
          AND  r.time_seconds > 0 AND COALESCE(r.is_relay, 0) = 0 AND COALESCE(r.is_field, 0) = 0
          AND  d.distance BETWEEN {TF_DIST[0]} AND {TF_DIST[1]}
          AND  r.time_seconds / (d.distance / 1000.0) < {PREFILTER_PACE}"""


def loadCandidates(cur, sport):
    cur.execute(candidateSql(cur, sport))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def raceRows(cur, sport, key):
    """Every result_id of the race."""
    source, meet_id, div_id, event_id = key
    if sport == "XC":
        cur.execute("""SELECT result_id FROM results
                       WHERE source IS NOT DISTINCT FROM %s AND meet_id = %s AND div_id = %s""",
                    (source, meet_id, div_id))
    else:
        cur.execute("""SELECT result_id FROM results_tf
                       WHERE source IS NOT DISTINCT FROM %s AND meet_id = %s AND div_id = %s
                         AND event_id IS NOT DISTINCT FROM %s""",
                    (source, meet_id, div_id, event_id))
    return [r[0] for r in cur.fetchall()]


def ownRaceShift(conn, cur, since, already, race_recs, res_recs, show, explain=()):
    """The second rule: XC races whose runners all ran far faster than in
    their own other races (own_race_shift.py). Appends to the same lists
    the record rule fills, so the pack, the fill and the boards leave them
    out exactly as they do a record-beating race. Returns (races, rows)."""
    import math as _m
    import own_race_shift as ors
    t0 = time.time()
    stats, fast, slow, info = ors.findXc(conn, since)
    if "centre" not in stats:
        print(f"  XC own-races: {stats.get('note')} -- nothing condemned")
        return 0, 0
    print(f"  XC own-races since {since}: {stats['rows']:,} rows, {stats['races']:,} races "
          f"with {ors.MIN_LINKED}+ linked runners; races vary by "
          f"{100 * stats['s_between']:.1f}% (runners inside one by "
          f"{100 * stats['s_within']:.1f}%); cut at {stats['z_cut']:.2f} sd "
          f"(5% chance of any false call among them) -- a 3-runner race needs to be "
          f"{100 * (1 - stats['ratio_at_3']):.0f}% faster than its runners elsewhere  "
          f"({time.time() - t0:.1f}s)")
    for want in explain:
        meet, _, div = str(want).strip("/").rpartition("/")
        hits = [r for r, v in info.items() if str(v[1]) == meet.split("/")[-1] and str(v[2]) == div]
        for r in hits or [None]:
            if r is None:
                print(f"      --race {want}: no XC rows with a normalized time since {since}")
                continue
            v = stats["every"].get(r)
            src, m_, d_, date, dist = info[r]
            if v is None:
                print(f"      --race {want} ({src}, {date}): fewer than {ors.MIN_LINKED} runners "
                      f"have another race within +-{ors.WINDOW} days -- the rule cannot judge it")
                continue
            n, m, z = v
            print(f"      --race {want} ({src}, {date}): {n} linked runners, "
                  f"{100 * (_m.exp(m - stats['centre']) - 1):+.1f}% vs their own races, "
                  f"{z:.1f} sd (condemned below -{stats['z_cut']:.2f}); stored "
                  f"{(dist or 0):.0f} m, looks like {ors.impliedDistance(dist, m, stats['centre'])} m")
    n_races = n_rows = 0
    for rid, n, m, z in fast:
        src, meet, div, date, dist = info[rid]
        key = (src, meet, div, None)
        if key in already:
            continue
        ids = raceRows(cur, "XC", key)
        implied = ors.impliedDistance(dist, m, stats["centre"])
        race_recs.append(("XC", src, meet, div, None, len(ids), int(n), None, None, None, None,
                          None if dist is None else float(dist), "own_races", float(m),
                          None if implied is None else float(implied)))
        res_recs.extend(("XC", rid_) for rid_ in ids)
        n_races += 1; n_rows += len(ids)
    print(f"  XC own-races: {n_races:,} races condemned ({n_rows:,} rows) -- listed fastest first:")
    for rid, n, m, z in fast[:show]:
        src, meet, div, date, dist = info[rid]
        print(f"      /race/xc/{meet}/{div}  {date}  {n} linked runners  "
              f"{100 * (_m.exp(m - stats['centre']) - 1):+.0f}% vs their own races  "
              f"({z:.1f} sd)  stored {(dist or 0):.0f} m, looks like "
              f"{ors.impliedDistance(dist, m, stats['centre'])} m")
    print(f"  XC own-races, SLOW side (not condemned -- the course difficulty absorbs "
          f"these when the race has voters; check the distance by hand):")
    for rid, n, m, z in slow[:min(show, 15)]:
        src, meet, div, date, dist = info[rid]
        print(f"      /race/xc/{meet}/{div}  {date}  {n} linked  "
              f"{100 * (_m.exp(m - stats['centre']) - 1):+.0f}%  ({z:.1f} sd)  stored "
              f"{(dist or 0):.0f} m, looks like {ors.impliedDistance(dist, m, stats['centre'])} m")
    return n_races, n_rows


def build(conn, write, show=20, own_since=None, explain=()):
    from dbfast import swapTable
    race_t, res_t = "impossible_race", "impossible_result"
    total_races = total_rows = 0
    race_recs, res_recs = [], []
    with conn.cursor() as cur:
        for sport in ("XC", "TF"):
            t0 = time.time()
            cands = loadCandidates(cur, sport)
            bad, floor_rows = judge(sport, cands)
            kept = ownFloorOnly(sport, cands)
            n_rows = 0
            for key, hits in sorted(bad.items(), key=lambda kv: min(h[3] / h[4] for h in kv[1])):
                ids = raceRows(cur, sport, key)
                n_rows += len(ids)
                worst = min(hits, key=lambda h: h[3] / h[4])
                race_recs.append((sport, key[0], key[1], key[2], key[3], len(ids), len(hits),
                                  worst[3], worst[4], worst[0], worst[1], worst[2],
                                  "record", None, None))
                res_recs.extend((sport, rid) for rid in ids)
            # the pool-floor rows, one by one: the row leaves, the race stays
            res_recs.extend((sport, rid) for rid, _k, _t, _d, _p, _f in floor_rows)
            print(f"  {sport}: {len(cands):,} rows under {PREFILTER_PACE:.0f} s/km; "
                  f"{sum(len(h) for h in bad.values()):,} beat the OPEN record -> {len(bad):,} races, "
                  f"{n_rows:,} rows condemned; {len(floor_rows):,} beat only their pool's floor "
                  f"-> those rows alone  ({time.time() - t0:.1f}s)")
            print(f"  {sport}: {len(kept):,} rows KEPT that their own pool's floor would have "
                  f"deleted (the hs-equivalent scale; record_pace.poolFactor)")
            for key, hits in list(sorted(bad.items(), key=lambda kv: min(h[3] / h[4] for h in kv[1])))[:show]:
                worst = min(hits, key=lambda h: h[3] / h[4])
                print(f"      {sport} source={key[0]} meet={key[1]} div={key[2]}"
                      + (f" event={key[3]}" if sport == "TF" else "")
                      + f"  {len(hits)} impossible; fastest {worst[3]:.0f} s/km over {worst[2]:.0f} m"
                        f" (record {worst[4]:.0f}) result {worst[0]}")
            for rid, key, t, d, pace, floor in sorted(floor_rows, key=lambda x: x[4] / x[5])[:min(show, 8)]:
                print(f"      {sport} row {rid}: {pace:.0f} s/km over {d:.0f} m against a pool floor of "
                      f"{floor:.0f} (meet={key[1]} div={key[2]}{'' if sport == 'XC' else ' event=' + str(key[3])})")
            total_races += len(bad); total_rows += n_rows + len(floor_rows)
            if sport == "XC" and own_since:
                r_, n_ = ownRaceShift(conn, cur, own_since, set(bad), race_recs, res_recs, show, explain)
                total_races += r_; total_rows += n_
        if not write:
            print(f"  DRY RUN: {total_races:,} races, {total_rows:,} rows would be written")
            return total_races, total_rows
        cur.execute(f"DROP TABLE IF EXISTS {race_t}_new, {res_t}_new")
        cur.execute(_DDL.format(race=f"{race_t}_new", result=f"{res_t}_new"))
        if race_recs:
            # ! execute_values, NOT executemany: executemany is one round trip
            #   per row, and a condemned race list runs to tens of thousands
            from psycopg2.extras import execute_values
            execute_values(cur, f"INSERT INTO {race_t}_new VALUES %s", race_recs,
                           page_size=5000)
            execute_values(cur, f"INSERT INTO {res_t}_new VALUES %s ON CONFLICT DO NOTHING",
                           res_recs, page_size=10000)
        conn.commit()
    swapTable(conn, race_t)
    swapTable(conn, res_t, renames=((f"{res_t}_new_pkey", f"{res_t}_pkey"),))
    print(f"  wrote {race_t} ({total_races:,} races) and {res_t} ({total_rows:,} rows)")
    return total_races, total_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="build and swap the tables")
    ap.add_argument("--show", type=int, default=20, help="races listed per sport")
    ap.add_argument("--own-since", default=None,
                    help="first date the own-races rule judges (default: 400 days ago; "
                         "'off' to skip the rule)")
    ap.add_argument("--race", action="append", default=[],
                    help="meet/div (as in /race/xc/<meet>/<div>): print where this race "
                         "lands under the own-races rule")
    args = ap.parse_args()
    import datetime as _dt
    own = args.own_since or os.environ.get("XCP_OWN_SHIFT_SINCE") or \
        (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
    own = None if str(own).lower() in ("off", "none", "0") else own
    print(f"[impossible] prefilter {PREFILTER_PACE:.0f} s/km; college pools exempt; "
          f"{'writing' if args.write else 'DRY RUN'}")
    with getConn() as conn:
        build(conn, args.write, args.show, own, args.race)
    return 0


if __name__ == "__main__":
    sys.exit(main())
