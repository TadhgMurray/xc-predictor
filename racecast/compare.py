"""
compare.py -- query builders for the head-to-head page (/compare).

Sits beside app.py the way rankings.py and teams.py do: the route stays
thin, the SQL lives here, every value is bound.

★ A MEETING IS THE SAME RACE, NOT THE SAME MEET. Two athletes at one meet
  in different divisions ran different fields on different terms; the
  head-to-head record only counts rows sharing (sport, source, meet_id,
  div_id) -- and on track the event and the round as well.

⚠ THE MEETINGS READ results / results_tf, NOT ranking_results (2026-10-05).
  ranking_results has no `source` column, and a meet_id is not a meet: the
  anet and tfrrs id spaces collide (15,096 XC ids, and track's small div
  and event ids collide more easily still). It also holds only rows the
  BOARDS rank -- no professional race, no corrected-distance division -- so
  two athletes who met at a pro meet "had never been in the same race". The
  base tables carry the source, the round and every finish; result_twin
  hides the same run stored twice, exactly as the athlete page does.

★ AND THE CROSS-FEED DEDUP THE PERFORMANCE BOARD ALREADY TAUGHT. Both
  sources can carry the same physical race, tied by canon_meet_id; both
  athletes duplicate together, so one race becomes two identical meeting
  rows. Deduped on (sport, canon key, both times rounded), anet's copy
  first because it is the one with the names.
"""

import datetime

from time_format import format_time

try:                                    # scripts/ is on the site's path
    from result_status import isSentinelTime
except ImportError:                     # standalone use: no sentinel rule
    def isSentinelTime(_t):
        return False

try:                                    # the athlete page's event labels
    from athlete_bests import _tfEvent
except ImportError:
    def _tfEvent(event):
        return None, str(event)

# One name row per person: the panels ath-temp rule, inlined for one id.
_NAME_SQL = """
    SELECT NULLIF(TRIM(first_name), '') AS first_name,
           NULLIF(TRIM(last_name),  '') AS last_name
    FROM   athletes
    WHERE  person_id = %(pid)s
    ORDER  BY (NULLIF(TRIM(last_name), '') IS NOT NULL) DESC
    LIMIT  1
"""

_SEASONS_SQL = """
    SELECT sport, year, pool, mean_rating, best_rating, n_races,
           school, grade, state, last_race
    FROM   athlete_season
    WHERE  person_id = %(pid)s
      AND  mean_rating IS NOT NULL
    ORDER  BY year DESC, sport
"""

# The card's school/grade fallback: the athlete's own newest rated row.
# athlete_season's mode() can come back NULL (thin rows), and right after
# a wipe the table can be empty entirely -- the athlete page's header
# reads the result rows and never blanks, so this card does too.
_LATEST_ROW_SQL = """
    SELECT school, grade
    FROM   ranking_results
    WHERE  person_id = %(pid)s
    ORDER  BY race_date DESC NULLS LAST
    LIMIT  1
"""

# The meeting self-join, per sport, on the base tables (see the header).
# Both sides come in through the person_id indexes; the names, course and
# distance are a lateral per MEETING, which is a few dozen rows at most.
#
# ! EVERY LOOKUP IS SCOPED BY SOURCE. The old reads named a tfrrs race after
#   the colliding anet meet (meets_tf by meet_id alone) or not at all ("Meet
#   25571" for Cowboy Jamboree: `meets` is anet's table, and a tfrrs XC
#   meet's name is in meets_tfrrs). Same lookups as the athlete page's.
# ! NO PERCENT SIGN IN THESE STRINGS, COMMENTS INCLUDED: psycopg2 reads one
#   as a placeholder (app.py get_races, 2026-09-03).
_TWIN_SQL = ("AND NOT EXISTS (SELECT 1 FROM result_twin x WHERE x.sport = "
             "'{sport}' AND x.result_id = {alias}.result_id)")

