#!/usr/bin/env python
# Project: xc-predictor
# File:    scripts/triage_gender_splits.py
# Purpose: mixed-GENDER divisions are not automatically mixed-DISTANCE. A real
#          co-ed race at one distance needs NOTHING done to it: pool is
#          (level, gender, sport) and is already resolved per row, and both
#          halves genuinely share a course, so they belong in one difficulty
#          cell. Splitting it would invent a problem.
#
#          So this tool sorts mixed-gender divisions into three piles and only
#          acts on one:
#
#            SAME_DISTANCE  a genuine co-ed race. Nothing is emitted.
#            SEPARATE       the two genders ran different races under one
#                           div_id -> per-row _RESULT_OVERRIDE pins for the
#                           subgroup whose distance differs from the label.
#            AMBIGUOUS      human queue.
#
# ============================================================================
# ★ WHAT THIS REVISION THREW OUT, AND WHY  (read before trusting v1's output)
# ============================================================================
# v1 classified by the FEMALE/MALE MEDIAN TIME RATIO. The idea: at one distance
# that ratio is some stable R0, so observed_ratio / R0 is the distance ratio.
#
# MEASURED ON ALL 356 MIXED DIVISIONS, R0 DOES NOT EXIST. The histogram came
# back a broad UNIMODAL hump from 0.94 to 1.44 -- no tight mode to read off.
# The ratio depends on field depth and composition; a youth co-ed race and a
# college co-ed race have genuinely different ones. v1's _SAME_TOL = 0.04
# against a +/-20% spread would have called nearly every division DIFFERENT,
# which is the opposite of the truth.
#
# ★ THE LESSON: v1 reached for INFERENCE (ratios, an assumed exponent, a snap
#   gate) while an EXACT signal sat unused in a column it was already reading.
#
# THE EXACT SIGNAL -- DUPLICATED PLACES
#   A single race numbers its finishers 1..N ONCE. Two races stacked into one
#   div_id each number from 1, so every place appears twice. Verified by hand
#   on meet 26785 div 0: places 1..67 each appearing exactly twice, two
#   monotone non-overlapping time sequences 400s apart, both source='tfrrs'
#   (so NOT a cross-source dedup twin -- twins carry the same time from two
#   different sources).
#
#   And the test that ties it to GENDER specifically: split the rows by gender
#   and ask whether each gender's places become unique. For 26785 they do --
#   M places 1..41 unique, F places 1..51 unique. That is the definition of
#   "these two genders ran two separate races", stated as counting rather than
#   as a threshold on ability.
#
# WHAT REPLACED THE INFERENCE
#   Proving the SPLIT and assigning the DISTANCES are different problems. The
#   place test proves the split exactly but says nothing about metres. So the
#   distances now come from the meet's OWN METADATA -- meets.distance across
#   the meet's divisions (anet) or division_distances (tfrrs) -- and the
#   shorter distance goes to the faster sub-race. If the metadata offers only
#   one distance there is no evidence to assign from, and that is AMBIGUOUS,
#   not a guess.
#
# WHY NOT triage_split_divisions.py
#   Two structural blockers, neither a bug:
#     1. `label = overrides.get((meet, div))` -- it skips divisions with no
#        distance override on file ("this lane is for overridden zoo
#        divisions"). Meet 26785 has none.
#     2. its evidence is each row's speed_rating swing against that athlete's
#        median from OTHER races. These fields are largely `Unattached`
#        athletes who race nowhere else, so `own_med` is NULL and _rows drops
#        them.
#   Its _physics IS reused verbatim, imported not copied.
#
# READ-ONLY by default. --write emits result_override_gender_<sport>.py.
#   ⚠ apply_triage needs a routing line, exactly as the splitter's header
#     documents for its own file:
#       ("result_override_gender_{s}.py", "_RESULT_OVERRIDE_ADDITIONS",
#        "_RESULT_OVERRIDE_{S}"),
#     A DIFFERENT FILENAME from the splitter's on purpose -- same name, and
#     whichever tool runs second clobbers the first.
#
# USAGE (from the project root)
#   scripts\genderpairs.txt from the earlier --discover run is still valid;
#   the detector has not changed, so there is no need to re-run the census.
#
#     python scripts\triage_gender_splits.py --pairs-file scripts\genderpairs.txt
#     python scripts\triage_gender_splits.py --pairs-file scripts\genderpairs.txt --write

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn, initPool
from speed_ratings_db import COLUMNS, _xcQuery, _splitVenueKey, \
    loadCanonicalNames
