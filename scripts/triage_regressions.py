# Project: xc-predictor
# File:    scripts/triage_regressions.py
# Purpose: READ-ONLY classifier for flagged divisions.
#
# ============================================================================
# 2026-07-15 -- THE FIX THAT UNBLOCKS EVERYTHING
#
# THE BUG
#   _overridesFor read _DISTANCE_OVERRIDES_BY_SPORT -- the OVERRIDE dict.
#   But backfill_normalize._resolveDistanceGender (line ~1085) resolves:
#
#       override = _DISTANCE_OVERRIDES_BY_SPORT[sport].get(key)
#       if override is not None:   return override
#       if distance_src == "event_short":   return distanceFromEventShort(...)   # TF
#       dist = meet_distances.get(row[_DIV])                                     # XC anet
#       if dist is not None:   return dist
#       if src == "tfrrs":     return tfrrs_blob[(meet, div)]["distance"]        # XC tfrrs
#
#   So the distance the RULER USED is the override ONLY when one exists. For
#   9,626 XC divisions and for ALL of TF there is no override -- the backfill
#   used meets.distance or distanceFromEventShort instead.
#
#   triage printed ov=None for those, _impliedDistance had no anchor, and it
#   DRAFTED NOTHING. The repair path was dead by construction: not because the
#   data was ambiguous, but because triage was reading the wrong dict.
#
# THE FIX
#   _effectiveDistances mirrors _resolveDistanceGender exactly. Every division
#   now has an anchor, so _impliedDistance (77.8% accurate vs source metadata,
#   per audit_snapper.py) can draft a repair for ALL 9,626 XC divisions and for
#   all 4,705 TF ones.
#
#   The console column is still labelled `ov=` so adjudicate's _ROW_RE keeps
#   matching -- but it is now the EFFECTIVE distance, which is what every
#   downstream calculation actually needed.
# ============================================================================
#
# USAGE
#   python scripts\triage_regressions.py --sport XC --from-scored --limit 500
#   python scripts\triage_regressions.py --sport TF --from-scored --limit 500

import argparse
import importlib.util
import math
import os
import statistics
import sys
from importlib.machinery import SourceFileLoader

sys.path.insert(0, "engine")
from database import getConn, initPool
# The SAME parser the backfill uses for TF. Importing it (not reimplementing)
# is what keeps triage's anchor identical to the ruler's.
from event_parse import distanceFromEventShort

_TABLE = {"XC": "results", "TF": "results_tf"}

_SPIKE = 15.0

# MEASURED, not chosen. calibrate_shape.py sampled 397 divisions the detector
# does NOT flag: gap-IQR p50=6.3, p90=10.4, p99=16.5. Above 10.4 the field did
# not move together.
_NORMAL_IQR = 10.4

# FAN THRESHOLD (2026-07-18). A division whose residual gap-IQR exceeds this did
# NOT move as one factor -- its runners are individually off their own form
# (slow race, ringers, corrupt times), so its median is NOT a distance signal
# and no distance draft may be auto-emitted. score_suspects measured the honest
# baseline IQR at 4.67% and gates at 1.8x = 8.4%; we use the slightly stricter
# _NORMAL_IQR (10.4, calibrate_shape's p90) as the fan line so a division must
# clear the honest p90 before it is called a fan. 163697/678411 (IQR ~16%) is
# the worked example. Kept as its own name, not reusing _NORMAL_IQR inline, so
# the two roles (shape classifier vs draft gate) can diverge if calibration says so.
_FAN_IQR = _NORMAL_IQR

# Standard race distances (m). Middle-school XC runs 800/1000/1200 and TF runs
# the full track card, so the table must reach well below 1500 -- a snapper
# whose floor is 1500 drafted 1500 for an 800m race (implied ~990) because it
# had no candidate below its own floor.
_SNAPS = [
    # track / short XC -- MS races and TF events
    400, 500, 600, 800, 1000, 1200, 1500, 1600, 1609,
    # mid
    1800, 2000, 2400, 2414, 2500, 2816, 3000, 3200, 3218.7,
    # XC main card
    4000, 4180, 4800, 4828, 5000, 5149, 5500, 6000, 6437, 7000,
    8000, 8046.7, 10000,
]

