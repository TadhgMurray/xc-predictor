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
import os
import re
import sys

from division_group import divisionGroup, groupRank, groupLabel

# ★ WHY THIS IS NOT level_graph._JUNK, WHICH ANSWERS THE SAME QUESTION.
#   The engine's pattern carries `^unat` -- an unanchored PREFIX, so it also
#   swallows Unatego Central, a real school district in New York. In the
#   level graph that costs one node out of ~100k and nobody can see it. In
#   team scoring it deletes a team that actually raced from the results
#   page, which is the most visible kind of wrong this file can produce.
#
#   The two patterns want different error trades -- the graph would rather
#   drop a real school than admit a fake one, and scoring would rather show
#   a fake team than hide a real one -- so they are deliberately separate,
#   and this comment is the link between them. Keep them in step in SPIRIT,
#   not character for character.
#
# ! WHICH TOKENS MAY MATCH ANYWHERE, AND WHICH MUST BE THE WHOLE STRING:
#     unattached / unaffiliated   anywhere. No school is named with them,
#                                 and the corpus writes "Unattached - Nike".
#     individual / independent    WHOLE STRING ONLY. "Individual Learning
#                                 Academy" and "Independence HS" are schools;
#                                 a bare "Individual" is a placeholder.
#     una / unat / unatt          WHOLE STRING ONLY, for the same reason
#                                 Unatego exists.
_NOT_A_TEAM = re.compile(r"""
      ^\s*$                      # blank, and the scrapers do write blanks
    | ^0$                        # the team_id=0 sentinel, as a string
    | ^-+$                       # a dash standing in for "none"
    | ^\?+$                      # ???
    | ^n/?a$                     # n/a, na
    | ^none$
    | ^no\s+school$
    | ^una?t{0,2}\.?$            # UNA, UNAT, UNATT, with an optional dot
    # ! THE PLURAL IS UNANCHORED, unlike its neighbours: NXN-style entries
    #   read "SW Individuals -6", one fake school per entrant, and the
    #   anchored form let every one count as a team -- each earned a
    #   team-place marker and a line in the incomplete-teams list. The
    #   SINGULAR stays anchored: "Individual" is imaginable inside a real
    #   school name, "Individuals" is not.
    | ^individuals?$
    | \bindividuals\b
    | ^independent$
    | \bunattached\b
    | \bunaffiliated\b

    # ★ A NATIONAL TEAM IS NOT A SCHOOL TEAM. At an international meet the
    #   school column holds the country, and five athletes wearing USA are a
    #   selection from the whole country -- the same argument as Unattached,
    #   only stronger: nobody attends it.
    #
    # ⚠ AND BARE COUNTRY NAMES ARE OFF LIMITS, however tempting. American
    #   towns are named after countries and their high schools take the name:
    #   Denmark, Peru, Cuba, Poland, Norway, Lebanon, Mexico and China Spring
    #   are all real US schools -- pro_flag's own header cites "Denmark High
    #   School" as the string that broke a simpler rule. So the test needs a
    #   TEAM MARKER, not a place: "Team Canada" is a national team, "Canada"
    #   is a village in New York.
    #
    # ! USA ALONE IS THE ONE EXCEPTION, whole-string only. No American school
    #   is named "USA" -- but "USA" is a substring of nothing safe either
    #   ("Sausalito", "Susa"), which is why this is anchored and the ones
    #   above are not.
    | ^u\.?s\.?a\.?$
    | ^united\s+states$
    | ^team\s+[a-z][a-z.\s'-]{2,}$
    | \bnational\s+team\b
""", re.IGNORECASE | re.VERBOSE)


def isTeam(school):
    """Is this school string a real team, or a placeholder for having none?

    ★ UNATTACHED IS NOT A TEAM, AND FIVE UNATTACHED RUNNERS ARE NOT A SQUAD.
      They share one string because none of them has a school -- not because
      they represent the same one -- so scoring them together invents a team
      out of exactly the runners who have none, and at a big open meet that
      invented team can beat real ones.

    ⚠ THE SAME STRING IS LEFT ALONE ELSEWHERE ON PURPOSE. panels._NON_SCHOOL
      deliberately omits 'Unattached' because for POOLING these are real kids
      at open meets whose grade still says what level they are. Being a real
      athlete and being a team are different questions; this answers only
      the second.
    """
    return bool(school and school.strip()) and not _NOT_A_TEAM.search(school)

# Standard cross country scoring.
SCORERS = 5
DISPLACERS = 2


# ------------------------------------------------------------------ #
#  1. COMPILED RESULTS
# ------------------------------------------------------------------ #

# ★ A ROW'S GENDER HERE IS ITS RACE'S, NOT ITS ATHLETE'S (owner, 2026-09-29,
#   NCAA DI 2024: "the compiled results [are] not like the actual races at
#   all and having random extra distances/races"). The grouping read the
#   athlete first and the division second, so every runner whose person
#   was wrong or blank left the race he ran: two men under a woman's
#   profile became a "Girls 10000m", five with no gender a "- 6000m" and
#   a "- 10000m" -- none of them races anyone ran. The division is the
#   race, so its word decides; where it has none (anet's "Collegiate",
#   "Varsity"), the field's own runners vote, with 04d's numbers
#   (person_gender.FIELD_MIN / FIELD_SHARE); only a division with neither --
#   a genuinely mixed race -- falls back to the athlete.
_DIV_LABEL = "COALESCE(m.division, mt.division_distances -> r.div_id::text ->> 'div_name')"


