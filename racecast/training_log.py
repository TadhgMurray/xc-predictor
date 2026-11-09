# Project: xc-predictor / racecast
# File:    training_log.py
# Purpose: A runner's own training, uploaded from their watch's files
#          (owner, 2026-11-08: "Start the upload page"). A signed-in account
#          that has linked its athlete page uploads activity files -- a FIT
#          from the watch, or the whole activities folder of a Strava,
#          Garmin or COROS export -- and sees its weeks back. The aim is a
#          model that reads training as well as races; that use is a
#          separate, explicit opt-in (below).
#
#     python racecast/training_log.py --init     # create the tables (once)
#     python racecast/training_log.py --check    # are they there, is the opt-in on
#
# ★ THE RUNNER'S OWN FILES, NOT A PLATFORM'S API (owner, 2026-11-08). Strava's
#   API agreement bars training models on its data and Garmin has paused new
#   developer access, so nothing here talks to either: the runner downloads
#   their own export and hands it to us. The data comes from them, under our
#   terms and their consent.
#
# ★ A SUMMARY PER RUN, NOTHING ELSE. Each file is read in memory and only
#   its summary is kept: start, sport, distance, elapsed and moving time,
#   heart rate (average, max), climb, the file's kind. NO GPS TRACK and no
#   raw file is ever stored -- where a runner trains, often from home and
#   often a minor's, is not ours to hold. Only running is kept (a ride or a
#   swim is skipped and counted as skipped).
#
# ★ ONLY THE RUNNER SEES IT. The page is under /account (private, no-store,
#   not crawled: app._PRIVATE_PREFIXES); no public page reads these tables.
#   Deleting the account deletes every activity (ON DELETE CASCADE), and the
#   page has its own "delete all my training".
#
# ⚠ THE RESEARCH OPT-IN IS OFF UNTIL THE OWNER TURNS IT ON
#   (XCP_TRAINING_RESEARCH=1, after the consent wording has been reviewed).
#   Off, the switch is not shown and nothing may be read for a model;
#   researchActivities() returns only accounts that ticked it, and is the one
#   door a model's training reads through. Accounts are 13+ (accounts.py);
#   many of these runners are minors.
#
# ! THE FILES ARE DATA. The app's 9 MB request cap holds, so the page sends a
#   folder in batches (static/training.js); each file is at most
#   MAX_FILE_BYTES, a .gz is opened to at most MAX_RAW_BYTES, XML is read
#   with defusedxml (no entities, no network), FIT with fitdecode. Nothing in
#   a file is run, fetched or shown unescaped.

import datetime
import gzip
import io
import math
import os
import sys

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("training", __name__)

MAX_FILE_BYTES = 8 * 1024 * 1024          # one upload's file, as sent
MAX_RAW_BYTES = 40 * 1024 * 1024          # a .gz opened
MAX_FILES_PER_REQUEST = 60
MAX_ACTIVITIES_PER_PERSON = 6000          # ~16 years of a run a day
WEEKS_SHOWN = 16
RECENT_SHOWN = 25
METERS_PER_MILE = 1609.344
CONSENT_VERSION = "2026-11-08"
CLAIM_KINDS = ("athlete", "coach_self")   # the claims that are a runner's own page
# a run: the sports the files name for running, on foot
_RUN_SPORTS = {"running", "run", "trail_running", "trail running", "treadmill",
               "treadmill_running", "track_running", "indoor_running", "virtual_run",
               "street_running", "9"}
# sanity: a run longer than this, or faster than this pace over a km, is not a run
MAX_RUN_M = 200_000.0
MAX_RUN_S = 30 * 3600.0
MIN_SEC_PER_KM = 150.0                    # 2:30 per km: a world record is ~2:37

