# Project: xc-predictor / racecast
# File:    meet_forecast.py
# Purpose: "What we said": each posted cross country race's prediction,
#          stored before the meet runs, so the recap after it is scored
#          against the forecast people actually saw (owner, 2026-10-10,
#          approved: the weekly "how we did" loop).
#
#     meet_forecast          one row per (source, meet_id, div_id)
#     forecastAction(...)    the freeze rule: write, refresh, or leave alone
#     saveRace / loadRace    the write (build_meet_forecasts.py) and the read
#                            (upcoming_preview.racePrediction, the recap)
#
# ★ THE PREDICTION IS upcoming_preview's, STORED. The rows hold exactly what
#   racePrediction returns for a race (its _trim: the whole projected field
#   in order with times and bands, the team scores with their scorers), so
#   the preview page reads a stored race the way it reads a live one, and
#   the recap reads the same numbers the preview showed.
#
# ★ THE FREEZE IS THE DAY BEFORE. A race is written the first night it is
#   on the calendar, rewritten once more by the last nightly run before the
#   meet's day (the run with every result through the day before it priced),
#   and never again: not on the meet's day, not after, and not once results
#   for it exist. The rule is forecastAction below, and the upsert repeats
#   it in SQL so no caller can write past it.
#
# ! NOT SWAPPED. This table accumulates: a stored row is the record of what
#   was said, and the pipeline's rebuilds never touch it.
import datetime
import json
import os

TABLE = "meet_forecast"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    source          text        NOT NULL,
    meet_id         bigint      NOT NULL,
    div_id          bigint      NOT NULL,
    meet_name       text,
    meet_date       date        NOT NULL,
    state           text,
    venue           text,
    race_label      text,
    gender          text,
    distance        real,
    edition_meet_id bigint,
    edition_date    date,
    n_field         int,
    runners         jsonb       NOT NULL,
    teams           jsonb       NOT NULL,
    model_basis     text,
    model_version   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    made_on         date        NOT NULL,
    score           jsonb,
    scored_at       timestamptz,
    PRIMARY KEY (source, meet_id, div_id)
);
CREATE INDEX IF NOT EXISTS {TABLE}_date ON {TABLE} (meet_date, state);
"""

# ★ "THE NIGHT BEFORE" IS ONE DAY. The nightly update runs once a day,
#   before dawn (deploy/install_nightly_timer.sh); the run dated the day
#   before a meet is the last one that finishes before any of its races can
#   start, wherever in the country it is.
REFRESH_LEAD_DAYS = 1


def _day(v):
    """A date from a date, a datetime or an ISO string; None otherwise."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def forecastAction(meet_date, today, made_on=None, has_results=False):
    """'new', 'refresh' or None for one race.

        new      no forecast stored, and the meet is still ahead
        refresh  stored on an earlier day, and tonight is the night before
        None     anything else -- above all, never on or after the meet's
                 day and never once a result for the race is in"""
    meet_date, today, made_on = _day(meet_date), _day(today), _day(made_on)
    if meet_date is None or today is None or meet_date <= today or has_results:
        return None
    if made_on is None:
        return "new"
    if made_on < today and (meet_date - today).days <= REFRESH_LEAD_DAYS:
        return "refresh"
    return None


def modelVersion():
    """'model.pt 2026-10-08 03:12' -- the checkpoint the times came from,
    named by its file and when it was written; None with no checkpoint (the
    rating basis then predicts alone, and model_basis says so)."""
    try:
        from predict import MODEL_PATH
        st = os.stat(MODEL_PATH)
    except (ImportError, OSError):
        return None
    when = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
    return f"{os.path.basename(MODEL_PATH)} {when:%Y-%m-%d %H:%M}"


def modelBasis():
    try:
        from predict import _predictBasis
        return _predictBasis()
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------------ #
#  writing (build_meet_forecasts.py)
# ------------------------------------------------------------------ #

def ensureTable(cur):
    cur.execute(DDL)


def existing(cur, source, meet_id):
    """{div_id: made_on} of the races of one meet already stored."""
    cur.execute(f"""SELECT div_id, made_on FROM {TABLE}
                    WHERE source = %s AND meet_id = %s""", (source, int(meet_id)))
    return {int(r["div_id"]): _day(r["made_on"]) for r in cur.fetchall()}


def racesWithResults(cur, source, meet_id):
    """The div_ids of a meet that already have a result row."""
    cur.execute("""SELECT DISTINCT div_id FROM results
                   WHERE meet_id = %s AND source = %s""", (int(meet_id), source))
    return {int(r["div_id"]) for r in cur.fetchall()}