_MEETINGS_SQL = {
    "XC": """
        WITH pair AS (
            SELECT a.meet_id, a.div_id, a.source, a.canon_meet_id,
                   left(a.date, 10)  AS date,
                   a.time_seconds    AS t_a,  b.time_seconds AS t_b,
                   a.result_id       AS rid_a, b.result_id   AS rid_b
            FROM   results a
            JOIN   results b
                   ON  b.person_id = %(b)s
                   AND b.meet_id   = a.meet_id
                   AND b.div_id    = a.div_id
                   AND b.source    = a.source
            WHERE  a.person_id = %(a)s
              AND  a.time_seconds > 0 AND b.time_seconds > 0
              {twin_a} {twin_b}
        )
        SELECT p.*, NULL::bigint AS event_id, NULL::text AS event_short,
               NULL::text AS round,
               COALESCE(NULLIF(btrim(m.meet_name), ''), mt.meet_name,
                        m.course_name, mt.venue_name)        AS meet_name,
               COALESCE(m.course_name, mt.venue_name)        AS course,
               COALESCE(dov.distance::real, m.distance,
                        (mt.division_distances -> p.div_id::text
                                               ->> 'distance')::real) AS distance
        FROM   pair p
        LEFT JOIN LATERAL (
            SELECT m.meet_name, m.course_name, m.distance
            FROM   meets m
            WHERE  m.meet_id = p.meet_id AND m.div_id = p.div_id
              AND  m.source  = p.source
            LIMIT  1) m ON TRUE
        LEFT JOIN LATERAL (
            SELECT mt.meet_name, mt.venue_name, mt.division_distances
            FROM   meets_tfrrs mt
            WHERE  p.source = 'tfrrs' AND mt.meet_id = p.meet_id
              AND  mt.sport = 'XC'
            LIMIT  1) mt ON TRUE
        LEFT JOIN LATERAL (
            SELECT dov.distance FROM dist_override dov
            WHERE  dov.meet_id = p.meet_id AND dov.div_id = p.div_id
            LIMIT  1) dov ON TRUE
        ORDER  BY p.date DESC, (p.source = 'anet') DESC
    """,
    # ★ THE ROUND IS PART OF THE RACE (EBAL Championship 2022, Murray v
    #   Caldwell: both in the 1600 prelim AND final made FOUR meetings, every
    #   prelim crossed with every final). And a tfrrs row with no event_id
    #   matches on its event name instead: COALESCE(event_id, 0) made every
    #   id-less event at a meet "the same race" as every other.
    "TF": """
        WITH pair AS (
            SELECT a.meet_id, a.div_id, a.source, a.canon_meet_id,
                   a.event_id, a.event_short,
                   NULLIF(btrim(a.round), '') AS round,
                   left(a.date, 10)  AS date,
                   a.time_seconds    AS t_a,  b.time_seconds AS t_b,
                   a.result_id       AS rid_a, b.result_id   AS rid_b
            FROM   results_tf a
            JOIN   results_tf b
                   ON  b.person_id = %(b)s
                   AND b.meet_id   = a.meet_id
                   AND b.source    = a.source
                   AND b.div_id IS NOT DISTINCT FROM a.div_id
                   AND (b.event_id = a.event_id
                        OR (a.event_id IS NULL AND b.event_id IS NULL
                            AND b.event_short = a.event_short))
                   AND NULLIF(btrim(b.round), '')
                       IS NOT DISTINCT FROM NULLIF(btrim(a.round), '')
            WHERE  a.person_id = %(a)s
              AND  a.time_seconds > 0 AND b.time_seconds > 0
              AND  COALESCE(a.is_field, 0) = 0 AND COALESCE(b.is_field, 0) = 0
              AND  COALESCE(a.is_relay, 0) = 0 AND COALESCE(b.is_relay, 0) = 0
              {twin_a} {twin_b}
        )
        SELECT p.*, NULL::text AS course, NULL::real AS distance,
               COALESCE(NULLIF(btrim(m.meet_name), ''), mt.meet_name) AS meet_name
        FROM   pair p
        LEFT JOIN LATERAL (
            SELECT m.meet_name FROM meets_tf m
            WHERE  m.meet_id = p.meet_id AND m.source = p.source
            LIMIT  1) m ON TRUE
        LEFT JOIN LATERAL (
            SELECT mt.meet_name FROM meets_tfrrs mt
            WHERE  p.source = 'tfrrs' AND mt.meet_id = p.meet_id
              AND  mt.sport = 'TF'
            LIMIT  1) mt ON TRUE
        ORDER  BY p.date DESC, (p.source = 'anet') DESC
    """,
}


