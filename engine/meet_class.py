"""
meet_class.py -- the championship class of a meet, in ONE place.

★ WHAT THE CLASS IS FOR (issue #22, 2026-09-11). The joint solve fits a
  taper term per (pool, sport, class): a tapered, qualified field runs a
  couple of percent faster than the same athletes mid-season, and without
  a shared term for that a venue that hosts only championships books the
  taper as an easy course. The class is read off the meet's NAME here, and
  off the feed's own flag where it has one (meets_tfrrs.is_championship).

    0   an ordinary meet: an invitational, a dual, a preview, a relays
    1   a league, conference, county or metro championship
    2   a qualifying round: a section, region, district, super-regional,
        prelim, semi, or anything called a qualifier
    3   a final: the state meet, the national meet (NXN, NXR, Foot Locker,
        Nike Cross, NCAA, NAIA, NJCAA, NIRCA), a state association's own
        series

  ⚠ THREE CLASSES, NOT ONE, AND EACH FITTED ON ITS OWN (owner, 2026-09-11:
    "no one is tapering for their league championship, but they are for
    their state meet. I'm not really sure you can just blanket these").
    Nothing is blanketed: each class's coefficient is fitted from its own
    rows with a zero prior, so a league championship never inherits the
    state meet's taper, and a class that shows no taper carries none. A
    meet's own deviation from its class lands in the race-day term, which
    a rating never removes. What a wrong class average can still move is
    the difficulty of a venue that hosts ONLY that class -- which is why
    the finer the classes, the smaller that error, and why
    scripts/meet_class_census.py exists.

★ THREE GUARDS KEEP A WRONG CLASS FROM MOVING A RATING. A misread name at
  a venue that hosts only one race would hand that race's rows about two
  thirds of the class's taper as extra credit (measured on a planted
  world, 2026-09-11: +2.2% on the rows, +1 point on the course). So:

    1. the invitational guard here: a name that says invitational,
       preview, classic, festival, relays or dual is class 0 whatever else
       it says ("Golden State Invitational"), unless it also says
       qualifier, championship, final, prelim or semi
    2. the season window (CHAMPIONSHIP_WINDOW): a championship-labelled
       meet outside the weeks championships are actually run is class 0
       (a "State Preview" in September, a "Regional" in March)
    3. run_joint.importanceClasses applies the term only at venues with
       two or more races: there the other races pin the course and the
       term is a relabel of the day; at a one-race venue the course would
       take a share of it whether or not the label was right

The SQL and the Python below are the same rule, in the same order.
"""
import re

N_CLASS = 3                       # classes above the reference (1, 2, 3)

# an ordinary meet is the reference class whatever else its name says ...
RX_INVITE = ("(invit|preview|classic|festival|jamboree|showcase|relays|"
             "(^|[^a-z])dual|tri-?meet|scrimmage|time trial|twilight|carnival)")
# ... unless it is also a qualifier, a championship, a final or a prelim
RX_KEEP = "(qualif|champ|final|prelim|semi)"
# a qualifying round by its own word, tested before the finals so that a
# "State Prelims" is a prelim and not the state meet
RX_PRELIM = "(qualif|prelim|semi)"
# the high-school national series: regionals included, since they are the
# peak of the season for nearly everyone who runs them
RX_HS_NATIONAL = "(nxn|nxr|foot ?locker|nike cross)"
# a qualifying round by its level
RX_QUAL = "(section|region|district|super ?regional)"
# the finals: the state meet, the college national meets, and the state
# associations by their acronyms as whole words ("CIF-SS", "PIAA")
RX_FINAL = ("((^|[^a-z])state|(^|[^a-z])nationals?|ncaa|naia|njcaa|nirca|"
            "(^|[^a-z])(cif|uil|ihsa|ihsaa|piaa|ohsaa|mhsaa|nysphsaa|fhsaa|ghsa|"
            "wiaa|osaa|chsaa|nchsaa|vhsl|njsiaa|mshsaa|kshsaa|tssaa|ahsaa|lhsaa|"
            "schsl|wvssac|mpssaa|ciac|dciaa|miaa|nhiaa|niaa|uhsaa|idhsaa|mhsa|"
            "wyhsaa|sdhsaa|ndhsaa|nsaa|khsaa|mshsl|iahsaa|okssaa|ossaa|aaa|"
            "ahsa|mpa|nmaa|asaa|dhsaa)([^a-z]|$))")
# a league-level championship
RX_LEAGUE = "(champ|conference|league|county|metro)"

# ★ WHEN CHAMPIONSHIPS ARE RUN, in academic days (0 = 1 August,
#   joint_solve.academicDay). XC: mid-October to mid-December (league finals
#   through NXN and Foot Locker). Track: early February (indoor conference)
#   to the start of July (state, NCAA, the national HS meets).
CHAMPIONSHIP_WINDOW = {0: (70, 140), 1: (180, 335)}      # 0 = XC, 1 = TF

_inv = re.compile(RX_INVITE, re.I)
_keep = re.compile(RX_KEEP, re.I)
_prelim = re.compile(RX_PRELIM, re.I)
_hsnat = re.compile(RX_HS_NATIONAL, re.I)
_qual = re.compile(RX_QUAL, re.I)
_final = re.compile(RX_FINAL, re.I)
_league = re.compile(RX_LEAGUE, re.I)

CLASS_NAMES = {0: "ordinary", 1: "league", 2: "qualifier", 3: "final"}


def classify(name, flag=False):
    """The class of a meet from its name and (optionally) the feed's own
    championship flag. The Python twin of sql(), same order."""
    s = name or ""
    if _inv.search(s) and not _keep.search(s):
        return 0
    if _prelim.search(s):
        return 2
    if _hsnat.search(s):
        return 3
    if _qual.search(s):
        return 2
    if _final.search(s):
        return 3
    if _league.search(s):
        return 1
    if flag:
        return 2                      # the feed says championship, the name does not say which
    return 0


def sql(name_expr, flag_expr=None):
    """A SELECT expression giving 3, 2, 1 or 0 for the meet name expression,
    in classify()'s order. flag_expr, when given, is a boolean SQL
    expression for the feed's own flag; it decides only when the name
    says nothing. No `%` and no braces in the regexes: the queries are
    f-strings and psycopg2 scans for `%`."""
    flag = f"WHEN {flag_expr} THEN 2 " if flag_expr else ""
    return (f"CASE WHEN {name_expr} ~* '{RX_INVITE}' "
            f"AND {name_expr} !~* '{RX_KEEP}' THEN 0 "
            f"WHEN {name_expr} ~* '{RX_PRELIM}' THEN 2 "
            f"WHEN {name_expr} ~* '{RX_HS_NATIONAL}' THEN 3 "
            f"WHEN {name_expr} ~* '{RX_QUAL}' THEN 2 "
            f"WHEN {name_expr} ~* '{RX_FINAL}' THEN 3 "
            f"WHEN {name_expr} ~* '{RX_LEAGUE}' THEN 1 "
            f"{flag}"
            f"ELSE 0 END")


def inWindow(sport, academic_day):
    """Is this academic day inside the sport's championship window?
    Vectorised over numpy arrays."""
    import numpy as np
    sport = np.asarray(sport)
    day = np.asarray(academic_day, dtype=np.float64)
    lo = np.where(sport == 1, CHAMPIONSHIP_WINDOW[1][0], CHAMPIONSHIP_WINDOW[0][0])
    hi = np.where(sport == 1, CHAMPIONSHIP_WINDOW[1][1], CHAMPIONSHIP_WINDOW[0][1])
    return (day >= lo) & (day <= hi)
