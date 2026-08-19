"""
============================================================================
 resolve_fanout.py -- the "later, smarter pass" for quarantined links
============================================================================

 THE SITUATION
 -------------
 merge_links.py deliberately quarantines every athlete link involved in
 fan-out (an anet_id with 2+ tfrrs partners, or a tfrrs_id with 2+ anet
 partners), because confidence alone can't separate "same person, two
 tfrrs profiles" from "two real people who collided". ~43K XC people and
 ~171K TF people are held back. This pass resolves them with evidence
 that ISN'T confidence:

   FALSIFY -- co-appearance: one person cannot FINISH the same meet
              twice. Two same-side ids that both finished one meet are
              provably two people.
   CORROBORATE -- school agreement: anet keeps ONE athlete_id across
              schools (PK is (athlete_id, school) -- one person, many
              school rows) while TFRRS mints a NEW id per team. A weak
              link whose tfrrs school appears in the anet athlete's
              school set has independent support a name collision lacks.
   (Date-range disjointness is deliberately NOT used: a graduating
    'Emma Johnson' and a different college 'Emma Johnson' pass it.)

 OUTPUT: the fanout_resolutions table -- one row per quarantined link
 with a decision ('stamp' / 'hold'), the rule that fired, and the
 evidence. NOTHING IS STAMPED. Inspect the table; a separate stamp step
 acts on decision='stamp' rows only after you've read them.

 THE RULES (all knobs are flags; defaults below):
   reverse star (1 anet -> n tfrrs):
     - any two tfrrs profiles co-finished a meet -> HOLD whole star
       ('co-appearance' -- at most one is the person; we don't guess)
     - else per link: STAMP if confidence >= --reverse-floor OR school
       overlap; HOLD otherwise ('weak-no-school')
   forward star (n anet -> 1 tfrrs):
     - STAMP the top claimant iff its confidence >= --forward-top-min
       AND every runner-up <= --forward-runnerup-max; HOLD the rest
       ('outmargined'). Otherwise HOLD all ('no-clear-winner').
   complex (2+ anet AND 2+ tfrrs chained) -> HOLD all ('complex').

 Usage:
     python scripts/resolve_fanout.py                     # both sports
     python scripts/resolve_fanout.py --sport XC
     python scripts/resolve_fanout.py --reverse-floor 3   # stricter
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS + CONSTANTS
# ===========================================================================

import argparse
import re                              # school-string tokenization
import time
from collections import defaultdict

import psycopg2.extras                 # execute_values: fast bulk INSERT

from database import getConn

# Sport decides the TABLE, source decides the FILTER (§0 invariant).
SPORT_TABLE = {"XC": "results", "TF": "results_tf"}

# Default knobs (all overridable by flags; see the header for what they mean).
REVERSE_FLOOR       = 2   # reverse-star link stamps outright at this confidence
FORWARD_TOP_MIN     = 3   # forward-star winner needs at least this
FORWARD_RUNNERUP_MAX = 1  # ...and no rival above this

# --- school tokenization (added after the first inspection) ---------------
# The exact-string school match missed 'peru' vs 'peru junior high school'
# and -- worse -- treated 'unattached' as a shared school (it's the ABSENCE
# of one). Agreement is now overlap of identity-carrying TOKENS. These are
# the tokens that carry NO identity and are dropped before comparing:
GENERIC_TOKENS = {
    # institution types -- the suffix words that vary between sources
    "school", "high", "hs", "junior", "jr", "middle", "ms", "elementary",
    "academy", "university", "univ", "college", "coll", "cc", "community",
    "institute", "prep", "preparatory", "senior", "sr", "city",
    # team-y filler
    "club", "track", "field", "running", "xc", "cross", "country", "team",
    "athletics", "athletic", "tc",
    # placeholders -- MUST never corroborate (the 'unattached' bug)
    "unattached", "una", "independent", "none",
    # glue words
    "of", "the", "and", "at", "st", "saint",
    # state abbreviations -- appear as '(mich.)' disambiguators on anet
    # college names; parenthetical CONTENTS are kept as tokens now (see
    # _normSchoolTokens), so these must be neutralized here or two
    # schools could 'agree' on merely sharing a state.
    "ala", "ariz", "ark", "calif", "colo", "conn", "del", "fla", "tenn",
    "ill", "ind", "kan", "mass", "mich", "minn", "miss", "mont", "neb",
    "nev", "okla", "ore", "penn", "tex", "wash", "wis", "wyo",
}
# Tokens shorter than this are dropped too ('wi', '32', stray initials) --
# too little identity to hang a person-weld on.
MIN_TOKEN_LEN = 3


# ===========================================================================
# CHUNK 2: LOAD + QUARANTINE -- rebuild exactly the set the merge refused
# ===========================================================================

def _loadAthleteLinks(cur, sport):
    # -----------------------------------------------------------------
    # Purpose:  pull ALL athlete links for one sport. The quarantine is
    #           derived from the full set (fan-out is a property of the
    #           whole link graph, not of any one link).
    # Arguments:
    #   cur   -- open cursor (one connection for the whole run)
    #   sport -- 'XC' or 'TF' (UPPERCASE, entity_links convention)
    # Output:  list of (anet_id, tfrrs_id, confidence, method) tuples.
    #          method rides along because fuzzy links ('fuzzy-in-meet')
    #          are name-SIMILAR evidence, exact links are name-EQUAL --
    #          two rules below read the difference.
    # -----------------------------------------------------------------
    cur.execute(
        "SELECT anet_id, tfrrs_id, confidence, method FROM entity_links "
        "WHERE entity_type = 'athlete' AND sport = %s",
        (sport,),
    )
    return cur.fetchall()


def _quarantine(links):
    # -----------------------------------------------------------------
    # Purpose:  the SAME both-sides fan-out test merge_links.py applies
    #           (an id appearing in >1 link contaminates all its links),
    #           re-derived here so this file provably acts on exactly
    #           the set the merge refused -- no silent drift if either
    #           file changes.
    # Arguments:
    #   links -- list of (anet_id, tfrrs_id, confidence, method)
    # Output:  the quarantined subset, same tuple shape.
    # -----------------------------------------------------------------
    anet_count, tfrrs_count = defaultdict(int), defaultdict(int)
    # *_ swallows everything after the two ids -- the counting only
    # needs positions 0 and 1, so it survives the 3->4 tuple change
    # (and any future widening) untouched.
    for a, t, *_ in links:             # first pass: how many links each id is in
        anet_count[a]  += 1
        tfrrs_count[t] += 1
    # second pass: a link is quarantined if EITHER of its ids is contested
    return [link for link in links
            if anet_count[link[0]] > 1 or tfrrs_count[link[1]] > 1]


# ===========================================================================
# CHUNK 3: CLUSTERING -- union-find groups links into connected components
# ===========================================================================
#
# The idea: treat each id as a node ('a', anet_id) or ('t', tfrrs_id); each
# link connects two nodes. A CLUSTER is everything reachable through shared
# ids -- which can chain (anet1-tfrrsA, anet2-tfrrsA, anet2-tfrrsB is ONE
# component of 3 links). Union-find tracks "which group is this node in"
# with two tiny operations: _find walks to a node's root representative,
# _union points one root at the other, merging the groups.

def _find(parent, x):
    # -----------------------------------------------------------------
    # Purpose:  return x's root representative, flattening the path as
    #           it walks (path compression) so later finds are O(1)-ish.
    # Arguments:
    #   parent -- dict node -> parent node; a root is its own parent
    #   x      -- the node to look up (created as a root if unseen)
    # Output:  the root node of x's group.
    # -----------------------------------------------------------------
    parent.setdefault(x, x)            # first sighting: x is its own root
    while parent[x] != x:              # walk up until a self-parented root
        parent[x] = parent[parent[x]]  # compression: point x at grandparent
        x = parent[x]
    return x


def _union(parent, x, y):
    # -----------------------------------------------------------------
    # Purpose:  merge the groups containing x and y (no-op if already
    #           together): find both roots, point one at the other.
    # Arguments: parent (same dict); x, y (nodes to connect).
    # Output:   None -- mutates parent.
    # -----------------------------------------------------------------
    rx, ry = _find(parent, x), _find(parent, y)
    if rx != ry:
        parent[ry] = rx


def _buildComponents(quarantined):
    # -----------------------------------------------------------------
    # Purpose:  group quarantined links into clusters. Each link joins
    #           its two id-nodes; afterwards, links sharing a root are
    #           one cluster.
    # Arguments:
    #   quarantined -- list of (anet_id, tfrrs_id, confidence, method)
    # Output:  list of clusters, each a list of link tuples.
    # -----------------------------------------------------------------
    parent = {}
    for a, t, *_ in quarantined:       # ('a',x) vs ('t',x): the tag keeps the
        _union(parent, ("a", a), ("t", t))   # two id spaces from colliding (§0!)
    groups = defaultdict(list)
    for link in quarantined:
        root = _find(parent, ("a", link[0]))   # either endpoint's root works
        groups[root].append(link)
    return list(groups.values())


def _classify(cluster):
    # -----------------------------------------------------------------
    # Purpose:  name the cluster's shape, which picks the rule set.
    # Arguments:
    #   cluster -- list of (anet_id, tfrrs_id, confidence, method)
    # Output:  'reverse' (one anet, many tfrrs), 'forward' (many anet,
    #          one tfrrs), or 'complex' (many of both / chained).
    # -----------------------------------------------------------------
    # index access instead of unpacking: works for any tuple width
    n_anet  = len({lnk[0] for lnk in cluster})
    n_tfrrs = len({lnk[1] for lnk in cluster})
    if n_anet == 1 and n_tfrrs > 1:
        return "reverse"
    if n_tfrrs == 1 and n_anet > 1:
        return "forward"
    return "complex"


# ===========================================================================
# CHUNK 4: EVIDENCE -- two bulk queries per sport, loaded into dicts
# ===========================================================================
#
# Design rule: evidence is computed SET-BASED, once, up front -- never one
# query per cluster (20K+ clusters of tiny queries is the row-by-row trap
# the fast build exists to avoid). The rule functions then work from plain
# Python dicts and never touch the database.

def _coAppearingPairs(cur, sport, side):
    # -----------------------------------------------------------------
    # Purpose:  THE FALSIFIER. Find pairs of same-side ids -- linked to
    #           the same partner on the other side -- that both FINISHED
    #           at least one common meet. One person cannot finish the
    #           same meet twice, so each pair is provably two people.
    #           (Meet-level, not event-level: strict enough for XC; for
    #           TF a person could in theory finish two events at one
    #           meet, but not under two team profiles simultaneously,
    #           so meet-level stands -- documented judgment call.)
    # Arguments:
    #   cur   -- open cursor
    #   sport -- 'XC' or 'TF'; picks the results table
    #   side  -- 'tfrrs' (pairs of tfrrs ids sharing an anet partner;
    #            feeds reverse stars) or 'anet' (pairs of anet ids
    #            sharing a tfrrs partner; feeds forward stars)
    # Output:  set of (id_low, id_high) tuples -- each a proven-distinct
    #          pair. Ordered low,high so lookups don't care about order.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    if side == "tfrrs":
        pair_col, group_col = "tfrrs_id", "anet_id"
        row_key, source     = "native_id", "tfrrs"
        # tfrrs non-finisher sentinels: NULL time or place 0 (§5)
        finisher = "r.time_seconds IS NOT NULL AND r.place <> 0"
    else:
        pair_col, group_col = "anet_id", "tfrrs_id"
        row_key, source     = "athlete_id", "anet"
        # anet sentinels: 999999 time or place 0 (§5)
        finisher = ("r.time_seconds IS NOT NULL AND r.time_seconds <> 999999 "
                    "AND r.place <> 0")

    cur.execute(
        # pairs: two links sharing the group column = two same-side ids
        # claimed by one partner. l1.id < l2.id keeps each pair once.
        f"WITH pairs AS ("
        f"  SELECT DISTINCT l1.{pair_col} AS id1, l2.{pair_col} AS id2 "
        f"  FROM entity_links l1 "
        f"  JOIN entity_links l2 "
        f"    ON l1.{group_col} = l2.{group_col} "
        f"   AND l1.{pair_col} < l2.{pair_col} "
        f"  WHERE l1.entity_type = 'athlete' AND l2.entity_type = 'athlete' "
        f"    AND l1.sport = %s AND l2.sport = %s "
        f") "
        f"SELECT p.id1, p.id2 FROM pairs p "
        # both ids finished the SAME meet: two probes on the id index,
        # joined on meet_id -- legal here because both rows are pinned
        # to ONE source and ONE sport's table (§0 satisfied).
        f"WHERE EXISTS ("
        f"  SELECT 1 FROM {table} r "
        f"  JOIN {table} r2 ON r2.meet_id = r.meet_id "
        f"                 AND r2.source = '{source}' AND r2.{row_key} = p.id2 "
        f"  WHERE r.source = '{source}' AND r.{row_key} = p.id1 "
        f"    AND {finisher} "
        f"    AND r2.time_seconds IS NOT NULL AND r2.place <> 0 "
        f")",
        (sport, sport),
    )
    return set(cur.fetchall())


def _normSchoolTokens(raw):
    # -----------------------------------------------------------------
    # Purpose:  one raw school string -> its set of identity-carrying
    #           tokens. What the inspections demanded, both rounds:
    #             'peru junior high school'   -> {'peru'}
    #             'topeka-hayden'             -> {'topeka', 'hayden'}
    #             'rockton (hononegah)'       -> {'rockton', 'hononegah'}
    #             'oakland (mich.) cc'        -> {'oakland'}  (mich = generic)
    #             'unattached'                -> {}   (nothing to match!)
    #           NOTE parentheticals are KEPT as tokens, not stripped:
    #           anet uses a 'Town (School)' convention where the
    #           parenthetical IS the school name (second inspection
    #           caught the strip deleting 'hononegah'). State-abbrev
    #           parentheticals like '(mich.)' are neutralized by the
    #           GENERIC_TOKENS blacklist instead.
    #           PURE function -- string in, set out -- so it's testable
    #           on toy inputs without a database.
    # Arguments:
    #   raw -- one school string as stored (any case, may be None)
    # Output:  set of tokens, each >= MIN_TOKEN_LEN and not generic.
    #          Empty set means "no identity here" -- can't corroborate.
    # -----------------------------------------------------------------
    if not raw:
        return set()
    s = re.sub(r"[^a-z0-9]+", " ", raw.lower())   # ALL punctuation (incl parens) -> spaces
    return {
        tok for tok in s.split()
        if len(tok) >= MIN_TOKEN_LEN      # drop 'wi', 'st', stray initials
        and tok not in GENERIC_TOKENS     # drop institution/filler/state words
        and not tok.isdigit()             # drop '32' from '32-unattached'
    }


def _schoolSets(cur, sport, anet_ids, tfrrs_ids):
    # -----------------------------------------------------------------
    # Purpose:  THE CORROBORATOR. Identity-token sets for every id in
    #           quarantine. anet: from athletes (ALL rows -- the
    #           (athlete_id, school) PK means one person's schools are
    #           several rows). tfrrs: distinct schools on result rows.
    #           Each id's schools are FLATTENED into one token set --
    #           fine for corroboration, since the old semantic was
    #           already 'any school matches any school'.
    # Arguments:
    #   cur       -- open cursor
    #   sport     -- picks the tfrrs-side results table
    #   anet_ids  -- list of quarantined anet ids
    #   tfrrs_ids -- list of quarantined tfrrs ids
    # Output:  (anet_schools, tfrrs_schools) -- two dicts,
    #          id -> set of identity tokens across all its schools.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    anet_schools, tfrrs_schools = defaultdict(set), defaultdict(set)

    # = ANY(%s): psycopg2 adapts a Python LIST to a SQL ARRAY, and
    # ANY(array) matches membership -- the safe way to pass thousands
    # of ids without building a giant IN (...) string. Raw strings come
    # back; tokenization happens here in Python where regex + blacklist
    # logic is at home (and this is sample-scale work: ~60K strings).
    cur.execute(
        "SELECT athlete_id, school FROM athletes "
        "WHERE athlete_id = ANY(%s) AND school IS NOT NULL",
        (list(anet_ids),),
    )
    for aid, school in cur.fetchall():
        anet_schools[aid] |= _normSchoolTokens(school)   # |= : in-place set union

    cur.execute(
        f"SELECT DISTINCT native_id, school FROM {table} "
        f"WHERE source = 'tfrrs' AND native_id = ANY(%s) AND school IS NOT NULL",
        (list(tfrrs_ids),),
    )
    for tid, school in cur.fetchall():
        tfrrs_schools[tid] |= _normSchoolTokens(school)
    return anet_schools, tfrrs_schools


# ===========================================================================
# CHUNK 5: RULES -- pure functions: cluster + evidence in, verdicts out
# ===========================================================================

# The method string fuzzy links carry -- everything else is name-EQUAL
# evidence. Two rules below read this distinction.
FUZZY_METHOD = "fuzzy-in-meet"


def _isSpur(lnk, prune_all_conf1):
    # -----------------------------------------------------------------
    # Purpose:  THE spur definition -- the one place "weak enough to
    #           cut from a complex cluster" is decided, so the two
    #           prune modes can't drift apart across call sites.
    # Arguments:
    #   lnk             -- one 4-tuple (anet_id, tfrrs_id, conf, method)
    #   prune_all_conf1 -- False (default): only fuzzy conf-1 links are
    #                      spurs -- exact evidence is never sacrificed.
    #                      True (--prune-all-conf1): ANY conf-1 link is
    #                      a spur -- frees strong links held hostage by
    #                      exact conf-1 chains (Chelsea's conf-14), at
    #                      the price of deferring probably-true exact
    #                      conf-1 links (~99% clean in XC) to a later
    #                      pass.
    # Output:  bool -- True if this link should be cut and held.
    # -----------------------------------------------------------------
    if lnk[2] > 1:                     # anything above conf 1 is never a spur
        return False
    return prune_all_conf1 or lnk[3] == FUZZY_METHOD


def _resolveReverse(cluster, co_tfrrs, anet_schools, tfrrs_schools, floor):
    # -----------------------------------------------------------------
    # Purpose:  verdicts for a reverse star (one anet person, n tfrrs
    #           profiles). Falsify first, then per-link corroborate.
    # Arguments:
    #   cluster       -- the star's links (anet_id identical throughout),
    #                    4-tuples (anet_id, tfrrs_id, confidence, method)
    #   co_tfrrs      -- set of (t_low, t_high) proven-distinct pairs
    #   anet_schools  -- dict anet_id -> school token set
    #   tfrrs_schools -- dict tfrrs_id -> school token set
    #   floor         -- confidence that stamps outright (--reverse-floor)
    # Output:  list of (anet_id, tfrrs_id, confidence, decision, rule,
    #          evidence) -- decision is 'stamp' or 'hold'.
    # -----------------------------------------------------------------
    tfrrs_ids = sorted({lnk[1] for lnk in cluster})
    # any proven-distinct pair inside the star? then at most one profile
    # is really this person, and we refuse to guess which -> hold all.
    for i, t1 in enumerate(tfrrs_ids):
        for t2 in tfrrs_ids[i + 1:]:   # (t1, t2) already low,high by sort
            if (t1, t2) in co_tfrrs:
                return [(a, t, c, "hold", "co-appearance",
                         f"tfrrs {t1} & {t2} finished a common meet")
                        for (a, t, c, m) in cluster]

    out = []
    for a, t, c, m in cluster:
        overlap = anet_schools.get(a, set()) & tfrrs_schools.get(t, set())
        if c >= floor:
            out.append((a, t, c, "stamp", "confidence", f"conf {c} >= {floor}"))
        elif overlap:
            # sorted(...) so the evidence string is deterministic run to run
            out.append((a, t, c, "stamp", "school-match",
                        f"shared school token(s) {sorted(overlap)}"))
        else:
            out.append((a, t, c, "hold", "weak-no-school",
                        f"conf {c} < {floor}, no school overlap"))
    return out


def _pickForwardWinner(cluster, top_min, runnerup_max):
    # -----------------------------------------------------------------
    # Purpose:  the winner decision for a forward star, ISOLATED so the
    #           two ways to win read as a list, not a tangle:
    #             1. MARGIN (method-blind, strict): top conf >= top_min
    #                and every rival <= runnerup_max.
    #             2. EXACT-OVER-FUZZY (the round-two lesson): exactly
    #                ONE link is exact evidence (name-EQUAL) and its
    #                confidence >= every fuzzy rival's. A conf-1 fuzzy
    #                coincidence must not dethrone a same-name exact
    #                incumbent -- the 'Austin Tungate' regression class.
    # Arguments:
    #   cluster      -- 4-tuple links of one forward star
    #   top_min      -- margin rule's winner minimum
    #   runnerup_max -- margin rule's rival ceiling
    # Output:  (winner_link_or_None, rule_name_or_None, ranked) --
    #          ranked returned so the caller doesn't re-sort.
    # -----------------------------------------------------------------
    ranked = sorted(cluster, key=lambda lnk: lnk[2], reverse=True)
    top, rest = ranked[0], ranked[1:]

    # way 1: the strict margin (unchanged from round one)
    if top[2] >= top_min and all(r[2] <= runnerup_max for r in rest):
        return top, "margin", ranked

    # way 2: a lone exact link that out-ranks every fuzzy challenger.
    # lnk[3] is method; exact means anything that ISN'T the fuzzy tag.
    exacts = [lnk for lnk in cluster if lnk[3] != FUZZY_METHOD]
    if len(exacts) == 1:
        exact = exacts[0]
        fuzz_rivals = [lnk for lnk in cluster if lnk[3] == FUZZY_METHOD]
        if all(exact[2] >= f[2] for f in fuzz_rivals):
            return exact, "exact-over-fuzzy", ranked

    return None, None, ranked


def _resolveForward(cluster, top_min, runnerup_max):
    # -----------------------------------------------------------------
    # Purpose:  verdicts for a forward star (n anet claimants, one
    #           tfrrs profile) -- the "welding strangers" direction.
    #           The winner logic lives in _pickForwardWinner; this
    #           function only converts (winner, rule) into verdicts.
    # Arguments:
    #   cluster      -- the star's 4-tuple links
    #   top_min      -- winner needs at least this (--forward-top-min)
    #   runnerup_max -- rivals must all be at or below this
    # Output:  same 6-tuple list as _resolveReverse.
    # -----------------------------------------------------------------
    winner, rule, ranked = _pickForwardWinner(cluster, top_min, runnerup_max)
    top, rest = ranked[0], ranked[1:]
    out = []
    for a, t, c, m in ranked:
        if winner and (a, t, c, m) == winner:
            out.append((a, t, c, "stamp", rule,
                        f"top conf {c} vs runner-up {rest[0][2]}" if rule == "margin"
                        else f"lone exact conf {c} vs fuzzy rivals"))
        elif winner:
            out.append((a, t, c, "hold", "outmargined",
                        f"conf {c} lost to {winner[2]} ({rule})"))
        else:
            out.append((a, t, c, "hold", "no-clear-winner",
                        f"top {top[2]} vs runner-up {rest[0][2]}"))
    return out


def _resolveComplex(cluster, co_tfrrs, anet_schools, tfrrs_schools, args):
    # -----------------------------------------------------------------
    # Purpose:  SPUR PRUNING -- the upgrade Chelsea's TF conf-14 has
    #           waited for. Complex clusters are usually clean stars
    #           chained together by conf-1 FUZZY spurs (one coincidence
    #           meet). Drop those spurs (they hold as 'pruned-fuzzy-
    #           spur'), re-component what remains -- the pruned graph
    #           can SPLIT into several clusters -- and give each piece
    #           the normal rules. Still-complex pieces hold as before.
    # Arguments:
    #   cluster  -- the complex cluster's 4-tuple links
    #   co_tfrrs / anet_schools / tfrrs_schools -- the usual evidence
    #   args     -- parsed knobs (floors/margins/prune mode for sub-rules)
    # Output:  6-tuple verdicts for EVERY link in the input cluster.
    # -----------------------------------------------------------------
    spurs = [lnk for lnk in cluster
             if _isSpur(lnk, args.prune_all_conf1)]
    kept  = [lnk for lnk in cluster
             if not _isSpur(lnk, args.prune_all_conf1)]

    # the rule label records WHICH kind of spur was cut, so the two
    # prune modes are distinguishable in the summary table when you
    # diff a default run against a --prune-all-conf1 run.
    out = [(a, t, c, "hold",
            "pruned-fuzzy-spur" if m == FUZZY_METHOD else "pruned-exact-spur",
            f"conf-1 {'fuzzy' if m == FUZZY_METHOD else 'exact'} spur "
            f"chaining a complex cluster")
           for (a, t, c, m) in spurs]

    # re-component: removing spurs can split one chain into clean stars
    for piece in _buildComponents(kept):
        n_anet  = len({lnk[0] for lnk in piece})
        n_tfrrs = len({lnk[1] for lnk in piece})
        if n_anet == 1 and n_tfrrs == 1:
            # a SINGLETON: pruning left one uncontested link. _classify
            # would call it 'complex' (it matches neither star test), so
            # dispatch it explicitly -- judged by the reverse gate
            # (confidence / school corroboration), the per-link rules.
            out += _resolveReverse(piece, co_tfrrs, anet_schools,
                                   tfrrs_schools, args.reverse_floor)
            continue
        shape = _classify(piece)
        if shape == "reverse":
            out += _resolveReverse(piece, co_tfrrs, anet_schools,
                                   tfrrs_schools, args.reverse_floor)
        elif shape == "forward":
            out += _resolveForward(piece, args.forward_top_min,
                                   args.forward_runnerup_max)
        else:                          # still complex after pruning: hold
            out += [(a, t, c, "hold", "complex", "chained cluster (post-prune)")
                    for (a, t, c, m) in piece]
    return out


# ===========================================================================
# CHUNK 6: OUTPUT -- the resolutions table + the summary you'll inspect
# ===========================================================================

def _ensureTable(cur):
    # -----------------------------------------------------------------
    # Purpose:  create fanout_resolutions if missing. PK matches
    #           entity_links' link identity so a re-run upserts cleanly.
    # Arguments: cur -- open cursor.
    # Output:   None.
    # -----------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fanout_resolutions (
            sport      TEXT    NOT NULL,
            cluster_id INTEGER NOT NULL,
            shape      TEXT    NOT NULL,
            anet_id    BIGINT  NOT NULL,
            tfrrs_id   BIGINT  NOT NULL,
            confidence INTEGER,
            decision   TEXT    NOT NULL,
            rule       TEXT    NOT NULL,
            evidence   TEXT,
            PRIMARY KEY (sport, anet_id, tfrrs_id)
        )
    """)


