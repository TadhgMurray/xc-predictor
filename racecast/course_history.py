# Project: xc-predictor / racecast
# File:    course_history.py
# Purpose: "Year by year" on the course page: how the course ran on every
#          race day the solve saw, and the course's difficulty in each era.
#
# ★ WHY (owner, 2026-10-04, while adding race_day_effect.course_effect:
#   "every year of a venue showed one difficulty -- Newhall +7.6% on
#   2021-2024, Mt. SAC +11.1% on 2021-2023"). The engine has fitted each
#   venue per ERA since --era-years, and kept a term for every race day
#   (how the field ran against its own ratings: weather, ground, a course
#   laid out long or short) since issue 197. The course page showed one
#   number -- the latest era's -- and none of the rest. This reads both
#   back from race_day_effect and lays them out by season:
#
#     course (era)   the difficulty the ratings used for that season
#     field ran      the rated runners' day term, runner-weighted
#     together       (1 + course) * e^day - 1: how hard the course
#                    actually played that day against an average track
#
# ! ONE CELL, NOT A NAME. Course pages are keyed by name and 21 venues are
#   called Woodward Park; the history is the cell with the most results at
#   the distance, the same pick get_course_cell_difficulties makes.
# ! READ-ONLY AND LIVE, beside the precomputed course board rather than in
#   it, so pipeline step 12b (build_course_boards) is untouched.

import math

DAY_CAP = 0.10                      # joint_solve.RACE_DAY_CAP


def historySql(ce_col):
    """ce_col: app._raceDayCourseSql's answer ('rde.course_effect' once the
    go-live writes it, else 'NULL::real')."""
    return f"""
        -- ★ EVERY CELL OF THIS NAME AT THIS DISTANCE (owner, 2026-10-08:
        --   "not every meet is shown"). A course name can sit at several
        --   canonical ids (one per set of coordinates entered), and the
        --   page is the name's; taking only the biggest cell dropped every
        --   day keyed under the others. summarize() merges a date that
        --   appears in two cells by rows.
        WITH cell AS (
            SELECT DISTINCT cd.canonical_id, cd.distance_m
            FROM   course_canonical cc
            JOIN   course_difficulties cd ON cd.canonical_id = cc.canonical_id
            WHERE  cc.course_name = %(course)s
              AND  (round(cd.distance_m / 100.0) * 100)::int = %(dm)s
        )
        SELECT rde.race_date::text AS race_date, rde.day_effect, rde.n_rows,
               {ce_col} AS course_effect
        FROM   cell
        JOIN   race_day_effect rde
               ON rde.canonical_id = cell.canonical_id
              AND rde.distance_m = cell.distance_m
        ORDER  BY rde.race_date DESC
        LIMIT  800
    """


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def seasonOf(day):
    """XC season year for 'YYYY-MM-DD' (season_year's clock; a January
    race belongs to the season that opened the August before)."""
    try:
        from season_year import seasonYearFromIso
        return seasonYearFromIso("XC", day[:10])
    except Exception:                                  # noqa: BLE001
        y, m = int(day[:4]), int(day[5:7])
        return y if m >= 7 else y - 1


def together(course, day):
    """How hard the course played: (1 + course) * e^clip(day) - 1."""
    if course is None and day is None:
        return None
    c = course or 0.0
    d = max(-DAY_CAP, min(DAY_CAP, day or 0.0))
    return (1.0 + c) * math.exp(d) - 1.0


def meetsByDate(meets, dist=None):
    """{'YYYY-MM-DD': meet} from the page's own meet list (one row per
    meet, its last date); a meet that ran this distance wins a shared date,
    then the bigger one."""
    out = {}
    for m in meets or ():
        day = str(m.get("last_date") or "")[:10]
        if not day:
            continue
        dists = [round(float(x)) for x in (m.get("distances") or []) if _f(x)]
        ran = dist is None or any(abs(x - dist) < 50 for x in dists)
        key = (1 if ran else 0, m.get("n_results") or 0)
        if day not in out or key > out[day][0]:
            out[day] = (key, m)
    return {d: m for d, (_k, m) in out.items()}