def _siblingPath():
    """engine/ and scripts/ on sys.path, for the lazy imports below."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub in ("engine", "scripts"):
        d = os.path.join(root, sub)
        if d not in sys.path:
            sys.path.append(d)


def _personGender():
    """engine/person_gender, imported when first asked for: it pulls in the
    database module, which a page import should not need."""
    _siblingPath()
    import person_gender
    return person_gender


# ------------------------------------------------------------------ #
#  0. WHO KEEPS A PLACE
# ------------------------------------------------------------------ #

# ★ ONE RULE FOR A PLACE, FOR THE RACE PAGE AND THE COMPILED PAGE (sweep
#   2026-10-10). This lived in app.py as _xcPlaced/_stampXcPlaces, which
#   the race page used and the compiled page could not -- this module
#   cannot import the app -- so a DQ took a compiled place, scored, and
#   displaced. It lives here now and app.py's names delegate to it.
#
# ! A DQ IS A RESULT THAT HAPPENED: its TIME is real (it stays in the list,
#   in time order) and its PLACE is void (no place, no points, displaces
#   nobody). result_status.kind is the one rule; the 999999 sentinel is its
#   fallback for rows stored before `status` existed.
_RS = []


def _resultStatus():
    """scripts/result_status, imported once (pure; no database)."""
    if not _RS:
        _siblingPath()
        import result_status
        _RS.append(result_status)
    return _RS[0]


def xcPlaced(row):
    """A finisher who keeps a place: not a DNF/DNS, and not a DQ (status DQ
    or FS -- the time is real, the place is void)."""
    return _resultStatus().kind(row.get("status"),
                                row.get("time_seconds")) == "ok"


def stampXcPlaces(rows, key="pl", status_key="pl_status"):
    """row[key]: the place among placed finishers, in the rows' order; None
    for the rest, which get row[status_key] ('DQ', 'DNF', 'DNS' -- the
    feed's own letters when it sent them, else a dash). In place."""
    normalise = _resultStatus().normalise
    n = 0
    for r in rows:
        if xcPlaced(r):
            n += 1
            r[key], r[status_key] = n, None
        else:
            r[key] = None
            # ! the feed's letters, or a dash: the bare sentinel cannot say
            #   whether it was a DNF or a DNS
            r[status_key] = normalise(r.get("status")) or " - "
    return rows


def rowGenderSql(athlete_gender="a.gender"):
    """SQL for one row's compiled gender, over the joins below (m, mt, r)
    and the athlete lateral; a window over the row's division."""
    _pg = _personGender()
    w = "OVER (PARTITION BY r.source, r.div_id)"
    fm = f"count(*) FILTER (WHERE {athlete_gender} = 'M') {w}"
    ff = f"count(*) FILTER (WHERE {athlete_gender} = 'F') {w}"
    return (f"COALESCE({_pg.labelExpr(_DIV_LABEL)}, "
            f"CASE WHEN {fm} >= {_pg.FIELD_MIN} AND {fm} >= {_pg.FIELD_SHARE} * {ff} THEN 'M' "
            f"WHEN {ff} >= {_pg.FIELD_MIN} AND {ff} >= {_pg.FIELD_SHARE} * {fm} THEN 'F' END, "
            f"{athlete_gender})")


# ★ THE POOL THE RATING WAS COMPUTED IN RIDES ON THE ROW (2026-09-30).
#   pool_view.stampRowsHs reads the row's own rating_pool first -- written by
#   the go-live beside speed_rating -- and ranking_results only when it is
#   absent; a board table a failed step 10 left behind prices a row on the
#   pool its season USED to be in. The column arrives with the go-live
#   (issue 171), so it is probed, never assumed: app._ratingPoolCol's rule,
#   restated because this module cannot import the app.
_RESULTS_COLS = {}


def _hasResultsCol(cur, col):
    """Does `results` carry `col` yet? A "yes" is cached, a "no" asked again
    (the column arrives with a deploy, not with a restart)."""
    have = _RESULTS_COLS.get(col)
    if have is None:
        try:
            cur.execute("""SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'results'
                             AND column_name = %s""", (col,))
            have = cur.fetchone() is not None
        except Exception:                                # noqa: BLE001
            cur.connection.rollback()
            have = False
        if have:
            _RESULTS_COLS[col] = True
    return have


def _ratingPoolSql(cur):
    return ("r.rating_pool" if _hasResultsCol(cur, "rating_pool")
            else "NULL::text AS rating_pool")


# ★ AND THE STATUS (sweep 2026-10-10), for the DQ rule (xcPlaced): a DQ row
#   carries a real time, so without the column it reads as a finisher. The
#   scrapers add it on their first save after deploy -- app._hasResultsStatus
#   probes the same thing for the race page.
def _statusSql(cur):
    return ("r.status" if _hasResultsCol(cur, "status")
            else "NULL::text AS status")


def compiledResults(cur, meet_id, source=None):
    """Every division of a meet, merged by (distance, gender).

    ⚠ `source` MATTERS WHEREVER TWO MEETS SHARE ONE meet_id. The anet and
      tfrrs id spaces overlap (15,096 XC meet_ids hold rows from both), and
      merging by (distance, gender) across sources compiles two different
      real-world meets into one imaginary race. Callers on a colliding meet
      pass the source they are showing; None keeps the old behaviour.

    Returns [{distance, gender, levels:[...], level, divisions, date, results,
    scores}], biggest group first -- the varsity race is almost always the
    one being looked for, and it is almost always the biggest.

    ★ SPLIT BY LEVEL (owner, 2026-10-10). A (distance, gender) group used to
      merge EVERY division -- and a JV runner merged into the varsity list
      takes a varsity scorer's place and pushes every team behind him down:
      noise in the one number the page exists for. Each group is now
      partitioned by division_group.divisionGroup: varsity-level labels (and
      blank) compile together, JV / Frosh-Soph / MS / Para each compile
      apart, and an unrecognised label is its own level, never merged by
      guess. `levels` lists them in GROUP_ORDER, each {level, label,
      divisions, div_labels, single, results, scores}.
    ★ THE FIRST LEVEL IS PRIMARY -- varsity whenever the group has one. Its
      divisions/results/scores are ALSO the group's own top-level keys, so
      every reader that wants "the" compiled race (and every link made
      before the split) gets the varsity compile and nothing else.
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
    cur.execute(f"""
        SELECT r.result_id, r.person_id, r.team_id, r.place, r.time_seconds,
               r.grade, r.school, r.speed_rating, r.div_id,
               r.date                                   AS race_date,
               {_ratingPoolSql(cur)}, {_statusSql(cur)},
               {_DIV_LABEL}                              AS div_label,
               (round(COALESCE(
                   dov.distance::real, m.distance,
                   (mt.division_distances -> r.div_id::text ->> 'distance')::real
                ) / 100.0) * 100)::int                AS distance,
               COALESCE(NULLIF(btrim(COALESCE(an.first_name, '') || ' '
                   || COALESCE(an.last_name, '')), ''),
                   NULLIF(btrim(r.athlete_name), ''))   AS name,
               -- ★ AND THE DIVISION'S OWN WORD FOR IT (owner, 2026-09-25:
               --   "compiled race for that race 404s"). Gender came only off
               --   athletes through person_id, and an unlinked tfrrs row has
               --   none: the whole meet grouped as "?", and the link the meet
               --   page drew -- .../compiled/8000/? -- never reached the
               --   route. tfrrs names every division ("Men's 8k").
               {rowGenderSql()}                          AS gender
        FROM   results r
        LEFT JOIN meets m ON m.meet_id = r.meet_id
                         AND m.div_id  = r.div_id
                         AND m.source  = r.source
        LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                                AND mt.sport   = 'XC'
        -- ★ THE CORRECTED DISTANCE FIRST (sweep 2026-10-10), as the race
        --   page's app._xc_distance_sql: an overridden division compiled
        --   under its scraped distance, into the wrong race
        LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id
                                   AND dov.div_id  = r.div_id
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
        -- ⚠ THE NAME FROM EVERY PROFILE, GENDERED OR NOT (owner, 2026-10-07:
        --   "still seeing unknowns"). `a` above keeps only M/F profiles of
        --   the person's id row -- right for the gender, wrong for the name:
        --   a named profile with no gender, or a name on another profile,
        --   read Unknown. Same rule as app._athlete_lateral.
        LEFT JOIN LATERAL (
            SELECT NULLIF(TRIM(x.first_name), '') AS first_name,
                   NULLIF(TRIM(x.last_name),  '') AS last_name
            FROM   (SELECT ap.first_name, ap.last_name FROM athletes ap
                    WHERE  ap.person_id = r.person_id
                    UNION ALL
                    SELECT ai.first_name, ai.last_name FROM athletes ai
                    WHERE  ai.athlete_id IN (r.person_id, r.athlete_id)) x
            ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL
                       OR NULLIF(TRIM(x.first_name), '') IS NOT NULL) DESC
            LIMIT  1
        ) an ON TRUE
        WHERE  r.meet_id = %(meet)s
          AND  (%(src)s::text IS NULL OR r.source = %(src)s)
          AND  r.time_seconds IS NOT NULL
          AND  r.time_seconds < 999999
          AND  COALESCE(
                 dov.distance::real, m.distance,
                 (mt.division_distances -> r.div_id::text ->> 'distance')::real
               ) > 0
        -- ⚠ BY NAME, NOT POSITION (2026-09-29). This read `ORDER BY 9, 11`
        --   under the comment "distance, gender": column 9 is div_id and 11
        --   the runner's NAME, so every compiled race was alphabetical --
        --   NCAA DI 2024's men's 10k opened Abel, Abraham, Adam, Adam -- and
        --   the compiled places and team points were scored in that order.
        ORDER  BY distance, gender, r.time_seconds
    """, {"meet": meet_id, "src": source})

    groups = {}
    for row in cur.fetchall():
        # ⚠ GENDER CAN BE NULL, and those rows must not silently merge into a
        #   group. They get their own bucket, which the page can label rather
        #   than pretending they belong to one side.
        key = (row["distance"], row["gender"] or "?")
        g = groups.setdefault(key, {"distance": row["distance"],
                                    "gender": row["gender"] or "?",
                                    "results": [], "date": None})
        # ★ THE DAY IT RAN, for the page's meta line and its Share title
        #   (sweep 2026-10-10, B20): the earliest of the merged divisions'
        #   dates, ISO text as results.date stores it.
        d = row.get("race_date")
        if d and (g["date"] is None or str(d) < g["date"]):
            g["date"] = str(d)[:10]
        g["results"].append({
            "result_id": row["result_id"],
            "person_id": row["person_id"],
            "name": (row["name"] or "").strip() or "Unknown",
            "school": row["school"],
            "grade": row["grade"],
            "div_id": row["div_id"],
            "div_label": (row.get("div_label") or "").strip() or None,
            "status": row.get("status"),
            "time_seconds": float(row["time_seconds"]),
            "speed_rating": (round(float(row["speed_rating"]), 1)
                             if row["speed_rating"] is not None else None),
            "rating_pool": row.get("rating_pool"),
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
    # ! PLACED FINISHERS ONLY (sweep 2026-10-10): the race page's numbering
    #   (stampXcPlaces), so "Ran" says what the race page says -- a DQ ran
    #   no place, and the runner behind it is not one place lower.
    by_div = {}
    for g in groups.values():
        for r in g["results"]:
            by_div.setdefault(r["div_id"], []).append(r)
    for rows in by_div.values():
        rows.sort(key=_compiledOrder)
        stampXcPlaces(rows, "division_place", "division_status")

    out = []
    for g in groups.values():
        levels = {}
        for r in g["results"]:
            lv = divisionGroup(r["div_label"])
            r["level"] = lv
            levels.setdefault(lv, []).append(r)
        g["levels"] = [_compileLevel(cur, lv, rows, source)
                       for lv, rows in sorted(
                           levels.items(),
                           key=lambda kv: (groupRank(kv[0]), kv[0]))]
        g["n_results"] = len(g["results"])
        # the primary level IS the group to every older reader
        first = g["levels"][0]
        g["level"] = first["level"]
        g["divisions"] = first["divisions"]
        g["results"] = first["results"]
        g["scores"] = first["scores"]
        out.append(g)

    out.sort(key=lambda g: -g["n_results"])
    return out


def _compiledOrder(r):
    """Time order, a DNF/DNS (which carries a time only by accident) last.
    ! A DQ STAYS IN TIME ORDER: its time is real, only its place is void."""
    k = _resultStatus().kind(r.get("status"), r.get("time_seconds"))
    return (k not in ("ok", "dq"), r["time_seconds"])


def levelLabel(level, div_labels=()):
    """'Varsity', 'JV', ...; an unrecognised level is called by the
    division's own name -- there is no generic word for it."""
    return (groupLabel(level)
            or next((d for d in div_labels if d), None)
            or level.split(":", 1)[-1].title())


