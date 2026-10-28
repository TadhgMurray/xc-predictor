#!/usr/bin/env python3
"""
wrong_distance.py -- cross country races stored at the wrong distance.

    python engine/wrong_distance.py                     # the ranked list + counts (read only)
    python engine/wrong_distance.py --csv out.csv       # ... and every row to a CSV
    python engine/wrong_distance.py --explain 254287/1015294   # one division, runner by runner
    python engine/wrong_distance.py --write             # the writer: distance_pin (pipeline 04g1b)
    python engine/wrong_distance.py --write --allow-many    # past the circuit breaker (first run)
    python engine/wrong_distance.py --hold anet/MEET/DIV    # owner: keep this division's rows as they are
    python engine/wrong_distance.py --refuse anet/MEET/DIV  # owner: never act on this division
    python engine/wrong_distance.py --release anet/MEET/DIV # owner: back to the writer's judgment

Design: docs/WRONG-DISTANCE-2026-10-25.md.

(owner, 2026-10-25: "Find wrong distances and change them ... never correct
a distance so the race ends up overrated ... over-find rather than
under-find ... I don't want things like what happened to Mt. SAC
happening.")

★ THE EVIDENCE IS RAW TIMES, NOT RATINGS. For every runner in a division:
  their time here at the LISTED distance against their own other races that
  season, each scaled to 5000 m with t ~ d^K:

      e   = ln(time) - K ln(listed / 5000)        one row, "5K-equivalent"
      res = e - median(e over the runner's other races that season)

  A field whose median res is -0.18 ran 18% faster than its own runners
  normally do: a 5K stored as 6K reads (5000/6000)^1.06 = -0.19. Nothing in
  it reads a speed_rating, a course number or an override, so

    - it does not move when a pin is applied (data_checks' race_field
      evidence vanished once its own pin fixed the race, which is why its
      pins had to be "sticky"), and every pin can be re-judged every run;
    - the course and race-day terms cannot absorb part of the error.

★ THE COURSE IS TAKEN OUT BY THE VENUE'S OWN HISTORY. A hard course reads
  slow every year at the same label; a wrong label reads off ONE day. The
  baseline is the median res of the venue's other meets at the same listed
  distance (else the meet's other divisions), and the excess over it is the
  division's.

★ ONLY BIG ERRORS ARE ACTED ON. The proposed distance must be a real one
  (a standard cross country distance, or one the venue has run on 2+ other
  days) and differ from the listed one by BIG_MOVE (12%). The smallest real
  error is 5K vs 6K (18%); 4828 vs 5000 (3.5%), 3000 vs 3218 (7%), 6000 vs
  6437 (7%) and a venue's 2.93 mi vs its 3 mi are label differences:
  counted as small_label and never acted on.

★ DIRECTION. Storing a distance LONGER than the truth overrates. So a
  proposal that LOWERS the distance is the one this exists for; one that
  RAISES it (the field ran slow) would raise ratings and is never a pin
  here -- at most "novote": keep the listed distance (underrated, safe) and
  stop the race voting on the course, which is where a too-short label hurts
  other people (Al Shirley 2026). Among ambiguous candidates, the lower one.
"""
import csv
import math
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

K = 1.06                 # t ~ d^K: the exponent every distance tool here uses

# ---- a runner's reference ----
REF_MIN = 3              # other races in the season before a runner testifies
SUSPECT = 0.03           # pass 1: a division this far off is left out of references

# ---- a division ----
FIELD_MIN = 6            # runners with a reference before the field testifies
PIN_MIN = 10             # ... and before it can be pinned (fewer: listed for review)
CAND = 0.08              # field (or field minus course) this far off = looked at
SHARE = 0.70             # ... with this share of its runners off the same way (3%+)
STRONG_N, STRONG_SHARE = 15, 0.85

# ---- the proposal ----
STANDARD = (1609.0, 2000.0, 2414.0, 3000.0, 3218.0, 4000.0, 4828.0, 5000.0,
            6000.0, 6437.0, 8000.0, 10000.0)
SNAP_TOL = 0.05          # |ln(implied / candidate)|
BIG_MOVE = math.log(1.12)  # |ln(listed / proposed)|: 5K vs 6K is 0.18, 3mi vs 5K 0.035
# ★ NEVER A DISTANCE LONGER THAN THE TIMES SAY (owner, 2026-10-25: "the main
#   thing isn't confidence but that you don't overestimate the distance").
#   A stored distance longer than the truth overrates, so whatever is
#   written may sit at most this far above the field's implied distance
#   (the noise of the estimate); otherwise the next real distance below.
OVERSHOOT = 0.02

# ---- the course baseline ----
VENUE_MIN_MEETS = 2      # other meets at the venue and label before they set the baseline
VENUE_TRUE = 0.05        # a venue whose other days read within this runs true
VENUE_WIDE = 0.12        # ... past this the whole venue reads off: listed, not used as a baseline
SIB_TRUE = 0.04          # the meet's other races within this ran true
# ★ A VENUE THAT READS OFF EVERY DAY IS THE COURSE NUMBER'S JOB (owner,
#   2026-10-25: "should this not just have the difficulty?"). The solve
#   gives each venue and distance its own difficulty, and with enough races
#   it learns even a 20% offset -- a pin there would only take the races'
#   course away. But the difficulty prior (sd ~3.5%, joint_solve) pulls a
#   thin venue most of the way back to normal, and then its races stay
#   overrated. So the last run's ratings decide: a venue-wide fast field
#   whose ratings read within ABSORBED of the runners' own level is left
#   to the difficulty; one still reading higher is pinned.
ABSORBED = 0.05

# ---- combined divisions ----
MIX_MIN = 10             # referenced runners before a split is looked for
MIX_SEP = 0.12           # cluster medians this far apart (time-log)
MIX_CLUSTER = 3          # runners in the smaller cluster, at least ...
MIX_SHARE = 0.15         # ... and this share of the field
MIX_PURE = 0.85          # an attribute value this concentrated in one cluster explains it

PACE_FAST, PACE_SLOW = 0.03, 0.97   # division-median percentile within its pool

ACTION_ORDER = ("pin", "split", "replace", "pin-up?", "novote", "list", "review",
                "keep", "unjudged", "ignore")
HAND_DICT = "_DISTANCE_OVERRIDES_XC"     # the hand-typed overrides in corrections.py

# ---- the writer (docs/WRONG-DISTANCE-2026-10-25.md sections 5-6) ----
# ★ A PIN STAYS ON LESS THAN IT TAKES TO MAKE ONE. CAND (8%) to be looked
#   at, STAY (6%) to be kept: a race sitting near the bar is not written,
#   retired and written again run after run.
STAY = 0.06
# ★ THE CIRCUIT BREAKER. venue_label wrote 14,802 pins in a week; a run
#   that would add or retire more than this writes nothing and says why.
MAX_CHANGES = 300


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def implied(listed, x):
    """The distance a field `x` (time-log excess) off ran, if the label is
    the error: res = K ln(d_true / listed)."""
    return float(listed) * math.exp(x / K)


def candidates(venue_labels=()):
    """Real distances: the standard ones and the venue's own (run on 2+
    other days), deduplicated within 1%."""
    out = sorted(set(STANDARD) | {float(d) for d in venue_labels if d})
    keep = []
    for d in out:
        if not keep or d / keep[-1] - 1 > 0.01:
            keep.append(d)
    return keep


def snap(imp, cands, prefer=()):
    """The candidate for an implied distance, or None. Two within SNAP_TOL
    (4828/5000, 3000/3218, 6000/6437): the one this meet or venue actually
    runs (`prefer`), and otherwise -- or if it runs both -- the LOWER, which
    gives the lower rating. Pure."""
    near = [c for c in cands if abs(math.log(imp / c)) <= SNAP_TOL]
    if not near:
        return None
    known = [c for c in near if any(abs(math.log(c / p)) <= 0.01 for p in prefer if p)]
    return min(known or near)