# Sports whose div_id identifies a RACE, so a (meet, div) override has a single
# well-defined point of application.
# TF is absent BY CONSTRUCTION, not by tuning: 0.9 -- its div_id is a CONTAINER
# ("Boys Varsity" = every event at the meet), and an override applies AT THE
# LOADER to every row in the bucket. 5.2 measured what that does: winner paces
# of 0:06-1:59 per mile, i.e. 3000m stamped onto an 11-second dash.
_DIV_OVERRIDE_SPORTS = ("XC",)


def _draftsAllowed(sport):
    # Purpose   : may this sport receive a drafted _DISTANCE_OVERRIDES line?
    # Arguments : sport -- "XC" or "TF".
    # Output    : bool.
    # Note      : one function so the rule lives in ONE place. `in` on a tuple
    #             is an ALLOWLIST -- a new sport is refused until it is named,
    #             rather than silently inheriting XC's assumptions.
    return sport in _DIV_OVERRIDE_SPORTS


# ================================================================== #
# CHUNK 1 -- INPUT: which divisions to classify
# ================================================================== #

def _importByPath(path, name="_mod"):
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pairsFromWorksheet(path):
    """diag's percentile crop: OVERRIDE+REGRESSION only. p[2]=meet p[3]=div."""
    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) > 11 and p[10] == "OVERRIDE" and p[11] == "REGRESSION":
                pairs.append((int(p[2]), int(p[3])))
    return pairs


def _readTsvHeader(path):
    """Map column NAME -> index. Header-driven so a schema change fails loudly."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                cols = line.lstrip("#").strip().split("\t")
                return {name.strip(): i for i, name in enumerate(cols)}
    sys.exit(f"{path}: no '#' header line found")


def _pairsFromScored(path, only_overrides=False, min_n=8, limit=0):
    """
    Every division the Z-CUT flagged. score_suspects models the null as
    c_j ~ N(0, tau^2 + SE_j^2) and cuts by FDR, so unlike diag's percentile
    (which flags ~1% forever) this CAN reach zero.
      XC: |z|>4 -> 9,207 divisions, FDR 0.31%   TF: 4,705, FDR 0.53%
    Sorted by |z| descending, so --limit takes the WORST first.
    """
    h = _readTsvHeader(path)
    for need in ("meet_id", "div_id", "z"):
        if need not in h:
            sys.exit(f"{path}: expected column '{need}'. Found: {sorted(h)}")

    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) <= max(h.values()):
                continue
            if only_overrides and h.get("already") is not None:
                if p[h["already"]].strip().upper() != "OVERRIDE":
                    continue
            if h.get("n") is not None:
                try:
                    if int(float(p[h["n"]])) < min_n:
                        continue
                except ValueError:
                    continue
            try:
                rows.append((abs(float(p[h["z"]])),
                             int(p[h["meet_id"]]), int(p[h["div_id"]])))
            except ValueError:
                continue

    rows.sort(reverse=True)
    if limit:
        rows = rows[:limit]
    return [(m, d) for _z, m, d in rows]


# ================================================================== #
# CHUNK 2 -- THE ANCHOR: what distance did the RULER actually use?
# ================================================================== #

def _overridesFor(sport, corr_path):
    """The override dict only. corrections.py's *_BY_SPORT views are re-pointed
    at the final bindings by a block at the bottom of that file (7/15) --
    without it, appended rebinds orphaned this view."""
    mod = _importByPath(corr_path, "_corr")
    return mod._DISTANCE_OVERRIDES_BY_SPORT[sport]


def _sourceCensus(cur, pairs):
    """
    Purpose : which SOURCE(s) wrote the rated rows in each (meet, div) bucket.
    Output  : list of (meet_id, div_id, source, n).
    Why     : 0.1 -- "meet_id is NOT a key. Identity is (meet_id, sport,
              source)." The backfill has row[_SRC] on every row; triage only has
              bare (meet, div) pairs off the scored TSV. This query recovers the
              one field the routing decision needs.
    Note    : GROUP BY, not DISTINCT ON. A bucket written by BOTH sources is a
              real possibility (0.1) and must be DETECTED, not silently resolved
              by whichever row Postgres hands back first.
              speed_rating > 0 keeps the rows the RULER used -- the population
              _gaps measures.
    """
    if not pairs:
        return []
    values = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, r.source, count(*) AS n
        FROM results r
        JOIN (VALUES {values}) v(m, d) ON v.m = r.meet_id AND v.d = r.div_id
        WHERE r.speed_rating > 0
        GROUP BY r.meet_id, r.div_id, r.source
    """)
    return cur.fetchall()


