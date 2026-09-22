"""
engine/pool_resolve.py -- the ONE decision about which pool a row belongs to.

WHY THIS EXISTS
    There were three implementations. The engine's poolOf ran a five-stage
    arbitration; build_ranking_results and panels each called poolFor with four
    of its five arguments and none of the gates. Measured against each other
    they disagreed on about a million athlete-seasons.

    That is not a cosmetic split, because speed_rating is POOL-RELATIVE and
    poolFor also selects the DISTANCE CURVE. Guillaume Tremblay, pooled ms_m,
    came out at 166.4 for an 800m and 109.1 for an 8000m -- the same athlete,
    57 points apart, because the ms_m time-vs-distance curve is fitted on
    800-3000m and 8000m is off the end of it. A pooling error does not shift a
    rating, it BENDS it, differently at every distance.

★ PURE. NO DATABASE, NO MODULE STATE, NO LOADERS.
  That is the whole trick. The engine holds 29M season levels in a dict because
  it is already carrying a 3.6GB pack; the site scripts stream tens of millions
  of rows on a box that is also running Postgres and cannot afford a second
  copy. So this function does not FETCH the five facts, it RECEIVES them --
  the engine passes them from its dicts, the site passes them from LEFT JOINs,
  and the decision is identical either way.

  The six facts and where the site gets them:
      season_level      athlete_season_level      (person_id, ay)
      grade_untrusted   grade_untrusted           (person_id, season)
      is_pro            pro_athlete_season        (person_id, season)
      pro_ability       pro_ability_season        (person_id, season) -> bool
      college_first     college_first_season      (person_id) -> date
      upperclass_first  upperclass_first_season   (person_id) -> date
"""

import re
from functools import lru_cache

from normalize_distance import poolFor

# ===================================================================== #
#  THE PROFESSIONAL ABILITY STANDARD
# ===================================================================== #
#
# ★ OWNER, 2026-09-22: "if they're sub 14:00? for men, or sub 15:30? for
#   women put in pro, otherwise trust grade." Seconds, as a 5000m-equivalent
#   normalized time.
#
# ⚠ THE CURVE IS NOT OPTIONAL AND IT IS NOT THE ATHLETE'S OWN POOL.
#   normalized_time is expressed at a DIFFERENT anchor distance per pool --
#   normalize_distance.targetFor: 5000 hs and pro, 8000 college men, 6000
#   college women, 3200 middle school. Reading the stored column would ask
#   a college man for a 14:00 EIGHT thousand, which nobody alive has run.
#   Every mark is measured on one common curve; see ABILITY_CURVE_POOL.
#
# ! MEASURED AGAINST REAL MARKS on the hs_m / hs_f potential, which is what
#   put these numbers where they are:
#
#       men, 14:00              women, 15:30
#         800  1:43 -> 13:27      800  1:56 -> 15:01
#         800  1:47 -> 13:58      800  2:00 -> 15:32  (fails)
#         800  1:50 -> 14:22      1500 4:05 -> 15:19
#         Mile 4:00 -> 13:56      Mile 4:20 -> 15:00
#         5000 13:30 -> 13:30     Mile 4:30 -> 15:35  (fails)
#         10000 28:30 -> 13:32    10000 31:30 -> 15:00
#
#   The 800 cut lands near 1:47.5 for men and 1:59.5 for women, which is
#   about the floor of professional half-lap racing. A 4:00 mile clears by
#   four seconds. The standard is deliberately hard: it is separating
#   professionals from a pool whose average member runs 25:53.
#
# ! STRICT. Exactly 14:00.0 does not qualify; the constant is the bar, not
#   the last passing value.
PRO_ABILITY_5K = {"M": 840.0, "F": 930.0}      # 14:00 / 15:30

# ⚠ ONE CURVE FOR EVERY MARK, WHATEVER SPORT IT WAS RUN IN. Under
#   fit_distance_exponent.MERGE_SPORTS (the default since 2026-09-19) XC
#   and TF share one potential per pool and this is a no-op. It matters
#   when the pickle on disk PREDATES that -- the Sep 15 build has separate
#   hs_m|XC and hs_m|TF potentials that disagree by 10-25s at the same
#   mark, enough to pass a 4:00 mile on one and fail it on the other. A
#   threshold that moves with the age of a pickle is not a threshold.
ABILITY_CURVE_POOL = {"M": "hs_m", "F": "hs_f"}
ABILITY_CURVE_SPORT = "TF"


# Level prefixes, kept here beside the promotions that use them.
# ===================================================================== #
#  SCOPE: WHICH RESULTS BELONG ON A BOARD AT ALL
# ===================================================================== #
#
# ★ AN EXPLICIT LIST, NOT A PATTERN. `state` holds 77 distinct two-letter
#   uppercase values for 50 states: the extras are Canadian provinces (AB, BC,
#   ON, QC), Australian (NSW, QLD, ACT) and similar. "Two capitals means
#   American" lets Ontario straight through, which is how Laval and Calgary
#   reached the boards.
#
# ⚠ AND IT IS THE ONLY GEOGRAPHY THERE IS. There is no country column
#   anywhere; state is the whole signal. Foreign rows carry region names
#   ("Auckland", "Canterbury", "Kanagawa") or a foreign two-letter code, so
#   membership of this set is the test.
#
# ⚠ A NULL STATE IS KEPT, NOT DROPPED, and that is a deliberate trade. Plenty
#   of legitimate US results have no state; dropping them would cost far more
#   than the foreign rows it would catch. So this filter removes what it can
#   PROVE is out of scope, and nothing else.
US_STATES = frozenset("""
    AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
    MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA
    WV WI WY
    PR GU VI AS MP
    AE AP AA
""".split())


# ⚠ SPELLED-OUT NAMES ARE ALSO US ROWS. The state column holds 18,938 of
#   them: "Texas", "Carolina", alongside foreign ones like "England" and
#   "Auckland". Testing only the two-letter codes threw away the American
#   ones, which is the opposite of what this filter is for. FIPS numerics are
#   here too, since 01-56 are the states.
US_NAMES = frozenset("""
    ALABAMA ALASKA ARIZONA ARKANSAS CALIFORNIA COLORADO CONNECTICUT DELAWARE
    FLORIDA GEORGIA HAWAII IDAHO ILLINOIS INDIANA IOWA KANSAS KENTUCKY
    LOUISIANA MAINE MARYLAND MASSACHUSETTS MICHIGAN MINNESOTA MISSISSIPPI
    MISSOURI MONTANA NEBRASKA NEVADA OHIO OKLAHOMA OREGON PENNSYLVANIA
    TENNESSEE TEXAS UTAH VERMONT VIRGINIA WASHINGTON WISCONSIN WYOMING
""".split()) | {
    "NEW HAMPSHIRE", "NEW JERSEY", "NEW MEXICO", "NEW YORK",
    "NORTH CAROLINA", "NORTH DAKOTA", "SOUTH CAROLINA", "SOUTH DAKOTA",
    "RHODE ISLAND", "WEST VIRGINIA", "DISTRICT OF COLUMBIA",
    "PUERTO RICO", "GUAM",
    # "Carolina" with no direction is ambiguous but is not a foreign place.
    "CAROLINA",
}