def _compileLevel(cur, level, rows, source):
    """One level's compiled race: its places and its team scores."""
    # The compiled place: position in THIS LEVEL's merged list, which is
    # what makes it different from the division place. Sorted here too, so
    # the order never again rests on a column number.
    rows.sort(key=_compiledOrder)
    # ★ A DQ (or a DNF with a time) KEEPS ITS ROW AND LOSES ITS PLACE: place
    #   None, place_status its letters for the Pl column (stampXcPlaces).
    stampXcPlaces(rows, "place", "place_status")
    divs = sorted({r["div_id"] for r in rows})
    labels = []
    for r in rows:
        if r["div_label"] and r["div_label"] not in labels:
            labels.append(r["div_label"])
    # identity-split colliding school names for scoring, then restore
    # the row dicts (the results table renders row.school directly).
    # ! The DQ rows go in too: annotateScoring's _finished leaves them
    #   unscored and unstamped, so they score and displace nobody.
    splitCollisionTeams(cur, rows, source=source)
    scores = unsplitTeams(scoreRows(rows))
    unstampRows(rows)
    return {"level": level, "label": levelLabel(level, labels),
            "divisions": divs, "div_labels": labels,
            # ★ ONE DIVISION IS JUST THAT RACE, re-placed: still shown, for
            #   completeness, but the page says so (owner, 2026-10-10)
            "single": len(divs) == 1,
            "results": rows, "scores": scores}