def saveRace(cur, meet, race, pred, today):
    """Upsert one race's forecast. `meet` is the plan (name, date, venue,
    state, source, edition), `race` one of its races, `pred` racePrediction's
    available answer. Returns True when a row was written.

    ! THE FREEZE AGAIN, IN SQL: an existing row is only replaced while its
      meet is still ahead of `today` and it has not been scored."""
    from psycopg2.extras import Json
    ed = meet.get("edition") or {}
    p = {"source": meet["source"], "meet_id": int(meet["meet_id"]),
         "div_id": int(race["div_id"]), "meet_name": meet.get("name"),
         "meet_date": str(meet["date"])[:10], "state": meet.get("state"),
         "venue": meet.get("venue"), "race_label": race.get("label"),
         "gender": race.get("gender"), "distance": race.get("distance"),
         "edition_meet_id": ed.get("meet_id"), "edition_date": ed.get("date") or None,
         "n_field": pred.get("n_field"),
         "runners": Json(pred.get("runners") or []), "teams": Json(pred.get("teams") or []),
         "model_basis": modelBasis(), "model_version": modelVersion(),
         "today": _day(today).isoformat()}
    cur.execute(f"""
        INSERT INTO {TABLE} (source, meet_id, div_id, meet_name, meet_date, state, venue,
                             race_label, gender, distance, edition_meet_id, edition_date,
                             n_field, runners, teams, model_basis, model_version, made_on)
        SELECT %(source)s, %(meet_id)s, %(div_id)s, %(meet_name)s, %(meet_date)s::date, %(state)s,
               %(venue)s, %(race_label)s, %(gender)s, %(distance)s, %(edition_meet_id)s,
               %(edition_date)s::date, %(n_field)s, %(runners)s, %(teams)s, %(model_basis)s,
               %(model_version)s, %(today)s::date
        WHERE  %(meet_date)s::date > %(today)s::date
        ON CONFLICT (source, meet_id, div_id) DO UPDATE SET
            meet_name = EXCLUDED.meet_name, meet_date = EXCLUDED.meet_date,
            state = EXCLUDED.state, venue = EXCLUDED.venue,
            race_label = EXCLUDED.race_label, gender = EXCLUDED.gender,
            distance = EXCLUDED.distance, edition_meet_id = EXCLUDED.edition_meet_id,
            edition_date = EXCLUDED.edition_date, n_field = EXCLUDED.n_field,
            runners = EXCLUDED.runners, teams = EXCLUDED.teams,
            model_basis = EXCLUDED.model_basis, model_version = EXCLUDED.model_version,
            made_on = EXCLUDED.made_on, updated_at = now()
        WHERE {TABLE}.meet_date > %(today)s::date
          AND EXCLUDED.meet_date > %(today)s::date
          AND {TABLE}.score IS NULL
          AND {TABLE}.made_on < %(today)s::date
    """, p)
    return cur.rowcount != 0


# ------------------------------------------------------------------ #
#  reading
# ------------------------------------------------------------------ #

_READY = {"at": 0.0, "ok": False}
_READY_TTL = 3600.0


def tableReady(cur):
    """Does the table exist yet? Checked once an hour per worker: a server
    whose pipeline has not run the step reads as "nothing stored"."""
    import time
    if time.time() - _READY["at"] < _READY_TTL:
        return _READY["ok"]
    try:
        cur.execute("SAVEPOINT mf_ready")
        cur.execute("SELECT to_regclass(%s) AS t", (f"public.{TABLE}",))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT mf_ready")
        v = row.get("t") if isinstance(row, dict) else (row[0] if row else None)
        _READY["ok"] = v is not None
    except Exception:                                   # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT mf_ready")
        except Exception:                               # noqa: BLE001
            pass
        _READY["ok"] = False
    _READY["at"] = time.time()
    return _READY["ok"]


def _src(source):
    """None reads anet, as upcomingMeet reads it first."""
    return "tfrrs" if source == "tfrrs" else "anet"


