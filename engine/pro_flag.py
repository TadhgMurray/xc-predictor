"""
pro_flag.py -- identify professional athlete-seasons, seed then propagate.

THE PROBLEM, AND WHY FIVE SIMPLER RULES FAILED
    A professional sitting in hs_f is rated as a high schooler. Shafiqua Maloney
    carries grade '9' and rated 149.9. But no single fact separates a 17-year-old
    representing the USA from a 28-year-old representing Ethiopia:

      school string   -> Denmark High School, a club called Bowerman Track
      meet name       -> Lutkenhaus raced Millrose as a junior
      venue country   -> Japanese ekiden runners really are high schoolers
      school=country  -> a US junior at World XC legitimately represents the USA
      grade slope     -> being held back gives a real student a slope under 1

    Every one collapses the TIME dimension. Lutkenhaus is a high schooler AND a
    pro; the difference is when.

★ THE UNIT IS THE ATHLETE-SEASON, CLASSIFIED INDEPENDENTLY.
  An earlier version assumed PRO IS ABSORBING -- once professional, every later
  season too. That is false in the NIL era: Sadie Engelhardt forwent a high
  school outdoor season to race professionally and then went to college. An
  absorbing flag would have marked all her NCAA seasons professional.

  So each season stands alone: a season is professional if the athlete ran at
  least MIN_PRO_RACES professional-field races IN THAT SEASON. This needs no
  span threshold, no grade arithmetic, no curated date ranges, and it handles
  every counterexample -- Lutkenhaus 2026 professional while 2025 is not,
  Engelhardt's pro season flagged and her college seasons not, Maloney flagged
  throughout.

★ RACES, NOT MEETS. Millrose runs a high-school mile and a professional mile on
  the same night. Flagging the meet would strip every high-school miler in it.
  The grain is the finest identifier the schema offers -- detected at runtime,
  because results_tf may or may not carry an event id.

SEED then PROPAGATE
    SEED       athletes with >= MIN_PRO_RACES at unambiguous professional meets
               (Diamond League, World Champs, Olympics, Euros).
               ⚠ The exclusion list matters as much as the inclusion list. Raw
               patterns caught AAU Junior Olympic Games (8,984 athletes),
               Youth Diamond League Series, USATF Masters and Senior Games --
               youth and masters meets riding the same words.
    PROPAGATE  a race whose field is mostly confirmed pros is a professional
               race; its finishers are pros from that year on. Repeat to a fixed
               point. This reaches athletes who never ran a seeded meet but who
               race against people who did.
    VETO       a season with an NCAA race, or (2026-09-30) a school grade at a
               school team, is never professional by inference -- seeded or
               propagated -- and so never a confirmed pro in another field.
               Eligibility is the evidence; see buildSchoolSeasons.

★ THE SEASON IS THE ACADEMIC YEAR, FROM season_year.py.

  pro_athlete_season was the LAST table in the codebase still keyed on the
  calendar year, and that made it the last reason poolOf had to carry two
  keys for one row:

      pkey = (pid, int(season))   calendar -- this table
      gkey = (pid, academic_year) academic -- grade_fix

  Every other season-keyed table -- grade_fix, athlete_season_level,
  pair_athlete_season, and the boards -- moved to the academic year. A table
  on a different clock is not a smaller version of the same thing; it is a
  join that silently matches nothing, which is how a spring race looked up
  the wrong pro season and how 372,478 of 372,641 athlete-seasons once came
  back NULL against a `LEFT JOIN` that looked fine.

  So this file no longer writes `left(date, 4)`. It asks season_year for the
  expression, exactly as grade_sanity does.

⚠ AND THE MEANING SHIFTS SLIGHTLY, IN THE RIGHT DIRECTION. A professional
  indoor race in December and the outdoor season that follows it in June are
  now ONE season rather than two, which is what they are. Under the calendar
  year an athlete who went pro in December was flagged for that year and then
  had to re-earn the flag in January.

Writes pro_athlete_season. Changes no pool and no rating.
"""

import os
import sys

sys.path.insert(0, 'scripts')

# ! THE SEASON EXPRESSION IS ASKED FOR, NOT RETYPED. season_year.py owns the
#   boundary; three copies of `left(date, 4)` in this file used to own their
#   own. Importing it means moving the seam is one edit and this file cannot
#   be left behind.
from season_year import seasonYearSqlInt

_YR = seasonYearSqlInt(None, "r.date")

# Unambiguously professional. Anchored where possible to avoid substring traps.
# ⚠ INVITATION-ONLY MEETS ONLY, AND WIDENING THIS WAS TRIED AND REVERTED.
#
#   Adding USATF Indoor and Outdoor Championships, Millrose, Prefontaine and
#   the European circuit took the seed from 8,618 athlete-seasons to 14,061
#   and filled the flagged list with high schoolers: Athing Mu 2019, Roisin
#   Willis 2020, Karrie Baloga, Blair Bartlett, Ellie Shea, Marlee Starliper.
#
#   The reason is entry standard, not name matching. A USATF championship has
#   OPEN ENTRY -- a high schooler who hits the qualifying mark races it, and
#   many do. The meet proves you were fast, not that you were professional.
#   Diamond League, the World and European Championships and the Continental
#   Tour are invitational: you are there because a promoter paid to have you.
#
#   That distinction is the whole seed. Any meet added here must be one a
#   schoolgirl cannot enter by running a qualifying time.
_INCLUDE = (r"(diamond league|world athletics champ|world indoor champ"
            r"|olympiad|olympic games|european athletics champ"
            r"|continental tour)")

