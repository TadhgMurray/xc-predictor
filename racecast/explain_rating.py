# Project: xc-predictor / racecast
# File:    explain_rating.py
# Purpose: "How was this rated?" -- one stored result taken apart into the
#          terms the engine applied to it, each as a percent of time and in
#          rating points, with a plain-English line. Served as JSON by
#          /api/explain/<sport>/<result_id> (app.py) and drawn by
#          static/rating-explain.js as a popover on the athlete and race pages.
#
# ★ WHY (owner, 2026-10-04: Isaiah Hammerand's WashU Distance Carnival 10k,
#   29:20.48, rated 126.3 -- a +7.03% weather credit for afternoon heat on a
#   race that ran at night -- while his 29:27 at NCAA rated 117.6, and
#   "nobody could see why from the site"). The answer existed only in two
#   server-side scripts (scripts/diag_track_conversion.py and
#   scripts/diag_row_weather.py), for track only, read by the person who
#   wrote them. This is the same arithmetic, for both sports, for anyone.
#
# THE CHAIN (all logs; + = a CREDIT to the runner, the rating goes up):
#
#     rating = 100 * pool_mean / adjusted
#     adjusted = normalized_time / exp(effect)
#     normalized_time = time * factor(distance, era, geometry) / wmult
#     effect = tilt * (log1p(difficulty) + anchor_shift)   the course, this era
#            + tilt * clip(day)          only for the sports the go-live named
#            + event offset + sport gain                    track only
#            + altitude, leave-one-out day ...              (what is left over)
#
#   so log(rating) is an exact SUM, and each term below is one addend. The
#   waterfall starts from the time converted for distance alone (on an
#   average outdoor track, where the anchor shift lives) and walks to the
#   stored rating; "other" is whatever the site cannot separate, so the
#   steps always land on the number the page shows.
#
# ! NOTHING HERE RE-DERIVES THE ENGINE. The factors come from
#   normalize_distance (factorForTime, _applyWeather, weatherTempAgg,
#   isRaceWeatherPlausible), the scale terms from conversions (engineScale,
#   _tilt, distance_offset, sport_gain, pool_mean), the weather window from
#   the artifact, and the grid query is backfill_normalize's own expression.
#
# ⚠ EVERY PIECE MAY BE MISSING, and a missing piece is a step that says
#   "not available", never a 500: a table not yet written by the go-live, a
#   meet with no GPS, a tfrrs row with no geometry stamp, an artifact that
#   is not on this machine. Each lookup is its own try.

import math
import os
import time

# ------------------------------------------------------------------ #
#  formatting
# ------------------------------------------------------------------ #

POOL_WORDS = {
    "hs_m": "high-school boys", "hs_f": "high-school girls",
    "college_m": "college men", "college_f": "college women",
    "ms_m": "middle-school boys", "ms_f": "middle-school girls",
}

_SPORT_WORDS = {"TF": "Track", "XC": "Cross country"}

# a term smaller than this (as a log) is "no adjustment" in the prose
_TINY = 0.0005


def clock(sec, places=2):
    """'29:20.48' / '1:02:03.4' for seconds; None for nothing."""
    if sec is None:
        return None
    sec = float(sec)
    if sec <= 0 or not math.isfinite(sec):
        return None
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    fmt = f"{{:0{3 + places}.{places}f}}" if places else "{:02.0f}"
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{fmt.format(s)}"
    return f"{int(m)}:{fmt.format(s)}"


def pctOf(x):
    """A log term as a percent of time: 100 * (e^x - 1)."""
    return None if x is None else 100.0 * math.expm1(x)


def _p(x, places=1):
    """'+7.0%' -- a log term in words."""
    v = pctOf(x)
    return "0.0%" if v is None or abs(v) < 0.05 else f"{v:+.{places}f}%"


def _a(x, places=1):
    """'7.0%' -- the size of a log term, direction left to the sentence."""
    v = pctOf(x)
    return f"{abs(v or 0.0):.{places}f}%"


def _m(meters):
    if not meters:
        return "?"
    v = float(meters)
    return f"{v:,.0f} m" if abs(v - round(v)) < 0.05 else f"{v:,.1f} m"


def tempWords(c):
    """'33°C (91°F)'."""
    if c is None:
        return None
    return f"{float(c):.0f}°C ({float(c) * 9 / 5 + 32:.0f}°F)"


def hourWords(h):
    """'3pm' for a local hour 0-23."""
    h = int(h) % 24
    return "12am" if h == 0 else "12pm" if h == 12 else (
        f"{h}am" if h < 12 else f"{h - 12}pm")


# ------------------------------------------------------------------ #
#  WEATHER  -- the grid, aggregated as the backfill aggregated it
# ------------------------------------------------------------------ #

_GRID = 0.25                      # backfill_normalize._WX_GRID


def snapCell(lat, lon):
    """backfill_normalize._snapCell: snap to the grid, THEN wrap the
    longitude into 0..360, rounded to 2 dp so the float key matches."""
    if lat is None or lon is None:
        return None
    clat = round(round(float(lat) / _GRID) * _GRID, 2)
    clon = round((round(float(lon) / _GRID) * _GRID) % 360, 2)
    return clat, clon


