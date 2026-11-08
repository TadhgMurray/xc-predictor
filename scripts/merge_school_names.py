#!/usr/bin/env python3
"""
merge_school_names.py -- two spellings, one team, proved by the athletes.

    python scripts/merge_school_names.py                 # DRY RUN, prints it
    python scripts/merge_school_names.py --show 80
    python scripts/merge_school_names.py --prefix        # also the shortenings
    python scripts/merge_school_names.py --write         # school_name_alias

★ THE BUG (owner, 2026-09-17). One athlete, twice on one board, because her
  team is spelled two ways:

    15  Chiara Dailey  La Jolla (CA)  152.2  9:53.38  San Diego HS vs La Jolla HS
    16  Chiara Dailey  La Jolla-CA    152.1  9:49.57  Brooks PR Invitation

  "please make it so team substrings like this are found and merged (where
  some athletes are the same!)"

⚠ THE SUBSTRING ALONE IS EXACTLY THE WRONG RULE, and the same conversation
  proves it: "Oregon" is a word prefix of "Oregon Episcopal", "Oregon Clay"
  and "Oregon School for the Deaf", which are four schools in three states.
  A containment test on its own would merge the set and undo the split this
  session just built.

★ SO THE NAME PROPOSES AND THE ATHLETES DECIDE -- the owner's own
  parenthesis, and the same join every other thing in this project that
  works is built on: person_id is already merged across the feeds, so
  "the same people wear both spellings" needs no spelling.

  Two bars, because the two candidate classes carry different risk:

    same    the spellings differ only in DECORATION -- a state suffix in
            any of the four shapes the feeds use, or a trailing "HS" /
            "College". Cheap to believe: MIN_SHARED athletes, and they must
            be MIN_FRACTION of the smaller spelling's roster.
    prefix  one name is a strict WORD prefix of the other. This is the
            Oregon Episcopal shape, so it is off unless --prefix and it
            needs PREFIX_MIN_SHARED / PREFIX_MIN_FRACTION instead.

⚠ AND THE anet STATE VETOES EITHER. A string whose anet teams sit in more
  than one state is a name several schools wear -- "St Thomas Aquinas",
  "Glendale", "Meridian", "Centennial" -- and is never one team, whoever it
  shares athletes with. Two strings each in ONE state, and the same state,
  are one school. That outranks any count.

  ! NOT THE TEAM ID. The first version vetoed on "different anet teams" and
    refused Peak To Peak / Peak to Peak, RHAM / Rham, St Cloud / St. Cloud --
    one school spelled twice, because ANET ITSELF CARRIES DUPLICATE TEAMS for
    one school. The state is what those duplicates agree on.

WHAT IT WRITES, AND WHAT READS IT
    school_name_alias (variant PK, canonical, n_variant, n_canonical,
                       n_shared, relation, source, built)

! THE RAW TABLES KEEP THE RAW STRINGS. That is this project's standing rule
  (racecast/app.py: "the scrapers would fight anything else; the qualified
  label lives in the derived layer"), and nothing here rewrites results or
  results_tf. build_ranking_results folds the variant onto the canonical as
  it streams, so the BOARDS hold one school and the feeds keep their own
  spelling -- which also means the undo is a rerun with the table dropped.
"""
import argparse
import os
import sys
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "racecast"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from school_name import (canonicalOf, mergeable,            # noqa: E402
                         nameWords, levelWords, statedState)

# a decoration difference: believable on modest evidence
MIN_SHARED = 3
MIN_FRACTION = 0.10
# a shortening: the Oregon Episcopal shape, so it has to be overwhelming
PREFIX_MIN_SHARED = 10
PREFIX_MIN_FRACTION = 0.50

# ★ TWO MORE KINDS OF CANDIDATE (owner, 2026-11-01: NESCAC listed
#   "Conn College" and "Connecticut College", "Trinity" and "Trinity
#   (Conn.)", "Wesleyan" and "Wesleyan (Conn.)" -- "do this next, try to be
#   exhaustive with variants"):
#     level    the names agree once the level words go ("Williams" /
#              "Williams College", "La Jolla" / "La Jolla High School",
#              "Michigan" / "University of Michigan"). The 2026-09-17 dry run
#              is why level words were never noise (Adrian <- Adrian Middle
#              School), so this merges only when BOTH spellings race at the
#              SAME level (athlete_season), a school pair also in one known
#              state, and on a stronger bar.
#     college  the college directory reads both as the same college
#              ("UW-La Crosse" / "Wis.-La Crosse" / "Wisconsin-La Crosse",
#              "SUNY Geneseo" / "Geneseo"); both must race at college level.
#   And the spelling rules themselves widened (school_name: "(Conn.)",
#   St./Saint, Mt./Mount, HS/High School, Conn/Connecticut ...).
LEVEL_MIN_SHARED = 5
LEVEL_MIN_FRACTION = 0.20
# a level a spelling races at for at least this share of its races counts as
# one of its levels (Trinity: high schools and colleges)
MIX_SHARE = 0.05
# a college spelling may fold into a school name that holds this share of
# college races (Roosevelt: thousands of high schoolers and one university)
COLLEGE_MIX = 0.02
# a name several colleges share folds into one of them only on this share
GENERIC_FRACTION = 0.80
# a named college folds into a plain name only when this share of its own
# runners also wear the plain name (Trinity (Conn.): 41%)
PICK_SHARE = 0.35
_LVL_NAMES = {"college", "university", "high", "middle", "junior", "senior", "community"}