# ⚠ WITHOUT THIS the seed is 9,000 twelve-year-olds. Measured from the corpus:
#   'AAU Junior Olympic Games'  8,984 athletes   'olympic games'
#   'Youth Diamond League Series'  495           'diamond league'
#   'USATF Masters Outdoor'        694           masters, not professional
#   'Diamond League #3 (BC-HT)'    143           a local BC series
_EXCLUDE = (r"(youth|junior|jr |aau|masters|senior games|little athletics"
            r"|age group|diamond league #|open &|high school|middle school)")

# ★ NCAA ELIGIBILITY IS THE VETO. Turning professional forfeits NCAA
#   eligibility, so racing an NCAA meet in a season PROVES that season was not
#   professional. This is the one signal that is decisive rather than
#   suggestive, and it fixes the whole college_f block: Parker Valby, Doris
#   Lemngole, Klaudia Kazimierska and Pamela Kosgei all race Diamond League
#   fields while enrolled, which is normal for NCAA distance stars and is why
#   they were flagged. Extend this pattern if collegiate meets in your corpus
#   use other names -- but keep it narrow, since a false NCAA match un-flags a
#   real professional.
_NCAA = r"(ncaa)"

# ★ A COUNTRY AT A SENIOR CHAMPIONSHIP IS A NATIONAL TEAM, ONE RACE IS ENOUGH
#   (owner, 2026-09-29, "do smthn abt this": Joao N'Tyamba "Angola IN" at the
#   1992 Olympics, Vyacheslav Shabunin "Russia OH" at the 1996 Olympics,
#   Clive Terrelonge "Jamaica CA" -- all on the all-time high school list).
#   An Olympian may run one 800 heat and go home, so _MIN_PRO_RACES cannot
#   be asked of them; what makes it certain instead is the school string
#   naming a country AT a meet only national teams enter. Either alone is
#   not enough: Angola High School is real, and a US junior at the Worlds
#   races for "USA", which is in neither list. The NCAA veto and the age
#   floor still apply below.
_SENIOR_CHAMP = (r"(olympic games|olympiad|world championships|world champs"
                 r"|world athletics champ|world indoor champ|pan american games"
                 r"|pan am games|commonwealth games|world cross country champ)")
_SENIOR_EXCLUDE = (_EXCLUDE[:-1] + r"|u20|u18|u23|under 2|under 1|special olympic"
                   r"|deaflympic|paralympic|universiade|university|student)")
_NT_STRIP = r"\s*\([a-z .]{2,6}\)\s*$|\s+(national team|nt|team)\s*$"


def nationalTeamNames():
    """Every lower-case country spelling a national-team row may carry."""
    from pool_resolve import AMBIGUOUS_NATIONAL_TEAMS, NATIONAL_TEAMS
    return sorted(NATIONAL_TEAMS | AMBIGUOUS_NATIONAL_TEAMS)


def nationalTeamSeedSql(results, meets, label_cols=()):
    """(person_id, yr) of rows at a senior international championship whose
    school is a country. Parameters: nationalTeamSeedParams(label_cols) --
    _SENIOR_CHAMP, _SENIOR_EXCLUDE (once for the meet name, once per label
    column), _NT_STRIP, the name list."""
    labels = "".join(f"\n          AND COALESCE(m.{c}, '') !~* %s"
                     for c in label_cols)
    return f"""
        SELECT DISTINCT r.person_id, {_YR} AS yr
        FROM {results} r
        JOIN {meets} m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        WHERE m.meet_name ~* %s AND m.meet_name !~* %s{labels}
          AND btrim(regexp_replace(lower(btrim(r.school)), %s, '')) = ANY(%s)
          AND r.date ~ '^(19|20)[0-9]{{2}}'
          AND r.person_id IS NOT NULL"""


def nationalTeamSeedParams(label_cols=()):
    """The positional parameters nationalTeamSeedSql(..., label_cols) takes."""
    return ((_SENIOR_CHAMP, _SENIOR_EXCLUDE)
            + (_SENIOR_EXCLUDE,) * len(label_cols)
            + (_NT_STRIP, nationalTeamNames()))


# ★ A SENIOR CHAMPIONSHIP'S JUNIOR RACE IS NOT A SENIOR RACE (2026-09-30).
#   _SENIOR_EXCLUDE read the meet NAME only, and the World Cross Country
#   Championships is one meet with a U20 race in it: Jackson Spencer, a
#   Herriman senior, ran its U20 8000 m on 2026-01-10, and every foreign
#   junior in that field -- "Kenya", "Ethiopia", "Uganda" -- took the
#   one-race national-team seed. A field of seeded "professionals" then
#   counted toward _FIELD_FRAC for everybody in it.
#
# ! THE DIVISION SAYS IT WHERE THE FEED KEEPS ONE: meets.division (anet
#   cross country), meets_tf.division and meets_tf.event_short (track). The
#   same exclusion pattern the meet name already answers to, so "U20 Men",
#   "Junior Women" and "U18 1500m" all fall out. Probed, never assumed: a
#   database without the column seeds exactly as before.
def seniorLabelColumns(cur, meets):
    """The label columns `meets` carries that can name a junior race."""
    return tuple(c for c in ("division", "event_short")
                 if hasColumn(cur, meets, c))