# ! THE BACKFILL'S OWN LOCAL-HOUR EXPRESSION (backfill_normalize._wxSql),
#   evaluated in SQL so Postgres's rounding of a half-hour longitude is
#   the one the stored rows saw -- Python's round() halves to even.
_LOCAL = ("mod(mod(hour + round((CASE WHEN cell_lon > 180 THEN cell_lon - 360 "
          "ELSE cell_lon END) / 15.0)::int, 24) + 24, 24)")

WEATHER_DAY_SQL = f"""
    SELECT {_LOCAL} AS local_hour, apparent_temperature, wind_speed_10m,
           precipitation, soil_moisture, snow_depth, snowfall
    FROM   weather_grid
    WHERE  cell_lat = %(clat)s AND cell_lon = %(clon)s AND date = %(day)s
    ORDER  BY 1
"""


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def aggregateWeather(rows, hours, temp_agg="avg(apparent_temperature)"):
    """The race-window summary from a day's hourly grid rows, exactly as
    backfill_normalize._wxSql aggregates it: the temperature by the
    artifact's own aggregate (avg for XC's morning, max for track's day),
    wind and soil averaged, precipitation summed, snow = mean depth plus
    total snowfall. rows: dicts with local_hour, apparent_temperature,
    wind_speed_10m, precipitation, soil_moisture, snow_depth, snowfall.
    Returns (weather dict for _applyWeather | None, summary for the page)."""
    lo, hi = hours
    win = [r for r in rows
           if r.get("local_hour") is not None and lo <= int(r["local_hour"]) <= hi]
    if not win:
        return None, None

    def col(name):
        return [v for v in (_num(r.get(name)) for r in win) if v is not None]

    def mean(vs):
        return sum(vs) / len(vs) if vs else None

    temps = col("apparent_temperature")
    t_max, t_avg = (max(temps) if temps else None), mean(temps)
    used = t_max if temp_agg.startswith("max") else t_avg
    wind = mean(col("wind_speed_10m"))
    precip_vals = col("precipitation")
    precip = sum(precip_vals) if precip_vals else None
    soil = mean(col("soil_moisture"))
    depth = mean(col("snow_depth"))
    fall = col("snowfall")
    snow = None if depth is None else depth + (sum(fall) if fall else 0.0)
    peak_hour = None
    if temps:
        peak_hour = max((r for r in win if _num(r.get("apparent_temperature")) is not None),
                        key=lambda r: _num(r["apparent_temperature"]))["local_hour"]
    wx = {"apparent_temp": used, "wind": wind, "precip": precip,
          "soil": soil, "snow": snow}
    def r2(v, n=2):
        return None if v is None else round(v, n)

    summary = {"temp_max": r2(t_max, 1), "temp_avg": r2(t_avg, 1),
               "temp_used": r2(used, 1),
               "temp_agg": "max" if temp_agg.startswith("max") else "avg",
               "peak_hour": peak_hour, "wind_kmh": r2(wind, 1),
               "precip_mm": r2(precip), "soil": r2(soil, 3), "snow_m": r2(snow, 3),
               "hours": [lo, hi],
               "n_hours": len(win),
               "hourly": [[int(r["local_hour"]),
                           None if _num(r.get("apparent_temperature")) is None
                           else round(_num(r["apparent_temperature"]), 1)]
                          for r in rows if r.get("local_hour") is not None]}
    return wx, summary


def weatherTerm(cur, row, sport, nd):
    """The live weather multiplier for this row, from the grid and the
    artifact, as (log credit | None, detail dict). The detail always says
    why when there is no number."""
    detail = {}
    art = None
    try:
        art = nd._weatherArtifactFor(sport)
    except Exception:                                  # noqa: BLE001
        art = None
    if art is None:
        return None, {"why": "the weather model is not loaded on this server"}
    if sport == "TF" and row.get("is_indoor") == 1:
        return None, {"why": "indoor: no weather correction by design",
                      "indoor": True}
    cell = snapCell(row.get("lat"), row.get("lon"))
    if cell is None:
        return None, {"why": "the meet has no GPS location, so no weather "
                             "was looked up (no correction)"}
    day = (row.get("date") or "")[:10]
    hours = tuple(art.get("race_local_hours", (8, 12)))
    try:
        temp_agg = nd.weatherTempAgg(art)
    except Exception:                                  # noqa: BLE001
        temp_agg = "avg(apparent_temperature)"
    try:
        cur.execute(WEATHER_DAY_SQL, {"clat": cell[0], "clon": cell[1], "day": day})
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:                                  # noqa: BLE001
        _rollback(cur)
        return None, {"why": "the weather grid could not be read"}
    wx, summary = aggregateWeather(rows, hours, temp_agg)
    if wx is None:
        return None, {"why": f"no weather in the grid for {day} at this location "
                             f"(no correction)"}
    detail.update(summary)
    detail["cell"] = list(cell)
    wx["doy"] = _doy(day)
    course = row.get("wx_course")
    try:
        if not nd.isRaceWeatherPlausible(wx):
            detail["why"] = ("the grid cell reports conditions no race is run in "
                             "(it describes terrain, not the course): no correction")
            return 0.0, detail
    except Exception:                                  # noqa: BLE001
        pass
    try:
        ref = nd._weatherReference(art, course, wx["doy"])
        detail["normal_temp"] = _num((ref or {}).get("apparent_temp"))
    except Exception:                                  # noqa: BLE001
        pass
    try:
        out = nd._applyWeather(1.0, wx, course, sport, distance_m=row.get("distance_m"))
    except Exception:                                  # noqa: BLE001
        return None, dict(detail, why="the weather model could not be evaluated")
    if not out or out <= 0:
        return None, detail
    # _applyWeather returns time / wmult: the credit is log(wmult)
    return -math.log(out), detail