DDL = """
CREATE TABLE IF NOT EXISTS school_name_alias (
    variant     text NOT NULL,
    canonical   text NOT NULL,
    n_variant   int  NOT NULL,
    n_canonical int  NOT NULL,
    n_shared    int  NOT NULL,
    relation    text NOT NULL,
    source      text NOT NULL DEFAULT 'coathlete',
    built       date NOT NULL DEFAULT current_date,
    PRIMARY KEY (variant))
"""


def _hasColumn(cur, table, column):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, column))
    return cur.fetchone() is not None


def rosters(cur, min_athletes=2):
    """{school string: {person_id}} from the raw result tables.

    ! FROM results/results_tf, NOT FROM THE BOARDS. This has to run BEFORE
      build_ranking_results -- the boards are what the alias fixes -- so the
      evidence comes from the tables that exist at that point.

    ! DISTINCT PEOPLE, NOT ROWS. One prolific athlete is not a roster.
    """
    out = {}
    for table in ("results", "results_tf"):
        cur.execute(f"""
            SELECT btrim(r.school) AS school, r.person_id
            FROM   {table} r
            WHERE  r.person_id IS NOT NULL
              AND  r.school IS NOT NULL AND btrim(r.school) <> ''
            GROUP  BY 1, 2
        """)
        for school, pid in cur.fetchall():
            out.setdefault(school, set()).add(pid)
    return {k: v for k, v in out.items() if len(v) >= min_athletes}


# ⚠⚠ THE TEAM ID WAS THE WRONG VETO, AND THE SECOND DRY RUN PROVED IT.
#    "different anet teams -> different schools" is false, because ANET
#    ITSELF CARRIES DUPLICATE TEAMS FOR ONE SCHOOL. The refusals:
#
#      Peak To Peak / Peak to Peak   117 shared   [19494] vs [37702]
#      LaVille      / Laville        117 shared   [28785] vs [17035]
#      RHAM         / Rham           110 shared   [14773] vs [58439]
#      DeKalb       / Dekalb         301 shared   [5173,...] vs [11638, 17210]
#      St Cloud     / St. Cloud      458 shared   [15430] vs [40114]
#      Lubbock Cooper / Lubbock-Cooper 346 shared [71151] vs [4495]
#
#    Every one of those is one school spelled two ways, refused because the
#    two spellings landed on two anet rows.
#
# ★ THE STATE IS THE SIGNAL, NOT THE ID. Ask instead how many states a
#   string's anet teams sit in:
#
#     * a string whose teams span MORE THAN ONE state is a generic name worn
#       by several schools -- "St Thomas Aquinas", "Glendale", "Meridian",
#       "Centennial". It is never merged into anything, whoever it shares
#       athletes with.
#     * two strings each in ONE state, and the same state, with a team's
#       worth of shared athletes, are one school.
#
#   That refuses exactly the cases the id veto was built for and admits the
#   ones it got wrong -- and it reads anet_state, the column anet actually
#   populates, not the queue's guess (see collegeTeams in link_tfrrs_to_anet).
def teamStates(cur):
    """{school string: {state}} for the anet teams its rows use. Empty
    where team_id does not exist, which is then simply no veto."""
    out = {}
    for table in ("results", "results_tf"):
        if not _hasColumn(cur, table, "team_id"):
            continue
        cur.execute(f"""
            SELECT btrim(r.school) AS school,
                   upper(btrim(COALESCE(t.anet_state, t.state))) AS st
            FROM   {table} r
            JOIN   anet_team t ON t.team_id = r.team_id
            WHERE  r.team_id IS NOT NULL AND r.team_id <> 0
              AND  r.school IS NOT NULL AND btrim(r.school) <> ''
              AND  COALESCE(t.anet_state, t.state) IS NOT NULL
              AND  btrim(COALESCE(t.anet_state, t.state)) <> ''
            GROUP  BY 1, 2
        """)
        for school, st in cur.fetchall():
            out.setdefault(school, set()).add(st)
    return out


# ★ THE CANDIDATE PAIRS, WITHOUT COMPARING EVERY NAME TO EVERY OTHER.
#
# ⚠ THE FIRST VERSION BUCKETED ON THE FIRST WORD AND WAS STILL QUADRATIC --
#   its own scale test took 90 seconds on 4,000 names, because a common first
#   word ("saint", "north", "central", "mount") puts thousands of schools in
#   one bucket and the pair scan inside it is n². Against the real corpus's
#   hundreds of thousands of strings that is not a slow pass, it is one that
#   never finishes.
#
# ★ NEITHER RELATION NEEDS A PAIR SCAN AT ALL, because both are defined on
#   the KEY:
#
#     same    two names with the SAME key -- a dict grouping, and every
#             group is one team's handful of spellings.
#     prefix  a name whose key is a proper WORD prefix of another's. So walk
#             each name's own prefixes and ask the dict whether any of them
#             is a key somebody else wears. That is one lookup per word, not
#             one per other school.
#
#   Linear in the number of names, and exact -- no bucket, nothing capped.
def candidates(names, want_prefix=False):
    """[(a, b, relation)] for every pair worth asking the athletes about.
    Pure -- takes the names, not a cursor."""
    by_key = {}
    for name in names:
        # ⚠ A ROSTER STATUS NEVER MERGES. "Unattached" and its forty spellings
        #   share athletes with each other because they are the same SENTINEL,
        #   not the same roster -- so the evidence this module runs on means
        #   nothing there. The first dry run folded all forty into one
        #   "school" of several thousand people. See school_name.mergeable.
        if not mergeable(name):
            continue
        words = nameWords(name)
        if words:
            by_key.setdefault(words, []).append(name)

    pairs = []
    for words, group in by_key.items():
        group = sorted(group)
        # every pair of spellings that reduce to one key
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pairs.append((a, b, "same"))
        if not want_prefix:
            continue
        # ...and every SHORTER key that is a word prefix of this one
        for k in range(1, len(words)):
            for a in by_key.get(words[:k], ()):
                for b in group:
                    pairs.append((a, b, "prefix"))
    return sorted(set(pairs))