def baseline(venue_meets, sibs):
    """(baseline, kind, words, venue_wide) for a division's course and day.
    venue_meets: [res] median of each OTHER meet at the venue and label;
    sibs: [res] of the meet's other divisions (n_ref >= FIELD_MIN). kind is
    "venue", "meet" or "none". A baseline past VENUE_WIDE is not a course,
    it is the label of every race there: not used. Pure."""
    if len(venue_meets) >= VENUE_MIN_MEETS:
        b = statistics.median(venue_meets)
        if abs(b) > VENUE_WIDE:
            return 0.0, "none", f"venue reads {100 * b:+.0f}% on {len(venue_meets)} other days", True
        return b, "venue", f"venue {100 * b:+.1f}% on {len(venue_meets)} other days", False
    if sibs:
        b = statistics.median(sibs)
        if abs(b) <= VENUE_WIDE:
            return b, "meet", f"meet's other races {100 * b:+.1f}%", False
    return 0.0, "none", "no course baseline", False


def split2(vals):
    """Best two-cluster split of sorted values (least within-cluster sum of
    squares). (low, high) lists, or None for fewer than 4. Pure."""
    v = sorted(vals)
    n = len(v)
    if n < 4:
        return None
    pre = [0.0]
    pre2 = [0.0]
    for a in v:
        pre.append(pre[-1] + a)
        pre2.append(pre2[-1] + a * a)

    def ss(i, j):
        m = j - i
        s = pre[j] - pre[i]
        return pre2[j] - pre2[i] - s * s / m

    best = min(range(1, n), key=lambda i: ss(0, i) + ss(i, n))
    return v[:best], v[best:]


def mixVerdict(rows, listed, base, cands):
    """A division holding two races. rows: [(res, gender, level)] for the
    referenced runners. Returns None, or a dict with each cluster's size,
    implied and snapped distance, and the attribute that explains the
    split (if one does). Pure."""
    if len(rows) < MIX_MIN:
        return None
    sp = split2([r[0] - base for r in rows])
    if not sp:
        return None
    lo, hi = sp
    small = min(len(lo), len(hi))
    if small < max(MIX_CLUSTER, MIX_SHARE * len(rows)):
        return None
    mlo, mhi = statistics.median(lo), statistics.median(hi)
    if mhi - mlo < MIX_SEP:
        return None
    # the clusters must be clusters: the gap between them wider than
    # either's own spread (a long smear is not two races)
    spread = max(_iqr(lo), _iqr(hi))
    if mhi - mlo < 2.0 * spread:
        return None
    cut = (max(lo) + min(hi)) / 2.0
    out = {"n": len(rows), "clusters": []}
    for name, members, med in (("fast", lo, mlo), ("slow", hi, mhi)):
        imp = implied(listed, med)
        out["clusters"].append({"name": name, "n": len(members), "res": med,
                                "implied": imp, "snap": snap(imp, cands, (listed,))})
    # which attribute explains it
    out["by"] = None
    for idx, attr in ((1, "gender"), (2, "level")):
        groups = {}
        for r in rows:
            if r[idx]:
                groups.setdefault(r[idx], []).append(r[0] - base < cut)
        if len(groups) < 2:
            continue
        fast_vals = [g for g, xs in groups.items() if sum(xs) / len(xs) >= MIX_PURE]
        slow_vals = [g for g, xs in groups.items() if 1 - sum(xs) / len(xs) >= MIX_PURE]
        covered = sum(len(groups[g]) for g in fast_vals + slow_vals)
        if fast_vals and slow_vals and covered >= 0.9 * sum(len(x) for x in groups.values()):
            out["by"] = (attr, sorted(fast_vals), sorted(slow_vals))
            break
    return out


def _iqr(xs):
    if len(xs) < 4:
        return 0.0
    q = statistics.quantiles(xs, n=4)
    return q[2] - q[0]


def pacePct(e50, pcts):
    """Where a division's median 5K-equivalent sits among its pool's
    division medians, 0 (fastest) .. 1. pcts: [(p, value)] ascending. Pure."""
    if e50 is None or not pcts:
        return None
    if e50 <= pcts[0][1]:
        return pcts[0][0]
    for (p0, v0), (p1, v1) in zip(pcts, pcts[1:]):
        if e50 <= v1:
            return p0 + (p1 - p0) * ((e50 - v0) / (v1 - v0) if v1 > v0 else 0.0)
    return pcts[-1][0]


def judge(d, ctx):
    """The verdict on one division. d: the division's numbers (listed,
    n_ref, res, sh_fast, sh_slow, div_name, pace_pct). ctx: venue_meets,
    sibs, sib_labels, venue_labels, raw_hist (other years' e50 at the venue,
    label and pool), e50, mix (mixVerdict or None). Returns a dict: kind, action,
    confidence, points, direction, implied, proposed, x, evidence. Pure."""
    listed = float(d["listed"])
    base, base_kind, base_src, wide = baseline(ctx.get("venue_meets", []), ctx.get("sibs", []))
    ev = []
    out = {"kind": None, "action": "ignore", "confidence": "", "points": 0,
           "direction": "", "implied": None, "proposed": None, "x": None,
           "base": base, "evidence": ""}
    name = (d.get("div_name") or "").lower()
    if "relay" in name:
        out.update(kind="relay", action="review")
        out["evidence"] = "a relay division: leg times, not one race"
        return out
    mix = ctx.get("mix")
    if mix:
        cl = mix["clusters"]
        desc = "; ".join(f"{c['n']} ran like {c['snap'] or c['implied']:,.0f} m" for c in cl)
        by = mix["by"]
        if by:
            desc += f"; split by {by[0]}: {'/'.join(map(str, by[1]))} fast, {'/'.join(map(str, by[2]))} slow"
        out.update(kind="combined", action="split", confidence="high" if by else "medium",
                   evidence=f"two races in one division ({desc}); {base_src}")
        return out
    n_ref, res = d.get("n_ref") or 0, d.get("res")
    if res is None or n_ref < FIELD_MIN:
        return _paceOnly(d, ctx, out)
    x = res - base
    out["x"] = x
    if wide:
        ev.append(base_src)
    if abs(x) < CAND:
        if wide:
            out.update(kind="venue_wide", action="review", evidence="; ".join(ev))
        return out
    share = d["sh_fast"] if x < 0 else d["sh_slow"]
    imp = implied(listed, x)
    out["implied"] = imp
    ev.insert(0, f"{n_ref} runners {100 * x:+.1f}% vs their own races "
                 f"({100 * (share or 0):.0f}% the same way); {base_src}; implies {imp:,.0f} m")
    if (share or 0) < SHARE:
        out.update(kind="mixed_field", action="review", evidence="; ".join(ev))
        return out
    cands = candidates(ctx.get("venue_labels", ()))
    s = snap(imp, cands, list(ctx.get("sib_labels", ())) + list(ctx.get("venue_labels", ())))
    if s is None:
        out.update(kind="off_no_standard", action="review", evidence="; ".join(ev))
        return out
    if math.log(s / imp) > OVERSHOOT:
        # longer than the times say: the next real distance below, or nothing
        below = [c for c in cands if math.log(c / imp) <= OVERSHOOT
                 and math.log(imp / c) <= SNAP_TOL]
        if not below:
            out.update(kind="off_no_standard", action="review", evidence="; ".join(
                ev + [f"the nearest real distance, {s:g}, is longer than the times say"]))
            return out
        s = max(below)
    out["proposed"] = s
    move = math.log(listed / s)
    if abs(move) < BIG_MOVE:
        out.update(kind="small_label", action="ignore",
                   evidence="; ".join(ev) + f" -> {s:g}, a label difference")
        return out
    down = s < listed
    out["direction"] = "down" if down else "up"
    pts = 2 if (n_ref >= STRONG_N and share >= STRONG_SHARE) else 1
    # ! THE MEET'S OTHER RACES TESTIFY ONLY WHEN THEY ARE NOT THE BASELINE:
    #   against the venue's usual when there is one, else against nothing
    sibs = ctx.get("sibs", [])
    if sibs and abs(statistics.median(sibs) - (base if base_kind == "venue" else 0.0)) <= SIB_TRUE:
        pts += 1
        ev.append(f"the meet's other {len(sibs)} races ran true")
    runs_it = False         # does this meet or venue actually run the proposal?
    if any(abs(math.log(sl / s)) <= 0.02 for sl in ctx.get("sib_labels", ()) if sl):
        pts += 1
        runs_it = True
        ev.append(f"another division of the meet is stored at {s:g}")
    vm = ctx.get("venue_meets", [])
    if len(vm) >= VENUE_MIN_MEETS and abs(statistics.median(vm)) <= VENUE_TRUE:
        pts += 1
        ev.append(f"the venue's other {len(vm)} days at {listed:g} ran true")
    if any(abs(math.log(vl / s)) <= 0.02 for vl in ctx.get("venue_labels", ()) if vl):
        pts += 1
        runs_it = True
        ev.append(f"the venue has run {s:g} on other days")
    rh = ctx.get("raw_hist", [])
    if len(rh) >= 2 and ctx.get("e50") is not None:
        dr = ctx["e50"] - statistics.median(rh)
        if (dr < 0) == (x < 0) and abs(dr) >= CAND:
            pts += 1
            ev.append(f"raw median {100 * dr:+.0f}% vs {len(rh)} other years here")
    pp = d.get("pace_pct")
    if pp is not None and ((down and pp <= PACE_FAST) or (not down and pp >= PACE_SLOW)):
        pts += 1
        ev.append(f"field median at the {100 * pp:.1f} percentile of its level at {listed:g}")
    out["points"] = pts
    conf = "high" if pts >= 4 else "medium" if pts == 3 else "low"
    out["confidence"] = conf
    rg, rn = d.get("rating_gap"), d.get("rating_n") or 0
    rated = rg is not None and rn >= FIELD_MIN
    if rated:
        ev.append(f"last run's ratings {100 * rg:+.0f}% vs their own level")
    if down and wide:
        if not rated:
            out.update(kind="venue_wide", action="review", points=pts, evidence="; ".join(
                ev + ["every day here reads fast, and no ratings to show whether the "
                      "course's difficulty takes it"]))
            return out
        if rg <= ABSORBED:
            out.update(kind="venue_wide", action="review", points=pts, evidence="; ".join(
                ev + ["every day here reads fast, but the ratings come out normal: "
                      "the course's difficulty takes it"]))
            return out
        ev.append("every day here reads fast and the difficulty does not take it")
    if down:
        # ★ CONFIDENCE DOES NOT GATE A DOWNWARD PIN (owner, 2026-10-25): it
        #   only lowers a distance, never below what the times say; the
        #   bars above (12% onto a real distance, 70% of the field) and the
        #   runner count are the gate. Confidence is reported.
        act = "pin"
        if n_ref < PIN_MIN:
            act = "review"
            ev.append(f"only {n_ref} runners with other races")
        out.update(kind="wrong_distance", action=act)
    else:
        act = ("pin-up?" if conf == "high" and runs_it
               else "novote" if conf in ("high", "medium") else "review")
        if wide:
            # ! EVERY DAY HERE READS SLOW: a hard course far more often than
            #   a label wrong every year (Steens Mountain +41%). novote on all
            #   of them would leave the course no difficulty: listed only.
            act = "review"
        out.update(kind="wrong_distance", action=act, confidence=conf)
    out["evidence"] = "; ".join(ev)
    return out