# compiledIndex
# Purpose:   the meet page's LIST of compiled races -- (distance, gender,
#            how many results, divisions, scoring teams) -- without
#            compiling any of them.
#
# ★ THE MEET PAGE WAS DOING ALL OF compiledResults FOR FIVE NUMBERS A GROUP
#   (owner, 2026-09-02: "meet page loads really slowly"). It fetched every
#   finisher with a name lateral, derived every division place, split the
#   colliding school names with a database probe per team, and scored every
#   race -- then threw all of it away and kept len(). A big invitational is
#   thousands of rows and dozens of teams, per page view, uncached. This is
#   one aggregate query: the same distance/gender/source discipline, the
#   same finisher filter, grouped in SQL.
#
# ⚠ n_teams IS "SCHOOLS WITH FIVE OR MORE FINISHERS", the scoring rule's
#   threshold, counted by name. The full compile also splits colliding
#   names by home state before scoring, so on a meet where two "Central"s
#   both fielded five it can count one more team than this does. The index
#   is a link list; the compiled page itself is still the full compile.
#
# ★ ONE ENTRY PER LEVEL (owner, 2026-10-10), as compiledResults splits them:
#   {distance, gender, level, label, primary, single, div_label, n_results,
#   n_divisions, n_teams}. The level comes from the division's label in
#   Python (division_group is not SQL), so the query groups down to
#   (distance, gender, division, school) and the counts are summed here --
#   still one query, a few hundred rows on the biggest invitational. The
#   primary level (varsity when there is one) comes first in its group and
#   is the one the bare compiled URL lands on.
def compiledIndex(cur, meet_id, source=None):
    cur.execute(f"""
        WITH rows AS (
            SELECT r.div_id, r.school, r.person_id,
                   {_DIV_LABEL}                         AS div_label,
                   (round(COALESCE(
                       dov.distance::real, m.distance,
                       (mt.division_distances -> r.div_id::text ->> 'distance')::real
                    ) / 100.0) * 100)::int                AS distance,
                   {rowGenderSql()}                     AS gender
            FROM   results r
            LEFT JOIN meets m ON m.meet_id = r.meet_id
                             AND m.div_id  = r.div_id
                             AND m.source  = r.source
            LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                                    AND mt.sport   = 'XC'
            -- ★ dist_override first, as compiledResults (sweep 2026-10-10)
            LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id
                                       AND dov.div_id  = r.div_id
            LEFT JOIN LATERAL (
                SELECT x.gender
                FROM   athletes x
                WHERE  x.athlete_id = r.person_id
                  AND  x.gender IN ('M', 'F')
                ORDER  BY (NULLIF(TRIM(x.last_name), '') IS NOT NULL) DESC
                LIMIT  1
            ) a ON TRUE
            WHERE  r.meet_id = %(meet)s
              AND  (%(src)s::text IS NULL OR r.source = %(src)s)
              AND  r.time_seconds IS NOT NULL
              AND  r.time_seconds < 999999
              AND  COALESCE(
                     dov.distance::real, m.distance,
                     (mt.division_distances -> r.div_id::text ->> 'distance')::real
                   ) > 0
        )
        SELECT distance, COALESCE(gender, '?') AS gender, div_id, div_label,
               school, count(*) AS n
        FROM   rows
        GROUP  BY distance, COALESCE(gender, '?'), div_id, div_label, school
    """, {"meet": meet_id, "src": source})
    groups = {}
    for row in cur.fetchall():
        if isinstance(row, dict):
            distance, gender, div_id, div_label, school, n = (
                row["distance"], row["gender"], row["div_id"],
                row["div_label"], row["school"], row["n"])
        else:
            distance, gender, div_id, div_label, school, n = row
        div_label = (div_label or "").strip() or None
        lv = divisionGroup(div_label)
        g = groups.setdefault((int(distance), gender), {})
        e = g.setdefault(lv, {"n_results": 0, "divs": set(), "labels": [],
                              "schools": {}})
        e["n_results"] += int(n)
        e["divs"].add(div_id)
        if div_label and div_label not in e["labels"]:
            e["labels"].append(div_label)
        if school is not None:
            e["schools"][school] = e["schools"].get(school, 0) + int(n)
    out = []
    # biggest (distance, gender) first, as before; its levels in GROUP_ORDER
    for (distance, gender), levels in sorted(
            groups.items(),
            key=lambda kv: -sum(e["n_results"] for e in kv[1].values())):
        for i, (lv, e) in enumerate(sorted(
                levels.items(), key=lambda kv: (groupRank(kv[0]), kv[0]))):
            out.append({
                "distance": distance, "gender": gender,
                "level": lv, "label": levelLabel(lv, e["labels"]),
                "primary": i == 0,
                "single": len(e["divs"]) == 1,
                "div_label": e["labels"][0] if e["labels"] else None,
                "n_results": e["n_results"],
                "n_divisions": len(e["divs"]),
                # the scoring rule's threshold, isTeam schools only -- the
                # compile's own rule for what can field a squad
                "n_teams": sum(1 for sc, k in e["schools"].items()
                               if k >= SCORERS and isTeam(sc)),
            })
    return out


