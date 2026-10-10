"""comps.py -- "Runners like you": the high-schoolers who were where this
athlete is -- same sport, same pool, same grade, a rating and a trajectory
the data cannot tell apart from theirs -- and what happened to them next.
Reads season_comps (build_comps.py, step 10f3).

★ "CANNOT TELL APART" IS MEASURED. A season rating is the mean of n races
  scattering by sigma (season_comps_meta), so its standard error is
  sigma / sqrt(n). A comp matches when its rating is within the two
  seasons' combined standard error of this one, and -- when both have a
  season before -- when its gain is within the two gains' combined error.
  Nothing here is a hand-picked band: a runner with more races is pinned
  tighter and gets closer comps.

★ ONLY FUTURES THAT HAVE HAPPENED. An outcome counts only once its season
  is over: the newest stored season may be under way, so "next season" needs
  year + 1 < newest, "senior season" year + (12 - grade) < newest, and
  "college" a full year past the senior season, because a missing college
  row means "did not run in college" only after their first college season
  could have shown up.

★ THE MODEL BESIDE THE COMPS. recruit_projection holds the model's own
  one-year projection for this athlete-season; it is shown next to the
  comps' next-season spread. The comps are what happened to people like
  this; the model is a fit. Where they disagree, the model is the one to
  look at.

! NEVER A 500: a missing table, no season on the floor, no comps -- each is
  None or an empty block with a reason, and the page goes on without it.
"""
import math

import psycopg2

from rankings import nameLateral

EXAMPLES = 5            # rows shown by name; display only, the stats use every comp
CLASS_WORD = {9: "freshmen", 10: "sophomores", 11: "juniors", 12: "seniors"}
CLASS_SHORT = {9: "Fr", 10: "So", 11: "Jr", 12: "Sr"}


def _pct(vals, q):
    """Linear-interpolated percentile of a sorted list (q in 0..1)."""
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


# ★ THE FAN ON THE ATHLETE CHART (owner, 2026-10-10: "runners like you
#   averaged 129 the next season (range 124-134)"). The range is the comps'
#   own 10th to 90th percentile -- where eight in ten of them landed -- and
#   the inner band is the middle half the recruit page already prints. Not
#   the min and max: one comp who got hurt or one who doubled their mileage
#   would set the range alone, and it would widen with every comp added.
#   Not a sd band either: next-season changes are skewed (a bad season
#   falls further than a good one climbs), and percentiles read off the
#   comps say what happened without assuming a shape.
FAN_Q = (0.10, 0.90)
# ! A 10th PERCENTILE NEEDS A RUNNER BEYOND IT ON EACH SIDE: below ten comps
#   the "range" is just the two extreme comps, so the outer band is left
#   off (the middle half and the average are still drawn).
FAN_MIN = int(round(1 / FAN_Q[0]))