def _writeResolutions(cur, sport, rows):
    # -----------------------------------------------------------------
    # Purpose:  replace this sport's verdicts (delete-then-insert keeps
    #           re-runs idempotent), bulk-inserted via execute_values.
    # Arguments:
    #   cur   -- open cursor
    #   sport -- 'XC' or 'TF'
    #   rows  -- list of (cluster_id, shape, anet_id, tfrrs_id,
    #            confidence, decision, rule, evidence)
    # Output:  None.
    # -----------------------------------------------------------------
    cur.execute("DELETE FROM fanout_resolutions WHERE sport = %s", (sport,))
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO fanout_resolutions "
        "(sport, cluster_id, shape, anet_id, tfrrs_id, confidence, "
        " decision, rule, evidence) VALUES %s",
        [(sport,) + r for r in rows],  # tuple concat prepends the sport
        page_size=10000,
    )


def _summarize(rows):
    # -----------------------------------------------------------------
    # Purpose:  the distribution you'll read first: links per
    #           (shape, decision, rule), so one glance shows how much
    #           the pass recovered and where the holds concentrate.
    # Arguments: rows -- the tuples given to _writeResolutions.
    # Output:   None -- prints.
    # -----------------------------------------------------------------
    counts = defaultdict(int)
    for (_, shape, _, _, _, decision, rule, _) in rows:
        counts[(shape, decision, rule)] += 1
    print("      shape      decision  rule                 links")
    for (shape, decision, rule), n in sorted(counts.items()):
        print(f"      {shape:<9}  {decision:<8}  {rule:<19}  {n:>9,}")