def levelMix(cur):
    """{school string: {'college' | 'hs' | 'ms': share of its races}} from
    the last boards build (athlete_season). A spelling the boards already
    fold onto another takes its canonical's."""
    cur.execute("SELECT to_regclass('public.athlete_season')")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("""
        SELECT school, split_part(split_part(pool, '|', 1), '_', 1) AS lvl, sum(n_races)
        FROM   athlete_season
        WHERE  COALESCE(btrim(school), '') <> '' AND pool IS NOT NULL
        GROUP  BY 1, 2
    """)
    tot = {}
    norm = {"pro": "college", "college": "college", "hs": "hs", "ms": "ms", "elem": "ms"}
    for school, lvl, n in cur.fetchall():
        lv = norm.get(lvl)
        if lv is not None:
            t = tot.setdefault(school, {})
            t[lv] = t.get(lv, 0) + int(n or 0)
    out = {}
    for sc, t in tot.items():
        n = sum(t.values())
        if n:
            out[sc] = {k: v / n for k, v in t.items()}
    cur.execute("SELECT to_regclass('public.school_name_alias')")
    if cur.fetchone()[0] is not None:
        cur.execute("SELECT variant, canonical FROM school_name_alias")
        for variant, canon in cur.fetchall():
            if variant not in out and canon in out:
                out[variant] = out[canon]
    return out


def levels(cur, mix=None):
    """{school string: its dominant level} (levelMix's biggest share)."""
    mix = levelMix(cur) if mix is None else mix
    return {sc: max(t, key=t.get) for sc, t in mix.items() if t}


def collegeKeys(cur, names, level_of):
    """{school string: (directory name, state)} for the college-level
    spellings the college directory places in one college."""
    try:
        from build_college_directory import loadDirectory, lookup
    except ImportError:
        return {}
    directory = loadDirectory(cur, "name", "state")
    if not directory:
        return {}
    out = {}
    for name in names:
        if level_of.get(name) != "college" or not mergeable(name):
            continue
        hit = lookup(directory, name, statedState(name))
        if hit:
            out[name] = tuple(hit) if isinstance(hit, (list, tuple)) else (hit, None)
    return out


def genericColleges(cur, names, level_of):
    """The college-level spellings the directory says several colleges
    wear, with no state written to pick one ("Concordia University")."""
    try:
        from build_college_directory import loadDirectory, candidateStates
    except ImportError:
        return set()
    directory = loadDirectory(cur, "state")
    return {n for n in names
            if level_of.get(n) == "college" and not statedState(n)
            and len(candidateStates(directory, n)) > 1}


_REL_RANK = {"same": 0, "college": 1, "level": 2, "prefix": 3, "roster": 4, "renamed": 5}

# ★ ONE TEAM BY ITS RUNNERS, WHATEVER THE NAMES (owner, 2026-11-05,
#   /school/U.%20of%20Chicago: "U. of Chicago" and "Chicago", 622 of the
#   smaller one's 646 runners in common, both college -- and no rule
#   proposed them, because every rule starts from the NAME and the college
#   directory placed neither). Two college-level spellings with
#   ROSTER_MIN_SHARED+ runners in common, ROSTER_MIN_FRACTION of the smaller
#   one's, are proposed as a pair; every veto in judge still applies (named
#   states, anet states, levels). Never a club, an alumni or unattached
#   entry: an alumni team's runners ARE its college's, and it is not the team.
ROSTER_MIN_SHARED = 25
ROSTER_MIN_FRACTION = 0.85
# ! "UNAT-Adams State", "UNA-Augustana (S.D.)": that college's runners
#   racing UNATTACHED (a redshirt, an open race) -- the college's people,
#   never its team: folded in, they would score for it (owner's dry run,
#   2026-11-05)
_NOT_A_TEAM = re.compile(r"\bclub\b|alumni|unattached|unaffiliated|\belite\b|\btc\b|"
                         r"track club|running club|striders|\baa\b|\bac\b|"
                         r"^\s*unat?\b|^\s*una?t?\s*-", re.I)


def rosterPairs(rosters_by_name, level_of, min_shared=ROSTER_MIN_SHARED,
                min_fraction=ROSTER_MIN_FRACTION):
    """[(a, b, 'roster')] for college-level spellings that share most of a
    roster. Pure."""
    names = [n for n, r in rosters_by_name.items()
             if level_of.get(n) == "college" and len(r) >= min_shared
             and mergeable(n) and not _NOT_A_TEAM.search(n)]
    by_person = {}
    for n in names:
        for pid in rosters_by_name[n]:
            by_person.setdefault(pid, []).append(n)
    together = {}
    for ns in by_person.values():
        if 1 < len(ns) <= 6:
            ns = sorted(ns)
            for i, a in enumerate(ns):
                for b in ns[i + 1:]:
                    together[(a, b)] = together.get((a, b), 0) + 1
    out = []
    for (a, b), shared in together.items():
        small = min(len(rosters_by_name[a]), len(rosters_by_name[b]))
        if shared >= min_shared and shared >= min_fraction * small:
            out.append((a, b, "roster"))
    return sorted(out)