def _sourcesFor(cur, pairs):
    """
    Purpose : (meet, div) -> 'anet' | 'tfrrs' | None.
    Output  : dict. None where the bucket has no rated rows, or -- loudly --
              where TWO sources wrote it, which no routing rule can resolve.
    Note    : setdefault(k, set()) collects the sources per key. A single-source
              bucket routes; a mixed one gets None and is ledgered, because
              picking one would be exactly the coincidence 0.2 warns about.
    """
    by_key = {}
    for meet_id, div_id, src, _n in _sourceCensus(cur, pairs):
        by_key.setdefault((meet_id, div_id), set()).add(src)

    out, mixed, empty = {}, 0, 0
    for key in pairs:
        srcs = by_key.get(key)
        if not srcs:
            out[key] = None
            empty += 1
        elif len(srcs) == 1:
            out[key] = next(iter(srcs))   # next(iter(s)) pulls the lone member
        else:
            out[key] = None
            mixed += 1

    if mixed or empty:
        print(f"[source] {mixed:,} buckets written by BOTH sources (no anchor), "
              f"{empty:,} with no rated rows")
    return out


def _anetXc(cur, pairs):
    """
    Purpose : anet XC distance -- meets.distance via the GLOBAL div_id.
    Output  : dict (meet, div) -> distance | None.
    Note    : keying on div_id ALONE is CORRECT here AND ONLY HERE. For anet it
              IS the global id and it carries the distance (2.3). Callers must
              hand this function anet pairs only -- see _effectiveXc.
    """
    if not pairs:
        return {}
    divs = sorted({d for _m, d in pairs})
    cur.execute("SELECT div_id, distance FROM meets WHERE div_id = ANY(%s)",
                (divs,))
    by_div = {d: dist for d, dist in cur.fetchall()}
    return {(m, d): by_div.get(d) for m, d in pairs}


def _blobXc(cur, pairs):
    """
    Purpose : tfrrs XC distance -- meets_tfrrs.division_distances, COMPOSITE key.
    Output  : dict (meet, div) -> distance | None.
    ★ NO FALLBACK TO meets. 2.4: "the ONE resolution path this enables ... and
      it NEVER TOUCHES meets." The old code consulted the blob only where
      meets.div_id MISSED -- and meets holds div_id 1 and 3 (both meet_id 1,
      both 5000.0), so 15,639 tfrrs meets took a stranger's distance and never
      reached their own blob, which said "Men's 8k" -> 8000. 8000/5000 = 1.6.
      That was the -40% cluster.
    Note    : jsonb keys are ALWAYS strings (2.4) -> int(div_str) to key against
              a row's integer div_id. A missing entry stays None: the row dies
              as no_distance downstream, which is right. NULL beats stale.
    """
    if not pairs:
        return {}
    cur.execute("SELECT meet_id, division_distances FROM meets_tfrrs "
                "WHERE meet_id = ANY(%s) AND division_distances IS NOT NULL",
                (sorted({m for m, _d in pairs}),))
    blobbed = {}
    for meet_id, blob in cur.fetchall():
        for div_str, info in (blob or {}).items():
            if not isinstance(info, dict):
                continue
            try:
                dist = info.get("distance")
                if dist is not None:
                    blobbed[(meet_id, int(div_str))] = float(dist)
            except (TypeError, ValueError):
                continue
    return {k: blobbed.get(k) for k in pairs}