def _doy(day):
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(day[:10]).timetuple().tm_yday
    except Exception:                                  # noqa: BLE001
        return None


def _rollback(cur):
    try:
        cur.connection.rollback()
    except Exception:                                  # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  THE RACE-DAY TERM -- which sports' ratings carry it
# ------------------------------------------------------------------ #

_PAIR_NPZ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "engine", "data", "pair_difficulty.npz")
_MODES = {"mtime": None, "val": {}}
RACE_DAY_CAP = 0.10                     # joint_solve.RACE_DAY_CAP


def parseDayModes(values):
    """{'XC': 'all' | 'fast'} from ('XC', 'TF:fast', ...): joint_golive.dayModes."""
    modes = {}
    for x in values or ():
        name, _, mode = str(x).strip().upper().partition(":")
        if name in ("XC", "TF"):
            modes[name] = "fast" if mode == "FAST" else "all"
    return modes


def dayModes(path=_PAIR_NPZ):
    """The sports whose published ratings carry the race-day term, with
    the mode -- read from what the go-live published, as app.RACE_DAY_SPORTS
    does (XCP_RACE_DAY_SPORTS overrides), but keeping ':fast'."""
    env = os.environ.get("XCP_RACE_DAY_SPORTS", "")
    if env.strip():
        return parseDayModes(env.split(","))
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if mt != _MODES["mtime"]:
        try:
            import numpy as _np
            with _np.load(path, allow_pickle=False) as z:
                _MODES["val"] = (parseDayModes(z["race_effect_sports"].tolist())
                                 if "race_effect_sports" in z.files else {})
        except Exception:                              # noqa: BLE001
            _MODES["val"] = {}
        _MODES["mtime"] = mt
    return _MODES["val"]


def appliedDay(u, mode):
    """The day term the rating applied (before the tilt): clipped at the
    cap, only the fast side under ':fast', nothing when the sport is out."""
    if u is None or not mode:
        return 0.0
    v = max(-RACE_DAY_CAP, min(RACE_DAY_CAP, float(u)))
    if mode == "fast":
        v = min(v, 0.0)
    return v


# ------------------------------------------------------------------ #
#  THE CHAIN -- pure: terms in, steps out
# ------------------------------------------------------------------ #

