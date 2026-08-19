"""
compare_slaney.py -- check our course difficulties against an INDEPENDENT
estimate.

THE BENCHMARK
    MalcolmSlaney/CrossCountryStats fits California XC course difficulties from
    XCStats data. Same estimator family as ours, arrived at independently:

        race_time = base * runner_ability * course_difficulty

    which in logs is our additive two-way model. He notes the model is ill-posed
    -- exactly our gauge freedom -- and pins it by normalizing to Crystal Springs
    = 1.0, where we anchor to result-weighted mean zero.

    This is the first EXTERNAL check available. reference_fit.py is our own
    second opinion on our own data; this is somebody else's data, somebody
    else's runners, somebody else's solver.

★ THE UNITS DIFFER AND THAT IS THE WHOLE DIFFICULTY OF THE COMPARISON.
    His number is a raw TIME RATIO at the course's own distance -- so
    "Woodward Park (2.0) = 0.646" and "Woodward Park (3.1) = 1.012" are the SAME
    GROUND, and the gap between them is almost entirely the 1.1 miles.

    Ours is distance-normalized first, so delta is terrain only.

    Correlating the two raw would mostly measure distance and prove nothing. So
    we convert OURS FORWARD into HIS space, putting the distance back using the
    same potential normalize_distance uses:

        ours_in_his_units(c) = (1 + delta_c) / (1 + delta_ref)
                               * exp(g(log d_c) - g(log d_ref))

    Forward rather than backward because his table has no distance-normalized
    form to convert to, and because it uses all ~443 of his courses instead of
    the ~20 that happen to share our reference distance.

★ WHAT AGREEMENT WOULD AND WOULD NOT PROVE. Both models are two-way fixed
  effects on overlapping populations, so they share blind spots: a common taper
  at championship venues, altitude, anything constant within a venue. Agreement
  says our solver and normalization are sound. It cannot vindicate a term
  neither model has.
"""

import math
import os
import re
import sys

# Run from the repo root or from engine/ -- either way the siblings import.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

_REF_NAME = "crystal springs"      # his anchor, difficulty 1.000

# ★ ALWAYS PRINTED, wherever they land. The worst-20 and best-10 tables only
#   show the extremes, so a course that agrees to within 2% -- which is most of
#   what you actually want to check -- appears in neither. Add names here.
_WATCH = ("glendoveer", "woodward", "crystal springs", "mt sac",
          "detweiller", "woodbridge", "silverlakes", "hydrangea", "steens")


# ------------------------------------------------------------------ #
# CHUNK 1 -- HIS TABLE
# ------------------------------------------------------------------ #

