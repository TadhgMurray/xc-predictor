# Project: xc-predictor / racecast
# File:    meet_track.py
# Purpose: The model's track record AT THIS MEET, on the predictions page
#          (owner, 2026-10-10, item 14): "On past editions of this meet the
#          model picked the winner 4 of 6 times; median miss 9.8 s."
#
#     GET /api/predict/track_record?meet_id=275685&sport=XC[&alt=1][&src=anet]
#
# ★ PRECOMPUTED, NEVER RUN ON A VIEW. Each past race is backtested ONCE by
#   racecast/build_meet_track.py -- the as-ran backtest of
#   scripts/backtest_predictions.py (target rerun_exact, every runner's
#   history cut at the race), through the SERVED path (_servedTimes, the
#   basis the page uses) -- and stored as one row per race in
#   meet_track_race. This module only reads those rows and sums them.
#
# ★ "THIS MEET" IS ITS EDITIONS, BY last_edition's RULE. A meet's editions
#   share a normalised name (last_edition.editionName: no years, no
#   ordinals, no punctuation) within one feed. And one state, too: a generic
#   name -- "Conference Championship" -- is a different meet in forty
#   states, and pooling them would score the wrong meet. The key is
#   "<edition name>|<state>".
#
# ! CROSS COUNTRY ONLY. The backtest machinery (and the predictor's course
#   model) is XC; a track meet gets {"available": false} and the page says
#   nothing.
import statistics

from flask import Blueprint, jsonify, request

import ttlcache

bp = Blueprint("meet_track", __name__)

TABLE = "meet_track_race"
_TTL = 3600.0

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    meet_id        bigint  NOT NULL,
    div_id         bigint  NOT NULL,
    source         text    NOT NULL,
    sport          text    NOT NULL DEFAULT 'XC',
    edition_key    text    NOT NULL,
    meet_name      text,
    race_date      date,
    n_runners      int,
    n_predicted    int,
    winner_actual  bigint,
    winner_pred    bigint,
    picked         boolean,
    abs_err        real[],
    median_err     real,
    median_err_pct real,
    basis          text,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meet_id, div_id, source)
);
CREATE INDEX IF NOT EXISTS {TABLE}_edition
    ON {TABLE} (source, edition_key, race_date);
"""


def editionKey(name, state):
    """'<normalised name>|<STATE>' -- the editions of one meet."""
    from last_edition import editionName
    return f"{editionName(name)}|{(state or '').strip().upper()}"


# ------------------------------------------------------------------ #
#  scoring one race (pure; the build script calls it)
# ------------------------------------------------------------------ #

def scoreRace(actual, predicted):
    """actual {pid: seconds} (everyone who finished), predicted {pid:
    seconds or None} -> the stored facts, or None when nothing was
    predicted.

    ! THE ACTUAL WINNER IS THE FIELD'S, NOT THE PREDICTED SUBSET'S: a winner
      the model had no time for is a miss, not a race left out."""
    pred = {p: float(s) for p, s in (predicted or {}).items()
            if s is not None and p in actual}
    if not pred or not actual:
        return None
    win_a = min(actual, key=lambda p: (actual[p], p))
    win_p = min(pred, key=lambda p: (pred[p], p))
    errs = [abs(pred[p] - float(actual[p])) for p in pred]
    pcts = [100.0 * abs(pred[p] - float(actual[p])) / float(actual[p])
            for p in pred if actual[p]]
    return {"n_runners": len(actual), "n_predicted": len(pred),
            "winner_actual": win_a, "winner_pred": win_p,
            "picked": win_a == win_p,
            "abs_err": [round(e, 1) for e in errs],
            "median_err": round(statistics.median(errs), 1),
            "median_err_pct": round(statistics.median(pcts), 2) if pcts else None}


# ------------------------------------------------------------------ #
#  reading it back
# ------------------------------------------------------------------ #

def summarize(rows):
    """Past races' rows -> {editions, first, last, races, picked,
    median_err, median_err_pct} or None. The median miss is over every
    RUNNER of every edition (the stored per-runner errors), not a median
    of race medians: a race of 300 counts for more than a race of 20."""
    rows = [r for r in rows or [] if r.get("picked") is not None]
    if not rows:
        return None
    errs = [float(e) for r in rows for e in (r.get("abs_err") or [])]
    years = sorted({str(r["race_date"])[:4] for r in rows if r.get("race_date")})
    pcts = [float(r["median_err_pct"]) for r in rows
            if r.get("median_err_pct") is not None]
    return {"editions": len({r["meet_id"] for r in rows}),
            "first": years[0] if years else None,
            "last": years[-1] if years else None,
            "races": len(rows),
            "picked": sum(1 for r in rows if r["picked"]),
            "median_err": round(statistics.median(errs), 1) if errs else None,
            "median_err_pct": round(statistics.median(pcts), 1) if pcts else None,
            "runners": len(errs)}


def sentence(s):
    """The page's one line."""
    if not s:
        return None
    span = (s["first"] if s["first"] == s["last"]
            else f"{s['first']} to {s['last']}") if s.get("first") else None
    eds = f"{s['editions']} past edition{'s' if s['editions'] != 1 else ''}"
    races = f"{s['races']} race{'s' if s['races'] != 1 else ''}"
    out = (f"On {eds} of this meet ({span + ', ' if span else ''}{races}) the model "
           f"picked the winner {s['picked']} of {s['races']} times")
    if s.get("median_err") is not None:
        out += f"; median miss {s['median_err']:.1f} s"
        if s.get("median_err_pct") is not None:
            out += f" ({s['median_err_pct']:.1f}%)"
    return out + "."


