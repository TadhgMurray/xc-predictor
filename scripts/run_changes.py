#!/usr/bin/env python3
"""
run_changes.py -- what a full pipeline run changed on the site, for the
"What's new" page (racecast/activity.py) and the run's log.

    /srv/venv/bin/python scripts/run_changes.py --snapshot          # step 00b, first
    /srv/venv/bin/python scripts/run_changes.py --report --log-dir logs/20261022_135400

★ WHY (owner, 2026-10-22: "say what changed in the recomputed ratings vs the
  last pipeline run, I want to give the people actual insight into it").
  A full run re-rates every result, so ratings and course numbers move; the
  site only ever showed the new numbers. --snapshot keeps the numbers the
  site was showing when the run started (this season's athlete ratings and
  every XC course number); --report, near the end, compares and writes
  engine/data/run_changes/<run>.json:

    new_results   results scraped since the previous full run started
    athletes      per level and sport, this season: how many compared, the
                  median and mean change, how many moved 2+ and 5+ points,
                  how many went up, how many were rated for the first time
    courses       course numbers that moved 1%+, courses rated for the first
                  time, and the biggest moves among well-raced courses

! NO ATHLETE IS NAMED, only counts: most of them are minors, and "who moved
  most" on a public page is a list of children. Courses are named.
! THE BASELINE IS WHAT THE SITE SHOWED, nightly updates included -- so the
  numbers are "what this recompute changed", not "since the last full run".
! READ ONLY on the site's tables; --snapshot writes its own two tables.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(_ROOT, "engine", "data", "run_changes")
MIN_COURSE_RESULTS = 200          # a course must be this well raced to be named
TOP_COURSES = 6

_SNAPSHOT_SQL = [
    "DROP TABLE IF EXISTS run_prev_athlete",
    """CREATE TABLE run_prev_athlete AS
       SELECT a.person_id, a.pool, a.year, a.sport, a.mean_rating
       FROM   athlete_season a
       JOIN  (SELECT sport, max(year) AS y FROM athlete_season GROUP BY sport) m
              ON m.sport = a.sport AND a.year = m.y
       WHERE  a.person_id IS NOT NULL AND a.mean_rating > 0""",
    "CREATE INDEX ON run_prev_athlete (person_id, pool, year, sport)",
    "DROP TABLE IF EXISTS run_prev_course",
    """CREATE TABLE run_prev_course AS
       SELECT canonical_id, distance_m, difficulty, n_results, course_name
       FROM   course_difficulties
       WHERE  canonical_id IS NOT NULL AND course_name LIKE 'XC:%%'""",
    "DROP TABLE IF EXISTS run_prev_meta",
    "CREATE TABLE run_prev_meta AS SELECT now() AS taken_at",
]

_ATHLETE_SQL = """
    WITH cur AS (
        SELECT a.person_id, a.pool, a.year, a.sport, a.mean_rating
        FROM   athlete_season a
        JOIN  (SELECT DISTINCT sport, year FROM run_prev_athlete) y
               ON y.sport = a.sport AND y.year = a.year
        WHERE  a.person_id IS NOT NULL AND a.mean_rating > 0
    )
    SELECT split_part(split_part(c.pool, '|', 1), '_', 1) AS lvl, c.sport,
           count(o.person_id)                                            AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(c.mean_rating - o.mean_rating))
                                                                         AS med_abs,
           avg(c.mean_rating - o.mean_rating)                            AS mean_d,
           count(*) FILTER (WHERE abs(c.mean_rating - o.mean_rating) >= 2) AS n2,
           count(*) FILTER (WHERE abs(c.mean_rating - o.mean_rating) >= 5) AS n5,
           count(*) FILTER (WHERE c.mean_rating > o.mean_rating + 0.05)  AS n_up,
           count(*) FILTER (WHERE c.mean_rating < o.mean_rating - 0.05)  AS n_down,
           count(*) FILTER (WHERE o.person_id IS NULL)                   AS n_new
    FROM   cur c
    LEFT   JOIN run_prev_athlete o
           ON o.person_id = c.person_id AND o.pool = c.pool
          AND o.year = c.year AND o.sport = c.sport
    GROUP  BY 1, 2
