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

  The five facts and where the site gets them:
      season_level      athlete_season_level      (person_id, ay)
      grade_untrusted   grade_untrusted           (person_id, season)
      is_pro            pro_athlete_season        (person_id, season)
      college_first     college_first_season      (person_id) -> date
      upperclass_first  upperclass_first_season   (person_id) -> date
"""

import re

from normalize_distance import poolFor

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
_PRO_PEOPLE = frozenset({
    # ⚠ EVERY ONE OF THESE DEFEATED A GENERAL RULE, WHICH IS WHY THEY ARE
    #   HERE RATHER THAN IN ONE.
    32703729,   # Guillaume Tremblay, Université Laval Rouge et Or. His feed
                # writes grade 5, 6, 7 across 2024-26 -- corroborated, cleanly
                # progressing, and describing a Québec programme year rather
                # than a US grade. He runs 1:54 for 800 m. Nothing in the
                # verdict machinery can see that a middle schooler cannot.
    26054637,   # Andrew Hunter, races as "Asics" -- a string shared with a
                # youth club of 161 people whose fastest mark is 6.54 s, so
                # the school cannot be used.
    29751385,   # Fouad Messaoudi, "Morocco"
    32542330,   # David Mullarkey, "Great Britain & N.I."
                # National-team entries. A country as the school is a strong
                # signal and would need a country list to use generally.
})


def isProTeam(school):
    """True when this school string names a professional team.

    ⚠ EXACT MATCH ON THE WHOLE NAME, NOT A SUBSTRING. "Chino Pumas Track
      Club" normalises to itself and is not in the set; "PUMA" normalises to
      "puma" and is.
    """
    return _normSchool(school) in _PRO_TEAMS


def resolvePool(grade, gender, source, school, sport,
                season_level=None, grade_untrusted=False, is_pro=False,
                college_first=None, upperclass_first=None,
                race_date=None, merge=False, poolfor=poolFor,
                fixed_grade=None, fixed_level=None,
                grade_verdict=None, person_id=None):
    """Which pool does this row belong to? Returns "hs_m|XC", or None.

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

    # ! A PRO IS REPOOLED, NOT DROPPED -- the same treatment pro_flag gives a
    #   flagged season. They stop dragging the school pools' 100 point and
    #   get measured against each other instead of being thrown away.
    # ! person_id IS OPTIONAL, so a caller that does not pass it simply gets
    #   the school-based test. Adding a required argument would break three
    #   call sites for a set that is currently empty.
    if isProTeam(school) or (person_id is not None
                             and int(person_id) in _PRO_PEOPLE):
        is_pro = True

    if grade_verdict in ("no_evidence", "contradicted", "thin_field",
                         "lone_word"):
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
    season_for_pool = None if (grade is not None and not grade_untrusted) \
                      else season_level
    pool = poolfor(None if grade_untrusted else grade,
                   gender, source, school, season_level=season_for_pool)
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
    if (grade_untrusted and grade is not None
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