def _paceOnly(d, ctx, out):
    """A division with too few referenced runners: only its raw pace
    against its level can speak, and only for review."""
    pp = d.get("pace_pct")
    if pp is None or PACE_FAST / 3 < pp < 1 - PACE_FAST / 3:
        return out
    out.update(kind="pace_outlier", action="review", confidence="low",
               direction="down" if pp < 0.5 else "up")
    out["evidence"] = (f"{d.get('n_rows')} runners ({d.get('n_ref') or 0} with other races); "
                       f"field median at the {100 * pp:.2f} percentile of its level "
                       f"at {float(d['listed']):g}")
    return out


def overrideVerdict(listed, override, imp, n_ref, hand=False):
    """An existing dist_override entry against the evidence at the listed
    distance. Returns (action, why). Pure.

    keep      within SNAP_TOL of what the times say
    replace   LONGER than the times say: it overrates, so it goes, whoever
              wrote it; or a generated one shorter than the times say
    list      a hand-typed one shorter than the times say: it only
              underrates, so it stays and is shown to the owner
    unjudged  too few runners with other races"""
    listed, override = float(listed), float(override)
    if abs(override - listed) < 1:
        return "keep", "agrees with the listed distance"
    flags = []
    if override > listed * 1.01:
        flags.append("RAISES the listed distance")
    if abs(math.log(listed / override)) < BIG_MOVE:
        flags.append("a label-sized move")
    if imp is None or (n_ref or 0) < FIELD_MIN:
        return "unjudged", "; ".join(flags + ["too few runners with other races"])
    off = math.log(override / imp)
    if abs(off) <= SNAP_TOL:
        return "keep", "; ".join(flags + [f"the field implies {imp:,.0f}"])
    if off > 0:
        return "replace", "; ".join(flags + [
            f"longer than the {imp:,.0f} the times say: overrates {100 * K * off:.0f}%"])
    why = f"shorter than the {imp:,.0f} the times say: underrates {100 * K * -off:.0f}%"
    return ("list" if hand else "replace"), "; ".join(flags + [why])


# ------------------------------------------------------------------ #
#  the database: one pass over results, all in temp tables
# ------------------------------------------------------------------ #

_DIV_SQL = r"""
CREATE TEMP TABLE wd_div AS
WITH cc AS (
    SELECT course_name, round(gps_lat::numeric, 5) AS la,
           round(gps_long::numeric, 5) AS lo, max(canonical_id) AS cid
    FROM   course_canonical GROUP BY 1, 2, 3),
a AS (
    SELECT m.source, 0::bigint AS jm, m.div_id::bigint AS div_id,
           max(m.meet_id)::bigint AS meet_id, max(m.distance)::float AS listed,
           max(m.meet_name) AS meet_name, max(m.division) AS div_name,
           max(m.course_name) AS course, max(m.gps_lat) AS la, max(m.gps_long) AS lo
    FROM   meets m
    WHERE  m.source <> 'tfrrs' AND m.distance > 0
    GROUP  BY 1, 2, 3),
t AS (
    SELECT 'tfrrs'::text AS source, mt.meet_id::bigint AS jm, kv.key::bigint AS div_id,
           mt.meet_id::bigint AS meet_id, (kv.value ->> 'distance')::float AS listed,
           mt.meet_name, kv.value ->> 'div_name' AS div_name,
           mt.venue_name AS course, mt.gps_lat AS la, mt.gps_long AS lo
    FROM   meets_tfrrs mt
    CROSS  JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(mt.division_distances) = 'object'
                                        THEN mt.division_distances ELSE '{}'::jsonb END) kv
    WHERE  mt.sport = 'XC' AND kv.key ~ '^[0-9]+$'
      AND  jsonb_typeof(kv.value) = 'object'
      AND  (kv.value ->> 'distance') ~ '^[0-9]+(\.[0-9]+)?$')
SELECT (row_number() OVER ())::int AS dk, u.source, u.jm, u.div_id, u.meet_id, u.listed,
       u.meet_name, u.div_name, u.course,
       COALESCE('c' || cc.cid::text, 'n:' || lower(btrim(u.course))) AS venue
FROM  (SELECT * FROM a UNION ALL SELECT * FROM t) u
LEFT  JOIN cc ON cc.course_name = u.course
             AND cc.la = round(u.la::numeric, 5) AND cc.lo = round(u.lo::numeric, 5)
WHERE u.listed BETWEEN 800 AND 16000
"""

_ROW_SQL = r"""
CREATE TEMP TABLE wd_row AS
SELECT d.dk, r.result_id, r.person_id,
       left(r.date::text, 4)::smallint AS yr,
       left(r.date::text, 10) AS day,
       (ln(r.time_seconds) - %(k)s * ln(d.listed / 5000.0))::real AS e,
       r.rating_pool AS pool
FROM   results r
JOIN   wd_div d ON d.source = r.source AND d.div_id = r.div_id
               AND d.jm = CASE WHEN r.source = 'tfrrs' THEN r.meet_id ELSE 0 END
WHERE  r.time_seconds BETWEEN 60 AND 14400
  AND  r.date::text ~ '^[0-9]{4}-'
"""

# a runner's season reference: the median 5K-equivalent of their races
# that season -- in pass 2 without the divisions pass 1 found off
_REF_SQL = """
CREATE TEMP TABLE {name} AS
SELECT person_id, yr, percentile_cont(0.5) WITHIN GROUP (ORDER BY e)::real AS m,
       count(*)::int AS n
FROM   wd_row w
WHERE  person_id IS NOT NULL {excl}
GROUP  BY 1, 2
HAVING count(*) >= %(n)s
"""