from triage_split_divisions import _physics, _importByPath, _MIN_SUBGROUP, \
    _WINNER_FLOOR, _TAIL_CEIL, _MILE


MIN_TIME, MAX_TIME = 600.0, 3600.0     # streamResults' defaults, kept identical

# A place set is called UNIQUE when at most this fraction of its rows sit on a
# duplicated place. Not zero: genuine ties happen in XC, and a scraper
# occasionally repeats one row. 10% is far below the 50% a two-race merge
# produces, so the two cases cannot be confused.
_DUP_TOL = 0.10

# A replacement distance must differ from the label by at least this much to be
# worth pinning. Kills the 5000-vs-5005 and 4828-vs-4683 pairs, which are one
# race carrying two metadata spellings -- pinning those adds rows to
# _RESULT_OVERRIDE_XC and changes nothing.
_MIN_DIFF = 0.05


# ================================================================== #
# CHUNK 1 -- DISCOVERY: which divisions carry both genders
# ================================================================== #

# _discover
# Purpose : the census, as a worklist. UNCHANGED from v1 -- this is the
#           population asked for: divisions where BOTH genders are present.
# Detail  : same LATERAL the engine uses, so a row's gender here is the gender
#           the engine will pool it under. The >= _MIN_SUBGROUP bar on BOTH
#           sides is the clump-independence argument: a handful of mis-genders
#           is a data error, not a merged race.
def _discover(cur, table, source=None):
    src = "AND r.source = %s" if source else ""
    params = (source,) if source else ()
    cur.execute(f"""
        SELECT r.meet_id, r.div_id,
               count(*) FILTER (WHERE a.gender = 'M') AS men,
               count(*) FILTER (WHERE a.gender = 'F') AS women
        FROM {table} r
        LEFT JOIN LATERAL (
            SELECT a.gender FROM athletes a
            WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
              AND a.gender IN ('M','F')
            ORDER BY a.school LIMIT 1
        ) a ON TRUE
        WHERE r.normalized_time IS NOT NULL {src}
        GROUP BY 1, 2
        HAVING count(*) FILTER (WHERE a.gender = 'M') >= {_MIN_SUBGROUP}
           AND count(*) FILTER (WHERE a.gender = 'F') >= {_MIN_SUBGROUP}
        ORDER BY count(*) DESC
    """, params)
    return [(m, d) for m, d, _men, _women in cur.fetchall()]


# _savePairs / _loadPairs
# Purpose : make the expensive discovery run ONCE, EVER. _discover is a GROUP
#           BY over 34.5M rows with a LATERAL into athletes; tolerable once,
#           not on every invocation of a tool run several times.
# Detail  : same whitespace-separated "MEET DIV" format the splitter's
#           --pairs-file uses, so the two tools' worklists interoperate.
def _savePairs(path, pairs):
    with open(path, "w", encoding="utf-8") as f:
        for meet, div in pairs:
            f.write(f"{meet} {div}\n")
    print(f"[cache] wrote {len(pairs)} pairs to {path}")


def _loadPairs(path):
    pairs = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            meet, div = line.split()
            pairs.append((int(meet), int(div)))
    print(f"[cache] read {len(pairs)} pairs from {path}")
    return pairs


