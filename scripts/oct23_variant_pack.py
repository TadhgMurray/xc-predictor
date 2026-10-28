#!/usr/bin/env python3
"""
oct23_variant_pack.py -- the pack as it would be WITHOUT the tfrrs venue
placement of 2026-10-22 (2b2a4cf, e97f4b7, b1b86fe, and the migration's
5c), everything else identical. READ-ONLY on the database; writes one file.

    cd /srv/ss-review && set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/oct23_variant_pack.py --repo /srv/xc-predictor \\
        --out /srv/oct23/pack_noplace.npz

★ WHY (owner, 2026-10-24: "settle it (the tfrrs venue matching change
  versus the tilt setting) before anything new goes live"). The run of
  20261023_171317 changed two things at once against the frozen Oct 19
  ratings it was judged by: XCP_TILT_SCALE=hs, and college XC meets placed
  on venues for the first time. This file is the second change undone on
  today's data, so run_joint can be run on both packs with both tilts
  (scripts/oct23_arm.sh) and each change read off on its own.

★ WHAT THE PLACEMENT CHANGED IN THE PACK, AND ONLY THAT IS UNDONE.
  1. The course key. _xcQuery keys an XC row by its canonical course,
     found through course_canonical on (name, gps); a tfrrs meet with no
     gps had none and keyed by 'name:<venue_name>'. A placed meet's rows
     whose key is a canonical id go back to 'XC:name:<venue>:d<dist>'.
     A championship key ('champ:...') does not depend on the place and
     stays. A row whose gps came from an athletic.net meet of the same
     div (COALESCE(m.gps_lat, mt.gps_lat)) stays.
  2. The weather. backfill_normalize looks the race's weather up by the
     meet's gps (WeatherIndex, _loadMeetCellsTfrrsXC); a meet with none
     took none. Each placed row is normalised again both ways
     (normalize_distance.normalizeResult, with and without the day's
     weather) and compared with its pack value; if the pack carries the
     weather (the usual case once a backfill ran after the placement) it
     comes off, as the ratio of the two.
  Which meets were placed: those with gps now and none in the migration's
  dump of OLD (/srv/migrate/db, pg_dump -Fd, taken before 5c), or absent
  from it. Without the dump, --all-tfrrs takes every tfrrs XC meet with
  gps (before 2b2a4cf the pipeline never placed one; the hand runs of
  backfill_tfrrs_location before it did, so this over-reverts a little).

! THE PACK'S MTIME IS COPIED. run_joint dates the pack by its mtime (the
  rows' days-ago are counted from it), so the variant keeps the original's.
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
        if a.startswith("--repo="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.abspath(os.path.join(HERE, ".."))


REPO = _repo()
for _p in (os.path.join(REPO, "backfill"), os.path.join(REPO, "scripts"),
           os.path.join(REPO, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def dumpGps(dump):
    """{meet_id: has gps} for the XC meets of meets_tfrrs in a pg_dump -Fd
    directory, read through pg_restore's COPY text (no database touched)."""
    p = subprocess.run(["pg_restore", "--data-only", "--table=meets_tfrrs", "-f", "-", dump],
                       capture_output=True, text=True, check=True)
    out, cols = {}, None
    for line in p.stdout.splitlines():
        if cols is None:
            if line.startswith("COPY ") and "meets_tfrrs" in line:
                cols = [c.strip().strip('"') for c in
                        line[line.index("(") + 1:line.index(")")].split(",")]
                i_m, i_s, i_lat = cols.index("meet_id"), cols.index("sport"), cols.index("gps_lat")
            continue
        if line == "\\.":
            break
        f = line.split("\t")
        if f[i_s] != "XC":
            continue
        lat = f[i_lat]
        out[int(f[i_m])] = lat not in ("\\N", "", "0", "0.0")
    if cols is None:
        raise SystemExit(f"[variant] no meets_tfrrs data in {dump}")
    return out


def placedMeets(cur, dump, all_tfrrs):
    cur.execute("""SELECT meet_id FROM meets_tfrrs
                   WHERE sport = 'XC' AND gps_lat IS NOT NULL AND gps_lat <> 0""")
    now = {int(m) for (m,) in cur.fetchall()}
    if all_tfrrs:
        print(f"[variant] --all-tfrrs: {len(now):,} tfrrs XC meets with gps, all taken as placed")
        return now
    old = dumpGps(dump)
    had = {m for m, g in old.items() if g}
    placed = now - had
    print(f"[variant] tfrrs XC meets: {len(old):,} in the dump ({len(had):,} with gps); "
          f"{len(now):,} with gps now; placed since: {len(placed):,} "
          f"({len(placed - set(old)):,} of them new since the dump)")
    return placed