_SUS_SQL = """
CREATE TEMP TABLE wd_sus AS
SELECT w.dk
FROM   wd_row w JOIN wd_ref1 f ON f.person_id = w.person_id AND f.yr = w.yr
GROUP  BY w.dk
HAVING count(*) >= 3
   AND abs(percentile_cont(0.5) WITHIN GROUP (ORDER BY w.e - f.m)) >= %(s)s
"""

_STAT_SQL = """
CREATE TEMP TABLE wd_stat AS
SELECT w.dk, count(*)::int AS n_rows, count(f.m)::int AS n_ref,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY w.e - f.m) AS res,
       percentile_cont(ARRAY[0.1, 0.25, 0.75, 0.9]) WITHIN GROUP (ORDER BY w.e - f.m) AS q,
       avg(CASE WHEN f.m IS NULL THEN NULL WHEN w.e - f.m < -0.03 THEN 1.0 ELSE 0.0 END) AS sh_fast,
       avg(CASE WHEN f.m IS NULL THEN NULL WHEN w.e - f.m >  0.03 THEN 1.0 ELSE 0.0 END) AS sh_slow,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY w.e) AS e50,
       min(w.day) AS day,
       mode() WITHIN GROUP (ORDER BY w.pool) AS pool
FROM   wd_row w
LEFT   JOIN wd_ref2 f ON f.person_id = w.person_id AND f.yr = w.yr
GROUP  BY w.dk
"""

_PCTS = (0.001, 0.005, 0.01, 0.03, 0.1, 0.25, 0.5, 0.75, 0.9, 0.97, 0.99, 0.995, 0.999)
_POOL_SQL = """
SELECT pool, percentile_cont(%(p)s::float8[]) WITHIN GROUP (ORDER BY e50)
FROM   wd_stat WHERE n_rows >= 5 AND pool IS NOT NULL
GROUP  BY pool HAVING count(*) >= 50
"""

# each meet at each venue and label: its divisions' median res, by pool
_VH_SQL = """
CREATE TEMP TABLE wd_vh AS
SELECT d.venue, (round(d.listed / 10) * 10)::int AS lab, d.source, d.meet_id, s.pool,
       min(s.day) AS day, count(*)::int AS n_div,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.res)
           FILTER (WHERE s.n_ref >= %(f)s) AS res,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.e50) AS e50
FROM   wd_div d JOIN wd_stat s USING (dk)
WHERE  d.venue IS NOT NULL
GROUP  BY 1, 2, 3, 4, 5
"""

_HAS_OVR_SQL = "SELECT to_regclass('dist_override') IS NOT NULL"

# the divisions worth judging: off by CAND (raw or against the venue's
# usual), spread like two races, an extreme pace, or already overridden
_CAND_SQL = """
CREATE TEMP TABLE wd_cand AS
WITH vb AS (
    SELECT venue, lab, percentile_cont(0.5) WITHIN GROUP (ORDER BY res) AS b, count(*) AS n
    FROM   (SELECT venue, lab, source, meet_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY res) AS res
            FROM wd_vh WHERE res IS NOT NULL GROUP BY 1, 2, 3, 4) m
    GROUP  BY 1, 2 HAVING count(*) >= 3)
SELECT s.dk
FROM   wd_stat s JOIN wd_div d USING (dk)
LEFT   JOIN vb ON vb.venue = d.venue AND vb.lab = (round(d.listed / 10) * 10)::int
LEFT   JOIN wd_pool p ON p.pool = s.pool
WHERE  (s.n_ref >= %(f)s AND (abs(s.res) >= %(c)s OR abs(s.res - COALESCE(vb.b, 0)) >= %(c)s))
   OR  (s.n_ref >= %(mix)s AND (s.q[3] - s.q[2] >= 0.10 OR s.res - s.q[1] >= %(sep)s
                               OR s.q[4] - s.res >= %(sep)s))
   OR  (s.n_rows >= 5 AND (s.e50 <= p.lo OR s.e50 >= p.hi))
   {ovr}
"""

# the last run's ratings for the candidates: each runner's rating here
# against their own mean in the same pool (data_checks' _base), so a
# venue that reads off every day can be told absorbed from not
_RATING_SQL = ("""
CREATE TEMP TABLE wd_rp AS
SELECT DISTINCT w.person_id FROM wd_row w JOIN wd_cand c ON c.dk = w.dk
WHERE  w.person_id IS NOT NULL
""", "ALTER TABLE wd_rp ADD PRIMARY KEY (person_id)", """
CREATE TEMP TABLE wd_rb AS
SELECT r.person_id, r.rating_pool AS pool, avg(ln(r.speed_rating)) AS lb
FROM   results r JOIN wd_rp p ON p.person_id = r.person_id
WHERE  r.speed_rating > 0 AND r.rating_pool IS NOT NULL
GROUP  BY 1, 2 HAVING count(*) >= 5
""", """
CREATE TEMP TABLE wd_rg AS
SELECT w.dk, percentile_cont(0.5) WITHIN GROUP (ORDER BY ln(r.speed_rating) - b.lb) AS g,
       count(*)::int AS n
FROM   wd_row w
JOIN   wd_cand c ON c.dk = w.dk
JOIN   results r ON r.result_id = w.result_id
JOIN   wd_rb b ON b.person_id = r.person_id AND b.pool = r.rating_pool
WHERE  r.speed_rating > 0
GROUP  BY 1
""")

_LOAD_SQL = """
SELECT d.dk, d.source, d.meet_id, d.div_id, d.listed, d.meet_name, d.div_name, d.course,
       d.venue, s.n_rows, s.n_ref, s.res, s.q, s.sh_fast, s.sh_slow, s.e50, s.day, s.pool,
       g.g, g.n
FROM   wd_div d JOIN wd_stat s USING (dk)
LEFT   JOIN wd_rg g USING (dk)
WHERE  {where}
"""


def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _ex(cur, sql, args=None, label=None, t0=None):
    t = time.time()
    cur.execute(sql, args)
    if label:
        n = cur.rowcount
        print(f"[wrong_distance]   {label}: {n:,} rows ({time.time() - t:.0f}s, "
              f"{time.time() - (t0 or t):.0f}s in)", flush=True)