# _tuneSession
# Purpose : the one Postgres setting this tool cannot run without.
# Detail  : ★ the planner estimates 1 row per (meet_id, div_id) in `results`;
#           the truth is 58.9. Every join in _xcQuery is on that key, so the
#           planner picks nested loops and the per-division query degrades.
#           SET is SESSION-scoped, so this must run on the SAME connection as
#           the queries -- which is why it takes a cursor rather than living
#           in a config file.
def _tuneSession(cur):
    cur.execute("SET enable_nestloop = off")
    print("[tune] enable_nestloop = off for this session")


# ================================================================== #
# CHUNK 2 -- ONE DIVISION: gender, place, raw time, label distance
# ================================================================== #

# _loadDivision
# Purpose : per-row (gender, raw time, PLACE) plus the LABEL distance the
#           pipeline currently believes.
# Detail  : the label comes from the ENGINE'S OWN venue key, decoded by the
#           established _splitVenueKey. Appending to _xcQuery's text is legal
#           because that text ends inside its WHERE clause, so no join is
#           touched and any future fix to the loader follows automatically.
#
#           `place` is the new column and the whole basis of this revision.
#           Sentinels are filtered here so the place logic never sees a 0.
def _loadDivision(cur, meet, div, names):
    from speed_ratings_db import preparePackQuery
    preparePackQuery(cur, "XC")        # the temp tables the query joins
    sql = _xcQuery(MIN_TIME, MAX_TIME, tw="") + \
        "\n          AND r.meet_id = %s AND r.div_id = %s"
    cur.execute(sql, (meet, div))
    engine = [dict(zip(COLUMNS, r)) for r in cur.fetchall()]

    cur.execute("""
        SELECT result_id, time_seconds, place FROM results
        WHERE meet_id = %s AND div_id = %s
          AND time_seconds > 0 AND time_seconds < 99999
          AND place IS DISTINCT FROM 0 AND place IS NOT NULL
    """, (meet, div))
    raw = {rid: (float(t), int(p)) for rid, t, p in cur.fetchall()}

    label, by_gender = None, defaultdict(list)
    for row in engine:
        if row["venue"] and label is None:
            _name, _cid, label = _splitVenueKey("XC:" + row["venue"], names)
        hit = raw.get(row["result_id"])
        if hit and row["gender"] in ("M", "F"):
            by_gender[row["gender"]].append((row["result_id"], hit[0], hit[1]))
    return label, by_gender


# _candidateDistances
# Purpose : every distance this MEET's own metadata offers. This is the only
#           evidence available for WHICH distance each sub-race ran.
# Output  : sorted list of distinct positive metres.
# Detail  : two sources, two homes.
#           anet  -- `meets` is keyed on div_id and FANS OUT over meet_id, so
#                    the meet's several divisions each carry their own
#                    distance. That fan-out is the evidence here, not noise.
#           tfrrs -- division_distances is jsonb, so its keys are STRINGS;
#                    jsonb_each is used rather than a str(div_id) lookup
#                    because we want EVERY division's distance, not this one's.
def _candidateDistances(cur, meet, div):
    cur.execute("""
        SELECT DISTINCT distance FROM meets
        WHERE meet_id = %s AND distance IS NOT NULL AND distance > 0
    """, (meet,))
    found = {float(d) for (d,) in cur.fetchall()}

    cur.execute("""
        SELECT (value ->> 'distance')::real
        FROM meets_tfrrs, jsonb_each(COALESCE(division_distances, '{}'::jsonb))
        WHERE meet_id = %s AND sport = 'XC'
    """, (meet,))
    found |= {float(d) for (d,) in cur.fetchall() if d}

    return sorted(found)


# ================================================================== #
# CHUNK 3 -- THE EXACT TEST: do the places duplicate, and by gender?
# ================================================================== #

# _dupRate
# Purpose : fraction of rows sitting on a place that some other row in the
#           same set also holds.
# Syntax  : len(places) - len(set(places)) counts the SURPLUS rows. Divided by
#           the total it is 0.0 for a clean 1..N sequence and ~0.5 for two
#           complete races stacked.
def _dupRate(places):
    if not places:
        return 0.0
    return (len(places) - len(set(places))) / len(places)


