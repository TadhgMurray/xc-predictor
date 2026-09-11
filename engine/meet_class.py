"""
meet_class.py -- the championship class of a meet, in ONE place.

★ WHAT THE CLASS IS FOR (issue #22, 2026-09-11). The joint solve fits a
  taper term per (pool, sport, class): a tapered, qualified field runs a
  couple of percent faster than the same athletes mid-season, and without
  a shared term for that a venue that hosts only championships books the
  taper as an easy course. The class is read off the meet's NAME here, and
  off the feed's own flag where it has one (meets_tfrrs.is_championship).

    0   an ordinary meet: an invitational, a dual, a preview, a relays
    1   a league, conference, county, district or metro championship
    2   a section, region, state or national championship (NXN, NXR, Foot
        Locker, Nike Cross, the NCAA/NAIA/NJCAA meets) or a qualifier for one

★ THREE THINGS KEEP A WRONG CLASS FROM MOVING A RATING. A misread name at
  a venue that hosts only one race would hand that race's rows about two
  thirds of the class's taper as extra credit (measured on a planted
  world, 2026-09-11: +2.2% on the rows, +1 point on the course). So:

    1. the invitational guard here: a name that says invitational,
       preview, classic, festival, relays or dual is class 0 whatever else
       it says ("Golden State Invitational"), unless it also says
       qualifier, championship, final or prelim
    2. the season window (CHAMPIONSHIP_WINDOW): a championship-labelled
       meet outside the weeks championships are actually run is class 0
       (a "State Preview" in September, a "Regional" in March)
    3. run_joint.importanceClasses applies the term only at venues with
       two or more races: there the other races pin the course and the
       term is a relabel of the day; at a one-race venue the course would
       take a share of it whether or not the label was right

  The class is also fitted, not assumed: joint_solve.IMP_PRIOR_MEAN is
  zero, so a class with no evidence carries no taper at all.

The SQL and the Python below are the same rule; scripts/meet_class_census.py
prints what the rule does to the real meet names, per class, so a
misclassification can be seen before it is trusted.
"""
import re

# class 2: the end-of-season series and its qualifiers
RX_2 = ("((^|[^a-z])state|section|region|(^|[^a-z])nationals?|nxn|nxr|"
        "foot ?locker|nike cross|super ?regional|qualif|ncaa|naia|njcaa|"
        "nirca|"
        # the state associations, as whole words ("CIF-SS Prelims", "UIL
        # Region II-6A", "PIAA District 3"): their series is the state series
        "(^|[^a-z])(cif|uil|ihsa|ihsaa|piaa|ohsaa|mhsaa|nysphsaa|fhsaa|ghsa|"
        "wiaa|osaa|chsaa|nchsaa|vhsl|njsiaa|mshsaa|kshsaa|tssaa|ahsaa|lhsaa|"
        "schsl|wvssac|mpssaa|ciac|dciaa|miaa|nhiaa|niaa|uhsaa|idhsaa|mhsa|"
        "wyhsaa|sdhsaa|ndhsaa|nsaa|khsaa|mshsl|iahsaa|okssaa|ossaa|aaa|"
        "ahsa|mpa|nmaa|asaa|dhsaa|ihsaa)([^a-z]|$))")
# class 1: a league-level championship
RX_1 = "(champ|conference|league|county|district|metro)"
# an ordinary meet is the reference class whatever else its name says ...
RX_INVITE = ("(invit|preview|classic|festival|jamboree|showcase|relays|"
             "(^|[^a-z])dual|tri-?meet|scrimmage|time trial|twilight|carnival)")
# ... unless it is also a qualifier, a championship, a final or a prelim
RX_KEEP = "(qualif|champ|final|prelim|semi)"

# ★ WHEN CHAMPIONSHIPS ARE RUN, in academic days (0 = 1 August,
#   joint_solve.academicDay). XC: mid-October to mid-December (league finals
#   through NXN and Foot Locker). Track: early February (indoor conference)
#   to the start of July (state, NCAA, the national HS meets).
CHAMPIONSHIP_WINDOW = {0: (70, 140), 1: (180, 335)}      # 0 = XC, 1 = TF

_rx2 = re.compile(RX_2, re.I)
_rx1 = re.compile(RX_1, re.I)
_inv = re.compile(RX_INVITE, re.I)
_keep = re.compile(RX_KEEP, re.I)


def classify(name, flag=False):
    """The class of a meet from its name and (optionally) the feed's own
    championship flag. The Python twin of sql()."""
    s = name or ""
    if _inv.search(s) and not _keep.search(s):
        return 0
    if flag:
        return 2
    if _rx2.search(s):
        return 2
    if _rx1.search(s):
        return 1
    return 0


def sql(name_expr, flag_expr=None):
    """A SELECT expression giving 2, 1 or 0 for the meet name expression.
    flag_expr, when given, is a boolean SQL expression for the feed's own
    flag; it outranks the name but not the invitational guard. No `%` and
    no braces in the regexes: the queries are f-strings and psycopg2 scans
    for `%`."""
    flag = f"WHEN {flag_expr} THEN 2 " if flag_expr else ""
    return (f"CASE WHEN {name_expr} ~* '{RX_INVITE}' "
            f"AND {name_expr} !~* '{RX_KEEP}' THEN 0 "
            f"{flag}"
            f"WHEN {name_expr} ~* '{RX_2}' THEN 2 "
            f"WHEN {name_expr} ~* '{RX_1}' THEN 1 ELSE 0 END")


def inWindow(sport, academic_day):
    """Is this academic day inside the sport's championship window?
    Vectorised over numpy arrays."""
    import numpy as np
    sport = np.asarray(sport)
    day = np.asarray(academic_day, dtype=np.float64)
    lo = np.where(sport == 1, CHAMPIONSHIP_WINDOW[1][0], CHAMPIONSHIP_WINDOW[0][0])
    hi = np.where(sport == 1, CHAMPIONSHIP_WINDOW[1][1], CHAMPIONSHIP_WINDOW[0][1])
    return (day >= lo) & (day <= hi)
