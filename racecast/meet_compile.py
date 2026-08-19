"""
meet_compile.py -- compiled results and team scores for one meet.

WHAT "COMPILED" MEANS
    A cross country meet runs several races: varsity boys, JV boys, varsity
    girls, and so on -- separate divisions, separate starts. A compiled result
    merges every division that ran the SAME DISTANCE and the SAME GENDER into
    one list, ordered by time.

★ ORDERED BY TIME, NOT RATING. Everyone in a compiled group ran the same
  course on the same day, so the raw time already controls for everything a
  rating would. Rating would be near-monotonic with it and would only add
  noise from the difficulty solve.

⚠ IT IS SYNTHETIC AND MUST BE LABELLED AS SUCH. Varsity and JV genuinely raced
  separately -- a JV runner never had the chance to sit on a varsity pack. The
  compiled list answers "who ran fastest", not "who beat whom".

TEAM SCORES, TWO WAYS
    published   what the meet actually reported, out of meet_extras. Includes
                whatever local scoring quirks applied, so it beats recomputing.
    computed    standard 5-scorers-plus-2-displacers, used for the compiled
                list (which has no published equivalent, since it never
                happened) and as a fallback where nothing was published.

⚠ NO COMPUTED SCORES FOR TRACK. Track scoring is per event, per place, with
  point tables that vary by meet type and relays counting differently --
  deriving it means encoding a rulebook that changes by state. Measured:
  meet_extras holds published scores for 105,507 XC meets and exactly ONE TF
  meet, so there is nothing to fall back on either. Track shows results without
  scores rather than a number that is wrong.
"""

import json

# Standard cross country scoring.
SCORERS = 5
DISPLACERS = 2


# ------------------------------------------------------------------ #
#  1. COMPILED RESULTS
# ------------------------------------------------------------------ #

def compiledResults(cur, meet_id):
    """Every division of a meet, merged by (distance, gender).

    Returns [{distance, gender, divisions, results:[...]}], biggest group
    first -- the varsity race is almost always the one being looked for, and
    it is almost always the biggest.
    """
    # ⚠ LEFT JOIN, AND A tfrrs FALLBACK. `meets` is ANET-ONLY -- 812,079 rows,
    #   zero tfrrs -- so an INNER JOIN silently returns nothing for a tfrrs
    #   meet and drops any anet division whose metadata is missing. That is
    #   why compiled results came back empty; get_meet_header carries the same
    #   warning for the same reason.
    #
    # ★ AND THE TFRRS DISTANCE IS PER DIVISION, IN JSONB. meets_tfrrs.distance
    #   is null on essentially every row; the real value lives in
    #   division_distances keyed on div_id AS TEXT. This is what
    #   speed_ratings_db._xcQuery already does, and its comment records the
    #   cost of reading the scalar instead: a women's 5000 and a men's 8000 at
    #   one meet sharing one distance.
    cur.execute("""
        SELECT r.result_id, r.person_id, r.place, r.time_seconds, r.grade,
               r.school, r.speed_rating, r.div_id,
               (round(COALESCE(
                   m.distance,
                   (mt.division_distances -> r.div_id::text ->> 'distance')::real
                ) / 100.0) * 100)::int                AS distance,
               COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')       AS name,
               a.gender
        FROM   results r
        LEFT JOIN meets m ON m.meet_id = r.meet_id
                         AND m.div_id  = r.div_id
                         AND m.source  = r.source
        LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                                AND mt.sport   = 'XC' 
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name,
                   x.gender
            FROM   athletes x
            WHERE  x.athlete_id = r.person_id
              AND  x.gender IN ('M', 'F')
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) a ON TRUE
        WHERE  r.meet_id = %(meet)s
          AND  r.time_seconds IS NOT NULL
          AND  r.time_seconds < 999999
          AND  COALESCE(
                 m.distance,
                 (mt.division_distances -> r.div_id::text ->> 'distance')::real
               ) > 0
        ORDER  BY distance, a.gender, r.time_seconds
    """, {"meet": meet_id})

    groups = {}
    for row in cur.fetchall():
        # ⚠ GENDER CAN BE NULL, and those rows must not silently merge into a
        #   group. They get their own bucket, which the page can label rather
        #   than pretending they belong to one side.
        key = (row["distance"], row["gender"] or "?")
        g = groups.setdefault(key, {"distance": row["distance"],
                                    "gender": row["gender"] or "?",
                                    "divisions": set(), "results": []})
        g["divisions"].add(row["div_id"])
        g["results"].append({
            "result_id": row["result_id"],
            "person_id": row["person_id"],
            "name": (row["name"] or "").strip() or "Unknown",
            "school": row["school"],
            "grade": row["grade"],
            "div_id": row["div_id"],
            "time_seconds": float(row["time_seconds"]),
            "speed_rating": (round(float(row["speed_rating"]), 1)
                             if row["speed_rating"] is not None else None),
            # ⚠ NOT row["place"]. That column is not the finishing position
            #   within the division -- race.html has never trusted it, it
            #   renders Place from `loop.index` over the ordered results. Using
            #   the stored value produced numbers running past 50 in a division
            #   of ten. Derived below, the same way race.html derives it.
            "division_place": None,
        })

    # ★ THE DIVISION PLACE IS DERIVED, ACROSS THE WHOLE MEET, BEFORE GROUPING.
    #   A division's finishing order is its own results by time -- and it has
    #   to be counted over every row of that division, not just the ones in
    #   this (distance, gender) group, or a division split across groups would
    #   restart its numbering in each.
    by_div = {}
    for g in groups.values():
        for r in g["results"]:
            by_div.setdefault(r["div_id"], []).append(r)
    for rows in by_div.values():
        rows.sort(key=lambda r: r["time_seconds"])
        for i, r in enumerate(rows, start=1):
            r["division_place"] = i

    out = []
    for g in groups.values():
        # The compiled place: position in the MERGED list, which is what makes
        # this different from the division place derived above.
        for i, r in enumerate(g["results"], start=1):
            r["place"] = i
        g["divisions"] = sorted(g["divisions"])
        g["scores"] = scoreRows(g["results"])
        out.append(g)

    out.sort(key=lambda g: -len(g["results"]))
    return out


