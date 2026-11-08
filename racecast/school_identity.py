"""
school_identity.py -- which real-world school a school STRING means.

★ THE DATA HAS NO SCHOOL IDS. `school` is free text from two scrapers,
  and `state` on a row is the VENUE's state, not the school's. So
  identity is inferred, and the inference is solid because kids race
  mostly at home: an athlete's HOME STATE is the state they race in
  most, and a school string's identities are its athletes clustered by
  home state. Plain "Highland" resolving to 60 UT athletes and 45 CA
  ones is two schools wearing one string.

★ THE LABEL IS THE DEFAULT, RAW TABLES STAY RAW. Every school renders
  as "Name (ST)" site-wide (the schoolLabel filter), but the qualified
  name lives only in DERIVED layers -- these two tables, search_index,
  the rendered pages. Rewriting results/results_tf would fight the
  scrapers on every re-scrape and break every override keyed on the
  raw string.

Tables (rebuilt by racecast/build_school_identity.py, pipeline 10b):

    person_home_state (person_id PK, state)
    school_identity   (school, state, n_athletes, share, is_primary)

A name SPLITS (state chips on its page, separate team rows in meet
scoring, separate search hits) only where a second cluster is real:
MIN_ATHLETES athletes and MIN_SHARE of the school. Below that, away
meets and transfers would mint phantom schools.

Everything here degrades: tables missing (mid-rebuild, old database)
means no chips, no splits, plain labels -- never an error.
"""

import re
import urllib.parse

MIN_ATHLETES = 3
MIN_SHARE = 0.10
COLLEGE_CHIP_MIN = 25     # a college level's chip, whatever its share (levelChips)

# the site-wide label cache: school -> primary state. Loaded once per
# process from school_identity; a few MB for the whole school universe.
_LABELS = {"loaded": False, "map": {}}


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    row = cur.fetchone()
    return (row[0] if not isinstance(row, dict)
            else row.get("to_regclass")) is not None


def loadLabels(conn_factory, force=False):
    """Fill the label cache from school_identity. Safe to call always:
    a missing table loads an empty map and plain names render."""
    if _LABELS["loaded"] and not force:
        return
    try:
        with conn_factory() as conn:
            with conn.cursor() as cur:
                if not _tableExists(cur, "school_identity"):
                    _LABELS["loaded"] = True
                    return
                cur.execute("""
                    SELECT school, state FROM school_identity
                    WHERE  is_primary
                """)
                _LABELS["map"] = {r[0]: r[1] for r in cur.fetchall()}
                # every cluster too, for a label in a known context
                cur.execute("SELECT school, state, share FROM school_identity")
                clusters = {}
                for sc, st, share in cur.fetchall():
                    clusters.setdefault(sc, {})[st] = float(share or 0.0)
                _LABELS["clusters"] = clusters
                # ★ THE COLLEGE DIRECTORY (211), for a college season's label
                #   (2026-09-06: an Amherst College runner's seasons read
                #   "Amherst (WI)" -- the name's biggest cluster is a
                #   Wisconsin high school). Keyed by the directory's own
                #   normalised name.
                if _tableExists(cur, "college_directory"):
                    from build_college_directory import loadDirectory
                    _LABELS["college"] = loadDirectory(cur, "state")
                    # and its full names, for a college a high school shares
                    # its name with (collegeFullName)
                    _LABELS["college_name"] = loadDirectory(cur, "name")
                # ★ NAMES THAT ARE SCHOOLS OF MORE THAN ONE LEVEL (owner,
                #   2026-11-05: Calvin the Michigan university and the Oklahoma
                #   and Louisiana high schools), ten athletes or more a level
                if _tableExists(cur, "school_level"):
                    cur.execute("""SELECT school FROM school_level
                                   WHERE NOT COALESCE(is_bucket, false) AND n_athletes >= 10
                                   GROUP BY school HAVING count(DISTINCT level) > 1""")
                    _LABELS["level_split"] = {r[0] for r in cur.fetchall()}
                # ! WHETHER THE ASSIGNMENT TABLE EXISTS, asked once per
                #   process here rather than per query: stateFilterSql is a
                #   pure SQL builder with no cursor, and a clause naming a
                #   table that is not there is an error on every page.
                _LABELS["athlete_state"] = _tableExists(cur, "school_athlete_state")
                # the towns behind athletic.net's short brackets ("(SB)")
                if _tableExists(cur, "school_qualifier_city"):
                    cur.execute("SELECT school, city FROM school_qualifier_city")
                    _LABELS["qual_city"] = {r[0]: r[1] for r in cur.fetchall()}
                # the runners of the college names two states share
                # (build_school_identity.buildCollegePersonState)
                if _tableExists(cur, "college_person_state"):
                    cur.execute("SELECT school, person_id, state FROM college_person_state")
                    person, size = {}, {}
                    for sc, pid, st in cur.fetchall():
                        person[(sc, pid)] = st
                        size.setdefault(sc, {})
                        size[sc][st] = size[sc].get(st, 0) + 1
                    _LABELS["college_person"] = person
                    _LABELS["college_size"] = size
    except Exception:                    # noqa: BLE001 -- labels are optional
        pass
    _LABELS["loaded"] = True