# ================================================================== #
#  SCHOOL ELIGIBILITY IS A VETO, LIKE NCAA ELIGIBILITY
# ================================================================== #
#
# ★ OWNER, 2026-09-30 (Jackson Spencer, 30178075; the rule and its limits
#   are pool_resolve.schoolSeasonVetoesPro's, which is this same test per
#   row): a season whose rows carry a SCHOOL grade at a SCHOOL team is not
#   a professional season by inference. The NCAA veto above already reads
#   eligibility as evidence ("racing an NCAA meet in a season PROVES that
#   season was not professional"); racing for your high school does too.
#
# ★ AT THE SOURCE, SEASON-WIDE. pool_resolve can only see one row, so an
#   unattached Bowerman Mile in the same season as forty Herriman rows is
#   one row it cannot judge. Here every row of the season is in view, and a
#   vetoed season never enters tmp_pro_season -- so it is not a "confirmed
#   pro" in anybody else's field fraction either. Spencer at the Festival
#   of Miles was one of the pros that made its field professional.
#
# THE SEASON'S GRADE, exactly as season_level.refuseCollegeBeforeCollege
#   reads it for the college veto (and through the same seasonIsSchool): the
#   grade_fix grade when the season has a grade_fix row -- a LEVEL verdict
#   (stale_grade, no_grades) has none, and nothing is vetoed -- else more
#   school grades than college class words among the school-team rows.
#
# A SCHOOL TEAM, exactly as the engine levels a row's team: anet's level for
#   the team id (speed_ratings_db.loadTeamLevels), else the tfrrs slug; and
#   not team_id 0 (no team), not a team build_team_pool calls professional,
#   not a hand-listed squad. A team nobody has levelled is not a school --
#   an elite squad's feed writes year counts where grades go.
#
# ! THE HAND LIST STANDS. A season pool_resolve._PRO_SEASONS names is taken
#   back out of the veto, in either sport, so a Lutkenhaus still counts as
#   the professional he is in every field he runs.
_SCHOOL_TEAM_LEVELS = ("hs", "ms", "elem")


def _loadTeams():
    """({anet team_id: level}, {professional team_id}) from the loaders the
    engine uses. Empty on a database without them: no veto from team ids,
    which is the old behaviour, said out loud."""
    try:
        from speed_ratings_db import loadTeamLevels, loadProTeams
        levels = loadTeamLevels()[0]
        pro_teams = loadProTeams()[0]
    except Exception as exc:                                # noqa: BLE001
        print(f"    ⚠ team levels unavailable ({type(exc).__name__}: {exc}) "
              f"-- only tfrrs slugs can name a school team")
        return {}, set()
    return levels, pro_teams


def _schoolTeamSql(cur, results):
    """(joins, predicate) naming a row at a school team in `results`."""
    from pool_resolve import _PRO_TEAMS
    has_team = hasColumn(cur, results, "team_id")
    has_slug = hasColumn(cur, results, "team_slug")
    slug = ("(split_part(r.team_slug, '_', 3) <> '' AND "
            "lower(split_part(r.team_slug, '_', 2)) IN ('hs', 'ms'))"
            if has_slug else "FALSE")
    if has_team:
        joins = """
            LEFT JOIN tmp_team_level tl ON tl.team_id = r.team_id
            LEFT JOIN tmp_pro_team pt ON pt.team_id = r.team_id"""
        level = f"""CASE WHEN r.team_id = 0 THEN FALSE
                         WHEN pt.team_id IS NOT NULL THEN FALSE
                         WHEN tl.team_id IS NOT NULL THEN tl.school
                         ELSE COALESCE({slug}, FALSE) END"""
    else:
        joins, level = "", f"COALESCE({slug}, FALSE)"
    # pool_resolve._normSchool, in SQL: casefold, punctuation out, one space
    norm = ("btrim(regexp_replace(regexp_replace(lower(COALESCE(r.school, '')),"
            " '[^a-z0-9 ]+', '', 'g'), '\\s+', ' ', 'g'))")
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in sorted(_PRO_TEAMS))
    return joins, f"({level}) AND {norm} NOT IN ({names})"


