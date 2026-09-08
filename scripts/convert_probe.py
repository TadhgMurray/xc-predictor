#!/usr/bin/env python3
"""convert_probe.py -- every term of one result's conversion, on one screen
(issue 302: a stored 9:01.10 3200 came back as a 9:28 3200).

    /srv/venv/bin/python scripts/convert_probe.py --tf <result_id>
    /srv/venv/bin/python scripts/convert_probe.py --tf --person <id> --seconds 541.1
"""
import argparse
import math
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
sys.path.insert(0, "engine")
from database import getConn                        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", nargs="?", const=True, default=None,
                    help="a results_tf result_id, or bare --tf with --person and --seconds")
    ap.add_argument("--person", type=int)
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--target", type=float, default=3200.0)
    ap.add_argument("--sample", type=int, default=0,
                    help="instead: N random 2025 track rows of --pool, the gap between "
                         "the rating path and the time path by rating band")
    ap.add_argument("--pool", default="hs_m")
    args = ap.parse_args()
    import conversions as C
    if args.sample:
        return sample(args, C)
    with getConn() as conn, conn.cursor() as cur:
        if args.tf is True:
            cur.execute("""SELECT result_id FROM results_tf WHERE person_id = %s
                           AND abs(time_seconds - %s) < 0.6 ORDER BY date DESC LIMIT 1""",
                        (args.person, args.seconds))
            row = cur.fetchone()
            if not row:
                print("no such row"); return
            rid = int(row[0])
        else:
            rid = int(args.tf)
        cur.execute("""SELECT r.time_seconds, r.normalized_time, r.speed_rating, r.rating_pool, r.date,
                              r.event_short, m.distance_meters, m.meet_name, m.location_id, m.is_indoor,
                              rr.pool
                       FROM results_tf r
                       LEFT JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id AND m.event_id = r.event_id
                       LEFT JOIN ranking_results rr ON rr.result_id = r.result_id AND rr.sport = 'TF'
                       WHERE r.result_id = %s""", (rid,))
        row = cur.fetchone()
    if not row:
        print("no such row"); return
    t, nt, rating, rpool, date, ev, dist, meet, loc, indoor, pool2 = row
    pool = rpool or pool2 or "hs_m"
    print(f"== result {rid}: {meet} {date} {ev} {dist}m  time {t:.2f}s  stored normalized {nt}  rating {rating}  pool {pool}")
    sc = C.engineScale(pool, "TF")
    pm = C.pool_mean(pool, "TF")
    print(f"engine_scale (pool_mean, median venue effect, anchor shift): {sc}   pool_mean used: {pm}")
    venue = C.venue_difficulty("TF", location_id=loc, distance_meters=dist, is_indoor=bool(indoor))
    print(f"venue difficulty for this row's track: {venue}   default for a track: {C.default_difficulty('TF')}")
    # the three terms of the effect, at this rating, for the source and for a typical track
    r = float(rating) if rating else None
    for label, d in (("source venue", venue), ("typical track", None)):
        base = (C._tilt(r) * (math.log1p(float(d)) + sc[2])) if (d is not None and sc) else (sc[1] if sc else None)
        off = C.distance_offset(pool, "TF", args.target, rating=r)
        gain = C.sport_gain(pool, "TF", r)
        print(f"effect at {label}: venue {base:+.4f}  distance offset({int(args.target)}m) {off:+.4f}  "
              f"sport gain {gain:+.4f}  total {base + off + gain:+.4f}")
    adj = C._norm_from_result(rid, "TF")
    print(f"norm from the stored rating (100 * pool_mean / rating): {adj:.2f}s")
    fwd = C._norm_from_time(float(t), float(dist), pool, sport="TF",
                            chosen=venue, event_short=ev)
    print(f"norm from the raw time forward through the same terms:  {fwd:.2f}s   (gap {100 * (adj / fwd - 1):+.2f}%)")
    factor = C._forward_factor(args.target, pool, None, None, None, "TF", None)
    print(f"normalisation factor for {int(args.target)}m: {factor}")
    for label, ctx in (("typical track", {"distance": args.target, "pool": pool, "sport": "TF"}),
                       ("this row's track", {"distance": args.target, "pool": pool, "sport": "TF",
                                             "difficulty": venue})):
        for src_label, n in (("rating path", adj), ("time path", fwd)):
            tt = C.normalized_to_time(n, ctx)
            print(f"{int(args.target)}m at {label:16} from the {src_label:11}: {tt / 60:.0f}:{tt % 60:05.2f}")