# ! FIFTY-ONE POSSIBLE ANSWERS, ASKED ONCE PER ROW. Cached.
@lru_cache(maxsize=4096)
def inScope(state):
    """True when this result belongs on a US board.

    Case-insensitive: the column carries 'Ca' and 'CA' for California, and the
    site upper-cases on the way in while the engine does not.
    """
    if state is None:
        return True                      # unprovable, so kept: see above
    s = str(state).strip().upper()
    if not s:
        return True
    if s.isdigit():
        # FIPS. 01-56 are states, 60+ are territories; anything else is not a
        # state code at all and is treated as unknown rather than foreign.
        return 1 <= int(s) <= 78
    return s in US_STATES or s in US_NAMES


_SUB_HS_LEVELS = ("elem", "ms")
_SCHOOL_LEVELS = ("elem", "ms", "hs")
_PRO_LEVELS = ("elem", "ms", "hs", "college")

# Ordered, so "is this a promotion" is a subtraction. Includes pro, which
# _PRO_LEVELS does not -- that tuple lists what CAN BE swapped TO pro, this
# ranks every level a pool may carry.
_LEVEL_RANK = {"elem": 0, "ms": 1, "hs": 2, "college": 3, "pro": 4}


def _levelOf(pool):
    """The level prefix of a pool, or None. 'hs_m' -> 'hs'.

    Prefix-stripping rather than split("_"), for the same reason the promotion
    helpers below do it: 'unknown_gender' contains an underscore.
    """
    if not pool:
        return None
    for level in _LEVEL_RANK:
        if pool.startswith(level + "_"):
            return level
    return None


# ------------------------------------------------------------------ #
#  1. PROMOTIONS                                                      #
# ------------------------------------------------------------------ #
#
# Prefix-stripping rather than split("_"), because "unknown_gender" itself
# contains an underscore and splitting would lose it.

# ⚠ NO LONGER CALLED BY resolvePool -- the promotion gates were removed.
#   Kept because speed_ratings re-exports both names for other importers.
def hsPool(pool):
    """Promote a sub-high-school pool to its hs equivalent."""
    for level in _SUB_HS_LEVELS:
        if pool.startswith(level + "_"):
            return "hs_" + pool[len(level) + 1:]
    return None


def collegePool(pool):
    """Promote a school-level pool to its college equivalent."""
    for level in _SCHOOL_LEVELS:
        if pool.startswith(level + "_"):
            return "college_" + pool[len(level) + 1:]
    return None


def proPool(pool):
    """Swap a pool's LEVEL for 'pro', keeping the gender."""
    for level in _PRO_LEVELS:
        if pool.startswith(level + "_"):
            return "pro_" + pool[len(level) + 1:]
    return None


# ------------------------------------------------------------------ #
#  2. THE DECISION                                                    #
# ------------------------------------------------------------------ #

# ★ ADAPTIVE COMPETITION IS NOT COMPARABLE TO THE POOLS, SO IT IS EXCLUDED
#   RATHER THAN RATED. A paralympic athlete's time is a performance in a
#   different event -- classified by impairment, often on different equipment
#   -- and putting it on the same scale as an able-bodied field says something
#   false about both.
#
# ⚠ "para" IS A PREFIX IN ORDINARY PLACE NAMES, so a bare substring match is
#   not safe. Measured against the corpus, `\bpara\b` alone would take
#   Munno Para (an Adelaide suburb) and Para Los Ninos Charter (a Los Angeles
#   school, where "para" is Spanish), while missing nothing. Paramus,
#   Paradise Valley, Paragould, Paramount and Paraclete are all real schools
#   that must survive.
#
#   So "para" only counts when it is part of `paralympic`, joined to `sport`,
#   qualified by a sporting word, prefixed by a nationality, or standing
#   alone as the whole string. That takes ~2,900 rows across ~450 people --
#   US Paralympic Elite, ParaSport Spokane, Texas Regional Para Sport,
#   Michigan GRIT Para Track & Field Club and the rest -- and no school.
_PARA_SCHOOL = re.compile(
    r"\bparalympic"
    r"|\bpara[ -]?sport"
    r"|\bpara\b.*\b(sport|track|athlet|team|develop)"
    r"|\b(u\.?s\.?a?|united states|mexico|jamaica|canada|great britain|gbr)"
    r"\.?\s+para\b"
    r"|^\s*para\s*$",
    re.I)


def isParaSchool(school):
    """True when this school string names adaptive competition."""
    return bool(school) and bool(_PARA_SCHOOL.search(school))


# ★ AN EXPLICIT LIST, NOT A PATTERN, AND THE CORPUS INSISTS ON IT.
#
#   A professional athlete's school string IS the sponsor -- "Asics", "HOKA
#   NAZ Elite", "Puma". Those are the only signal they carry, because a pro
#   has no grade and no school, so the field rule reads whatever meet they
#   entered and answers college. Andrew Hunter tops the College (M) track
#   board off the Millrose Games on exactly that path.
#
# ⚠ BUT THE BRANDS ARE ALSO MASCOTS AND PLACE NAMES. Measured over every
#   distinct school in the corpus, matching these as substrings -- or even on
#   word boundaries -- takes:
#
#       Chino Pumas Track Club, Arizona Puma Track, Plainfield Pumas,
#       Visalia Pumas Track Club          youth clubs, ~10,300 rows
#       Tahoka / Tahoka Middle School /
#       Tahoka Junior High                a Texas town, ~6,500 rows
#       La Crescent-Hokah                 Minnesota
#       Shoka Univ Sr High-Okayama        Japan
#       Basics TC                         contains "asics"
#       Johnston Running Club, Wilton
#       Running, Princeton Running Club   contain "on running"
#
#   Sixteen thousand rows of collateral damage to catch seven thousand. A
#   mascot and a sponsor are the same word and no rule separates them, so the
#   names are enumerated instead. Adding a team later is one line, and a name
#   nobody has seen cannot silently take a school with it.
#
# ! MATCHED ON A NORMALISED FORM -- casefolded, punctuation dropped, runs of
#   space collapsed. That covers ASICS/Asics/asics and HOKA ONE ONE/Hoka One
#   One without sixteen more entries, and without a regex that could reach
#   further than intended.
#   ⚠ AND THE LIST IS SHORT BECAUSE MOST SPONSOR NAMES ARE SHARED. Checked
#     against the corpus, a single school string holds BOTH populations:
#
#       "Puma"    161 people, grades 1 through 12, fastest mark 6.54 s
#                 -- and Alex Botterill running 1:45.6 for 800 m
#       "Asics"    75 people, grades 2 through 12, fastest 6.52 s
#                 -- and Mark English, Ireland's 800 m record holder
#       "Saucony"  61 people, 1:43.9 at meets labelled "Invit Elite"
#       "On Running" 77 people, grades 8 to 12 -- and Kieran Lumb
#
#     A youth club and a professional squad wear the same sponsor, so the
#     string cannot separate them and every one of those names is left out.
#     Sponsored school teams are out for the same reason: "HOKA Aggie Running
#     Club" and "Asics Aggies" carry grades 9 to 12.
#
#     What survives is the four HOKA elite squads, which showed no school
#     grades, no sprint times, and 13 to 38 people each -- the size a
#     professional group actually is.
_PRO_TEAMS = frozenset("""
    hoka
    hoka one one
    hoka naz elite
    hoka njnytc
""".split("\n"))
_PRO_TEAMS = frozenset(x.strip() for x in _PRO_TEAMS if x.strip())

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