def buildSchoolSeasons(cur, team_levels=None, pro_teams=None):
    """tmp_school_season (person_id, yr): the athlete-seasons the school veto
    removes from inference. See the block above; seasonIsSchool is the rule.

    team_levels / pro_teams default to the engine's loaders; the tests hand
    them in."""
    from psycopg2.extras import execute_values
    from pool_resolve import _PRO_SEASONS, isProPerson
    from season_level import _gradeKinds

    if team_levels is None or pro_teams is None:
        got_levels, got_pro = _loadTeams()
        team_levels = got_levels if team_levels is None else team_levels
        pro_teams = got_pro if pro_teams is None else pro_teams
    _gradeKinds(cur)
    cur.execute("DROP TABLE IF EXISTS tmp_team_level")
    cur.execute("CREATE TEMP TABLE tmp_team_level "
                "(team_id bigint PRIMARY KEY, school boolean NOT NULL)")
    rows = [(int(t), lv in _SCHOOL_TEAM_LEVELS)
            for t, lv in (team_levels or {}).items() if lv]
    if rows:
        execute_values(cur, "INSERT INTO tmp_team_level VALUES %s", rows,
                       page_size=10000)
    cur.execute("DROP TABLE IF EXISTS tmp_pro_team")
    cur.execute("CREATE TEMP TABLE tmp_pro_team (team_id bigint PRIMARY KEY)")
    if pro_teams:
        execute_values(cur, "INSERT INTO tmp_pro_team VALUES %s",
                       [(int(t),) for t in pro_teams], page_size=10000)

    cur.execute("DROP TABLE IF EXISTS tmp_school_rows")
    cur.execute("""CREATE TEMP TABLE tmp_school_rows
                   (person_id bigint, yr int, n_school int, n_class int)""")
    for results in ("results", "results_tf"):
        joins, school_team = _schoolTeamSql(cur, results)
        cur.execute(f"""
            INSERT INTO tmp_school_rows
            SELECT r.person_id, {_YR},
                   count(*) FILTER (WHERE gk.school),
                   count(*) FILTER (WHERE NOT gk.school)
            FROM {results} r
            {joins}
            LEFT JOIN tmp_grade_kind gk ON gk.g = r.grade
            WHERE r.person_id IS NOT NULL
              AND r.date ~ '^(19|20)[0-9]{{2}}'
              AND {school_team}
            GROUP BY 1, 2""")

    cur.execute("SELECT to_regclass('grade_fix')")
    if cur.fetchone()[0] is not None:
        join = """LEFT JOIN grade_fix gf ON gf.person_id = s.person_id
                                        AND gf.season = s.yr"""
        # season_level.refuseCollegeBeforeCollege's test, and seasonIsSchool's
        school = """CASE WHEN gf.person_id IS NOT NULL
                         THEN COALESCE(gf.grade IN (SELECT g FROM tmp_grade_kind
                                                    WHERE school), FALSE)
                              AND s.n_class <= s.n_school
                         ELSE s.n_school > s.n_class END"""
    else:
        join, school = "", "s.n_school > s.n_class"
    cur.execute("DROP TABLE IF EXISTS tmp_school_season")
    cur.execute(f"""
        CREATE TEMP TABLE tmp_school_season AS
        SELECT DISTINCT s.person_id, s.yr
        FROM (SELECT person_id, yr, sum(n_school) AS n_school,
                     sum(n_class) AS n_class
              FROM tmp_school_rows GROUP BY 1, 2) s
        {join}
        WHERE {school}""")

    # the hand list stands: its seasons are professional in either sport
    hand = sorted(_PRO_SEASONS)
    if hand:
        cur.execute("SELECT person_id, yr FROM tmp_school_season "
                    "WHERE person_id = ANY(%s)", (hand,))
        keep = [(p, y) for p, y in cur.fetchall()
                if isProPerson(p, "XC", y) or isProPerson(p, "TF", y)]
        for p, y in keep:
            cur.execute("DELETE FROM tmp_school_season "
                        "WHERE person_id = %s AND yr = %s", (p, y))
        if keep:
            print(f"    school veto: {len(keep):,} hand-listed "
                  f"professional seasons kept professional")
    cur.execute("CREATE INDEX ON tmp_school_season (person_id, yr)")
    cur.execute("ANALYZE tmp_school_season")
    cur.execute("SELECT count(*) FROM tmp_school_season")
    n = cur.fetchone()[0]
    print(f"    school veto: {n:,} athlete-seasons carry a school grade "
          f"at a school team")
    return n

# ⚠ THE SCHOOL NAME IS NOT A SEED, AND THIS WAS TRIED AND REMOVED.
#
#   Jackson Sharp runs for HOKA NAZ Elite and never races a seeded meet, so
#   the traversal cannot reach him. Adding a list of professional club names
#   looked like the fix. It was not.
#
#   `school` is whatever the scraper found in the team column, and for an
#   unattached entry at a club-hosted meet that is THE CLUB. Addison
#   Ritzenhein, a Niwot high schooler, has four rows reading
#   "UNAT-On Athletics Club" -- UNAT means unattached -- and an unanchored
#   match on "on athletics club" flagged her professional. Two earlier
#   patterns, `asics` and `nike swoosh`, had already flagged two elementary
#   schoolers and three middle schoolers, because sponsors name youth teams.
#
#   The name in that column describes how one row was typed, not who the
#   athlete is. A meet is a fact about the race; a school string is not.
#   Widening the MEET seed -- USATF championships, Millrose, Prefontaine, the
#   national championships of other countries -- is the version of this that
#   uses evidence of the same kind that already works.
#
_MIN_PRO_RACES = 3      # 1 caught Sam Ruthe and Owen Powell, real schoolers
_FIELD_FRAC = 0.70      # share of a field already confirmed -> pro race
# ★ 0.70 IS MEASURED, NOT CHOSEN. At 0.50 the propagation leaked -- successive
#   rounds ADDED +1,916, +2,100, +2,156, +2,591, +3,609, +5,668, +10,526, which
#   is a threshold eating whole NCAA championship fields. At 0.70 it decayed
#   cleanly: +538, +291, +178, +59, +51, +20, +2.
#
#   The reason 0.50 fails is visible in the field-composition histogram, which
#   is a RAMP and not two clusters: 4,396 races at 0.0-0.1 declining smoothly to
#   304 at 0.5-0.6, then a spike of 614 at exactly 1.0. The middle is foreign
#   collegians -- they race Diamond League AND their conference meet, so a
#   24-person NCAA final holding six of them sits near 0.25 and a weaker final
#   can drift past 0.50.
_MIN_FIELD = 6          # below this, "most of the field" means nothing
_MAX_ROUNDS = 8


# ------------------------------------------------------------------ #
# CHUNK 1 -- SCHEMA
# ------------------------------------------------------------------ #

def hasColumn(cur, table, column):
    """Does <table>.<column> exist? House rule: read the schema, never guess."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s LIMIT 1""",
                (table, column))
    return cur.fetchone() is not None


