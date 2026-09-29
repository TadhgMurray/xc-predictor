#!/usr/bin/env python3
"""
link_teamless.py -- an anet athlete profile with no team, that is really a
named-school athlete, joins that athlete.

    /srv/venv/bin/python scripts/link_teamless.py                 # DRY RUN: counts + samples
    /srv/venv/bin/python scripts/link_teamless.py --show 80       # more samples
    /srv/venv/bin/python scripts/link_teamless.py --person 13642378   # one profile, every step
    /srv/venv/bin/python scripts/link_teamless.py --seasons 6     # look further back
    /srv/venv/bin/python scripts/link_teamless.py --apply         # merge (pipeline 04a2)
    /srv/venv/bin/python scripts/link_teamless.py --undo 13642378 # take one back, and never again
    /srv/venv/bin/python scripts/link_teamless.py --undo all      # take every one back

★ THE HOLE (owner, 2026-09-28, from Tadhg Murray's page). athletic.net
  lists some results under a SECOND athlete profile that belongs to no
  school: Tadhg Murray, De La Salle (CA), is /athlete/29603086 with his whole
  high school career, and his Foot Locker West Regional (Mt. SAC,
  2024-12-07, 16:34) sits on /athlete/13642378, "Tadhg Murray -- Unknown
  (WA)", one race, no grade, team "Danville CA". His teammate Trey Caldwell
  ran the same race and it merged fine: anet had filed Trey's under his own
  profile. anet rows are seeded person_id = athlete_id at insert and nothing
  joins two anet profiles, so the second profile is a second person for
  ever -- its own page, its own search hit, a race missing from the real
  career.

  The owner, the same day: a Foot-Locker-only rule is too narrow -- handle
  the general problem: anet profiles whose rows have NO REAL TEAM that are
  the same person as a named-school athlete.

★ WHAT "NO REAL TEAM" IS (isTeamlessRow / teamlessSql, one rule twice):
    - an anet row on team 0 or no team id ("no team" to anet, pool_resolve);
    - a school that says it is no team: blank, Unknown, Unattached in every
      spelling panels.isTeamName refuses, Individual, Independent, Club, Open;
    - a HOMETOWN in the school column: "Danville CA", "Salt Lake City UT".
      That is how anet files every runner at a Foot Locker regional -- the
      race page scores "Spokane WA" and "San Jose CA" as teams -- and a city
      plus a state code is nobody's school. A name with a school word in it
      (High, Academy, Prep, Club ...) is not a hometown.

★ WHO IS A CANDIDATE. An anet profile X still on its own seed (person_id =
  athlete_id), with a teamless row in the last --seasons academic years, and
  EVERY row carrying person_id X -- both sports, any year -- a teamless anet
  row of athlete X. One real-school row and X is a career, not a stray
  profile, and is left alone; so is an X that a linker has already joined a
  tfrrs row to.

★ WHO IS THE TARGET (decide(), pure, tests/test_link_teamless.py):
    1. NAME: same normalised full name (link_freshmen.normName), two tokens
       at least; a namesake of the other gender (both known) is not him.
    2. WINDOW: a person with a real-school row within WINDOW_DAYS (60) of
       any of X's rows. Exactly ONE such person, counted BEFORE the grade and
       level filters below: two namesakes racing that autumn is a refusal,
       even if one of them would fail the fit test. A missed merge costs a
       page; a wrong one welds a stranger's race onto a career.
    3. COVERAGE: every row of X lies within the window of one of the
       target's real-school rows. An X that also raced years before or after
       the target's career is somebody else's profile, or two people's. And
       an X cross country race on a day the target raced XC for his school
       is a second runner in the field, not him.
    4. FIT (the generation test): a numeric grade on X's rows gives a class
       year (academic year + 1 + 12 - grade) that must be within one of the
       target's; a numeric "grade" past 12 reads as an age and refuses; an X
       that is high school (a 9-12 grade, or a high school all-star meet --
       Foot Locker, NXN) never joins a target racing college that season.
    5. EVIDENCE: at least TWO of three, and nothing that contradicts --
         rating    X's rated rows sit within RATING_TOL (6.0) of the median
                   of the target's same-sport ratings within the window
                   (MIN_RATED of them at least). Off by more than
                   RATING_CONTRA (15) refuses outright: one person does not
                   run 15 points away from his own season a week later.
         region    X's state -- the hometown's state code, else the meet's
                   state -- is the state of the target's team (anet_team),
                   else of the meets the target raced in the window.
         qualifier X raced an all-star championship (Foot Locker / Champs
                   Sports / Eastbay, NXN, Brooks, RunningLane) and the target
                   raced a postseason meet (state, section, CIF, district,
                   regional, a championship) in the QUAL_DAYS (21) before it
                   -- the route by which a school runner gets to one.
       Tadhg Murray has all three: 129.4 against a 129.0 median over his
       five races in the window; Danville CA against De La Salle's CA; and
       the CIF State Championships seven days before the regional.
    6. ONE CLAIM PER TARGET: two teamless profiles of the same name touching
       the same target are BOTH refused -- the owner's rule, "do not merge
       when two same-name candidates exist".

! MERGE, NOT SPLIT -- AND THE HEADER OF engine/unlink.py SAYS A MERGE IS THE
  HARDER CLAIM. So every guard is on the side of leaving a profile alone, the
  dry run prints why each one was refused, and everything is reversible.

★ WHAT --apply WRITES, IN ONE TRANSACTION:
    - person_link_log: every moved result, (sport, result_id, X, target,
      'teamless') -- the same audit log the tfrrs linkers write;
    - results / results_tf: person_id X -> target, rows of athlete X only;
    - athletes: the rows of athlete X that carry person_id X -> target.
      ⚠ THIS IS WHAT MAKES THE REDIRECT WORK. person_redirects calls an id
      gone only when NO athletes row carries it; left alone, X's athletes row
      keeps the id "alive", 13c0 writes no redirect (and drops one that
      exists), and /athlete/X renders an empty page under the name instead
      of a 301. Nothing else in the corpus ever moves an athletes row, which
      is why this is the first linker that needs to;
    - teamless_merge: the decision (X, target, name, evidence) -- what makes
      the step STICKY: a re-scrape inserts X's next teamless row seeded with
      person_id X, and the next run moves it to the same target without
      deciding again (and says so if X has since grown a real-school row);
    - person_redirect: X -> target, directly. 13c0 derives the same redirect
      from the 01a snapshot when this runs inside the pipeline (01a sees X,
      13c0 sees X gone and its probe rows on the target), but a run by hand
      outside the pipeline has no snapshot that saw X, so it is written here
      too; the upsert makes the two agree.

! UNDO. --undo X puts X's rows and athletes rows back, drops the redirect
  and the decision, and VETOES X (teamless_veto) so the next run does not
  merge it again. --undo all reverses every merge (no veto: set
  XCP_LINK_TEAMLESS=0 to keep the pipeline from redoing them).

Read-only without --apply / --undo.
"""
import argparse
import datetime
import os
import re
import statistics
import sys
from collections import Counter, defaultdict, namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ★ ONE NAME RULE: the freshman linker's. Two linkers that normalise a name
#   differently would each find namesakes the other cannot see.
from link_freshmen import LOG_DDL, normName                  # noqa: E402
# the one definition of a college row / a high school row (level_conflict)
from level_conflict import levelSql, stageCollegeSchools     # noqa: E402

