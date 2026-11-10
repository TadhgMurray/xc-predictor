"""rr_layout -- the physical layout switches of the boards tables.

ranking_results and athlete_season are rebuilt from scratch on every run
(build_ranking_results: a shadow, its indexes, a swap), so how they sit on
disk is decided by the build alone and can change from one run to the next
with nothing to migrate. docs/TABLE-LAYOUT-EXPERIMENTS.md measured two
changes that return exactly the same rows to every query; each ships behind
its own switch, OFF by default, read when the build runs:

  XCP_RR_LEAN=1      (the plan's step 1, build only)
      * every index of both tables built WITH (fillfactor = 100): the trees
        are built once and never updated, so the 10% a btree leaves free
        per page is waste;
      * the four RATING indexes of ranking_results partial on
        `speed_rating IS NOT NULL` (LEAN_PARTIAL). Every reader that walks
        them says so (or says `speed_rating >= x`, which implies it), and
        every PREFIX of them is also the prefix of a full index, so a
        lookup that names no rating still has a tree;
      * the ranking_results shadow created with its columns in alignment
        order (8-byte, 4-byte, ..., then text), from the live table's own
        catalogue, so the types, NOT NULLs, defaults and CHECKs are the
        live table's and only the order moves. Every writer names its
        columns (the stream COPY and seedOtherSports name _COLUMNS).
      Nothing is dropped. Which trees to retire is decided from
      production's pg_stat_user_indexes (scripts/layout_probe.py).

  XCP_RR_ORDERED=1   (the plan's step 2)
      The finish step's SET LOGGED rewrite of each shadow becomes an
      ordered copy into a LOGGED table: ranking_results in board order
      (BOARD_ORDER), athlete_season likewise. Same heap write, one sort
      added; the boards then read their candidates from a few pages.

  Off, the build is byte for byte the one before these switches. Turning
  one off again is the rollback: the next run builds the old layout and
  swaps it in.

! THIS MODULE IMPORTS NOTHING HEAVY. The builder, the 11b page-index step
  (scripts/add_page_indexes.py), ensure_ranking_indexes and db_health all
  compare a live index with the one the code wants, and every one of them
  has to agree on what a lean index looks like or it calls a lean tree
  "missing" and builds a second copy beside it.
"""
import os
import re


def _on(value):
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def lean():
    """XCP_RR_LEAN: fillfactor 100, partial rating indexes, narrow rows.
    Read at each call, so a test (or a process) sees the current setting."""
    return _on(os.environ.get("XCP_RR_LEAN", "0"))


def ordered():
    """XCP_RR_ORDERED: the shadows rewritten in board order, logged."""
    return _on(os.environ.get("XCP_RR_ORDERED", "0"))


# the tables these switches govern; nothing else is touched
TABLES = ("ranking_results", "athlete_season")

LEAN_FILLFACTOR = 100

# ★ THE RATING TREES, PARTIAL (plan step 1.2). Their readers:
#   rankings.getPerformanceRankings' candidates, _countLive,
#   _rankInResults -- all `speed_rating IS NOT NULL`.
# ⚠ NOT THE TIME INDEXES (rr_board_time_idx, rr_pr_time_idx,
#   rr_pr_sport_time_idx). The PR board's per-distance walk
#   (rankings._prCandidatesPerDistance: `SELECT min(distance) ... WHERE
#   pool AND sport AND distance BETWEEN`) carries no time predicate, so a
#   partial time tree cannot serve it: 10x the buffers in the lab. And
#   they are the full trees that keep every prefix of these four usable.
LEAN_PARTIAL = {
    "ranking_results": {
        "rr_pool_rating_idx": "speed_rating IS NOT NULL",
        "rr_pool_sport_rating_idx": "speed_rating IS NOT NULL",
        "rr_board_rating_idx": "speed_rating IS NOT NULL",
        "rr_perf_dist_rating_idx": "speed_rating IS NOT NULL",
    },
}

# ★ BOARD ORDER (plan step 2), each ending in a deterministic tiebreak so
#   two builds of the same rows lay them out the same way. athlete_season
#   is unique on (person_id, pool, sport, year), so its order is total;
#   ranking_results' (sport, result_id) is its row key and person_id
#   settles the rare copy of one result.
BOARD_ORDER = {
    "ranking_results": "pool, sport, year, speed_rating DESC NULLS LAST, result_id, person_id",
    "athlete_season": "pool, sport, year, mean_rating DESC NULLS LAST, person_id",
}