DDL = """
CREATE TABLE IF NOT EXISTS training_activity (
    id          bigserial PRIMARY KEY,
    account_id  bigint NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    person_id   bigint NOT NULL,
    started_at  timestamptz NOT NULL,
    sport       text NOT NULL,
    distance_m  real,
    elapsed_s   real,
    moving_s    real,
    avg_hr      real,
    max_hr      real,
    ascent_m    real,
    source      text NOT NULL,
    dedupe      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, person_id, dedupe)
);
CREATE INDEX IF NOT EXISTS training_activity_person_idx ON training_activity (person_id, started_at);
CREATE TABLE IF NOT EXISTS training_consent (
    account_id  bigint PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    research    boolean NOT NULL DEFAULT false,
    version     text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
"""


class TrainingError(Exception):
    """A message for the person, never a traceback."""


def researchEnabled():
    return os.environ.get("XCP_TRAINING_RESEARCH", "0") == "1"


# ---- reading a file (pure) ---------------------------------------------------

def unpack(name, data):
    """(name without .gz, raw bytes): a gzip opened, at most MAX_RAW_BYTES."""
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            raw = gz.read(MAX_RAW_BYTES + 1)
        if len(raw) > MAX_RAW_BYTES:
            raise TrainingError("too large once opened")
        name = name[:-3] if name.lower().endswith(".gz") else name
        return name, raw
    return name, data


def sniff(data):
    """'fit', 'gpx', 'tcx' or None, from the bytes, not the name."""
    if len(data) >= 12 and data[8:12] == b".FIT":
        return "fit"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        if b"<gpx" in head:
            return "gpx"
        if b"<trainingcenterdatabase" in head:
            return "tcx"
    return None


def _utc(t):
    if t is None:
        return None
    if isinstance(t, str):
        t = t.strip().replace("Z", "+00:00")
        try:
            t = datetime.datetime.fromisoformat(t)
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc)


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def readFit(raw):
    """[activity] from a FIT file: one per session message; from the records
    when a file has none."""
    import fitdecode
    sessions, first_ts, last_ts, last_dist, sport = [], None, None, None, None
    with fitdecode.FitReader(io.BytesIO(raw), check_crc=fitdecode.CrcCheck.DISABLED,
                             keep_raw_chunks=False) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            def get(name, f=frame):
                return f.get_value(name, fallback=None) if f.has_field(name) else None
            if frame.name == "session":
                sessions.append({
                    "started_at": _utc(get("start_time")),
                    "sport": str(get("sport") or ""), "sub_sport": str(get("sub_sport") or ""),
                    "distance_m": _num(get("total_distance")),
                    "elapsed_s": _num(get("total_elapsed_time")),
                    "moving_s": _num(get("total_timer_time")),
                    "avg_hr": _num(get("avg_heart_rate")), "max_hr": _num(get("max_heart_rate")),
                    "ascent_m": _num(get("total_ascent"))})
            elif frame.name == "sport" and sport is None:
                sport = str(get("sport") or "")
            elif frame.name == "record":
                ts = _utc(get("timestamp"))
                if ts is not None:
                    first_ts = first_ts or ts
                    last_ts = ts
                d = _num(get("distance"))
                if d is not None:
                    last_dist = d
    if sessions:
        return sessions
    if first_ts and last_ts:
        span = (last_ts - first_ts).total_seconds()
        return [{"started_at": first_ts, "sport": sport or "", "sub_sport": "",
                 "distance_m": last_dist, "elapsed_s": span, "moving_s": None,
                 "avg_hr": None, "max_hr": None, "ascent_m": None}]
    return []


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def climb(eles, step=3.0):
    """Metres climbed, counting a rise only once it passes `step` (GPS
    elevation wanders; every wiggle summed doubles a flat run's climb)."""
    total, ref = 0.0, None
    for e in eles:
        if ref is None:
            ref = e
        elif e - ref >= step:
            total += e - ref
            ref = e
        elif e < ref:
            ref = e
    return total