# ! TWO REGEX SUBSTITUTIONS PER CALL, AND resolvePool CALLS IT ON EVERY ROW.
#   Over 61.6M rows that is 123M regex passes to answer a question with a few
#   hundred thousand distinct answers. Pure, so caching changes nothing but
#   the clock.
@lru_cache(maxsize=1 << 18)
def _normSchool(school):
    """Casefold, drop punctuation, collapse spaces. 'HOKA ONE ONE' -> the
    same key as 'Hoka One One'."""
    return _SPACE.sub(" ", _PUNCT.sub("", (school or "").lower())).strip()


# ★ AND A HANDFUL OF NAMED INDIVIDUALS, BECAUSE THE SCHOOL CANNOT SAY IT.
#
#   A professional under a shared sponsor name is invisible to the list
#   above: Mark English's rows read "Asics", which is also a youth club. His
#   grade is "-", he has no school, and the field rule reads whatever
#   invitational he entered and answers college. Nothing in his data
#   identifies him except who he is.
#
# ⚠ THIS IS A LAST RESORT AND IT SHOULD STAY SMALL. A per-person list does
#   not generalise and nobody maintains it. It is here because the general
#   signal -- meet or division naming -- does not separate professional
#   fields from ordinary invitationals in this corpus, and one wrong athlete
#   at the top of a board is worth one line until it does.
# ★ HAND-LISTED PROFESSIONALS, AND NOW PER SEASON RATHER THAN PER PERSON.
#
#   This was a frozenset of person_ids, which applied to EVERY race that
#   person ever ran, in both sports, for all time. For Fouad Messaoudi or
#   David Mullarkey that is harmless -- they have no school seasons in this
#   corpus at all -- but Andrew Hunter had a real high school career, and a
#   person-wide flag erases it.
#
# ⚠ THE YEARS ARE SEASON LABELS, THE WAY A PERSON SAYS THEM. A track season
#   opens in December and is NAMED for the year it closes in, so "2026 TF" is
#   the Dec 2025 - Jul 2026 campaign, which season_year STORES as 2025.
#   proSeasons() does that conversion once; every caller passes the stored
#   season and nobody else has to think about it.
#
#   (first_label, last_label, sport) -- None anywhere means "no bound".
_PRO_SEASONS = {
    # ⚠ EVERY ONE OF THESE DEFEATED A GENERAL RULE, WHICH IS WHY THEY ARE
    #   HERE RATHER THAN IN ONE.
    32703729: (None, None, None),
    # Guillaume Tremblay, Université Laval Rouge et Or. His feed writes grade
    # 5, 6, 7 across 2024-26 -- corroborated, cleanly progressing, and
    # describing a Québec programme year rather than a US grade. He runs 1:54
    # for 800 m. Nothing in the verdict machinery can see that a middle
    # schooler cannot. Every season, both sports.

    26054637: (2017, None, None),
    # Andrew Hunter, races as "Asics" -- a string shared with a youth club of
    # 161 people whose fastest mark is 6.54 s, so the school cannot be used.
    # ! FROM 2017 ONLY. He ran high school before that, and the old
    #   person-wide flag was rating those seasons against professionals.

    29751385: (2026, 2026, "TF"),
    # Fouad Messaoudi, "Morocco" -- the 2026 track season and nothing else.

    32542330: (2025, 2026, None),
    # David Mullarkey, "Great Britain & N.I."
    # National-team entries. A country as the school is a strong signal and
    # would need a country list to use generally.

    32628652: (2021, None, None),
    26468447: (2019, None, None),
    32606806: (2025, None, None),
    30052170: (2026, None, None),
    27196114: (2023, None, None),
}


def _label(sport, season):
    """Stored season year -> the label a person uses. See season_year."""
    return season + 1 if sport == "TF" and season is not None else season


def isProPerson(person_id, sport=None, season=None):
    """Is this athlete-season one of the hand-listed professional ones?

    `season` is the STORED season year (August-July, named for the year it
    opens in) -- what season_year.seasonYearFor returns.

    ⚠ AN UNKNOWN SEASON IS TREATED AS INSIDE THE SPAN, which is the old
      behaviour and is deliberate: a caller that cannot say which season a row
      belongs to should not silently start rating a professional against high
      schoolers. Every caller in this repo passes one; this is the floor under
      the ones that do not.
    """
    if person_id is None:
        return False
    spec = _PRO_SEASONS.get(int(person_id))
    if spec is None:
        return False
    first, last, only_sport = spec
    if only_sport and sport and sport.split("|")[0] != only_sport:
        return False
    if season is None:
        return True
    label = _label(sport, season)
    if first is not None and label < first:
        return False
    if last is not None and label > last:
        return False
    return True


def isProTeam(school):
    """True when this school string names a professional team.

    ⚠ EXACT MATCH ON THE WHOLE NAME, NOT A SUBSTRING. "Chino Pumas Track
      Club" normalises to itself and is not in the set; "PUMA" normalises to
      "puma" and is.
    """
    return _normSchool(school) in _PRO_TEAMS