def withTown(school, state):
    """withState, with athletic.net's short town bracket replaced by the
    state like every other label: 'San Marcos (SB)' + 'CA' -> 'San Marcos
    (CA)' (owner, 2026-11-08: "I kind of want CA, not really Santa
    Barbara. Like everything else"). Only a bracket the build knows is a
    town (school_qualifier_city) is dropped; else withState."""
    town = (_LABELS.get("qual_city") or {}).get(school)
    if not town:
        return withState(school, state)
    base = _TRAIL_PAREN.sub("", str(school)).rstrip()
    return withState(base, state) if state else base


def schoolLabel(school):
    """'Highland' -> 'Highland (UT)'. The template filter every school
    column renders through. Unknown schools come back untouched."""
    if not school:
        return school
    st = _LABELS["map"].get(school)
    return withTown(school, st)


# a same-named school in the context's own state needs at least this share
# of the name's athletes to be the one meant (a stray away meet is not a
# cluster); below it the primary state stands
CONTEXT_MIN_SHARE = 0.03


def contextState(school, state=None, trusted=False):
    """WHICH school a mention means, given where the mention appears.

    ★ THE ONE RESOLVER FOR A MENTION (owner, 2026-09-13: a race page showed
      Hope (AR) wearing Hope (RI)'s crest). The label, the link and the
      crest each used to work this out their own way, so one row could
      answer three different questions -- and the badge is the answer that
      shows. schoolLabelIn labels with this and school_logo picks with it,
      so they cannot disagree.

    The name's cluster in the context's state wins when it is real; else
    the primary. None when the identity cannot place the name at all --
    and NOT the context state, because a row's state is the VENUE's."""
    if not school:
        return None
    # ★ A TRUSTED STATE IS NOT A CONTEXT (owner, 2026-09-16: the race page
    #   still said Williams (CA) after every row was stamped with its
    #   athlete's own cluster). CONTEXT_MIN_SHARE exists to stop a stray
    #   AWAY MEET's state from labelling a school -- it is a bar on a GUESS.
    #   school_athlete_state is not a guess: it is the same assignment the
    #   clusters were counted from, per athlete, so re-judging it by size is
    #   how a small school loses to a big one wearing its name. Williams
    #   College is a few dozen athletes against a California high school's
    #   1,401, i.e. under 3%, so the bar discarded the right answer and
    #   returned the primary.
    if trusted and state:
        return str(state).upper()
    if state:
        clusters = _LABELS.get("clusters") or {}
        share = (clusters.get(school) or {}).get(state)
        if share is not None and share >= CONTEXT_MIN_SHARE:
            return state
    return _LABELS["map"].get(school)


def splitsByState(school):
    """Does this name cover more than one school?

    ★ ASKED BY THE CREST (owner, 2026-09-14: "the Oregon (IL) thing still
      isn't fixed ... I also see it for Williams"). A name with one school
      may wear the one crest stored for it whatever state the mention came
      from -- that is the common case and it must keep working. A name with
      TWO cannot: the crest stored under OR is the University of Oregon's,
      and putting it beside Oregon (IL) is the bug.

    Reads the same cluster cache contextState does, at the same bar, so
    the two cannot disagree about how many Oregons there are."""
    clusters = (_LABELS.get("clusters") or {}).get(school) or {}
    return sum(1 for share in clusters.values()
               if share >= CONTEXT_MIN_SHARE) > 1


def _isTeam(school):
    try:
        from panels import isTeamName
    except ImportError:                  # panels not importable: plain words only
        return str(school).strip().lower() not in ("unknown", "unattached", "none", "-")
    return isTeamName(school)


def withState(school, state):
    """'Xavier' + 'NY' -> 'Xavier (NY)'; 'Xavier (NY)' + 'NY' -> unchanged.

    ★ NO DOUBLE SUFFIX (2026-10-02, NXN 2024's team scores read "Xavier (NY)
      (NY)"). Some team names arrive with their state already in them, and
      every label site appended it again. Every "(ST)" the site writes goes
      through here."""
    if not school or not state:
        return school
    # ★ NO STATE ON A NAME THAT IS NO TEAM (owner, 2026-10-08: the Purple
    #   Valley XC Classic's unattached runners read "Unknown (WA)" -- the
    #   state of the biggest cluster of runners filed under "Unknown").
    #   "Unknown", "Unattached", a country or a pro club names no school, so
    #   it has no state to give; panels.isTeamName is the site's one test.
    if not _isTeam(school):
        return school
    # ! A NUMBER IS NOT A STATE (owner, 2026-11-08, /athlete/19764025: "Guam
    #   (45)"). Outside the states the feed's region field can hold a numeric
    #   id; a label says nothing rather than print it.
    if any(ch.isdigit() for ch in str(state)):
        return school
    tail = f"({str(state).strip()})".lower()
    if str(school).rstrip().lower().endswith(tail) or namesItsState(school):
        return school
    return f"{school} ({state})"


