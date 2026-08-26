"""
school_prs.py -- one school's PRs, per distance/event.

★ MIRRORS THE PR BOARD'S RULES ON PURPOSE. Distances snap to the same
  whitelist with the same 0.25% band (rankings.PR_DISTANCES) so a section
  here and its "view all" board show the same population; a distance the
  board refuses still gets a section, just no link. Same DNF sentinel
  handling: the clock, not the rating, decides.

★ RUNNING FROM ranking_results, FIELD FROM results_tf. ranking_results
  carries times and distances for everything the engine rated; field
  marks never enter it (they are not times), so jumps and throws come
  straight from results_tf with tf_points' canonical-event and mark
  parsing -- the exact code the meet scorer uses.

★ HURDLES AND STEEPLE ARE A THIRD SOURCE for the same reason twice
  over: the engine rates flat races only, so they never reach
  ranking_results, and they are times, so fieldSql refuses them. They
  come from results_tf too, picked out by what the event calls itself
  (hurdleSql's pattern), ranked on time_seconds.

Filters: a season label narrows to that season's bests; an XC course
narrows to bests run THERE. Both are cut in Python over one fetch,
because the year list and the course chips need the unfiltered rows
anyway.
"""

from rankings import PR_DISTANCES, PR_DISTANCE_TOL
from school import seasonLabel, storedYear
from tf_points import (canonicalEvent, displayEvent, eventDistance,
                       genderOf, parseMark)

# ranking_results stores metres as the meet recorded them; label the
# imperial ones the way the people who ran them say them.
_MILE_LABELS = {1609: "1 Mile", 2414: "1.5 Mile", 3218: "2 Mile",
                4023: "2.5 Mile", 4828: "3 Mile", 6437: "4 Mile",
                8047: "5 Mile"}


def _distLabel(d):
    return _MILE_LABELS.get(d, f"{d}m")


def _snap(d):
    """(bucket, on_board): the PR board's distance for this race, with
    the board's own tolerance; off-list distances bucket to the metre
    and simply don't link."""
    best = min(PR_DISTANCES, key=lambda x: abs(x - d))
    if abs(best - d) <= best * PR_DISTANCE_TOL:
        return best, True
    return int(round(d)), False


def _genderOfPool(pool):
    if not pool:
        return None
    if pool.endswith("_m"):
        return "M"
    if pool.endswith("_f"):
        return "F"
    return None


def runningSql(sport):
    """The running-rows SQL, exposed so scripts/explain_pages.py can
    EXPLAIN the exact text the page runs. The meets join stays XC-only:
    `meets` is keyed in the XC div-id space and a TF div id colliding
    with it would hand a track race a cross country course."""
    course_sql = (
        "LEFT JOIN meets m ON m.div_id = rr.div_id "
        "AND m.meet_id = rr.meet_id" if sport == "XC" else "")
    course_col = "m.course_name" if sport == "XC" else "NULL"
    return f"""
        SELECT rr.result_id, rr.person_id, rr.pool, rr.speed_rating,
               rr.time_seconds, rr.distance, rr.race_date, rr.year,
               rr.grade, rr.meet_id, rr.div_id, rr.event_id,
               {course_col} AS course_name
        FROM   ranking_results rr
        {course_sql}
        WHERE  rr.school = %(school)s
          AND  rr.sport  = %(sport)s
          AND  rr.time_seconds > 0
          AND  rr.time_seconds < 86400
          AND  rr.distance IS NOT NULL
    """


def fieldSql():
    """The field-rows SQL, exposed for the same EXPLAIN tooling."""
    from season_year import seasonYearSqlInt
    return f"""
        SELECT r.result_id, r.person_id, r.athlete_id, r.mark, r.grade,
               r.date, r.meet_id, r.div_id, r.event_id,
               {seasonYearSqlInt('TF', 'r.date')} AS year,
               COALESCE(NULLIF(TRIM(m.event_short), ''),
                        NULLIF(TRIM(r.event_short), '')) AS event_short
        FROM   results_tf r
        LEFT JOIN meets_tf m ON m.meet_id  = r.meet_id
                            AND m.div_id   = r.div_id
                            AND m.event_id = r.event_id
        WHERE  r.school = %(school)s
          AND  COALESCE(r.is_relay, 0) = 0
          AND  (r.is_field = 1 OR r.result_kind IN ('field', 'combined'))
          AND  r.mark IS NOT NULL
          AND  r.date IS NOT NULL
    """