# _placeVerdict
# Purpose : the whole classification, as counting.
# Output  : ("SAME" | "SEPARATE" | "UNCLEAR", diagnostics)
# Detail  : three states, and the logic is a conjunction, not a threshold.
#           combined places UNIQUE            -> one race numbered once -> SAME
#           combined DUPLICATED, each gender
#             internally UNIQUE               -> two races split BY GENDER
#           anything else                     -> UNCLEAR. Includes the case
#             where a single gender's own places duplicate, which means more
#             than two races are stacked (varsity+JV inside one gender) and a
#             gender split would not separate them.
def _placeVerdict(by_gender):
    men = [p for _rid, _t, p in by_gender["M"]]
    women = [p for _rid, _t, p in by_gender["F"]]
    combined = men + women

    diag = {"n_M": len(men), "n_F": len(women),
            "dup_all": round(_dupRate(combined), 3),
            "dup_M": round(_dupRate(men), 3),
            "dup_F": round(_dupRate(women), 3)}

    if diag["dup_all"] <= _DUP_TOL:
        return "SAME", diag
    if diag["dup_M"] <= _DUP_TOL and diag["dup_F"] <= _DUP_TOL:
        return "SEPARATE", diag
    return "UNCLEAR", diag


# ================================================================== #
# CHUNK 4 -- REASSIGN ONLY WHERE THE LABEL IS REFUTED
# ================================================================== #
#
# ★ WHAT v2 GOT WRONG HERE, MEASURED ON ALL 356
#   v2 FORCED a pairing: take the two candidate distances and give the shorter
#   one to whichever gender had the faster median. That confounds DISTANCE with
#   SEX. Men are ~12% faster than women AT THE SAME DISTANCE, so a meet running
#   a separate men's 5K and a separate women's 5K -- entirely normal, and not a
#   defect at all -- had the men handed the shorter candidate automatically.
#   Result: 18 of 30 proposals gave the WOMEN the LONGER race, which in XC
#   essentially never happens.
#
#   ★ THE LESSON: duplicated places prove TWO RACES. They do not prove TWO
#     DISTANCES. Two separate races at one distance need no correction.
#
# THE RULE NOW: change nothing unless the LABEL ITSELF is refuted for exactly
# one subgroup, and let the evidence -- not a pairing -- name the replacement.
#
# ★ WHY THE WINNER FLOOR AND NOT THE TAIL CEILING
#   The two rails are not equally trustworthy. A winner faster than world class
#   is IMPOSSIBLE: that is proof the label overstates the distance. A tail
#   slower than a walk is merely UNUSUAL -- on a middle-school field with
#   walkers it is real, and v2's tail failures (17.5-23 min/mi) were rejecting
#   legitimate fields. So the floor DETECTS and the ceiling only VETOES a
#   proposed replacement. Neither rail moves, so no prior verdict is re-judged.
#
#   It also matches the damage: a too-LONG label makes a field look fast and
#   floats it to the top of every board. A too-SHORT label makes it look slow
#   and pollutes nothing.

# _storedFor
# Purpose : the distances ALREADY pinned on this subgroup's own rows.
# Output  : sorted list of distinct metres, usually empty.
#
# ★ WHY THIS IS A CANDIDATE SOURCE AT ALL
#   _candidateDistances reads the MEET's metadata and nothing else, so it never
#   sees what a previous pass concluded about THESE ROWS. At meet 26785, 31 of
#   the 51 women already carried (4828, None) from an earlier correction while
#   the metadata offered 5000 -- so the tool proposed 5000 for the other 20 and
#   would have left ONE FIELD normalising against two distances 3.4% apart.
#
#   Deferring to stored evidence per ROW is right, but it produces an
#   INCOHERENT FIELD when the stored evidence covers only part of one. The fix
#   is the same trick as the gender recovery, one grain down: the rows that
#   have a value tell you the value for the rows that do not.
def _storedFor(rows, existing):
    found = set()
    for rid, _t, _p in rows:
        value = existing.get(rid)
        if value:
            metres = value[0] if isinstance(value, tuple) else value
            if metres:
                found.add(float(metres))
    return sorted(found)