_TABLE_READY = {"checked": 0.0, "ready": False}


def _tableReady(cur):
    import time
    if time.time() - _TABLE_READY["checked"] > _TTL:
        try:
            cur.execute("SELECT to_regclass(%s) AS t", (f"public.{TABLE}",))
            row = cur.fetchone()
            v = row["t"] if isinstance(row, dict) else row[0]
            _TABLE_READY["ready"] = v is not None
        except Exception:                               # noqa: BLE001
            cur.connection.rollback()
            _TABLE_READY["ready"] = False
        _TABLE_READY["checked"] = time.time()
    return _TABLE_READY["ready"]


def pastRows(cur, source, key, before, exclude_meet=None):
    cur.execute(f"""
        SELECT meet_id, div_id, race_date, picked, abs_err, median_err,
               median_err_pct
        FROM   {TABLE}
        WHERE  source = %(src)s AND edition_key = %(key)s
          AND  (%(before)s::date IS NULL OR race_date < %(before)s::date)
          AND  (%(ex)s::bigint IS NULL OR meet_id <> %(ex)s)
        ORDER  BY race_date DESC
    """, {"src": source, "key": key, "before": before, "ex": exclude_meet})
    return [dict(r) for r in cur.fetchall()]


def trackRecord(cur, meet_id, source):
    """{available, ...summary, text} for one XC meet in one feed."""
    from last_edition import upcomingMeet
    if not _tableReady(cur):
        return {"available": False, "reason": "not built"}
    up = upcomingMeet(cur, int(meet_id), "XC", source)
    if not up or not up.get("name"):
        return {"available": False, "reason": "unknown meet"}
    key = editionKey(up["name"], up.get("state"))
    before = str(up["date"])[:10] if up.get("date") else None
    s = summarize(pastRows(cur, up["source"], key, before, int(meet_id)))
    if not s:
        return {"available": False, "reason": "no past editions scored"}
    return dict(s, available=True, text=sentence(s))


def _resolveSource(cur, meet_id, alt, src):
    """The feed the predictions page reads this meet from: its own ?alt= /
    ?src= rule (app._predictSourceFor), imported late -- app imports this
    module. None when it cannot be resolved; upcomingMeet then reads anet
    first, as everywhere else."""
    try:
        import app as A
        source, _idx = A._predictSourceFor(cur, int(meet_id), "XC", alt, None, src)
        return source
    except Exception:                                   # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                               # noqa: BLE001
            pass
        return src if src in ("anet", "tfrrs") else None


@bp.route("/api/predict/track_record")
def api_track_record():
    raw = (request.args.get("meet_id") or "").strip()
    sport = (request.args.get("sport") or "XC").strip().upper()
    if not raw.isdigit():
        return jsonify({"error": "meet_id is required"}), 400
    if sport != "XC":
        return jsonify({"available": False, "reason": "cross country only"})
    alt = (request.args.get("alt") or "").strip() or None
    src = (request.args.get("src") or "").strip().lower() or None

    def compute():
        import psycopg2.extras
        from database import getConn
        with getConn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                source = _resolveSource(cur, raw, alt, src)
                out = trackRecord(cur, raw, source)
                conn.rollback()
                return out
    try:
        val, _stamp = ttlcache.get(("track-record", int(raw), alt, src), compute,
                                   ttl=_TTL)
    except Exception as exc:                            # noqa: BLE001
        # ! A LINE, NOT A PAGE: a failure is "nothing to say", never a 500
        print(f"track_record {raw}: {type(exc).__name__}: {exc}", flush=True)
        val = {"available": False, "reason": "unavailable"}
    resp = jsonify(val)
    resp.headers["Cache-Control"] = "public, max-age=600"
    return resp