def _effectiveXc(cur, pairs):
    """
    Purpose : the XC anchor -- routed by SOURCE, exactly as the ruler routes it.
    Output  : dict (meet, div) -> distance | None.
    THE MIRROR: backfill_normalize._resolveDistanceGender now reads
              if row[_SRC] == "tfrrs": return _tfrrsXcDistance(...)
              return _anetXcDistance(...)
    This function must make the SAME decision or triage's `ov=` column stops
    being the effective distance -- and that column is what adjudicate's no-op
    test diffs against. A mirror that drifts is worse than no mirror.
    """
    sources = _sourcesFor(cur, pairs)
    anet = [k for k in pairs if sources.get(k) == "anet"]
    tfrrs = [k for k in pairs if sources.get(k) == "tfrrs"]

    eff = {k: None for k in pairs}          # unroutable stays None, loudly
    eff.update(_anetXc(cur, anet))
    eff.update(_blobXc(cur, tfrrs))
    print(f"[anchor XC] {len(anet):,} anet (meets.distance)  "
          f"{len(tfrrs):,} tfrrs (blob)")
    return eff


def _parseOne(ev):
    # Purpose   : one event_short -> metres, or None.
    # Arguments : ev -- the free-text event name, e.g. "Men\'s 800 Meters".
    # Output    : float metres, or None if the parser refused it.
    # Note      : distanceFromEventShort returns a (metres, gender) TUPLE. The
    #             isinstance guard survives a future signature change to a bare
    #             number. Gender is ignored here; _blobGender owns that.
    try:
        res = distanceFromEventShort(ev)
    except Exception:
        return None
    dist = res[0] if isinstance(res, tuple) else res
    return float(dist) if dist else None