def meetingsSql(sport, twins=True):
    """The meeting query for one sport, with the twin anti-join on both
    sides when result_twin exists (absent, nothing is hidden -- the athlete
    page's own posture, app._hasResultTwin)."""
    fill = {"twin_a": "", "twin_b": ""}
    if twins:
        fill = {"twin_a": _TWIN_SQL.format(sport=sport, alias="a"),
                "twin_b": _TWIN_SQL.format(sport=sport, alias="b")}
    return _MEETINGS_SQL[sport].format(**fill)


def _hasTwinTable(cur):
    try:
        cur.execute("SELECT to_regclass('result_twin') IS NOT NULL AS ok")
        row = cur.fetchone()
        return bool(row["ok"] if isinstance(row, dict) else row[0])
    except Exception:                                # noqa: BLE001
        cur.connection.rollback()
        return False

_BESTS_SQL = """
    SELECT sport, round(distance)::int AS dist,
           min(time_seconds) AS best
    FROM   ranking_results
    WHERE  person_id = %(pid)s
      AND  time_seconds > 0
      AND  distance > 0
    GROUP  BY sport, round(distance)::int
"""

# The best single race by RATING, with the context the HS view needs.
_BEST_RATING_SQL = """
    SELECT speed_rating, pool, sport, distance, result_id
    FROM   ranking_results
    WHERE  person_id = %(pid)s
      AND  speed_rating IS NOT NULL
    ORDER  BY speed_rating DESC
    LIMIT  1
"""

# The rating trajectory for the overlay chart: every rated race, dated,
# with the meet name for tooltips. Per sport, because the name lives in a
# different table for each.
_SERIES_SQL = {
    "XC": """
        SELECT to_char(r.race_date, 'YYYY-MM-DD') AS date,
               r.speed_rating, r.result_id, r.sport, r.distance, r.pool,
               r.year,
               (SELECT min(mm.meet_name) FROM meets mm
                 WHERE mm.meet_id = r.meet_id
                   AND mm.div_id  = r.div_id) AS meet_name
        FROM   ranking_results r
        WHERE  r.person_id = %(pid)s
          AND  r.sport = 'XC'
          AND  r.speed_rating IS NOT NULL
          AND  r.race_date IS NOT NULL
        ORDER  BY r.race_date
    """,
    "TF": """
        SELECT to_char(r.race_date, 'YYYY-MM-DD') AS date,
               r.speed_rating, r.result_id, r.sport, r.distance, r.pool,
               r.year,
               (SELECT min(mt.meet_name) FROM meets_tf mt
                 WHERE mt.meet_id = r.meet_id) AS meet_name
        FROM   ranking_results r
        WHERE  r.person_id = %(pid)s
          AND  r.sport = 'TF'
          AND  r.speed_rating IS NOT NULL
          AND  r.race_date IS NOT NULL
        ORDER  BY r.race_date
    """,
}

# The TF rungs worth a row, in display order. XC gets its own fastest-5K
# row; anything else either athlete raced stays off the table rather than
# padding it with one-sided oddities.
_BEST_RUNGS = (400, 800, 1500, 1600, 3000, 3200, 5000, 10000)


def fmtTime(seconds):
    """! time_format.format_time, the site's one formatter (sweep 2026-10-10):
    the old m:ss.s here split before rounding and printed 959.96 as 15:60.0."""
    if seconds is None:
        return None
    return format_time(seconds)


def displayYear(sport, year):
    """A TF season is stored under the year it opens in and named for the
    year it ends in, everywhere a person reads it."""
    return int(year) + 1 if sport == "TF" else int(year)