def parseSlaney(path):
    """
    (name, miles, boys, girls) rows from his HTML or CSV.

    His course names carry the distance in parentheses -- "Woodward Park (3.1)",
    "GGP - WCAL pre 2022 (3.0)" -- so the distance is pulled out of the name and
    the remainder becomes the match key.
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    rows = []

    # HTML table rows first; fall back to comma-separated.
    cells = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)
    if cells:
        for tr in cells:
            tds = [re.sub(r"<[^>]+>", "", c).strip()
                   for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
            rows.append(tds)
    else:
        rows = [line.split(",") for line in text.splitlines()]

    out = []
    for tds in rows:
        name, miles = None, None
        nums = []
        for c in tds:
            c = c.strip()
            m = re.match(r"^(.*?)\s*\(([\d.]+)\)$", c)
            if m and name is None:
                name, miles = m.group(1), float(m.group(2))
                continue
            # ⚠ ONLY AFTER THE NAME. His table leads with an Index column, and
            #   collecting every numeric cell made index 365 the "boys
            #   difficulty" and shifted girls into its place. The name is the
            #   anchor; difficulties follow it.
            if name is None:
                continue
            try:
                nums.append(float(c))
            except ValueError:
                pass
        if name and len(nums) >= 2:
            out.append((name, miles, nums[0], nums[1]))
    return out


_STATES = frozenset("""
al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo
mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc
""".split())


def normName(s):
    """
    Loose key for matching free-text course names.

    His names are hand-entered ("GGP - WCAL pre 2022", "Mt. Sac", "RL Stevenson
    HS"), so punctuation, case and common suffixes are stripped. Deliberately
    lossy -- a false match is visible in the scatter, a missed match is not.

    ★ TRAILING STATE CODES GO TOO. He disambiguates out-of-state courses in the
      NAME -- "Glendoveer Golf Course, OR" -- while ours is just "Glendoveer
      Golf Course". Without this the keys are "glendoveer or" and "glendoveer",
      and one of the few Oregon courses in his table silently fails to match.
      Only a TRAILING code is stripped, so "Oregon City" survives intact.
    """
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(hs|high school|golf course|park|course|the|of)\b", " ", s)
    parts = re.sub(r"\s+", " ", s).strip().split()
    while len(parts) > 1 and parts[-1] in _STATES:
        parts.pop()
    return " ".join(parts)


# ------------------------------------------------------------------ #
# CHUNK 2 -- OURS, CONVERTED FORWARD
# ------------------------------------------------------------------ #

def loadOurs(npz_path):
    """difficulty, degree, and the engine course keys."""
    with np.load(npz_path, allow_pickle=False) as f:
        return (f["difficulty"], f["degree"],
                [str(k) for k in f["course_keys"]])


def splitKey(key):
    """(canonical_id, distance_m) from 'XC:<id>:d<dist>'."""
    if not key.startswith("XC:"):
        return (None, None)
    venue, tag, dist = key[3:].rpartition(":d")
    if tag and dist.isdigit():
        return (venue, int(dist))
    return (None, None)


def canonicalNames(cur, ids):
    """canonical_id -> canonical_name, for matching against his free text."""
    if not ids:
        return {}
    cur.execute("""SELECT DISTINCT canonical_id::text, canonical_name
                   FROM course_canonical
                   WHERE canonical_id::text = ANY(%s)""", (list(ids),))
    return {r[0]: r[1] for r in cur.fetchall()}


def potentialAt(pool, sport, distance):
    """
    g(log distance) from the live artifact -- the SAME function normalize_distance
    uses, imported rather than reimplemented so the conversion cannot drift from
    the normalization it is undoing.
    """
    from normalize_distance import (_distancePotentialEntry,
                                    _evalDistancePotential)
    entry = _distancePotentialEntry(pool, sport)
    return _evalDistancePotential(entry, math.log(distance))


def toSlaneyUnits(difficulty, distance, ref_difficulty, ref_distance,
                  pool="hs_m", sport="XC"):
    """
    Our delta -> his raw time ratio, distance put back in.

    ratio = (1+d_c)/(1+d_ref) * exp(g(log dist_c) - g(log dist_ref))

    The first factor is terrain; the second undoes the 5k normalization for both
    courses. At the reference course both are 1 and the result is 1.000, which is
    his Crystal Springs anchor by construction.
    """
    terrain = (1.0 + difficulty) / (1.0 + ref_difficulty)
    dist = math.exp(potentialAt(pool, sport, distance)
                    - potentialAt(pool, sport, ref_distance))
    return terrain * dist


# ------------------------------------------------------------------ #
# CHUNK 3 -- COMPARE
# ------------------------------------------------------------------ #

def report(matched):
    """
    Correlation and worst disagreements.

    ★ SPEARMAN AS WELL AS PEARSON. Pearson on a ratio is dominated by the
      distance spread -- 0.52 to 1.05 across his table -- so it will look
      excellent even if the terrain ordering is wrong. Spearman on the residual
      after distance is the part that tests our difficulty estimate.
    """
    if len(matched) < 5:
        print(f"[cmp] only {len(matched)} matched courses -- too few")
        return

    ours = np.array([m["ours"] for m in matched])
    his = np.array([m["his"] for m in matched])
    print(f"\n[cmp] {len(matched)} courses matched")
    print(f"    pearson  r = {np.corrcoef(ours, his)[0, 1]:.4f}")
    ro = np.argsort(np.argsort(ours)); rh = np.argsort(np.argsort(his))
    print(f"    spearman r = {np.corrcoef(ro, rh)[0, 1]:.4f}")
    print(f"    mean |ours - his| = {np.abs(ours - his).mean():.4f}")
    print(f"    bias  (ours - his) = {(ours - his).mean():+.4f}")

    print("\n[cmp] worst 20 disagreements")
    print("      ours     his    diff   dist  deg   course")
    for m in sorted(matched, key=lambda m: -abs(m["ours"] - m["his"]))[:20]:
        print(f"    {m['ours']:6.3f}  {m['his']:6.3f}  "
              f"{m['ours'] - m['his']:+6.3f}  {m['dist']:5d} "
              f"{int(m['degree']):5d}   {m['name']}")

    # ★ BY DISTANCE. The worst-20 table showed six negatives at 4800 and six
    #   positives at 5000, several at high degree -- a distance-dependent
    #   disagreement rather than scatter. His numbers carry NO distance
    #   handling; ours puts distance back through the potential. So a per-
    #   distance bias measures OUR potential against his empirical data, which
    #   is precisely what distance_bake changed.
    print("\n[cmp] bias by distance  (ours - his; near 0 means the potential "
          "agrees)")
    print("     dist   courses     bias   mean|diff|")
    by_dist = {}
    for m in matched:
        by_dist.setdefault(m["dist"], []).append(m["ours"] - m["his"])
    for dist in sorted(by_dist):
        d = np.array(by_dist[dist])
        if d.size < 3:
            continue
        print(f"    {dist:>5} {d.size:>9} {d.mean():>+8.4f} "
              f"{np.abs(d).mean():>11.4f}")

    print("\n[cmp] WATCHED courses")
    print("      ours     his    diff   dist  deg   course")
    seen = set()
    for m in sorted(matched, key=lambda m: m["name"]):
        key = normName(m["name"])
        if any(w in key for w in _WATCH) and key not in seen:
            seen.add(key)
            print(f"    {m['ours']:6.3f}  {m['his']:6.3f}  "
                  f"{m['ours'] - m['his']:+6.3f}  {m['dist']:5d} "
                  f"{int(m['degree']):5d}   {m['name']}")
    if not seen:
        print("      (none of the watched names matched)")

    print("\n[cmp] best 10 agreements")
    for m in sorted(matched, key=lambda m: abs(m["ours"] - m["his"]))[:10]:
        print(f"    {m['ours']:6.3f}  {m['his']:6.3f}  "
              f"{m['ours'] - m['his']:+6.3f}  {m['dist']:5d} "
              f"{int(m['degree']):5d}   {m['name']}")


def main(slaney_path, npz_path, pool="hs_m", min_degree=25):
    from database import getConn

    his_rows = parseSlaney(slaney_path)
    print(f"[cmp] parsed {len(his_rows)} courses from {slaney_path}")
    if not his_rows:
        print("[cmp] nothing parsed -- check the file format")
        return

    difficulty, degree, keys = loadOurs(npz_path)
    ids = {splitKey(k)[0] for k in keys if splitKey(k)[0]}
    with getConn() as conn, conn.cursor() as cur:
        names = canonicalNames(cur, ids)

    # our cells, keyed by normalized name -> list of (distance, difficulty, deg)
    ours_by_name = {}
    for i, k in enumerate(keys):
        cid, dist = splitKey(k)
        if not cid or not dist or degree[i] < min_degree:
            continue
        nm = normName(names.get(cid, ""))
        if nm:
            ours_by_name.setdefault(nm, []).append((dist, difficulty[i], degree[i]))

    # reference: his anchor
    ref = ours_by_name.get(_REF_NAME)
    if not ref:
        print(f"[cmp] '{_REF_NAME}' not found among our cells -- cannot anchor. "
              f"Pass a different reference or check the canonical name.")
        return
    ref_dist, ref_diff, _ = max(ref, key=lambda t: t[2])   # best-measured cell
    print(f"[cmp] anchor: {_REF_NAME} at {ref_dist} m, delta {ref_diff:+.4f}")

    matched = []
    for name, miles, boys, girls in his_rows:
        nm = normName(name)
        cells = ours_by_name.get(nm)
        if not cells:
            continue
        target = miles * 1609.34
        dist, diff, deg = min(cells, key=lambda t: abs(t[0] - target))
        if abs(dist - target) > 250:        # different course at that venue
            continue
        matched.append({
            "name": name, "dist": dist, "degree": deg,
            "his": boys if pool.endswith("_m") else girls,
            "ours": toSlaneyUnits(diff, dist, ref_diff, ref_dist, pool)})

    report(matched)


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: compare_slaney.py <course_difficulties.html> "
              "[pair_validated.npz]")
        sys.exit(1)
    main(args[0],
         # pair_all writes pair_difficulty.npz; pair_validated.npz only exists
         # if --validate was passed.
         args[1] if len(args) > 1
         else os.path.join(here, "pair_difficulty.npz"))