def _load(v):
    if isinstance(v, (bytes, str)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _rowOut(r):
    r = dict(r)
    for k in ("runners", "teams", "score"):
        r[k] = _load(r.get(k))
    for k in ("meet_date", "edition_date", "made_on"):
        d = _day(r.get(k))
        r[k] = d.isoformat() if d else None
    return r


_COLS = """source, meet_id, div_id, meet_name, meet_date, state, venue, race_label,
           gender, distance, edition_meet_id, edition_date, n_field, runners, teams,
           model_basis, model_version, created_at, updated_at, made_on, score, scored_at"""


def _safe(cur, sql, p):
    """fetchall of a read that must never fail a page."""
    if not tableReady(cur):
        return []
    try:
        cur.execute("SAVEPOINT mf_read")
        cur.execute(sql, p)
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT mf_read")
        return [_rowOut(r) for r in rows if isinstance(r, dict) and "div_id" in r]
    except Exception as exc:                            # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT mf_read")
        except Exception:                               # noqa: BLE001
            pass
        print(f"meet forecast read: {type(exc).__name__}: {exc}", flush=True)
        return []


def loadMeet(cur, meet_id, source=None):
    """Every stored race of one meet, by div_id."""
    return _safe(cur, f"""SELECT {_COLS} FROM {TABLE}
                          WHERE source = %s AND meet_id = %s ORDER BY div_id""",
                 (_src(source), int(meet_id)))


def loadRace(cur, meet_id, div_id, source=None):
    """One stored race in racePrediction's shape ({"available": True,
    "teams", "runners", "n_field"} plus "frozen": when it was made), or
    None when nothing is stored."""
    rows = _safe(cur, f"""SELECT {_COLS} FROM {TABLE}
                          WHERE source = %s AND meet_id = %s AND div_id = %s""",
                 (_src(source), int(meet_id), int(div_id)))
    return asPrediction(rows[0]) if rows else None


def asPrediction(row):
    return {"available": True, "teams": row.get("teams") or [],
            "runners": row.get("runners") or [],
            "n_field": row.get("n_field") or len(row.get("runners") or []),
            "target_source": row.get("source"),
            "frozen": {"made_on": row.get("made_on"),
                       "model_basis": row.get("model_basis"),
                       "model_version": row.get("model_version")}}


def weekRows(cur, lo, hi, state=None):
    """The stored races dated lo..hi (inclusive), one state or all."""
    return _safe(cur, f"""SELECT {_COLS} FROM {TABLE}
                          WHERE meet_date BETWEEN %(lo)s AND %(hi)s
                            AND (%(st)s::text IS NULL OR state = %(st)s)
                          ORDER BY meet_date, meet_name, meet_id, div_id""",
                 {"lo": str(lo), "hi": str(hi), "st": state})


def latestScoredDate(cur):
    if not tableReady(cur):
        return None
    try:
        cur.execute("SAVEPOINT mf_last")
        cur.execute(f"SELECT max(meet_date) AS d FROM {TABLE} WHERE score IS NOT NULL")
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT mf_last")
    except Exception:                                   # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT mf_last")
        except Exception:                               # noqa: BLE001
            pass
        return None
    return _day(row.get("d")) if isinstance(row, dict) else None


def recapKeys(cur, today):
    """{(source, meet_id)} of the meets with a stored forecast whose day has
    come: the ones a meet page, the preview and the home page link to a
    recap from."""
    if not tableReady(cur):
        return set()
    try:
        cur.execute("SAVEPOINT mf_keys")
        cur.execute(f"""SELECT DISTINCT source, meet_id FROM {TABLE}
                        WHERE meet_date <= %s""", (str(today),))
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT mf_keys")
    except Exception:                                   # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT mf_keys")
        except Exception:                               # noqa: BLE001
            pass
        return set()
    return {(r["source"], int(r["meet_id"])) for r in rows
            if isinstance(r, dict) and "meet_id" in r}


# ------------------------------------------------------------------ #
#  scoring (build_meet_forecasts.py, after the meet)
# ------------------------------------------------------------------ #

def toScore(cur, today, window_days):
    """(source, meet_id) of the meets to score tonight: every stored race
    already run (meet_date < today) that was never scored, and every one
    run in the last `window_days` (late results and corrections arrive
    through the week after)."""
    cur.execute(f"""
        SELECT DISTINCT source, meet_id FROM {TABLE}
        WHERE  meet_date < %(t)s::date
          AND  (score IS NULL OR meet_date >= %(t)s::date - %(w)s::int)
    """, {"t": str(today), "w": int(window_days)})
    return [(r["source"], int(r["meet_id"])) for r in cur.fetchall()]


def saveScore(cur, source, meet_id, div_id, score):
    from psycopg2.extras import Json
    cur.execute(f"""UPDATE {TABLE} SET score = %s, scored_at = now()
                    WHERE source = %s AND meet_id = %s AND div_id = %s""",
                (Json(score), source, int(meet_id), int(div_id)))