RULE = "teamless"

WINDOW_DAYS = 60        # a real-school row this close to a teamless one
QUAL_DAYS = 21          # the postseason meet before an all-star race
RATING_TOL = 6.0        # "the same runner" -- Tadhg: 0.4 off his median
RATING_CONTRA = 15.0    # "not the same runner", whatever else agrees
MIN_RATED = 2           # target ratings needed before the rating testifies
CLASS_SLACK = 1         # a grade a year stale is noise, two is a generation
MIN_EVIDENCE = 2        # of rating / region / qualifier
DEFAULT_SEASONS = 3

TABLES = (("XC", "results"), ("TF", "results_tf"))

# ------------------------------------------------------------------ #
#  WHAT A TEAMLESS ROW IS -- Python and SQL, pinned equal by the tests
# ------------------------------------------------------------------ #
# the spellings of "no team" -- panels.isTeamName's lists, plus the scraper's
# own placeholder: scrape_results writes "Unknown" when anet gave no school
NO_TEAM_EXACT = ("unknown", "unat", "none", "n/a", "na", "no team",
                 "no school", "independent", "individual", "individuals",
                 "club", "open", "unattached", "unattached runner", "-")
NO_TEAM_FRAGMENTS = ("unattached", "individual", "independent", "no team",
                     "no school")
NO_TEAM_PREFIXES = ("unatt", "unnatt", "unath")

US_STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD "
             "MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD "
             "TN TX UT VT VA WA WV WI WY").split()
# ! NO %, NO BRACES in these: they go into SQL that psycopg2 formats and
#   that this file builds with f-strings (champ_course.py's rule too)
_HOMETOWN_RX = ("^[A-Za-z][A-Za-z .'-]*,? (" + "|".join(US_STATES) + ")$")
# a word that makes a string a school or a club, not a city
_SCHOOL_WORD_RX = ("(^|[^a-z])(school|high|hs|academy|prep|preparatory|"
                   "college|university|univ|christian|catholic|charter|"
                   "institute|middle|ms|jr|elementary|club|tc|ac|running|"
                   "track|xc|team|athletics|striders|harriers)([^a-z]|$)")
_hometown = re.compile(_HOMETOWN_RX)
_school_word = re.compile(_SCHOOL_WORD_RX)


def hometownState(school):
    """'Danville CA' -> 'CA'; None when the string is not a bare city+state."""
    s = (school or "").strip()
    if not _hometown.match(s) or _school_word.search(s.lower()):
        return None
    return s[-2:]