# ★ A NAME THAT ALREADY SAYS ITS STATE, IN ANY SPELLING (owner, 2026-10-08:
#   "St. Joseph's (Me.) (NY)"; "Franciscan (Ohio) (OH)" on the Fairborn
#   page). tfrrs names colleges AP-style -- (Me.), (Ohio), (Wis.), (N.Y.),
#   (Calif.) -- and a second suffix only contradicts or repeats it. Only a
#   STATE in the parentheses counts: "(Club)" or "(Varsity)" say nothing
#   about where the school is.
_AP_STATES = {
    "ala", "alaska", "ariz", "ark", "calif", "cal", "colo", "conn", "del", "dc",
    "d.c", "fla", "ga", "hawaii", "idaho", "ill", "ind", "iowa", "kan", "kans",
    "ky", "la", "maine", "me", "md", "mass", "mich", "minn", "miss", "mo",
    "mont", "neb", "nebr", "nev", "n.h", "nh", "n.j", "nj", "n.m", "nm", "n.y",
    "ny", "n.c", "nc", "n.d", "nd", "ohio", "okla", "ore", "oreg", "pa", "penn",
    "r.i", "ri", "s.c", "sc", "s.d", "sd", "tenn", "texas", "tex", "utah", "vt",
    "va", "wash", "w.va", "w. va", "wis", "wisc", "wyo",
    # 2026-11-02: "Charleston (W.V.) (WV)" -- tfrrs's own spelling
    "w.v", "w. v", "n.mex", "n. mex", "s.dak", "s. dak", "n.dak", "n. dak",
    "n. h", "n. j", "n. y", "n. c", "s. c", "r. i",
}
_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}
_TRAIL_PAREN = re.compile(r"\(([^()]+)\)\s*$")


def namesItsState(school):
    """True when the name ends in a parenthesised US state, any spelling:
    '(Me.)', '(Ohio)', '(N.Y.)', '(CA)', '(New York)'."""
    m = _TRAIL_PAREN.search(str(school or ""))
    if not m:
        return False
    inner = m.group(1).strip().rstrip(".").lower()
    # ! A STATE CODE, NOT ANY TWO LETTERS (owner, 2026-11-08: "San Marcos
    #   (SB)" -- athletic.net's bracket for Santa Barbara -- passed as its own
    #   state, so no "(CA)" was ever added)
    if len(inner) == 2 and inner.isalpha():
        from school_name import STATE_CODES
        return inner.upper() in STATE_CODES
    return inner in _AP_STATES or inner in _STATE_NAMES


def collegeFullName(school, state=None):
    """The college directory's own name for a college whose name a school
    of another level also carries ("Calvin" -> "Calvin University"), else
    None. Only those: Tufts stays Tufts (owner, 2026-11-05: "I'd prefer
    them to show as Calvin University if they are in the University")."""
    if not school or school not in (_LABELS.get("level_split") or ()):
        return None
    names = _LABELS.get("college_name")
    if not names:
        return None
    try:
        from build_college_directory import lookup
        full = lookup(names, school, state)
    except Exception:                                   # noqa: BLE001
        return None
    return full if full and str(full).strip() and full != school else None


def schoolLabelIn(school, state, trusted=False, pool=None):
    """'Kingston' on a Missouri race -> 'Kingston (MO)', not the biggest
    Kingston's '(WA)' (owner, 2026-09-06: the Steelville race page
    labelled three Missouri schools WA, MI and CA).

    `trusted`: the state is the ROW's own answer (meet_compile
    .stampSchoolStates), not the meet's -- so it is used as given. See
    contextState.

    `pool`: the row's level, when the page knows it. ★ A COLLEGE ROW IS THE
    COLLEGE'S (owner, 2026-11-05, school_label_audit: 152 college names --
    Yale, Dartmouth, Notre Dame -- a page without the level shows under a
    high school's state, e.g. a Yale runner at a Massachusetts meet read
    "Yale (MI)"): with a college pool the college directory answers
    (teamState); without one, as before."""
    if not school:
        return school
    p = str(pool or "").lower()
    if p.startswith(("college", "pro")):
        st = teamState(school, pool, state)
        return withState((collegeFullName(school, st) if p.startswith("college") else None)
                         or school, st)
    st = contextState(school, state, trusted)
    return withTown(school, st)