def build(cur, work_mem="512MB"):
    """The temp tables. One read of results, everything after on wd_row."""
    t0 = time.time()
    cur.execute("SET LOCAL work_mem = %s", (work_mem,))
    cur.execute("SET LOCAL statement_timeout = 0")
    _ex(cur, _DIV_SQL, None, "divisions (meets + tfrrs blob)", t0)
    cur.execute("CREATE INDEX ON wd_div (source, div_id, jm)")
    cur.execute("ANALYZE wd_div")
    # ★ ONE HASH JOIN, NOT 34M INDEX PROBES (owner's server, 2026-10-26: this
    #   statement ran 1 h 41 m and was killed). wd_div fits in work_mem; a
    #   nested loop through its index, one probe per result row, is what a
    #   statement this slow looks like. Temp tables get no parallel workers,
    #   so the plan is all there is to choose.
    cur.execute("SET LOCAL enable_nestloop = off")
    cur.execute("SET LOCAL enable_mergejoin = off")
    _ex(cur, _ROW_SQL, {"k": K}, "result rows at their listed distance", t0)
    cur.execute("SET LOCAL enable_nestloop = on")
    cur.execute("SET LOCAL enable_mergejoin = on")
    cur.execute("ANALYZE wd_row")
    _ex(cur, _REF_SQL.format(name="wd_ref1", excl=""), {"n": REF_MIN + 1},
        "runner-seasons, pass 1", t0)
    cur.execute("ANALYZE wd_ref1")
    _ex(cur, _SUS_SQL, {"s": SUSPECT}, "divisions off 3%+ (left out of references)", t0)
    cur.execute("ALTER TABLE wd_sus ADD PRIMARY KEY (dk)")
    _ex(cur, _REF_SQL.format(
        name="wd_ref2", excl="AND NOT EXISTS (SELECT 1 FROM wd_sus s WHERE s.dk = w.dk)"),
        {"n": REF_MIN}, "runner-seasons, pass 2", t0)
    cur.execute("ANALYZE wd_ref2")
    _ex(cur, _STAT_SQL, None, "division statistics", t0)
    cur.execute("ALTER TABLE wd_stat ADD PRIMARY KEY (dk)")
    cur.execute("ANALYZE wd_stat")
    cur.execute(_POOL_SQL, {"p": list(_PCTS)})
    pools = {p: list(zip(_PCTS, vals)) for p, vals in cur.fetchall()}
    cur.execute("CREATE TEMP TABLE wd_pool (pool text PRIMARY KEY, lo float8, hi float8)")
    for p, pc in pools.items():
        dct = dict(pc)
        cur.execute("INSERT INTO wd_pool VALUES (%s, %s, %s)", (p, dct[0.005], dct[0.995]))
    _ex(cur, _VH_SQL, {"f": FIELD_MIN}, "venue history (meet x label x pool)", t0)
    cur.execute("CREATE INDEX ON wd_vh (venue, lab)")
    cur.execute("ANALYZE wd_vh")
    cur.execute(_HAS_OVR_SQL)
    has_ovr = cur.fetchone()[0]
    ovr = ("OR (d.meet_id, d.div_id) IN (SELECT meet_id, div_id FROM dist_override)"
           if has_ovr else "")
    # ★ AND EVERY DIVISION THE WRITER HAS A ROW FOR, so each is re-judged
    #   every run (a pin comes off when its evidence goes), and every
    #   override the writer vetoed (dist_override_vetoed), so a veto is
    #   judged against the override it removed
    if _hasTable(cur, "distance_pin"):
        ovr += (" OR (d.source, d.meet_id, d.div_id) IN (SELECT source, meet_id, div_id "
                "FROM distance_pin WHERE status IN ('active', 'held'))")
    if _hasTable(cur, "dist_override_vetoed"):
        ovr += (" OR (d.meet_id, d.div_id) IN (SELECT meet_id, div_id "
                "FROM dist_override_vetoed)")
    _ex(cur, _CAND_SQL.format(ovr=ovr),
        {"f": FIELD_MIN, "c": CAND, "mix": MIX_MIN, "sep": MIX_SEP}, "divisions to judge", t0)
    cur.execute("ALTER TABLE wd_cand ADD PRIMARY KEY (dk)")
    for i, q in enumerate(_RATING_SQL):
        _ex(cur, q, None, "the candidates' last ratings" if i == len(_RATING_SQL) - 1 else None, t0)
    return pools, has_ovr


def _divDict(row):
    keys = ("dk", "source", "meet_id", "div_id", "listed", "meet_name", "div_name",
            "course", "venue", "n_rows", "n_ref", "res", "q", "sh_fast", "sh_slow",
            "e50", "day", "pool", "rating_gap", "rating_n")
    return dict(zip(keys, row))


def _level(pool, grade):
    if pool:
        return pool.split("_")[0]
    g = str(grade or "").strip()
    if g.isdigit():
        gi = int(g)
        return "ms" if gi <= 8 else "hs" if gi <= 12 else None
    return None


def gather(cur, pools, has_ovr):
    """Load the candidates and the context each needs; judge them all."""
    cur.execute(_LOAD_SQL.format(where="dk IN (SELECT dk FROM wd_cand)"))
    cand = [_divDict(r) for r in cur.fetchall()]
    print(f"[wrong_distance] judging {len(cand):,} divisions", flush=True)
    # siblings: every division of the candidates' meets
    cur.execute(_LOAD_SQL.format(where="""(d.source, d.meet_id) IN
                    (SELECT DISTINCT d2.source, d2.meet_id FROM wd_div d2
                     JOIN wd_cand c ON c.dk = d2.dk)"""))
    by_meet = {}
    for r in cur.fetchall():
        dd = _divDict(r)
        by_meet.setdefault((dd["source"], dd["meet_id"]), []).append(dd)
    # venue history at the candidates' venues, indexed once
    cur.execute("""SELECT venue, lab, source, meet_id, pool, day, res, e50
                   FROM wd_vh WHERE venue IN (SELECT DISTINCT d.venue FROM wd_div d
                                              JOIN wd_cand c ON c.dk = d.dk)""")
    vh = venueIndex(cur.fetchall())
    ovr = {}
    if has_ovr:
        # the writer's own pins are not overrides to judge; the overrides
        # it vetoed are (from dist_override_vetoed), against the evidence
        vetoed = ("UNION ALL SELECT meet_id, div_id, distance, dict_name "
                  "FROM dist_override_vetoed" if _hasTable(cur, "dist_override_vetoed") else "")
        cur.execute(f"""SELECT o.meet_id, o.div_id, o.distance, o.dict_name
                        FROM  (SELECT meet_id, div_id, distance, dict_name FROM dist_override
                               WHERE dict_name NOT IN ('distance_pin', 'novote') {vetoed}) o
                        JOIN wd_div d ON d.meet_id = o.meet_id AND d.div_id = o.div_id
                        JOIN wd_cand c ON c.dk = d.dk""")
        ovr = {(int(m), int(dv)): (float(dist), name) for m, dv, dist, name in cur.fetchall()}
    # per-runner residuals for the ones spread like two races
    mixc = [c for c in cand if (c["n_ref"] or 0) >= MIX_MIN and c["q"] and c["res"] is not None
            and (c["q"][2] - c["q"][1] >= 0.10 or c["res"] - c["q"][0] >= MIX_SEP
                 or c["q"][3] - c["res"] >= MIX_SEP)]
    rows_by = {}
    if mixc:
        cur.execute("""
            SELECT w.dk, w.e - f.m, COALESCE(right(w.pool, 1), lower(left(a.gender, 1))),
                   w.pool, r.grade
            FROM   wd_row w
            JOIN   wd_ref2 f ON f.person_id = w.person_id AND f.yr = w.yr
            JOIN   results r ON r.result_id = w.result_id
            LEFT   JOIN athletes a ON r.source = 'anet' AND a.athlete_id = r.athlete_id
            WHERE  w.dk = ANY(%s)""", ([c["dk"] for c in mixc],))
        for dk, res, g, pool, grade in cur.fetchall():
            rows_by.setdefault(dk, []).append((float(res), g, _level(pool, grade)))
    out = []
    for c in cand:
        out.append(judgeOne(c, by_meet, vh, pools, ovr, rows_by))
    return out


def venueIndex(rows):
    """{venue: {"res": {lab: {meet: [res]}}, "meets": {lab: {meet}},
    "raw": {(lab, pool): [(meet, year, e50)]}}} from wd_vh rows
    (venue, lab, source, meet, pool, day, res, e50). Pure."""
    out = {}
    for venue, lab, source, meet, pool, day, res, e50 in rows:
        v = out.setdefault(venue, {"res": {}, "meets": {}, "raw": {}})
        key = (source, meet)
        v["meets"].setdefault(lab, set()).add(key)
        if res is not None:
            v["res"].setdefault(lab, {}).setdefault(key, []).append(float(res))
        if e50 is not None and day:
            v["raw"].setdefault((lab, pool), []).append((key, str(day)[:4], float(e50)))
    return out


def venueContext(vi, lab, meet, pool, year):
    """(venue_meets, venue_labels, raw_hist) for one division, its own meet
    left out. vi: one venue's entry of venueIndex. Pure."""
    if not vi:
        return [], [], []
    venue_meets = [statistics.median(v) for k, v in vi["res"].get(lab, {}).items() if k != meet]
    venue_labels = [float(l) for l, ms in vi["meets"].items()
                    if l != lab and len(ms - {meet}) >= 2]
    raw_hist = [e for k, y, e in vi["raw"].get((lab, pool), ()) if k != meet and y != year]
    return venue_meets, venue_labels, raw_hist