def isTeamlessRow(school, team_id=None, source="anet"):
    """True when the row names no team. The Python twin of teamlessSql()."""
    if source == "anet" and (team_id is None or int(team_id) <= 0):
        return True
    s = (school or "").strip().lower()
    if not s or s in NO_TEAM_EXACT:
        return True
    if any(f in s for f in NO_TEAM_FRAGMENTS):
        return True
    if re.sub(r"[^a-z0-9]", "", s).startswith(NO_TEAM_PREFIXES):
        return True
    if re.search(r"(^|[^a-z0-9])(unatt|unnatt|unath)", s):
        return True
    return hometownState(school) is not None


def teamlessSql(a="r"):
    """A boolean SQL expression, never NULL: the row names no team."""
    exact = ", ".join("'" + x.replace("'", "''") + "'" for x in NO_TEAM_EXACT)
    frags = "|".join(NO_TEAM_FRAGMENTS)
    pref = "|".join(NO_TEAM_PREFIXES)
    home = _HOMETOWN_RX.replace("'", "''")
    word = _SCHOOL_WORD_RX
    return (f"COALESCE(("
            f"({a}.source = 'anet' AND COALESCE({a}.team_id, 0) <= 0)"
            f" OR NULLIF(btrim({a}.school), '') IS NULL"
            f" OR lower(btrim({a}.school)) IN ({exact})"
            f" OR lower({a}.school) ~ '({frags})'"
            f" OR regexp_replace(lower({a}.school), '[^a-z0-9]', '', 'g') ~ '^({pref})'"
            f" OR lower({a}.school) ~ '(^|[^a-z0-9])({pref})'"
            f" OR (btrim({a}.school) ~ '{home}'"
            f"     AND lower(btrim({a}.school)) !~ '{word}')"
            f"), false)")


def nameKeySql(first, last):
    """normName('First Last') in SQL -- the join key between a candidate and
    its namesakes. Pinned to normName by the tests."""
    return (f"btrim(regexp_replace(regexp_replace(lower(replace("
            f"concat_ws(' ', btrim({first}), btrim({last})), '-', ' ')), "
            f"'[^a-z ]+', '', 'g'), '\\s+', ' ', 'g'))")


# ------------------------------------------------------------------ #
#  MEETS: an all-star race, and the postseason that leads to one
# ------------------------------------------------------------------ #
_ALLSTAR = re.compile(r"(foot ?locker|champs ?sports|eastbay|nike cross|\bnx[nr]\b"
                      r"|brooks (xc|cross|pr)|running ?lane)", re.I)
_ALLSTAR_WORD = re.compile(r"(champ|national|regional|final|invitational)", re.I)
_POSTSEASON = re.compile(r"(\bstate\b|\bsection|\bcif\b|district|regional"
                         r"|championship|\bchamps\b|\bfinals?\b|conference)", re.I)


def isAllStar(meet_name):
    s = meet_name or ""
    return bool(_ALLSTAR.search(s) and _ALLSTAR_WORD.search(s))


def isPostseason(meet_name):
    return bool(_POSTSEASON.search(meet_name or "")) and not isAllStar(meet_name)


# ------------------------------------------------------------------ #
#  THE DECISION -- pure
# ------------------------------------------------------------------ #
# One row, either side. level is level_conflict's: 'college' / 'hs' / None;
# state is the meet's, team_state the anet team's. A namesake carries only
# its REAL-team rows (gather() splits them on teamlessSql).
Row = namedtuple("Row", "date sport grade rating state meet school level team_state",
                 defaults=(None, None))
# A person: a candidate (every row teamless) or a namesake (rows as they are).
Person = namedtuple("Person", "pid name gender rows")
Verdict = namedtuple("Verdict", "target reason evidence touched school",
                     defaults=((), (), None))

MATCH = "match"


def _day(d):
    try:
        return datetime.date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


def _g(v):
    v = str(v or "").strip().upper()[:1]
    return v if v in ("M", "F") else None


def _numGrade(grade):
    g = str(grade or "").strip()
    return int(g) if re.fullmatch(r"\d{1,2}", g) else None


def classYear(date, grade):
    """The class a numeric grade 1-12 implies: the year the academic year
    ends plus the grades left. None for no date, no numeric grade, or past 12."""
    d, g = _day(date), _numGrade(grade)
    if d is None or g is None or not 1 <= g <= 12:
        return None
    ay_end = d.year + 1 if d.month >= 8 else d.year
    return ay_end + (12 - g)


def _near(d, rows, days):
    return [r for r in rows
            if r.date and d and abs((_day(r.date) - d).days) <= days]