def raceGrain(cur, table):
    """
    The finest race identifier this table offers.

    ★ A TF meet-division holds many EVENTS -- the 100m and the mile are one
      div_id. Grouping at div_id would blend a high-school 800 with a
      professional 800 at the same meet and dilute both. event_id is used when
      present for exactly that reason.
    """
    cols = ["meet_id", "div_id"]
    for extra in ("event_id", "source"):
        if hasColumn(cur, table, extra):
            cols.append(extra)
    return cols


# ------------------------------------------------------------------ #
# CHUNK 2 -- SEED
# ------------------------------------------------------------------ #

def buildSeed(cur, min_pro_races=_MIN_PRO_RACES, team_levels=None,
              pro_teams=None):
    """
    Athlete-SEASONS with enough races at unambiguously professional meets.

    Output: rows into tmp_pro_season (person_id, yr, pro_races, round), and
    the two veto sets propagate reads: tmp_ncaa_season, tmp_school_season.

    One row per (person, year): a professional season is evidence about THAT
    season only. See the module docstring for why an absorbing flag was wrong.

    team_levels / pro_teams: see buildSchoolSeasons.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_pro_race")
    cur.execute("CREATE TEMP TABLE tmp_pro_race (person_id bigint, yr int)")

    # ⚠ ONE ROW PER RESULT, NOT PER EVENT OF ITS DIVISION (2026-09-30). This
    #   was a JOIN on (meet_id, div_id), and meets_tf holds a row per (div,
    #   EVENT) -- so a track result was counted once for every event in its
    #   division, and one Diamond League race cleared _MIN_PRO_RACES alone.
    #   Jackson Spencer's Bowerman Mile (and its 1500 split) at the
    #   Prefontaine Classic seeded his high school senior season that way.
    #   meets (cross country) is one row per division, so it never
    #   multiplied; EXISTS reads both the same.
    for results, meets in (("results", "meets"), ("results_tf", "meets_tf")):
        cur.execute(f"""
            INSERT INTO tmp_pro_race (person_id, yr)
            SELECT r.person_id, {_YR}
            FROM {results} r
            WHERE EXISTS (SELECT 1 FROM {meets} m
                          WHERE m.meet_id = r.meet_id AND m.div_id = r.div_id
                            AND m.meet_name ~* %s AND m.meet_name !~* %s)
              AND r.date ~ '^(19|20)[0-9]{{2}}'
              AND r.person_id IS NOT NULL
        """, (_INCLUDE, _EXCLUDE))
        print(f"    {results}: {cur.rowcount:,} rows at seeded meets")

    # The veto set: (person, year) pairs with any NCAA-named race.
    cur.execute("DROP TABLE IF EXISTS tmp_ncaa_season")
    cur.execute("CREATE TEMP TABLE tmp_ncaa_season (person_id bigint, yr int)")
    for results, meets in (("results", "meets"), ("results_tf", "meets_tf")):
        cur.execute(f"""
            INSERT INTO tmp_ncaa_season (person_id, yr)
            SELECT DISTINCT r.person_id, {_YR}
            FROM {results} r
            JOIN {meets} m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE m.meet_name ~* %s AND r.date ~ '^(19|20)[0-9]{{2}}'
              AND r.person_id IS NOT NULL
        """, (_NCAA,))
    cur.execute("CREATE INDEX ON tmp_ncaa_season (person_id, yr)")
    cur.execute("ANALYZE tmp_ncaa_season")
    cur.execute("SELECT count(*) FROM tmp_ncaa_season")
    print(f"    NCAA veto: {cur.fetchone()[0]:,} athlete-seasons with an "
          f"NCAA race")

    cur.execute("DROP TABLE IF EXISTS tmp_pro_season")
    cur.execute(f"""
        CREATE TEMP TABLE tmp_pro_season AS
        SELECT person_id, yr, count(*)::int AS pro_races, 0 AS round
        FROM tmp_pro_race
        GROUP BY person_id, yr
        HAVING count(*) >= {min_pro_races}
    """)
    # the national-team seed: one race at a senior championship, see above --
    # and not its junior races, which the division names (seniorLabelColumns)
    n_nt = 0
    for results, meets in (("results", "meets"), ("results_tf", "meets_tf")):
        labels = seniorLabelColumns(cur, meets)
        cur.execute(f"""
            INSERT INTO tmp_pro_season (person_id, yr, pro_races, round)
            SELECT s.person_id, s.yr, 1, 0
            FROM ({nationalTeamSeedSql(results, meets, labels)}) s
            WHERE NOT EXISTS (SELECT 1 FROM tmp_pro_season p
                              WHERE p.person_id = s.person_id AND p.yr = s.yr)
        """, nationalTeamSeedParams(labels))
        n_nt += cur.rowcount
    print(f"    national teams at senior championships: {n_nt:,} more "
          f"athlete-seasons")

    cur.execute("""DELETE FROM tmp_pro_season p
                   USING tmp_ncaa_season n
                   WHERE n.person_id = p.person_id AND n.yr = p.yr""")
    print(f"    NCAA veto removed {cur.rowcount:,} seeded athlete-seasons")

    # ★ AND SCHOOL ELIGIBILITY, THE SAME WAY (owner, 2026-09-30): see
    #   buildSchoolSeasons. Before propagation, so a schoolboy is never a
    #   confirmed professional in another race's field.
    buildSchoolSeasons(cur, team_levels=team_levels, pro_teams=pro_teams)
    cur.execute("""DELETE FROM tmp_pro_season p
                   USING tmp_school_season s
                   WHERE s.person_id = p.person_id AND s.yr = p.yr""")
    print(f"    school veto removed {cur.rowcount:,} seeded athlete-seasons")

    # The age floor. See _MIN_PRO_LEVEL: a name list will always leak, and
    # this removes the whole class rather than the pattern that caused it.
    cur.execute("SELECT to_regclass('athlete_season')")
    if cur.fetchone()[0] is not None:
        cur.execute("""
            DELETE FROM tmp_pro_season p
            USING  athlete_season a
            WHERE  a.person_id::text = p.person_id::text
              AND  a.year = p.yr
              AND  split_part(a.pool, '_', 1) IN ('elem', 'ms')
        """)
        print(f"    age floor removed {cur.rowcount:,} seeded athlete-seasons "
              f"pooled elem or ms")

    cur.execute("SELECT count(*) FROM tmp_pro_season")
    n = cur.fetchone()[0]
    cur.execute("CREATE INDEX ON tmp_pro_season (person_id, yr)")
    cur.execute("ANALYZE tmp_pro_season")
    cur.execute("SELECT count(DISTINCT person_id) FROM tmp_pro_season")
    print(f"    seed: {n:,} athlete-seasons "
          f"({cur.fetchone()[0]:,} athletes) with >= {min_pro_races} pro races")
    return n


def _suffix(table):
    return "xc" if table == "results" else "tf"


def buildRaceTables(cur, grains, min_field=_MIN_FIELD, since=None):
    # Session-scoped. The temp tables are dropped at the end either way, and
    # the index builds below sort tens of millions of rows.
    cur.execute("SET maintenance_work_mem = '2GB'")
    cur.execute("SET synchronous_commit = off")
    """
    ★ THE OPTIMISATION. Two scans total, instead of two per round.

    Field SIZE never changes between rounds -- only the count of confirmed pros
    in it does. So the per-race totals are computed ONCE, and membership is
    stored indexed BY PERSON. A round then drives from the ~400 known pros and
    index-looks-up their races, instead of scanning 61M rows to discover that
    99% of races contain no pro at all.

    Cost: ~2 scans plus two index builds, once. Each round afterwards touches
    only the races a known pro actually ran.
    """
    # ⚠ EVERY ROW, NOT JUST RATED ONES, AND THIS WAS TRIED AND REVERTED.
    #   Filtering to rated rows cut tmp_member_tf from 191.3M to 25.0M and
    #   looked like a free 87%. It killed the propagation: +21 athletes became
    #   +0, and the fully-professional races in the histogram went from 445 at
    #   ratio 1.0 to five.
    #
    #   The reason is min_field, not the ratio. A Diamond League final is
    #   twelve runners; drop the unrated ones and it falls under six finishers
    #   and stops being a race at all. The field SIZE has to be the real field.
    #
    # ! person_id IS NOT NULL is kept: a row with no athlete cannot be a
    #   membership, and it does not change any field's size for this purpose.
    where = "r.date ~ '^(19|20)[0-9]{2}' AND r.person_id IS NOT NULL"
    if since:
        where += f" AND r.date >= '{since}-01-01'"

    for results, _meets, grain in grains:
        sfx = _suffix(results)
        keys = ", ".join(grain)
        rkeys = ", ".join(f"r.{c}" for c in grain)

        cur.execute(f"DROP TABLE IF EXISTS tmp_member_{sfx}")

        # ! UNLOGGED. A temp table is already crash-exempt in the sense that it
        #   dies with the session, but Postgres still WAL-logs a plain CREATE
        #   TEMP TABLE AS unless told otherwise on some configurations. This
        #   makes the intent explicit and costs nothing where it was already
        #   free.
        #
        # ! AND THE SMALLEST TYPES THAT FIT. 191M rows carry these four columns
        #   through two index builds, so the width is the runtime. yr is a
        #   smallint (a year fits in 32,767) and the race keys are already
        #   narrow; the win is not writing left(date,4) as text and casting.
        cur.execute(f"""
            CREATE TEMP TABLE tmp_member_{sfx} AS
            SELECT {rkeys}, r.person_id,
                   ({_YR})::smallint AS yr
            FROM {results} r WHERE {where}
        """)
        n_rows = cur.rowcount

        # Field size per race-year, once. Small: one row per race.
        cur.execute(f"DROP TABLE IF EXISTS tmp_field_{sfx}")
        cur.execute(f"""
            CREATE TEMP TABLE tmp_field_{sfx} AS
            SELECT {keys}, yr, count(*)::int AS n
            FROM tmp_member_{sfx}
            GROUP BY {keys}, yr
            HAVING count(*) >= {min_field}
        """)
        n_races = cur.rowcount

        # BY PERSON is the index that matters -- it is what lets a round start
        # from the pro list rather than from the corpus.
        #
        # ! ONE COMPOSITE INSTEAD OF TWO SINGLES where the second was only ever
        #   probed alongside the race keys. Postgres can use a leading-column
        #   prefix, so (person_id, yr) serves the person lookup too.
        cur.execute(f"CREATE INDEX ON tmp_member_{sfx} (person_id, yr)")
        cur.execute(f"CREATE INDEX ON tmp_member_{sfx} ({keys}, yr)")
        cur.execute(f"CREATE INDEX ON tmp_field_{sfx} ({keys}, yr)")
        cur.execute(f"ANALYZE tmp_member_{sfx}")
        cur.execute(f"ANALYZE tmp_field_{sfx}")
        print(f"    {results}: {n_rows:,} memberships, "
              f"{n_races:,} races with >= {min_field} finishers")


def reportFieldDistribution(cur, results, grain, min_field=_MIN_FIELD):
    """
    ★ MEASURE BEFORE PICKING THE THRESHOLD. _FIELD_FRAC = 0.5 is a guess until
      this says the distribution is bimodal. If most races sit in the middle, no
      threshold separates professional fields from school fields and propagation
      should not run.
    """
    sfx = _suffix(results)
    keys = ", ".join(grain)
    on = " AND ".join(f"f.{c} = m.{c}" for c in grain)
    cur.execute(f"""
        WITH pro AS (
            SELECT m.{grain[0]} AS k0, {", ".join("m." + c for c in grain[1:])},
                   m.yr, count(*) AS n_pro
            FROM tmp_member_{sfx} m
            JOIN tmp_pro_season p ON p.person_id = m.person_id
                                 AND p.yr = m.yr
            GROUP BY 1, {", ".join(str(i + 2) for i in range(len(grain) - 1))},
                     {len(grain) + 1}
        )
        SELECT width_bucket(pro.n_pro::float / f.n, 0, 1, 10) AS bucket,
               count(*) AS races, sum(f.n) AS rows_
        FROM pro JOIN tmp_field_{sfx} f
          ON f.{grain[0]} = pro.k0 AND f.yr = pro.yr
         AND {" AND ".join(f"f.{c} = pro.{c}" for c in grain[1:])}
        GROUP BY 1 ORDER BY 1
    """)
    print(f"    field composition in {results} (races with >=1 known pro):")
    for bucket, races, rows in cur.fetchall():
        lo = (bucket - 1) / 10.0
        print(f"      {lo:.1f}-{lo + 0.1:.1f}: {races:>8,} races  {rows:>10,} rows")


# ------------------------------------------------------------------ #
# CHUNK 4 -- PROPAGATE
# ------------------------------------------------------------------ #

def propagate(cur, grains, rounds=_MAX_ROUNDS, frac=_FIELD_FRAC,
              min_added=5, min_pro=_MIN_PRO_RACES):
    """
    Fixed-point expansion: pro races reveal pros, who reveal more pro races.

    Same structure as informativeMask -- iterate until nothing changes, because
    each addition can qualify another race. Now driven from tmp_pro_season, so a
    round costs a lookup per known pro rather than a scan of the corpus.
    """
    total_new, prev = 0, None
    for rnd in range(1, rounds + 1):
        added = 0
        for results, _meets, grain in grains:
            sfx = _suffix(results)
            join_pro = " AND ".join(f"f.{c} = pro.{c}" for c in grain)
            join_mem = " AND ".join(f"m2.{c} = f.{c}" for c in grain)
            cur.execute(f"""
                INSERT INTO tmp_pro_season (person_id, yr,
                                            pro_races, round)
                SELECT m2.person_id, m2.yr, count(*), {rnd}
                FROM (
                    SELECT {", ".join("m." + c for c in grain)}, m.yr,
                           count(*) AS n_pro
                    FROM tmp_member_{sfx} m
                    JOIN tmp_pro_season p ON p.person_id = m.person_id
                                         AND p.yr = m.yr
                    GROUP BY {", ".join(str(i + 1) for i in range(len(grain) + 1))}
                ) pro
                JOIN tmp_field_{sfx} f ON {join_pro} AND f.yr = pro.yr
                                      AND pro.n_pro::float / f.n >= {frac}
                JOIN tmp_member_{sfx} m2 ON {join_mem} AND m2.yr = f.yr
                LEFT JOIN tmp_pro_season q ON q.person_id = m2.person_id
                                          AND q.yr = m2.yr
                LEFT JOIN tmp_ncaa_season nc ON nc.person_id = m2.person_id
                                            AND nc.yr = m2.yr
                LEFT JOIN tmp_school_season sc ON sc.person_id = m2.person_id
                                              AND sc.yr = m2.yr
                -- ★ THE VETO MUST APPLY DURING PROPAGATION, not only after.
                --   A flagged collegian counts toward field composition, so
                --   leaving them in would let NCAA finals tip past the
                --   threshold and flag the whole field.
                -- ★ AND THE SCHOOL VETO THE SAME WAY (2026-09-30): a school
                --   senior at the Festival of Miles is a non-professional
                --   in its field, never a confirmed one.
                WHERE q.person_id IS NULL AND nc.person_id IS NULL
                  AND sc.person_id IS NULL
                  AND m2.person_id IS NOT NULL
                GROUP BY m2.person_id, m2.yr
                -- ★ SAME EVIDENCE BAR AS THE SEED. Without this, propagation
                --   needed ONE professional field while the seed needed three,
                --   and a high schooler who ran a single elite race was flagged
                --   for life. It caught Sadie Engelhardt (pro_races 2), Chase
                --   Howard, Autumn Ost and both Robertsons -- all genuine
                --   American schoolers -- while every correct catch had 41+.
                HAVING count(*) >= {min_pro}
            """)
            added += cur.rowcount
        print(f"    round {rnd}: +{added:,} athletes")
        total_new += added
        # ⚠ STOP ON A TRICKLE, NOT ONLY ON ZERO. Adding one athlete can tip one
        #   more race, which adds one more -- a chain that ran 90+ rounds at
        #   +1 each. Those are single marginal races, not a population.
        if added < min_added:
            if added:
                print(f"    stopping: fewer than {min_added} per round is a "
                      f"chain of marginal races, not a population")
            break
        # ⚠ AND STOP ON AN EXPLOSION. Growth that accelerates means the
        #   threshold is leaking into NCAA fields; one more round would flag
        #   whole championship fields including domestic collegians.
        if rnd > 1 and added > 3 * prev:
            print(f"    ⚠ ROUND {rnd} GREW {added / max(prev, 1):.1f}x -- "
                  f"leaking. Raise _FIELD_FRAC and re-run.")
            break
        prev = added
    return total_new


# ------------------------------------------------------------------ #
# CHUNK 5 -- OUTPUT
# ------------------------------------------------------------------ #

_DDL = """
    CREATE TABLE pro_athlete_season (
        person_id   bigint  NOT NULL,
        season      int     NOT NULL,
        pro_races   int,
        round       int,
        PRIMARY KEY (person_id, season)
    )"""


def writeTable(cur):
    """
    One row per PROFESSIONAL athlete-season.

    ★ Not one row per athlete. A season is professional on its own evidence, so
      an athlete can be professional in 2025 and a collegian in 2026 -- which is
      exactly what Engelhardt did, and what an absorbing flag got wrong.
    """
    cur.execute("DROP TABLE IF EXISTS pro_athlete_season")
    cur.execute(_DDL)
    cur.execute("""INSERT INTO pro_athlete_season
                   SELECT person_id, yr, sum(pro_races)::int, min(round)
                   FROM tmp_pro_season GROUP BY person_id, yr""")
    n = cur.rowcount
    cur.execute("CREATE INDEX ON pro_athlete_season (season)")

    # ★ NCAA IS ONE-WAY INTO SCHOOL POOLS. An athlete who has raced NCAA cannot
    #   subsequently be a high schooler -- unlike "pro", which Engelhardt showed
    #   is NOT absorbing, this direction genuinely is. It catches the
    #   post-collegiate athletes who never touch a Diamond League field and so
    #   never seed: a club runner at Apex Elite with four races and a 137 rating
    #   in a school pool.
    cur.execute("DROP TABLE IF EXISTS ncaa_first_season")
    cur.execute("""CREATE TABLE ncaa_first_season (
                       person_id    bigint NOT NULL PRIMARY KEY,
                       first_season int    NOT NULL)""")
    cur.execute("""INSERT INTO ncaa_first_season
                   SELECT person_id, min(yr) FROM tmp_ncaa_season
                   WHERE person_id IS NOT NULL
                   GROUP BY person_id""")
    print(f"    ncaa_first_season: {cur.rowcount:,} athletes")
    return n


def reportTop(cur, limit=30):
    """The flagged athlete-seasons that were rated highest -- the sanity check."""
    cur.execute("SELECT to_regclass('pair_athlete_season')")
    if cur.fetchone()[0] is None:
        print("    (pair_athlete_season not found; skipping rating check)")
        return
    cur.execute(f"""
        SELECT an.name, s.pool, s.season, s.races,
               round(s.rating_seasonal::numeric, 1), p.pro_races, p.round
        FROM pair_athlete_season s
        JOIN (SELECT person_id, yr, sum(pro_races) AS pro_races,
                     min(round) AS round
              FROM tmp_pro_season GROUP BY person_id, yr) p
          ON p.person_id::text = s.person_id AND p.yr = s.season
        LEFT JOIN athlete_named an ON an.person_id = p.person_id
        WHERE s.races >= 3
        ORDER BY s.rating_seasonal DESC LIMIT {limit}
    """)
    print("\n[pro] highest-rated FLAGGED athlete-seasons")
    print("    rating  pool        season races  round  proraces  name")
    for name, pool, season, races, rating, pro_races, rnd in cur.fetchall():
        print(f"    {rating:>6}  {pool:<11} {season:>6} {races:>5} "
              f"{rnd:>6} {pro_races:>9}  {name or '?'}")


def main(write=False, rounds=_MAX_ROUNDS, show_dist=True, since=None):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            grains = []
            for results, meets in (("results", "meets"),
                                   ("results_tf", "meets_tf")):
                g = raceGrain(cur, results)
                grains.append((results, meets, g))
                print(f"[pro] {results} race grain: {', '.join(g)}")

            print("\n[pro] building race tables (one pass)...")
            buildRaceTables(cur, grains, since=since)

            print("\n[pro] seeding...")
            if not buildSeed(cur):
                print("[pro] empty seed -- check the meet patterns")
                return

            if show_dist:
                # Two full scans. Worth it once, to check _FIELD_FRAC is sane;
                # skippable on re-runs with --skip-dist.
                print("\n[pro] field composition BEFORE propagating:")
                for results, _m, g in grains:
                    reportFieldDistribution(cur, results, g)

            print("\n[pro] propagating...")
            new = propagate(cur, grains, rounds=rounds)
            cur.execute("SELECT count(*) FROM tmp_pro_season")
            print(f"[pro] total {cur.fetchone()[0]:,} athlete-seasons "
                  f"({new:,} from propagation)")

            reportTop(cur)

            if write:
                n = writeTable(cur)
                conn.commit()
                print(f"\n[pro] wrote pro_athlete_season ({n:,} rows)")
            else:
                print("\n[pro] DRY RUN -- pass --write to save "
                      "pro_athlete_season")


if __name__ == "__main__":
    # Each round is a full scan of both results tables, so rounds dominate the
    # runtime. Propagation converges fast -- 2 is usually enough.
    rounds = _MAX_ROUNDS
    for a in sys.argv[1:]:
        if a.startswith("--rounds="):
            rounds = int(a.split("=", 1)[1])
    since = None
    for a in sys.argv[1:]:
        if a.startswith("--since="):
            since = int(a.split("=", 1)[1])
    main(write="--write" in sys.argv, rounds=rounds,
         show_dist="--skip-dist" not in sys.argv, since=since)