def athleteCard(cur, pid):
    """Name plus the freshest season line, or None for an unknown id."""
    cur.execute(_NAME_SQL, {"pid": pid})
    row = cur.fetchone()
    cur.execute(_SEASONS_SQL, {"pid": pid})
    seasons = cur.fetchall()
    if row is None and not seasons:
        return None
    name = " ".join(p for p in
                    ((row or {}).get("first_name"),
                     (row or {}).get("last_name")) if p) or f"Athlete {pid}"

    current = rated = None
    if seasons:
        # The season the reader means by "now": the one raced most recently.
        # It names the team, the grade and the state, as the athlete page's
        # latest_team does.
        current = max(seasons, key=lambda s: (s.get("last_race") or
                                              datetime.date.min))
        rated = headerSeason(seasons)
    best = max((float(s["best_rating"]) for s in seasons
                if s.get("best_rating") is not None), default=None)
    # ★ THE HS BEST IS ITS OWN MAX (owner, 2026-10-09): seasons span pools,
    #   so the best race on the HS scale need not be the own-pool best.
    from pool_view import repFactor
    best_hs = max((float(s["best_rating"])
                   * (repFactor(s.get("pool"), s["sport"]) or 1.0)
                   for s in seasons if s.get("best_rating") is not None),
                  default=None)

    school = (current or {}).get("school")
    grade = (current or {}).get("grade")
    if not school or not grade:
        cur.execute(_LATEST_ROW_SQL, {"pid": pid})
        latest = cur.fetchone() or {}
        school = school or latest.get("school")
        grade = grade or latest.get("grade")

    return {
        "person_id": pid,
        "name": name,
        "school": school,
        "grade": grade,
        # ★ THE CARD KEPT THE POOL AND DROPPED THE STATE, so compare.html had
        #   only a bare name to render and fell back to schoolLabel -- the
        #   name's BIGGEST cluster (owner, 2026-09-17: "Oregon (WI)" on the
        #   panels, same cause). _SEASONS_SQL has selected `state` all along.
        #   It is a CONTEXT for school_identity, not a label: teamState takes
        #   the college directory first for a college pool.
        "state": (current or {}).get("state"),
        "pool": (current or {}).get("pool"),
        # ★ THE RATING IS THE ATHLETE PAGE'S HEADER SEASON, NOT "NOW"
        #   (2026-10-05). The newest season can be two races deep: Trey
        #   Caldwell's card read "2026 TF 111.6, 2 races" while his own page
        #   heads with 2025 TF at 135.5, the boards' three-race rule. Two
        #   pages quoting two ratings for one athlete is a defect whichever
        #   is right; headerSeason is that page's ORDER BY. Its pool rides
        #   in season_pool, because the HS stamp must use the RATING's pool,
        #   not the team season's.
        "season_label": (f"{displayYear(rated['sport'], rated['year'])} "
                         f"{rated['sport']}") if rated else None,
        "season_sport": rated["sport"] if rated else None,
        "season_pool": rated.get("pool") if rated else None,
        "season_rating": (float(rated["mean_rating"])
                          if rated and rated.get("mean_rating") is not None
                          else None),
        "season_races": int(rated["n_races"]) if rated else 0,
        "best_rating": best,
        "best_rating_hs": round(best_hs, 1) if best_hs is not None else None,
        "seasons": seasons,
    }


def headerSeason(seasons):
    """The season the athlete page's header rates (app.athlete): a rated
    season first, then one with three races or more, then the latest raced.
    _SEASONS_SQL only returns rated seasons, so the first key is implicit."""
    if not seasons:
        return None
    return min(seasons, key=lambda s: (
        0 if int(s.get("n_races") or 0) >= 3 else 1,
        -(s.get("last_race") or datetime.date.min).toordinal(),
        -int(s.get("year") or 0),
        -int(s.get("n_races") or 0)))