def decide(cand, namesakes, window=WINDOW_DAYS):
    """Verdict(target pid or None, reason, evidence, touched pids, school).

    cand: a Person whose rows are all teamless. namesakes: Persons of any
    name (filtered here), their rows the REAL-SCHOOL rows only."""
    key = normName(cand.name)
    if not key:
        return Verdict(None, "no usable name")
    days = [_day(r.date) for r in cand.rows]
    days = [d for d in days if d]
    if not days:
        return Verdict(None, "no dated rows")
    gc = _g(cand.gender)
    pool = [t for t in namesakes
            if t.pid != cand.pid and normName(t.name) == key
            and not (gc and _g(t.gender) and gc != _g(t.gender))]
    touched = [t for t in pool
               if any(_near(d, t.rows, window) for d in days)]
    tids = tuple(sorted(t.pid for t in touched))
    if not touched:
        return Verdict(None, "no named-school namesake in the window", (), tids)
    if len(touched) > 1:
        return Verdict(None, "ambiguous: namesakes in the window", (), tids)
    t = touched[0]
    school = Counter(r.school for r in t.rows
                     if any(abs((_day(r.date) - d).days) <= window
                            for d in days)).most_common(1)
    school = school[0][0] if school else None

    # 3. coverage: every teamless row inside the target's career
    if not all(_near(d, t.rows, window) for d in days):
        return Verdict(None, "rows outside the namesake's career", (), tids, school)

    # ! TWO CROSS COUNTRY RACES ON ONE DAY IS TWO PEOPLE. A namesake who
    #   raced for a school the same day X raced teamless is somebody else
    #   in the same field (or the same race stored twice, which is 04c's
    #   to flag, not this step's to weld). Track is left out: one athlete
    #   runs several events a day, and anet can file them apart.
    t_xc_days = {str(x.date)[:10] for x in t.rows if x.sport == "XC"}
    if any(r.sport == "XC" and str(r.date)[:10] in t_xc_days for r in cand.rows):
        return Verdict(None, "same day as the namesake's own race", (), tids, school)

    # 4. fit: generation and level
    t_cls =[c for c in (classYear(r.date, r.grade) for r in t.rows) if c]
    t_cls_med = statistics.median(t_cls) if t_cls else None
    for r in cand.rows:
        g = _numGrade(r.grade)
        if g is not None and g > 12:
            return Verdict(None, "grade reads as an age", (), tids, school)
        cy = classYear(r.date, r.grade)
        if cy is not None and t_cls_med is not None \
                and abs(cy - t_cls_med) > CLASS_SLACK:
            return Verdict(None, "generation mismatch", (), tids, school)
        is_hs = (g is not None and 9 <= g <= 12) or isAllStar(r.meet)
        if is_hs and any(x.level == "college"
                         for x in _near(_day(r.date), t.rows, window)):
            return Verdict(None, "level mismatch (college namesake)", (), tids, school)

    # 5. evidence
    ev = []
    # rating
    diffs = []
    for r in cand.rows:
        if r.rating is None:
            continue
        near = _near(_day(r.date), t.rows, window)
        cmp_ = [x.rating for x in near if x.rating is not None and x.sport == r.sport]
        if len(cmp_) < MIN_RATED:
            cmp_ = [x.rating for x in near if x.rating is not None]
        if len(cmp_) < MIN_RATED:
            continue
        med = statistics.median(cmp_)
        diffs.append((r.rating - med, r.rating, med, len(cmp_)))
    if any(abs(dd[0]) > RATING_CONTRA for dd in diffs):
        worst = max(diffs, key=lambda dd: abs(dd[0]))
        return Verdict(None, "rating contradicts", (
            f"rating {worst[1]:.1f} vs {worst[2]:.1f}",), tids, school)
    if diffs and all(abs(dd[0]) <= RATING_TOL for dd in diffs):
        dd = diffs[0]
        ev.append(f"rating {dd[1]:.1f} vs median {dd[2]:.1f} of {dd[3]}")
    # region
    t_states = {x.team_state for x in t.rows if x.team_state}
    if not t_states:
        ms = Counter(x.state for x in t.rows if x.state).most_common(1)
        t_states = {ms[0][0]} if ms else set()
    c_states = {hometownState(r.school) or r.state for r in cand.rows} - {None}
    hit = sorted(c_states & t_states)
    if hit:
        ev.append(f"region {'/'.join(hit)}")
    # qualifier
    for r in cand.rows:
        if not isAllStar(r.meet):
            continue
        d = _day(r.date)
        pre = [x for x in t.rows if x.date and isPostseason(x.meet)
               and 0 <= (d - _day(x.date)).days <= QUAL_DAYS]
        if pre:
            x = max(pre, key=lambda x: x.date)
            ev.append(f"qualifier {str(x.meet)[:40]} {(d - _day(x.date)).days}d before")
            break
    if len(ev) < MIN_EVIDENCE:
        return Verdict(None, "weak evidence", tuple(ev), tids, school)
    return Verdict(t.pid, MATCH, tuple(ev), tids, school)


def resolveClaims(verdicts, names=None):
    """{cand_pid: Verdict} with the owner's rule applied: two teamless
    profiles of one name that touch one target are both refused -- whether
    or not the other one would have matched."""
    names = names or {}
    by_target = defaultdict(set)
    for pid, v in verdicts.items():
        for t in v.touched:
            by_target[(normName(names.get(pid, "")) or "", t)].add(pid)
    out = dict(verdicts)
    for (_k, t), pids in by_target.items():
        if len(pids) < 2:
            continue
        for pid in pids:
            v = out[pid]
            if v.target is not None:
                out[pid] = v._replace(target=None,
                                      reason="two teamless namesakes claim one target")
    return out


