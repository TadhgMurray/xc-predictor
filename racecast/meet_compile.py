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
          AND  (%(src)s::text IS NULL OR r.source = %(src)s)
          AND  r.time_seconds IS NOT NULL
          AND  r.time_seconds < 999999
          AND  COALESCE(
                 m.distance,
                 (mt.division_distances -> r.div_id::text ->> 'distance')::real
               ) > 0
        ORDER  BY distance, a.gender, r.time_seconds
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


# ------------------------------------------------------------------ #
#  2. SCORING
# ------------------------------------------------------------------ #

def _finished(r):
    """A row with a real time. 999999 is the DNS/DNF sentinel, not a time."""
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


def splitCollisionTeams(cur, rows):
    """Stamp rows of colliding school names with a home-state identity.
    Mutates rows in place (callers score COPIES). No-op mid-rebuild."""
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
    for r in rows:
        s = r.get("school")
        if s in multi:
            # the athlete's own home state; an unknown falls to the name's
            # biggest cluster so nobody vanishes from scoring
            st = home.get(r.get("person_id")) or clus[s][0]
            r["school"] = f"{s}{_KEYSEP}{st}"


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