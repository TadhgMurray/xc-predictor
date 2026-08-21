# Project: xc-predictor
# File:    scripts/triage_split_divisions.py
# Purpose: the TIER-3 AUTO LANE (designed 7/13, built 7/14). For divisions the
#          classifier calls BIMODAL or PARTIAL -- more than one race sharing a
#          (meet, div) bucket -- no division-wide value can be right, because
#          there is no single truth. This tool works at the row grain instead:
#
#            1. invert EACH spiked row's own swing into an implied distance
#               (sign rule: rated FAST => ran SHORTER than label; rated SLOW
#               => ran LONGER),
#            2. snap each implied distance to a canonical race distance,
#            3. if a subgroup coherently snaps to ONE distance (>=80% agree,
#               >=5 rows -- the clump-independence bar), emit per-row
#               _RESULT_OVERRIDE pins for exactly those rows,
#            4. guard every proposal with RAW-TIME PHYSICS (winner pace not
#               beyond world class, tail pace not slower than a walk) -- the
#               check the 9308 lesson proved immune to bracket contamination.
#
#          Incoherent subgroups are NOT pinned; they are reported for the
#          human queue (usually _DISTANCE_DROP). Clean rows are never touched:
#          the label is right for them (the 26785 rule).
#
#          Precedent: the 9889/9888 combined divisions, split per-result into
#          337 _RESULT_OVERRIDE entries. This mechanizes that surgery.
#
# READ-ONLY by default; --write emits result_override_<sport>.py for
# apply_triage (which needs the one-line routing addition documented at the
# bottom of this header):
#     ("result_override_{s}.py", "_RESULT_OVERRIDE_ADDITIONS",
#      "_RESULT_OVERRIDE_{S}"),
#
# USAGE
#   python scripts\triage_split_divisions.py --sport XC --pair 225743 901707 --pair 33116 143724
#   python scripts\triage_split_divisions.py --sport XC --pairs-file zoo.txt --write

import argparse
import importlib.util
import os
import statistics
import sys
from importlib.machinery import SourceFileLoader

sys.path.insert(0, "scripts")
from database import getConn, initPool

_TABLE = {"XC": "results", "TF": "results_tf"}

# ★ THE LABEL DISTANCE, FROM WHEREVER IT ACTUALLY LIVES.
#
#   This tool used to read the label out of _DISTANCE_OVERRIDES only, and skip
#   any division without one -- "this lane is for overridden zoo divisions".
#   That was true when the zoo WAS the overridden set. propose_distances
#   --merged now finds merged divisions across the whole corpus, and 222 of
#   the 320 it verified have no override at all: they are merged at their
#   SCRAPED distance. Skipping those left the tool unable to fix the cases it
#   was built for.
#
# ⚠ THE ORDER MATTERS AND IT IS THE BACKFILL'S ORDER. A hand-written override
#   beats the scraped column, because that is what normalisation used, and the
#   swing being inverted here was measured against exactly that number. Read
#   them in any other order and every implied distance is wrong by their ratio.
#
# ! tfrrs KEEPS ITS DISTANCE IN A JSON BLOB keyed by div_id as a STRING, and
#   every college cross country meet is tfrrs. Without this half, the college
#   side of the sport reports "no distance on file" and is skipped.
_LABEL_SQL = {
    "XC": """
        SELECT COALESCE(
            (SELECT m.distance FROM meets m
              WHERE m.meet_id = %(meet)s AND m.div_id = %(div)s
                AND m.distance IS NOT NULL LIMIT 1),
            (SELECT (mt.division_distances -> %(divtext)s ->> 'distance')::float
               FROM meets_tfrrs mt
              WHERE mt.meet_id = %(meet)s AND mt.sport = 'XC' LIMIT 1)
        )
    """,
    # Track carries its distance in the event name, not a column, and this
    # tool has no event parser. TF divisions still need an override.
    "TF": None,
}


def labelDistance(cur, sport, meet, div, overrides):
    """(metres, where_it_came_from) for a division, or (None, why not)."""
    pinned = overrides.get((meet, div))
    if pinned:
        return float(pinned), "dist_override"
    sql = _LABEL_SQL.get(sport)
    if sql is None:
        return None, "no override, and TF has no distance column to fall back on"
    cur.execute(sql, {"meet": meet, "div": div, "divtext": str(div)})
    row = cur.fetchone()
    if row and row[0]:
        return float(row[0]), "the distance tables"
    return None, "no distance on file anywhere"
# ⚠ THE DISTANCE-LAW EXPONENT, AND XC'S USED TO BE 1.00. The normaliser uses
#   K = 1.06 everywhere else -- propose_distances.K, audit_overrides.K, the
#   anchor arithmetic -- so a 1.00 here made this tool disagree with every
#   other estimator in the project. TF's true exponent is per pool (1.06-1.22
#   fitted); 1.10 is the stand-in it has always used.
_B = {"XC": 1.06, "TF": 1.10}

