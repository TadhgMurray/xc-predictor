#!/usr/bin/env python3
"""
diag_track_conversion.py -- why a track race converts to a different time.

    /srv/venv/bin/python scripts/diag_track_conversion.py --name "Tadhg Murray" --date 2025-04-12 --event 3200
    /srv/venv/bin/python scripts/diag_track_conversion.py --result 123456789

★ WHY (owner, 2026-09-26: a 9:01.10 3200 at Arcadia converts to 9:05.93
  over 3200 -- "the weather was bad that day, and arcadia has no difficulty
  bcs it's a track"). /conversions inverts the stored rating, and the stored
  rating carries every term the engine applied to that row. This prints
  each one for the race, as a percent of time (+ = the engine treated the
  race as SLOWER than an ordinary day on an ordinary track, so the neutral
  time is faster; - = it treated it as FAST, so the neutral time is slower):

    weather      the correction inside normalized_time (raw time vs norm,
                 after the distance factor)
    venue        the track's fitted difficulty, at the athlete's tilt
    race day     race_day_effect for (venue, date): how that day's field ran
                 against their own ratings -- weather the model does not
                 see, and anything else that made everyone fast or slow
    event, gain  the per-distance offset and the track gain the engine
                 applies at this rating

  and the neutral time with the race-day term left out, which is the number
  to compare with the page when deciding whether that term belongs in a
  conversion. READ-ONLY.
"""
import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")

from database import getConn                                  # noqa: E402


def fmt(sec):
    if sec is None:
        return "-"
    m, s = divmod(float(sec), 60)
    return f"{int(m)}:{s:05.2f}"


def pct(x):
    return "-" if x is None else f"{(math.exp(x) - 1) * 100:+.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=int)
    ap.add_argument("--name")
    ap.add_argument("--date")
    ap.add_argument("--event", help="text in event_short, e.g. 3200")
    a = ap.parse_args()
    if not a.result and not (a.name and a.date):
        ap.error("--result, or --name and --date")
    import conversions as C

    with getConn() as conn, conn.cursor() as cur:
        if a.result:
            ids = [a.result]
        else:
            first, _, last = a.name.strip().partition(" ")
            cur.execute("""
                SELECT r.result_id FROM results_tf r
                JOIN   athletes x ON x.athlete_id = r.athlete_id
                WHERE  lower(btrim(x.first_name)) = lower(%s)
                  AND  lower(btrim(x.last_name))  = lower(%s)
                  AND  r.date = %s
                  AND  (%s::text IS NULL OR r.event_short ILIKE '%%' || %s || '%%')
            """, (first, last.strip(), a.date, a.event, a.event))
            ids = [r[0] for r in cur.fetchall()]
        if not ids:
            print("no such result")
            return
        for rid in ids:
            cur.execute("""
                SELECT r.result_id, r.person_id, r.date, r.event_short,
                       r.time_seconds, r.normalized_time, r.speed_rating,
                       split_part(r.rating_pool, '|', 1), m.meet_name,
                       m.location_id, COALESCE(m.is_indoor, 0),
                       m.distance_meters, r.meet_id, r.source
                FROM   results_tf r
                LEFT   JOIN meets_tf m ON m.meet_id = r.meet_id
                      AND m.div_id = r.div_id AND m.event_id = r.event_id
                WHERE  r.result_id = %s""", (rid,))
            row = cur.fetchone()
            if not row:
                continue
            (rid, pid, date, ev, t, norm, rating, pool, meet, loc, indoor,
             dist, meet_id, source) = row
            cell = f"TF:loc:{loc}:{'in' if indoor else 'out'}" if loc else None
            delta = u = None
            if cell:
                cur.execute("SELECT difficulty FROM course_difficulties "
                            "WHERE course_name = %s LIMIT 1", (cell,))
                got = cur.fetchone()
                delta = float(got[0]) if got and got[0] is not None else None
                cur.execute("SELECT to_regclass('public.race_day_effect')")
                if cur.fetchone()[0]:
                    cur.execute("SELECT day_effect, n_rows FROM race_day_effect "
                                "WHERE course_name = %s AND race_date::text = %s",
                                (cell, str(date)))
                    got = cur.fetchone()
                    u = float(got[0]) if got and got[0] is not None else None
                    u_n = got[1] if got else None
            weather = None
            try:
                cur.execute("SELECT hour, temp_c, wind_speed_kmh, humidity, "
                            "precipitation_mm FROM weather WHERE meet_id = %s "
                            "AND source = %s ORDER BY hour", (meet_id, source))
                weather = cur.fetchall()
            except Exception:                           # noqa: BLE001
                conn.rollback()

            print(f"\nresult {rid}  {date}  {meet}  {ev}  {fmt(t)}  "
                  f"(person {pid}, pool {pool}, venue {cell})")
            print(f"  stored rating {rating}   normalized_time {norm}")
            if not (t and norm and pool and dist):
                print("  (missing time, norm, pool or distance -- nothing to take apart)")
                continue
            factor = C._forward_factor(float(dist), pool, None, None, None,
                                       "TF", ev)
            w = math.log(float(t) * factor / float(norm)) if factor else None
            sc = C.engineScale(pool, "TF")
            tilt = C._tilt(float(rating)) if rating else None
            shift = sc[2] if sc else 0.0
            venue = (tilt * (math.log1p(delta) + shift)
                     if tilt is not None and delta is not None else None)
            off = C.distance_offset(pool, "TF", float(dist), rating=rating)
            gain = C.sport_gain(pool, "TF", rating)
            print(f"  weather   {pct(w):>8}   (time x distance factor / norm)")
            print(f"  venue     {pct(venue):>8}   (difficulty {delta}, tilt {tilt}, "
                  f"track anchor shift {shift:+.4f})")
            print(f"  race day  {pct(u):>8}   (race_day_effect, "
                  f"{u_n if u is not None else 0} rows that day)")
            print(f"  event     {pct(off):>8}   gain {pct(gain)}")
            if weather:
                print("  weather rows (hour, temp C, wind km/h, humidity, precip mm):")
                for wr in weather[:24]:
                    print(f"    {wr}")
            n2 = C._norm_from_result(rid, "TF")
            same = C.normalized_to_time(n2, {"distance": float(dist),
                                             "pool": pool, "sport": "TF"})
            print(f"  the page's neutral {ev}: {fmt(same)}  (ran {fmt(t)})")
            if u is not None and same:
                print(f"  without the race-day term: {fmt(same * math.exp(u))}")


if __name__ == "__main__":
    main()
