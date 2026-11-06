# Project: xc-predictor / racecast
# File:    data_snapshots.py
# Purpose: Dated, frozen copies of the season ratings and the course
#          difficulties, and their downloads (owner, 2026-11-06: "build the
#          snapshots"; a county's All-County selection asked "is there a way
#          to download or archive athlete ratings and course difficulty
#          data?" and "how much might historical ratings change?").
#
#              /data                                 every snapshot, with links
#              /data/<id>/athletes.csv[?state=WI&level=hs_m]
#              /data/<id>/courses.csv
#
#          and, on the server,
#
#              scripts/take_snapshot.py --label "2026 cross country, end of season"
#
# ★ FROZEN MEANS COPIED. Ratings are recomputed every week as results come in
#   and data is corrected; a selection made on a date needs the numbers of
#   that date. A snapshot copies athlete_season (the boards' own table) and
#   course_difficulties into its own rows when it is taken and never reads
#   them again, so a download a year later is the file of that day.
# ★ HS-EQUIVALENT, LIKE EVERY RATING THE SITE SHOWS (CLAUDE.md): each row
#   stores the rating on the public scale (pool_view.toHs) at the moment it
#   is copied. A row with no name is left out (rankings.UNNAMED_OUT's rule).
import csv
import io

from flask import Blueprint, Response, abort, render_template, request

bp = Blueprint("data_snapshots", __name__)

_DDL = """
CREATE TABLE IF NOT EXISTS data_snapshot (
    snap_id   serial PRIMARY KEY,
    label     text NOT NULL,
    sport     text NOT NULL,
    year      int  NOT NULL,
    taken_at  timestamptz NOT NULL DEFAULT now(),
    n_athletes int, n_courses int);
CREATE TABLE IF NOT EXISTS data_snapshot_athlete (
    snap_id int NOT NULL, person_id bigint, name text, school text, state text,
    level text, grade text, rating real, best real, n_races int);
CREATE INDEX IF NOT EXISTS data_snapshot_athlete_idx
    ON data_snapshot_athlete (snap_id, level, state);
CREATE TABLE IF NOT EXISTS data_snapshot_course (
    snap_id int NOT NULL, course text, distance_m real, difficulty_pct real, n_results int);
CREATE INDEX IF NOT EXISTS data_snapshot_course_idx ON data_snapshot_course (snap_id);
"""

LEVELS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")


def ensure(cur):
    cur.execute(_DDL)


AUTO_PREFIX = "Week of "


def prune(cur, keep):
    """Drop the weekly snapshots (label AUTO_PREFIX...) past the newest
    `keep`; a snapshot taken by hand, with any other label, stays. -> n."""
    ensure(cur)
    cur.execute("""SELECT snap_id FROM data_snapshot WHERE label LIKE %s
                   ORDER BY taken_at DESC, snap_id DESC OFFSET %s""", (AUTO_PREFIX + "%", keep))
    ids = [r[0] for r in cur.fetchall()]
    for t in ("data_snapshot_athlete", "data_snapshot_course", "data_snapshot"):
        cur.execute(f"DELETE FROM {t} WHERE snap_id = ANY(%s)", (ids,))
    return len(ids)


def take(cur, label, sport, year):
    """Copy this season's ratings and every course difficulty into a new
    snapshot. -> (snap_id, n_athletes, n_courses)."""
    import pool_view
    from rankings import nameLateral
    ensure(cur)
    cur.execute("INSERT INTO data_snapshot (label, sport, year) VALUES (%s, %s, %s) "
                "RETURNING snap_id", (label, sport, year))
    snap = cur.fetchone()[0]
    cur.execute(f"""
        SELECT s.person_id, a.name, s.school, s.state, split_part(s.pool, '|', 1) AS level,
               s.grade, s.mean_rating, s.best_rating, s.n_races
        FROM   athlete_season s
        {nameLateral("s")}
        WHERE  s.sport = %(sport)s AND s.year = %(year)s AND s.mean_rating IS NOT NULL
          AND  a.name IS NOT NULL""", {"sport": sport, "year": year})
    rows = []
    for pid, name, school, state, level, grade, mean, best, n in cur.fetchall():
        hs = pool_view.toHs(mean, level, sport)
        hs_best = pool_view.toHs(best, level, sport)
        rows.append((snap, pid, name, school, state, level, grade,
                     round(hs if hs is not None else float(mean), 1),
                     round(hs_best if hs_best is not None else float(best or 0), 1) if best else None,
                     n))
    from psycopg2.extras import execute_values
    if rows:
        execute_values(cur, "INSERT INTO data_snapshot_athlete VALUES %s", rows, page_size=5000)
    cur.execute("SELECT to_regclass('course_difficulties')")
    courses = []
    if cur.fetchone()[0] is not None:
        from courses import courseDisplayName
        prefix = "XC:" if sport == "XC" else "TF:"
        cur.execute("""SELECT course_name, distance_m, difficulty, n_results
                       FROM course_difficulties
                       WHERE course_name LIKE %s AND difficulty IS NOT NULL""", (prefix + "%",))
        for key, dist, diff, n in cur.fetchall():
            courses.append((snap, courseDisplayName(key), dist, round(100.0 * float(diff), 2), n))
        if courses:
            execute_values(cur, "INSERT INTO data_snapshot_course VALUES %s", courses, page_size=5000)
    cur.execute("UPDATE data_snapshot SET n_athletes = %s, n_courses = %s WHERE snap_id = %s",
                (len(rows), len(courses), snap))
    return snap, len(rows), len(courses)


