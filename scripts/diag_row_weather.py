#!/usr/bin/env python3
"""diag_row_weather.py -- what the weather correction did to ONE row.

    .venv/bin/python scripts/diag_row_weather.py --tf 123 456
    .venv/bin/python scripts/diag_row_weather.py --xc 789

Per row: the race-day weather the ERA5 grid cell gave it (the backfill's
own window and snap), the multiplier the live artifact applies, that
multiplier in rating points, and the stored normalised time and rating.
The owner (2026-09-06): "a 3:59 1500 is rated the same as a 4:01 with the
same difficulty and worse weather" -- this says how much the weather was
worth to each, so the rest of the gap can be laid on the day term
(explain_joint_row) or the rounding.

! TRACK WEATHER COMES FROM meets_tf_meta ONLY (anet, outdoor, with GPS).
  A tfrrs track row has no weather at all, and a meet with no GPS has
  none either; the tool says so per row rather than printing 0.
"""
import argparse
import math
import os
import pickle
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import normalize_distance as nd                       # noqa: E402
from event_parse import distanceFromEventShort       # noqa: E402
from database import getConn                         # noqa: E402

_GRID = 0.25
_ART = os.path.join(_ROOT, "engine", "data", "weather_correction_{sport}.pkl")


def snapCell(lat, lon):
    clat = round(round(float(lat) / _GRID) * _GRID, 2)
    clon = round((round(float(lon) / _GRID) * _GRID) % 360, 2)
    return clat, clon


def raceHours(sport):
    """(local hour window, temperature aggregate) the sport was fitted on."""
    try:
        with open(_ART.format(sport=sport), "rb") as f:
            art = pickle.load(f)
    except OSError:
        return None
    return tuple(art.get("race_local_hours", (8, 12))), nd.weatherTempAgg(art)


def cellWeather(cur, clat, clon, day, hours, temp_agg="avg(apparent_temperature)"):
    lo, hi = hours
    signed = "(CASE WHEN cell_lon > 180 THEN cell_lon - 360 ELSE cell_lon END)"
    local = f"mod(mod(hour + round({signed} / 15.0)::int, 24) + 24, 24)"
    cur.execute(f"""
        SELECT {temp_agg}, avg(wind_speed_10m),
               sum(precipitation), avg(soil_moisture),
               avg(snow_depth) + coalesce(sum(snowfall), 0), count(*)
        FROM   weather_grid
        WHERE  cell_lat = %s AND cell_lon = %s AND date = %s
          AND  {local} BETWEEN {lo} AND {hi}
    """, (clat, clon, day))
    row = cur.fetchone()
    if not row or not row[5]:
        return None
    temp, wind, precip, soil, snow, _n = row
    return {"apparent_temp": temp, "wind": wind, "precip": precip,
            "soil": soil, "snow": snow, "doy": day.timetuple().tm_yday}


def rowsTf(cur, ids):
    cur.execute("""
        SELECT r.result_id, r.source, r.meet_id, r.date::date, r.event_short,
               r.time_seconds, r.normalized_time, r.speed_rating,
               m.gps_lat, m.gps_long, m.location_id, m.is_indoor
        FROM   results_tf r
        LEFT JOIN meets_tf_meta m ON m.meet_id = r.meet_id
        WHERE  r.result_id = ANY(%s)
    """, (list(ids),))
    for (rid, src, mid, day, ev, t, nt, rating, lat, lon, loc, indoor) in cur.fetchall():
        course = None if lat is None else f"TF:loc:{loc}:out"
        note = None
        if src == "tfrrs":
            note = "tfrrs track row: the backfill never looks weather up for these"
        elif lat is None:
            note = "meet has no GPS in meets_tf_meta: no weather"
        elif indoor:
            note = "indoor: no weather by design"
        yield dict(id=rid, sport="TF", day=day, label=f"{ev} {t}s", t=t, nt=nt,
                   rating=rating, lat=lat, lon=lon, course=course, note=note,
                   dist=distanceFromEventShort(ev)[0])


def rowsXc(cur, ids):
    cur.execute("""
        SELECT r.result_id, r.source, r.meet_id, r.date::date, r.distance,
               r.time_seconds, r.normalized_time, r.speed_rating,
               m.gps_lat, m.gps_long, m.course_name
        FROM   results r
        LEFT JOIN meets m ON m.meet_id = r.meet_id
        WHERE  r.result_id = ANY(%s)
    """, (list(ids),))
    for (rid, src, mid, day, dist, t, nt, rating, lat, lon, course) in cur.fetchall():
        note = None if lat is not None else "meet has no GPS in meets: no weather"
        yield dict(id=rid, sport="XC", day=day, label=f"{dist}m {t}s", t=t, nt=nt,
                   rating=rating, lat=lat, lon=lon, course=course, note=note, dist=dist)


def report(cur, rows):
    for r in rows:
        print(f"\n[{r['sport']} {r['id']}] {r['day']}  {r['label']}  "
              f"stored norm {r['nt']}  rating {r['rating']}")
        if r["nt"] and r["t"]:
            print(f"    stored norm / time = {float(r['nt']) / float(r['t']):.5f} "
                  f"(distance, geometry, era AND weather together)")
        if r["note"]:
            print(f"    weather: none -- {r['note']}")
            continue
        got = raceHours(r["sport"])
        if got is None:
            print("    weather: no artifact for this sport -- a no-op")
            continue
        hours, temp_agg = got
        clat, clon = snapCell(r["lat"], r["lon"])
        wx = cellWeather(cur, clat, clon, r["day"], hours, temp_agg)
        if wx is None:
            print(f"    weather: cell ({clat}, {clon}) has no rows for {r['day']} "
                  f"in hours {hours} -- a no-op")
            continue
        print(f"    grid cell ({clat}, {clon}), local hours {hours[0]}-{hours[1]}, "
              f"{temp_agg.split('(')[0]} temperature: "
              f"apparent {wx['apparent_temp']:.0f}C, wind {wx['wind']:.1f}, "
              f"precip {wx['precip']:.2f}, soil {wx['soil']:.2f}, snow {wx['snow']:.2f}")
        if not nd.isRaceWeatherPlausible(wx):
            print("    weather: implausible for a race (the cell, not the course) "
                  "-- a no-op")
            continue
        factor = nd._applyWeather(1.0, wx, r["course"], r["sport"], r["dist"])
        pct = 100.0 * (1.0 / factor - 1.0) if factor else 0.0
        pts = 130.0 * (1.0 / factor - 1.0) if factor else 0.0
        print(f"    weather multiplier {factor:.5f}: the time is treated as "
              f"{pct:+.2f}% {'slower' if pct < 0 else 'faster'} than run, "
              f"about {abs(pts):.1f} rating points at 130 "
              f"{'credited' if pct > 0 else 'taken'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tf", type=int, nargs="*", default=[], help="results_tf ids")
    ap.add_argument("--xc", type=int, nargs="*", default=[], help="results ids")
    a = ap.parse_args()
    if not a.tf and not a.xc:
        ap.error("give --tf and/or --xc ids")
    with getConn() as conn, conn.cursor() as cur:
        if a.tf:
            report(cur, list(rowsTf(cur, a.tf)))
        if a.xc:
            report(cur, list(rowsXc(cur, a.xc)))


if __name__ == "__main__":
    main()