def placedRows(cur, meets):
    """result_id -> (meet_id, date, venue name, distance) for the placed
    meets' tfrrs rows whose gps is the tfrrs meet's own (not an athletic.net
    meet's of the same div), with _xcQuery's own joins and name."""
    cur.execute("""
        SELECT r.result_id, r.meet_id, r.date,
               btrim(COALESCE(m.course_name, mt.venue_name)), r.time_seconds
        FROM results r
        JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND mt.sport = 'XC'
        LEFT JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        WHERE r.source = 'tfrrs' AND r.meet_id = ANY(%s)
          AND (m.gps_lat IS NULL OR m.gps_lat = 0)""", (sorted(meets),))
    from datetime import date
    out = {}
    for rid, mid, d, name, t in cur.fetchall():
        try:                                # results.date is text
            d = date.fromisoformat(str(d)[:10]) if d else None
        except ValueError:
            d = None
        out[int(rid)] = (int(mid), d, name, t)
    return out


def rekey(cols, rows):
    """Placed rows on a canonical key go back to the venue's name key.
    Returns (row indices moved, counts)."""
    keys = [str(k) for k in cols["course_keys"]]
    index = {k: i for i, k in enumerate(keys)}
    course = np.asarray(cols["course"]).astype(np.int64).copy()
    rid = np.asarray(cols["result_id"]).astype(np.int64)
    is_xc = np.array([k.startswith("XC:") for k in keys])
    cand = np.flatnonzero((course >= 0) & is_xc[np.maximum(course, 0)]
                          & np.isin(rid, np.fromiter(rows, dtype=np.int64)))
    n = {"placed rows in the pack": cand.size, "re-keyed": 0, "champ or name key kept": 0,
         "no venue name": 0}
    moved = []
    for i in cand:
        k = keys[course[i]]
        venue, tag, dist = k[3:].rpartition(":d")
        name = rows[int(rid[i])][2]
        if not tag or not venue.isdigit():
            n["champ or name key kept"] += 1
            continue
        if not name:
            n["no venue name"] += 1
            continue
        nk = f"XC:name:{name}:d{dist}"
        j = index.get(nk)
        if j is None:
            j = index[nk] = len(keys)
            keys.append(nk)
        course[i] = j
        moved.append(i)
        n["re-keyed"] += 1
    new = len(keys) - len(cols["course_keys"])
    n["new name keys"] = new
    n["name keys already there"] = len({keys[course[i]] for i in moved}) - new
    return np.asarray(moved, dtype=np.int64), course, keys, n


def extendPerCourse(cols, n_old, n_new):
    """Every per-course-key array grows by the new keys: NaN (no place, no
    track), '' or 0; cell_days is rebuilt after."""
    add = n_new - n_old
    for k, v in list(cols.items()):
        if not isinstance(v, np.ndarray) or k == "course_keys" or v.ndim != 1 or v.size != n_old:
            continue
        if v.dtype.kind == "f":
            fill = np.full(add, np.nan, dtype=v.dtype)
        elif v.dtype.kind == "U":
            fill = np.full(add, "", dtype=v.dtype)
        else:
            fill = np.zeros(add, dtype=v.dtype)
        cols[k] = np.concatenate([v, fill])


def cellDays(course, year, doy, n_crs):
    """speed_ratings.packResults' cell_days: distinct race days per cell."""
    real = course >= 0
    datekey = year[real].astype(np.int64) * 400 + doy[real].astype(np.int64)
    uniq = np.unique((course[real].astype(np.int64) << 20) + datekey)
    return np.bincount(uniq >> 20, minlength=n_crs).astype(np.int32)