# ------------------------------------------------------------------ #
#  2. SCORING
# ------------------------------------------------------------------ #


def _finished(r):
    """A row that keeps a place: a real time (999999 is the DNS/DNF
    sentinel, not a time) and no DQ/FS/DNF/DNS status.

    ⚠ A ROW WITH NO TIME COLUMN AT ALL HAS FINISHED. The team boards race a
      meet that never happened -- entrants ordered by season rating, with no
      time on any row -- and the day this check first asked for one every
      squad in that meet lost all seven runners and 14,317 boards came out
      empty (2026-09-06). Only a row that CARRIES a time can say it did not
      finish; a row from a field that has no times is a finisher by
      construction. A present-but-null time is still a non-finish: the
      compiled race pages write None for a DNS the scraper left blank.
    """
    if "time_seconds" not in r:
        return True
    # ⚠ AND A DQ HAS NOT, FOR SCORING (sweep 2026-10-10). Its time is real,
    #   so the old `< 999999` test let it score and displace -- on the
    #   compiled page, which never went through the race page's filter.
    #   xcPlaced is the race page's own rule; the status decides first.
    return xcPlaced(r)


def annotateScoring(rows):
    """Stamp team_place and score_place on an ORDERED field, in place.

    score_place is the published "Points" number: the runner's rank among
    those who can score or displace -- the first SCORERS + DISPLACERS
    finishers of a school that fielded at least SCORERS finishers, isTeam
    schools only. Everyone else -- an unattached runner, an incomplete
    team's runner, an eligible team's 8th -- gets None and moves nobody up.
    team_place is stamped for every isTeam finisher, scoring or not.

    ⚠ THE 7-RUNNER CAP IS THE RULEBOOK'S, and the old scoreRows renumber
      loop lacked it: a team's 8th and 9th finishers displaced. Both the
      team totals and the per-row Points column now come through this one
      loop, so they cannot disagree.

    ! NON-TEAMS ARE NEVER COUNTED, so they can neither score nor be
      reported as short of runners. isTeam is asked once per school, not
      once per runner -- it is a nine-alternative regex, and the
      hypothetical national meet has 140,000 entrants.
    """
    counts, real = {}, {}
    for r in rows:
        school = r.get("school")
        ok = real.get(school)
        if ok is None:
            ok = real[school] = isTeam(school)
        if ok and _finished(r):
            counts[school] = counts.get(school, 0) + 1
    full = {s for s, n in counts.items() if n >= SCORERS}

    place, on_team = 0, {}
    for r in rows:
        school = r.get("school")
        r["team_place"] = None
        r["score_place"] = None
        if not real.get(school) or not _finished(r):
            continue
        k = on_team[school] = on_team.get(school, 0) + 1
        r["team_place"] = k
        if school in full and k <= SCORERS + DISPLACERS:
            place += 1
            r["score_place"] = place
    return rows


# ★ SAME STRING, DIFFERENT SCHOOLS, ONE RACE (NXN 2025: two Jesuits, two
#   Lincolns). Scoring by the school STRING merges them into one impossible
#   team, mislabels both with the biggest namesake's state, and forced the
#   published-score graft to give dup names NO scorers at all. The fix is
#   identity: before scoring, stamp each collision row's school with its
#   athlete's home state (school_identity says which names collide;
#   person_home_state says who is from where); after scoring, unsplit the
#   key back into school + state for display and links.
_KEYSEP = "\x00"