# _winnerPace
# Purpose : the fastest row's seconds-per-mile under a proposed distance.
def _winnerPace(rows, dist):
    return min(t for _rid, t, _p in rows) / (dist / _MILE)


# _refuted
# Purpose : which genders' subgroups are impossible at the label distance.
# Output  : list of gender letters, usually empty.
def _refuted(by_gender, label):
    return [g for g, rows in by_gender.items()
            if _winnerPace(rows, label) < _WINNER_FLOOR]


# _viable
# Purpose : candidate distances that rescue a refuted subgroup.
# Detail  : three gates, all necessary.
#           1. differs from the label by >= _MIN_DIFF -- a 0.1% "difference" is
#              a metadata spelling, not a distance.
#           2. SHORTER than the label -- a winner beating world class means the
#              field ran less far than the label claims. Longer cannot help.
#           3. passes BOTH rails at the new distance, the ceiling included --
#              here it is a veto on a proposal, not evidence about the label.
def _viable(rows, label, candidates):
    out = []
    for d in candidates:
        if abs(d - label) / label < _MIN_DIFF or d >= label:
            continue
        ok, _lo, _hi = _physics([t for _rid, t, _p in rows], d)
        if ok:
            out.append(d)
    return out


# _assign
# Purpose : the verdict for a division whose split is already proven.
# Output  : (verdict, {gender: metres} | None, diagnostics)
#           MERGED_OK  two races, one distance, nothing to fix
#           SEPARATE   exactly one subgroup refuted and exactly one rescue
#           AMBIGUOUS  everything else -- human queue, never a guess
def _assign(by_gender, candidates, label, existing):
    if label is None:
        return "AMBIGUOUS", None, {"why": "no label distance"}

    bad = _refuted(by_gender, label)

    # Nothing impossible at the label: two races at one distance. Fine.
    if not bad:
        return "MERGED_OK", None, {}

    # Both refuted means the label is wrong for the WHOLE division -- that is a
    # division-wide distance problem, a different lane, not a gender split.
    if len(bad) == 2:
        return "AMBIGUOUS", None, {"why": "label refuted for BOTH genders "
                                          "-- division-wide, not a split"}

    gender = bad[0]
    rows = by_gender[gender]

    # Stored verdicts on these very rows join the candidate pool.
    stored = _storedFor(rows, existing)
    options = _viable(rows, label, sorted(set(candidates) | set(stored)))

    # ★ PRECEDENCE, applied to the CHOICE and not just to the row. If any
    #   viable option is one this subgroup is already partly pinned to, that
    #   option wins outright -- adopting it makes the whole field coherent,
    #   whereas picking a metadata value would leave the field split in two.
    preferred = [d for d in options if d in stored]
    if preferred:
        options = preferred

    if len(options) != 1:
        return "AMBIGUOUS", None, {
            "why": f"{gender} refuted at {label:.0f} "
                   f"({_winnerPace(rows, label)/60:.2f} min/mi "
                   f"winner) but {len(options)} viable replacements"}

    other = "F" if gender == "M" else "M"
    return "SEPARATE", {gender: options[0], other: label}, {
        "refuted": gender,
        "from_stored": options[0] in stored,
        "winner_at_label": round(_winnerPace(rows, label) / 60, 2)}


# ================================================================== #
# CHUNK 5 -- EMIT
# ================================================================== #