def _spread(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return {"n": len(vals), "p25": _pct(vals, .25), "median": _pct(vals, .5),
            "p75": _pct(vals, .75),
            # ★ added 2026-10-10 for the chart's fan (FAN_Q); additive keys,
            #   the recruit page reads none of them
            "mean": sum(vals) / len(vals),
            "p10": _pct(vals, FAN_Q[0]) if len(vals) >= FAN_MIN else None,
            "p90": _pct(vals, FAN_Q[1]) if len(vals) >= FAN_MIN else None}


def nextSeasonFan(result):
    """The chart's next-season band from a runnersLikeYou() answer, or None:
    {year, sport, mean, p10, p25, median, p75, p90, n, rating, grade}."""
    if not result or not result.get("next"):
        return None
    s, nx = result["subject"], result["next"]
    return {"year": int(s["year"]) + 1, "sport": s["sport"], "grade": s.get("grade"),
            "rating": float(s["rating"]), "n": nx["n"], "mean": nx["mean"],
            "p10": nx.get("p10"), "p25": nx["p25"], "median": nx["median"],
            "p75": nx["p75"], "p90": nx.get("p90"), "fan_q": list(FAN_Q)}


def subjectSeason(cur, person_id, sport):
    """The athlete's newest high-school season in `sport` on the floor."""
    cur.execute("""SELECT * FROM season_comps WHERE person_id = %s AND sport = %s
                   ORDER BY year DESC LIMIT 1""", (person_id, sport))
    r = cur.fetchone()
    return dict(r) if r else None


def runnersLikeYou(cur, person_id, sport):
    """{subject, sigma, n, next, senior, college, examples, model} or None
    (no table, no season)."""
    try:
        cur.execute("SAVEPOINT comps")
        out = _runnersLikeYou(cur, person_id, sport)
        cur.execute("RELEASE SAVEPOINT comps")
        return out
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT comps")
        print(f"comps: {type(exc).__name__}: {str(exc).splitlines()[0]}", flush=True)
        return None


def _runnersLikeYou(cur, person_id, sport):
    s = subjectSeason(cur, person_id, sport)
    if not s or s.get("grade") is None:
        return None
    cur.execute("SELECT sigma, newest FROM season_comps_meta WHERE sport = %s AND pool = %s",
                (sport, s["pool"]))
    m = cur.fetchone()
    if not m:
        return None
    sigma, newest = float(m["sigma"]), int(m["newest"])
    var_s = sigma ** 2 / max(int(s["n_races"]), 1)
    has_prev = s.get("prev_rating") is not None and s.get("prev_n")
    var_ds = (var_s + sigma ** 2 / int(s["prev_n"])) if has_prev else None
    gain = (float(s["rating"]) - float(s["prev_rating"])) if has_prev else None
    # the widest a comp's own error can make the band: one with the fewest
    # races the floor allows -- an outer bound for the index range only
    cur.execute("SELECT min(n_races) AS n FROM season_comps WHERE sport = %s AND pool = %s",
                (sport, s["pool"]))
    min_n = max(int((cur.fetchone() or {}).get("n") or 1), 1)
    outer = math.sqrt(var_s + sigma ** 2 / min_n)
    params = {"sport": sport, "pool": s["pool"], "g": s["grade"], "r": float(s["rating"]),
              "lo": float(s["rating"]) - outer, "hi": float(s["rating"]) + outer,
              "me": person_id, "y": s["year"], "var_s": var_s, "sig2": sigma ** 2,
              "gain": gain, "var_ds": var_ds}
    traj = ("""AND c.prev_rating IS NOT NULL
               AND abs((c.rating - c.prev_rating) - %(gain)s)
                   <= sqrt(%(var_ds)s + %(sig2)s / c.n_races + %(sig2)s / c.prev_n)"""
            if has_prev else "")
    cur.execute(f"""
        SELECT c.*, a.name,
               abs(c.rating - %(r)s) / sqrt(%(var_s)s + %(sig2)s / c.n_races) AS dist
        FROM   season_comps c
        {nameLateral('c')}
        WHERE  c.sport = %(sport)s AND c.pool = %(pool)s AND c.grade = %(g)s
          AND  c.rating BETWEEN %(lo)s AND %(hi)s
          AND  c.person_id <> %(me)s AND c.year < %(y)s
          AND  abs(c.rating - %(r)s) <= sqrt(%(var_s)s + %(sig2)s / c.n_races)
          {traj}
    """, params)
    comps = [dict(r) for r in cur.fetchall()]

    g = int(s["grade"])
    nxt = [c for c in comps if c["year"] + 1 < newest]
    sen = [c for c in comps if c["year"] + (12 - g) < newest] if g < 12 else []
    col = [c for c in comps if c["year"] + (12 - g) + 1 < newest]
    n_col = sum(1 for c in col if c.get("college"))
    divs = {}
    for c in col:
        if c.get("college"):
            d = c.get("college_division") or "Other"
            divs[d] = divs.get(d, 0) + 1
    # the closest comps whose story is complete enough to tell: senior season
    # known (or, for a senior, the college question settled)
    told = sen if g < 12 else col
    examples = sorted(told, key=lambda c: (float(c["dist"]), -c["year"]))[:EXAMPLES]

    model = None
    try:
        cur.execute("SAVEPOINT proj")
        cur.execute("""SELECT proj_rating, proj_sigma_pct, horizon_weeks
                       FROM recruit_projection
                       WHERE person_id = %s AND sport = %s AND year = %s""",
                    (person_id, sport, s["year"]))
        pr = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT proj")
        if pr:
            model = dict(pr)
    except psycopg2.Error:
        cur.execute("ROLLBACK TO SAVEPOINT proj")

    if model and model.get("proj_sigma_pct") is not None:
        # the model's spread as the same middle half the comps' rows show:
        # a normal's quartiles sit 0.674 sd either side of its centre
        half = 0.6745 * float(model["proj_sigma_pct"]) / 100.0 * float(model["proj_rating"])
        model["p25"] = float(model["proj_rating"]) - half
        model["p75"] = float(model["proj_rating"]) + half

    return {
        "subject": s, "sigma": sigma, "se": math.sqrt(var_s), "gain": gain,
        "n": len(comps),
        "next": _spread(c.get("next_rating") for c in nxt),
        "next_pool": len(nxt),
        "senior": _spread(c.get("senior_rating") for c in sen) if g < 12 else None,
        "senior_pool": len(sen),
        "college": ({"n": len(col), "ran": n_col,
                     "divisions": sorted(divs.items(), key=lambda kv: -kv[1])}
                    if col else None),
        "examples": examples,
        "model": model,
    }