# ------------------------------------------------------------------ #
#  2. SCORING
# ------------------------------------------------------------------ #

def scoreRows(rows):
    """Team scores from an ordered list of finishers.

    `rows` must already be in finishing order and carry `place`.

    ⚠ DISPLACERS COUNT EVEN THOUGH THEY DO NOT SCORE. Runners 6 and 7 push
      every later finisher's place up, which is the whole tactical point of
      depth -- dropping them would score a deep team identically to a
      top-heavy one.

    ⚠ AND PLACES ARE RENUMBERED AFTER REMOVING INCOMPLETE TEAMS. A school with
      four runners cannot score, and by the rules its runners are lifted out
      and everyone behind them moves up. Scoring against raw finishing places
      instead inflates every complete team's total.
    """
    counts = {}
    for r in rows:
        if r.get("school"):
            counts[r["school"]] = counts.get(r["school"], 0) + 1
    full = {s for s, n in counts.items() if n >= SCORERS}

    scoring, place = {}, 0
    for r in rows:
        if r.get("school") not in full:
            continue
        place += 1
        scoring.setdefault(r["school"], []).append({**r, "score_place": place})

    out = []
    for school, runners in scoring.items():
        out.append({
            "school": school,
            "points": sum(x["score_place"] for x in runners[:SCORERS]),
            "runners": runners[:SCORERS + DISPLACERS],
        })

    # ⚠ TIES ARE BROKEN BY THE SIXTH RUNNER, as in the real rules. Without it
    #   two teams on 64 points sort arbitrarily -- and Spectrum and Elk River
    #   tied on exactly that in the sample data.
    def sixth(t):
        return (t["runners"][SCORERS]["score_place"]
                if len(t["runners"]) > SCORERS else 10 ** 6)

    out.sort(key=lambda t: (t["points"], sixth(t)))
    for i, t in enumerate(out, start=1):
        t["place"] = i

    incomplete = [{"school": s, "n": n} for s, n in counts.items()
                  if s not in full]
    return {"teams": out,
            # Shown, not dropped: "you are two runners short" is information.
            "incomplete": sorted(incomplete, key=lambda x: -x["n"])}


# ------------------------------------------------------------------ #
#  3. PUBLISHED SCORES
# ------------------------------------------------------------------ #

def publishedScores(cur, meet_id):
    """What the meet reported, keyed (div_id, gender). {} when absent.

    ⚠ sport = 'xc', LOWERCASE, AND THIS IS NOT A TYPO. meet_extras holds both
      cases and they are different scrapers: lowercase rows come from anet and
      carry team_scores_json, uppercase rows come from tfrrs and never do.
      Measured -- xc: 105,507 meets, ALL with scores. XC: 16,402, NONE.
      Querying 'XC' finds the wrong 16,402 rows and reports no scores exist.
    """
    cur.execute("""
        SELECT team_scores_json
        FROM   meet_extras
        WHERE  meet_id = %s AND lower(sport) = 'xc'
          AND  team_scores_json IS NOT NULL
        LIMIT  1
    """, (meet_id,))
    row = cur.fetchone()
    if not row or not row["team_scores_json"]:
        return {}

    blob = row["team_scores_json"]
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except ValueError:
            return {}
    if not isinstance(blob, list):
        return {}

    out = {}
    for entry in blob:
        div = entry.get("DivisionID")
        gender = entry.get("Gender")
        if div is None:
            continue
        out.setdefault((int(div), gender), []).append({
            "school": entry.get("Name") or entry.get("rawName"),
            "points": entry.get("Points"),
            "place": entry.get("Place"),
        })

    for teams in out.values():
        teams.sort(key=lambda t: (t["place"] is None, t["place"]))
    return out