def readGpx(raw):
    """[activity] from a GPX file: its track points summed. The points are
    read and dropped; only the summary leaves this function."""
    from defusedxml import ElementTree as ET
    root = ET.fromstring(raw)
    kind, pts, times, eles, hrs = "", [], [], [], []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "type" and not kind:
            kind = (el.text or "").strip()
        elif tag == "trkpt":
            try:
                p = (float(el.get("lat")), float(el.get("lon")))
            except (TypeError, ValueError):
                continue
            pts.append(p)
            for ch in el.iter():
                t = _local(ch.tag)
                if t == "time":
                    times.append(_utc(ch.text))
                elif t == "ele" and _num(ch.text) is not None:
                    eles.append(float(ch.text))
                elif t == "hr" and _num(ch.text):
                    hrs.append(float(ch.text))
    times = [t for t in times if t is not None]
    if len(pts) < 2 or len(times) < 2:
        return []
    dist = sum(_haversine(a, b) for a, b in zip(pts, pts[1:]))
    return [{"started_at": min(times), "sport": kind or "running", "sub_sport": "",
             "distance_m": dist, "elapsed_s": (max(times) - min(times)).total_seconds(),
             "moving_s": None, "avg_hr": sum(hrs) / len(hrs) if hrs else None,
             "max_hr": max(hrs) if hrs else None, "ascent_m": climb(eles) if eles else None}]


def readTcx(raw):
    """[activity] from a TCX file: one per Activity, its laps summed."""
    from defusedxml import ElementTree as ET
    root = ET.fromstring(raw)
    out = []
    for act in (el for el in root.iter() if _local(el.tag) == "activity"):
        sport = act.get("Sport") or ""
        start, dist, secs, hr_w, hr_n, hr_max = None, 0.0, 0.0, 0.0, 0.0, None
        for lap in (el for el in act.iter() if _local(el.tag) == "lap"):
            t0 = _utc(lap.get("StartTime"))
            start = t0 if start is None or (t0 and t0 < start) else start
            vals = {}
            for ch in lap:
                tag = _local(ch.tag)
                if tag in ("totaltimeseconds", "distancemeters"):
                    vals[tag] = _num(ch.text)
                elif tag in ("averageheartratebpm", "maximumheartratebpm"):
                    v = next((_num(x.text) for x in ch if _local(x.tag) == "value"), None)
                    vals[tag] = v
            s = vals.get("totaltimeseconds") or 0.0
            secs += s
            dist += vals.get("distancemeters") or 0.0
            if vals.get("averageheartratebpm") and s:
                hr_w += vals["averageheartratebpm"] * s
                hr_n += s
            if vals.get("maximumheartratebpm"):
                hr_max = max(hr_max or 0.0, vals["maximumheartratebpm"])
        if start is None:
            ids = [x for x in act if _local(x.tag) == "id"]
            start = _utc(ids[0].text) if ids else None
        if start is None:
            continue
        out.append({"started_at": start, "sport": sport, "sub_sport": "",
                    "distance_m": dist or None, "elapsed_s": secs or None, "moving_s": secs or None,
                    "avg_hr": hr_w / hr_n if hr_n else None, "max_hr": hr_max, "ascent_m": None})
    return out


def isRun(a):
    """A run on foot, by the sport the file names (sub-sport too)."""
    words = {str(a.get("sport") or "").strip().lower(), str(a.get("sub_sport") or "").strip().lower()}
    return bool(words & _RUN_SPORTS)


def whyNot(a):
    """The reason an activity is not kept as a run, or None."""
    if not isRun(a):
        return "not a run"
    if a.get("started_at") is None:
        return "no start time"
    d, t = a.get("distance_m"), a.get("moving_s") or a.get("elapsed_s")
    if not d or d <= 0 or not t or t <= 0:
        return "no distance or time"
    if d > MAX_RUN_M or t > MAX_RUN_S:
        return "too long to be a run"
    if d >= 1000 and t / (d / 1000.0) < MIN_SEC_PER_KM:
        return "too fast to be a run on foot"
    return None


