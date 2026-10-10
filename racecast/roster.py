# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Web
# File Title: roster.py
# Purpose: Who is on a team's roster in a season that has started but is not
#          yet decided -- the carry-forward rule, in one place.
#
# ===================================================================== #
#
# ★ THE PROBLEM. A season's roster is a question about the FUTURE for its
#   first few weeks, and the database only ever answers about the past: an
#   athlete exists in athlete_season once they have raced. So in September a
#   team's roster is whoever happened to have run already, and the fifth
#   runner who was sick for the opener is simply not on the team.
#
#   Owner, 2026-09-15: "once it goes to a new season, it should take the
#   roster from last season, remove the seniors, but keep everybody else on
#   the roster until they either don't run the first 3 races or until they
#   race for another team... For example 2026 roster for a team after their
#   first race should include ppl from last year who got sick during the
#   first race."
#
# ★ THE RULE, THEN, IS A WINDOW AND TWO EXITS.
#
#     roster = everyone who has raced for the school this season
#            + (while the school has run fewer than CARRY_RACES races)
#              last season's roster, minus the graduating class, minus
#              anyone already racing for someone else
#
#   The window is counted in the TEAM's races, not in weeks and not in the
#   athlete's -- "the first 3 races" is the team's first three, which is what
#   makes "missed all of them" mean something. A team that has raced twice
#   carries everyone; after its third, the carry stops and the roster is
#   whoever has actually run.
#
# ! AND THAT LAST SENTENCE IS THE WHOLE OF THE "MISSED ALL THREE" TEST,
#   which is why you will not find one. Anyone who ran any race this season
#   -- the first three or the fifth -- HAS a current-season row and is in the
#   roster on their own account. So dropping the carried set at the third
#   race removes exactly the people who ran none of them, and nothing else.
#   A separate "did they appear in races 1-3" query would compute the same
#   set at the cost of a second answer that can disagree with this one.
#
# ⚠ WHAT THIS REPLACED WAS ALL-OR-NOTHING, AND THAT IS THE BUG. The old test
#   was "does this school have ANY current-season row": the moment one
#   athlete of a forty-person programme crossed a line in September, the
#   whole carry-forward switched off and the other thirty-nine vanished from
#   the roster, the predictions field and the squad picker at once.
#
# ⚠ NOTE ON SAME-NAMED SCHOOLS. racesRun counts by school NAME, so two
#   schools sharing one (the school_identity state chips) count each other's
#   meets and leave the window early. That is the pre-existing behaviour --
#   an early exit is the OLD rule -- so it can only ever be as wrong as what
#   it replaced, never more. Threading the state filter through is worth
#   doing when school_identity is threaded through predict.py generally.
# ===================================================================== #

# The team's first three races. Three because it is what was asked for, and
# because it is about the length of time it takes a programme to have run
# everyone once: after three meets, someone who has not appeared has really
# not appeared.
CARRY_RACES = 3

# ★ KEYS, NOT SPELLINGS. rankings.gradeKeySql normalises whatever the feed
#   called them -- digits out of the string, the fr/so/jr/sr prefixes read, a
#   college 13-16 mapped onto its class word -- so a high school senior keys
#   to '12' and a college senior to 'sr', and those two are the whole
#   terminal set. Listing spellings is what let "Sr.", "SR-4" and a bare "16"
#   stay on a roster for a year after they graduated.
TERMINAL_KEYS = ("12", "sr")


def graduatedClause(alias="s", param="term_keys"):
    """SQL for "this row is not the graduating class".

    ! AN UNGRADED ROW STAYS, deliberately. A blank grade is not evidence of
      graduation, and dropping them would shrink every squad whose feed is
      thin on grades. The callers flag them for a human instead.
    """
    from rankings import gradeKeySql
    return (f"AND ({alias}.grade IS NULL OR BTRIM({alias}.grade) = '' "
            f"OR {gradeKeySql(alias)} <> ALL(%({param})s))")


def transferredClause(alias="s", year_param="active_yr",
                      sport_param="sport"):
    """SQL for "this row's athlete is not already racing somewhere else".

    ★ A TRANSFER HAS ALREADY RACED SOMEWHERE ELSE; A GRADUATE HAS NOT. That
      is the one signal separating them -- a transfer is not a terminal grade,
      so aging-out alone left them on the old school's squad while they raced
      for the new one.

    ⚠ ELSEWHERE IS CHECKED EXPLICITLY, and it did not used to be. This
      leaned on the carry-forward only ever running for a school with NO
      current-season row, which made "has a current row" and "has a current
      row at another school" the same statement. Under the window rule the
      carry runs for schools that DO have current rows, and that premise is
      gone -- so the clause states what it means.
    """
    return (f"""AND NOT EXISTS (SELECT 1 FROM athlete_season c
                                WHERE c.person_id = {alias}.person_id
                                  AND c.year   = %({year_param})s
                                  AND c.sport  = %({sport_param})s
                                  AND c.school IS DISTINCT FROM {alias}.school)""")


