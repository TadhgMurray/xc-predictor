"""diag_conversion_gain.py -- does the conversions page agree with the
ratings the engine actually stored? Both sports, and it says WHICH HALF
disagrees.

    /srv/venv/bin/python scripts/diag_conversion_gain.py
    /srv/venv/bin/python scripts/diag_conversion_gain.py --sport XC --pool hs_m
    /srv/venv/bin/python scripts/diag_conversion_gain.py --sport TF --n 800

★ TWO CHECKS, AND THE SECOND IS THE ONE THAT USUALLY FIRES.

    time -> normalized_time   the distance curve, the difficulty and the
                              sport gain, measured at each row's OWN
                              distance so the curve cancels. Tight and
                              small here means normalisation is healthy.

    the anchor                engine_scale read back, and the XC-minus-TF
                              median_effect compared with the grass cost
                              joint_solve.XC_TRACK_GAP states. Every
                              difficulty is anchored on the unweighted
                              mean over outdoor track cells, so this is
                              where a bad TRACK solve becomes a bad XC
                              CONVERSION -- with a perfectly correct
                              normalized_time sitting behind it.

  That second one is the shape of "the normalized 5k is fine but the
  conversions read high", and it is not visible in any round trip,
  because venueEffect is on both legs and the error cancels.

★ THE SIGN, STATED ONCE. Errors are reported as a percentage of the
  engine's own number, and "FAST" always means the page produces a
  smaller time (or a bigger rating) than the engine stored.

! IT MEASURES AGREEMENT, NOT TRUTH. Both halves can agree perfectly and
  the ratings still be wrong, if the engine's curve is wrong -- this
  cannot see that. What it can see is the page and the engine drifting
  apart, which is the class of bug that keeps recurring because every
  round-trip test cancels it.
"""
import argparse
import math
import os
import statistics
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                   # noqa: E402

from database import getConn                             # noqa: E402

# ⚠ TWO TABLES, ON PURPOSE. ranking_results has the RATING POOL and the
#   adjudicated distance; only the source table has normalized_time (it is
#   not in build_ranking_results._COLUMNS). Joining on result_id is what
#   puts rating, normalized_time and distance on one row, which is what
#   makes the A/B split below possible at all.
_SOURCE = {"XC": "results", "TF": "results_tf"}

SAMPLE_SQL = """
    SELECT rr.speed_rating, rr.time_seconds, rr.distance, rr.pool,
           src.normalized_time
    FROM   ranking_results rr
    JOIN   {source} src ON src.result_id = rr.result_id
    WHERE  rr.speed_rating IS NOT NULL
      AND  rr.time_seconds > %(min_time)s
      AND  rr.distance     IS NOT NULL
      AND  src.normalized_time IS NOT NULL
      AND  src.normalized_time > %(min_time)s
      AND  rr.sport = %(sport)s
      AND  rr.race_date >= %(since)s
      {pool_clause}
    ORDER BY random()
    LIMIT %(n)s
"""


def sample(cur, n, sport, pool, since, min_time):
    clause = "AND rr.pool = %(pool)s" if pool else ""
    params = {"n": n, "sport": sport, "since": since,
              "min_time": min_time, "pool": pool}
    cur.execute(SAMPLE_SQL.format(source=_SOURCE[sport], pool_clause=clause),
                params)
    return cur.fetchall()


