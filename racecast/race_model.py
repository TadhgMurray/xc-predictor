"""
race_model.py -- the race model (model/race_trees.py) on the page: every
runner's time in one race, from the same inputs model/race_rows.py trains
on. OFF unless XCP_PREDICT_BASIS=race; the Kailo recorder records it beside
the other bases either way (scripts/kailo_compare.py BASES).

    preds = predictRace(cur, person_ids, spec, cut, rated)   # {pid: entry}

★ ONE DEFINITION OF EVERY INPUT. runnerFeatures and raceFeatures are
  imported from model/race_rows.py, not copied, so the page cannot ask the
  trees a question they were not trained on. base is the rating entry's own
  seconds (predict._ratingTimes), which is exactly race_rows.pageBase's
  clock.

★ WHAT COMES BACK. seconds = exp(mean base + level + the runner's centred
  part); sigma_pct = the place spread of the runner's band (what race_sim
  draws); lo/hi = the time range, place spread and race-level spread
  together -- both fitted on the held-out season (race_trees "RANGES").

! A FIELD THE TREES CANNOT READ IS LEFT TO THE RATINGS: no model file,
  fewer than MIN_FIELD rated runners, or anything raising. Never a partial
  field: every runner of one race is on one clock.
"""
import datetime
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DIR = os.path.join(_ROOT, "model")
if _MODEL_DIR not in sys.path:
    sys.path.insert(0, _MODEL_DIR)

MODEL_PATH = os.environ.get("XCP_RACE_MODEL") or os.path.join(_ROOT, "model", "data",
                                                               "race_trees.pkl")
MIN_FIELD = 3
_CACHE = {"mtime": None, "model": None}
_GRADES = ("FR", "SO", "JR", "SR")


def loadModel(path=None):
    """The fitted dict race_trees.py --out wrote, or None. Re-read when the
    file changes."""
    import pickle
    path = path or MODEL_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _CACHE["mtime"] != mtime:
        try:
            with open(path, "rb") as fh:
                _CACHE["model"] = pickle.load(fh)
        except Exception:                                # noqa: BLE001
            _CACHE["model"] = None
        _CACHE["mtime"] = mtime
    return _CACHE["model"]


def gradeAt(grade, then, day):
    """A grade recorded on `then`, carried to `day` by academic years.
    '10' two seasons on is '12'; '12' one season on is 'FR'; 'SO' on is
    'JR'. None for anything unreadable. Pure."""
    import race_rows as RR
    g = (grade or "").strip().upper()
    n = RR.gradeNum(g)
    if math.isnan(n) or then is None or day is None:
        return None
    n += RR.seasonOf(day) - RR.seasonOf(then)
    if n <= 12:
        return str(int(n))
    return _GRADES[min(int(n) - 13, 3)]


def _history(rows):
    """predict._ratingRows' rows (newest first, date text) -> race_rows'
    history (oldest first, dates)."""
    out = []
    for r in reversed(rows or []):
        try:
            d = datetime.date.fromisoformat(str(r[0])[:10])
        except ValueError:
            continue
        out.append((d, float(r[1]), r[2], r[3]))
    return out


def _latest(cur, ids, day):
    """{pid: (grade, school, date)} from each runner's newest cross country
    row before the day."""
    cur.execute("""
        SELECT DISTINCT ON (person_id) person_id, grade, school, left(date, 10)
        FROM   results
        WHERE  person_id = ANY(%s) AND date < %s
        ORDER  BY person_id, date DESC""", (sorted(ids), day.isoformat()))
    out = {}
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        try:
            out[row[0]] = (row[1], row[2], datetime.date.fromisoformat(row[3]))
        except (TypeError, ValueError):
            continue
    return out


def _pastRaces(cur, ids, cut):
    """{pid: [(raceHash, ln time)]}: each runner's last race_rows.H2H_LAST
    cross country races in the H2H_DAYS before the cut, as the table keeps
    them for head-to-head."""
    import race_rows as RR
    lo = (cut - datetime.timedelta(days=RR.H2H_DAYS)).isoformat()
    cur.execute("""
        SELECT person_id, meet_id, div_id, source, left(date, 10), time_seconds
        FROM   results
        WHERE  person_id = ANY(%s) AND date >= %s AND date < %s
          AND  time_seconds > 0 AND time_seconds < 19999
          AND  meet_id IS NOT NULL AND div_id IS NOT NULL
        ORDER  BY person_id, date""", (sorted(ids), lo, cut.isoformat()))
    out = {}
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        out.setdefault(row[0], []).append((RR.raceHash(row[1], row[2], row[3], row[4]),
                                           math.log(float(row[5]))))
    return {p: v[-RR.H2H_LAST:] for p, v in out.items()}