# ------------------------------------------------------------------ #
#  THE CORPUS SIDE
# ------------------------------------------------------------------ #
def _step(cur, what, sql, params=None):
    import time
    t0 = time.time()
    print(f"[teamless]   {what} ...", end="", flush=True)
    cur.execute(sql, params or {})
    print(f" {time.time() - t0:.0f}s"
          + (f", {cur.rowcount:,} rows" if cur.rowcount >= 0 else ""), flush=True)


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def sinceFor(seasons, today=None):
    today = today or datetime.date.today()
    ay = today.year if today.month >= 8 else today.year - 1
    return f"{ay - seasons + 1}-08-01"


def _rowsSql(has_team, persons, real_only):
    """Every row of the persons in the temp table `persons` since %(lo)s, both
    sports, with the meet's name and state, the team's state and the two
    verdicts; real_only keeps the rows that name a team."""
    ts = "t.state" if has_team else "NULL::text"
    tj = "LEFT JOIN anet_team t ON t.team_id = r.team_id" if has_team else ""
    real = f"AND NOT {teamlessSql('r')}" if real_only else ""
    return f"""
        SELECT 'XC' AS sport, r.person_id, r.date, r.grade, r.speed_rating,
               m.state, m.meet_name, r.school, {levelSql('r')} AS level,
               {ts} AS team_state
        FROM   results r JOIN {persons} p ON p.person_id = r.person_id
        LEFT   JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        {tj}
        WHERE  r.date >= %(lo)s {real}
        UNION ALL
        SELECT 'TF', r.person_id, r.date, r.grade, r.speed_rating,
               (SELECT m.state FROM meets_tf m WHERE m.meet_id = r.meet_id LIMIT 1),
               (SELECT m.meet_name FROM meets_tf m WHERE m.meet_id = r.meet_id LIMIT 1),
               r.school, {levelSql('r')}, {ts}
        FROM   results_tf r JOIN {persons} p ON p.person_id = r.person_id
        {tj}
        WHERE  r.date >= %(lo)s AND COALESCE(r.is_relay, 0) = 0 {real}
    """


def _fetchRows(cur, has_team, persons, real_only, lo, what):
    _step(cur, what, _rowsSql(has_team, persons, real_only), {"lo": lo})
    rows = defaultdict(list)
    for sport, pid, date, grade, rating, state, meet, school, level, tst \
            in cur.fetchall():
        rows[pid].append(Row(date, sport, grade,
                             None if rating is None else float(rating),
                             state, meet, school, level, tst))
    for v in rows.values():
        v.sort(key=lambda r: str(r.date))
    return rows