# What a hurdle or steeple race calls itself. The word forms catch
# "110m Hurdles" / "3000m Steeplechase"; the code arm catches the bare
# feed spellings -- a distance, then h/hh/lh/ih or sc as its own word
# ("110h", "100mH", "60 H", "2kSC"). Digit-first keeps "hj" a high jump
# and "5th" a grade. \M is Postgres for end-of-word.
HURDLE_PATTERN = r"(hurd|steeple|[0-9]\s*[km]?\s*(sc|[hli]?h)\M)"


def hurdleSql():
    """The hurdle/steeple rows SQL, exposed for the EXPLAIN tooling.
    These races live only in results_tf: the engine rates flat races, so
    ranking_results never sees them, and fieldSql refuses times. The
    relay guard is doubled -- the flag, plus the name ("Shuttle Hurdle
    Relay" with a dropped flag would otherwise mint a section)."""
    from season_year import seasonYearSqlInt
    name_sql = ("COALESCE(NULLIF(TRIM(m.event_short), ''), "
                "NULLIF(TRIM(r.event_short), ''))")
    return f"""
        SELECT r.result_id, r.person_id, r.athlete_id, r.time_seconds,
               r.mark, r.grade, r.date, r.meet_id, r.div_id, r.event_id,
               {seasonYearSqlInt('TF', 'r.date')} AS year,
               {name_sql} AS event_short
        FROM   results_tf r
        LEFT JOIN meets_tf m ON m.meet_id  = r.meet_id
                            AND m.div_id   = r.div_id
                            AND m.event_id = r.event_id
        WHERE  r.school = %(school)s
          AND  COALESCE(r.is_relay, 0) = 0
          AND  COALESCE(r.is_field, 0) = 0
          AND  COALESCE(r.result_kind, '') NOT IN ('field', 'combined')
          AND  r.time_seconds > 0
          AND  r.date IS NOT NULL
          AND  {name_sql} ~* %(pat)s
          AND  {name_sql} !~* '(relay|[0-9]\\s*x\\s*[0-9])'
    """


def _runningRows(cur, school, sport):
    """Every rated-or-timed race for this school in one sport, with the
    course name for XC.

    ⚠ NO NAME LATERAL. A big track program is tens of thousands of rows,
      and probing the 16M-row athletes table once per row was the page's
      whole cost -- track worst, since it makes several results per
      athlete per meet. Gender comes from the pool; names are looked up
      in ONE bulk query afterwards, for only the rows that display."""
    cur.execute(runningSql(sport), {"school": school, "sport": sport})
    return cur.fetchall()


def _fieldRows(cur, school):
    """Every individual field/multi mark for this school. The event name
    coalesces the meets_tf copy with the result's own, the scoring
    query's rule. Names AND genders resolve in one bulk lookup after --
    same reasoning as _runningRows."""
    cur.execute(fieldSql(), {"school": school})
    return cur.fetchall()


def _hurdleRows(cur, school):
    """Every individual hurdle/steeple time for this school."""
    cur.execute(hurdleSql(), {"school": school, "pat": HURDLE_PATTERN})
    return cur.fetchall()


