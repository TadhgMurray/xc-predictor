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
    args = ap.parse_args()
    import conversions as C
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


if __name__ == "__main__":
    main()