# ===========================================================================
# CHUNK 7: MAIN
# ===========================================================================

def _resolveSport(cur, sport, args):
    # -----------------------------------------------------------------
    # Purpose:  the full pipeline for one sport: load -> quarantine ->
    #           cluster -> evidence -> rules -> write -> summary.
    # Arguments: cur; sport; args (the parsed knobs).
    # Output:   None -- writes fanout_resolutions, prints the summary.
    # -----------------------------------------------------------------
    t0 = time.time()
    links = _loadAthleteLinks(cur, sport)
    quarantined = _quarantine(links)
    clusters = _buildComponents(quarantined)
    print(f"    {len(quarantined):,} quarantined links in "
          f"{len(clusters):,} clusters  [{time.time()-t0:.1f}s]")

    t0 = time.time()
    co_tfrrs = _coAppearingPairs(cur, sport, "tfrrs")
    co_anet  = _coAppearingPairs(cur, sport, "anet")   # recorded for complex/forward review
    anet_schools, tfrrs_schools = _schoolSets(
        cur, sport,
        {lnk[0] for lnk in quarantined},   # index access: 4-tuple safe
        {lnk[1] for lnk in quarantined},
    )
    print(f"    evidence: {len(co_tfrrs):,} distinct tfrrs pairs, "
          f"{len(co_anet):,} distinct anet pairs  [{time.time()-t0:.1f}s]")

    rows = []
    for cid, cluster in enumerate(clusters):
        shape = _classify(cluster)
        if shape == "reverse":
            verdicts = _resolveReverse(cluster, co_tfrrs, anet_schools,
                                       tfrrs_schools, args.reverse_floor)
        elif shape == "forward":
            verdicts = _resolveForward(cluster, args.forward_top_min,
                                       args.forward_runnerup_max)
        else:
            # complex clusters now get SPUR PRUNING instead of a blanket
            # hold: drop fuzzy conf-1 chains, re-component, rule each piece.
            verdicts = _resolveComplex(cluster, co_tfrrs, anet_schools,
                                       tfrrs_schools, args)
        for (a, t, c, decision, rule, evidence) in verdicts:
            rows.append((cid, shape, a, t, c, decision, rule, evidence))

    _writeResolutions(cur, sport, rows)
    _summarize(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Resolve quarantined fan-out links; write verdicts, stamp nothing.")
    parser.add_argument("--sport", choices=["XC", "TF"], default=None)
    parser.add_argument("--reverse-floor", type=int, default=REVERSE_FLOOR,
                        help="reverse-star confidence that stamps outright")
    parser.add_argument("--forward-top-min", type=int, default=FORWARD_TOP_MIN,
                        help="forward-star winner needs at least this")
    parser.add_argument("--forward-runnerup-max", type=int,
                        default=FORWARD_RUNNERUP_MAX,
                        help="forward-star rivals must all be at or below this")
    parser.add_argument("--prune-all-conf1", action="store_true",
                        help="complex-cluster pruning cuts ANY conf-1 link, "
                             "not just fuzzy ones -- frees strong links held "
                             "hostage by exact conf-1 chains, at the cost of "
                             "deferring probably-true exact conf-1 links")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["XC", "TF"]
    print(f"=== resolve fan-out (writes fanout_resolutions; stamps NOTHING) ===")
    print(f"    knobs: reverse-floor={args.reverse_floor}, "
          f"forward-top-min={args.forward_top_min}, "
          f"forward-runnerup-max={args.forward_runnerup_max}, "
          f"prune-all-conf1={args.prune_all_conf1}")

    with getConn() as conn:
        cur = conn.cursor()
        _ensureTable(cur)
        for sport in sports:
            print(f"\n--- {sport} ---")
            _resolveSport(cur, sport, args)
        conn.commit()                  # one commit: all-or-nothing per run

    print("\n=== done. Inspect fanout_resolutions, then stamp decision='stamp'. ===")


if __name__ == "__main__":
    main()