# ★ WHICH SCHOOL A RESULT ROW'S MENTION MEANS (owner, 2026-09-16: "the races
#   still say Williams(CA)"). race.html labels, links and crests every school
#   with the MEET's state -- header.state -- through contextState, because
#   that was the only context a row had. For a college that is a travel
#   state: a Williams College row at a Connecticut meet asks for "Williams in
#   CT", finds no CT cluster, and falls back to the name's primary -- the
#   California high school.
#
#   The row carries a person_id, and school_athlete_state says which cluster
#   that athlete of that school is in. That is not a context to guess from,
#   it is the answer. Stamped as `school_state` so the template can prefer it
#   and fall back to the meet's state exactly as before.
#
# ! EVERY ROW, NOT ONLY COLLIDING ONES, and a no-op without the table: a
#   one-school name's assignment IS its only cluster, so stamping it changes
#   nothing and costs one indexed lookup per page.
def _schoolKey(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def teamStates(cur, rows):
    """{team_id: state} from team_identity for the rows' anet team ids --
    anet's OWN record of where each team is (build_team_identity.py; owner,
    2026-09-18: "Ur gonna do schools by team id. Only, everywhere").

    ★ A FACT, NOT A COUNT. school_athlete_state and the home-state clusters
      infer a school's state from where its athletes raced, and lose to
      their own evidence whenever a name is shared -- De La Salle's Concord
      runners filed under New Orleans (2026-09-26), Georgetown University
      under a Texas high school. A team id is anet's identifier and
      anet_state is anet's record of where it is.
    ! ONLY WHEN THE NAMES AGREE. The team's name must be the row's school
      name (letters and digits, or one a prefix of the other); a row whose
      team id names some other school keeps the inferred answer.
    ! A ROW WITH NO TEAM ID (every tfrrs row) is untouched.
    """
    ids = sorted({int(r["team_id"]) for r in rows
                  if r.get("team_id") not in (None, 0)})
    if not ids:
        return {}
    try:
        cur.execute("SELECT to_regclass('public.team_identity') IS NOT NULL AS ok")
        got = cur.fetchone()
        if not bool(got["ok"] if isinstance(got, dict) else got[0]):
            return {}
        cur.execute("SELECT team_id, school, state FROM team_identity "
                    "WHERE team_id = ANY(%s) AND state IS NOT NULL", (ids,))
        found = {}
        for row in cur.fetchall():
            tid, sc, st = ((row["team_id"], row["school"], row["state"])
                           if isinstance(row, dict) else tuple(row))
            found[int(tid)] = (_schoolKey(sc), st)
    except Exception:                                   # noqa: BLE001
        cur.connection.rollback()
        return {}
    out = {}
    for r in rows:
        tid = r.get("team_id")
        if tid in (None, 0) or int(tid) not in found:
            continue
        key, st = found[int(tid)]
        mine = _schoolKey((r.get("school") or "").split(_KEYSEP, 1)[0])
        if key and mine and (key == mine or key.startswith(mine)
                             or mine.startswith(key)):
            out[int(tid)] = st
    return out


def stampSchoolStates(cur, rows):
    """Set r["school_state"] from school_athlete_state, in place."""
    pairs = {(r["school"], r["person_id"]) for r in rows
             if r.get("school") and r.get("person_id")}
    if not pairs:
        return
    cur.execute("SELECT to_regclass('school_athlete_state') IS NOT NULL AS ok")
    got = cur.fetchone()
    if not bool(got["ok"] if isinstance(got, dict) else got[0]):
        by_team = teamStates(cur, rows)
        for r in rows:
            tid = r.get("team_id")
            if tid not in (None, 0) and int(tid) in by_team:
                r["school_state"] = by_team[int(tid)]
        return
    cur.execute(
        "SELECT school, person_id, state FROM school_athlete_state "
        "WHERE  school = ANY(%s) AND person_id = ANY(%s)",
        (sorted({x[0] for x in pairs}), sorted({x[1] for x in pairs})))
    seen = {}
    for row in cur.fetchall():
        sc, pid, st = ((row["school"], row["person_id"], row["state"])
                       if isinstance(row, dict) else (row[0], row[1], row[2]))
        seen[(sc, pid)] = st
    for r in rows:
        st = seen.get((r.get("school"), r.get("person_id")))
        if st:
            r["school_state"] = st
    # ★ THE RUNNER'S HOME STATE BEFORE THE MEET'S (owner, 2026-10-09: Zarian
    #   Rodriguez of Hamilton, Chandler AZ, read "Hamilton (CA)" at the
    #   Arcadia Invitational). A runner with no school_athlete_state row
    #   fell to the race's state at the page. person_home_state is the state
    #   they race in most; it is used only where a school of THIS name
    #   exists in that state (school_identity), so a club or a transfer is
    #   not moved to a state its name was never seen in.
    left = {r["person_id"]: r for r in rows
            if not r.get("school_state") and r.get("school") and r.get("person_id")}
    if left:
        try:
            cur.execute("SAVEPOINT home_state")
            cur.execute("""
                SELECT h.person_id, h.state FROM person_home_state h
                WHERE  h.person_id = ANY(%s)""", (sorted(left),))
            home = {}
            for row in cur.fetchall():
                pid, st = ((row["person_id"], row["state"]) if isinstance(row, dict)
                           else (row[0], row[1]))
                home[pid] = st
            pairs = sorted({(r["school"], home[pid]) for pid, r in left.items() if home.get(pid)})
            if pairs:
                cur.execute("""
                    SELECT DISTINCT school, state FROM school_identity
                    WHERE  (school, state) IN (SELECT * FROM unnest(%s::text[], %s::text[]))""",
                            ([p[0] for p in pairs], [p[1] for p in pairs]))
                known = {((row["school"], row["state"]) if isinstance(row, dict)
                          else (row[0], row[1])) for row in cur.fetchall()}
                for r in rows:
                    if r.get("school_state") or not r.get("person_id"):
                        continue
                    st = home.get(r["person_id"])
                    if st and (r.get("school"), st) in known:
                        r["school_state"] = st
            cur.execute("RELEASE SAVEPOINT home_state")
        except Exception:                                # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT home_state")
    # ★ THE TEAM ID OUTRANKS THE INFERENCE (teamStates)
    by_team = teamStates(cur, rows)
    for r in rows:
        tid = r.get("team_id")
        if tid not in (None, 0) and int(tid) in by_team:
            r["school_state"] = by_team[int(tid)]


def splitCollisionTeams(cur, rows, meet_state=None, published_names=None,
                        source=None):
    """Stamp rows of colliding school names with a home-state identity.
    Mutates rows in place (callers score COPIES). No-op mid-rebuild.

    ★ NEVER IN A tfrrs RACE (owner, 2026-09-29, NCAA DI 2025 men's 10k:
      "Butler (IN)" and "Butler (NC)", "Syracuse (UT)", "Notre Dame (NJ)",
      "Georgetown (TX)", "Portland (MI)" -- one college team broken into
      two, and the pieces scored or dropped apart). tfrrs is the college
      feed: its team names are the colleges' own, one team per name per
      race, and a runner's home or high school state says nothing about
      which. The split exists for two high schools under one string at an
      anet meet (two Jesuits at NXN), which is not this.

    meet_state: the race's own state -- the identity a wrongly split team
    is put back under (see _rejoinFalseSplits). published_names: the school
    names in the meet's published team scores, each with how many times it
    appears; a name published ONCE is one team here, however its runners'
    identities resolve."""
    def _has(t):
        # ⚠ ALIASED AND READ BY NAME, BECAUSE THIS CURSOR IS NOT ALWAYS A
        #   TUPLE CURSOR. app.py's meet route opens a RealDictCursor and
        #   passes it straight down through compiledResults, and a dict row
        #   subscripted with 0 raises KeyError -- not IndexError, which is
        #   why it does not read like an off-by-one:
        #
        #       File "racecast/meet_compile.py", line 327, in _has
        #         return cur.fetchone()[0] is not None
        #       KeyError: 0
        #
        #   Every XC meet page that reached splitCollisionTeams answered 500.
        #   A named column works on BOTH cursor shapes; positional works on
        #   only one, and which one arrives is decided forty lines away in a
        #   different file.
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (t,))
        row = cur.fetchone()
        return bool(row["present"] if isinstance(row, dict) else row[0])
    if source == "tfrrs":
        return
    schools = sorted({r["school"] for r in rows if r.get("school")})
    if not schools or not _has("school_identity") \
            or not _has("person_home_state"):
        return
    cur.execute("""
        SELECT school, state FROM school_identity
        WHERE school = ANY(%s) AND n_athletes >= 3 AND share >= 0.10
        ORDER BY school, n_athletes DESC""", (schools,))
    clus = {}
    for row in cur.fetchall():
        s, st = (row["school"], row["state"]) if isinstance(row, dict) \
            else (row[0], row[1])
        clus.setdefault(s, []).append(st)
    multi = {s for s, sts in clus.items() if len(sts) >= 2}
    if not multi:
        return
    pids = sorted({r["person_id"] for r in rows
                   if r.get("school") in multi and r.get("person_id")})
    home = {}
    if pids:
        cur.execute("SELECT person_id, state FROM person_home_state "
                    "WHERE person_id = ANY(%s)", (pids,))
        for row in cur.fetchall():
            p, st = (row["person_id"], row["state"]) \
                if isinstance(row, dict) else (row[0], row[1])
            home[p] = st

    # ★ AND THE ASSIGNMENT OUTRANKS THE HOME STATE (owner, 2026-09-16: "I can
    #   also see things like MIT(CT) and Tufts(CT) which have become diff
    #   'schools' in races"). school_athlete_state says which cluster each
    #   athlete of a school IS in -- their own anet team, then the college
    #   directory, then the racing mode -- and it is already folded through
    #   the merge, so it needs neither the alias nor the clamp. The home
    #   state stays for the names it does not cover.
    #
    # ⚠ THE SAME FAILURE THE AMHERST NOTE ABOVE DESCRIBES, ONE LAYER DOWN.
    #   The alias fixed the case where the merge had folded the travel states
    #   away; a name that legitimately SPLITS keeps both clusters, so MIT's
    #   New England away meets put its athletes in CT and the split shattered
    #   the team again.
    assigned = {}
    if pids and _has("school_athlete_state"):
        cur.execute(
            "SELECT school, person_id, state FROM school_athlete_state "
            "WHERE  school = ANY(%s) AND person_id = ANY(%s)",
            (sorted(multi), pids))
        for row in cur.fetchall():
            sc, p, st = ((row["school"], row["person_id"], row["state"])
                         if isinstance(row, dict) else (row[0], row[1], row[2]))
            assigned[(sc, p)] = st

    # ⚠ A RAW HOME STATE SHATTERS A COLLEGE TEAM (owner, 2026-09-13: Amherst
    #   scored 318 at a DIII meet with all seven place columns blank, while
    #   its seven runners sat in the results right there). A home state is
    #   where an athlete races MOST, and a college races away most weekends
    #   -- so Amherst's seven came out MA, CT, NY and so on, this split them
    #   into four pseudo-teams of one and two, none of them reached five
    #   scorers, none was scoreable, and the published graft had nothing to
    #   attach. Exactly the BYU failure school_identity documents in its own
    #   header.
    #
    # ! AND THE CURE WAS ALREADY IN THE DATABASE. school_state_alias exists
    #   to say which resolved cluster each original home state went to --
    #   "for readers keyed on an athlete's home state", which is precisely
    #   what this is. It just was not asked.
    alias = {}
    if multi and _has("school_state_alias"):
        cur.execute("SELECT school, home_state, state FROM school_state_alias "
                    "WHERE school = ANY(%s)", (sorted(multi),))
        for row in cur.fetchall():
            sc, hs, st = ((row["school"], row["home_state"], row["state"])
                          if isinstance(row, dict) else (row[0], row[1], row[2]))
            alias[(sc, hs)] = st

    by_team = teamStates(cur, [r for r in rows if r.get("school") in multi])
    for r in rows:
        s = r.get("school")
        if s in multi:
            # ★ anet's own team id first (teamStates): where the team IS,
            #   not where its athletes raced. Kept even when the clusters
            #   do not list that state -- a fact is not clamped to a guess.
            tid = r.get("team_id")
            if tid not in (None, 0) and int(tid) in by_team:
                r["school"] = f"{s}{_KEYSEP}{by_team[int(tid)]}"
                continue
            # the athlete's home state, resolved through the merge that
            # school_identity already did, then clamped to a cluster the
            # name actually has -- an unknown or a travel state falls to
            # the biggest, so nobody vanishes from scoring
            st = assigned.get((s, r.get("person_id")))
            if st is None:
                st = home.get(r.get("person_id"))
                st = alias.get((s, st), st)
            if st not in clus[s]:
                st = clus[s][0]
            r["school"] = f"{s}{_KEYSEP}{st}"
    _rejoinFalseSplits(rows, multi, meet_state, published_names)