# _pinShape
# Purpose : match whatever tuple shape _RESULT_OVERRIDE_<SPORT> already uses,
#           instead of guessing -- AND hand back the existing dict so the
#           caller can honour precedence.
# Output  : (shape_fn, existing_dict)
def _pinShape(corrections_path, sport):
    existing = {}
    try:
        mod = _importByPath(corrections_path, "_corr_shape")
        existing = getattr(mod, f"_RESULT_OVERRIDE_{sport}", {}) or {}
        sample = next(iter(existing.values()))
    except Exception:
        sample = (None, None)          # fall back to the splitter's own shape

    arity = len(sample) if isinstance(sample, tuple) else 1
    print(f"[shape] _RESULT_OVERRIDE_{sport} entries are "
          f"{arity}-tuples (sample: {sample!r}); "
          f"{len(existing):,} rows already pinned")
    if arity == 1:
        return (lambda dist, gender: (dist,)), existing
    # Second slot is the gender pin in the resolver's (distance, gender)
    # return shape. We KNOW the gender here, so fill it.
    return (lambda dist, gender: (dist, gender)), existing


# _pinsFor
# Purpose : only rows whose gender's distance DIFFERS from the label need a
#           pin. The matching half is already correct and must not be touched.
# Output  : (pins, conflicts)
#
# ★ PRECEDENCE: page > stored > draft. A result_id that ALREADY carries a
#   _RESULT_OVERRIDE is a STORED verdict and this tool is a DRAFT, so the
#   stored value wins and the row is NOT re-pinned. It is returned as a
#   CONFLICT instead, for the human queue.
#
#   This is not hypothetical. On the first --write, 31 rows at meet 26785
#   already read (4828, None) -- an earlier pass had judged them 3 miles --
#   and this function silently proposed (5000, 'F') over the top of them.
#   apply_triage merges with dict.update(), so last-write-wins and NOTHING
#   reported the overwrite: the run printed "65 pins" and corrections.py grew
#   by 34. Physics cannot separate 4828 from 5000 here (6:02 vs 5:50 per mile
#   for the same winner, both plausible), so the draft has no standing to
#   overrule the stored value.
def _pinsFor(label, assigned, by_gender, shape, existing):
    pins, conflicts = {}, {}
    for gender, dist in assigned.items():
        if label is not None and abs(dist - label) < 1.0:
            continue
        value = shape(int(dist), gender)
        for rid, _t, _p in by_gender[gender]:
            if rid in existing:
                if existing[rid] != value:
                    conflicts[rid] = (existing[rid], value)
                continue                   # stored wins, either way
            pins[rid] = value
    return pins, conflicts


def _write(path, pins, sport):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# GENERATED by triage_gender_splits.py -- per-row distance\n"
                "# pins for divisions where the two genders ran SEPARATE\n"
                "# races under one div_id, proven by duplicated places.\n"
                "# Genuine co-ed races are deliberately absent: they need no\n"
                "# correction.\n"
                f"# Merge into _RESULT_OVERRIDE_{sport}.\n"
                "_RESULT_OVERRIDE_ADDITIONS = {\n")
        for rid, value in sorted(pins.items()):
            f.write(f"    {rid}: {value!r},\n")
        f.write("}\n")
    print(f"wrote {path}  ({len(pins):,} pins)")


# ================================================================== #
# CHUNK 6 -- ORCHESTRATION
# ================================================================== #

# _one
# Purpose : classify a single division. Returns (verdict, pins, line).
# Detail   : kept small so main() reads as a sequence, not a nest.
def _one(cur, meet, div, names, shape, existing):
    label, by_gender = _loadDivision(cur, meet, div, names)
    if len(by_gender.get("M", [])) < _MIN_SUBGROUP or \
       len(by_gender.get("F", [])) < _MIN_SUBGROUP:
        return "SKIPPED", {}, {}, f"  {meet}/{div} too few rows per gender"

    verdict, diag = _placeVerdict(by_gender)
    if verdict == "SAME":
        return "SAME_DISTANCE", {}, {}, None
    if verdict == "UNCLEAR":
        return "AMBIGUOUS", {}, {}, f"  {meet}/{div} places unclear {diag}"

    outcome, assigned, why = _assign(
        by_gender, _candidateDistances(cur, meet, div), label, existing)

    # Two races at one distance. Not a distance bug -- counted, not listed.
    if outcome == "MERGED_OK":
        return "MERGED_OK", {}, {}, None
    if outcome == "AMBIGUOUS":
        return "AMBIGUOUS", {}, {}, f"  {meet}/{div} split proven, {why}"

    pins, conflicts = ({}, {})
    if shape:
        pins, conflicts = _pinsFor(label, assigned, by_gender, shape, existing)
    src = "adopted from stored" if why.get("from_stored") else "from metadata"
    line = (f"  {meet}/{div} label={label:.0f} -> "
            f"{why['refuted']} reassigned to {assigned[why['refuted']]:.0f} "
            f"({src}; winner was {why['winner_at_label']} min/mi at the "
            f"label) {diag}")
    return "SEPARATE", pins, conflicts, line


