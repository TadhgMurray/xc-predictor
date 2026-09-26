"""
own_race_shift.py -- the races whose runners were all far faster than they
ran in their own other races that month: a wrong distance, not a great day.
Called by impossible_race.py (step 06c), which condemns them the way it
condemns a record-beating race.

★ WHY (owner, 2026-09-26, the Sublimity Road Run: a 3200 stored as 5000,
  14:33 read as a 5k, rated 146): "outliers like that aren't getting caught
  because they're not world records." The record rule only fires on times
  no human can run. A 3200 jogged at 7 min/mile and stored as a 5k is
  nowhere near a record, but every runner in it is 20-40% faster than
  they are anywhere else -- and that is what this measures.

THE ESTIMATOR (engine/bad_distance.py's, per race): for each runner with
another race within +-WINDOW days, log(normalized time here) minus the
mean log normalized time of their other races. Ability cancels (same
runner), fitness cancels (symmetric window). The race's median of that is
its shift. A course and a day move it too, which is why the threshold is
measured, not chosen:

    s_between  how much race medians spread across well-linked races
               (courses and days, the real variation)
    s_within   how much runners spread inside one race
    sd(race)   sqrt(s_between^2 + (pi/2) s_within^2 / n) for n runners
    Z          the family-wise cut: across all N races checked, a 5% chance
               that ANY honest race is condemned (Bonferroni):
               Z = Phi^-1(1 - 0.05/N). With ~50,000 races a season that is
               about 4.9 sd -- a wrong distance is typically 10 or more.

A race is condemned when its shift is more than Z sd FASTER than the centre
and at least two thirds of its linked runners are faster than the centre.

! ONLY THE FAST SIDE IS CONDEMNED. A course cannot make a field 30% faster
  than it runs everywhere else; a hill, mud or altitude can make it slower.
  The slow side is printed for review (a slow race with voters has its
  shift absorbed into the course difficulty, so its ratings are not
  inflated -- the +82% courses on the scan are that).
"""
import math
import statistics

import numpy as np

WINDOW = 30            # days either side, as the bracket engine's window
MIN_LINKED = 2         # a race needs this many runners with another race
_WELL_LINKED = 10      # races used to measure how much races really vary
K = 1.06               # the normaliser's distance exponent (propose_distances.K)
FAMILY_ALPHA = 0.05    # chance of ANY false condemnation in a season's check
_CONVENTIONS = (1200, 1500, 1600, 2000, 2400, 2500, 3000, 3200, 4000, 4023,
                4800, 5000, 6000, 8000, 10000)


def ownOtherMeans(pk, day, race, lnt, window=WINDOW):
    """Per row: (sum, count) of the same person's log normalized times at
    OTHER races within +-window days. Arrays in any order.

    ! ONLY THE PAIRS STILL ALIVE ARE CARRIED TO THE NEXT LAG. Sorted by
      (person, day), a row whose partner k places on is another person or
      out of the window has no partner at k+1 either, so each lag looks at
      the survivors of the last one instead of every row again."""
    order = np.lexsort((day, pk))
    p, d, r, v = pk[order], day[order], race[order], lnt[order]
    n = p.size
    s = np.zeros(n)
    c = np.zeros(n, dtype=np.int64)
    i = np.arange(max(n - 1, 0))
    k = 1
    while i.size:
        j = i + k
        near = (p[i] == p[j]) & (d[j] - d[i] <= window)
        i, j = i[near], j[near]
        ok = r[i] != r[j]
        ii, jj = i[ok], j[ok]
        s += np.bincount(ii, weights=v[jj], minlength=n)
        s += np.bincount(jj, weights=v[ii], minlength=n)
        c += np.bincount(ii, minlength=n) + np.bincount(jj, minlength=n)
        k += 1
        i = i[i + k < n]
    out_s, out_c = np.zeros(n), np.zeros(n, dtype=np.int64)
    out_s[order], out_c[order] = s, c
    return out_s, out_c


def raceMedians(race, gap):
    """{race: (n, median gap, rows)} over the linked rows."""
    order = np.lexsort((gap, race))
    r, g = race[order], gap[order]
    out = {}
    if r.size == 0:
        return out
    starts = np.flatnonzero(np.r_[True, r[1:] != r[:-1]])
    ends = np.r_[starts[1:], r.size]
    for a, b in zip(starts, ends):
        vals = g[a:b]
        out[int(r[a])] = (b - a, float(np.median(vals)), vals)
    return out