def buildChain(t):
    """Steps from the numeric terms of one row. Pure (tests call it with a
    dict). Keys of t (any may be None):

      sport, time, distance_m, ref_distance, pool, rating, pm,
      f_dist      distance-only factor (no era, no geometry)
      era         log f(season) / f(none)          (+ = norm slower)
      geom        log f(+geometry) / f(season)      (+ = norm slower)
      track_length, track_type, season
      w_stored    log(time * factor / norm)  -- the weather inside the row
      w_live      the current weather model's log credit, w_detail
      difficulty, course_source ('champ'|'era'|'latest'), course_applied,
      venue, tilt, shift
      day_u, day_n, day_mode
      offset, gain
    """
    sport = t.get("sport") or "XC"
    steps, notes = [], []
    rating = t.get("rating")
    pm = t.get("pm")
    tilt = t.get("tilt") if t.get("tilt") is not None else 1.0
    tsec, dist = t.get("time"), t.get("distance_m")
    pool_words = POOL_WORDS.get((t.get("pool") or "").split("|")[0], t.get("pool") or "this pool")

    # --- the start: distance alone, on an average outdoor track --------- #
    eq = (tsec * t["f_dist"]) if (tsec and t.get("f_dist")) else None
    shift = t.get("shift") or 0.0
    base_log = None
    if eq and pm:
        base_log = math.log(100.0 * pm / eq) + tilt * shift
    ref_m = t.get("ref_distance")
    if eq:
        txt = (f"{clock(tsec)} over {_m(dist)} converts to {clock(eq, 1)} at the "
               f"{_m(ref_m)} reference on the {pool_words} distance curve")
        if base_log is not None:
            txt += (f": a {math.exp(base_log):.1f} on an average outdoor track, "
                    f"before anything about this race")
        steps.append({"key": "distance", "label": "Distance", "pct": None,
                      "pts": None, "value": clock(eq, 1), "available": True,
                      "text": txt + "."})
    else:
        steps.append({"key": "distance", "label": "Distance", "available": False,
                      "pct": None, "pts": None,
                      "text": "Not available: the race's distance or pool is unknown."})

    run = base_log                        # running log rating
    acc = 0.0                             # credits so far (for 'other' w/o pm)

    def add(key, label, x, text, available=True, **extra):
        nonlocal run, acc
        pts = None
        if x is not None and run is not None:
            before = math.exp(run)
            run += x
            pts = math.exp(run) - before
        if x is not None:
            acc += x
        st = {"key": key, "label": label, "available": available,
              "pct": None if x is None else round(pctOf(x), 2),
              "pts": None if pts is None else round(pts, 2), "text": text}
        st.update(extra)
        steps.append(st)

    # --- era --------------------------------------------------------- #
    era = t.get("era")
    if era is None:
        add("era", "Era", None, "Not available.", available=False)
    else:
        x = -era
        season = t.get("season")
        if abs(x) < _TINY:
            txt = f"No era adjustment for {season}."
        elif x < 0:
            txt = (f"Times ran faster across the board in {season} than in the "
                   f"reference era (shoes, depth), so this one counts {_a(x, 2)} less.")
        else:
            txt = (f"Times ran slower across the board in {season} than in the "
                   f"reference era, so this one counts {_a(x, 2)} more.")
        add("era", "Era", x, txt)

    # --- geometry (track only) ---------------------------------------- #
    if sport == "TF":
        g = t.get("geom")
        tl, tt = t.get("track_length"), t.get("track_type")
        if g is None:
            add("geometry", "Track", None, "Not available.", available=False)
        elif abs(g) < _TINY:
            what = (f"{_m(tl)} {'banked' if tt == 'Banked' else 'flat'} track"
                    if tl else "a standard 400 m track")
            add("geometry", "Track", 0.0, f"Run on {what}: no geometry adjustment.")
        else:
            x = -g
            what = f"{_m(tl) if tl else 'a 400 m'} {'banked' if tt == 'Banked' else 'flat'} track"
            add("geometry", "Track", x,
                f"Run on {what}: the time is put onto a 400 m flat track "
                f"({_p(x, 2)}).")

    # --- weather ------------------------------------------------------ #
    wd = dict(t.get("w_detail") or {})
    w_stored, w_live = t.get("w_stored"), t.get("w_live")
    w = w_stored if w_stored is not None else w_live
    wd["model_pct"] = None if w_live is None else round(pctOf(w_live), 2)
    wd["stored_pct"] = None if w_stored is None else round(pctOf(w_stored), 2)
    if w is None:
        add("weather", "Weather", None,
            "Not available" + (f": {wd['why']}." if wd.get("why") else "."),
            available=False, detail=wd)
    else:
        txt = _weatherText(w, wd, sport)
        if (w_stored is not None and w_live is not None
                and abs(w_stored - w_live) > 0.0015):
            txt += (f" (The stored time carries {_p(w_stored, 2)}; today's weather "
                    f"model gives {_p(w_live, 2)} -- the row was normalised before "
                    f"the latest weather refit and moves at the next backfill.)")
        add("weather", "Weather", w, txt, detail=wd)

    # --- course --------------------------------------------------------- #
    d = t.get("difficulty")
    venue = t.get("venue") or ("this track" if sport == "TF" else "this course")
    src = t.get("course_source")
    if t.get("course_applied") is False:
        add("course", "Course", 0.0,
            t.get("course_why") or "The rating carries no course term for this race.",
            detail={"difficulty": d})
    elif d is None:
        add("course", "Course", None,
            "Not available: the venue has no fitted difficulty.", available=False)
    else:
        x = tilt * math.log1p(float(d))
        ref = "an average outdoor track"
        era_note = {"era": " in this race's era", "champ": " as a championship course",
                    "latest": ""}.get(src, "")
        if abs(float(d)) < 0.0005:
            txt = f"{venue} is average{era_note} ({_p(math.log1p(float(d)))}): no course adjustment."
        else:
            harder = float(d) > 0
            txt = (f"{venue} runs {_a(math.log1p(float(d)))} "
                   f"{'slower' if harder else 'faster'} than {ref}{era_note}, so the "
                   f"time {'is credited' if harder else 'counts'} {_a(x, 2)}"
                   f"{'' if harder else ' less'}")
            if abs(tilt - 1.0) > 0.005:
                txt += f" (the engine applies {tilt:.2f}x of a course at this ability)"
            txt += "."
        add("course", "Course", x, txt,
            detail={"difficulty": d, "source": src, "tilt": round(tilt, 3)})

    # --- race day ------------------------------------------------------- #
    u, mode = t.get("day_u"), t.get("day_mode")
    if u is None:
        add("day", "Race day", None,
            "Not available: the solve kept no race-day term for this race.",
            available=False, applied=bool(mode))
    else:
        slow = float(u) > 0
        level = abs(float(u)) < 0.0005
        how = ("level" if level else
               f"{_a(float(u))} {'slow' if slow else 'fast'}")
        n = t.get("day_n")
        who = f"the field ({n} runners rated here)" if n else "the field"
        if not mode:
            txt = (f"That day {who} ran {how} against their own ratings. "
                   f"{_SPORT_WORDS.get(sport, sport)} ratings do not carry the race-day term (it only "
                   f"keeps the course number honest), so nothing is applied.")
            add("day", "Race day", 0.0, txt, applied=False,
                detail={"day_pct": round(pctOf(float(u)), 2), "n_rows": n})
        else:
            v = appliedDay(u, mode)
            x = tilt * v
            if mode == "fast" and float(u) > 0:
                txt = (f"That day {who} ran {how}. {_SPORT_WORDS.get(sport, sport)} ratings carry only a "
                       f"FAST day, so a slow one is not credited.")
            elif level:
                txt = f"That day {who} ran level: no race-day adjustment."
            else:
                txt = (f"That day {who} ran {how} against their own ratings "
                       f"(conditions the weather model does not see), so this "
                       f"rating {'is credited' if x > 0 else 'is lowered'} {_a(x, 2)}.")
                if abs(float(u)) > RACE_DAY_CAP + 1e-9:
                    txt += " Capped at 10%: beyond that the result sheet is suspect."
            add("day", "Race day", x, txt, applied=True,
                detail={"day_pct": round(pctOf(float(u)), 2), "n_rows": n})

    # --- event offset, sport gain (track) -------------------------------- #
    if sport == "TF":
        off = t.get("offset")
        if off is None:
            add("event", "Event", None, "Not available.", available=False)
        elif abs(off) < _TINY:
            add("event", "Event", 0.0,
                f"No per-event correction for the {_m(dist)} at this ability.")
        else:
            add("event", "Event", off,
                f"The distance curve is checked against athletes who run several "
                f"events; at this ability the {_m(dist)} gets {_p(off, 2)}.")
        g = t.get("gain")
        if g is not None and abs(g) >= _TINY:
            add("gain", "Track season", g,
                f"Track ratings are shifted {_p(g, 2)} at this ability so a "
                f"runner's track and cross country numbers line up.")

    # --- what is left, so the steps land on the stored rating ------------ #
    if rating and run is not None:
        x = math.log(float(rating)) - run
        if abs(pctOf(x)) >= 0.05:
            add("other", "Other", x,
                "What the rating carries that this page cannot separate: the "
                "altitude credit, the race-day term as the rest of the field "
                "ran it, a rating priced without a solve, rounding.")
    elif rating is None:
        notes.append("This result has no rating.")
    else:
        notes.append("The pool's scale is not available, so the steps are "
                     "percentages of time only.")

    return {"steps": steps, "notes": notes,
            "start_rating": None if base_log is None else round(math.exp(base_log), 2),
            "rating": None if rating is None else round(float(rating), 2)}


