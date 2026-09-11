"""
meet_class.py -- the championship class of a meet BY NAME, as a diagnostic.

★ WHAT THE CLASS IS FOR NOW (issue #22, 2026-09-11, second cut). The joint
  solve's taper term no longer reads anything off a meet's name. Its
  covariate is the race's SEASON-END SHARE (run_joint.seasonEndShare): the
  fraction of the race's field, counting only athlete-seasons with three
  or more races whose season has closed, for whom the race falls within
  two weeks of the last race of their own season. A state final is a race
  where nearly everyone's season ends; a mid-season invitational is one
  where nearly nobody's does; a league meet sits wherever its own field
  puts it. The owner's two objections to a name-based class both fall
  away: nothing is blanketed (a league meet whose field mostly keeps
  racing carries a small share, whatever its name says) and there is no
  regex to misread ("Golden State Invitational" is an ordinary race
  because its runners keep racing, not because a guard caught the word).

  The name class below is kept as a CROSS-CHECK. The pack still carries
  it (column meet_class), the solve prints the mean season-end share by
  name class, and scripts/meet_class_census.py prints the names behind
  each class. If the finals (3) do not show the highest share and the
  ordinary meets (0) the lowest, something is wrong with the calendars
  the share is read off, and that is the line that says so.

    0   an ordinary meet: an invitational, a dual, a preview, a relays
    1   a league, conference, county or metro championship
    2   a qualifying round: a section, region, district, super-regional,
        prelim, semi, or anything called a qualifier
    3   a final: the state meet, the national meet (NXN, NXR, Foot Locker,
        Nike Cross, NCAA, NAIA, NJCAA, NIRCA), a state association's own
        series

  The rule keeps its guards (an invitational is class 0 whatever else its
  name says unless it also says qualifier, championship, final, prelim or
  semi; a championship-labelled meet outside CHAMPIONSHIP_WINDOW is class
  0) so that the cross-check is as honest as a name can be. Nothing here
  moves a rating.

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
