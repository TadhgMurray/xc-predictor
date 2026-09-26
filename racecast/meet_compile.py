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

def compiledResults(cur, meet_id, source=None):
    """Every division of a meet, merged by (distance, gender).

    ⚠ `source` MATTERS WHEREVER TWO MEETS SHARE ONE meet_id. The anet and
      tfrrs id spaces overlap (15,096 XC meet_ids hold rows from both), and
      merging by (distance, gender) across sources compiles two different
      real-world meets into one imaginary race. Callers on a colliding meet
      pass the source they are showing; None keeps the old behaviour.

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
               COALESCE(NULLIF(btrim(COALESCE(a.first_name, '') || ' '
                   || COALESCE(a.last_name, '')), ''),
                   NULLIF(btrim(r.athlete_name), ''))   AS name,
               -- ★ AND THE DIVISION'S OWN WORD FOR IT (owner, 2026-09-25:
               --   "compiled race for that race 404s"). Gender came only off
               --   athletes through person_id, and an unlinked tfrrs row has
               --   none: the whole meet grouped as "?", and the link the meet
               --   page drew -- .../compiled/8000/? -- never reached the
               --   route. tfrrs names every division ("Men's 8k").
               COALESCE(a.gender,
                   CASE WHEN (mt.division_distances -> r.div_id::text
                              ->> 'div_name') ~* '(women|girls|female)'
                        THEN 'F'
                        WHEN (mt.division_distances -> r.div_id::text
                              ->> 'div_name') ~* '(men|boys|male)'
                        THEN 'M' END)                 AS gender
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
          AND  (%(src)s::text IS NULL OR r.source = %(src)s)
          AND  r.time_seconds IS NOT NULL
          AND  r.time_seconds < 999999
          AND  COALESCE(
                 m.distance,
                 (mt.division_distances -> r.div_id::text ->> 'distance')::real
               ) > 0
        ORDER  BY 9, 11, r.time_seconds       -- distance, gender (computed), time
    """, {"meet": meet_id, "src": source})

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
        # identity-split colliding school names for scoring, then restore
        # the row dicts (the results table renders row.school directly)
        splitCollisionTeams(cur, g["results"])
        g["scores"] = unsplitTeams(scoreRows(g["results"]))
        unstampRows(g["results"])
        out.append(g)

    out.sort(key=lambda g: -len(g["results"]))
    return out


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
def compiledIndex(cur, meet_id, source=None):
    cur.execute("""
        WITH rows AS (
            SELECT r.div_id, r.school, r.person_id,
                   (round(COALESCE(
                       m.distance,
                       (mt.division_distances -> r.div_id::text ->> 'distance')::real
                    ) / 100.0) * 100)::int                AS distance,
                   a.gender
            FROM   results r
            LEFT JOIN meets m ON m.meet_id = r.meet_id
                             AND m.div_id  = r.div_id
                             AND m.source  = r.source
            LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                                    AND mt.sport   = 'XC'
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
                     m.distance,
                     (mt.division_distances -> r.div_id::text ->> 'distance')::real
                   ) > 0
        ),
        teams AS (
            SELECT distance, COALESCE(gender, '?') AS gender, school
            FROM   rows
            WHERE  school IS NOT NULL
            GROUP  BY distance, COALESCE(gender, '?'), school
            HAVING count(*) >= 5
        )
        SELECT g.distance, g.gender, g.n_results, g.n_divisions,
               COALESCE(t.n_teams, 0) AS n_teams
        FROM (
            SELECT distance, COALESCE(gender, '?') AS gender,
                   count(*)              AS n_results,
                   count(DISTINCT div_id) AS n_divisions
            FROM   rows
            GROUP  BY distance, COALESCE(gender, '?')
        ) g
        LEFT JOIN (
            SELECT distance, gender, count(*) AS n_teams
            FROM   teams
            GROUP  BY distance, gender
        ) t ON t.distance = g.distance AND t.gender = g.gender
        ORDER  BY g.n_results DESC
    """, {"meet": meet_id, "src": source})
    out = []
    for row in cur.fetchall():
        get = row.get if isinstance(row, dict) else None
        if get is None:
            distance, gender, n_results, n_divisions, n_teams = row
        else:
            distance, gender, n_results, n_divisions, n_teams = (
                row["distance"], row["gender"], row["n_results"],
                row["n_divisions"], row["n_teams"])
        # isTeam() is the compile's own rule for what counts as a school;
        # its non-team names (unattached, countries) cannot field a squad
        # in the compile and are not worth a second query to exclude here.
        out.append({"distance": int(distance), "gender": gender,
                    "n_results": int(n_results),
                    "n_divisions": int(n_divisions),
                    "n_teams": int(n_teams)})
    return out


# ------------------------------------------------------------------ #
#  2. SCORING
# ------------------------------------------------------------------ #


def _finished(r):
    """A row with a real time. 999999 is the DNS/DNF sentinel, not a time.

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
    t = r.get("time_seconds")
    try:
        return t is not None and float(t) < 999999
    except (TypeError, ValueError):
        return False


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
def stampSchoolStates(cur, rows):
    """Set r["school_state"] from school_athlete_state, in place."""
    pairs = {(r["school"], r["person_id"]) for r in rows
             if r.get("school") and r.get("person_id")}
    if not pairs:
        return
    cur.execute("SELECT to_regclass('school_athlete_state') IS NOT NULL AS ok")
    got = cur.fetchone()
    if not bool(got["ok"] if isinstance(got, dict) else got[0]):
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


def splitCollisionTeams(cur, rows, meet_state=None, published_names=None):
    """Stamp rows of colliding school names with a home-state identity.
    Mutates rows in place (callers score COPIES). No-op mid-rebuild.

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

    for r in rows:
        s = r.get("school")
        if s in multi:
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