def racesRun(cur, schools, sport, year):
    """{school: how many meets it has raced in `year`}, zero omitted.

    ! ranking_results, NOT results. It carries the stored SEASON year on
      every row -- so the filter is an equality on an indexed column rather
      than a date expression -- and it is the same table the school page's
      own meet list counts, which is the list a reader would check this
      against.
    """
    schools = sorted({s for s in schools if s})
    if not schools or year is None:
        return {}
    cur.execute("""
        SELECT rr.school, count(DISTINCT rr.meet_id) AS n
        FROM   ranking_results rr
        WHERE  rr.school = ANY(%(schools)s)
          AND  rr.sport  = %(sport)s
          AND  rr.year   = %(year)s
        GROUP  BY rr.school
    """, {"schools": schools, "sport": sport, "year": year})
    return {r["school"]: int(r["n"]) for r in cur.fetchall()}


# ★ THE WINDOW COUNTS THE A TEAM'S MEETS, NOT EVERY MEET (owner,
#   2026-10-10: NYU "heavily underrated because they haven't run their A
#   team yet this season" -- the meet the varsity was entered in was
#   cancelled, the B team ran three, and the window closed on a roster whose
#   top seven had not raced once). A meet counts toward CARRY_RACES only if
#   one of last season's top VARSITY_N returners raced in it for the school.
#   VARSITY_N is a scoring team: 5 score, 2 displace (predict.TEAM_SCORERS +
#   TEAM_DISPLACERS). A school with no rated returners last season (a new
#   programme) falls back to every meet, the old rule.
# ! THE A TEAM HAVING RACED THREE TIMES STILL CLOSES IT: a top returner
#   missing all three is out, exactly as before.
VARSITY_N = 7


def carryingSchools(cur, schools, sport, year, gender=None):
    """Of `schools`, the ones whose roster still carries last season's
    returners: fewer than CARRY_RACES of the A team's meets run this season
    (see VARSITY_N).

    A school with no meets at all is carrying -- that is the preseason case
    the carry-forward was written for in the first place.
    gender: "M"/"F" -- whose top returners decide it (a women's A team that
    has raced says nothing about the men's).
    """
    schools = sorted({s for s in schools if s})
    if not schools or year is None:
        return set()
    from rankings import gradeKeySql
    gclause = ("AND upper(right(s.pool, 1)) = %(gender)s"
               if gender in ("M", "F") else "")
    cur.execute(f"""
        WITH top AS (
            SELECT school, person_id FROM (
                SELECT s.school, s.person_id,
                       row_number() OVER (PARTITION BY s.school, s.pool
                                          ORDER BY s.mean_rating DESC NULLS LAST,
                                                   s.person_id) AS rn
                FROM   athlete_season s
                WHERE  s.school = ANY(%(schools)s) AND s.sport = %(sport)s
                  AND  s.year = %(prev)s AND s.mean_rating IS NOT NULL
                  {gclause}
                  AND (s.grade IS NULL OR BTRIM(s.grade) = ''
                       OR {gradeKeySql("s")} <> ALL(%(term_keys)s))
            ) t WHERE rn <= %(top_n)s),
        has AS (SELECT DISTINCT school FROM top)
        SELECT rr.school, count(DISTINCT rr.meet_id) AS n,
               count(DISTINCT rr.meet_id)
                   FILTER (WHERE tp.person_id IS NOT NULL) AS n_top,
               bool_or(h.school IS NOT NULL) AS has_top
        FROM   ranking_results rr
        LEFT   JOIN top tp ON tp.school = rr.school AND tp.person_id = rr.person_id
        LEFT   JOIN has h ON h.school = rr.school
        WHERE  rr.school = ANY(%(schools)s)
          AND  rr.sport  = %(sport)s
          AND  rr.year   = %(year)s
        GROUP  BY rr.school
    """, {"schools": schools, "sport": sport, "year": year, "prev": year - 1,
          "gender": gender, "term_keys": list(TERMINAL_KEYS), "top_n": VARSITY_N})
    out = set(schools)
    for r in cur.fetchall():
        counted = (r.get("n_top") if r.get("has_top") else None)
        if counted is None:
            counted = r.get("n") or 0
        if int(counted) >= CARRY_RACES:
            out.discard(r["school"])
    return out