def _rejoinFalseSplits(rows, names, meet_state=None, published_names=None):
    """Undo a split that made one real team into several short ones.

    ★ WHY (owner, 2026-09-26: De La Salle 2nd on 67 published points at a
      California meet, every place column blank). Its seven runners came out
      of the identity split as four "De La Salle (LA)" and three "De La
      Salle (CA)" -- the name is a Concord school and a New Orleans one, and
      four of the Concord runners' own assignments said Louisiana. Neither
      piece reached five, neither could score, and the published team had
      nothing to attach to.

    ! TWO RULES, BOTH NARROW:
      1. no piece of the name can score (fewer than five runners each) but
         all of them together can -- a real two-school collision leaves at
         least one scoring team, as the two Jesuits at NXN did;
      2. the meet published the name exactly ONCE in its team scores -- the
         meet itself says there was one team of that name.
      The pieces go back under the meet's own state when one of them has
      it, else under the biggest piece.
    """
    from collections import Counter
    pieces = {}
    for r in rows:
        sc = r.get("school") or ""
        if _KEYSEP not in sc:
            continue
        base, st = sc.split(_KEYSEP, 1)
        if base in names:
            pieces.setdefault(base, Counter())[st] += 1
    def _n(x):
        return re.sub(r"[^a-z0-9]", "", (x or "").lower())
    # the meet spells schools its own way: compare letters and digits only
    counts = Counter()
    for n, k in (published_names or {}).items():
        counts[_n(n)] += k
    once = {n for n, k in counts.items() if k == 1}
    for base, by_state in pieces.items():
        if len(by_state) < 2:
            continue
        total = sum(by_state.values())
        short = all(n < SCORERS for n in by_state.values()) and total >= SCORERS
        if not (short or _n(base) in once):
            continue
        keep = (meet_state if meet_state in by_state
                else by_state.most_common(1)[0][0])
        for r in rows:
            sc = r.get("school") or ""
            if sc.startswith(base + _KEYSEP):
                r["school"] = f"{base}{_KEYSEP}{keep}"