# ★ THE TEAM'S LEVEL, FROM THE FEEDS (the pooling redo, 2026-09-14). anet
#   names every team's level and tfrrs's slug carries one
#   ("CT_college_f_Conn_College"). The engine used to know only the
#   school STRING, so a club, an elite squad or a national team racing in
#   a college field for a whole season -- Nike Swoosh TC at The TEN, ASICS
#   Furman Elite at Sir Walter, "Great Britain & N.I." -- was a college
#   season by the field rule, and headed the college board.
#     'club'     a gradeless row on a club is a professional (repooled pro,
#                as pro_flag would); a club runner WITH a school grade is
#                that grade's (youth clubs carry grade 10s)
#     'college'  a college team's row is a college season, as the field
#                rule already says when the field is college
#   Other levels change nothing: the grade rules already hold them.
def teamLevelFromSlug(slug):
    """'CT_college_f_Conn_College' -> 'college'; None when the slug does
    not name a level this code knows."""
    if not slug:
        return None
    parts = str(slug).split("_")
    if len(parts) >= 3 and parts[1].lower() in ("college", "hs", "ms", "club"):
        return parts[1].lower()
    return None


# ★ AND team_id = 0 IS NOT A SCHOOL (owner, 2026-09-16: "If any school has
#   id == 0 we should just put them in pro"). anet writes 0 where there is
#   no team -- unattached entries, open-meet walk-ups -- so the string in
#   `school` is whatever the athlete typed, and the school-name map is being
#   asked to level a name that names nothing.
#
# ★ AND IT IS UNCONDITIONAL (owner, 2026-09-16, asked whether to gate it on
#   the season majority like the club rules: "unconditional -- these
#   atheltes don't matter enough for me to let them corrupt boards"). So it
#   does NOT go through team_level='club', which would hand it to the
#   majority gate and the grade guards. resolvePool takes it as `no_team`,
#   decided before anything else: a row with no team is not a school row,
#   whatever its grade, its verdict or the field it raced says.
#
# ⚠ THE COST, STATED. A high schooler's one unattached summer race is a
#   professional row, and their season is split across two pools. That is
#   the trade the owner chose over the same row reaching a school board.
#   scripts/diag_school_collisions.py --zero prices it before the go-live:
#   if team_id = 0 turns out to be a sentinel on ordinary school rows
#   rather than "unattached", this rule has to go.
UNATTACHED_TEAM_ID = 0


def teamLevelOf(team_id, team_slug, anet_levels=None):
    """The team's level name from anet's table (through anet_levels,
    speed_ratings_db.loadTeamLevels) or the tfrrs slug, else None.
    team_id 0 is anet's "no team" and reads as 'club' -- see above."""
    if team_id is not None:
        try:
            tid = int(team_id)
        except (TypeError, ValueError):
            tid = None
        if tid == UNATTACHED_TEAM_ID:
            # ! A ZERO NAMES NO LEVEL. It is not read as 'club' here: the
            #   pro repool is resolvePool's `no_team`, which is
            #   UNCONDITIONAL (owner, 2026-09-16) and the club path would
            #   weaken it. A tfrrs slug still names what it names; a tfrrs
            #   row carries no anet team_id at all, so in practice only
            #   anet reaches this branch.
            return teamLevelFromSlug(team_slug)
        if anet_levels and tid is not None:
            got = anet_levels.get(tid)
            if got:
                return got
    return teamLevelFromSlug(team_slug)


# ★ A PROFESSIONAL WHOSE SCHOOL STRING CANNOT BE LEVELLED IS STILL A
#   PROFESSIONAL (2026-09-16). Stage 2 repools a pro by SWAPPING the level
#   of the pool the grade or the school produced -- so when neither can
#   produce one, there was nothing to swap and the row was dropped. That
#   silently undid the rule for exactly the rows it matters most for: an
#   unattached entry (no_team) whose `school` is whatever the athlete
#   typed, and the elite squads in _PRO_TEAMS, whose names are in no school
#   level map because they are not schools.
#
# ! ONLY WITH A KNOWN SEX. 'pro_unknown_gender' is not a pool anything can
#   rate against, so a row with no sex still drops -- one junk pool is not
#   better than one missing row.
def _proPoolFor(gender):
    """'pro_m' / 'pro_f' from the sex alone, or None. The same spelling
    proPool produces, so the two can never disagree."""
    g = str(gender or "").strip().upper()
    if g == "M":
        return "pro_m"
    if g == "F":
        return "pro_f"
    return None


def _levelFromVerdict(level):
    """A level name this module ranks, or None. Anything it does not rank --
    None, '', 'club', a typo -- is 'no verdict', never a silent default."""
    if level is None:
        return None
    lv = str(level).strip().lower()
    return lv if lv in _LEVEL_RANK else None


def _gradeLevel(grade):
    """'10' -> 'hs', '3' -> 'elem', 'JR-3' -> whatever the parser says,
    None when unreadable. The same parser poolFor uses."""
    try:
        import normalize_distance as nd
        return nd.GRADE_TO_LEVEL.get(nd.normalizeGrade(grade))
    except Exception:                                    # noqa: BLE001
        return None