# ★ A HIGH SCHOOL RENAMED KEEPS ITS RUNNERS (owner, 2026-11-08,
#   /athlete/26510177: Micah Houlihan ran for "Covenant School" in 2024,
#   "Covenant Christian" in 2025 and "The Covenant School of Jacksonville" in
#   2026 -- the same meets each year, three school pages). No name rule
#   proposes those (no shared key, no word prefix), and the college roster
#   rule above is college only. A transfer moves a runner or two; a rename
#   moves the team. So two high-school spellings that share a distinctive
#   word and RENAMED_MIN_SHARED+ runners, RENAMED_MIN_FRACTION of the
#   smaller roster, are proposed; judge then also wants them in ONE known
#   state, the same one, with every other veto as before.
RENAMED_MIN_SHARED = 8
RENAMED_MIN_FRACTION = 0.40
# words a hundred schools wear: a pair sharing only these is not proposed
_COMMON_WORDS = {"the", "of", "school", "schools", "high", "hs", "christian", "academy",
                 "prep", "preparatory", "catholic", "central", "north", "south", "east",
                 "west", "saint", "st", "county", "city", "community", "charter",
                 "regional", "senior", "junior", "middle", "day", "country", "valley",
                 "lake", "mount", "mt", "new", "public", "classical", "christ", "holy",
                 "memorial", "international", "science", "arts", "and", "at", "for"}


def _distinctive(name):
    """A name's words a few schools share at most (len 3+, not common). Pure."""
    return {w for w in nameWords(name) if len(w) >= 3 and w not in _COMMON_WORDS}


def renamedPairs(rosters_by_name, level_of, min_shared=RENAMED_MIN_SHARED,
                 min_fraction=RENAMED_MIN_FRACTION):
    """[(a, b, 'renamed')] for high-school spellings sharing a distinctive
    word and most of a roster. Pure."""
    names = [n for n, r in rosters_by_name.items()
             if level_of.get(n) == "hs" and len(r) >= min_shared
             and mergeable(n) and not _NOT_A_TEAM.search(n)]
    words = {n: _distinctive(n) for n in names}
    by_person = {}
    for n in names:
        if words[n]:
            for pid in rosters_by_name[n]:
                by_person.setdefault(pid, []).append(n)
    together = {}
    for ns in by_person.values():
        if 1 < len(ns) <= 4:
            ns = sorted(ns)
            for i, x in enumerate(ns):
                for y in ns[i + 1:]:
                    if words[x] & words[y]:
                        together[(x, y)] = together.get((x, y), 0) + 1
    out = []
    for (x, y), shared in together.items():
        small = min(len(rosters_by_name[x]), len(rosters_by_name[y]))
        if shared >= min_shared and shared >= min_fraction * small:
            out.append((x, y, "renamed"))
    return sorted(out)


_STATE_WORDS = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado",
                "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
                "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
                "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
                "missouri", "montana", "nebraska", "nevada", "ohio", "oklahoma", "oregon",
                "pennsylvania", "tennessee", "texas", "utah", "vermont", "virginia",
                "washington", "wisconsin", "wyoming"}


def _levelClass(words):
    """The school level a name's own words say, or None: 'middle' (middle
    school, junior high), 'high', 'elementary'. Pure."""
    if "middle" in words or ("junior" in words and "high" in words):
        return "middle"
    if "elementary" in words:
        return "elementary"
    if "high" in words:
        return "high"
    return None


def _qualifier(name):
    """A bracketed qualifier that is not a state ('Bklyn', 'Long Island'),
    lowercased, or None. Pure."""
    import re as _re
    m = _re.search(r"\(([^)]*)\)\s*$", name or "")
    if not m or statedState(name):
        return None
    return " ".join(_re.findall(r"[a-z0-9]+", m.group(1).lower())) or None


def _stateCollege(a, b):
    """One is a bare state name, the other that state + College. Pure."""
    for x, y in ((a, b), (b, a)):
        wx, wy = nameWords(x), nameWords(y)
        if len(wx) == 1 and wx[0] in _STATE_WORDS and "college" in wy and "university" not in wy:
            return True
    return False


def candidatesAll(names, want_prefix=False, level_of=None, college_key=None):
    """candidates() plus the 'level' and 'college' pairs, one relation per
    pair (the strongest). Pure."""
    level_of, college_key = level_of or {}, college_key or {}
    best = {}

    def add(a, b, rel):
        if a == b:
            return
        k = (a, b) if a < b else (b, a)
        if k not in best or _REL_RANK[rel] < _REL_RANK[best[k]]:
            best[k] = rel
    for a, b, rel in candidates(names, want_prefix):
        add(a, b, rel)
    by_level, by_college = {}, {}
    for name in names:
        if not mergeable(name):
            continue
        if level_of.get(name):
            lw = levelWords(name)
            if lw:
                by_level.setdefault((lw, level_of[name]), []).append(name)
        if name in college_key:
            by_college.setdefault(college_key[name], []).append(name)
    for group_map, rel in ((by_level, "level"), (by_college, "college")):
        for group in group_map.values():
            # ! A GENERIC NAME'S BUCKET IS MANY SCHOOLS, NEVER ONE: "central"
            #   at hs level holds hundreds. Skipped rather than scanned.
            if len(group) > 40:
                continue
            group = sorted(group)
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    add(a, b, rel)
    return sorted((a, b, rel) for (a, b), rel in best.items())