def _collegeState(school, state=None, person_id=None):
    """The directory's state for a college name, or None when it does not
    know the name.

    ★ A NAME TWO COLLEGES SHARE IS STILL ONE OF THEIR STATES (owner,
      2026-10-19: the college men's board read "Georgetown (TX)").
      Georgetown is DC and KY in the directory, so the plain lookup gave
      up, and teamState fell through to the name's biggest cluster: a
      Texas high school. Now: the row's own state when it is one of the
      candidates (Georgetown College racing at home in KY), else the
      candidate where the name's athletes cluster most (the university's
      DC), else the first -- never a state no college of that name is
      in."""
    college = _LABELS.get("college")
    if not college or not school:
        return None
    try:
        from build_college_directory import lookup, candidateStates
    except ImportError:                  # scripts/ not on the path: no directory
        return None
    st = lookup(college, school, state)
    if st:
        return st
    cands = candidateStates(college, school)
    if not cands:
        return None
    # ★ THE RUNNERS DECIDE (owner, 2026-11-01: "it should always be runner
    #   count"). A runner of this name's team in one candidate state is
    #   that team's wherever they race (build_school_identity.
    #   buildCollegePersonState); with no runner to ask, the candidate with
    #   more college runners. The name's clusters count every athlete of the
    #   name -- a Georgia high school called Wesleyan out-counts the CT
    #   university -- so they decide only when neither is known.
    if person_id is not None:
        own = (_LABELS.get("college_person") or {}).get((school, person_id))
        if own in cands:
            return own
    if state in cands:
        return state
    size = (_LABELS.get("college_size") or {}).get(school) or {}
    if any(size.get(c) for c in cands):
        return max(cands, key=lambda c: (size.get(c, 0), -cands.index(c)))
    clusters = (_LABELS.get("clusters") or {}).get(school) or {}
    return max(cands, key=lambda c: (clusters.get(c, 0.0), -cands.index(c)))


def teamState(school, pool=None, state=None, person_id=None):
    """The state a school BELONGS to, given a row that happened in `state`.

    ★ THE ANSWER TO "WHICH TEAM IS THIS", NOT "WHERE DID THEY RACE".
      ranking_results.state -- and so athlete_season.state, which is the
      MODE of a season's rows -- is where the RESULT happened. A college
      races away most weekends, so the mode is a travel state: Air Force
      came out OK, Oregon CA, Furman FL. build_team_season keys a team
      (school, state) via team_rank.teamKey, so one squad became several
      teams sharing a name -- BYU held ranks 5, 7 and 8 of the college
      board as WI, OK and FL, with five and six athletes each instead of
      one BYU with seventeen (owner, 2026-09-08). That breaks the
      invariant build_team_season states in its own docstring: "A team
      sits in exactly one state ... two rows per team, never more."

    ! IT IS NOT JUST primaryState(). Two genuinely different schools share
      a name -- Kingston WA and Kingston MO -- and (school, state) is what
      separates them; collapsing everything to the primary would merge two
      real teams into one. The clustering already drew that line:
      build_school_identity merges co-racing clusters of one name (BYU)
      and leaves clusters that never share a race apart (the Kingstons).
      So a context state that IS one of the name's own clusters is kept,
      and only a state the name has no cluster in -- a travel state --
      falls back to the primary. Same rule, same threshold, as
      schoolLabelIn.

    ! AND THE SAME PRECEDENCE AS THE LABEL, deliberately: schoolLabelFor
      is now a thin wrapper over this, so the state a board KEYS a team by
      and the state it SHOWS cannot disagree. A second lookup would be a
      second answer -- see primaryState.
    """
    if not school:
        return state
    p = (pool or "").lower()
    college = p.startswith("college") or p.startswith("pro")
    if college:
        st = _collegeState(school, state, person_id)
        if st:
            return st
    if state:
        clusters = _LABELS.get("clusters") or {}
        share = (clusters.get(school) or {}).get(state)
        # ★ A SCHOOL-LEVEL ROW'S OWN STATE STANDS IF THE NAME HAS A SCHOOL
        #   THERE AT ALL (owner, 2026-10-05: a grade-5 runner from Amherst,
        #   Wisconsin labelled "Amherst (MA)" and filed with Amherst MA).
        #   The 3% bar is for a COLLEGE's travel state; a high school,
        #   middle or elementary season is raced at home, and a cluster in
        #   that state -- counted from athletes' home states, which an away
        #   meet does not create -- is a real school of that name, however
        #   small beside the name's biggest one.
        if share is not None and (share >= CONTEXT_MIN_SHARE
                                  or (not college and share > 0)):
            return state
    return _LABELS["map"].get(school) or state


def hasCluster(school, state):
    """Whether the name has a school (a cluster of its athletes) in `state`."""
    clusters = _LABELS.get("clusters") or {}
    return bool(state) and (clusters.get(school) or {}).get(state) is not None


def schoolLabelFor(school, pool, state=None):
    """The label for a SEASON: a college-pooled season of a name the
    college directory knows gets the college's state ("Amherst (MA)"),
    whatever the name's biggest cluster is; anything else is the name's
    cluster in context, else its primary. The template filter for season
    lines -- and the same verdict teamState reaches, by construction."""
    if not school:
        return school
    st = teamState(school, pool, state)
    # ★ THE FULL NAME KEEPS ITS STATE (owner, 2026-11-05: "Wake Forest
    #   University" with no state beside "Tennessee (TN)" on the college
    #   team board -- "I would rather have it"). Every college row reads
    #   "<name> (ST)", whichever name it is called by.
    if str(pool or "").lower().startswith("college"):
        full = collegeFullName(school, st)
        if full:
            return withState(full, st)
    return withState(school, st)