def _weatherText(w, wd, sport):
    bits = []
    agg = wd.get("temp_agg")
    lo, hi = (wd.get("hours") or [None, None])[:2]
    if wd.get("temp_used") is not None and lo is not None:
        which = "peak" if agg == "max" else "average"
        bits.append(f"the {which} feels-like temperature over {hourWords(lo)}-"
                    f"{hourWords(hi)} local was {tempWords(wd['temp_used'])}"
                    + (f" at {hourWords(wd['peak_hour'])}" if agg == "max"
                       and wd.get("peak_hour") is not None else "")
                    + (f", against a normal of {tempWords(wd['normal_temp'])} "
                       f"for this venue and time of year"
                       if wd.get("normal_temp") is not None else ""))
    if wd.get("wind_kmh") is not None:
        bits.append(f"wind {wd['wind_kmh']:.0f} km/h")
    if wd.get("precip_mm"):
        bits.append(f"rain {wd['precip_mm']:.1f} mm")
    head = ""
    if abs(w) < _TINY:
        head = "Ordinary conditions: no weather adjustment."
    elif w > 0:
        head = f"Conditions were judged slow, so the time is credited {_a(w, 2)}."
    else:
        head = f"Conditions were judged fast, so the time counts {_a(w, 2)} less."
    if wd.get("why") and not bits:
        return (f"{head} (The day's weather is not shown here: {wd['why']}.)"
                if abs(w) >= _TINY else f"{head} ({wd['why'][:1].upper()}{wd['why'][1:]}.)")
    said = "; ".join(bits)
    out = head + (f" {said[:1].upper()}{said[1:]}." if said else "")
    if agg == "max" and sport == "TF":
        out += (" Start times are not known, so a track race is read at the day's "
                "peak heat even if it ran in the evening.")
    elif lo is not None:
        out += (f" Start times are not known; the model reads the "
                f"{hourWords(lo)}-{hourWords(hi)} window.")
    return out


# ------------------------------------------------------------------ #
#  THE ROW -- one query per sport
# ------------------------------------------------------------------ #

def xcRowSql(frag):
    """frag: app.py's own SQL fragments (tfrrs_join, dov_join, champ_join,
    xc_course, xc_distance, rating_pool), so the cell this resolves is the
    one the athlete page shows -- course_canonical by (name, gps), the
    corrected distance, the championship cell first."""
    return f"""
        SELECT r.result_id, r.source, r.meet_id, r.div_id, r.date::text AS date,
               r.time_seconds, r.normalized_time, r.speed_rating,
               {frag['rating_pool']}, rr.pool AS rr_pool, NULL::text AS event_short,
               {frag['xc_distance']} AS distance,
               COALESCE(m.meet_name, mt.meet_name) AS meet_name,
               {frag['xc_course']} AS venue,
               COALESCE(m.gps_lat, mt.gps_lat) AS lat,
               COALESCE(m.gps_long, mt.gps_long) AS lon,
               m.course_name AS wx_course,
               cc.canonical_id, cd.distance_m AS cell_distance_m,
               cd.difficulty AS cell_difficulty, cdc.difficulty AS champ_difficulty,
               (dov.distance IS NOT NULL
                AND COALESCE(m.distance, {frag['blob']}::real) IS NOT NULL
                AND abs(dov.distance::real
                        - COALESCE(m.distance, {frag['blob']}::real)) >= 1) AS dist_corrected,
               NULL::bigint AS location_id, NULL::int AS is_indoor,
               NULL::text AS track_type, NULL::real AS track_length
        FROM   results r
        LEFT JOIN ranking_results rr ON rr.result_id = r.result_id AND rr.sport = 'XC'
        LEFT JOIN meets m ON m.div_id = r.div_id AND m.meet_id = r.meet_id
              AND m.source = r.source
        {frag['tfrrs_join']}{frag['dov_join']}
        LEFT JOIN course_canonical cc
               ON cc.course_name = {frag['xc_course']}
              AND round(cc.gps_lat::numeric, 5) = round(COALESCE(m.gps_lat, mt.gps_lat)::numeric, 5)
              AND round(cc.gps_long::numeric, 5) = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              AND cd.distance_m = (round({frag['xc_distance']} / 100.0) * 100)::int
        {frag['champ_join']}
        WHERE  r.result_id = %(rid)s
        LIMIT  1
    """