def _mad(x):
    x = np.asarray(x, dtype=np.float64)
    return 1.4826 * float(np.median(np.abs(x - np.median(x)))) if x.size else float("nan")


def judge(med):
    """From raceMedians' output: the measured spread and the verdicts.
    Returns (stats dict, [(race, n, median, z)] condemned fast,
    [(race, n, median, z)] slowest for review). Pure."""
    well = [m for n, m, _ in med.values() if n >= _WELL_LINKED]
    if len(well) < 30:
        return {"races": len(med), "note": "too few well-linked races to measure"}, [], []
    centre = float(np.median(well))
    s_between = _mad(well)
    within = np.concatenate([v - m for n, m, v in med.values() if n >= 3]) \
        if any(n >= 3 for n, _, _ in med.values()) else np.array([])
    s_within = _mad(within) if within.size else s_between
    judged = {k: v for k, v in med.items() if v[0] >= MIN_LINKED}
    n_races = max(len(judged), 2)
    z_cut = statistics.NormalDist().inv_cdf(1.0 - FAMILY_ALPHA / n_races)
    fast, slow, every = [], [], {}
    for key, (n, m, vals) in judged.items():
        sd = math.sqrt(s_between ** 2 + (math.pi / 2) * s_within ** 2 / n)
        z = (m - centre) / sd if sd > 0 else 0.0
        every[key] = (n, m, z)
        if z < -z_cut and (vals < centre).sum() >= math.ceil(2 * n / 3):
            fast.append((key, n, m, z))
        elif z > z_cut:
            slow.append((key, n, m, z))
    fast.sort(key=lambda t: t[3])
    slow.sort(key=lambda t: -t[3])
    stats = dict(races=len(judged), centre=centre, s_between=s_between, every=every,
                 s_within=s_within, z_cut=z_cut,
                 ratio_at_3=math.exp(centre - z_cut * math.sqrt(
                     s_between ** 2 + (math.pi / 2) * s_within ** 2 / 3)))
    return stats, fast, slow


def impliedDistance(recorded, shift, centre=0.0):
    """The distance the shift says was run, snapped to a convention when
    one is within 4% (else the raw estimate, rounded)."""
    if not recorded:
        return None
    est = float(recorded) * math.exp((shift - centre) / K)
    near = min(_CONVENTIONS, key=lambda c: abs(c - est))
    return near if abs(near - est) / est <= 0.04 else round(est, -1)


LOAD_SQL = """
    SELECT r.person_id, r.source, r.meet_id, r.div_id, r.date,
           r.normalized_time,
           COALESCE(dov.distance, m.distance,
                    (mt.division_distances -> r.div_id::text ->> 'distance')::real,
                    mt.distance)::real AS distance
    FROM   results r
    LEFT JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
    LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id AND mt.sport = 'XC'
    LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
    WHERE  r.normalized_time > 0 AND r.person_id IS NOT NULL
      AND  r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' AND r.date >= %s
"""


def findXc(conn, since, window=WINDOW):
    """Load the XC rows since `since` and judge every race. Returns
    (stats, fast, slow, info) where info[race] = (source, meet, div, date,
    recorded distance) and fast/slow come from judge()."""
    import datetime as dt
    keys, info = {}, {}
    pk, day, race, lnt = [], [], [], []
    epoch = dt.date(1970, 1, 1)
    with conn.cursor(name="own_race_shift") as cur:
        cur.itersize = 200_000
        cur.execute(LOAD_SQL, (since,))
        for pid, src, meet, div, date, nt, dist in cur:
            try:
                dd = dt.date.fromisoformat(str(date)[:10])
            except ValueError:
                continue
            k = (src, meet, div)
            rid = keys.get(k)
            if rid is None:
                rid = keys[k] = len(keys)
                info[rid] = (src, meet, div, str(date)[:10], dist)
            pk.append(int(pid))
            day.append((dd - epoch).days)
            race.append(rid)
            lnt.append(math.log(float(nt)))
    pk, day = np.array(pk, dtype=np.int64), np.array(day, dtype=np.int64)
    race, lnt = np.array(race, dtype=np.int64), np.array(lnt)
    s, c = ownOtherMeans(pk, day, race, lnt, window)
    has = c > 0
    gap = lnt[has] - s[has] / c[has]
    stats, fast, slow = judge(raceMedians(race[has], gap))
    stats["rows"] = int(pk.size)
    return stats, fast, slow, info