def schoolHref(school, state=None, pool=None, sport=None, trusted=False):
    """The URL for a MENTION of a school -- the same school the label
    names and the crest pictures.

    ★ THE LINK WAS THE HALF THAT STAYED STATELESS (owner, 2026-09-14:
      "Oregon (OR) and Oregon (IL) still go to same page with hs logo").
      race.html labelled the row with schoolLabelIn(header.state) and,
      after the crest fix, drew the crest with the same context -- and
      then wrote a bare /school/Oregon under both. Two schools, one page,
      and the page picked the bigger one. A mention now resolves ONCE and
      the label, the crest and the href all come off that answer.

    ! THE LEVEL RIDES ALONG WHEN THE POOL KNOWS IT. Amherst (MA) is a
      NESCAC college and a regional middle school; a college row's link
      says ?level=college so the page opens on the right institution
      rather than on whichever has more athletes. The route drops a level
      the school does not actually split on, so this is never wrong, only
      sometimes redundant.
    """
    if not school:
        return "#"
    # ★ NO LINK TO A PAGE THAT 404s (Search Console, 2026-11-03: 7,865
    #   "Not found" -- clubs, "48-Unattached", a time in the school column).
    #   The route refuses a name panels.isTeamName refuses; so does the link.
    try:
        from panels import isTeamName
        if not isTeamName(school):
            return "#"
    except Exception:                                   # noqa: BLE001
        pass
    st = (teamState(school, pool, state) if pool
          else contextState(school, state, trusted))
    # ! quote(safe="/") is exactly what Jinja's |urlencode did here, and
    #   the route is a <path:> converter -- a school string genuinely
    #   contains a slash ("Chisago Lakes/Rush City").
    q = []
    if sport:
        q.append(("sport", sport))
    if st:
        q.append(("state", st))
    lvl = levelOf(pool)
    if lvl:
        q.append(("level", lvl))
    url = "/school/" + urllib.parse.quote(str(school), safe="/")
    return url + ("?" + urllib.parse.urlencode(q) if q else "")


def stateFor(school, preferred=None):
    """The state a school label should carry: the preferred one when the
    school has a cluster there, else its primary, else the preferred."""
    if not school:
        return preferred
    if preferred:
        share = ((_LABELS.get("clusters") or {}).get(school) or {}).get(preferred)
        if share is not None and share >= CONTEXT_MIN_SHARE:
            return preferred
    return _LABELS["map"].get(school) or preferred


def primaryState(school):
    """'Tufts' -> 'MA'. The school's HOME state, or None.

    ★ THE ROW'S OWN state COLUMN IS NOT THIS. ranking_results.state is where
      the RESULT happened, so one school renders as many states -- a Tufts
      board showed "Tufts IA", "Tufts CT" and "Tufts MA" down a single
      column, which reads as three schools and is one. The identity table
      already answers the question; nothing was asking it.

    ! SAME MAP schoolLabel USES, so the board and every template that renders
      through the school_label filter cannot disagree about where a school
      is. A second lookup would be a second answer.
    """
    if not school:
        return None
    return _LABELS["map"].get(school)


def schoolClusters(cur, school):
    """[{'state', 'n', 'share', 'is_primary'}] biggest first, from the
    identity table, else computed live (mid-rebuild)."""
    if _tableExists(cur, "school_identity"):
        cur.execute("""
            SELECT state, n_athletes AS n, share, is_primary
            FROM   school_identity
            WHERE  school = %(school)s
            ORDER  BY n_athletes DESC
        """, {"school": school})
        raw = cur.fetchall()
        if raw and isinstance(raw[0], dict):
            return raw
        return [{"state": r[0], "n": r[1], "share": r[2],
                 "is_primary": r[3]} for r in raw]
    return _liveClusters(cur, school)