def sample(args, C):
    """Across random rated 2025 track rows: ln(adjusted from the stored
    rating / adjusted from the raw time through venue, distance and
    sport gain). Zero means the stored rating carries every term; a gap
    equal to the sport gain means the go-live never applied the shift."""
    import statistics
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT r.result_id, r.time_seconds, r.normalized_time, r.speed_rating,
                              r.event_short, m.distance_meters, m.location_id, m.is_indoor
                       FROM results_tf r
                       JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id AND m.event_id = r.event_id
                       WHERE r.date >= '2025-01-01' AND r.speed_rating > 0 AND r.normalized_time > 0
                         AND (r.rating_pool = %s OR r.rating_pool LIKE %s)
                         AND m.distance_meters BETWEEN 800 AND 10000
                         AND COALESCE(r.is_field, 0) = 0
                       ORDER BY random() LIMIT %s""", (args.pool, args.pool + "|%", args.sample))
        rows = cur.fetchall()
        # ! THE BARE POOL NAME IS THE FILL'S SPELLING; the go-live writes the
        #   sport inside it. The first cut of this sampled the bare name and
        #   so sampled only filled rows (2026-09-08). Both are shown apart.
        cur.execute("""SELECT r.rating_pool, count(*) FROM results_tf r
                       WHERE r.date >= '2025-01-01' AND r.speed_rating > 0
                         AND (r.rating_pool = %s OR r.rating_pool LIKE %s) GROUP BY 1""",
                    (args.pool, args.pool + "|%"))
        print("== rows by pool spelling since 2025: " + "; ".join(f"{p} {n:,}" for p, n in cur.fetchall()))
        cur.execute("""SELECT r.result_id, r.rating_pool FROM results_tf r WHERE r.result_id = ANY(%s)""",
                    ([r[0] for r in rows],))
        spelled = dict(cur.fetchall())
    bands = {}
    for rid, t, nt, rating, ev, dist, loc, indoor in rows:
        filled = "|" not in (spelled.get(rid) or "")
        try:
            venue = C.venue_difficulty("TF", location_id=loc, distance_meters=dist, is_indoor=bool(indoor))
            adj = C._norm_from_rating(float(rating), args.pool, 0.0, "TF")
            fwd = C._norm_from_time(float(t), float(dist), args.pool, sport="TF", chosen=venue, event_short=ev)
            gain = C.sport_gain(args.pool, "TF", float(rating))
        except Exception:                             # noqa: BLE001
            continue
        if not adj or not fwd:
            continue
        b = ("filled" if filled else "solved", int(float(rating) // 10 * 10))
        bands.setdefault(b, []).append((math.log(adj / fwd), gain, venue is not None))
    print(f"== {len(rows)} random 2025 {args.pool} track rows: ln(rating path / time path), by rating band")
    print("   zero = the stored rating carries every term; +sport gain = the shift was never applied")
    for b in sorted(bands):
        v = bands[b]
        kind, b = b
        print(f"  {kind:6}", end="")
        gaps = [x[0] for x in v]
        print(f"  {b}-{b + 10}: n {len(v):4d}  median gap {100 * statistics.median(gaps):+.2f}%  "
              f"(p25 {100 * sorted(gaps)[len(gaps) // 4]:+.2f}, p75 {100 * sorted(gaps)[3 * len(gaps) // 4]:+.2f})  "
              f"sport gain here {100 * statistics.median(x[1] for x in v):+.2f}%  "
              f"venue known {100 * sum(x[2] for x in v) / len(v):.0f}%")


if __name__ == "__main__":
    main()