def _guarded(cur, name, fn, default=None, say=None):
    """fn() under its own savepoint: a failed statement here is rolled back
    to it and `default` comes back (with one line naming the error when
    `say` names the race). Never a whole rollback -- that would end the
    caller's transaction and its savepoints (upcoming_preview's up_pred,
    the page's request)."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except Exception as exc:                             # noqa: BLE001
        if say:
            print(f"race model {say}: {type(exc).__name__}: {exc}", flush=True)
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        return default
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return out


def _weather(cur, spec):
    """The race's weather as the table carries it: the forecast at the cross
    country hour when there is one, else the normal for the place and date.
    ! The forecast is asked WITHOUT cur: forecast.py's table cache commits,
    which would end the caller's transaction (savepoint "up_pred" does not
    exist). The in-process cache still holds each fetch."""
    import predict as P
    fc = P._fc()
    hour = fc.raceHour(spec.get("sport"))
    lat, lon, day = spec.get("gps_lat"), spec.get("gps_long"), spec.get("date")
    try:
        row = fc.forecastAt(lat, lon, day, hour)
    except Exception:                                    # noqa: BLE001
        row = None
    return row or _guarded(cur, "race_wx", lambda: fc.normalAt(cur, lat, lon, day, hour))


def _division(cur, spec):
    """The race's division title (race_rows.divType reads it), or None."""
    import predict as P
    return _guarded(cur, "race_div", lambda: P._divisionLabel(
        cur, spec.get("meet_id"), spec.get("div_id"), "XC", spec.get("source")))


def _meetFacts(cur, spec):
    """(meet name, is_champ) of the target meet; (None, False) unknown."""
    mid, src = spec.get("meet_id"), spec.get("source")
    if not mid:
        return None, False
    if src == "tfrrs":
        cur.execute("SELECT meet_name, COALESCE(is_championship, 0) FROM meets_tfrrs "
                    "WHERE meet_id = %s AND sport = 'XC' LIMIT 1", (mid,))
    else:
        cur.execute("SELECT meet_name, 0 FROM meets WHERE meet_id = %s LIMIT 1", (mid,))
    row = cur.fetchone()
    if not row:
        return None, False
    row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    return row[0], bool(row[1])


def _targetPool(form_pool, spec):
    """The runner's pool in THIS race: a college race makes a high-school
    form a first college season (level_change, as race_rows reads it)."""
    import race_rows as RR
    if spec.get("source") == "tfrrs" and RR.levelOf(form_pool) < 3:
        sex = "f" if (form_pool or "").endswith("_f") else "m"
        return f"college_{sex}"
    return form_pool