def summarize(rows, meets=None, dist=None, day_mode=None):
    """The section's data from race_day_effect rows (newest first), or
    None when there is nothing to show. Pure."""
    days = []
    for r in rows or ():
        day = str(r.get("race_date") or "")[:10]
        u = _f(r.get("day_effect"))
        if not day or u is None:
            continue
        days.append({"date": day, "season": seasonOf(day), "day": u,
                     "n": int(r.get("n_rows") or 0),
                     "course": _f(r.get("course_effect"))})
    if not days:
        return None
    # two races on one day (two divisions, one cell): one line, runner-weighted
    merged = {}
    for d in days:
        m = merged.get(d["date"])
        if m is None:
            merged[d["date"]] = dict(d)
            continue
        n = m["n"] + d["n"]
        if n:
            m["day"] = (m["day"] * m["n"] + d["day"] * d["n"]) / n
        m["n"] = n
        m["course"] = m["course"] if m["course"] is not None else d["course"]
    days = sorted(merged.values(), key=lambda d: d["date"], reverse=True)
    by_date = meetsByDate(meets, dist)
    for d in days:
        mt = by_date.get(d["date"])
        d["meet"] = mt.get("meet_name") if mt else None
        d["meet_id"] = mt.get("meet_id") if mt else None
        d["capped"] = abs(d["day"]) > DAY_CAP + 1e-9
        d["together"] = together(d["course"], d["day"])

    seasons = {}
    for d in days:
        s = seasons.setdefault(d["season"], {"season": d["season"], "days": 0,
                                             "runners": 0, "_w": 0.0, "_u": 0.0,
                                             "courses": {}})
        s["days"] += 1
        s["runners"] += d["n"]
        w = max(d["n"], 1)
        s["_w"] += w
        s["_u"] += w * max(-DAY_CAP, min(DAY_CAP, d["day"]))
        if d["course"] is not None:
            k = round(d["course"], 4)
            s["courses"][k] = s["courses"].get(k, 0) + w
    out_seasons = []
    for y in sorted(seasons, reverse=True):
        s = seasons[y]
        course = (max(s["courses"], key=lambda k: s["courses"][k])
                  if s["courses"] else None)
        day = s["_u"] / s["_w"] if s["_w"] else None
        out_seasons.append({"season": y, "days": s["days"], "runners": s["runners"],
                            "course": course, "day": day,
                            "together": together(course, day)})

    # the eras: consecutive seasons sharing one course number (to 0.1%)
    eras = []
    for s in sorted(out_seasons, key=lambda s: s["season"]):
        c = s["course"]
        if c is None:
            continue
        if eras and abs(eras[-1]["course"] - c) < 0.0005:
            eras[-1]["last"] = s["season"]
        else:
            eras.append({"first": s["season"], "last": s["season"], "course": c})
    biggest = max((abs(s["together"]) for s in out_seasons
                   if s["together"] is not None), default=0.0)
    return {"days": days, "seasons": out_seasons, "eras": eras,
            "dist": dist, "day_mode": day_mode,
            "has_course": any(s["course"] is not None for s in out_seasons),
            "scale": max(0.05, min(0.25, biggest * 1.15))}


def fetchHistory(cur, course_name, dist, ce_col="NULL::real"):
    """race_day_effect rows for this course at this distance, or []."""
    if not course_name or not dist:
        return []
    try:
        cur.execute(historySql(ce_col),
                    {"course": course_name, "dm": int(round(float(dist) / 100.0) * 100)})
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:                           # noqa: BLE001
        try:
            cur.connection.rollback()
        except Exception:                              # noqa: BLE001
            pass
        print(f"course_history: {course_name} {dist}: {type(exc).__name__}: {exc}",
              flush=True)
        return []