def _athleteInfo(cur, ids):
    """{athlete_id: {"name", "gender"}} in ONE query -- the bulk
    replacement for the per-row lateral. Same pick rule: a row with a
    name beats one without, then a usable gender."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    cur.execute("""
        SELECT DISTINCT ON (athlete_id)
               athlete_id,
               NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '')
                   AS name,
               gender
        FROM   athletes
        WHERE  athlete_id = ANY(%(ids)s)
        ORDER  BY athlete_id,
                  (COALESCE(TRIM(first_name), '') <> ''
                   OR COALESCE(TRIM(last_name), '') <> '') DESC,
                  (gender IN ('M', 'F')) DESC
    """, {"ids": ids})
    out = {}
    for rec in cur.fetchall():
        aid, name, g = ((rec["athlete_id"], rec["name"], rec["gender"])
                        if isinstance(rec, dict)
                        else (rec[0], rec[1], rec[2]))
        out[aid] = {"name": name, "gender": g}
    return out


def _bestPer(rows, value_of, key_of):
    """Best row per key by value ascending (negate marks to reuse)."""
    best = {}
    for r in rows:
        v = value_of(r)
        k = key_of(r)
        if v is None or k is None:
            continue
        if k not in best or v < best[k][0]:
            best[k] = (v, r)
    return sorted(best.values(), key=lambda e: e[0])


def _person(r):
    return r.get("person_id") or ((r.get("name") or "").strip().lower() or None)


def schoolPrData(cur, school, sport, year_label=None, course=None,
                 per_table=100):
    """Everything the PRs page renders, one dict.

    {"sections": [{label, dist_note, kind, on_board, distance,
                   tables: {"M": [rows], "F": [rows]},
                   left: {"M": n, "F": n}}],
     "years": [labels desc], "courses": [names], "pools": {M,F},
     "year": picked label or None, "course": picked or None,
     "any": bool}
    """
    stored = storedYear(sport, year_label) if year_label else None
    running = _runningRows(cur, school, sport)
    field = _fieldRows(cur, school) if sport == "TF" else []
    hurdles = _hurdleRows(cur, school) if sport == "TF" else []

    # field and hurdle rows need gender BEFORE grouping (the tables
    # split on it); one bulk lookup covers every athlete in both. A
    # hurdle event usually names its gender itself, so that wins first
    # -- the scorer's own layering.
    if field or hurdles:
        info = _athleteInfo(cur, (r.get("person_id") or r.get("athlete_id")
                                  for r in field + hurdles))
        for r in field:
            a = info.get(r.get("person_id") or r.get("athlete_id"), {})
            r["name"] = a.get("name")
            r["gender"] = a.get("gender")
        for r in hurdles:
            a = info.get(r.get("person_id") or r.get("athlete_id"), {})
            r["name"] = a.get("name")
            r["gender"] = genderOf(r.get("event_short")) or a.get("gender")

    # year bar and course chips come from the UNFILTERED rows
    years = sorted({seasonLabel(sport, r["year"]) for r in running
                    if r.get("year")} |
                   {seasonLabel("TF", r["year"]) for r in field + hurdles
                    if r.get("year")}, reverse=True)
    # Every course the school has actually RACED (2+ results keeps a
    # single stray row from minting a chip), most-raced first. The
    # template shows the first few and folds the rest behind an expand,
    # so the cap is only against pathology.
    course_counts = {}
    for r in running:
        c = (r.get("course_name") or "").strip()
        if c:
            course_counts[c] = course_counts.get(c, 0) + 1
    courses = [c for c, n in sorted(course_counts.items(),
                                    key=lambda kv: (-kv[1], kv[0]))
               if n >= 2][:60]

    if stored:
        running = [r for r in running if r.get("year") == stored]
        field = [r for r in field if r.get("year") == stored]
        hurdles = [r for r in hurdles if r.get("year") == stored]
    if course and sport == "XC":
        running = [r for r in running
                   if (r.get("course_name") or "").strip() == course]

    # modal pool per gender, for the rankings links
    pool_counts = {"M": {}, "F": {}}
    for r in running:
        g = _genderOfPool(r.get("pool"))
        if g:
            pool_counts[g][r["pool"]] = pool_counts[g].get(r["pool"], 0) + 1
    pools = {g: (max(c, key=c.get) if c else
                 ("hs_m" if g == "M" else "hs_f"))
             for g, c in pool_counts.items()}

    # ---- running sections: bucket, best per person, rank ------------- #
    buckets = {}
    for r in running:
        g = _genderOfPool(r.get("pool"))
        if not g:
            continue
        bucket, on_board = _snap(float(r["distance"]))
        b = buckets.setdefault((bucket, on_board), {"M": [], "F": []})
        b[g].append(r)

    sections = []
    for (bucket, on_board), by_g in buckets.items():
        tables, left = {}, {}
        for g in ("M", "F"):
            ranked = _bestPer(by_g[g], lambda r: float(r["time_seconds"]),
                              _person)
            rows = [dict(r, sport=sport, distance=bucket)
                    for _v, r in ranked]
            left[g] = max(0, len(rows) - per_table)
            tables[g] = rows[:per_table]
        n = sum(len(by_g[g]) for g in ("M", "F"))
        sections.append({"label": _distLabel(bucket),
                         "dist_note": (f"{bucket}m"
                                       if bucket in _MILE_LABELS else ""),
                         "kind": "running", "distance": bucket,
                         "on_board": on_board, "n": n,
                         "tables": tables, "left": left})
    if sport == "XC":
        sections.sort(key=lambda s: -s["n"])       # most-raced first
    else:
        sections.sort(key=lambda s: s["distance"])

    # ---- hurdle/steeple sections (TF): canonical event, best time ---- #
    # Running-shaped tables (a time, no rating -- the engine never rates
    # these), sorted by the distance their name leads with so the 60H
    # stops sorting after the 300H. No board takes hurdle times, so no
    # view-all link -- the standing rule for off-board sections.
    h_groups = {}
    for r in hurdles:
        canon = (canonicalEvent(r.get("event_short")) or
                 f"#{r.get('event_id')}")
        h_groups.setdefault(canon, []).append(r)
    h_sections = []
    for canon, rows_g in h_groups.items():
        by_g = {"M": [], "F": []}
        for r in rows_g:
            if r.get("gender") in ("M", "F"):
                by_g[r["gender"]].append(r)
        tables, left = {}, {}
        for g in ("M", "F"):
            ranked = _bestPer(
                by_g[g],
                lambda r: (float(t) if (t := r.get("time_seconds")) and
                           float(t) > 0 else None),
                _person)
            rows = [dict(r) for _v, r in ranked]
            left[g] = max(0, len(rows) - per_table)
            tables[g] = rows[:per_table]
        if not (tables["M"] or tables["F"]):
            continue
        name_src = next((r.get("event_short") for r in rows_g
                         if r.get("event_short")), None)
        h_sections.append({
            "label": (displayEvent(name_src) if name_src
                      else f"Event {rows_g[0].get('event_id')}"),
            "dist_note": "", "kind": "hurdles",
            "distance": eventDistance(name_src) if name_src else None,
            "on_board": False, "n": len(rows_g),
            "tables": tables, "left": left})
    h_sections.sort(key=lambda s: (s["distance"] or 1e9, s["label"]))
    sections.extend(h_sections)

    # ---- field sections (TF): canonical event, best mark ------------- #
    f_groups = {}
    for r in field:
        canon = (canonicalEvent(r.get("event_short")) or
                 f"#{r.get('event_id')}")
        f_groups.setdefault(canon, []).append(r)
    f_sections = []
    for canon, rows_g in sorted(f_groups.items()):
        by_g = {"M": [], "F": []}
        for r in rows_g:
            if r.get("gender") in ("M", "F"):
                by_g[r["gender"]].append(r)
        tables, left = {}, {}
        for g in ("M", "F"):
            ranked = _bestPer(
                by_g[g],
                lambda r: (-v if (v := parseMark(r.get("mark"))) is not None
                           else None),
                _person)
            rows = [dict(r) for _v, r in ranked]
            left[g] = max(0, len(rows) - per_table)
            tables[g] = rows[:per_table]
        if not (tables["M"] or tables["F"]):
            continue
        name_src = next((r.get("event_short") for r in rows_g
                         if r.get("event_short")), None)
        f_sections.append({
            "label": (displayEvent(name_src) if name_src
                      else f"Event {rows_g[0].get('event_id')}"),
            "dist_note": "", "kind": "field", "distance": None,
            "on_board": False, "n": len(rows_g),
            "tables": tables, "left": left})
    f_sections.sort(key=lambda s: s["label"])
    sections.extend(f_sections)

    # running names last, for exactly the rows that DISPLAY: the whole
    # point of dropping the per-row lateral
    need = {}
    for sec in sections:
        if sec["kind"] != "running":
            continue
        for g in ("M", "F"):
            for r in sec["tables"][g]:
                if r.get("person_id") and not r.get("name"):
                    need.setdefault(r["person_id"], []).append(r)
    if need:
        info = _athleteInfo(cur, need.keys())
        for pid, need_rows in need.items():
            nm = info.get(pid, {}).get("name")
            for r in need_rows:
                r["name"] = nm

    return {"sections": sections, "years": years, "courses": courses,
            "pools": pools, "year": year_label, "course": course,
            "any": bool(sections)}