def _liveClusters(cur, school):
    """The same clustering from ranking_results directly: per-athlete
    modal state, grouped. One indexed query over this school's rows."""
    cur.execute("""
        SELECT person_id, state, count(*) AS n
        FROM   ranking_results
        WHERE  school = %(school)s
          AND  person_id IS NOT NULL AND state IS NOT NULL
        GROUP  BY person_id, state
    """, {"school": school})
    per = {}
    raw = cur.fetchall()
    for r in raw:
        pid, st, n = ((r["person_id"], r["state"], r["n"])
                      if isinstance(r, dict) else (r[0], r[1], r[2]))
        best = per.get(pid)
        if best is None or n > best[1]:
            per[pid] = (st, n)
    counts = {}
    for st, _n in per.values():
        counts[st] = counts.get(st, 0) + 1
    total = sum(counts.values()) or 1
    out = [{"state": st, "n": n, "share": n / total, "is_primary": False}
           for st, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    if out:
        out[0]["is_primary"] = True
    return out


# ★ WHICH INSTITUTION (305 / the Amherst split, 2026-09-13). A name and a
#   state can cover two schools -- Amherst College and Amherst Regional
#   Middle School are both "Amherst" in MA -- and only the level tells
#   them apart. Same thresholds as the state split, so the rule is one
#   rule; degrades to [] when school_level has not been built, and the
#   page then reads exactly as it did before.
_LEVEL_LABEL = {"college": "College", "hs": "High school",
                "ms": "Middle school", "elem": "Elementary", "pro": "Pro"}


# ★ A COLLEGE IS AN INSTITUTION AT ANY SHARE (owner, 2026-11-05: "Calvin"
#   in MI is Calvin University -- tfrrs's spelling -- and a high school; the
#   university's runners are a sliver of 1,198 athletes over thirty years,
#   under MIN_SHARE, so no chips, one mixed roster, and the college's NCAA
#   DIII written over the high school). In levelChips a college level with
#   COLLEGE_CHIP_MIN athletes always gets its chip.
def levelChips(cur, school, state):
    """[{level, label, n, share}] widest first, or [] when this school is
    one institution (which is nearly all of them).

    ! NOT is_bucket: "Arkansas" at a state meet is a few hundred high
      schoolers who each belong to a real high school, and a chip for them
      puts the university on a page with its own visitors (owner,
      2026-09-13; the rule is build_school_identity's BUCKET_SHARE)."""
    if not school or not _tableExists(cur, "school_level"):
        return []
    sql = """
        SELECT level, n_athletes, share FROM school_level
        WHERE  school = %s AND state = %s
          AND  ((n_athletes >= %s AND share >= %s)
                OR (level = 'college' AND n_athletes >= %s)){bucket}
        ORDER  BY n_athletes DESC
    """
    args = (school, state or "", MIN_ATHLETES, MIN_SHARE, COLLEGE_CHIP_MIN)
    rows = None
    # a table built before is_bucket existed still answers the old query,
    # and chips without the filter beat no chips at all
    for clause in (" AND NOT is_bucket", ""):
        try:
            cur.execute(sql.format(bucket=clause), args)
            rows = cur.fetchall()
            break
        except Exception:                          # noqa: BLE001 -- optional
            cur.connection.rollback()
    if not rows or len(rows) < 2:
        return []
    out = []
    for r in rows:
        lvl, n, share = ((r["level"], r["n_athletes"], r["share"])
                         if isinstance(r, dict) else r)
        out.append({"level": lvl, "label": _LEVEL_LABEL.get(lvl, (lvl or "").title()),
                    "n": n, "share": float(share or 0)})
    return out


def namesakes(cur, school, state, level=None):
    """[{state, level, n}] the name's schools in OTHER states, ten athletes
    or more, not buckets, biggest first -- linked from the page of the one
    on screen (owner, 2026-11-05: "what happened to the hs page?").

    ! A COLLEGE TRAVELS (owner, 2026-11-07: Duke University's page listed
    Duke (CA), (NJ), (IL), (NY) and (TX) colleges -- Duke itself, filed by
    the states it raced in). school_level keys a college by its race's
    state, so one program is a "college" in every state it visits. On a
    college page no other-state college is listed; on any other page only
    the biggest one, the program's home."""
    if not school or not _tableExists(cur, "school_level"):
        return []
    try:
        cur.execute("""SELECT state, level, n_athletes FROM school_level
                       WHERE school = %s AND state <> %s AND n_athletes >= 10
                         AND NOT COALESCE(is_bucket, false)
                       ORDER BY n_athletes DESC LIMIT 6""", (school, state or ""))
        rows = cur.fetchall()
    except Exception:                                  # noqa: BLE001 -- optional
        cur.connection.rollback()
        return []
    out = []
    college_seen = level == "college"
    for r in rows:
        st, lvl, n = (r["state"], r["level"], r["n_athletes"]) if isinstance(r, dict) else r
        if lvl == "college":
            if college_seen:
                continue
            college_seen = True
        if st and not any(o["state"] == st for o in out):
            out.append({"state": st, "level": lvl, "n": n,
                        "label": _LEVEL_LABEL.get(lvl, (lvl or "").title())})
    return out


def namesakeElsewhere(cur, school, state):
    """Does this name belong to another school of another LEVEL in another
    state (school_level: ten or more athletes, not a bucket)? Calvin is the
    Michigan university and the Oklahoma and Louisiana high schools; a page
    for the bare name is then the main one's only (school_page)."""
    if not school or not state or not _tableExists(cur, "school_level"):
        return False
    try:
        cur.execute("""SELECT 1 FROM school_level o
                       WHERE o.school = %(s)s AND o.state <> %(st)s
                         AND NOT COALESCE(o.is_bucket, false) AND o.n_athletes >= 10
                         AND o.level NOT IN (SELECT level FROM school_level
                                             WHERE school = %(s)s AND state = %(st)s)
                       LIMIT 1""", {"s": school, "st": state})
        return cur.fetchone() is not None
    except Exception:                                  # noqa: BLE001 -- optional
        cur.connection.rollback()
        return False


def hasLevel(cur, school, state, level):
    """Does this school name have athletes at `level` in this state
    (school_level, any number)? A link that names the level is honoured
    then, chips or not (school_page)."""
    if not school or not level or not _tableExists(cur, "school_level"):
        return False
    try:
        cur.execute("""SELECT 1 FROM school_level WHERE school = %s AND state = %s
                         AND level = %s AND n_athletes > 0 LIMIT 1""",
                    (school, state or "", level))
        return cur.fetchone() is not None
    except Exception:                                  # noqa: BLE001 -- optional
        cur.connection.rollback()
        return False


def mainLevel(cur, school, state):
    """The level most of a school name's athletes in this state race at,
    or None. A page with one institution's chips-less view labels itself
    with THAT level's units only (school_page)."""
    if not school or not _tableExists(cur, "school_level"):
        return None
    try:
        cur.execute("""SELECT level FROM school_level WHERE school = %s AND state = %s
                       ORDER BY n_athletes DESC LIMIT 1""", (school, state or ""))
        row = cur.fetchone()
    except Exception:                                  # noqa: BLE001 -- optional
        cur.connection.rollback()
        return None
    if not row:
        return None
    return row["level"] if isinstance(row, dict) else row[0]


def levelOf(pool):
    """The level a pool belongs to: "college_m" -> "college"."""
    return (pool or "").split("|")[0].split("_")[0] or None


def stateChips(cur, school, include=None):
    """(chips, primary_state). Chips only when a SECOND real cluster
    exists -- one-state schools get no chip row at all.

    ⚠ TWO BARS FOR ONE QUESTION WAS THE BUG (owner, 2026-09-14). The label
      and the link name a cluster at CONTEXT_MIN_SHARE (3%); this drew
      chips, and school_page validated ?state=, at MIN_SHARE (10%). So a
      race page labelled a row "Oregon (IL)", linked it to ?state=IL, and
      the page threw the state away and served Oregon (OR) -- the link
      promised a page that did not exist.

      `include` is the state the caller was actually asked for. When it is
      a real cluster the narrow bar happens to hide, the chip row widens to
      the bar that named it, so the page exists AND is reachable from the
      other namesake. Nothing widens for a school nobody asked about, so an
      ordinary page's chips are unchanged."""
    return chipsFrom(schoolClusters(cur, school), include)


def chipsFrom(clusters, include=None):
    """stateChips' rule on clusters already in hand (biggest first) -- so a
    caller holding many schools' clusters from one query applies the same
    bars as the school page, not a second spelling of them."""
    primary = clusters[0]["state"] if clusters else None
    real = [c for c in clusters
            if c["n"] >= MIN_ATHLETES and float(c["share"]) >= MIN_SHARE]
    if include and not any(c["state"] == include for c in real):
        wider = [c for c in clusters
                 if c["n"] >= MIN_ATHLETES
                 and float(c["share"]) >= CONTEXT_MIN_SHARE]
        if any(c["state"] == include for c in wider):
            real = wider
    return (real if len(real) >= 2 else []), primary


def clustersMany(cur, schools):
    """{school: clusters as schoolClusters returns them} for many schools in
    one query; a school with no rows is absent. Mid-rebuild (no table) it
    asks schoolClusters per school, which computes them live."""
    schools = sorted({s for s in schools if s})
    if not schools:
        return {}
    if not _tableExists(cur, "school_identity"):
        return {s: schoolClusters(cur, s) for s in schools}
    cur.execute("""
        SELECT school, state, n_athletes AS n, share, is_primary
        FROM   school_identity
        WHERE  school = ANY(%(schools)s)
        ORDER  BY school, n_athletes DESC
    """, {"schools": schools})
    out = {}
    for r in cur.fetchall():
        row = (r if isinstance(r, dict) else
               {"school": r[0], "state": r[1], "n": r[2], "share": r[3],
                "is_primary": r[4]})
        out.setdefault(row["school"], []).append(
            {"state": row["state"], "n": row["n"], "share": row["share"],
             "is_primary": row["is_primary"]})
    return out


def assignedStates(cur, pairs):
    """{(school, person_id): state} -- where the identity puts each athlete
    for that school name: the school's own assignment (school_athlete_state)
    first, else their home state. A pair neither table knows is ABSENT, not
    guessed: stateFilterSql's last resort, the primary chip, is a guess the
    caller may not want to act on."""
    pairs = {(s, int(p)) for s, p in pairs if s and p}
    if not pairs:
        return {}
    out = {}
    if _LABELS.get("athlete_state") or _tableExists(cur, "school_athlete_state"):
        cur.execute("""
            SELECT school, person_id, state FROM school_athlete_state
            WHERE  school = ANY(%(s)s) AND person_id = ANY(%(p)s)
        """, {"s": sorted({s for s, _p in pairs}),
              "p": sorted({p for _s, p in pairs})})
        for r in cur.fetchall():
            sc, pid, st = ((r["school"], r["person_id"], r["state"])
                           if isinstance(r, dict) else tuple(r))
            if (sc, pid) in pairs and st:
                out[(sc, pid)] = st
    home = homeStates(cur, [p for s, p in pairs if (s, p) not in out])
    for s, p in pairs:
        if (s, p) not in out and home.get(p):
            out[(s, p)] = home[p]
    return out


def stateFilterSql(alias, state, primary, school=None):
    """(clause, params) restricting rows to athletes ASSIGNED to
    `state`: the school's own assignment for them, else their home state,
    else the primary chip (unknowns follow the majority).

    ★ THE ASSIGNMENT COMES FIRST NOW (owner, 2026-09-16: "Oregon(IL) and
      Oregon(or) are colliding"). This used to read person_home_state
      alone -- where the athlete RACES -- so a roster narrowed to
      Oregon (OR) meant "athletes who race mostly in Oregon", which for a
      college is a travel mode: half the university missing, an Illinois
      kid or two added, and the roster not adding up to the chip beside
      it. school_athlete_state (build_school_identity.buildAthleteState)
      is the same COALESCE the clusters were COUNTED from, so the page and
      the counts are one answer.

    ⚠ IT IS ONLY CONSULTED WHEN THE CALLER NAMES THE SCHOOL AND THE TABLE
      EXISTS. Without either, this is byte-for-byte the old clause -- and
      the table holds contested names only, so for every other school the
      fallback IS the answer.
    """
    if not state:
        return "", {}
    params = {"sf_state": state, "sf_primary": primary or ""}
    assigned = ""
    if school is not None and _LABELS.get("athlete_state"):
        assigned = (f"(SELECT sa.state FROM school_athlete_state sa"
                    f" WHERE sa.person_id = {alias}.person_id"
                    f" AND sa.school = %(sf_school)s), ")
        params["sf_school"] = school
    cond = (f"COALESCE({assigned}"
            f"(SELECT ph.state FROM person_home_state ph"
            f" WHERE ph.person_id = {alias}.person_id),"
            f" %(sf_primary)s) = %(sf_state)s")
    # ★ A COLLEGE'S RUNNERS ARE ITS CAMPUS'S (owner, 2026-11-05, Calvin): a
    #   university recruits from other states, and their home states put
    #   Calvin's Illinois and Indiana runners in "Calvin (IL)" and "(IN)".
    #   On the page of the state the directory puts the college in, a college
    #   row is the college's whoever's home it is.
    try:
        campus = _collegeState(school) if school else None
    except Exception:                                   # noqa: BLE001
        campus = None
    if campus and str(campus).upper() == str(state).upper():
        return (f" AND (split_part(split_part(COALESCE({alias}.pool, ''), '|', 1), '_', 1)"
                f" = 'college' OR {cond})"), params
    return f" AND {cond}", params


def homeStates(cur, person_ids):
    """{person_id: state} in one PK-batch query; empty when the table
    is not built yet. Feeds the meet-scoring split."""
    ids = sorted({int(i) for i in person_ids if i})
    if not ids or not _tableExists(cur, "person_home_state"):
        return {}
    cur.execute("""
        SELECT person_id, state FROM person_home_state
        WHERE  person_id = ANY(%(ids)s)
    """, {"ids": ids})
    out = {}
    for r in cur.fetchall():
        pid, st = ((r["person_id"], r["state"]) if isinstance(r, dict)
                   else (r[0], r[1]))
        out[pid] = st
    return out


def _stateNames():
    try:
        from landing import STATE_NAMES
    except ImportError:                  # landing not importable: no state teams
        return frozenset()
    return frozenset(n.lower() for n in STATE_NAMES.values())


def isStateTeamRace(schools, pools=None):
    """True when most of a race's teams are US states ("Arkansas",
    "Texas") -- an all-star or state-team meet.

    ★ A STATE TEAM IS NOT A SCHOOL (owner, 2026-10-07: RunningLane Track
      Championships 2026 read "Arkansas (AL)" -- the label looked up the
      school called Arkansas and appended where it races). On such a race
      the team name already says where it is from, so no suffix is drawn.
      It is a RACE-level verdict, not a name-level one: Oregon (IL) is a
      real high school, and on an ordinary race it keeps its "(IL)".
      "Most" is a plain majority of the race's distinct teams.

    ★ NEVER A COLLEGE RACE (owner, 2026-10-19: NCAA DI Indoors 5000m had
      no crest or link for Alabama, Florida, Oregon, Arkansas, New Mexico,
      West Virginia, North Dakota, Washington). Eight of its fourteen
      teams are universities named for their states -- a majority -- so
      the race read as an all-star meet. State teams are a youth format;
      `pools` (each row's rating pool) says when most rows are college or
      pro, and then no team is a state."""
    if pools is not None:
        ps = [str(p).lower() for p in pools if p]
        grown = sum(1 for p in ps if p.startswith(("college", "pro")))
        if ps and 2 * grown > len(ps):
            return False
    names = _stateNames()
    teams = {str(s).strip().lower() for s in schools if s and str(s).strip()}
    if not teams or not names:
        return False
    return 2 * sum(1 for t in teams if t in names) > len(teams)


def isStateName(school):
    return bool(school) and str(school).strip().lower() in _stateNames()