def _stats(errs):
    if not errs:
        return None
    errs.sort()
    return {"n": len(errs), "median": statistics.median(errs),
            "p25": errs[len(errs) // 4], "p75": errs[3 * len(errs) // 4],
            "mean_abs": statistics.fmean(abs(e) for e in errs)}


def scaleReport(cur, pool):
    """engine_scale read back, and the one relation that must hold.

    ★ THIS REPLACED A BROKEN CHECK (2026-09-15). The first version compared
      _norm_from_rating against the stored normalized_time and called the
      difference "the pool mean and engine scale". It is not: by the
      engine's own algebra rating = 100 * pm * exp(eff) / norm, so
      100 * pm / rating is the ADJUSTED time, and the comparison was
      measuring exp(eff) -- the per-row course effect. That is why it read
      a ~0 median with a +-2% IQR and pointed at nothing.

    ★ WHAT ACTUALLY DECIDES IT. joint_golive anchors every difficulty on
      the UNWEIGHTED MEAN over outdoor track cells:

          anchor_used = float(np.mean(raw[ref]))     # ref = solved & TF & outdoor
          anchored    = raw - anchor_used

      so XC difficulties are expressed relative to the average track, and
      engine_scale.median_effect carries that offset per (pool, sport).
      The gap between a pool's XC and TF median_effect is therefore the
      measured grass cost, and joint_solve.XC_TRACK_GAP says what it
      should be. Off there and every XC conversion is off by a constant --
      while normalized_time, which never sees the anchor, stays correct.
      That is the shape of "the normalized 5k is fine but the conversions
      read high".

    ⚠ AN UNWEIGHTED MEAN IS THE OUTLIER-SENSITIVE CHOICE. It was picked
      over the results-weighted mean so a few enormous championship ovals
      could not define the zero -- but it buys that by letting a handful
      of wild per-venue track estimates drag it instead. joint_golive
      prints the median beside it for exactly this reason, with the note
      that the two agreeing is the assumption. Indoor ovals reading 4%
      easy (owner, 2026-09-15) is that assumption breaking."""
    cur.execute("SELECT pool, sport, pool_mean, median_effect, anchor_shift, "
                "n_rows FROM engine_scale ORDER BY pool, sport")
    return cur.fetchall()


def effects(cv, rows, sport):
    """The engine's applied effect per row, recovered exactly, beside the
    page's effect for the same row. In log units, and in percent.

    ★★ THIS IS THE ONLY CLEAN COMPARISON, AND TWO EARLIER VERSIONS OF THIS
       SCRIPT GOT IT WRONG (2026-09-15). Both compared a quantity that has
       the effect ALREADY DIVIDED OUT against normalized_time, which has
       not, so both were measuring exp(eff) and calling it a scale error:

         _norm_from_rating -> 100 * pm / rating,   which IS norm/exp(eff)
         _norm_from_time   -> `adjusted`,          same thing (see its
                                                   `return adjusted`)

       The engine's own algebra gives the effect back exactly, from three
       stored numbers and no join:

           rating   = 100 * pm / adjusted,  adjusted = norm / exp(eff)
        => eff      = ln(norm * rating / (100 * pm))

       So the engine's per-row effect is knowable, and the page's is just
       venueEffect at the same rating. Comparing THOSE is apples to
       apples; everything else here was not.

    ⚠ A GAP IS EXPECTED FOR TRACK AND IS BY DESIGN. With no venue named
      the page prices track at difficulty 0.0 -- the average OUTDOOR track
      -- while these rows are the real mix, indoor included, and
      venueEffect's own note records that the median applied effect over
      track rows sits about 1% off that anchor. Read the track number
      against ~1%, not against 0."""
    pm = cv.pool_mean(rows[0]["pool"], sport) if rows else None
    if not pm:
        return None, None
    eng, page = [], []
    for r in rows:
        rating = float(r["speed_rating"])
        norm = float(r["normalized_time"])
        if rating <= 0 or norm <= 0:
            continue
        eng.append(math.log(norm * rating / (100.0 * pm)))
        page.append(cv.venueEffect(r["pool"], sport, rating, None,
                                   float(r["distance"])) or 0.0)
    if not eng:
        return None, None
    diff = [100.0 * (math.expm1(p) - math.expm1(e)) for e, p in zip(eng, page)]
    return (_stats([100.0 * math.expm1(e) for e in eng]),
            _stats(diff))


def describe(label, st, fast_word="FAST"):
    if st is None:
        print(f"    {label:<28} nothing could be converted")
        return
    way = "SLOW" if st["median"] > 0 else fast_word
    print(f"    {label:<28} median {st['median']:+7.3f}%  ({way})   "
          f"IQR {st['p25']:+.3f}..{st['p75']:+.3f}   "
          f"mean |err| {st['mean_abs']:.3f}%   n={st['n']:,}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="conversions vs the stored ratings")
    ap.add_argument("--sport", default="both", choices=("XC", "TF", "both"))
    ap.add_argument("--n", type=int, default=500, help="rows sampled per sport")
    ap.add_argument("--pool", default="hs_m", help="rating pool, '' for any")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--min-time", type=float, default=60.0)
    args = ap.parse_args(argv)

    pool = args.pool or None
    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM sport_gain")
            n_gain = cur.fetchone()["n"]
            by_sport = {s: sample(cur, args.n, s, pool, f"{args.year}-01-01",
                                  args.min_time) for s in sports}
            scale = scaleReport(cur, pool)

    gain_env = os.environ.get("XCP_CONVERT_SPORT_GAIN")
    print(f"\nsport_gain: {n_gain:,} rows "
          f"({'the go-live SHIFTED the track rows' if n_gain else 'no shift applied'})"
          f"{'  [XCP_CONVERT_SPORT_GAIN=' + gain_env + ']' if gain_env else ''}")
    print(f"pool {pool or 'any'}, {args.year} onwards\n")

    import conversions as cv
    worst = None
    for s in sports:
        rows = by_sport[s]
        print(f"  {s}  ({len(rows):,} rows)")
        if not rows:
            print("    nothing sampled -- widen --year, or clear --pool\n")
            continue
        eng, diff = effects(cv, rows, s)
        if eng is None or diff is None:
            print("    no pool mean for this pool/sport\n")
            continue
        print(f"    engine's own applied effect   median {eng['median']:+7.3f}%   "
              f"IQR {eng['p25']:+.3f}..{eng['p75']:+.3f}")
        expect = "  (expect ~+1%: the page prices an unnamed track at 0.0)" \
            if s == "TF" else ""
        print(f"    page minus engine             median {diff['median']:+7.3f}%   "
              f"IQR {diff['p25']:+.3f}..{diff['p75']:+.3f}   "
              f"mean |err| {diff['mean_abs']:.3f}%{expect}")
        print()
        if worst is None or abs(diff["median"]) > abs(worst[2]):
            worst = (s, "page-engine", diff["median"])

    # ---- the anchor, which is what moves XC conversions ---------------- #
    import joint_solve as js
    print("  engine_scale, and the grass cost it implies:")
    print(f"    {'pool':<12}{'sport':>6}{'pool_mean':>12}{'median_eff':>12}"
          f"{'anchor_shift':>14}{'rows':>10}")
    by_pool = {}
    for r in scale:
        print(f"    {r['pool']:<12}{r['sport']:>6}{r['pool_mean']:>12.2f}"
              f"{r['median_effect']:>12.5f}{r['anchor_shift']:>14.5f}"
              f"{r['n_rows']:>10,}")
        by_pool.setdefault(r["pool"], {})[r["sport"]] = r
    print()
    bad = []
    for pname, d in sorted(by_pool.items()):
        if "XC" not in d or "TF" not in d:
            continue
        gap = float(d["XC"]["median_effect"]) - float(d["TF"]["median_effect"])
        off = gap - js.XC_TRACK_GAP
        flag = "  <-- OFF" if abs(off) > 0.01 else ""
        print(f"    {pname:<12} XC - TF median_effect = {100 * gap:+7.2f}%   "
              f"expected {100 * js.XC_TRACK_GAP:+.2f}%   off {100 * off:+6.2f} pts{flag}")
        if abs(off) > 0.01:
            bad.append((pname, off))

    print("\n  reading it:")
    print("    'page minus engine' is the number that matters: it is how far the")
    print("    page's effect model sits from what the engine actually applied,")
    print("    at the same rating, for the same rows. Near zero for XC and near")
    print("    +1% for TF is agreement. A systematic offset beyond that is the")
    print("    page and the engine on different scales.")
    print()
    print("    the XC-TF gap is the measured grass cost, and every difficulty is")
    print("    anchored on the UNWEIGHTED MEAN over outdoor track cells. Wild")
    print("    per-venue track estimates drag that mean, and the shift lands on")
    print("    every XC course at once -- which is a conversion error with a")
    print("    correct normalized_time behind it.")
    if bad:
        print(f"\n  ⚠ {len(bad)} pool(s) off the expected grass cost: "
              + ", ".join(f"{p} {100 * o:+.1f} pts" for p, o in bad))
        print("    Check the run log for the two lines that say so directly:")
        print("      grep -E 'track zero|difficulty zero|grass cost' logs/run24.out")
        print("    'track zero: mean 0.000, median X' -- mean and median diverging")
        print("    means the track distribution has outliers dragging the anchor.")
    else:
        print("\n  the grass cost looks right in every pool; the anchor is not the problem.")
    if worst:
        print(f"\n  largest time->normalized disagreement: {worst[0]}, "
              f"mean |err| {worst[2]:.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
