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

import urllib.parse

MIN_ATHLETES = 3
MIN_SHARE = 0.10

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
                # ! WHETHER THE ASSIGNMENT TABLE EXISTS, asked once per
                #   process here rather than per query: stateFilterSql is a
                #   pure SQL builder with no cursor, and a clause naming a
                #   table that is not there is an error on every page.
                _LABELS["athlete_state"] = _tableExists(cur, "school_athlete_state")
    except Exception:                    # noqa: BLE001 -- labels are optional
        pass
    _LABELS["loaded"] = True


def schoolLabel(school):
    """'Highland' -> 'Highland (UT)'. The template filter every school
    column renders through. Unknown schools come back untouched."""
    if not school:
        return school
    st = _LABELS["map"].get(school)
    return f"{school} ({st})" if st else school


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


def schoolLabelIn(school, state, trusted=False):
    """'Kingston' on a Missouri race -> 'Kingston (MO)', not the biggest
    Kingston's '(WA)' (owner, 2026-09-06: the Steelville race page
    labelled three Missouri schools WA, MI and CA).

    `trusted`: the state is the ROW's own answer (meet_compile
    .stampSchoolStates), not the meet's -- so it is used as given. See
    contextState."""
    if not school:
        return school
    st = contextState(school, state, trusted)
    return f"{school} ({st})" if st else school


def _collegeState(school):
    college = _LABELS.get("college")
    if not college or not school:
        return None
    try:
        from build_college_directory import lookup
    except ImportError:                  # scripts/ not on the path: no directory
        return None
    return lookup(college, school)


def teamState(school, pool=None, state=None):
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
    if p.startswith("college") or p.startswith("pro"):
        st = _collegeState(school)
        if st:
            return st
    if state:
        clusters = _LABELS.get("clusters") or {}
        share = (clusters.get(school) or {}).get(state)
        if share is not None and share >= CONTEXT_MIN_SHARE:
            return state
    return _LABELS["map"].get(school) or state


def schoolLabelFor(school, pool, state=None):
    """The label for a SEASON: a college-pooled season of a name the
    college directory knows gets the college's state ("Amherst (MA)"),
    whatever the name's biggest cluster is; anything else is the name's
    cluster in context, else its primary. The template filter for season
    lines -- and the same verdict teamState reaches, by construction."""
    if not school:
        return school
    st = teamState(school, pool, state)
    return f"{school} ({st})" if st else school


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
          AND  n_athletes >= %s AND share >= %s{bucket}
        ORDER  BY n_athletes DESC
    """
    args = (school, state or "", MIN_ATHLETES, MIN_SHARE)
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
    clusters = schoolClusters(cur, school)
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
    clause = (f" AND COALESCE({assigned}"
              f"(SELECT ph.state FROM person_home_state ph"
              f" WHERE ph.person_id = {alias}.person_id),"
              f" %(sf_primary)s) = %(sf_state)s")
    return clause, params


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
