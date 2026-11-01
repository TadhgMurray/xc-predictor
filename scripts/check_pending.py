#!/usr/bin/env python3
"""
check_pending.py -- did the owner-run steps of 2026-10-30/31 happen?
READ-ONLY. One line per step: done / NOT DONE, and what it looked at.

    cd /srv/xc-predictor && set -a && . /etc/xc-predictor.env && set +a && /srv/venv/bin/python scripts/check_pending.py

  1. recruiting rebuild    college_recruit_meta.built_at, and college_transfer exists
  2. school_units write    school_unit has college rows; Wake Forest's college row has a region
  3. 758 TFRRS hole ids    none of the ids the DELETE named are still queued
  4. Mt. SAC rain course   meet 273894's course_name says "rain course"
  5. site restarted        xc-predictor started after the recruiting rebuild
  (diag_venue_days only prints; there is nothing for it to have changed)
"""
import datetime
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


def _one(cur, sql, args=()):
    try:
        cur.execute(sql, args)
        return cur.fetchone()
    except Exception as exc:                                        # noqa: BLE001
        cur.connection.rollback()
        return ("error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}")


def _say(ok, what, detail):
    print(f"  {'done    ' if ok else 'NOT DONE'}  {what:<22} {detail}")


def _serviceStart():
    try:
        out = subprocess.run(["systemctl", "show", "xc-predictor", "-p", "ActiveEnterTimestamp", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out or None
    except Exception:                                               # noqa: BLE001
        return None


def main():
    from database import getConn
    print("[check] owner-run steps")
    with getConn() as conn:
        cur = conn.cursor()

        built = _one(cur, "SELECT max(built_at) FROM college_recruit_meta")
        transfers = _one(cur, "SELECT to_regclass('public.college_transfer') IS NOT NULL")
        n_tr = _one(cur, "SELECT count(*) FROM college_transfer") if transfers and transfers[0] is True else (0,)
        built_at = built[0] if built and built[0] != "error" else None
        _say(bool(built_at) and bool(transfers and transfers[0] is True)
             and built_at >= datetime.datetime(2026, 10, 30, tzinfo=built_at.tzinfo),
             "recruiting rebuild",
             f"built {built_at:%Y-%m-%d %H:%M} UTC, {n_tr[0]:,} transfer rows" if built_at else f"{built}")

        units = _one(cur, "SELECT count(*) FILTER (WHERE is_college), count(*) FILTER (WHERE NOT is_college) "
                          "FROM school_unit")
        wake = _one(cur, "SELECT region, conference FROM school_unit WHERE school = 'Wake Forest' "
                         "AND is_college ORDER BY sport LIMIT 1")
        ok = bool(units and units[0] != "error" and units[0] and wake and wake[0] not in (None, "error"))
        _say(ok, "school_units write",
             f"{units[0]:,} college / {units[1]:,} school rows; Wake Forest college region "
             f"{wake[0] if wake else None!r}, conference {wake[1] if wake else None!r}"
             if units and units[0] != "error" else f"{units}")

        holes = _one(cur, """SELECT count(*) FROM meet_queue q
                             WHERE source = 'tfrrs' AND sport = 'XC' AND scraped = 0
                               AND COALESCE(priority, 0) = 0 AND meet_id BETWEEN 27518 AND 29018
                               AND meet_id <> 28447
                               AND NOT EXISTS (SELECT 1 FROM meets_tfrrs m
                                               WHERE m.meet_id = q.meet_id AND m.sport = 'XC')""")
        _say(holes and holes[0] == 0, "758 TFRRS hole ids",
             f"{holes[0]:,} of them still queued" if holes and holes[0] != "error" else f"{holes}")

        sac = _one(cur, "SELECT string_agg(DISTINCT course_name, ' | ') FROM meets WHERE meet_id = 273894")
        _say(bool(sac and sac[0] and "rain" in sac[0].lower()), "Mt. SAC rain course",
             f"meet 273894 course_name {sac[0]!r}" if sac else "meet 273894 not found")
        conn.rollback()

    started = _serviceStart()
    if started and built_at:
        try:
            st = datetime.datetime.strptime(" ".join(started.split()[1:3]), "%Y-%m-%d %H:%M:%S")
            # systemctl prints local time with a zone; compare as naive local vs built_at local
            local_built = built_at.astimezone().replace(tzinfo=None)
            _say(st >= local_built, "site restarted", f"service up since {started}")
        except ValueError:
            _say(False, "site restarted", f"could not read {started!r}")
    else:
        _say(False, "site restarted", f"service start {started!r} (run on the server)")
    print("  (diag_venue_days only prints a report; nothing to check)")


if __name__ == "__main__":
    main()