def meetings(cur, a, b):
    """All same-race rows for the pair, both sports, newest first, deduped
    across feeds. Times and margin are precomputed; the template only
    prints."""
    rows, seen = [], set()
    twins = _hasTwinTable(cur)
    for sport in ("XC", "TF"):
        cur.execute(meetingsSql(sport, twins), {"a": a, "b": b})
        for r in cur.fetchall():
            t_a, t_b = float(r["t_a"]), float(r["t_b"])
            # a non-finish sentinel is not a time (issue 59): no meeting
            if isSentinelTime(t_a) or isSentinelTime(t_b):
                continue
            event = eventLabel(sport, r)
            # ! NO EVENT IN THE KEY: the two feeds' copies of one race can
            #   resolve different distances (or none) and spell the round
            #   differently; the pair of times already says which race.
            key = (sport,
                   r["canon_meet_id"] or (r["source"], r["meet_id"],
                                          r["div_id"]),
                   round(t_a, 1), round(t_b, 1))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "sport": sport,
                "date": r["date"],
                "meet_id": r["meet_id"], "div_id": r["div_id"],
                "meet_name": r["meet_name"] or "Race results",
                "course": (r["course"] if r.get("course")
                           and r["course"] != r["meet_name"] else None),
                "event": event,
                "href": raceHref(sport, r),
                "time_a": fmtTime(t_a), "time_b": fmtTime(t_b),
                # winner: 'a' | 'b' | None on a dead heat at the stored
                # precision. margin always positive, labelled by the template.
                "winner": "a" if t_a < t_b else ("b" if t_b < t_a else None),
                "margin": abs(t_a - t_b),
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def eventLabel(sport, r):
    """'5000m' for an XC race; for track the athlete page's own event label
    ('1600m', 'Mile') plus the round when the feed sent one."""
    if sport == "XC":
        d = r.get("distance")
        return f"{int(round(float(d)))}m" if d else None
    label = _tfEvent(r["event_short"])[1] if r.get("event_short") else None
    rnd = r.get("round")
    return " ".join(p for p in (label, rnd) if p) or None


def raceHref(sport, r):
    """The race page link the athlete page would draw for A's row.

    ⚠ TRACK IS THREE PARTS, /race/tf/<meet>/<event>/<div>; this page built
      two and every track meeting 404ed. ?r= pins the page to the row's own
      feed, since the id spaces collide; with no event or division (tfrrs
      rows can lack both) the meet page is where the row lives."""
    rid = r["rid_a"]
    if sport == "XC" and r.get("div_id") is not None:
        return f"/race/xc/{r['meet_id']}/{r['div_id']}?r={rid}"
    if (sport == "TF" and r.get("event_id") is not None
            and r.get("div_id") is not None):
        return f"/race/tf/{r['meet_id']}/{r['event_id']}/{r['div_id']}?r={rid}"
    return f"/meet/{sport.lower()}/{r['meet_id']}?r={rid}"


def record(mtgs):
    """(wins_a, wins_b, ties, avg_margin_signed) -- positive favours A."""
    wa = sum(1 for m in mtgs if m["winner"] == "a")
    wb = sum(1 for m in mtgs if m["winner"] == "b")
    ties = len(mtgs) - wa - wb
    if mtgs:
        signed = [m["margin"] if m["winner"] == "a" else -m["margin"]
                  for m in mtgs if m["winner"]]
        avg = sum(signed) / len(signed) if signed else 0.0
    else:
        avg = 0.0
    return wa, wb, ties, avg


def seasonRows(card_a, card_b):
    """Merge the two athletes' season lists into one keyed table."""
    table = {}
    for side, card in (("a", card_a), ("b", card_b)):
        for s in card["seasons"]:
            key = (displayYear(s["sport"], s["year"]), s["sport"])
            cell = table.setdefault(key, {"year": key[0], "sport": key[1]})
            cell[side] = {"rating": float(s["mean_rating"]),
                          "races": int(s["n_races"]),
                          "pool": s.get("pool"), "sport": s["sport"]}
    rows = [table[k] for k in sorted(table, reverse=True)]
    for r in rows:
        ra = r.get("a", {}).get("rating")
        rb = r.get("b", {}).get("rating")
        if ra is not None and rb is not None and abs(ra - rb) > 1e-9:
            r["edge"] = "a" if ra > rb else "b"
            r["edge_by"] = abs(ra - rb)
    return rows