def _eventShortCensus(cur, pairs):
    """
    Purpose : every DISTINCT event_short in each (meet, div) bucket, with a row
              count.
    Output  : list of (meet_id, div_id, event_short, n).
    WHY GROUP BY, NOT DISTINCT ON:
              the old query was `SELECT DISTINCT ON (meet_id, div_id) ...
              ORDER BY meet_id, div_id`. DISTINCT ON keeps the first row per
              group AS DETERMINED BY ORDER BY -- and that ORDER BY held only the
              grouping keys, with no tie-break. So Postgres returned an
              ARBITRARY event_short and this function called it "the distance of
              the division". For meet 580895 div 1 that bucket is
              300mh|shot|javelin. It rolled dice and drafted 4180m.
              14: a MAX(col) sample answers "what SHAPE", not "what MIX" -- use
              GROUP BY. DISTINCT ON is MAX(col) in a different hat.
    Note    : speed_rating > 0 keeps only the rows the RULER used -- the same
              population _gaps measures. Field events never got a rating, so
              they never reach here.
    """
    values = ",".join(cur.mogrify("(%s,%s)", p).decode() for p in pairs)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, r.event_short, count(*) AS n
        FROM results_tf r
        JOIN (VALUES {values}) v(m, d) ON v.m = r.meet_id AND v.d = r.div_id
        WHERE r.speed_rating > 0
        GROUP BY r.meet_id, r.div_id, r.event_short
    """)
    return cur.fetchall()


def _distinctDistances(rows):
    """
    Purpose : census rows -> {(meet, div): {distance, distance, ...}}.
    Output  : dict keyed (meet, div), value a SET of parsed distances.
    Note    : setdefault(k, set()) creates the set on first sight of a key and
              returns the existing one after -- one line instead of an
              `if k not in out` dance. round(d, 1) collapses float noise so
              1609.34 and 1609.3 count as ONE distance, not two.
              An unparseable event_short is SKIPPED, not counted as a distinct
              distance: "the parser could not read this" is not evidence that
              the bucket holds two races.
    """
    out = {}
    for meet_id, div_id, ev, _n in rows:
        dist = _parseOne(ev)
        if dist is None:
            continue
        out.setdefault((meet_id, div_id), set()).add(round(dist, 1))
    return out


def _storedTf(cur, pairs):
    """
    Purpose : the TF anchor -- a distance ONLY when the bucket holds exactly
              one, among the rows the ruler actually rated.
    Output  : dict (meet, div) -> distance | None.
    THE IDEA: 0.9 says a TF div_id is a CONTAINER, so it has no single distance.
              Rather than ASSERT that, MEASURE it. One distinct distance -> an
              honest anchor. More than one -> None, and say so. A None anchor
              means _impliedDistance has nothing to invert and no draft is
              produced: the repair path closes itself, from data, not from a
              hardcoded sport rule.
    Ledger  : exclusion stays visible at every layer.
    """
    by_key = _distinctDistances(_eventShortCensus(cur, pairs))

    out, mixed, empty = {}, 0, 0
    for key in pairs:
        dists = by_key.get(key)
        if not dists:
            out[key] = None
            empty += 1                     # no rated rows, or none parseable
        elif len(dists) == 1:
            out[key] = next(iter(dists))   # next(iter(s)) pulls the lone member
        else:
            out[key] = None
            mixed += 1

    print(f"[anchor TF] {len(pairs) - mixed - empty:,} single-distance  "
          f"{mixed:,} MIXED (container -- 0.9, no anchor)  "
          f"{empty:,} no rated/parseable rows")
    return out


def _effectiveDistances(cur, sport, pairs, corr_path):
    """
    Purpose : THE ANCHOR. The distance backfill_normalize ACTUALLY USED for
              each division -- not the override dict.
    Mirrors  _resolveDistanceGender's order exactly (as rewritten 7/16):
              override  ->  TF:  distanceFromEventShort
                        ->  XC:  ROUTE BY SOURCE, then
                              anet  -> meets.distance by GLOBAL div_id
                              tfrrs -> the blob by COMPOSITE (meet, div)
              The source gate is the fix: div_id's two id spaces COLLIDE
              (0.2), and the old meets-first order sent 15,639 tfrrs meets
              to meet 1's 5000.0 instead of their own blob.
    Output  : dict (meet, div) -> distance | None.
    Why     : without this, 9,626 XC divisions and 100% of TF have ov=None,
              _impliedDistance has nothing to invert, and triage drafts
              NOTHING. This one function is what makes the repair path exist.
    """
    if sport == "TF":
        eff = _storedTf(cur, pairs)
    else:
        eff = _effectiveXc(cur, pairs)

    overrides = _overridesFor(sport, corr_path)   # override wins over all of it
    for key in pairs:
        ov = overrides.get(key)
        if ov is not None:
            eff[key] = ov
    return eff


# ================================================================== #
# CHUNK 3 -- EVIDENCE
# ================================================================== #

def _gaps(cur, table, meet, div):
    """Per-runner gap%: this race's rating vs the runner's own other-date
    median. Identical population to diag's --dump."""
    cur.execute(f"""
        WITH here AS (
            SELECT COALESCE(r.person_id, r.athlete_id) AS ident,
                   r.speed_rating AS sr_here, r.date
            FROM {table} r
            WHERE r.meet_id = %s AND r.div_id = %s
              AND r.speed_rating > 0
              AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
        )
        SELECT h.sr_here,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r2.speed_rating)
                FROM {table} r2
                WHERE COALESCE(r2.person_id, r2.athlete_id) = h.ident
                  AND r2.speed_rating > 0 AND r2.date <> h.date) AS own_med
        FROM here h
    """, (meet, div))
    return [(sr / om - 1.0) * 100.0 for sr, om in cur.fetchall() if om]


# ================================================================== #
# CHUNK 4 -- CLASSIFY
# ================================================================== #