def gather(cur, since, person=None):
    """({cand_pid: Person}, {cand_pid: [namesake Person]}, counts).
    Temp tables only; the caller rolls back."""
    counts = {}
    cur.execute("SET LOCAL work_mem = '512MB'")
    # ! levelSql's college test reads the staged college-school set
    #   (level_conflict.collegeSql), so it has to exist on this session
    stageCollegeSchools(cur)
    for t in ("tm_c", "tm_n", "tm_cp", "tm_np"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    # 1. anet profiles on their own seed with a recent teamless row
    who = ("AND r.person_id = %(person)s" if person is not None
           else "AND r.date >= %(since)s")
    # ! GROUPED BEFORE THE REGEXES: one (person, school, team-or-not) group
    #   per profile and school is a few million, the rows are tens of millions,
    #   and the verdict only reads those three columns.
    grouped = ("SELECT r.person_id, 'anet'::text AS source, r.school, "
               "CASE WHEN COALESCE(r.team_id, 0) <= 0 THEN 0 ELSE 1 END AS team_id "
               "FROM {t} r WHERE r.source = 'anet' AND r.person_id = r.athlete_id "
               + who + " GROUP BY 1, 2, 3, 4")
    _step(cur, "anet profiles with a teamless row (a scan of both tables)", f"""
        CREATE TEMP TABLE tm_c AS
        SELECT DISTINCT g.person_id FROM (
            {grouped.format(t='results')}
            UNION ALL
            {grouped.format(t='results_tf')}
        ) g
        WHERE {teamlessSql('g')}
    """, {"since": since, "person": person})
    if _hasTable(cur, "teamless_veto"):
        cur.execute("DELETE FROM tm_c WHERE person_id IN "
                    "(SELECT old_id FROM teamless_veto)")
    counts["profiles with a teamless row"] = _count(cur, "tm_c")
    # 2. their names and genders, off the anet registry
    cur.execute("ALTER TABLE tm_c ADD COLUMN name text, ADD COLUMN nm text, "
                "ADD COLUMN gender text")
    _step(cur, "their names (index probes)", f"""
        UPDATE tm_c c SET name = a.name, nm = a.nm, gender = a.gender
        FROM (SELECT DISTINCT ON (x.athlete_id) x.athlete_id,
                     concat_ws(' ', btrim(x.first_name), btrim(x.last_name)) AS name,
                     {nameKeySql('x.first_name', 'x.last_name')} AS nm, x.gender
              FROM   athletes x JOIN tm_c c2 ON c2.person_id = x.athlete_id
              ORDER  BY x.athlete_id,
                        (NULLIF(btrim(x.last_name), '') IS NOT NULL) DESC,
                        COALESCE(x.gender IN ('M', 'F'), false) DESC) a
        WHERE a.athlete_id = c.person_id
    """)
    cur.execute("DELETE FROM tm_c WHERE nm IS NULL OR position(' ' in nm) = 0")
    cur.execute("CREATE INDEX ON tm_c (nm)")
    # 3. every anet identity of the same name. ⚠ THE WHOLE REGISTRY, NOT A
    #    SURNAME PREFILTER: "Mary Ann | Smith" and "Mary | Ann Smith" are one
    #    name, and a namesake missed here is a merge that looks unique and
    #    is not -- the one mistake this step must not make.
    _step(cur, "their namesakes in the anet registry (a scan of athletes)", f"""
        CREATE TEMP TABLE tm_n AS
        SELECT DISTINCT ON (x.person_id, x.nm) x.person_id, x.nm, x.name, x.gender
        FROM (SELECT a.person_id, a.gender,
                     {nameKeySql('a.first_name', 'a.last_name')} AS nm,
                     concat_ws(' ', btrim(a.first_name), btrim(a.last_name)) AS name
              FROM   athletes a WHERE a.person_id IS NOT NULL) x
        WHERE  x.nm IN (SELECT nm FROM tm_c)
        ORDER  BY x.person_id, x.nm, COALESCE(x.gender IN ('M', 'F'), false) DESC
    """)
    cur.execute("DELETE FROM tm_c c WHERE NOT EXISTS (SELECT 1 FROM tm_n n "
                "WHERE n.nm = c.nm AND n.person_id <> c.person_id)")
    counts["... with a namesake anywhere"] = _count(cur, "tm_c")
    # 4. a candidate is ONLY teamless rows of its own athlete id, ever.
    # ! AFTER the namesakes are read: a profile with a real team AND a
    #   teamless row (Trey Caldwell's shape) is dropped as a candidate here,
    #   and must still be there as somebody's namesake.
    _step(cur, "dropping profiles with any real-team or linked row (index probes)", f"""
        DELETE FROM tm_c c
        WHERE EXISTS (SELECT 1 FROM results r WHERE r.person_id = c.person_id
                        AND NOT (r.source = 'anet' AND r.athlete_id = c.person_id
                                 AND {teamlessSql('r')}))
           OR EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = c.person_id
                        AND NOT (r.source = 'anet' AND r.athlete_id = c.person_id
                                 AND {teamlessSql('r')}))
    """)
    counts["... every row teamless (candidates)"] = _count(cur, "tm_c")
    has_team = _hasTable(cur, "anet_team")
    # 5. the candidates' rows (every one, any year), then their namesakes'
    #    real-team rows from a window before the earliest of them
    cur.execute("CREATE TEMP TABLE tm_cp AS SELECT person_id FROM tm_c")
    crow = _fetchRows(cur, has_team, "tm_cp", False, "0000",
                      "the candidates' rows (index probes)")
    first = min((str(r.date)[:10] for v in crow.values() for r in v
                 if _day(r.date)), default=since)
    lo = (_day(first) - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    cur.execute("CREATE TEMP TABLE tm_np AS SELECT DISTINCT n.person_id FROM tm_n n "
                "WHERE n.nm IN (SELECT nm FROM tm_c) "
                "AND n.person_id NOT IN (SELECT person_id FROM tm_c)")
    cur.execute("CREATE INDEX ON tm_np (person_id)")
    nrow = _fetchRows(cur, has_team, "tm_np", True, lo,
                      f"their namesakes' real-team rows since {lo} (index probes)")
    cur.execute("SELECT person_id, name, nm, gender FROM tm_c")
    cands, keys = {}, {}
    for pid, name, nm, g in cur.fetchall():
        cands[pid] = Person(pid, name, g, crow.get(pid, []))
        keys[pid] = nm
    cur.execute("SELECT person_id, name, nm, gender FROM tm_n "
                "WHERE person_id IN (SELECT person_id FROM tm_np)")
    by_key = defaultdict(dict)
    for pid, name, nm, g in cur.fetchall():
        by_key[nm][pid] = Person(pid, name, g, nrow.get(pid, []))
    namesakes = {pid: list(by_key[keys[pid]].values()) for pid in cands}
    return cands, namesakes, counts


def _count(cur, t):
    cur.execute(f"SELECT count(*) FROM {t}")
    return cur.fetchone()[0]


def judge(cands, namesakes):
    """{cand_pid: Verdict}, the per-target claim rule applied."""
    vs = {pid: decide(c, namesakes[pid]) for pid, c in cands.items()}
    return resolveClaims(vs, {pid: c.name for pid, c in cands.items()})


# ------------------------------------------------------------------ #
#  WRITING
# ------------------------------------------------------------------ #
MERGE_DDL = """
CREATE TABLE IF NOT EXISTS teamless_merge (
    old_id    bigint PRIMARY KEY,
    new_id    bigint NOT NULL,
    name      text,
    evidence  text,
    merged_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS teamless_veto (
    old_id    bigint PRIMARY KEY,
    vetoed_at timestamptz NOT NULL DEFAULT now()
)"""


def write(conn, decisions):
    """Record the new decisions, then (re)apply EVERY decision on file: the
    rows of athlete X still seeded to X move to the target, logged; X's
    athletes rows follow; the redirect is upserted. Returns counts."""
    from psycopg2.extras import execute_values
    from person_redirects import DDL as REDIRECT_DDL
    out = {}
    with conn.cursor() as cur:
        cur.execute(LOG_DDL)
        cur.execute(MERGE_DDL)
        cur.execute(REDIRECT_DDL)
        cur.execute("SELECT count(*) FROM teamless_merge")
        before = cur.fetchone()[0]
        if decisions:
            execute_values(cur, """
                INSERT INTO teamless_merge (old_id, new_id, name, evidence)
                VALUES %s ON CONFLICT (old_id) DO NOTHING""", decisions)
        cur.execute("SELECT count(*) FROM teamless_merge")
        out["decisions new"] = cur.fetchone()[0] - before
        # ! A TARGET CAN MOVE TOO (a twin merge, a later link): follow its
        #   redirect, so X's next row goes where the target went and not to
        #   an id no page serves.
        from person_redirects import follow
        cur.execute("SELECT old_id, new_id FROM teamless_merge")
        for old_id, new_id in cur.fetchall():
            moved = follow(cur, new_id)
            if moved and moved != old_id:
                cur.execute("UPDATE teamless_merge SET new_id = %s WHERE old_id = %s",
                            (moved, old_id))
        cur.execute("DROP TABLE IF EXISTS tm_map")
        # a decision whose X has grown a real-team row since is left alone:
        # the profile is somebody's career now, and that is for a human
        cur.execute(f"""
            CREATE TEMP TABLE tm_map AS
            SELECT m.old_id, m.new_id FROM teamless_merge m
            WHERE  m.old_id NOT IN (SELECT old_id FROM teamless_veto)
              AND  NOT EXISTS (SELECT 1 FROM results r
                               WHERE r.person_id = m.old_id AND NOT {teamlessSql('r')})
              AND  NOT EXISTS (SELECT 1 FROM results_tf r
                               WHERE r.person_id = m.old_id AND NOT {teamlessSql('r')})
        """)
        cur.execute("SELECT m.old_id FROM teamless_merge m WHERE m.old_id NOT IN "
                    "(SELECT old_id FROM tm_map) AND m.old_id NOT IN "
                    "(SELECT old_id FROM teamless_veto)")
        stuck = [r[0] for r in cur.fetchall()]
        if stuck:
            print(f"[teamless] ⚠ {len(stuck)} merged profiles have since grown a "
                  f"real-team row and were NOT re-applied (check them; --undo "
                  f"<id> if the merge was wrong): {stuck[:10]}")
        for sport, table in TABLES:
            cur.execute(f"""
                INSERT INTO person_link_log (sport, result_id, from_person,
                                             to_person, rule)
                SELECT %s, r.result_id, m.old_id, m.new_id, %s
                FROM   {table} r JOIN tm_map m ON r.person_id = m.old_id
                WHERE  r.source = 'anet' AND r.athlete_id = m.old_id
                ON CONFLICT DO NOTHING
            """, (sport, RULE))
            cur.execute(f"""
                UPDATE {table} r SET person_id = m.new_id
                FROM   tm_map m
                WHERE  r.person_id = m.old_id
                  AND  r.source = 'anet' AND r.athlete_id = m.old_id
            """)
            out[f"{sport} rows moved"] = cur.rowcount
        cur.execute("""
            UPDATE athletes a SET person_id = m.new_id
            FROM   tm_map m
            WHERE  a.athlete_id = m.old_id AND a.person_id = m.old_id
        """)
        out["athletes rows repointed"] = cur.rowcount
        cur.execute("""
            INSERT INTO person_redirect (old_id, new_id, n_probes)
            SELECT old_id, new_id, 0 FROM tm_map
            ON CONFLICT (old_id) DO UPDATE SET new_id = EXCLUDED.new_id
            WHERE person_redirect.new_id <> EXCLUDED.new_id
        """)
        out["redirects written"] = cur.rowcount
    conn.commit()
    return out


def undo(conn, which):
    """which: 'all' or one old id (that one is also vetoed)."""
    with conn.cursor() as cur:
        if not _hasTable(cur, "teamless_merge"):
            print("[teamless] nothing to undo")
            return
        cur.execute(MERGE_DDL)
        one = None if which == "all" else int(which)
        cond = "" if one is None else "AND l.from_person = %(one)s"
        back = {}
        for sport, table in TABLES:
            cur.execute(f"""
                UPDATE {table} r SET person_id = l.from_person
                FROM   person_link_log l
                WHERE  l.sport = %(sport)s AND l.rule = %(rule)s {cond}
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person
            """, {"sport": sport, "rule": RULE, "one": one})
            back[sport] = cur.rowcount
        mcond = "" if one is None else "AND m.old_id = %(one)s"
        cur.execute(f"""
            UPDATE athletes a SET person_id = a.athlete_id
            FROM   teamless_merge m
            WHERE  a.athlete_id = m.old_id AND a.person_id = m.new_id {mcond}
        """, {"one": one})
        back["athletes"] = cur.rowcount
        if _hasTable(cur, "person_redirect"):
            cur.execute(f"""
                DELETE FROM person_redirect p USING teamless_merge m
                WHERE p.old_id = m.old_id AND p.new_id = m.new_id {mcond}
            """, {"one": one})
        cur.execute(f"DELETE FROM person_link_log l WHERE l.rule = %(rule)s {cond}",
                    {"rule": RULE, "one": one})
        cur.execute(f"DELETE FROM teamless_merge m WHERE true {mcond}", {"one": one})
        if one is not None:
            cur.execute("INSERT INTO teamless_veto (old_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING", (one,))
    conn.commit()
    print(f"[teamless] undone {which}: rows put back {back}"
          + ("; vetoed, the next run leaves it alone" if which != "all" else ""))


# ------------------------------------------------------------------ #
#  THE REPORT
# ------------------------------------------------------------------ #
def _rowText(r):
    return (f"{str(r.date)[:10]} {r.sport} {str(r.meet or '?')[:38]} "
            f"[{r.school or '-'}; gr {r.grade or '-'}; "
            f"{'%.1f' % r.rating if r.rating is not None else 'unrated'}]")


def report(cands, verdicts, show):
    reasons = Counter(v.reason for v in verdicts.values())
    print(f"[teamless] {len(verdicts):,} candidates judged:")
    for why, n in reasons.most_common():
        print(f"    {n:>8,}  {why}")
    matches = [(pid, v) for pid, v in verdicts.items() if v.target is not None]
    print(f"\n[teamless] {len(matches):,} would merge (showing {min(show, len(matches))}):")
    for pid, v in sorted(matches)[:show]:
        c = cands[pid]
        print(f"  /athlete/{pid} {c.name} ({len(c.rows)} rows) -> /athlete/{v.target} "
              f"{v.school or '?'}   evidence: {'; '.join(v.evidence)}")
        for r in c.rows[:3]:
            print(f"        {_rowText(r)}")
    per = max(3, show // 8)
    for why, _n in reasons.most_common():
        if why == MATCH:
            continue
        sample = [(pid, v) for pid, v in verdicts.items() if v.reason == why][:per]
        print(f"\n  refused, {why} (e.g.):")
        for pid, v in sample:
            c = cands[pid]
            extra = f" touched {list(v.touched)[:4]}" if v.touched else ""
            ev = f" [{'; '.join(v.evidence)}]" if v.evidence else ""
            print(f"    /athlete/{pid} {c.name}{extra}{ev}")
            if c.rows:
                print(f"        {_rowText(c.rows[0])}")
    return matches


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--seasons", type=int, default=DEFAULT_SEASONS,
                    help="academic years back a teamless row may be (default 3)")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--person", type=int, help="judge one anet profile, any year")
    ap.add_argument("--undo", metavar="ID|all")
    a = ap.parse_args()
    if os.environ.get("XCP_LINK_TEAMLESS", "1") in ("0", "false"):
        print("[teamless] XCP_LINK_TEAMLESS=0 -- skipped")
        return
    from database import getConn
    with getConn() as conn:
        if a.undo:
            undo(conn, a.undo)
            return
        since = sinceFor(a.seasons)
        print(f"[teamless] {'APPLY' if a.apply else 'DRY RUN'}; teamless rows "
              f"since {since}" + (f"; profile {a.person}" if a.person else ""))
        with conn.cursor() as cur:
            cands, namesakes, counts = gather(cur, since, a.person)
        conn.rollback()                         # temp tables only
        for k, n in counts.items():
            print(f"    {k:<40} {n:>10,}")
        verdicts = judge(cands, namesakes)
        if a.person and a.person not in cands:
            with conn.cursor() as cur:
                if _hasTable(cur, "teamless_merge"):
                    cur.execute("SELECT new_id, evidence, merged_at FROM "
                                "teamless_merge WHERE old_id = %s", (a.person,))
                    got = cur.fetchone()
                    if got:
                        print(f"  {a.person} was merged into /athlete/{got[0]} "
                              f"on {got[2]:%Y-%m-%d} ({got[1]})")
                    cur.execute("SELECT 1 FROM teamless_veto WHERE old_id = %s",
                                (a.person,))
                    if cur.fetchone():
                        print(f"  {a.person} is VETOED (--undo {a.person}); "
                              f"DELETE FROM teamless_veto to let it be judged again")
            conn.rollback()
        if a.person and a.person in cands:
            for t in namesakes[a.person]:
                print(f"  namesake /athlete/{t.pid} {t.name} {t.gender}: "
                      f"{len(t.rows)} real-team rows in range")
        matches = report(cands, verdicts, a.show)
        decisions = [(pid, v.target, cands[pid].name, "; ".join(v.evidence))
                     for pid, v in matches]
        if not a.apply:
            print(f"\n[teamless] dry run: {len(decisions):,} profiles would merge; "
                  f"--apply to merge them")
            return
        out = write(conn, decisions)
        print(f"\n[teamless] {out}; logged in person_link_log rule '{RULE}' "
              f"(--undo <id> | all)")


if __name__ == "__main__":
    main()