# ★ THE RUNNERS' STATE, FOR A RENAMED PAIR WITH NO TEAM RECORD (owner's
#   dry run, 2026-11-08: Lincoln-Sudbury / Lincoln-Sudbury Regional, Reading
#   / Reading Memorial, Plant / H B Plant ... every one refused because one
#   spelling had no athletic.net team to give a state). school_identity
#   places each name by where its runners race; a name ONE_STATE_SHARE of
#   whose runners are in one state is that state's. A name many states wear
#   ("Douglas", "Mount Vernon", "Notre Dame") never reaches the bar.
ONE_STATE_SHARE = 0.90


def identityStates(cur):
    """{school string: {state}} for the names school_identity places in one
    state (ONE_STATE_SHARE of its runners); empty without the table."""
    cur.execute("SELECT to_regclass('public.school_identity')")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("SELECT school, state, share FROM school_identity")
    best = {}
    for school, st, share in cur.fetchall():
        if share is not None and float(share) >= ONE_STATE_SHARE and st:
            best[school] = {str(st).strip().upper()}
    return best


def judge(pairs, rosters_by_name, states_by_name,
          min_shared=MIN_SHARED, min_fraction=MIN_FRACTION,
          prefix_min_shared=PREFIX_MIN_SHARED,
          prefix_min_fraction=PREFIX_MIN_FRACTION, level_of=None,
          level_mix=None, generic=None, identity_states=None):
    """(merged, refused) -- the whole decision, and it is pure so it is
    testable. Each entry is
    (a, b, relation, n_a, n_b, n_shared, fraction, why)."""
    merged, refused = [], []
    for a, b, rel in pairs:
        ra = rosters_by_name.get(a) or set()
        rb = rosters_by_name.get(b) or set()
        shared = len(ra & rb)
        small = min(len(ra), len(rb))
        frac = (shared / small) if small else 0.0
        need_n = {"prefix": prefix_min_shared, "level": LEVEL_MIN_SHARED,
                  "roster": ROSTER_MIN_SHARED, "renamed": RENAMED_MIN_SHARED}.get(rel, min_shared)
        need_f = {"prefix": prefix_min_fraction, "level": LEVEL_MIN_FRACTION,
                  "roster": ROSTER_MIN_FRACTION, "renamed": RENAMED_MIN_FRACTION}.get(rel, min_fraction)
        row = [a, b, rel, len(ra), len(rb), shared, round(frac, 4), None]
        sa = states_by_name.get(a) or set()
        sb = states_by_name.get(b) or set()
        if rel == "renamed" and identity_states:
            # a spelling with no team record: where its runners are
            sa = sa or identity_states.get(a) or set()
            sb = sb or identity_states.get(b) or set()
        la = (level_of or {}).get(a)
        lb = (level_of or {}).get(b)
        mix = level_mix or {}
        da, db = statedState(a), statedState(b)
        # ★ THE LEVELS (2026-11-01, the second dry run). Each spelling's
        #   dominant level; a spelling worn at several levels (Trinity:
        #   high schools AND colleges) can take in a narrower spelling of
        #   one of its levels -- "Trinity (Conn.)" into "Trinity" -- because
        #   the boards keep the levels apart by pool. The wider one is then
        #   the canonical (groups).
        def holds(name, lvl, share=MIX_SHARE):
            return (mix.get(name) or {}).get(lvl, 0.0) >= share
        level_ok = (not la or not lb or la == lb
                    or (rel == "same" and not (da or db))       # decoration, as always
                    or (rel in ("same", "college")
                        and (holds(b, la, COLLEGE_MIX if la == "college" else MIX_SHARE)
                             or holds(a, lb, COLLEGE_MIX if lb == "college" else MIX_SHARE))))
        # ★ A SPELLING THAT WRITES ITS STATE PICKS ONE OF A WIDE NAME'S
        #   COLLEGES: "Trinity (Conn.)" folds into "Trinity", which the
        #   boards split by state for a college by its runners
        #   (school_identity.teamState, college_person_state). College only:
        #   a high school has no such split, so "Glendale (CA)" still stays
        #   out of "Glendale" unless it is a college.
        #   (2026-11-01, third look: "Wesleyan" is one Georgia high school
        #   on athletic.net plus the CT university's runners from tfrrs --
        #   not two anet states, but it holds college runners; that is
        #   enough for a named college spelling to pick it.)
        coll_a = la == "college" or holds(a, "college", COLLEGE_MIX)
        coll_b = lb == "college" or holds(b, "college", COLLEGE_MIX)

        # ! AND MOST OF THE NAMED COLLEGE'S OWN RUNNERS ALSO WEAR THE PLAIN
        #   NAME (2026-11-01, fifth look: "St. Joseph's (Pa.)", 553 runners,
        #   27 of them under "St. Joseph's") -- else the plain name is not
        #   how that college is spelled, and a clean spelling would be lost
        #   to an ambiguous one.
        def picksInto(x, sx, y, y_lvl, y_stated, sy):
            ny = len(rosters_by_name.get(y) or ()) or 1
            # ! the plain name must be WIDE -- several states, or schools and
            #   a college together; a plain name that is only colleges
            #   ("Concordia University") is just a vaguer spelling, and the
            #   named one does not fold into it
            mx = mix.get(x) or {}
            mixed = (mx.get("college", 0) >= COLLEGE_MIX
                     and mx.get("hs", 0) + mx.get("ms", 0) >= MIX_SHARE)
            return bool(y_lvl == "college" and (y_stated or rel == "college")
                        and (len(sx) > 1 or mixed)
                        and sy <= ({y_stated} if y_stated else sy)
                        and shared / ny >= PICK_SHARE)
        picks = picksInto(a, sa, b, lb, db, sb) or picksInto(b, sb, a, la, da, sa)
        # ★ A NAME SEVERAL COLLEGES SHARE ("Concordia University": Nebraska,
        #   Wisconsin, Irvine, Portland ...) folds into one of them only when
        #   nearly all of its runners are that one's.
        g_frac = None
        if generic and (a in generic) != (b in generic):
            g = a if a in generic else b
            g_frac = shared / max(1, len(rosters_by_name.get(g) or ()))
        # ★ AN UNNAMED SPELLING INTO A NAMED ONE (2026-11-01, fourth look:
        #   "Concordia University" into "Concordia (Neb.)" on 78 of 148,
        #   "Archbishop Carroll" into "Archbishop Carroll (DC)" on 105 of
        #   200): the unnamed one may be several schools, so nearly all of
        #   its runners must be the named one's -- unless it is the wide
        #   name the named spelling folds INTO (picks).
        #   College only: a high school pair has the anet-state vetoes above,
        #   and its unnamed spelling is usually the main team ("La Jolla").
        #   A school pair too, when the unnamed one carries no athletic.net
        #   state to check it by ("Archbishop Carroll" into "... (DC)" on 105
        #   of 200).
        #   A bracketed place counts as naming one ("St. Joseph's (Brooklyn)").
        na, nb = bool(da or _qualifier(a)), bool(db or _qualifier(b))
        if g_frac is None and na != nb and not picks:
            u = b if na else a
            if "college" in (la, lb) or not (states_by_name.get(u)):
                g_frac = shared / max(1, len(rosters_by_name.get(u) or ()))
        wa, wb = set(nameWords(a)), set(nameWords(b))
        qa, qb = _qualifier(a), _qualifier(b)
        ca, cb = _levelClass(wa), _levelClass(wb)
        if da and db and da != db:
            row[7] = f"they name different states ({da} vs {db})"
            refused.append(tuple(row))
        elif (da and sb and da not in sb and not picks) or (db and sa and db not in sa and not picks):
            row[7] = f"a named state is not where the other's teams are ({da or db})"
            refused.append(tuple(row))
        elif not level_ok:
            row[7] = f"different levels ({la} vs {lb})"
            refused.append(tuple(row))
        elif ca and cb and ca != cb:
            # "Pearl MS" / "Pearl High School", "Scottsboro ms" / "Scottsboro
            # HS": the names say two schools, whatever the rows say
            row[7] = f"the names say two levels ({ca} vs {cb})"
            refused.append(tuple(row))
        elif qa and qb and qa != qb and not (da and db):
            # "St. Joseph's (Bklyn)" / "St. Joseph's (Long Island)": two
            # campuses, two teams
            row[7] = f"two different qualifiers ({qa} vs {qb})"
            refused.append(tuple(row))
        elif rel == "college" and _stateCollege(a, b):
            # "Louisiana" (the flagship) / "Louisiana College" (Pineville)
            row[7] = "a state's university and a college named for the state"
            refused.append(tuple(row))
        elif rel == "level" and not (la and lb):
            row[7] = "level words differ and a level is unknown"
            refused.append(tuple(row))
        elif rel == "level" and (({"college"} <= wa and "university" in wb and "college" not in wb)
                                 or ({"college"} <= wb and "university" in wa and "college" not in wa)):
            # "Antelope Valley College" / "University of Antelope Valley",
            # "Boston College" / "Boston University": two institutions
            row[7] = "college vs university: two institutions"
            refused.append(tuple(row))
        elif g_frac is not None and g_frac < GENERIC_FRACTION:
            row[7] = (f"a name that may be several schools; only {g_frac:.0%} of its "
                      f"runners are this one's")
            refused.append(tuple(row))
        elif rel == "level" and la != "college" and ((wa | wb) & {"college", "university"}):
            # "Boston College High" into "Boston College": a high school
            # named for a college is not that college's shortening
            row[7] = "a school named for a college"
            refused.append(tuple(row))
        elif rel == "level" and la != "college" and not (
                (sa and sb and sa == sb and len(sa) == 1)
                or (len(sa | sb) == 1 and shared >= 20 and frac >= 0.5)):
            row[7] = "level words differ; a school pair needs one known state"
            refused.append(tuple(row))
        elif rel == "renamed" and not (sa and sb and sa == sb and len(sa) == 1):
            row[7] = "a renamed school needs both spellings in one known state"
            refused.append(tuple(row))
        elif rel == "renamed" and (la != "hs" or lb != "hs"):
            row[7] = f"a renamed school is a high school pair ({la} vs {lb})"
            refused.append(tuple(row))
        elif rel == "college" and not (coll_a and coll_b):
            row[7] = "the directory's match, but not both college-level"
            refused.append(tuple(row))
        elif rel == "college" and (("state" in wa) != ("state" in wb)) and not (
                shared >= 20 and frac >= 0.5):
            # the directory reads past "State": "Arkansas" / "Arkansas
            # State" and "Portland" / "Portland State" are two universities,
            # "Cortland St." / "SUNY Cortland" and "Truman" / "Truman State"
            # one -- and the runners say which (one shares nearly everyone)
            row[7] = "one says State, the other does not, and they share too few"
            refused.append(tuple(row))
        # THE STATE VETO. A string whose anet teams sit in several states is
        #   a name many schools wear; it is never one team, whoever it shares
        #   athletes with -- unless the other spelling picks one of them.
        elif (len(sa) > 1 or len(sb) > 1) and not picks:
            wide = a if len(sa) > 1 else b
            row[7] = (f"{wide!r} spans {sorted(sa if len(sa) > 1 else sb)} "
                      f"-- a name several schools wear")
            refused.append(tuple(row))
        elif sa and sb and sa != sb and not picks:
            row[7] = f"different states {sorted(sa)} vs {sorted(sb)}"
            refused.append(tuple(row))
        elif shared >= need_n and frac >= need_f:
            merged.append(tuple(row))
        else:
            row[7] = (f"{shared} shared of {small} ({frac:.0%}); "
                      f"needs {need_n} and {need_f:.0%}")
            refused.append(tuple(row))
    merged.sort(key=lambda r: -r[5])
    refused.sort(key=lambda r: -r[5])
    return merged, refused