def judgeOne(c, by_meet, vh, pools, ovr, rows_by):
    """Assemble one candidate's context and judge it."""
    listed = float(c["listed"])
    lab = int(round(listed / 10) * 10)
    meet = (c["source"], c["meet_id"])
    sibs_all = [s for s in by_meet.get(meet, []) if s["dk"] != c["dk"]]
    sibs = [s["res"] for s in sibs_all if (s["n_ref"] or 0) >= FIELD_MIN and s["res"] is not None]
    sib_labels = [float(s["listed"]) for s in sibs_all
                  if abs(float(s["listed"]) - listed) >= 1]
    venue_meets, venue_labels, raw_hist = venueContext(
        vh.get(c["venue"]), lab, meet, c["pool"], (c["day"] or "")[:4])
    d = dict(c)
    d["pace_pct"] = pacePct(c["e50"], pools.get(c["pool"]))
    base = baseline(venue_meets, sibs)[0]
    mix = None
    if c["dk"] in rows_by:
        mix = mixVerdict(rows_by[c["dk"]], listed, base, candidates(venue_labels))
    v = judge(d, {"venue_meets": venue_meets, "sibs": sibs, "sib_labels": sib_labels,
                  "venue_labels": venue_labels, "raw_hist": raw_hist, "e50": c["e50"],
                  "mix": mix})
    v["mix"] = mix
    o = ovr.get((int(c["meet_id"]), int(c["div_id"])))
    v["override"] = o
    if o:
        imp = v["implied"]
        if imp is None and c["res"] is not None and (c["n_ref"] or 0) >= FIELD_MIN:
            imp = implied(listed, c["res"] - base)
        act, why = overrideVerdict(listed, o[0], imp, c["n_ref"], hand=o[1] == HAND_DICT)
        if act == "replace":
            # with what: this system's own pin, else the listed distance if
            # the times say it is right, else nothing better is known
            if v["action"] == "pin":
                why += f"; -> {v['proposed']:g}"
            elif imp and abs(math.log(imp / listed)) <= SNAP_TOL:
                why += f"; -> the listed {listed:g}"
            else:
                act, why = "review", why + "; no real distance fits the times either"
        v["override_verdict"] = (act, why)
        if v["kind"] is None or v["action"] == "ignore":
            v.update(kind="override", action=act,
                     evidence=f"override {o[0]:g} ({o[1]}): {why}")
    v["div"] = c
    return v



# ------------------------------------------------------------------ #
#  the writer: distance_pin, one table for every distance fix
# ------------------------------------------------------------------ #
#
# Design: docs/WRONG-DISTANCE-2026-10-25.md sections 5 and 6. Rows are keyed
# (source, meet_id, div_id, part): part '' is the whole division, 'g:f' /
# 'lvl:ms' one side of a combined division. Actions:
#   pin     the division (or part) ran `distance`, never longer than its
#           times say; the backfill normalises on it
#   novote  keep the listed distance, vote on no course (dist_override.novote)
#   veto    an existing override the times reject; the listed distance stands
# Nothing is deleted: a row the evidence no longer holds is RETIRED with its
# reason, and every change goes to distance_pin_log. held / refused are the
# owner's word and never changed here.

_PIN_DDL = """
CREATE TABLE IF NOT EXISTS distance_pin (
    source      text   NOT NULL,
    meet_id     bigint NOT NULL,
    div_id      bigint NOT NULL,
    part        text   NOT NULL DEFAULT '',
    listed      real,
    distance    real,
    action      text   NOT NULL,
    status      text   NOT NULL DEFAULT 'active',
    kind        text,
    confidence  text,
    points      int,
    evidence    text,
    set_by      text   NOT NULL DEFAULT 'auto',
    first_seen  timestamptz DEFAULT now(),
    last_judged timestamptz DEFAULT now(),
    retired_at  timestamptz,
    retired_why text,
    PRIMARY KEY (source, meet_id, div_id, part));
CREATE TABLE IF NOT EXISTS distance_pin_log (
    at       timestamptz DEFAULT now(),
    source   text, meet_id bigint, div_id bigint, part text,
    old      text, new text, why text);
"""


def wants(v):
    """[(part, action, distance)] the verdict on one division asks for. Pure."""
    c = v["div"]
    listed = float(c["listed"])
    out = []
    if v["action"] == "pin" and v.get("proposed"):
        out.append(("", "pin", float(v["proposed"])))
    elif v["action"] == "split":
        # ★ THE FAST SIDE ONLY, ONLY DOWN, ONLY WHEN AN ATTRIBUTE EXPLAINS IT
        #   (design section 4). The side that ran the listed distance keeps
        #   it, and the division votes on no course either way.
        mix = v.get("mix") or {}
        by = mix.get("by")
        fast = next((cl for cl in mix.get("clusters", ()) if cl["name"] == "fast"), None)
        s = fast and fast.get("snap")
        if (by and s and s < listed and math.log(listed / s) >= BIG_MOVE
                and math.log(s / fast["implied"]) <= OVERSHOOT):
            attr = "g" if by[0] == "gender" else "lvl"
            for val in by[1]:
                out.append((f"{attr}:{val}", "pin", float(s)))
        out.append(("", "novote", None))
    elif v["action"] == "novote":
        out.append(("", "novote", None))
    ov = v.get("override_verdict")
    if (ov and ov[0] == "replace" and v["action"] != "pin" and "-> the listed" in ov[1]
            and not any(p == "" for p, _a, _d in out)):
        out.append(("", "veto", None))
    return out


def keeps(row, v):
    """(True, why) when an active auto row the verdict no longer asks for
    should stay: STAY, not CAND, is the bar (hysteresis). Pure.
    row: dict with part, action, distance, listed."""
    if v is None:
        return False, "the division is no longer in the corpus"
    x = v.get("x")
    listed = float(row.get("listed") or v["div"]["listed"])
    if row["part"]:
        return (v.get("kind") == "combined",
                "still two races in one division" if v.get("kind") == "combined"
                else "no longer two races in one division")
    if row["action"] == "pin":
        if x is None or x > -STAY:
            return False, f"the field now reads {100 * (x or 0):+.1f}% at the listed distance"
        imp = implied(listed, x)
        if math.log(float(row["distance"]) / imp) > OVERSHOOT:
            return False, f"the pin {row['distance']:g} is now longer than the {imp:,.0f} the times say"
        return True, f"the field still reads {100 * x:+.1f}%"
    if row["action"] == "novote":
        if v.get("kind") == "combined" or (x is not None and x >= STAY):
            return True, "still slow, or still two races"
        return False, f"the field now reads {100 * (x or 0):+.1f}%"
    if row["action"] == "veto":
        ov = v.get("override_verdict")
        if v.get("override") and ov and ov[0] in ("replace", "review"):
            return True, "the override is still rejected"
        return False, "no override left to veto, or the times now agree with it"
    return False, "unknown action"


def plan(verdicts, rows):
    """The changes one run makes. verdicts: judged divisions; rows:
    {(source, meet, div, part): row dict} already in distance_pin. Returns
    (upserts, retires, refreshed): upserts [(key, action, distance, v)] new
    or changed; retires [(key, why)]; refreshed [(key, v, why)] kept as they
    are. Pure."""
    by_div = {(v["div"]["source"], int(v["div"]["meet_id"]), int(v["div"]["div_id"])): v
              for v in verdicts}
    upserts, retires, refreshed, wanted = [], [], [], set()
    for dkey, v in by_div.items():
        for part, action, dist in wants(v):
            key = dkey + (part,)
            wanted.add(key)
            old = rows.get(key)
            if old and old["status"] in ("held", "refused"):
                refreshed.append((key, v, "owner's " + old["status"]))
                continue
            if (old and old["status"] == "active" and old["action"] == action
                    and (dist is None or abs(float(old["distance"] or 0) - dist) < 1)):
                refreshed.append((key, v, "confirmed"))
                continue
            upserts.append((key, action, dist, v))
    for key, row in rows.items():
        if key in wanted or row["status"] != "active" or row.get("set_by") != "auto":
            if row["status"] == "held" and key not in wanted:
                refreshed.append((key, by_div.get(key[:3]), "owner's held"))
            continue
        ok, why = keeps(row, by_div.get(key[:3]))
        if ok:
            refreshed.append((key, by_div.get(key[:3]), why))
        else:
            retires.append((key, why))
    return upserts, retires, refreshed


def _loadRows(cur):
    cur.execute("""SELECT source, meet_id, div_id, part, listed, distance, action, status, set_by
                   FROM distance_pin""")
    out = {}
    for src, m, d, part, listed, dist, act, st, by in cur.fetchall():
        out[(src, int(m), int(d), part)] = {"part": part, "listed": listed, "distance": dist,
                                            "action": act, "status": st, "set_by": by}
    return out