"""

_COURSE_SQL = """
    SELECT n.course_name, n.distance_m, n.n_results, o.difficulty, n.difficulty
    FROM   course_difficulties n
    LEFT   JOIN run_prev_course o
           ON o.canonical_id = n.canonical_id AND o.distance_m = n.distance_m
    WHERE  n.canonical_id IS NOT NULL AND n.course_name LIKE 'XC:%%'
"""


# ---- pure parts ----------------------------------------------------------

def courseName(raw):
    """'XC:Mt. SAC|...' -> 'Mt. SAC'. Pure."""
    s = re.sub(r"^XC:", "", raw or "")
    return s.split("|", 1)[0].strip() or "?"


def courseChanges(rows, min_results=MIN_COURSE_RESULTS, top=TOP_COURSES):
    """rows: (course_name, distance_m, n_results, old, new) difficulty as a
    fraction. Pure."""
    compared = moved = new = 0
    big = []
    for name, dist, n, old, cur in rows:
        if cur is None:
            continue
        if old is None:
            new += 1
            continue
        compared += 1
        d = 100.0 * (float(cur) - float(old))
        if abs(d) >= 1.0:
            moved += 1
        if (n or 0) >= min_results and abs(d) >= 0.5:
            big.append((abs(d), courseName(name), int(dist or 0), round(d, 2)))
    big.sort(reverse=True)
    return {"compared": compared, "moved_1pct": moved, "new": new,
            "biggest": [{"course": c, "distance_m": dm, "change_pct": d}
                        for _a, c, dm, d in big[:top]]}


def athleteChanges(rows):
    """rows from _ATHLETE_SQL -> [{level, sport, ...}], biggest groups first.
    Pure."""
    out = []
    for lvl, sport, n, med, mean, n2, n5, up, down, new in rows:
        if not n and not new:
            continue
        out.append({"level": lvl or "?", "sport": sport, "compared": int(n or 0),
                    "median_abs": round(float(med or 0), 2), "mean": round(float(mean or 0), 2),
                    "moved_2": int(n2 or 0), "moved_5": int(n5 or 0),
                    "up": int(up or 0), "down": int(down or 0), "new": int(new or 0)})
    out.sort(key=lambda r: -(r["compared"] + r["new"]))
    return out


# ★ DID THE ENGINE ITSELF CHANGE (owner, 2026-10-22: "does it say if the
#   engine itself actually changes and how?"). The "how" is a person's
#   sentence (racecast/public_changes.py); this records the commit each run
#   ran and which method files changed since the run before, so a change
#   with no sentence is flagged in the log and on the admin page.
ENGINE_PATHS = ("engine", "model", "racecast/predict.py", "racecast/conversions.py",
                "racecast/build_ranking_results.py", "racecast/tilt.py")


def _git(*args):
    import subprocess
    try:
        return subprocess.run(["git", "-C", _ROOT, *args], capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return ""


def engineChanges(run):
    """{"commit", "previous_commit", "engine_files", "public_notes"}."""
    commit = _git("rev-parse", "HEAD")
    prev = None
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "*.json")), reverse=True):
        if os.path.basename(path)[:-5] >= run:
            continue
        try:
            prev = json.load(open(path)).get("commit")
        except (OSError, ValueError):
            prev = None
        if prev:
            break
    out = {"commit": commit, "previous_commit": prev, "engine_files": []}
    if commit and prev and prev != commit:
        names = _git("diff", "--name-only", f"{prev}..{commit}", "--", *ENGINE_PATHS)
        out["engine_files"] = [n for n in names.splitlines() if n.strip()]
    try:
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        import public_changes
        prev_at = previousRunStart(os.path.join(_ROOT, "logs", run))
        out["public_notes"] = len(public_changes.between(
            prev_at.date() if prev_at else None, datetime.date.today()))
    except Exception:                                   # noqa: BLE001
        out["public_notes"] = None
    return out


def previousRunStart(log_dir):
    """The start of the full run before `log_dir`, from the sibling folder
    names (20261019_143556), or None. Pure on the names."""
    here = os.path.basename(os.path.normpath(log_dir or ""))
    root = os.path.dirname(os.path.normpath(log_dir or ""))
    names = sorted(os.path.basename(d) for d in glob.glob(os.path.join(root, "2*"))
                   if os.path.isdir(d))
    earlier = [n for n in names if n < here]
    if not earlier:
        return None
    m = re.match(r"(\d{8})_(\d{6})", earlier[-1])
    return (datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            if m else None)


# ---- the two modes ---------------------------------------------------------

def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        for sql in _SNAPSHOT_SQL:
            cur.execute(sql)
        cur.execute("SELECT (SELECT count(*) FROM run_prev_athlete), "
                    "(SELECT count(*) FROM run_prev_course)")
        a, c = cur.fetchone()
    conn.commit()
    print(f"[changes] snapshot: {a:,} athlete-seasons (latest season per sport), "
          f"{c:,} XC course numbers")


def report(conn, log_dir):
    run = os.path.basename(os.path.normpath(log_dir))
    out = {"run": run, "at": datetime.datetime.now().isoformat(timespec="seconds")}
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute("SELECT to_regclass('run_prev_athlete') IS NOT NULL")
        if not cur.fetchone()[0]:
            print("[changes] no snapshot (step 00b did not run): nothing to compare")
            return None
        cur.execute("SELECT taken_at FROM run_prev_meta LIMIT 1")
        row = cur.fetchone()
        out["baseline_at"] = row[0].isoformat(timespec="seconds") if row and row[0] else None
        prev = previousRunStart(log_dir)
        out["previous_run_at"] = prev.isoformat(timespec="seconds") if prev else None
        if prev:
            since = prev.date().isoformat()
            cur.execute("SELECT (SELECT count(*) FROM results WHERE scraped_at >= %s)"
                        " + (SELECT count(*) FROM results_tf WHERE scraped_at >= %s)",
                        (since, since))
            out["new_results"] = int(cur.fetchone()[0] or 0)
        cur.execute(_ATHLETE_SQL)
        out["athletes"] = athleteChanges(cur.fetchall())
        cur.execute(_COURSE_SQL)
        out["courses"] = courseChanges(cur.fetchall())
    conn.rollback()
    out.update(engineChanges(run))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{run}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for a in out["athletes"]:
        print(f"[changes] {a['level']:<8}{a['sport']:<4} {a['compared']:>9,} compared  "
              f"median |change| {a['median_abs']:.2f}  mean {a['mean']:+.2f}  "
              f"2+ pts {a['moved_2']:,}  5+ pts {a['moved_5']:,}  "
              f"up {a['up']:,} / down {a['down']:,}  new {a['new']:,}")
    c = out["courses"]
    print(f"[changes] courses: {c['compared']:,} compared, {c['moved_1pct']:,} moved 1%+, "
          f"{c['new']:,} new")
    for b in c["biggest"]:
        print(f"[changes]   {b['course']} {b['distance_m']} m: {b['change_pct']:+.2f}%")
    if out.get("engine_files"):
        print(f"[changes] engine code changed since {out.get('previous_commit', '?')[:8]}: "
              f"{len(out['engine_files'])} file(s); public notes this run: "
              f"{out.get('public_notes', 0)}")
        if not out.get("public_notes"):
            print("[changes] ! no racecast/public_changes.py entry since the last run -- "
                  "What's new will not say how the method changed")
    print(f"[changes] wrote {path}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--log-dir", default=None)
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        if a.snapshot:
            snapshot(conn)
        if a.report:
            if not a.log_dir:
                ap.error("--report needs --log-dir")
            report(conn, a.log_dir)


if __name__ == "__main__":
    main()