def unweather(cur, cols, idx, rows, mode):
    """Take the day's weather off the placed rows' norm where the pack
    carries it. mode: check (decide from the rows), on (always), off."""
    if mode == "off" or idx.size == 0:
        return {"weather": "left as packed (--weather off)"}
    cwd = os.getcwd()
    os.chdir(REPO)                          # backfill_normalize's relative paths
    try:
        import backfill_normalize as bn
        import normalize_distance as nd
        if not bn._weatherEnabled("XC"):
            return {"weather": "no XC weather artifact: nothing to take off"}
        t0 = time.time()
        wx = bn.WeatherIndex.build(cur, "XC")
        print(f"[variant] weather index in {time.time() - t0:.0f}s", flush=True)
    finally:
        os.chdir(cwd)
    norm = np.asarray(cols["norm"], dtype=np.float64)
    rid = np.asarray(cols["result_id"]).astype(np.int64)
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    keys = cols["athlete_keys"]
    athlete = np.asarray(cols["athlete"]).astype(np.int64)
    ratio = np.ones(idx.size)
    n = {"with weather": 0, "without": 0, "neither": 0, "no weather that day": 0}
    for j, i in enumerate(idx):
        mid, d, _name, t = rows[int(rid[i])]
        w, course = wx.lookup("tfrrs", mid, d)
        if not w or not t:
            n["no weather that day"] += 1
            continue
        pool = str(keys[athlete[i]][1]).partition("|")[0]
        gender = "F" if pool.endswith("_f") else "M"
        kw = dict(date=d, sport="XC", course=course, pool=pool)
        a = nd.normalizeResult(float(t), float(dist[i]), None, gender, weather=w, **kw)
        b = nd.normalizeResult(float(t), float(dist[i]), None, gender, weather=None, **kw)
        a, b = (a or {}).get("normalized_time"), (b or {}).get("normalized_time")
        if not a or not b:
            n["neither"] += 1
            continue
        ratio[j] = b / a
        ea, eb = abs(np.log(norm[i] / a)), abs(np.log(norm[i] / b))
        n["with weather" if ea < eb and ea < 1e-3 else "without" if eb < 1e-3 else "neither"] += 1
    apply = mode == "on" or n["with weather"] > n["without"]
    if apply:
        cols["norm"] = cols["norm"].copy()
        cols["norm"][idx] = (norm[idx] * ratio).astype(cols["norm"].dtype)
    moved = ratio[ratio != 1.0]
    n["weather"] = ("taken off" if apply else "left (the pack does not carry it)")
    if moved.size:
        n["median |change| of the moved rows"] = f"{100 * np.median(np.abs(np.log(moved))):.2f}%"
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="the checkout whose engine and backfill are read")
    ap.add_argument("--pack", default=None, help="default <repo>/engine/data/packed_XC_TF.npz")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dump", default="/srv/migrate/db",
                    help="the migration's pg_dump -Fd of OLD, taken before the placement")
    ap.add_argument("--all-tfrrs", action="store_true",
                    help="no dump: every tfrrs XC meet with gps counts as placed")
    ap.add_argument("--weather", choices=("check", "on", "off"), default="check")
    a = ap.parse_args()
    pack = a.pack or os.path.join(REPO, "engine", "data", "packed_XC_TF.npz")
    if os.path.abspath(a.out) == os.path.abspath(pack):
        raise SystemExit("[variant] --out is the production pack; refusing")
    import speed_ratings as sr
    from database import getConn
    cols = sr.loadCols(pack)
    with getConn() as conn, conn.cursor() as cur:
        meets = placedMeets(cur, a.dump, a.all_tfrrs)
        rows = placedRows(cur, meets)
        print(f"[variant] {len(rows):,} tfrrs rows in those meets carry the meet's own gps")
        idx, course, keys, n = rekey(cols, rows)
        for k, v in n.items():
            print(f"[variant]   {k:28s} {v:,}")
        n_old = len(cols["course_keys"])
        extendPerCourse(cols, n_old, len(keys))
        cols["course"] = course.astype(np.asarray(cols["course"]).dtype)
        cols["course_keys"] = np.asarray(keys) if isinstance(cols["course_keys"], np.ndarray) \
            else keys
        if "cell_days" in cols:
            cols["cell_days"] = cellDays(course, np.asarray(cols["year"]),
                                         np.asarray(cols["doy"]), len(keys))
        w = unweather(cur, cols, idx, rows, a.weather)
        for k, v in w.items():
            print(f"[variant]   weather: {k:26s} {v}")
        conn.rollback()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sr.saveCols(cols, a.out)
    st = os.stat(pack)
    os.utime(a.out, (st.st_atime, st.st_mtime))
    print(f"[variant] wrote {a.out} with the pack's own date; nothing written to the database")


if __name__ == "__main__":
    main()