def tfRowSql(frag):
    """The track row with its venue cell, geometry and weather location as
    the backfill and the athlete page resolve them: anet geometry at the
    event grain (meets_tf), anet weather from meets_tf_meta (outdoor), a
    tfrrs row's coordinates from meets_tfrrs and its venue, indoor flag and
    geometry from the stamp (tfrrs_meet_geometry)."""
    return f"""
        SELECT r.result_id, r.source, r.meet_id, r.div_id, r.event_id,
               r.date::text AS date, r.time_seconds, r.normalized_time,
               r.speed_rating, {frag['rating_pool']}, rr.pool AS rr_pool,
               r.event_short, m.distance_meters AS distance,
               COALESCE(NULLIF(btrim(m.meet_name), ''), mt.meet_name) AS meet_name,
               NULL::text AS venue,
               COALESCE(mm.gps_lat, mt.gps_lat) AS lat,
               COALESCE(mm.gps_long, mt.gps_long) AS lon,
               CASE WHEN r.source = 'tfrrs'
                    THEN CASE WHEN mt.location_id IS NOT NULL
                              THEN 'TF:loc:' || mt.location_id || ':out' END
                    ELSE 'TF:loc:' || mm.location_id || ':out' END AS wx_course,
               COALESCE(m.location_id, mt.location_id) AS location_id,
               COALESCE(m.is_indoor, mt.is_indoor, mm.is_indoor, 0) AS is_indoor,
               COALESCE(m.track_type, mt.track_type) AS track_type,
               COALESCE(m.track_length, mt.track_length) AS track_length,
               cd.difficulty AS cell_difficulty, NULL::real AS champ_difficulty,
               false AS dist_corrected
        FROM   results_tf r
        LEFT JOIN ranking_results rr ON rr.result_id = r.result_id AND rr.sport = 'TF'
        LEFT JOIN LATERAL (
            SELECT m.meet_name, m.location_id, m.is_indoor, m.distance_meters,
                   m.track_type, m.track_length
            FROM   meets_tf m
            WHERE  m.meet_id = r.meet_id AND m.source = r.source
            ORDER  BY (m.event_id IS NOT DISTINCT FROM r.event_id
                       AND m.div_id IS NOT DISTINCT FROM r.div_id) DESC
            LIMIT  1
        ) m ON TRUE
        LEFT JOIN meets_tf_meta mm
               ON r.source <> 'tfrrs' AND mm.meet_id = r.meet_id
              AND COALESCE(mm.is_indoor, 0) = 0
        LEFT JOIN LATERAL (
            SELECT mt.meet_name, mt.gps_lat, mt.gps_long, g.location_id,
                   g.is_indoor, g.track_type, g.track_length
            FROM   meets_tfrrs mt
            LEFT JOIN tfrrs_meet_geometry g ON g.meet_id = mt.meet_id AND g.sport = 'TF'
            WHERE  r.source = 'tfrrs' AND mt.meet_id = r.meet_id AND mt.sport = 'TF'
            LIMIT  1
        ) mt ON TRUE
        LEFT JOIN course_difficulties cd
               ON cd.course_name = 'TF:loc:' || COALESCE(m.location_id, mt.location_id)::text ||
                  CASE WHEN COALESCE(m.is_indoor, mt.is_indoor, 0) = 1 THEN ':in' ELSE ':out' END
        WHERE  r.result_id = %(rid)s
        LIMIT  1
    """


# the bare row, when a join above names a table this database lacks
_MIN_SQL = {
    "XC": """SELECT r.result_id, r.source, r.meet_id, r.div_id, r.date::text AS date,
                    r.time_seconds, r.normalized_time, r.speed_rating,
                    NULL::text AS rating_pool, NULL::text AS rr_pool,
                    NULL::text AS event_short, NULL::real AS distance
             FROM results r WHERE r.result_id = %(rid)s LIMIT 1""",
    "TF": """SELECT r.result_id, r.source, r.meet_id, r.div_id, r.event_id,
                    r.date::text AS date, r.time_seconds, r.normalized_time,
                    r.speed_rating, NULL::text AS rating_pool, NULL::text AS rr_pool,
                    r.event_short, NULL::real AS distance
             FROM results_tf r WHERE r.result_id = %(rid)s LIMIT 1""",
}