def predictField(model, field, race):
    """{pid: entry} for one field already carrying runner inputs. Pure but
    for numpy and the fitted trees.

    field: [{"pid", "base_ln", "school", "f": runner inputs}]
    race:  race_rows.raceFeatures' race dict (distance, day, cell, ...)"""
    import numpy as np
    import race_rows as RR
    import race_trees as RT
    for r in field:
        r.setdefault("secs", math.exp(r["base_ln"]))     # raceFeatures' y; unused
    RR.raceFeatures(field, race)
    # ★ EACH RUNNER'S OWN RECORD AGAINST THE RATINGS (race_trees OWN_FEATURES),
    #   carried from the table's last day to this race; a runner the table
    #   never saw reads as "no record", which is what the trees learned for
    #   a first race. ! As fresh as the last table build: rebuild weekly.
    import zlib
    lead = int(race.get("lead_days") or 0)
    asof = race["day"].toordinal() - lead            # what is known: today
    for name, feats, key in (("own", RT.OWN_FEATURES, lambda r: r["pid"]),
                             ("team", RT.TEAM_FEATURES,
                              lambda r: zlib.crc32(r["school"].encode()) if r.get("school") else None)):
        pack = model.get(name)
        if pack is None:
            continue
        if f"_{name}_index" not in model:
            model[f"_{name}_index"] = {int(p): i for i, p in enumerate(pack["pid"])}
        for r in field:
            k = key(r)
            st = RT.ownState(pack, model[f"_{name}_index"], k) if k is not None else None
            vals = RT.ownFeatures(st, asof)
            if name == "team" and k is None:
                vals = [math.nan] * len(feats)
            r["f"].update(zip(feats, vals))
    # ★ RECORDS IN CONTEXT (race_trees CTX): this course, its difficulty, the
    #   distance, the heat -- the same keys the table was built with
    packs = model.get("ctx") or {}
    if packs:
        wx = race.get("weather") or {}
        cid = race.get("canonical_id")
        for r in field:
            keys = RT.ctxKeys([r["pid"]], [int(cid) if cid is not None else -1],
                              [race.get("cell") if race.get("cell") is not None else math.nan],
                              [1.0 if race.get("cell") is not None else 0.0],
                              [race.get("distance") or math.nan],
                              [wx.get("temp_c") if wx.get("temp_c") is not None else math.nan])
            for fam in RT.CTX:
                vals = RT.ctxLookup(packs.get(fam), int(keys[fam][0]))
                r["f"].update(zip((f"{fam}_{x}" for x in RT.CTX_STATS), vals))
    # ★ THE COURSE'S OWN LEVEL RECORD (race_trees CLV): one value for the field
    if model.get("course_level") is not None:
        cid = race.get("canonical_id")
        key = RT.courseLevelKey([int(cid) if cid is not None else -1],
                                [race.get("distance") or math.nan])
        vals = RT.ctxLookup(model["course_level"], int(key[0]))
        for r in field:
            r["f"].update(zip(RT.CLV_FEATURES, vals))
    X = np.array([[r["f"].get(k, math.nan) for k in model["features"]] for r in field],
                 dtype=np.float32)
    base = np.array([r["base"] for r in field])
    rel = base + model["runner"].predict(X)
    rel -= rel.mean()
    lf = model.get("level_features") or RT.LEVEL_FEATURES
    lrow = np.array([[float(np.nanmean([r["f"].get(k, math.nan) for r in field]))
                      if any(not math.isnan(r["f"].get(k, math.nan)) for r in field)
                      else math.nan for k in lf]], dtype=np.float32)
    level = float(model["level"].predict(lrow)[0])
    mean_base = float(np.mean([r["base_ln"] for r in field]))
    by_lead = model.get("sigmas_by_lead") or {}
    sig = (by_lead[min(by_lead, key=lambda ld: abs(ld - lead))] if by_lead
           else model.get("sigmas") or {})
    out = {}
    for r, x in zip(field, rel):
        band = RT.bandOf(r["f"]["opener"], r["f"]["last_days"])
        ps = float(sig.get(band, sig.get("_all", 0.03)))
        ts = math.sqrt(ps * ps + float(sig.get("_level", 0.0)) ** 2)
        secs = math.exp(mean_base + level + float(x))
        out[r["pid"]] = {"seconds": round(secs, 1), "lo": round(secs * math.exp(-ts), 1),
                         "hi": round(secs * math.exp(ts), 1),
                         "sigma_pct": round(100.0 * ps, 2),
                         "time_sigma_pct": round(100.0 * ts, 2),
                         "is_race_time": True, "basis": "race", "time_basis": "race",
                         "race_band": band, "race_level_pct": round(100.0 * level, 2)}
    return out


def predictRace(cur, person_ids, spec, cut, rated):
    """{pid: entry} from the race model for the rated runners of one race,
    or {} when the trees cannot read the field (see the header). Its reads
    sit under one savepoint, so a failure leaves the caller's transaction
    as it was."""
    model = loadModel()
    if model is None or not spec or not spec.get("distance_meters"):
        return {}
    say = f"{spec.get('meet_id')}/{spec.get('div_id')}"
    try:
        return _guarded(cur, "race_model",
                        lambda: _predictRace(cur, model, person_ids, spec, cut, rated),
                        default={}, say=say)
    except Exception as exc:                             # noqa: BLE001
        print(f"race model {say}: {type(exc).__name__}: {exc}", flush=True)
        return {}


def _predictRace(cur, model, person_ids, spec, cut, rated):
    import race_rows as RR
    import predict as P
    day = P._asDate(spec.get("date")) or cut or datetime.date.today()
    ids = [p for p in person_ids if (rated.get(p) or {}).get("seconds")]
    if len(ids) < MIN_FIELD:
        return {}
    cut = cut or day
    rows = P._ratingRows(cur, ids, None, cut.isoformat())
    latest = _latest(cur, ids, cut)
    past = _pastRaces(cur, ids, cut)
    name, champ = _meetFacts(cur, spec)
    field = []
    for pid in ids:
        e = rated[pid]
        g, school, then = latest.get(pid, (None, None, None))
        got = RR.runnerFeatures(_history(rows.get(pid)), day,
                                {"pool": _targetPool(e.get("form_pool"), spec),
                                 "grade": gradeAt(g, then, day)})
        if got is None:
            continue
        field.append({"pid": pid, "school": school, "base_ln": math.log(float(e["seconds"])),
                      "f": got[2], "past": past.get(pid) or []})
    if len(field) < MIN_FIELD:
        return {}
    race = {"distance": float(spec["distance_meters"]), "day": day, "meet_name": name,
            "canonical_id": spec.get("canonical_id"),
            "division": _division(cur, spec),
            "source": spec.get("source"), "is_champ": champ, "cell": spec.get("venue_cell"),
            "page_base": True, "weather": _weather(cur, spec),
            # how far ahead this is asked: the forecast copies' lead_days
            "lead_days": max(0, (day - datetime.date.today()).days)}
    return predictField(model, field, race)