def dedupeKey(a):
    """The same run from two exports (Strava's copy and the watch's) is one:
    its start to the minute and its distance to 100 m."""
    return f"{a['started_at']:%Y%m%d%H%M}|{int(round((a.get('distance_m') or 0) / 100.0))}"


def readFile(name, data):
    """(runs, skipped reason or None) for one uploaded file."""
    if len(data) > MAX_FILE_BYTES:
        return [], "file too large"
    try:
        name, raw = unpack(name, data)
        kind = sniff(raw)
        if kind is None:
            return [], "not a FIT, GPX or TCX file"
        acts = {"fit": readFit, "gpx": readGpx, "tcx": readTcx}[kind](raw)
    except TrainingError as exc:
        return [], str(exc)
    except Exception:                                    # noqa: BLE001 -- a broken file
        return [], "could not be read"
    runs, why = [], None
    for a in acts:
        bad = whyNot(a)
        if bad:
            why = why or bad
            continue
        a["source"] = kind
        a["sport"] = "run"
        runs.append(a)
    return runs, (None if runs else (why or "no activity in it"))


def weeklyTotals(rows, today, weeks=WEEKS_SHOWN):
    """[{week (Monday), miles, runs, longest, hours}] for the last `weeks`
    weeks, newest first, empty weeks included. rows: dicts with started_at
    (UTC), distance_m, moving_s/elapsed_s. Pure."""
    monday = today - datetime.timedelta(days=today.weekday())
    out = []
    for k in range(weeks):
        start = monday - datetime.timedelta(weeks=k)
        out.append({"week": start, "miles": 0.0, "runs": 0, "longest": 0.0, "hours": 0.0})
    first = out[-1]["week"]
    for r in rows:
        d = r["started_at"].date()
        if d < first or d > today:
            continue
        w = out[(monday - (d - datetime.timedelta(days=d.weekday()))).days // 7]
        mi = (r.get("distance_m") or 0.0) / METERS_PER_MILE
        w["miles"] += mi
        w["runs"] += 1
        w["longest"] = max(w["longest"], mi)
        w["hours"] += (r.get("moving_s") or r.get("elapsed_s") or 0.0) / 3600.0
    return out


def paceText(distance_m, seconds):
    """'7:32/mi', or ''."""
    if not distance_m or not seconds:
        return ""
    s = seconds / (distance_m / METERS_PER_MILE)
    return f"{int(s // 60)}:{int(round(s % 60)):02d}/mi" if s < 3600 else ""


# ---- database -----------------------------------------------------------------

def tablesReady(cur):
    cur.execute("SELECT to_regclass('public.training_activity') AS t")
    row = cur.fetchone()
    return bool(row and (row["t"] if isinstance(row, dict) else row[0]))


def initTables(conn):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(DDL)
    conn.commit()


def runnersFor(cur, account_id):
    """[(person_id, name)] the account speaks for as a runner."""
    import accounts as A
    out = []
    for c in A.claimsFor(cur, account_id):
        if c["kind"] in CLAIM_KINDS and c.get("person_id") and c["person_id"] not in [p for p, _n in out]:
            out.append((int(c["person_id"]), c.get("name") or f"Athlete {c['person_id']}"))
    return out


def storeRuns(cur, account_id, person_id, runs):
    """(added, already there). Capped at MAX_ACTIVITIES_PER_PERSON."""
    cur.execute("SELECT count(*) AS n FROM training_activity WHERE account_id = %s AND person_id = %s",
                (account_id, person_id))
    row = cur.fetchone()
    have = int(row["n"] if isinstance(row, dict) else row[0])
    added = dup = 0
    for a in runs:
        if have + added >= MAX_ACTIVITIES_PER_PERSON:
            raise TrainingError(f"at most {MAX_ACTIVITIES_PER_PERSON:,} runs per athlete")
        cur.execute("""INSERT INTO training_activity
                         (account_id, person_id, started_at, sport, distance_m, elapsed_s, moving_s,
                          avg_hr, max_hr, ascent_m, source, dedupe)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (account_id, person_id, dedupe) DO NOTHING""",
                    (account_id, person_id, a["started_at"], a["sport"], a.get("distance_m"),
                     a.get("elapsed_s"), a.get("moving_s"), a.get("avg_hr"), a.get("max_hr"),
                     a.get("ascent_m"), a["source"], dedupeKey(a)))
        if cur.rowcount:
            added += 1
        else:
            dup += 1
    return added, dup


def researchActivities(cur):
    """THE ONE DOOR a model's training reads through: the runs of accounts
    that ticked the research opt-in, while it is on. Nothing else in the
    codebase may read training_activity for a model."""
    if not researchEnabled():
        return []
    cur.execute("""SELECT t.person_id, t.started_at, t.sport, t.distance_m, t.elapsed_s, t.moving_s,
                          t.avg_hr, t.max_hr, t.ascent_m, t.source
                   FROM   training_activity t
                   JOIN   training_consent c ON c.account_id = t.account_id AND c.research
                   ORDER  BY t.person_id, t.started_at""")
    return cur.fetchall()


# ---- the page -------------------------------------------------------------------

def _page(**kw):
    return render_template("training.html", research_enabled=researchEnabled(),
                           max_mb=MAX_FILE_BYTES // (1024 * 1024), **kw)


@bp.route("/account/training")
def training_page():
    import accounts as A
    sess, go = A._requireSession()
    if go:
        return go
    with A._db() as (conn, cur):
        if not tablesReady(cur):
            return _page(mode="unready", account=sess["account"])
        runners = runnersFor(cur, sess["account"]["id"])
        pick = request.args.get("person", "")
        person = next((p for p in runners if str(p[0]) == pick), runners[0] if runners else None)
        rows, weeks, consent, total = [], [], False, 0
        if person:
            cur.execute("""SELECT started_at, distance_m, elapsed_s, moving_s, avg_hr, max_hr, ascent_m,
                                  source
                           FROM   training_activity
                           WHERE  account_id = %s AND person_id = %s
                           ORDER  BY started_at DESC""", (sess["account"]["id"], person[0]))
            rows = [dict(r) for r in cur.fetchall()]
            total = len(rows)
            weeks = weeklyTotals(rows, datetime.date.today())
        cur.execute("SELECT research FROM training_consent WHERE account_id = %s",
                    (sess["account"]["id"],))
        c = cur.fetchone()
        consent = bool(c and c["research"])
        conn.commit()
    for r in rows[:RECENT_SHOWN]:
        r["miles"] = (r.get("distance_m") or 0.0) / METERS_PER_MILE
        r["pace"] = paceText(r.get("distance_m"), r.get("moving_s") or r.get("elapsed_s"))
    return _page(mode="page", account=sess["account"], csrf=sess["csrf"], runners=runners,
                 person=person, recent=rows[:RECENT_SHOWN], weeks=weeks, total=total,
                 consent=consent, notice=request.args.get("notice", "")[:200],
                 error=request.args.get("error", "")[:200])


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


@bp.route("/account/training/upload", methods=["POST"])
def training_upload():
    """A batch of files from the page (static/training.js) -> JSON: how many
    runs were added, how many were there already, and each file skipped
    with why."""
    import accounts as A
    sess = A.currentSession()
    if not sess:
        return _bad("Please sign in again.", 401)
    if not A.csrfOk(sess):
        return _bad("That request did not come from this site.")
    files = request.files.getlist("files")
    if not files:
        return _bad("No files were sent.")
    if len(files) > MAX_FILES_PER_REQUEST:
        return _bad(f"At most {MAX_FILES_PER_REQUEST} files at a time.")
    pid = (request.form.get("person_id") or "").strip()
    with A._db() as (conn, cur):
        if not tablesReady(cur):
            return _bad("Training uploads aren't available yet.", 503)
        runners = dict(runnersFor(cur, sess["account"]["id"]))
        if not pid.isdigit() or int(pid) not in runners:
            return _bad("Link your athlete page first, then upload.")
        runs, skipped, seen = [], [], set()
        for f in files:
            name = os.path.basename(f.filename or "file")[:120]
            got, why = readFile(name, f.read(MAX_FILE_BYTES + 1))
            if why:
                skipped.append({"file": name, "why": why})
            for a in got:
                k = dedupeKey(a)
                if k not in seen:
                    seen.add(k)
                    runs.append(a)
        try:
            added, dup = storeRuns(cur, sess["account"]["id"], int(pid), runs)
        except TrainingError as exc:
            conn.rollback()
            return _bad(str(exc))
        A.logEvent(cur, "training_upload", sess["account"]["id"],
                   detail={"person_id": int(pid), "files": len(files), "added": added})
        conn.commit()
    return jsonify({"ok": True, "added": added, "duplicates": dup, "skipped": skipped})


@bp.route("/account/training/consent", methods=["POST"])
def training_consent():
    import accounts as A
    from flask import redirect
    sess, go = A._requireSession()
    if go:
        return go
    back = "/account/training"
    if not A.csrfOk(sess):
        return redirect(back + "?error=That+request+did+not+come+from+this+site."), 400
    if not researchEnabled():
        return redirect(back), 400
    on = request.form.get("research") == "1"
    with A._db() as (conn, cur):
        cur.execute("""INSERT INTO training_consent (account_id, research, version, updated_at)
                       VALUES (%s, %s, %s, now())
                       ON CONFLICT (account_id) DO UPDATE
                       SET research = EXCLUDED.research, version = EXCLUDED.version, updated_at = now()""",
                    (sess["account"]["id"], on, CONSENT_VERSION))
        A.logEvent(cur, "training_consent", sess["account"]["id"], detail={"research": on})
        conn.commit()
    return redirect(back + ("?notice=Thanks.+Your+training+can+now+help+improve+predictions."
                            if on else "?notice=Done.+Your+training+is+for+your+eyes+only."))


@bp.route("/account/training/delete", methods=["POST"])
def training_delete():
    import accounts as A
    from flask import redirect
    sess, go = A._requireSession()
    if go:
        return go
    back = "/account/training"
    if not A.csrfOk(sess):
        return redirect(back + "?error=That+request+did+not+come+from+this+site."), 400
    if (request.form.get("confirm") or "").strip().upper() != "DELETE":
        return redirect(back + "?error=Type+DELETE+to+confirm."), 400
    with A._db() as (conn, cur):
        cur.execute("DELETE FROM training_activity WHERE account_id = %s", (sess["account"]["id"],))
        n = cur.rowcount
        cur.execute("DELETE FROM training_consent WHERE account_id = %s", (sess["account"]["id"],))
        A.logEvent(cur, "training_deleted", sess["account"]["id"], detail={"runs": n})
        conn.commit()
    return redirect(back + f"?notice=Deleted+{n}+runs.")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--init", action="store_true", help="create the tables")
    ap.add_argument("--check", action="store_true", help="are they there")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        if a.init:
            initTables(conn)
            print("training tables ready")
        with conn.cursor() as cur:
            print(f"tables: {'ready' if tablesReady(cur) else 'NOT created (--init)'}; "
                  f"research opt-in: {'ON' if researchEnabled() else 'off (XCP_TRAINING_RESEARCH=1)'}")


if __name__ == "__main__":
    _here = os.path.dirname(os.path.abspath(__file__))
    for _p in (_here, os.path.join(os.path.dirname(_here), "scripts")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    main()
