"""
compare.py -- query builders for the head-to-head page (/compare).

Sits beside app.py the way rankings.py and teams.py do: the route stays
thin, the SQL lives here, every value is bound.

★ A MEETING IS THE SAME RACE, NOT THE SAME MEET. Two athletes at one meet
  in different divisions ran different fields on different terms; the
  head-to-head record only counts rows sharing (sport, meet_id, div_id,
  event_id). ranking_results is the source on purpose: it holds exactly
  the rated rows the site's every other number is built from, so the
  record cannot disagree with the pages it links to.

★ AND THE CROSS-FEED DEDUP THE PERFORMANCE BOARD ALREADY TAUGHT. Both
  sources can carry the same physical race, tied by canon_meet_id; both
  athletes duplicate together, so one race becomes two identical meeting
  rows. Deduped on (sport, canon key, both times rounded), same posture
  as getPerformanceRankings.
"""

import datetime

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

# The meeting self-join, per sport. Meet names ride along per sport's own
# meets table; a lateral per row is fine at head-to-head row counts.
_MEETINGS_SQL = {
    "XC": """
        SELECT ra.meet_id, ra.div_id,
               to_char(ra.race_date, 'YYYY-MM-DD') AS date,
               round(ra.distance)::int  AS distance,
               ra.time_seconds AS t_a,  rb.time_seconds AS t_b,
               ra.result_id    AS rid_a, rb.result_id   AS rid_b,
               ra.canon_meet_id,
               (SELECT min(mm.meet_name) FROM meets mm
                 WHERE mm.meet_id = ra.meet_id
                   AND mm.div_id  = ra.div_id) AS meet_name
        FROM   ranking_results ra
        JOIN   ranking_results rb
               ON  rb.sport    = 'XC'
               AND rb.meet_id  = ra.meet_id
               AND rb.div_id   = ra.div_id
               AND rb.person_id = %(b)s
        WHERE  ra.sport = 'XC'
          AND  ra.person_id = %(a)s
          AND  ra.time_seconds > 0 AND rb.time_seconds > 0
        ORDER  BY ra.race_date DESC
    """,
    "TF": """
        SELECT ra.meet_id, ra.div_id,
               to_char(ra.race_date, 'YYYY-MM-DD') AS date,
               round(ra.distance)::int  AS distance,
               ra.time_seconds AS t_a,  rb.time_seconds AS t_b,
               ra.result_id    AS rid_a, rb.result_id   AS rid_b,
               ra.canon_meet_id,
               (SELECT min(mt.meet_name) FROM meets_tf mt
                 WHERE mt.meet_id = ra.meet_id) AS meet_name
        FROM   ranking_results ra
        JOIN   ranking_results rb
               ON  rb.sport    = 'TF'
               AND rb.meet_id  = ra.meet_id
               AND rb.div_id   = ra.div_id
               AND COALESCE(rb.event_id, 0) = COALESCE(ra.event_id, 0)
               AND rb.person_id = %(b)s
        WHERE  ra.sport = 'TF'
          AND  ra.person_id = %(a)s
          AND  ra.time_seconds > 0 AND rb.time_seconds > 0
        ORDER  BY ra.race_date DESC
    """,
}

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
    if seconds is None:
        return None
    s = float(seconds)
    m, rem = divmod(s, 60.0)
    if m >= 60:
        h, m = divmod(int(m), 60)
        return f"{h}:{m:02d}:{rem:04.1f}"
    return f"{int(m)}:{rem:04.1f}"


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

    current = None
    if seasons:
        # The season the reader means by "now": the one raced most recently.
        current = max(seasons, key=lambda s: (s.get("last_race") or
                                              datetime.date.min))
    best = max((float(s["best_rating"]) for s in seasons
                if s.get("best_rating") is not None), default=None)
    return {
        "person_id": pid,
        "name": name,
        "school": (current or {}).get("school"),
        "grade": (current or {}).get("grade"),
        "pool": (current or {}).get("pool"),
        "season_label": (f"{displayYear(current['sport'], current['year'])} "
                         f"{current['sport']}") if current else None,
        "season_sport": current["sport"] if current else None,
        "season_rating": (float(current["mean_rating"])
                          if current and current.get("mean_rating") is not None
                          else None),
        "season_races": int(current["n_races"]) if current else 0,
        "best_rating": best,
        "seasons": seasons,
    }


def meetings(cur, a, b):
    """All same-race rows for the pair, both sports, newest first, deduped
    across feeds. Times and margin are precomputed; the template only
    prints."""
    rows, seen = [], set()
    for sport in ("XC", "TF"):
        cur.execute(_MEETINGS_SQL[sport], {"a": a, "b": b})
        for r in cur.fetchall():
            key = (sport,
                   r["canon_meet_id"] or (r["meet_id"], r["div_id"]),
                   round(float(r["t_a"]), 1), round(float(r["t_b"]), 1))
            if key in seen:
                continue
            seen.add(key)
            t_a, t_b = float(r["t_a"]), float(r["t_b"])
            rows.append({
                "sport": sport,
                "date": r["date"],
                "meet_id": r["meet_id"], "div_id": r["div_id"],
                "meet_name": r["meet_name"] or f"Meet {r['meet_id']}",
                "distance": r["distance"],
                "time_a": fmtTime(t_a), "time_b": fmtTime(t_b),
                # winner: 'a' | 'b' | None on a dead heat at the stored
                # precision. margin always positive, labelled by the template.
                "winner": "a" if t_a < t_b else ("b" if t_b < t_a else None),
                "margin": abs(t_a - t_b),
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


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