def write(cur, verdicts, allow_many=False):
    """Apply plan() to distance_pin. Returns the counts, or None when the
    circuit breaker refused."""
    cur.execute(_PIN_DDL)
    rows = _loadRows(cur)
    upserts, retires, refreshed = plan(verdicts, rows)
    adds = sum(1 for k, *_ in upserts if k not in rows or rows[k]["status"] != "active")
    print(f"[wrong_distance] writer: {len(upserts):,} to write ({adds:,} new), "
          f"{len(retires):,} to retire, {len(refreshed):,} kept", flush=True)
    if not allow_many and (adds > MAX_CHANGES or len(retires) > MAX_CHANGES):
        print(f"[wrong_distance] !! CIRCUIT BREAKER: more than {MAX_CHANGES} adds or retires "
              f"in one run -- NOTHING WRITTEN. Read the list; --allow-many writes it.", flush=True)
        return None
    from psycopg2.extras import execute_values
    log, up_rows = [], []
    for key, action, dist, v in upserts:
        c = v["div"]
        old = rows.get(key)
        up_rows.append(key + (float(c["listed"]), dist, action, v.get("kind"),
                              v.get("confidence") or None, v.get("points") or 0,
                              (v.get("evidence") or "")[:2000]))
        log.append(key + (f"{old['action']} {old['distance']} {old['status']}" if old else None,
                          f"{action} {dist}", (v.get("evidence") or "")[:500]))
    if up_rows:
        execute_values(cur, """
            INSERT INTO distance_pin (source, meet_id, div_id, part, listed, distance, action,
                                      kind, confidence, points, evidence)
            VALUES %s
            ON CONFLICT (source, meet_id, div_id, part) DO UPDATE SET
                listed = EXCLUDED.listed, distance = EXCLUDED.distance,
                action = EXCLUDED.action, kind = EXCLUDED.kind,
                confidence = EXCLUDED.confidence, points = EXCLUDED.points,
                evidence = EXCLUDED.evidence, status = 'active', set_by = 'auto',
                last_judged = now(), retired_at = NULL, retired_why = NULL""", up_rows)
    for key, why in retires:
        cur.execute("""UPDATE distance_pin SET status = 'retired', retired_at = now(),
                              retired_why = %s, last_judged = now()
                       WHERE source = %s AND meet_id = %s AND div_id = %s AND part = %s""",
                    (why,) + key)
        old = rows[key]
        log.append(key + (f"{old['action']} {old['distance']} active", "retired", why))
    for key, v, _why in refreshed:
        if v is not None:
            cur.execute("""UPDATE distance_pin SET last_judged = now(), evidence = %s
                           WHERE source = %s AND meet_id = %s AND div_id = %s AND part = %s""",
                        ((v.get("evidence") or "")[:2000],) + key)
    if log:
        execute_values(cur, """INSERT INTO distance_pin_log
                               (source, meet_id, div_id, part, old, new, why) VALUES %s""", log)
    cur.execute("""SELECT action, count(*) FROM distance_pin WHERE status IN ('active', 'held')
                   GROUP BY 1 ORDER BY 1""")
    now = dict(cur.fetchall())
    print(f"[wrong_distance] distance_pin now: {now}; {len(upserts):,} written, "
          f"{len(retires):,} retired", flush=True)
    return {"written": len(upserts), "retired": len(retires), "now": now}


# dump_overrides' reading of distance_pin, here so it can be tested
def applyPins(rows, pins):
    """rows {key: (distance, dict_name)}; pins [(meet, div, action, distance,
    listed)]. Returns (rows, novote_keys, vetoed [(meet, div, distance,
    dict_name)]). Pure."""
    rows, novote, vetoed = dict(rows), set(), []
    for m, d, action, dist, listed in pins:
        key = (int(m), int(d))
        if action == "pin" and dist:
            rows[key] = (float(dist), "distance_pin")
        elif action == "veto" and key in rows:
            vetoed.append(key + rows.pop(key))
        elif action == "novote":
            if key not in rows and listed:
                rows[key] = (float(listed), "novote")
            if key in rows:
                novote.add(key)
    return rows, novote, vetoed


def setStatus(cur, ref, status):
    """--hold / --refuse / --release SOURCE/MEET/DIV[/PART]."""
    parts = ref.strip("/").split("/")
    src, meet, div = parts[0], int(parts[1]), int(parts[2])
    part = parts[3] if len(parts) > 3 else ""
    cur.execute(_PIN_DDL)
    new = "active" if status == "release" else status
    by = "auto" if status == "release" else "owner"
    # a division with no row yet gets action 'none': holding or refusing it
    # says "leave it alone", not "novote" (readers apply pin/veto/novote only)
    cur.execute("""INSERT INTO distance_pin (source, meet_id, div_id, part, action, status, set_by)
                   VALUES (%s, %s, %s, %s, 'none', %s, %s)
                   ON CONFLICT (source, meet_id, div_id, part) DO UPDATE
                   SET status = EXCLUDED.status, set_by = EXCLUDED.set_by, last_judged = now()""",
                (src, meet, div, part, new, by))
    cur.execute("""INSERT INTO distance_pin_log (source, meet_id, div_id, part, old, new, why)
                   VALUES (%s, %s, %s, %s, NULL, %s, 'owner')""", (src, meet, div, part, new))
    print(f"[wrong_distance] {src}/{meet}/{div}{'/' + part if part else ''}: {new} ({by})")


# ------------------------------------------------------------------ #
#  the report
# ------------------------------------------------------------------ #

_CONF_RANK = {"high": 0, "medium": 1, "low": 2, "": 3}


def rankKey(v):
    c = v["div"]
    move = (abs(math.log(float(c["listed"]) / v["proposed"])) if v.get("proposed") else
            abs(v["x"] or 0))
    return (ACTION_ORDER.index(v["action"]) if v["action"] in ACTION_ORDER else 99,
            _CONF_RANK.get(v["confidence"], 3), -v["points"],
            -(c["n_rows"] or 0) * move)


def counts(verdicts):
    """{(kind, action, confidence): n}"""
    out = {}
    for v in verdicts:
        if v["kind"] is None:
            continue
        k = (v["kind"], v["action"], v["confidence"] or "-")
        out[k] = out.get(k, 0) + 1
    return out


def _line(v):
    c = v["div"]
    prop = f"{v['proposed']:g}" if v.get("proposed") else "-"
    return (f"{v['action']:<8} {v['confidence'] or '-':<6} {v['kind']:<15} "
            f"{c['source']} {c['meet_id']}/{c['div_id']}  {(c['day'] or '')[:10]}  "
            f"{(c['meet_name'] or '')[:40]} | {(c['div_name'] or '')[:24]}  "
            f"{float(c['listed']):g} -> {prop}  [{v['evidence']}]")


_CSV_COLS = ("rank", "action", "confidence", "points", "kind", "direction", "source",
             "meet_id", "div_id", "date", "meet_name", "div_name", "course", "pool",
             "n_rows", "n_ref", "listed", "implied", "proposed", "move_pct", "field_pct",
             "course_base_pct", "override", "override_dict", "override_verdict",
             "evidence", "page")


def writeCsv(path, verdicts):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_CSV_COLS)
        for i, v in enumerate(verdicts, 1):
            c = v["div"]
            o = v.get("override")
            ov = v.get("override_verdict")
            listed = float(c["listed"])
            w.writerow([
                i, v["action"], v["confidence"], v["points"], v["kind"], v["direction"],
                c["source"], c["meet_id"], c["div_id"], (c["day"] or "")[:10],
                c["meet_name"], c["div_name"], c["course"], c["pool"], c["n_rows"],
                c["n_ref"], f"{listed:g}",
                f"{v['implied']:.0f}" if v.get("implied") else "",
                f"{v['proposed']:g}" if v.get("proposed") else "",
                f"{100 * (v['proposed'] / listed - 1):+.1f}" if v.get("proposed") else "",
                f"{100 * v['x']:+.1f}" if v.get("x") is not None else "",
                f"{100 * v['base']:+.1f}" if v.get("base") else "",
                f"{o[0]:g}" if o else "", o[1] if o else "",
                f"{ov[0]}: {ov[1]}" if ov else "",
                v["evidence"], f"https://racecast.co/race/xc/{c['meet_id']}/{c['div_id']}"])