def main():
    ap = argparse.ArgumentParser(description="Split mixed-gender divisions "
                                 "where the genders ran separate races.")
    ap.add_argument("--sport", choices=["XC"], default="XC")
    ap.add_argument("--discover", action="store_true",
                    help="run the expensive census. Pair with --save-pairs "
                         "and you never need it again.")
    ap.add_argument("--source", help="restrict discovery, e.g. tfrrs")
    ap.add_argument("--pair", nargs=2, type=int, action="append",
                    metavar=("MEET", "DIV"))
    ap.add_argument("--pairs-file",
                    help="read the worklist from a file instead of --discover")
    ap.add_argument("--save-pairs",
                    help="write --discover's worklist here, then reuse it")
    ap.add_argument("--corrections",
                    default=os.path.join("engine", "corrections.py"))
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    names = loadCanonicalNames()
    initPool()

    verdicts, pins, queue, hits = defaultdict(int), {}, [], []
    conflicts = {}
    with getConn() as conn, conn.cursor() as cur:
        _tuneSession(cur)

        pairs = list(map(tuple, args.pair or []))
        if args.pairs_file:
            pairs += _loadPairs(args.pairs_file)
        if args.discover:
            found = _discover(cur, "results", args.source)
            pairs += found
            if args.save_pairs:
                _savePairs(args.save_pairs, found)
        if not pairs:
            sys.exit("no divisions: use --pairs-file, --discover, or --pair")
        print(f"[scan] {len(pairs)} mixed-gender divisions")

        # `existing` is loaded on EVERY run, not just --write: the verdict now
        # depends on what these rows are already pinned to, so a read-only
        # report must reach the same conclusion the write would.
        shape_fn, existing = _pinShape(args.corrections, args.sport)
        shape = shape_fn if args.write else None

        for meet, div in pairs:
            verdict, div_pins, div_conf, line = _one(
                cur, meet, div, names, shape, existing)
            verdicts[verdict] += 1
            pins.update(div_pins)
            conflicts.update(div_conf)
            if verdict == "SEPARATE" and line:
                hits.append(line)
            elif verdict == "AMBIGUOUS" and line:
                queue.append(line)
        conn.rollback()                      # nothing here writes

    print("\n[SEPARATE] label refuted for one gender -- reassigned:")
    for line in hits:
        print(line)

    print("\n[verdicts] " + "  ".join(f"{k}={v}" for k, v in
                                      sorted(verdicts.items())))
    print(f"[human queue] {len(queue)}")
    for line in queue:
        print(line)

    if conflicts:
        print(f"\n[CONFLICT] {len(conflicts)} rows already carry a stored "
              f"_RESULT_OVERRIDE. Stored wins; NOT re-pinned:")
        for rid, (stored, drafted) in list(conflicts.items())[:20]:
            print(f"  {rid}: stored {stored} kept, draft {drafted} discarded")
        if len(conflicts) > 20:
            print(f"  ... and {len(conflicts) - 20} more")

    if args.write and pins:
        _write(os.path.join(args.dir,
                            f"result_override_gender_{args.sport.lower()}.py"),
               pins, args.sport)
    elif args.write:
        print("nothing to write.")


if __name__ == "__main__":
    main()