def _csv(header, rows, filename):
    """A CSV download, written as it is read (a whole season is large)."""
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        yield buf.getvalue()
        for r in rows:
            buf.seek(0)
            buf.truncate()
            w.writerow(r)
            yield buf.getvalue()
    return Response(gen(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _snapshot(cur, snap_id):
    cur.execute("SELECT snap_id, label, sport, year, taken_at FROM data_snapshot WHERE snap_id = %s",
                (snap_id,))
    return cur.fetchone()


@bp.route("/data")
def data_page():
    from database import getConn
    snaps = []
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('data_snapshot')")
        if cur.fetchone()[0] is not None:
            cur.execute("""SELECT snap_id, label, sport, year, taken_at, n_athletes, n_courses
                           FROM data_snapshot ORDER BY taken_at DESC""")
            snaps = [dict(zip(("id", "label", "sport", "year", "taken_at", "n_athletes",
                               "n_courses"), r)) for r in cur.fetchall()]
        conn.rollback()
    from season_floor import poolWords
    return render_template("data.html", snaps=snaps,
                           levels=[(lv, poolWords(lv).capitalize()) for lv in LEVELS])


@bp.route("/data/<int:snap_id>/athletes.csv")
def athletes_csv(snap_id):
    from database import getConn
    state = (request.args.get("state") or "").strip().upper()[:2] or None
    level = (request.args.get("level") or "").strip()
    level = level if level in LEVELS else None
    # ★ ONE STATE AT A TIME (owner, 2026-11-06: "yes to both"): the whole
    #   season in one file is what a scraper is after. The state is the
    #   ATHLETE'S -- their school's (a college runner's campus), as the
    #   boards file them -- not where the races were.
    if not state or not state.isalpha():
        return Response("Pick a state: /data/<id>/athletes.csv?state=WI (athletes whose team is "
                        "in that state).\n", status=400, mimetype="text/plain")
    with getConn() as conn, conn.cursor() as cur:
        snap = _snapshot(cur, snap_id) if _exists(cur) else None
        if not snap:
            abort(404)
        cur.execute("""SELECT level, name, school, state, grade, rating, best, n_races, person_id
                       FROM data_snapshot_athlete
                       WHERE snap_id = %(s)s
                         AND (%(st)s::text IS NULL OR state = %(st)s)
                         AND (%(lv)s::text IS NULL OR level = %(lv)s)
                       ORDER BY level, rating DESC NULLS LAST""",
                    {"s": snap_id, "st": state, "lv": level})
        rows = cur.fetchall()
        conn.rollback()
    site = request.host_url.rstrip("/")
    out = ((lv, nm, sc, st, gr, r, b, n, f"{site}/athlete/{pid}")
           for lv, nm, sc, st, gr, r, b, n, pid in rows)
    name = f"racecast-{snap[2].lower()}-{snap[3]}-ratings-{snap_id}" \
           + (f"-{state}" if state else "") + (f"-{level}" if level else "") + ".csv"
    return _csv(["level", "name", "school", "state", "grade", "rating_hs_equivalent",
                 "best_race_hs_equivalent", "races", "athlete_page"], out, name)


@bp.route("/data/<int:snap_id>/courses.csv")
def courses_csv(snap_id):
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        snap = _snapshot(cur, snap_id) if _exists(cur) else None
        if not snap:
            abort(404)
        cur.execute("""SELECT course, distance_m, difficulty_pct, n_results
                       FROM data_snapshot_course WHERE snap_id = %s
                       ORDER BY course, distance_m""", (snap_id,))
        rows = cur.fetchall()
        conn.rollback()
    return _csv(["course", "distance_m", "difficulty_pct_vs_flat_track", "results"], rows,
                f"racecast-{snap[2].lower()}-courses-{snap_id}.csv")


def _exists(cur):
    cur.execute("SELECT to_regclass('data_snapshot')")
    return cur.fetchone()[0] is not None