def fetchRow(cur, sport, result_id, frag):
    """The result as a dict, or None. The full query first; if it fails
    (a table this database does not have yet), the bare row."""
    sql = xcRowSql(frag) if sport == "XC" else tfRowSql(frag)
    notes = []
    try:
        cur.execute(sql, {"rid": result_id})
        row = cur.fetchone()
    except Exception as exc:                           # noqa: BLE001
        _rollback(cur)
        print(f"explain: full {sport} row query failed: {type(exc).__name__}: {exc}",
              flush=True)
        notes.append("Venue and weather details could not be read for this result.")
        try:
            cur.execute(_MIN_SQL[sport], {"rid": result_id})
            row = cur.fetchone()
        except Exception:                              # noqa: BLE001
            _rollback(cur)
            return None, notes
    return (dict(row) if row else None), notes


def raceDayRow(cur, sport, row, ce_col, has_table):
    """(day_effect, n_rows, course_effect) for this race, or Nones.
    The keys are app._raceDayRow's: (canonical_id, distance_m, date) for
    XC, (course_name, date) for track."""
    if not has_table or not row.get("date"):
        return None, None, None
    try:
        if sport == "XC":
            if row.get("canonical_id") is None or row.get("cell_distance_m") is None:
                return None, None, None
            cur.execute(f"""
                SELECT day_effect, n_rows, {ce_col} AS course_effect
                FROM race_day_effect
                WHERE canonical_id = %(cid)s AND distance_m = %(dm)s
                  AND race_date::text = %(day)s::text
                ORDER BY n_rows DESC LIMIT 1""",
                {"cid": row["canonical_id"], "dm": row["cell_distance_m"],
                 "day": row["date"][:10]})
        else:
            if not row.get("location_id"):
                return None, None, None
            key = (f"TF:loc:{row['location_id']}:"
                   f"{'in' if row.get('is_indoor') == 1 else 'out'}")
            cur.execute(f"""
                SELECT day_effect, n_rows, {ce_col} AS course_effect
                FROM race_day_effect
                WHERE course_name = %(key)s AND race_date::text = %(day)s::text
                ORDER BY n_rows DESC LIMIT 1""", {"key": key, "day": row["date"][:10]})
        got = cur.fetchone()
    except Exception:                                  # noqa: BLE001
        _rollback(cur)
        return None, None, None
    if not got:
        return None, None, None
    got = dict(got)
    return (_num(got.get("day_effect")), got.get("n_rows"),
            _num(got.get("course_effect")))


def _distanceOf(row, sport, nd):
    d = None
    try:
        d = nd.metersFromDistance(row.get("distance")) if row.get("distance") else None
    except Exception:                                  # noqa: BLE001
        d = None
    if not d and sport == "TF" and row.get("event_short"):
        # ! THE EVENT NAME WHEN THE MEET HAS NO DISTANCE ("10-km" at WashU,
        #   2026-10-04): the parser the backfill normalised with
        try:
            from event_parse import distanceFromEventShort
            d = distanceFromEventShort(row["event_short"])[0]
        except Exception:                              # noqa: BLE001
            d = None
        if not d:
            try:
                d = (nd.parseEventShort(row["event_short"]) or {}).get("meters")
            except Exception:                          # noqa: BLE001
                d = None
    return float(d) if d else None


