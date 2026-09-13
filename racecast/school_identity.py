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


def contextState(school, state=None):
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
    if state:
        clusters = _LABELS.get("clusters") or {}
        share = (clusters.get(school) or {}).get(state)
        if share is not None and share >= CONTEXT_MIN_SHARE:
            return state
    return _LABELS["map"].get(school)


def schoolLabelIn(school, state):
    """'Kingston' on a Missouri race -> 'Kingston (MO)', not the biggest
    Kingston's '(WA)' (owner, 2026-09-06: the Steelville race page
    labelled three Missouri schools WA, MI and CA)."""
    if not school:
        return school
    st = contextState(school, state)
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


def stateChips(cur, school):
    """(chips, primary_state). Chips only when a SECOND real cluster
    exists -- one-state schools get no chip row at all."""
    clusters = schoolClusters(cur, school)
    primary = clusters[0]["state"] if clusters else None
    real = [c for c in clusters
            if c["n"] >= MIN_ATHLETES and float(c["share"]) >= MIN_SHARE]
    return (real if len(real) >= 2 else []), primary


def stateFilterSql(alias, state, primary):
    """(clause, params) restricting rows to athletes ASSIGNED to
    `state`: their home state matches, or they are unassigned and this
    is the primary chip (unknowns follow the majority)."""
    if not state:
        return "", {}
    clause = (f" AND COALESCE((SELECT ph.state FROM person_home_state ph"
              f" WHERE ph.person_id = {alias}.person_id),"
              f" %(sf_primary)s) = %(sf_state)s")
    return clause, {"sf_state": state, "sf_primary": primary or ""}


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