# ★ A GROUP, NOT A PAIR. Three spellings of one team give three pairs, and
#   writing them as pairs would make "La Jolla (CA)" point at "La Jolla-CA"
#   which points at "La Jolla" -- a chain the reader would have to walk.
#   Union-find, then one canonical per group, so every variant resolves in
#   one lookup.
def groups(merged, rosters_by_name, states_by_name=None, level_mix=None):
    """[(canonical, [(variant, n, relation, n_shared)])] -- one row per
    spelling that is NOT the canonical. Pure.

    ★ NO CHAINS (2026-11-01, the second dry run: "Concordia (Wis.)" reached
      "Concordia (Neb.)" through "Concordia University", which both wear;
      "Arkansas State" reached "Arkansas" through a three-runner "Arkansas
      St."). Two groups never join when they name different states; a
      spelling worn in several states or at several levels is the group's
      canonical (it is the one the narrower spellings fold INTO); and a
      variant stays only when its own pair with the canonical was judged
      a merge -- a link through a third spelling is not evidence."""
    states_by_name = states_by_name or {}
    level_mix = level_mix or {}
    parent, named = {}, {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    def stated(x):
        st = statedState(x)
        return {st} if st else set()

    direct = {}
    for a, b, rel, _na, _nb, shared, _f, _why in merged:
        direct[(a, b)] = direct[(b, a)] = (rel, shared)
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        na = named.get(ra, stated(a)) | stated(a)
        nb = named.get(rb, stated(b)) | stated(b)
        if len(na | nb) > 1:
            continue                       # two named states never share a group
        parent[rb] = ra
        named[ra] = na | nb

    members = {}
    for name in {x for r in merged for x in r[:2]}:
        members.setdefault(find(name), set()).add(name)

    # ! WIDE IS SEVERAL STATES, OR A SCHOOL NAME THAT ALSO HOLDS COLLEGE
    #   RUNNERS -- not a high school with a few middle-school rows
    #   (2026-11-01: "Abingdon High School" was made the canonical over
    #   "Abingdon" with 1,035 runners for that).
    def collegeNamed(m):
        mix = level_mix.get(m) or {}
        return bool(statedState(m)) and mix.get("college", 0) >= 0.5

    def wide(n, names=()):
        mix = level_mix.get(n) or {}
        mixed = (mix.get("college", 0) >= COLLEGE_MIX
                 and mix.get("hs", 0) + mix.get("ms", 0) >= MIX_SHARE)
        # a college-and-school mix makes the canonical only where a named
        # college spelling is folding into it ("Ammons Middle School" with
        # a stray college row is not the canonical over "Ammons MS")
        return (len(states_by_name.get(n) or ()) > 1
                or (mixed and any(collegeNamed(m) for m in names if m != n)))

    out = []
    for root, names in sorted(members.items()):
        sized = [(n, len(rosters_by_name.get(n) or ())) for n in sorted(names)]
        wides = [x for x in sized if wide(x[0], names)]
        if len(wides) > 1:
            continue                       # two wide names: never one school
        canon = wides[0][0] if wides else canonicalOf(sized)
        variants = []
        for n, sz in sized:
            if n == canon or (n, canon) not in direct:
                continue
            rel, shared = direct[(n, canon)]
            variants.append((n, sz, rel, shared))
        if variants:
            out.append((canon, sorted(variants)))
    return out


def write(cur, grouped, rosters_by_name):
    from scrape_school_logos import ensureTable
    ensureTable(cur, DDL)
    cur.execute("DELETE FROM school_name_alias WHERE source = 'coathlete'")
    rows = []
    for canon, variants in grouped:
        n_canon = len(rosters_by_name.get(canon) or ())
        for variant, n_var, rel, shared in variants:
            rows.append((variant, canon, n_var, n_canon, shared, rel))
    cur.executemany("""
        INSERT INTO school_name_alias (variant, canonical, n_variant,
                                       n_canonical, n_shared, relation, source)
        VALUES (%s, %s, %s, %s, %s, %s, 'coathlete')
        ON CONFLICT (variant) DO UPDATE
        SET canonical = EXCLUDED.canonical, n_variant = EXCLUDED.n_variant,
            n_canonical = EXCLUDED.n_canonical, n_shared = EXCLUDED.n_shared,
            relation = EXCLUDED.relation, built = current_date
    """, rows)
    return len(rows)


def explainPair(x, y, by_name, level_mix, level_of, college_key, generic, by_state,
                pairs, merged, refused):
    """--pair: everything the rules saw about two spellings."""
    for n in (x, y):
        ros = by_name.get(n)
        print(f"  {n!r}: {len(ros) if ros else 0} athletes"
              f"{'' if ros else ' (fewer than 2: not a candidate at all)'}; "
              f"level {level_of.get(n)} {level_mix.get(n)}; directory "
              f"{college_key.get(n)}; shared-name college {n in generic}; "
              f"anet states {sorted(by_state.get(n) or [])}; mergeable {mergeable(n)}")
    if by_name.get(x) and by_name.get(y):
        print(f"  shared athletes: {len(by_name[x] & by_name[y])}")
    # what the college directory holds for each spelling's key
    try:
        from build_college_directory import normName, _candidates, lookup
        from database import getConn
        import build_college_directory as BCD
        with getConn() as conn, conn.cursor() as cur:
            directory = BCD.loadDirectory(cur, "name", "state")
        for n in (x, y):
            key = normName(n)
            cands = _candidates(directory, key)
            print(f"  directory key {key!r}: {len(cands)} entr{'y' if len(cands) == 1 else 'ies'} "
                  f"{cands[:6]}; lookup -> {lookup(directory, n, statedState(n))}")
    except Exception as exc:                            # noqa: BLE001
        print(f"  (directory not read: {type(exc).__name__}: {exc})")
    key = {x, y}
    cand = [p for p in pairs if {p[0], p[1]} == key]
    print(f"  candidate pair: {cand or 'NO -- no rule proposes them (same / level / college / prefix)'}")
    for m in merged:
        if {m[0], m[1]} == key:
            print(f"  MERGED: {m}")
    for r in refused:
        if {r[0], r[1]} == key:
            print(f"  REFUSED: {r[2]}: {r[-1]}  ({r[3]} / {r[4]} athletes, {r[5]} shared)")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="without this, a dry run")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--prefix", action="store_true",
                    help="also consider a name that is a strict WORD prefix "
                         "of another (the 'Oregon Episcopal' shape) -- a much "
                         "higher bar, and off by default")
    ap.add_argument("--min-shared", type=int, default=MIN_SHARED)
    ap.add_argument("--min-fraction", type=float, default=MIN_FRACTION)
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"),
                    help="two spellings: say whether they are a candidate pair and why "
                         "they were or were not merged (no write)")
    a = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        by_name = rosters(cur)
        print(f"  {len(by_name):,} school strings with two or more athletes")
        level_mix = levelMix(cur)
        level_of = levels(cur, level_mix)
        print(f"  {len(level_of):,} spellings with a known level (college / hs / ms)")
        college_key = collegeKeys(cur, by_name.keys(), level_of)
        print(f"  {len(college_key):,} college spellings the directory places")
        generic = genericColleges(cur, by_name.keys(), level_of)
        print(f"  {len(generic):,} college spellings several colleges share")
        pairs = candidatesAll(by_name.keys(), want_prefix=a.prefix,
                              level_of=level_of, college_key=college_key)
        # the runners propose too: college spellings sharing most of a roster
        have = {(x, y) for x, y, _r in pairs}
        pairs = sorted(pairs + [p for p in rosterPairs(by_name, level_of)
                                if (p[0], p[1]) not in have and (p[1], p[0]) not in have])
        # ...and high-school spellings a rename left behind
        have = {(x, y) for x, y, _r in pairs}
        pairs = sorted(pairs + [p for p in renamedPairs(by_name, level_of)
                                if (p[0], p[1]) not in have and (p[1], p[0]) not in have])
        kinds = {}
        for _x, _y, rel in pairs:
            kinds[rel] = kinds.get(rel, 0) + 1
        print(f"  {len(pairs):,} candidate pairs {kinds}")
        by_state = teamStates(cur)
        print(f"  {len(by_state):,} of them carry an anet team, so their "
              f"state is known")
        id_states = identityStates(cur)
        merged, refused = judge(pairs, by_name, by_state,
                                min_shared=a.min_shared,
                                min_fraction=a.min_fraction, level_of=level_of,
                                level_mix=level_mix, generic=generic,
                                identity_states=id_states)
        if a.pair:
            explainPair(a.pair[0], a.pair[1], by_name, level_mix, level_of, college_key,
                        generic, by_state, pairs, merged, refused)
            return 0
        grouped = groups(merged, by_name, by_state, level_mix)
        n_variants = sum(len(v) for _c, v in grouped)
        print(f"  {len(merged):,} pairs agree -> {len(grouped):,} groups, "
              f"{n_variants:,} spellings folded onto another\n")
        print(f"  {'canonical':<38}{'variant':<38}{'ath':>5}{'shared':>7}  rel")
        for canon, variants in grouped[:a.show]:
            for variant, n_var, rel, shared in variants:
                print(f"  {canon[:37]:<38}{variant[:37]:<38}{n_var:>5}"
                      f"{shared:>7}  {rel}")
        if refused:
            print(f"\n  NOT MERGED ({len(refused):,}) -- the near misses:")
            for x, y, rel, na, nb, shared, _f, why in refused[:a.show]:
                print(f"  {x[:33]:<34}{y[:33]:<34}{na:>5}{nb:>5}{shared:>6}"
                      f"  {rel}: {why}")
        if not a.write:
            print("\n  DRY RUN -- nothing written. --write to store them.\n")
            return 0
        n = write(cur, grouped, by_name)
        conn.commit()
        print(f"\n  wrote {n:,} rows to school_name_alias.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