_SPIKE = 15.0                          # |gap%| beyond which a row is "spiked"
_SNAP_TOL = 0.06                       # 6% snap gate, same as pass-2 triage
_MIN_SUBGROUP = 5                      # clump-independence bar
_COHERENCE = 0.80                      # full-coherence bar: pin all agreeing rows
_MAJORITY = 0.60                       # majority lane: pin ONLY the agreeing rows,
                                       # leave outliers flagged for the next pass.
                                       # Exists because fast-side inversion amplifies
                                       # per-row noise (+/-15pt gap spread at +65%
                                       # swings implied distance ~+/-9%), so one real
                                       # race can sit at 60-75% agreement with its
                                       # MEDIAN dead on a canonical distance.
_MAJORITY_MAX_MED_ERR = 0.035          # majority lane also demands the median snap
                                       # be this tight -- a loose center is scatter.

_SNAPS = [1500, 1600, 1609, 1800, 2000, 2414, 2500, 3000, 3200, 3218.7,
          4000, 4180, 4828, 5000, 5149, 5500, 6000, 6437, 7000, 8000,
          8046.7, 10000]

# Raw-time physics rails (s per mile). Winner faster than ~3:50/mi is beyond
# world class; a tail slower than ~17:00/mi is slower than a walk. A proposed
# distance that puts the subgroup outside these rails is WRONG regardless of
# what the statistics say.
_WINNER_FLOOR = 230.0
_TAIL_CEIL = 1020.0
_MILE = 1609.34


# ================================================================== #
# CHUNK 1 -- EVIDENCE: per-row gaps WITH result ids and raw times
# ================================================================== #

def _rows(cur, table, meet, div):
    """Same population as the classifier/--dump, plus result_id and raw time
    so verdicts land on rows and physics can see the clock."""
    cur.execute(f"""
        WITH here AS (
            SELECT r.result_id, r.time_seconds,
                   COALESCE(r.person_id, r.athlete_id) AS ident,
                   r.speed_rating AS sr_here, r.date
            FROM {table} r
            WHERE r.meet_id = %s AND r.div_id = %s
              AND r.speed_rating > 0
              AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
        )
        SELECT h.result_id, h.time_seconds, h.sr_here,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r2.speed_rating)
                FROM {table} r2
                WHERE COALESCE(r2.person_id, r2.athlete_id) = h.ident
                  AND r2.speed_rating > 0 AND r2.date <> h.date) AS own_med
        FROM here h
    """, (meet, div))
    out = []
    for rid, t, sr, om in cur.fetchall():
        if om and t:
            out.append((rid, float(t), (sr / om - 1.0) * 100.0))
    return out