def gatherTerms(cur, sport, row, ce_col="NULL::real", has_day_table=False,
                modes=None, nd=None, cv=None):
    """The numeric terms of one row (buildChain's input). nd, cv: the
    normalize_distance and conversions modules (tests pass stubs)."""
    if nd is None:
        import normalize_distance as nd                # noqa: F811
    if cv is None:
        import conversions as cv                       # noqa: F811
    modes = dayModes() if modes is None else modes
    t = {"sport": sport}
    tsec = _num(row.get("time_seconds"))
    norm = _num(row.get("normalized_time"))
    rating = _num(row.get("speed_rating"))
    pool = ((row.get("rating_pool") or "").split("|")[0]
            or (row.get("rr_pool") or "").split("|")[0] or None)
    dist = _distanceOf(row, sport, nd)
    row["distance_m"] = dist
    season = None
    try:
        season = int(str(row.get("date"))[:4])          # normalizeResult: date.year
    except (TypeError, ValueError):
        pass
    t.update(time=tsec, distance_m=dist, pool=pool, rating=rating, season=season,
             track_length=_num(row.get("track_length")),
             track_type=row.get("track_type"))
    ev = row.get("event_short") if sport == "TF" else None
    tl, tt = t["track_length"], t["track_type"]

    # --- the factors: distance alone, + era, + geometry ----------------- #
    f_full = None
    if tsec and dist and pool:
        try:
            f0 = nd.factorForTime(tsec, dist, pool, None, None, None, sport, ev)
            f_era = nd.factorForTime(tsec, dist, pool, season, None, None, sport, ev)
            f_full = nd.factorForTime(tsec, dist, pool, season, tl, tt, sport, ev)
            # the era's own leg: distance alone carries no season
            t["f_dist"] = f0
            t["era"] = math.log(f_era / f0) if f0 and f_era else None
            t["geom"] = math.log(f_full / f_era) if f_era and f_full else None
        except Exception as exc:                       # noqa: BLE001
            print(f"explain: factor failed: {type(exc).__name__}: {exc}", flush=True)
        try:
            t["ref_distance"] = nd.targetFor(pool, sport)
        except Exception:                              # noqa: BLE001
            t["ref_distance"] = None

    # --- weather: what the row carries, and what the model says today ---- #
    try:
        w_live, w_detail = weatherTerm(cur, row, sport, nd)
    except Exception as exc:                           # noqa: BLE001
        print(f"explain: weather failed: {type(exc).__name__}: {exc}", flush=True)
        w_live, w_detail = None, {"why": "the weather could not be evaluated"}
    t["w_live"], t["w_detail"] = w_live, w_detail
    if tsec and norm and f_full:
        stored = math.log(tsec * f_full / norm)
        # ⚠ BEYOND WHAT WEATHER CAN DO (_WMULT_MIN/MAX in normalize_distance)
        #   the gap is not weather: the row was normalised in another pool or
        #   on another curve. It is left to "other" rather than called heat.
        if abs(stored) <= math.log(1.25):
            t["w_stored"] = stored
        else:
            t["w_stored"] = None
            t["w_detail"] = dict(w_detail or {}, stored_gap=round(pctOf(stored), 2))
    # a row with no stored norm and no live weather: nothing either way

    # --- the scale ---------------------------------------------------- #
    sc = None
    if pool:
        try:
            sc = cv.engineScale(pool, sport)
        except Exception:                              # noqa: BLE001
            sc = None
        try:
            t["pm"] = cv.pool_mean(pool, sport)
        except Exception:                              # noqa: BLE001
            t["pm"] = sc[0] if sc else None
    t["shift"] = sc[2] if sc else 0.0
    try:
        t["tilt"] = cv._tilt(rating, pool) if (rating and sc) else 1.0
    except Exception:                                  # noqa: BLE001
        t["tilt"] = 1.0

    # --- course and day ----------------------------------------------- #
    u, n, ce = raceDayRow(cur, sport, row, ce_col, has_day_table)
    t["day_u"], t["day_n"], t["day_mode"] = u, n, modes.get(sport)
    if row.get("dist_corrected"):
        t["course_applied"] = False
        t["course_why"] = ("This division's distance was corrected by hand, so it "
                           "votes on no course and its ratings carry no course term.")
        t["difficulty"] = None
    elif row.get("champ_difficulty") is not None:
        t["difficulty"], t["course_source"] = _num(row["champ_difficulty"]), "champ"
    elif ce is not None:
        t["difficulty"], t["course_source"] = ce, "era"
    else:
        t["difficulty"] = _num(row.get("cell_difficulty"))
        t["course_source"] = "latest" if t["difficulty"] is not None else None
    if sport == "TF":
        t["venue"] = ("This indoor track" if row.get("is_indoor") == 1
                      else "This track")
    else:
        t["venue"] = row.get("venue") or "This course"

    # --- track terms -------------------------------------------------- #
    if sport == "TF" and pool and dist:
        try:
            t["offset"] = cv.distance_offset(pool, "TF", dist, rating=rating)
        except Exception:                              # noqa: BLE001
            t["offset"] = None
        try:
            t["gain"] = cv.sport_gain(pool, "TF", rating)
        except Exception:                              # noqa: BLE001
            t["gain"] = None
    return t


def explainResult(cur, sport, result_id, frag, ce_col="NULL::real",
                  has_day_table=False, modes=None, nd=None, cv=None):
    """The JSON body for one result, or None when there is no such row."""
    sport = (sport or "").upper()
    row, notes = fetchRow(cur, sport, result_id, frag)
    if row is None:
        return None
    t = gatherTerms(cur, sport, row, ce_col=ce_col, has_day_table=has_day_table,
                    modes=modes, nd=nd, cv=cv)
    body = buildChain(t)
    body["notes"] = notes + body["notes"]
    pool = t.get("pool")
    body.update({
        "ok": True, "sport": sport, "result_id": result_id,
        "race": {"date": (row.get("date") or "")[:10] or None,
                 "meet": row.get("meet_name"),
                 "event": row.get("event_short") if sport == "TF" else (
                     _m(t.get("distance_m")) if t.get("distance_m") else None),
                 "time": clock(t.get("time")),
                 "distance_m": t.get("distance_m"),
                 "pool": pool,
                 "pool_label": POOL_WORDS.get(pool or "", pool)},
    })
    return body


# ------------------------------------------------------------------ #
#  the cache the route keeps
# ------------------------------------------------------------------ #

class TtlCache:
    """{key: body} for ttl seconds, at most `cap` entries (oldest out)."""

    def __init__(self, ttl=1800.0, cap=4000, clock=time.time):
        self.ttl, self.cap, self.clock, self._d = ttl, cap, clock, {}

    def get(self, key):
        hit = self._d.get(key)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        if hit:
            self._d.pop(key, None)
        return None

    def put(self, key, body):
        self._d[key] = (self.clock(), body)
        if len(self._d) > self.cap:
            oldest = min(self._d, key=lambda k: self._d[k][0])
            self._d.pop(oldest, None)