def report(verdicts, top=60, csv_path=None):
    listed = sorted((v for v in verdicts if v["kind"] is not None), key=rankKey)
    cnt = counts(listed)
    print("\n== COUNTS (kind, action, confidence) ==")
    for (kind, act, conf), n in sorted(cnt.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        print(f"    {kind:<16} {act:<9} {conf:<7} {n:>8,}")
    by_act = {}
    for v in listed:
        by_act[v["action"]] = by_act.get(v["action"], 0) + 1
    print("    by action: " + ", ".join(f"{a} {by_act[a]:,}" for a in ACTION_ORDER if a in by_act))
    ov = [v for v in verdicts if v.get("override_verdict")]
    if ov:
        tally = {}
        for v in ov:
            nm = v["override"][1]
            tally.setdefault(nm, {}).setdefault(v["override_verdict"][0], 0)
            tally[nm][v["override_verdict"][0]] += 1
        print("\n== EXISTING OVERRIDES the field could judge, by the dict they came from ==")
        for nm, t in sorted(tally.items(), key=lambda kv: -sum(kv[1].values())):
            print(f"    {nm:<32} " + ", ".join(f"{a} {n:,}" for a, n in sorted(t.items())))
    shown = [v for v in listed if v["action"] not in ("ignore", "keep")][:top]
    print(f"\n== TOP {len(shown)} (action, confidence, kind, division, listed -> proposed, evidence) ==")
    for v in shown:
        print("  " + _line(v))
    if csv_path:
        writeCsv(csv_path, listed)
        print(f"\n[wrong_distance] {len(listed):,} rows -> {csv_path}")


# ------------------------------------------------------------------ #
#  --explain: one division, runner by runner, straight from results
# ------------------------------------------------------------------ #

_EXPLAIN_SQL = """
WITH here AS (
    SELECT r.result_id, r.person_id, r.time_seconds, left(r.date::text, 4) AS yr,
           COALESCE(a.first_name || ' ' || a.last_name, r.athlete_name, '') AS name,
           r.grade, r.rating_pool
    FROM   results r
    LEFT   JOIN athletes a ON r.source = 'anet' AND a.athlete_id = r.athlete_id
    WHERE  r.meet_id = %(m)s AND r.div_id = %(d)s AND r.source = %(s)s
      AND  r.time_seconds > 0)
SELECT h.name, h.grade, h.rating_pool, h.time_seconds, o.time_seconds,
       COALESCE(m.distance, (mt.division_distances -> o.div_id::text ->> 'distance')::float),
       COALESCE(m.meet_name, mt.meet_name), left(o.date::text, 10)
FROM   here h
LEFT   JOIN results o ON o.person_id = h.person_id AND o.result_id <> h.result_id
                     AND left(o.date::text, 4) = h.yr AND o.time_seconds > 0
LEFT   JOIN meets m ON o.source <> 'tfrrs' AND m.div_id = o.div_id AND m.source = o.source
LEFT   JOIN meets_tfrrs mt ON o.source = 'tfrrs' AND mt.meet_id = o.meet_id AND mt.sport = 'XC'
ORDER  BY h.time_seconds, 8
"""


def explain(cur, meet, div, source=None):
    if source is None:
        cur.execute("SELECT source FROM results WHERE meet_id = %s AND div_id = %s LIMIT 1",
                    (meet, div))
        r = cur.fetchone()
        source = r[0] if r else "anet"
    if source == "tfrrs":
        cur.execute("""SELECT meet_name, venue_name,
                              (division_distances -> %s::text ->> 'distance')::float,
                              division_distances -> %s::text ->> 'div_name'
                       FROM meets_tfrrs WHERE meet_id = %s AND sport = 'XC'""", (div, div, meet))
    else:
        cur.execute("""SELECT meet_name, course_name, distance::float, division FROM meets
                       WHERE div_id = %s AND source = %s""", (div, source))
    head = cur.fetchone()
    if not head:
        print(f"no division {source} {meet}/{div}")
        return
    name, course, listed, divname = head
    print(f"{source} {meet}/{div}: {name} | {divname} at {course}, listed {listed:g} m")
    cur.execute(_EXPLAIN_SQL, {"m": meet, "d": div, "s": source})
    runners = {}
    for nm, gr, pool, t, ot, od, omeet, oday in cur.fetchall():
        key = (nm, gr, pool, t)
        runners.setdefault(key, [])
        if ot and od and od > 0:
            runners[key].append((ot, float(od), omeet, oday))
    ress = []
    print(f"\n  {'runner':<26} {'gr':>3} {'pool':<9} {'time':>7}  "
          f"{'own 5K-eq':>9} {'here':>7} {'excess':>7} {'implied':>8}  other races")
    for (nm, gr, pool, t), others in sorted(runners.items(), key=lambda kv: kv[0][3]):
        e_here = math.log(t) - K * math.log(listed / 5000.0)
        if len(others) >= REF_MIN:
            m = statistics.median(math.log(ot) - K * math.log(od / 5000.0)
                                  for ot, od, _x, _y in others)
            res = e_here - m
            ress.append(res)
            col = (f"{_mmss(math.exp(m)):>9} {_mmss(math.exp(e_here)):>7} "
                   f"{100 * res:+6.1f}% {implied(listed, res):8,.0f}")
        else:
            col = f"{'':>9} {_mmss(math.exp(e_here)):>7} {'':>7} {'':>8}"
        oth = ", ".join(f"{_mmss(ot)}@{od:g}" for ot, od, _x, _y in others[:5])
        print(f"  {nm[:26]:<26} {str(gr or ''):>3} {str(pool or ''):<9} {_mmss(t):>7}  {col}  {oth}")
    if ress:
        med = statistics.median(ress)
        fast = sum(1 for r in ress if r < -0.03) / len(ress)
        slow = sum(1 for r in ress if r > 0.03) / len(ress)
        print(f"\n  {len(ress)} runners with {REF_MIN}+ other races: median {100 * med:+.1f}% "
              f"({100 * fast:.0f}% fast, {100 * slow:.0f}% slow) -> implies "
              f"{implied(listed, med):,.0f} m before any course baseline")
        sp = split2(ress)
        if (sp and min(len(sp[0]), len(sp[1])) >= MIX_CLUSTER
                and statistics.median(sp[1]) - statistics.median(sp[0]) >= MIX_SEP):
            a, b = statistics.median(sp[0]), statistics.median(sp[1])
            print(f"  best two-group split: {len(sp[0])} at {100 * a:+.1f}% "
                  f"({implied(listed, a):,.0f} m), {len(sp[1])} at {100 * b:+.1f}% "
                  f"({implied(listed, b):,.0f} m)")
    else:
        print(f"\n  no runner has {REF_MIN}+ other races that season")


def _mmss(s):
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--csv", help="write every judged division here")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--explain", help="MEET/DIV (or a racecast race URL): one division")
    ap.add_argument("--source", help="with --explain: anet or tfrrs")
    ap.add_argument("--work-mem", default=os.environ.get("XCP_WD_WORK_MEM", "512MB"))
    ap.add_argument("--write", action="store_true", help="the writer: distance_pin")
    ap.add_argument("--allow-many", action="store_true",
                    help=f"past the circuit breaker ({MAX_CHANGES} adds or retires)")
    for st in ("hold", "refuse", "release"):
        ap.add_argument(f"--{st}", metavar="SOURCE/MEET/DIV[/PART]")
    a = ap.parse_args(argv)
    from database import getConn
    for st in ("hold", "refuse", "release"):
        ref = getattr(a, st)
        if ref:
            with getConn() as conn, conn.cursor() as cur:
                setStatus(cur, ref, {"hold": "held", "refuse": "refused"}.get(st, st))
                conn.commit()
            return
    with getConn() as conn, conn.cursor() as cur:
        try:
            if a.explain:
                import re
                nums = re.findall(r"-?\d+", a.explain.split("#")[0])
                explain(cur, int(nums[-2]), int(nums[-1]), a.source)
                return
            t0 = time.time()
            print("[wrong_distance] building (one read of results; 20-60 min)", flush=True)
            pools, has_ovr = build(cur, a.work_mem)
            verdicts = gather(cur, pools, has_ovr)
            report(verdicts, a.top, a.csv)
            if a.write:
                done = write(cur, verdicts, a.allow_many)
                conn.commit()
                print(f"[wrong_distance] done in {(time.time() - t0) / 60:.0f} min. "
                      + ("Written." if done else "Nothing written (circuit breaker)."))
                return
            print(f"[wrong_distance] done in {(time.time() - t0) / 60:.0f} min. Nothing written.")
        finally:
            conn.rollback()      # temp tables; a --write committed above


if __name__ == "__main__":
    main()