def _gapSpread(sorted_gaps):
    """The IQR of the gap distribution -- the number _SPIKE throws away."""
    n = len(sorted_gaps)
    return sorted_gaps[(3 * n) // 4] - sorted_gaps[n // 4]


def _classify(gaps):
    """Buckets -> verdict.

    A distance error is a property of the COURSE and every runner ran the same
    course, so a wrong label multiplies EVERY gap by the same factor: the
    distribution SLIDES, its shape does not change. Two populations in one
    bucket INFLATES the spread. Median = how far off; IQR = did they move
    together. PARTIAL is asked BEFORE UNIFORM because a blown-out spread
    refutes a div-wide label error no matter how many rows are spiked.
    """
    n = len(gaps)
    if n < 8:
        return "TOO_SMALL", {}

    s = sorted(gaps)
    iqr = _gapSpread(s)
    pos = sum(g > _SPIKE for g in gaps)
    neg = sum(g < -_SPIKE for g in gaps)
    clean = n - pos - neg
    stray = max(1, round(0.05 * n))

    stats = {"n": n, "pos": pos, "neg": neg, "clean": clean, "iqr": iqr,
             "med_pos": statistics.median([g for g in gaps if g > _SPIKE] or [0]),
             "med_neg": statistics.median([g for g in gaps if g < -_SPIKE] or [0])}

    if pos >= 0.15 * n and neg >= 0.15 * n:
        return "BIMODAL", stats
    if iqr > _NORMAL_IQR and (pos + neg) >= 0.15 * n:
        return "PARTIAL", stats
    if neg >= 0.60 * n and pos <= stray:
        return "UNIFORM_NEG", stats
    if pos >= 0.60 * n and neg <= stray:
        return "UNIFORM_POS", stats
    if (pos + neg) < 0.20 * n:
        return "FEW_SPIKES", stats
    return "UNCLEAR", stats


def _impliedDistance(effective_m, med_gap):
    """
    What distance would this field have had to run to be rated the way it was?

    INVERSION: the gap is a RATIO -- rating_here/rating_own = 1+g -- so recover
    the distance by DIVIDING by it, not multiplying by its complement. At
    g=-20%: old linear gave ov*1.20, correct gives ov/0.80 = ov*1.25. For
    6437 that is 7724 (snaps 8000) vs 8046 (snaps 8046.7 = 5 miles, the true
    value). Off by one snap step, 341 of 623 times.

    SNAP: nearest in LOG space. A distance error is multiplicative, so
    "nearest" must be a ratio -- boundary at sqrt(a*b), not (a+b)/2.

    audit_snapper.py, 623 drafts vs source metadata: 25.7% -> 77.8%.
    """
    ratio = 1.0 + med_gap / 100.0
    if not effective_m or ratio <= 0.01:
        return None, []
    implied = effective_m / ratio
    near = sorted(_SNAPS, key=lambda s: abs(math.log(s / implied)))[:2]
    return implied, near


# ================================================================== #
# CHUNK 5 -- REPORT + DRAFTS
# ================================================================== #

_ADVICE = {
    "UNIFORM_NEG": "wrong snap, label too SHORT -> page-verify drafted value",
    "UNIFORM_POS": "check POOL/GENDER first (9308 lesson); else label too LONG",
    "BIMODAL":     "merged races -> _RESULT_OVERRIDE split or _DISTANCE_DROP",
    "PARTIAL":     "mixed content; label right for clean half -> per-result pins ONLY",
    "FEW_SPIKES":  "row-level (cooked rows), not a label problem",
    "UNCLEAR":     "shape ambiguous -> read the full --dump",
    "TOO_SMALL":   "field too small to classify -> read the full --dump",
}


def _collectPairs(args):
    """Assemble the crop from whichever input flags were given."""
    pairs = list(map(tuple, args.pair or []))
    if args.pairs_file:
        for line in open(args.pairs_file, encoding="utf-8"):
            if line.strip():
                m, d = line.split()
                pairs.append((int(m), int(d)))
    if args.from_worksheet:
        ws = os.path.join(args.dir, f"suspects_div_{args.sport.lower()}.tsv")
        got = _pairsFromWorksheet(ws)
        print(f"percentile crop: {len(got):,} from {ws}")
        pairs += got
    if args.from_scored:
        sc = os.path.join(args.dir, f"scored_div_{args.sport.lower()}.tsv")
        got = _pairsFromScored(sc, only_overrides=args.only_overrides,
                               min_n=8, limit=args.limit)
        print(f"z-cut crop: {len(got):,} from {sc}"
              + (f" (worst {args.limit})" if args.limit else ""))
        pairs += got

    seen, out = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _draftLine(meet, div, eff, med_neg):
    """The one-line fix for an honest wrong-snap. eff = the EFFECTIVE distance
    the ruler used, so this now works for divisions with no override."""
    if not eff:
        return None
    implied, near = _impliedDistance(eff, med_neg)
    if not implied:
        return None
    return (f"    ({meet}, {div}): {near[0]},  # was {eff}; field "
            f"med {med_neg:+.1f}% -> implied ~{implied:.0f} "
            f"(next snap {near[1]}). PAGE-VERIFY before pasting. "
            f"classifier 7/15 recip+geom")


def _reportDrafts(sport, drafting, drafts):
    """
    Purpose   : print the drafts, or explain PRECISELY why there are none.
    Arguments : sport    -- "XC" or "TF".
                drafting -- bool from _draftsAllowed.
                drafts   -- list of formatted lines from _draftLine.
    Note      : early returns, one branch per reason. "No drafts" has TWO
                different meanings and they must not print the same text: a
                STRUCTURAL refusal is not an empty crop.
    """
    if not drafting:
        print(f"\nNO DRAFTS -- {sport} div_id is a CONTAINER, not a race (0.9).\n"
              f"  A (meet, div) override applies AT THE LOADER to every row in\n"
              f"  the bucket, including field events this classifier never\n"
              f"  measured (5.2: winner paces of 0:06/mile).\n"
              f"  The verdicts above are still valid. Route them:\n"
              f"    FEW_SPIKES      -> _RESULT_DROP_{sport}\n"
              f"    BIMODAL/PARTIAL -> the splitter (_RESULT_OVERRIDE_{sport})")
        return

    if not drafts:
        print("\nno drafts (no UNIFORM_NEG with a resolvable effective distance)")
        return

    print(f"\nDRAFTED _DISTANCE_OVERRIDES edits ({len(drafts):,}; "
          f"page-verify each):")
    for d in drafts:
        print(d)


def main():
    ap = argparse.ArgumentParser(description="Classify flagged divisions by "
                                 "gap-distribution shape. Read-only.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--from-worksheet", action="store_true")
    ap.add_argument("--from-scored", action="store_true",
                    help="classify every division the Z-CUT flagged")
    ap.add_argument("--only-overrides", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap at the N worst by |z| (0 = no cap)")
    ap.add_argument("--pair", nargs=2, type=int, action="append",
                    metavar=("MEET", "DIV"))
    ap.add_argument("--pairs-file")
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--corrections",
                    default=os.path.join("engine", "corrections.py"))
    args = ap.parse_args()

    pairs = _collectPairs(args)
    if not pairs:
        sys.exit("no divisions given: --from-scored, --from-worksheet, "
                 "--pair, or --pairs-file")

    table = _TABLE[args.sport]
    drafting = _draftsAllowed(args.sport)   # the 0.9 gate, decided ONCE
    out_rows, drafts = [], []
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        # THE ANCHOR -- what the ruler actually used, per division.
        eff_map = _effectiveDistances(cur, args.sport, pairs, args.corrections)
        n_anchored = sum(1 for k in pairs if eff_map.get(k))
        print(f"effective distance resolved for {n_anchored:,}/{len(pairs):,}")

        for meet, div in pairs:
            gaps = _gaps(cur, table, meet, div)
            verdict, s = _classify(gaps)
            eff = eff_map.get((meet, div))

            # label stays `ov=` so adjudicate's _ROW_RE keeps matching. fan= is
            # the draft gate: iqr past the honest p90 means the field did NOT
            # move as one factor, so its median is not a distance signal. The
            # adjudicator reads this and refuses a distance draft when FAN.
            iqr_val = s.get("iqr", 0)
            fan = "FAN" if iqr_val > _FAN_IQR else "-"
            line = (f"{meet:>8} {div:>9}  ov={str(eff):>8}  {verdict:<12} "
                    f"n={s.get('n', 0):>4} +{s.get('pos', 0):<4} "
                    f"-{s.get('neg', 0):<4} clean={s.get('clean', 0):<4} "
                    f"iqr={iqr_val:>5.1f} fan={fan}  {_ADVICE[verdict]}")
            print(line)
            out_rows.append(line)

            if verdict == "UNIFORM_NEG" and drafting:
                d = _draftLine(meet, div, eff, s["med_neg"])
                if d:
                    drafts.append(d)
        conn.rollback()                      # read-only, always

    out = os.path.join(args.dir, f"regression_verdicts_{args.sport.lower()}.tsv")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(out_rows) + "\n")
    print(f"\nwrote {out}")

    _reportDrafts(args.sport, drafting, drafts)


if __name__ == "__main__":
    main()