# _whyEmpty
# Purpose : say why a division produced nothing, instead of printing rows=0 and
#           moving on.
#
# ⚠ rows=0 IS NOT "THIS DIVISION IS FINE". It means this tool could not form an
#   opinion, which reads identically to "no anomaly found" in the output and is
#   the opposite claim. --merged flags a division from a GROUP shift measured
#   against a pool; this tool needs each athlete's OWN other races. A division
#   can be genuinely merged and still be mute here -- unlinked athletes, a
#   one-race field, nothing rated. Those want a different tool, not a shrug.
#
# ! Only ever called on the empty ones, so the extra scan costs nothing on the
#   divisions that had something to say.
def _whyEmpty(cur, table, meet, div):
    cur.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE r.speed_rating > 0),
               count(*) FILTER (WHERE COALESCE(r.person_id, r.athlete_id)
                                      IS NOT NULL),
               count(*) FILTER (WHERE r.time_seconds IS NOT NULL)
        FROM {table} r
        WHERE r.meet_id = %s AND r.div_id = %s
    """, (meet, div))
    total, rated, ident, timed = cur.fetchone()
    if not total:
        return "no results at all under this meet/div"
    if not rated:
        return f"{total} results, none rated"
    if not ident:
        return f"{total} results, {rated} rated, none linked to a person"
    if not timed:
        return f"{total} results, {rated} rated, no clock on any of them"
    return (f"{total} results ({rated} rated, {ident} linked) but no athlete "
            f"has a rated race on another date to be compared against")


# ================================================================== #
# CHUNK 2 -- INVERSION + SNAP (per row)
# ================================================================== #

# _impliedPerRow
# Purpose : one row's implied true distance from its own swing.
#
# ★ THE EXACT INVERSION, NOT THE FIRST-ORDER ONE. Derived from the same chain
#   everything else uses:
#
#       nt     = t * (anchor / d) ^ K
#       rating = M / nt = (M / t) * (d / anchor) ^ K
#
#   so running d_true while the label says d_label scales the rating by
#   (d_label / d_true)^K, and inverting gives
#
#       d_true = d_label * (rating_true / rating_here) ^ (1/K)
#              = d_label * (1 / (1 + gap)) ^ (1/K)
#
# ⚠ THE OLD FORM WAS label * (1 + |gap|) ON THE SLOW SIDE, and that is a
#   different function, not a rounding of this one. 1/(1-g) against (1+g)
#   agree to a percent at small swings and diverge fast:
#
#       gap -15%    correct  5628 m    old  5552 m     -1.3%
#       gap -25%    correct  6333 m    old  6035 m     -4.7%
#       gap -42%    correct  8071 m    old  6856 m    -15.1%
#       gap -60%    correct 11460 m    old  7725 m    -32.6%
#
#   Which is why the four Detweiller Park divisions -- whose own athletes say
#   they ran about 8,040 m -- were being snapped to 7000. The FAST side was
#   never more than 2.8% out over the same range, because there the old
#   expression was already label / (1+g)^(1/b) -- algebraically this one. All
#   that changed for it is b, 1.00 -> 1.06. Only the slow branch was a
#   different function.
#
# ! A gap at or below -100% is not a slower race, it is a broken row. Clamped
#   rather than allowed to divide by zero.
def _impliedPerRow(label_m, gap_pct, b):
    g = max(float(gap_pct), -99.0) / 100.0
    return label_m * (1.0 / (1.0 + g)) ** (1.0 / b)


def _snap(implied):
    """Nearest canonical distance and its relative error; None if outside the
    6% gate -- an unsnappable row must not vote."""
    best = min(_SNAPS, key=lambda s: abs(s - implied))
    err = abs(best - implied) / best
    return (best, err) if err <= _SNAP_TOL else (None, err)


# ================================================================== #
# CHUNK 3 -- SUBGROUP VERDICTS (fast and slow judged independently)
# ================================================================== #

# _subgroup
# Purpose : all spiked rows on one side, judged against ONE group snap: the
#           median implied distance is snapped once, and each row agrees iff
#           its own implied is within the 6% gate of that group snap.
#           Why not per-row plurality voting: adjacent canonical distances
#           (5000/5149, 1600/1609, 3200/3218.7) sit ~1-3% apart -- closer than
#           per-row noise -- so plurality SPLITS one coherent race's votes
#           between neighbors and wrongly reports incoherence (caught by the
#           unit tests before this ever ran on real data).
#           Returns (verdict, snap, member rows, diagnostics).
def _subgroup(rows, label_m, b, side):
    members = [(rid, t, g) for rid, t, g in rows
               if (g > _SPIKE if side == "fast" else g < -_SPIKE)]
    if len(members) < _MIN_SUBGROUP:
        return "TOO_SMALL", None, members, {}
    implied = {rid: _impliedPerRow(label_m, g, b) for rid, t, g in members}
    group_snap, snap_err = _snap(statistics.median(implied.values()))
    if group_snap is None:
        return "NO_SNAP", None, members, {"median_err": round(snap_err, 3)}
    agree = [(rid, t) for rid, t, g in members
             if abs(implied[rid] - group_snap) / group_snap <= _SNAP_TOL]
    diag = {"snap": group_snap, "agree": len(agree), "of": len(members),
            "median_snap_err": round(snap_err, 3)}
    if len(agree) < _MIN_SUBGROUP or len(agree) < _MAJORITY * len(members):
        return "INCOHERENT", None, members, diag
    if len(agree) < _COHERENCE * len(members):
        if snap_err > _MAJORITY_MAX_MED_ERR:
            return "INCOHERENT", None, members, diag
        return "MAJORITY", group_snap, agree, diag
    return "COHERENT", group_snap, agree, diag


# _physics
# Purpose : the bracket-immune check. Under the proposed distance, the
#           subgroup's fastest raw time must not beat world class and its
#           slowest must not be slower than a walk.
def _physics(times, dist_m):
    per_mile = [t / (dist_m / _MILE) for t in times]
    lo, hi = min(per_mile), max(per_mile)
    ok = lo >= _WINNER_FLOOR and hi <= _TAIL_CEIL
    return ok, lo, hi


# ================================================================== #
# CHUNK 4 -- ORCHESTRATION + OUTPUT
# ================================================================== #

def _importByPath(path, name="_mod"):
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description="Per-row splitter for merged/"
                                 "mixed divisions (tier-3 auto lane).")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--pair", nargs=2, type=int, action="append",
                    metavar=("MEET", "DIV"))
    ap.add_argument("--pairs-file")
    ap.add_argument("--corrections",
                    default=os.path.join("engine", "corrections.py"))
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--write", action="store_true",
                    help="emit result_override_<sport>.py (default: report only)")
    args = ap.parse_args()

    pairs = list(map(tuple, args.pair or []))
    if args.pairs_file:
        # ⚠ COMMENTS AND TRAILING FIELDS BOTH HAVE TO SURVIVE. The old parser
        #   did `m, d = line.split()` on every non-blank line, so a work list
        #   with a header -- which is what propose_distances --pairs-out
        #   writes, naming the command that consumes it -- died on line 1 with
        #   a ValueError about unpacking. A file somebody can read has to be a
        #   file this can read.
        for lineno, line in enumerate(open(args.pairs_file, encoding="utf-8"),
                                      start=1):
            text = line.split("#", 1)[0].strip()
            if not text:
                continue
            bits = text.split()
            if len(bits) < 2:
                print(f"  {args.pairs_file}:{lineno}: not a 'meet div' pair, "
                      f"skipped: {line.strip()!r}")
                continue
            pairs.append((int(bits[0]), int(bits[1])))
    if not pairs:
        sys.exit("no divisions given: --pair or --pairs-file")

    overrides = _importByPath(args.corrections, "_corr") \
        ._DISTANCE_OVERRIDES_BY_SPORT[args.sport]
    n_skipped = n_mute = 0
    table, b = _TABLE[args.sport], _B[args.sport]

    pins, drop_queue = {}, []
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        for meet, div in pairs:
            label, whence = labelDistance(cur, args.sport, meet, div, overrides)
            if label is None:
                print(f"{meet}/{div}: skipped -- {whence}")
                n_skipped += 1
                continue
            rows = _rows(cur, table, meet, div)
            print(f"\n== {meet}/{div}  label={label} (from {whence})  "
                  f"rows={len(rows)} ==")
            if not rows:
                print(f"  MUTE: {_whyEmpty(cur, table, meet, div)}")
                n_mute += 1
                continue
            div_pins = 0
            for side in ("fast", "slow"):
                verdict, snap, members, votes = _subgroup(rows, label, b, side)
                if verdict == "TOO_SMALL":
                    continue
                if verdict not in ("COHERENT", "MAJORITY"):
                    print(f"  {side:<5} {verdict}: votes={votes} -- human "
                          f"queue (likely _DISTANCE_DROP territory)")
                    drop_queue.append((meet, div, side, verdict))
                    continue
                ok, lo, hi = _physics([t for _, t in members], snap)
                if not ok:
                    print(f"  {side:<5} snap {snap} FAILS PHYSICS "
                          f"(pace {lo/60:.2f}-{hi/60:.2f} min/mi) -- NOT pinned")
                    drop_queue.append((meet, div, side, "PHYSICS_FAIL"))
                    continue
                extra = ""
                if verdict == "MAJORITY":
                    extra = (f" [MAJORITY: {votes['of'] - votes['agree']} "
                             f"outliers left flagged for next pass]")
                print(f"  {side:<5} {verdict} -> {snap}m for {len(members)} rows "
                      f"(pace {lo/60:.2f}-{hi/60:.2f} min/mi, sane){extra}")
                for rid, _ in members:
                    pins[rid] = (snap, meet, div, side)
                    div_pins += 1
            clean = sum(1 for _, _, g in rows if abs(g) <= _SPIKE)
            print(f"  clean rows untouched: {clean}   pinned here: {div_pins}")
        conn.rollback()

    print(f"\nTOTAL: {len(pins)} per-row pins proposed; "
          f"{len(drop_queue)} subgroups to the human queue"
          + (f"; {n_skipped} divisions skipped for want of a distance"
             if n_skipped else "")
          + (f"; {n_mute} divisions MUTE -- this tool could form no opinion, "
             f"which is not the same as finding them clean"
             if n_mute else ""))
    for item in drop_queue:
        print(f"  human queue: meet/div {item[0]}/{item[1]} {item[2]} ({item[3]})")

    if args.write and pins:
        path = os.path.join(args.dir, f"result_override_{args.sport.lower()}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# GENERATED by triage_split_divisions.py -- per-row\n"
                    "# distance pins for merged/mixed divisions. Each row's\n"
                    "# distance comes from its OWN swing inversion, physics-\n"
                    "# guarded. Merge into _RESULT_OVERRIDE_"
                    f"{args.sport}.\n"
                    "_RESULT_OVERRIDE_ADDITIONS = {\n")
            for rid, (snap, meet, div, side) in sorted(pins.items()):
                f.write(f"    {rid}: ({snap}, None),  "
                        f"# {side} subgroup, meet/div {meet}/{div}, "
                        f"split 7/14\n")
            f.write("}\n")
        print(f"wrote {path}")
    elif args.write:
        print("nothing to write.")


if __name__ == "__main__":
    main()