def unsplitTeams(scores):
    """Fold the identity key back into school + state on a scoreRows
    result, so templates render 'Jesuit (CA)' and link with ?state=CA."""
    for t in scores.get("teams", []) + scores.get("incomplete", []):
        if _KEYSEP in (t.get("school") or ""):
            t["school"], t["state"] = t["school"].split(_KEYSEP, 1)
    return scores


def unstampRows(rows):
    """Strip the identity key off row dicts that get rendered directly
    (the compiled results table links row.school)."""
    for r in rows:
        s = r.get("school")
        if s and _KEYSEP in s:
            r["school"] = s.split(_KEYSEP, 1)[0]


def scoreRows(rows):
    """Team scores from an ordered list of finishers.

    `rows` must already be in finishing order.

    ⚠ DISPLACERS COUNT EVEN THOUGH THEY DO NOT SCORE. Runners 6 and 7 push
      every later finisher's place up, which is the whole tactical point of
      depth -- dropping them would score a deep team identically to a
      top-heavy one.

    ⚠ AND PLACES ARE RENUMBERED AFTER REMOVING INCOMPLETE TEAMS. A school with
      four runners cannot score, and by the rules its runners are lifted out
      and everyone behind them moves up. Scoring against raw finishing places
      instead inflates every complete team's total.

    The renumbering itself lives in annotateScoring (shared with the race
    pages' Points column), which also stamps the rows in place -- callers
    rendering the same list get score_place and team_place for free.
    """
    annotateScoring(rows)
    counts, scoring = {}, {}
    for r in rows:
        if r.get("team_place"):
            s = r["school"]
            counts[s] = max(counts.get(s, 0), r["team_place"])
        if r.get("score_place"):
            scoring.setdefault(r["school"], []).append(r)
    full = set(scoring)

    out = []
    for school, runners in scoring.items():
        out.append({
            "school": school,
            "points": sum(x["score_place"] for x in runners[:SCORERS]),
            "runners": runners,          # annotate capped these at 7
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