def resolvePool(grade, gender, source, school, sport,
                season_level=None, grade_untrusted=False, is_pro=False,
                season=None,
                college_first=None, upperclass_first=None,
                race_date=None, merge=False, poolfor=poolFor,
                fixed_grade=None, fixed_level=None,
                grade_verdict=None, person_id=None, team_level=None,
                team_has_pros=False, no_team=False, team_pro=False,
                race_top_level=None, pro_ability=None):
    """Which pool does this row belong to? Returns "hs_m|XC", or None.

    team_level: the team's level from the feeds (teamLevelOf): 'club'
    makes a gradeless row professional, 'college' makes the row a college
    season, a school level decides where the school NAME would have (see
    below); anything else changes nothing. team_has_pros: the team has a
    professional in it (speed_ratings_db.loadClubPros) and the season is
    raced mostly for it -- then EVERY row of it is professional, whatever
    grade it carries (owner, 2026-09-16), unless the team is a college.
    no_team: anet wrote team_id = 0, so there is no school at all;
    professional, unconditionally.

    `poolfor` is injectable so the engine can hand in a memoised poolFor. It
    defaults to the real one, so a caller that does not care never notices.

    ★ WHAT DECIDES A POOL, EXHAUSTIVELY: pro_flag; a grade that stopped
      advancing; a corroborated grade; the level of the field raced. The
      first arrives as is_pro, the other three as grade_fix, and every one
      of them is per athlete-season on the academic year. There is no fifth
      input.

    STAGE 1 -- THE BASE POOL.
      An untrusted grade is dropped by passing grade=None, which makes
      poolFor's getPool return unknown_level so levelForSchool decides instead.
      The season verdict goes in either way; it is what enables poolFor's
      step 0, and without it poolFor is -- by its own docstring -- "byte for
      byte the old one" from before athlete_season_level existed.

    STAGE 2 -- THE GATES, strongest first.
      pro wins outright: a professional season has left the school pools
      entirely, and pro outranks college. Only when NOT pro are the promotions
      considered, and college is tested before upperclass with an elif, so an
      athlete cannot be promoted twice.

    ⚠ COLLEGE AND UPPERCLASS ARE COMPARED BY DATE, NOT BY YEAR. A senior's
      spring high-school track season and their first autumn of college share
      a calendar year, and the engine treats the calendar year as the season
      for both sports. Comparing years promoted 288,264 genuine high-school
      athlete-seasons -- 42% of all promotions. The date puts spring before the
      boundary and autumn after it. Do not "simplify" this to a year.
    """
    # -- stage 0: THE RESOLVED GRADE, WHERE THERE IS ONE ------------
    #
    # ★ A FIX IS AN ANSWER, NOT A DOUBT, AND THIS IS THE WHOLE POINT OF IT.
    #   grade_sanity works out what the grade SHOULD be:
    #
    #     majority   the athlete's own races disagreed and one value won, so
    #                the correct grade is known and is used in place of the
    #                recorded one.
    #     field      too few races to have an internal majority, so the level
    #                of the races they ran decides. A level, not a grade: a
    #                middle school race holds grades 6, 7 and 8, so the field
    #                can only say which group, never which year.
    #     no_grades  nobody in any of their races carried a grade, so it was
    #                not a school race and the season is pro.
    #
    # ⚠ WITHOUT THIS, grade_untrusted ALONE MAKES THE MAJORITY CASE WORSE.
    #   The flag drops the grade entirely, so an athlete whose nine races read
    #   9,9,9,9,11,9,9,9,9 would lose a grade that is right eight times out of
    #   nine and fall through to whatever the school says. Distrusting a value
    #   you have already corrected is throwing away the correction.
    # ★ AN EXPLICIT "NO EVIDENCE" VERDICT MEANS NO POOL, NOT A FALLBACK.
    #
    #   grade_sanity now writes a row for EVERY athlete-season it examined,
    #   including the ones it could establish nothing about. Reaching here
    #   with method='no_evidence' is that row: the rules looked and found
    #   nothing, which is a decision rather than a gap.
    #
    #   Without this the row falls through to the raw grade -- also nothing --
    #   and then to the SCHOOL NAME. Measured: Alexa Hernandez, one race,
    #   grade '-', pooled ms_f purely because "Stockton Rising Club" resolves
    #   that way, and third on the national middle school girls board at
    #   159.8.
    #
    # ⚠ ONLY THE EXPLICIT VERDICT DOES THIS. A season with NO row at all --
    #   an athlete grade_sanity has not seen because it has not been re-run --
    #   still falls through as before. Absence stays backward-compatible;
    #   only a stated "we do not know" is decisive.
    # ! THREE VERDICTS MEAN "NO POOL", NOT ONE.
    #     no_evidence   the rules looked and found nothing
    #     contradicted  the season held numbers AND words, and the words won
    #                   -- so the two feeds disagree about whether this
    #                   athlete is in school, and the tiebreak was which feed
    #                   scraped more races
    #     thin_field    the verdict came from a field that has since been
    #                   thrown away as mostly self-contradictory
    #
    #   All three are grade_sanity saying it does not know. Falling through
    #   would hand the row to the raw grade and then to the school name --
    #   the weakest signals in the system, and the ones these verdicts exist
    #   to refuse.
    # ! BEFORE EVERYTHING. A para row has no pool regardless of what its
    #   grade, school or season verdict says -- the exclusion is about the
    #   event, not about the athlete's level.
    if isParaSchool(school):
        return None

    # ! NO TEAM, NO SCHOOL. Before every other rule and ungated: see
    #   UNATTACHED_TEAM_ID for why it is not the club path.
    #
    # ★★ BUT THE RACE ANSWERS IT FIRST (owner, 2026-09-20: "if a runner is
    #    unattached they should resolve to the highest pool in the race
    #    they're running in").
    #
    #    "No team" is a fact about the ENTRY, not about the athlete's level,
    #    and calling it professional was inferring the second from the first.
    #    A runner with no vest in a high school race is a high schooler. The
    #    race is the only evidence an unattached row carries, and
    #    race_top_level (level_graph.raceTopLevels) is that evidence: the
    #    highest level anybody in that race is racing at, over races whose
    #    teams are >= 90% known.
    #
    # ! THE CEILING, NOT THE MODE, AND THAT IS THE OWNER'S WORD. race_level --
    #   the artifact season_level reads -- requires UNANIMITY and so decides
    #   nothing about a mixed open section at a college meet. This rule wants
    #   exactly that race to resolve, and to resolve upward.
    #
    # ! PRO IS STILL THE FALLBACK, not the default. No verdict for the race
    #   (too few known teams, or level_graph has not run) leaves the old
    #   behaviour exactly as it was, so this can never be half-applied.
    #
    # ! AND A STATED CEILING OF 'pro' IS STILL 'pro'. An open race full of
    #   professionals resolves an unattached entry to pro through the same
    #   rule, rather than by assumption -- which is the difference.
    if no_team:
        top = _levelFromVerdict(race_top_level)
        if top is None:
            is_pro = True
        elif top == "pro":
            is_pro = True
        else:
            # The race's ceiling IS the level. It outranks the school name
            # (there is none) and the stale grade a scraper copied forward,
            # for the same reason season_level exists.
            fixed_level = top
            season_level = top

    # ★ AND THE TEAM ITSELF CAN BE PROFESSIONAL, BY TEAM ID (2026-09-19).
    #   team_pro is engine/build_team_pool.py's adjudicated verdict for this
    #   row's anet team_id, read from the team_pool table -- most of them for
    #   the rule the owner was emphatic about: fewer than fifteen distinct
    #   athletes ALL TIME ("no not 15 per year 15 over all time") is not a
    #   school, whatever its name looks like.
    #
    # ! IT IS A VERDICT ARRIVING, NOT A DECISION BEING MADE. This function
    #   stays pure and receives the fact, exactly as it receives season_level
    #   and is_pro; re-deriving the threshold here would be the second
    #   implementation of one decision, which is the failure this module's
    #   own header exists to record.
    #
    # ! UNGATED, LIKE no_team AND FOR THE SAME REASON. The club rules run
    #   through clubSeason's majority gate because one national-team race
    #   should not repool a school season. A team with fewer than fifteen
    #   athletes in its entire history is not a school that an athlete had a
    #   season at, so there is no majority to take.
    if team_pro:
        is_pro = True

    # ! A PRO IS REPOOLED, NOT DROPPED -- the same treatment pro_flag gives a
    #   flagged season. They stop dragging the school pools' 100 point and
    #   get measured against each other instead of being thrown away.
    # ! person_id IS OPTIONAL, so a caller that does not pass it simply gets
    #   the school-based test. Adding a required argument would break three
    #   call sites for a set that is currently empty.
    # ! THE SEASON DECIDES NOW, NOT JUST THE PERSON. See _PRO_SEASONS: a
    #   hand-listed professional is professional for the seasons they were
    #   professional in, and a high school career before that stays a high
    #   school career.
    if isProTeam(school) or isProPerson(person_id, sport, season):
        is_pro = True
    # ★ THE FEED'S OWN WORD ON THE TEAM (team_level, above): a gradeless row
    #   on a club is a professional; a college team's row is college
    school_grade = _gradeLevel(fixed_grade if fixed_grade is not None else grade)
    if team_level == "club" and (school_grade is None or grade_untrusted) \
            and fixed_level not in ("hs", "ms", "elem"):
        is_pro = True
    # ★ A CLUB WITH PROFESSIONALS HAS NO SCHOOLCHILDREN AT ALL (owner,
    #   2026-09-14: its grade 1-8 is a year count, its gradeless row a
    #   professional; then 2026-09-16, asked whether a grade 9-12 row on
    #   such a team should be swept in too -- "DO do this, only when they
    #   run a majority of races at that club").
    #
    # ! THE MAJORITY IS THE ONLY GATE, AND IT IS NOT HERE. team_has_pros
    #   arrives False unless the athlete-year raced mostly for the team
    #   (speed_ratings.clubSeason, loadClubMajority), so by the time it is
    #   True the season has been established as the club's -- and a grade
    #   on it is a year count rather than a school year. The guards that
    #   used to stand here (a grade of 9-12 kept, a grade_fix level of 'hs'
    #   kept) were a second opinion about that same season, and they are
    #   what left an elite squad's "11" on the high school boards.
    #
    # ⚠ A SPONSOR'S YOUTH SQUAD IS THE COST. "HOKA Aggie Running Club" and
    #   "Asics Aggies" carry grades 9-12 (see _PRO_TEAMS), so a real high
    #   schooler racing mostly for such a club now pools pro. The owner
    #   priced that against the boards and chose this.
    # ⚠⚠⚠ AND A SCHOOL IS NOT A CLUB HERE EITHER (owner, 2026-09-22: "there
    #     are way too many ppl getting put in pro due to their schools, when
    #     their schools are hs in anet").
    #
    #     This is the SECOND implementation of the rule fixed in
    #     build_team_pool.classify on 2026-09-20. That fix made team_pool
    #     stop calling a school pro for having produced a professional --
    #     and team_pool feeds the `team_pro` argument above. But this line
    #     is a different route to the same verdict, from loadClubPros by way
    #     of clubSeason's majority gate, and it tested only `!= "college"`.
    #     So an anet high school with one pro-flagged athlete-season still
    #     swept every athlete who raced mostly for it into the pro pool,
    #     through a door I had not noticed was there.
    #
    #     That is exactly the failure this module's own header exists to
    #     record -- "There were three implementations ... they disagreed on
    #     about a million athlete-seasons" -- committed again by me, one
    #     file away from the note about it.
    #
    # ! THE EXCLUSION IS THE SCHOOL LEVELS, NOT just college. pro_flag
    #   classifies a SEASON on purpose ("Lutkenhaus raced Millrose as a
    #   junior"), so a senior flagged pro is a pro-flagged season sitting on
    #   their high school's team id, and the whole roster followed them out.
    # ! A CLUB STILL SWEEPS, which keeps the case the note below prices: a
    #   sponsor's youth squad carries grades 9-12 and is a club, so
    #   "HOKA Aggie Running Club" behaves exactly as the owner chose.
    if team_has_pros and team_level not in ("college", "hs", "ms", "elem") \
            and not is_pro:
        is_pro = True
    elif team_level == "college" and not is_pro and school_grade in (None, "hs", "college"):
        fixed_level = fixed_level or "college"
        season_level = season_level or "college"

    # ★ AND A SCHOOL LEVEL FROM THE FEED OUTRANKS THE SCHOOL'S NAME (owner,
    #   2026-09-16: "we need to fix schools coming together ... use the
    #   school locations from anet and the school ids/names to separate
    #   schools ... currently Oregon(IL) and Oregon(or) are colliding
    #   despite hs vs college, and this happens to Williams (CA) vs (MA)").
    #
    # ⚠ THE COLLISION IS IN THE KEY, AND THE KEY IS A NAME. When the grade
    #   cannot answer, poolFor falls through to levelForSchool -- a lookup
    #   in school_level_graph and school_levels.pkl, both keyed on the
    #   NORMALISED NAME and nothing else. So one string is one level for
    #   everybody wearing it:
    #
    #       "Oregon"    Oregon High School, Ogle County IL   <- hs
    #                   University of Oregon                 <- college
    #       "Williams"  Williams High School, CA             <- hs
    #                   Williams College, MA                 <- college
    #
    #   Whichever level the map holds, the other school's gradeless rows
    #   are pooled on it -- an Illinois tenth grader rated against the
    #   college mean, or a Williams College runner against high schoolers.
    #
    # ★ BUT THE ROW ALREADY CARRIES AN IDENTITY THE NAME DOES NOT: anet's
    #   team_id (a different id per school, with its own level, state and
    #   city in anet_team) and tfrrs's slug (whose first token is the state
    #   and second the level). team_level is that, resolved. Using it is
    #   the whole fix for the pooling half: two schools with one name have
    #   two team ids, so they get two levels.
    #
    # ! ONLY WHERE THE NAME MAP WOULD HAVE DECIDED -- an unreadable or
    #   untrusted grade, and no grade_fix verdict. A trusted grade still
    #   decides alone (stage 1), and grade_sanity's own per-season verdict
    #   still outranks a per-row team level.
    #
    # ! AND IT IS WHY THE VERDICT KILL BELOW DOES NOT FIRE EITHER. That
    #   kill exists because falling through would hand the row to the raw
    #   grade and then to the school NAME -- "the weakest signals in the
    #   system". A level the feed states for the team is not that.
    elif team_level in ("hs", "ms", "elem") and not is_pro \
            and fixed_level is None and fixed_grade is None \
            and (school_grade is None or grade_untrusted):
        fixed_level = team_level
        season_level = team_level

    # ★ A SEASON RACED AT COLLEGE OR PRO FIELDS IS A COLLEGE OR PRO SEASON,
    #   WHATEVER THE GRADE SAYS (owner, 2026-09-06: "if a person is on a
    #   college team, pool them as college"). season_level is the unanimous
    #   level of every race the athlete ran that year (season_level.py; only
    #   unanimous verdicts are loaded). Nobody spends a whole season in
    #   college fields as a high schooler, so above hs the field outranks
    #   the grade -- an Amherst College runner's "SO-2" read as grade 10
    #   against a name the level map calls a Wisconsin high school, and
    #   his "JR-3" season was 'contradicted' and unrated. Below college
    #   nothing changes: the ms/hs call stays the grade's (Luke Morelli,
    #   stage 1 below), because eighth graders do race varsity.
    field = fixed_level or season_level
    above_hs = field in ("college", "pro")
    if field == "pro":
        is_pro = True                     # repooled below, as pro_flag would

    # ================================================================== #
    #  THE ABILITY GATE -- the last word on is_pro, over all SIX routes
    # ================================================================== #
    #
    # ★ OWNER, 2026-09-22: "if they're sub 14:00? for men, or sub 15:30?
    #   for women put in pro, otherwise trust grade."
    #
    # ⚠ WHY A GATE AND NOT A SIXTH FIX. The pro pool's average member runs
    #   a 25:53 5K-equivalent -- C(pro_m) = 1553.1 against C(hs_m) =
    #   1211.5. It is not lightly contaminated, it is DOMINATED by people
    #   who are not professionals, and every one of them drags the pool's
    #   100-anchor, which bends the rating of every real professional in
    #   it. Three separate doors to is_pro have been found wrong in three
    #   days (build_team_pool twice, team_has_pros once). This sits
    #   DOWNSTREAM of all of them and of whatever the next one turns out
    #   to be.
    #
    # ★ IT IS NECESSARY, NEVER SUFFICIENT. Being fast does not make anyone
    #   professional -- that would pool every good high schooler pro,
    #   which is the opposite of what the owner asked for ("their season
    #   should still be hs"). This can only ever take is_pro away.
    #
    # ! TRI-STATE, AND None IS INERT. True/False are verdicts; None means
    #   no verdict and behaves EXACTLY as before this existed -- the same
    #   posture race_top_level takes. A caller that does not pass it, a
    #   season with no rated mark, a database that has never built the
    #   table: all unchanged. That is what makes this safe to ship before
    #   every caller is wired.
    #
    # ! WHAT HAPPENS TO THE REFUSED (owner's call, 2026-09-22). They are
    #   not moved to another pool, they fall through to the ordinary
    #   ladder -- "otherwise trust grade". Where the grade, the school
    #   level, the season verdict and the race ceiling are ALL absent,
    #   stage 1 below returns None and the row goes unrated. That is the
    #   gradeless unattached adult running a 40:00 10k, and dropping them
    #   is the mechanism by which the pool actually gets clean.
    if pro_ability is False:
        is_pro = False
    # ! A LEVEL THE FEED STATES FOR THE TEAM COUNTS AS EVIDENCE. The three
    #   verdicts mean grade_sanity does not know the GRADE; the kill is
    #   there because the fallback would be the school name. When anet or
    #   the tfrrs slug names the team's own level there is no guess to
    #   refuse -- and an athlete-season whose every race was killed this
    #   way is the owner's report of 2026-09-16 ("a lot of them have all
    #   their races killed bcs their grade is untrusted").
    # ! AND IT MAY NOT DROP A PROFESSIONAL (ISSUES M, 2026-09-16). The kill
    #   ran before the pro repool, so a row already established as
    #   professional -- no team at all, or a season raced mostly for a club
    #   with professionals in it -- was dropped rather than pooled pro,
    #   purely because grade_sanity could not name a GRADE for it. Of course
    #   it could not: there is no grade to name. Both rules the owner
    #   approved on 2026-09-16 land in exactly this case, so is_pro is
    #   decisive here as it is everywhere else.
    told = team_level in ("elem", "ms", "hs", "college")
    if grade_verdict in ("no_evidence", "contradicted", "thin_field",
                         "lone_word") and not above_hs and not told \
            and not is_pro:
        return None

    if fixed_grade is not None:
        grade = fixed_grade
        grade_untrusted = False           # it is trustworthy now: it was fixed
    if fixed_level is not None:
        # The field's verdict IS the season verdict for this athlete-season,
        # and it is more specific than the sport-wide one, so it wins.
        season_level = fixed_level

    # -- stage 1 ----------------------------------------------------
    #
    # ! A TRUSTED GRADE DECIDES ALONE. THE SCHOOL DOES NOT GET A VOTE.
    #
    #   season_level is passed ONLY when the grade cannot answer. That is the
    #   whole design: the grade decides, and the field decides when the grade
    #   is missing or unusable. Handing arbitrateLevel both meant the level
    #   could override a grade that was known good, which is how Luke Morelli
    #   sat on the high school board as a grade 8 with EIGHT races all reading
    #   8. Foundation Academy is K-12, so its race levels are 'hs', and that
    #   promoted every middle schooler who ran the school's own meets.
    #
    #   The one-step ms -> hs promotion was defended as "eighth graders race
    #   varsity", and they do -- but the pool is meant to say what YEAR an
    #   athlete is in, not which race they entered. A grade 8 varsity runner
    #   is still a grade 8.
    # ! ONE STEP ONLY. A third grader's Junior Olympic season carries the
    #   college bit (arbitrateLevel's guard 1: 1,315 of them were pooled
    #   college once); the field outranks a HIGH SCHOOL grade or no grade,
    #   never an elementary or middle school one.
    college_season = (field == "college"
                      and _gradeLevel(grade) in (None, "hs", "college"))
    season_for_pool = None if (grade is not None and not grade_untrusted
                               and not college_season) else season_level
    if college_season and grade is not None and not grade_untrusted:
        grade_untrusted = True            # the field decides; see above
    pool = poolfor(None if grade_untrusted else grade,
                   gender, source, school, season_level=season_for_pool)
    if pool is None:
        # see _proPoolFor: there is nothing for stage 2 to swap, and a
        # professional is not a row we have failed to level
        pool = _proPoolFor(gender) if is_pro else None
        if pool is None:
            return None

    # -- stage 1b: UNTRUSTING MAY NOT PROMOTE -----------------------
    #
    # ★ MEASURED, AND IT IS THE WHOLE REASON THIS EXISTS. Untrusting a grade
    #   makes the race LEVEL decide instead. Across athlete-seasons with two
    #   races or fewer that changes the pool for ~108k of them -- and the
    #   change is overwhelmingly UPWARD: 95,455 promotions against 13,000
    #   demotions, 79,147 of them ms -> hs alone.
    #
    #   The bias is structural, not noise. Young athletes race in meets whose
    #   overall level is higher than their own: K-12 schools where the middle
    #   schoolers run the school's meets, open meets, club invitationals. So
    #   "fall back to what the race was" almost always promotes.
    #
    #   Foundation Academy is the case. It is K-12, every season verdict is
    #   'hs', and Michael Leiferman reads grade 6,7,8,9 across four years --
    #   his grade is RIGHT. Letting the level win puts a sixth grader on the
    #   high school board at a middle schooler's rating, which is the exact
    #   thing untrusting was meant to prevent.
    #
    # ⚠ AN UNRELIABLE GRADE IS A REASON TO STOP TRUSTING IT, NOT A REASON TO
    #   MOVE THE ATHLETE UP. The fallback may hold the level or lower it,
    #   never raise it.
    #
    #   Amelio Chio still works: he has NO season verdict, so poolFor falls
    #   through to the school, "SoCal Elite Track and Field Club" resolves to
    #   nothing, and he drops out. Dropping out is not a promotion, so this
    #   does not block it.
    #
    #   Only fires when the grade was untrusted. A TRUSTED grade losing to a
    #   season verdict is arbitrateLevel's business and has its own guards.
    # ⚠ NOT WHEN A FIX SUPPLIED THE ANSWER. The guard is for a grade falling
    #   back to the SCHOOL, where the fallback is a guess and the bias is
    #   upward: 95,455 promotions against 13,000 demotions, 79,147 of them
    #   ms -> hs, because young athletes race in meets whose overall level is
    #   higher than their own.
    #
    #   A resolved level is not that. "Nobody in any of their races carried a
    #   grade, so this is pro" is a promotion by construction, and blocking it
    #   would leave the athlete in the middle school pool the fix exists to
    #   remove them from. Evidence outranks a safeguard against guessing.
    if (grade_untrusted and grade is not None and not college_season
            and fixed_grade is None and fixed_level is None):
        trusted = poolfor(grade, gender, source, school, season_level=None)
        was = _LEVEL_RANK.get(_levelOf(trusted))
        now = _LEVEL_RANK.get(_levelOf(pool))
        if was is not None and now is not None and now > was:
            pool = trusted

    # -- stage 2 ----------------------------------------------------
    if is_pro:
        swapped = proPool(pool)
        if swapped:
            pool = swapped
    else:
        # ============================================================ #
        #  THE PROMOTION GATES ARE GONE. DO NOT PUT THEM BACK.
        #
        #  ★ A POOL IS DECIDED BY FOUR THINGS AND NOTHING ELSE:
        #        pro_flag                      -> is_pro, above
        #        a grade that never advanced   -> grade_fix level 'pro'
        #        a corroborated grade          -> grade_fix grade
        #        the level of the field raced  -> grade_fix level
        #    All four arrive through stage 0, all four are per athlete-season,
        #    and all four are keyed on the academic year. Anything else that
        #    pools an athlete is a fifth opinion competing with those, on a
        #    different clock and a different grain.
        #
        #  ⚠ WHAT WAS HERE, AND WHAT IT COST.
        #
        #    upperclass_first_season promoted ms/elem -> hs from ONE date per
        #    PERSON, seeded by a SINGLE qualifying race and two grade
        #    spellings, covering 5,854,481 athletes.
        #
        #      Person 23950279 is corroborated grade 6, 7, 8 across three
        #      academic years -- a middle schooler, unambiguously. The gate
        #      seeded 2024-09-08, so 2024 and 2025 were promoted to hs_m. His
        #      ability (~675) is a 3200-anchored number and hs_m's pool_mean
        #      (~1240) is a 5000-anchored one, so rating = 100 * mean /
        #      ability came out at 187 instead of ~111. A 2:10 800m at the
        #      top of the high school board.
        #
        #    college_first_season did the same in the other direction:
        #    27072849, a grade 8 girl, ran two races under school "Louisiana"
        #    -- a STATE team name that resolves to a university -- which was
        #    exactly min_races, seeded 2026-05-22, and split her season.
        #    30580279 seeded from "Emmanuel (Ga.), FR-1" rows and then topped
        #    the College (M) board running Ohio junior-high 800s.
        #
        #  ★ AND THE RATCHET IS WHY A ROW-LEVEL VETO WAS NOT ENOUGH. Both
        #    gates record ONE date per person and promote everything after it
        #    for good. A veto makes that survivable; it does not make it
        #    right. With per-pool anchors a wrong pool is no longer a wrong
        #    label -- it divides a 3200-scale ability into a 5000-scale mean,
        #    and the arithmetic is incoherent rather than merely off.
        #
        #  ! THE ARGUMENTS STAY IN THE SIGNATURE, IGNORED. speed_ratings,
        #    panels and build_ranking_results all still pass them. Changing
        #    four call sites to drop an argument is four chances to change
        #    three. They are accepted and discarded here, in one place,
        #    visibly.
        _ = (college_first, upperclass_first, race_date)
        # ============================================================ #

    # merge=True drops the sport from the athlete key, so one person's XC and
    # TF results share a SINGLE ability unknown. Venues stay sport-namespaced
    # in packResults, so difficulty still separates the two.
    return pool if merge else f"{pool}|{sport}"


# ------------------------------------------------------------------ #
#  3. A MEMOISED poolFor, FOR CALLERS DOING MILLIONS OF ROWS          #
# ------------------------------------------------------------------ #

def memoPoolFor(cache):
    """poolFor, memoised into `cache`, keyed on everything it reads.

    ★ season_level IS PART OF THE KEY. The engine's old cache was keyed on
      (grade, gender, source, school) only, and therefore had to be BYPASSED
      whenever a season verdict applied -- which, with 29M season levels
      loaded, is most rows. Putting the verdict in the key means the cache
      works on those rows too, and it cannot leak one athlete's verdict onto
      another row sharing the other four fields. The level takes about five
      distinct values, so the key space barely grows.

    Person-specific facts (untrusted, pro, college, upperclass) are NOT in the
    key and must never be: they are handled outside poolFor, in resolvePool's
    stage 2, precisely so they cannot be cached.
    """
    def cached(grade, gender, source, school, season_level=None):
        key = (grade, gender, source, school, season_level)
        hit = cache.get(key, False)
        if hit is False:
            hit = poolFor(grade, gender, source, school,
                          season_level=season_level)
            cache[key] = hit
        return hit
    return cached