def stampEdgeHs(rows):
    """row["hs_edge_by"]: the season edge on the HS scale, from the stamped
    cells. Call after stampBoardRows has stamped s.a / s.b.

    ! NONE WHEN THE HS VIEW WOULD FLIP THE LEADER (owner, 2026-10-09). Two
      pools in one season (a college man against a college woman) can trade
      places on the HS scale, and the cell names the own-pool leader; a
      negative "+X" under his name would be wrong, so rv() keeps own.
    """
    for r in rows:
        r["hs_edge_by"] = None
        if not r.get("edge"):
            continue
        ha = (r.get("a") or {}).get("hs_rating")
        hb = (r.get("b") or {}).get("hs_rating")
        if ha is None or hb is None:
            continue
        d = ha - hb if r["edge"] == "a" else hb - ha
        if d > 0:
            r["hs_edge_by"] = round(d, 1)
    return rows


def bestRows(cur, a, b):
    """PR table: TF rungs either athlete owns, then the fastest XC 5K."""
    bests = {}
    for side, pid in (("a", a), ("b", b)):
        cur.execute(_BESTS_SQL, {"pid": pid})
        for r in cur.fetchall():
            bests.setdefault((r["sport"], int(r["dist"])), {})[side] = \
                float(r["best"])
    rows = []
    for rung in _BEST_RUNGS:
        cell = bests.get(("TF", rung))
        if not cell:
            continue
        rows.append(_bestRow(f"{rung}m", cell))
    xc = bests.get(("XC", 5000))
    if xc:
        rows.append(_bestRow("XC 5000m (fastest)", xc))
    return rows


def _bestRow(label, cell):
    a, b = cell.get("a"), cell.get("b")
    row = {"label": label,
           "a": fmtTime(a), "b": fmtTime(b)}
    if a is not None and b is not None and abs(a - b) > 1e-9:
        row["edge"] = "a" if a < b else "b"
    return row


def bestRatingRows(cur, a, b):
    """One row per athlete: their single best-rated race, with the context
    (pool, sport, distance, result_id) stampRowsHs needs for the HS view."""
    out = {}
    for side, pid in (("a", a), ("b", b)):
        cur.execute(_BEST_RATING_SQL, {"pid": pid})
        r = cur.fetchone()
        if r:
            out[side] = {"speed_rating": float(r["speed_rating"]),
                         "pool": r["pool"], "sport": r["sport"],
                         "distance": r["distance"],
                         "result_id": r["result_id"]}
    return out


def ratingSeries(cur, pid):
    """Every rated race, both sports, date-sorted, ready for stamping."""
    rows = []
    for sport in ("XC", "TF"):
        cur.execute(_SERIES_SQL[sport], {"pid": pid})
        rows.extend({"date": r["date"],
                     "speed_rating": float(r["speed_rating"]),
                     "result_id": r["result_id"], "sport": r["sport"],
                     "distance": r["distance"], "pool": r["pool"],
                     "year": int(r["year"]),
                     "meet_name": r["meet_name"]}
                    for r in cur.fetchall())
    rows.sort(key=lambda r: r["date"])
    return rows


def chartPoints(series):
    """Stamped series rows -> the athlete chart's own point shape
    ({d, v, vh, meet, y, sp} -- see athlete_chart_data), so /compare and
    the athlete page draw from one contract."""
    pts = []
    for r in series:
        p = {"d": r["date"], "v": round(r["speed_rating"], 4),
             "meet": r["meet_name"],
             "y": displayYear(r["sport"], r["year"]),
             "sp": r["sport"]}
        if r.get("hs_rating") is not None:
            p["vh"] = round(float(r["hs_rating"]), 4)
        pts.append(p)
    return pts