def leanSpec(table, name, cols, on=None):
    """An index's column spec as the lean build wants it: the rating trees
    gain their predicate. Unchanged when the switch is off, for any other
    index, or when the spec already has a WHERE."""
    if not (lean() if on is None else on):
        return cols
    pred = LEAN_PARTIAL.get(table, {}).get(name)
    if not pred or re.search(r"\bWHERE\b", cols, re.I):
        return cols
    return f"{cols} WHERE {pred}"


# a storage clause that sets the fillfactor and nothing else: as Postgres
# writes it back (`WITH (fillfactor='100')`) or as the build writes it
_FF_ONLY = re.compile(r"\s+WITH\s*\(\s*fillfactor\s*=\s*'?\d+'?\s*\)", re.I)
# any storage clause (a WITH (...) holds no parentheses of its own)
_ANY_WITH = re.compile(r"\s+WITH\s*\([^()]*\)", re.I)


def isBtree(ddl):
    m = re.search(r"\bUSING\s+(\w+)", ddl, re.I)
    return m is None or m.group(1).lower() == "btree"


def withStorage(ddl, on=None):
    """A CREATE INDEX statement with the switch's fillfactor.

    Lean: `WITH (fillfactor = 100)` on a btree, placed where Postgres wants
    it -- after the column list and any INCLUDE, before a WHERE. Off: a
    fillfactor-only clause a lean build left in the catalogue is taken out,
    so the trees go back to Postgres' default and the rollback is complete.
    With nothing to strip (today's catalogue) the statement is unchanged.
    Another index type, or a clause with other options, is left alone."""
    if not isBtree(ddl) or (_ANY_WITH.search(ddl) and not _FF_ONLY.search(ddl)):
        return ddl
    bare = _FF_ONLY.sub("", ddl)
    if not (lean() if on is None else on):
        return bare
    clause = f" WITH (fillfactor = {LEAN_FILLFACTOR})"
    m = re.search(r"\s+WHERE\s", bare, re.I)
    if m:
        return bare[:m.start()] + clause + bare[m.start():]
    return bare + clause


def indexSig(ddl):
    """An index's column list and predicate, normalised for comparison:
    from the first "(", storage clause removed, then spaces and
    parentheses removed, lower case.

    ⚠ THE STORAGE CLAUSE IS STRIPPED (2026-11-08, the lean build).
      pg_indexes writes a fillfactor back as
      `... (person_id) WITH (fillfactor='100')`; left in, no lean index
      would match its _CANONICAL_INDEXES entry and verifyIndexes would call
      every one of them missing and refuse the swap. Fillfactor is a
      storage detail: what a query can use is the columns and predicate.
    ⚠ PARENTHESES ARE STRIPPED, NOT JUST SPACES (2026-09-22). pg_indexes
      writes a partial index's predicate back WRAPPED --
      `(event_kind, mark DESC) WHERE (event_kind IS NOT NULL)`."""
    i = ddl.find("(")
    tail = ddl[i:] if i >= 0 else ddl
    tail = _ANY_WITH.sub("", tail)
    # ⚠ AND THE DEFAULT ORDERINGS (2026-11-10): pg_indexes writes
    #   `grade ASC NULLS LAST` back as plain `grade`, and `x DESC NULLS FIRST`
    #   as `x DESC` -- the defaults are dropped. e0126da's as_sort_grade_idx
    #   pair was stated with them, so verifyIndexes called both missing on
    #   every build, rebuilt them, still could not match, and REFUSED the
    #   athlete_season swap (tests/test_rr_layout_flags.py, 8 failures).
    #   Dropped here the way Postgres drops them; DESC and a non-default
    #   NULLS clause stay, because those change the index.
    tail = re.sub(r"(?i)\s+ASC\s+NULLS\s+LAST\b", "", tail)
    tail = re.sub(r"(?i)\s+DESC\s+NULLS\s+FIRST\b", " DESC", tail)
    tail = re.sub(r"(?i)\s+ASC\b", "", tail)                # ASC NULLS FIRST -> NULLS FIRST
    return re.sub(r"[\s()]", "", tail).lower()


def alignRank(typlen, typalign):
    """Sort key for the narrow column order: fixed-width columns first,
    widest alignment first (d=8, i=4, s=2, c=1), variable-width last. A
    fixed column never waits on padding behind a short text."""
    if typlen is None or int(typlen) < 0:
        return 9
    return {"d": 0, "i": 1, "s": 2, "c": 3}.get(typalign, 4)
