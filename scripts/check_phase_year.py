# Project: xc-predictor / scripts
# File:    check_phase_year.py
# Purpose: Course difficulty by CALENDAR position across the whole academic
#          year, both sports, per pool. READ ONLY.
#
#     python scripts/check_phase_year.py
#     python scripts/check_phase_year.py --pool hs_m
#     python scripts/check_phase_year.py --self-test
#
# Run from the PROJECT ROOT. Needs the pack (packed_XC_TF.npz) and the
# solved difficulties (pair_difficulty.npz) from ONE run in engine/data.
# No database.
#
# ★ THE QUESTION THIS ANSWERS (issue #113). The engine's XC-versus-track
#   level is a within-academic-year population contrast: fall XC against
#   the following spring's track. Nothing inside an athlete-season separates
#   "track is easier" from "athletes are fitter in April", and the owner's
#   definition -- a rating is skill at that moment -- needs them separated.
#   A whole-year fitness curve (rust_fitness extended from days-since-opener
#   to day-of-academic-year) would carry the seasonal cycle instead of the
#   difficulties. Whether that curve is THERE TO FIT is what this table
#   shows, before any engine code is written for it:
#
#     a SMOOTH DECLINE in mean difficulty from August through the winter
#     into spring, continuous across the November/December boundary where
#     late XC (NXN, Foot Locker) overlaps the indoor openers
#         -> season phase is leaking into difficulty all year; a curve fits it
#
#     a STEP at the sport boundary with flat plateaus either side
#         -> the level is the surface, and a curve would only move it by
#            assumption
#
#   The indoor rows (December-February track) are the hinge: they are track
#   surface at near-fall fitness, the only place surface and calendar come
#   apart in the corpus. Their bucket is printed on its own line.
#
# ⚠ WHAT IS BEING BUCKETED. A cell is (venue, distance) and its difficulty
#   is one number; cells are bucketed by the MEDIAN day-of-year of their
#   rows, so a venue used all season lands mid-season. That blurs the curve
#   toward flat; it cannot manufacture a slope. The XC-season-only version
#   (check_phase_bias.py) has the same limit.
#
# ⚠ THE DIFFICULTIES ARE POST-RECENTRE. pair_difficulty.npz carries the
#   sport gap already deposited (delta_c += bbar * s_c), so XC sits above
#   TF by the applied gap. The step at the boundary is therefore the
#   applied level PLUS whatever the data put there; the boundary line
#   prints both the gap and the within-sport slopes so the two are read
#   apart.

import argparse
import os
import sys

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

import numpy as np                                    # noqa: E402

_DATA = os.path.join("engine", "data")

# Academic-year buckets, keyed by day-of-year. The year opens 1 August
# (doy 213); anything before that is the previous year's June/July tail.
# The point is the TREND and the boundary, not the borders.
_BUCKETS = [
    ("Aug",        213, 243, "XC"),
    ("early Sep",  244, 258, "XC"),
    ("late Sep",   259, 273, "XC"),
    ("Oct",        274, 304, "XC"),
    ("Nov",        305, 334, "XC"),
    ("Dec",        335, 366, "both"),     # NXN / Foot Locker + indoor openers
    ("Jan",          1,  31, "indoor"),
    ("Feb",         32,  59, "indoor"),
    ("Mar",         60,  90, "TF"),
    ("Apr",         91, 120, "TF"),
    ("May",        121, 151, "TF"),
    ("Jun+",       152, 212, "TF"),
]


def _academicDay(doy):
    """0 at 1 August, so the winter is contiguous instead of wrapping."""
    return (np.asarray(doy, dtype=np.float64) - 213.0) % 365.0


def cellStats(course, doy, athlete_pool, n_cells):
    """Per cell: median day-of-year (academic order), row count, modal pool."""
    ok = course >= 0
    c = course[ok]
    a_day = _academicDay(doy[ok])
    pool_row = athlete_pool[ok]
    order = np.argsort(c, kind="stable")
    c_s, d_s, p_s = c[order], a_day[order], pool_row[order]
    starts = np.flatnonzero(np.r_[True, c_s[1:] != c_s[:-1]])
    ends = np.r_[starts[1:], len(c_s)]
    med = np.full(n_cells, np.nan)
    n_rows = np.zeros(n_cells, dtype=np.int64)
    pool = np.full(n_cells, "", dtype=object)
    for s, e in zip(starts, ends):
        i = c_s[s]
        med[i] = (np.median(d_s[s:e]) + 213.0) % 365.0      # back to doy
        n_rows[i] = e - s
        vals, cnt = np.unique(p_s[s:e], return_counts=True)
        pool[i] = vals[np.argmax(cnt)]
    return med, n_rows, pool


def _wmean(x, w):
    return float(np.average(x, weights=w)) if w.sum() > 0 else float("nan")


def table(difficulty, solved, is_xc, is_tf, med_doy, n_rows, label,
          quiet=False):
    """Print one pool's table; return {bucket: (xc_mean, tf_mean)}."""
    use = solved & ~np.isnan(med_doy)
    out = {}
    if not quiet:
        print(f"\n  {label}: {int((use & is_xc).sum()):,} XC cells, "
              f"{int((use & is_tf).sum()):,} TF cells, by the median "
              f"day-of-year of each cell's races")
        print(f"    {'bucket':<11}{'':>6}{'XC cells':>10}{'XC rows':>11}"
              f"{'XC mean':>10}{'TF cells':>11}{'TF rows':>11}"
              f"{'TF mean':>10}")
        print("    " + "-" * 80)
    for name, lo, hi, tag in _BUCKETS:
        m = use & (med_doy >= lo) & (med_doy <= hi)
        mx, mt = m & is_xc, m & is_tf
        wx = n_rows[mx].astype(np.float64)
        wt = n_rows[mt].astype(np.float64)
        xm = _wmean(difficulty[mx], wx) if mx.any() else float("nan")
        tm = _wmean(difficulty[mt], wt) if mt.any() else float("nan")
        out[name] = (xm, tm)
        if quiet:
            continue
        fx = f"{xm:>+10.4f}" if mx.any() else f"{'--':>10}"
        ft = f"{tm:>+10.4f}" if mt.any() else f"{'--':>10}"
        print(f"    {name:<11}{tag:>6}{int(mx.sum()):>10,}{int(wx.sum()):>11,}"
              f"{fx}{int(mt.sum()):>11,}{int(wt.sum()):>11,}{ft}")
    return out


def verdict(rows, label):
    """The three numbers that decide: the within-XC slope Sep->Nov, the
    within-TF slope Mar->May, and the boundary step (last XC vs first TF,
    with the indoor months between them)."""
    def get(name, i):
        v = rows.get(name, (np.nan, np.nan))[i]
        return v

    xc_slope = get("Nov", 0) - get("early Sep", 0)
    tf_slope = get("May", 1) - get("Mar", 1)
    xc_end = np.nanmean([get("Oct", 0), get("Nov", 0)])
    tf_start = np.nanmean([get("Mar", 1), get("Apr", 1)])
    indoor = np.nanmean([get("Dec", 1), get("Jan", 1), get("Feb", 1)])
    step = tf_start - xc_end
    print(f"\n    {label}:  XC Sep->Nov {xc_slope:+.4f}   "
          f"TF Mar->May {tf_slope:+.4f}   "
          f"boundary (Mar/Apr TF - Oct/Nov XC) {step:+.4f}   "
          f"indoor Dec-Feb {indoor:+.4f}")
    if np.isnan(indoor):
        print("      no indoor cells in this pool: the boundary cannot be "
              "read apart from the applied gap here")
    elif not np.isnan(step):
        # Where the indoor months sit between the two plateaus says which
        # story the data tells. Indoor near the TF plateau: the step is
        # surface (track is track in January). Indoor between the two,
        # nearer XC: fitness is still climbing through the winter and a
        # year curve has something to fit.
        frac = (indoor - xc_end) / step if abs(step) > 1e-9 else np.nan
        print(f"      indoor sits {frac:+.2f} of the way from the XC "
              f"plateau to the TF plateau (0 = fall level, 1 = spring level)")
    return xc_slope, tf_slope, step, indoor


def run(cols, difficulty, solved, keys, pool_filter=None, quiet=False):
    course = np.asarray(cols["course"])
    doy = np.asarray(cols["doy"], dtype=np.float64)
    n_cells = len(keys)
    athlete_keys = cols["athlete_keys"]
    athlete_pool = np.array(
        [str(k[1]).split("|", 1)[0] if (k is not None and len(k) > 1
                                         and k[1]) else "unknown"
         for k in athlete_keys], dtype=object)[np.asarray(cols["athlete"])]

    med_doy, n_rows, cell_pool = cellStats(course, doy, athlete_pool, n_cells)
    is_xc = np.array([str(k).startswith("XC:") for k in keys], dtype=bool)
    is_tf = np.array([str(k).startswith("TF:") for k in keys], dtype=bool)

    results = {}
    pools = ([pool_filter] if pool_filter
             else ["ALL"] + sorted(p for p in set(cell_pool) if p))
    for p in pools:
        m = np.ones(n_cells, dtype=bool) if p == "ALL" else (cell_pool == p)
        if not (m & solved).any():
            continue
        rows = table(difficulty, solved & m, is_xc, is_tf, med_doy, n_rows,
                     p, quiet=quiet)
        results[p] = verdict(rows, p) if not quiet else rows
    return results


def loadRun(pack_path, diff_path):
    import pair_engine as pe
    cols = pe.loadPack(pack_path)
    npz = np.load(diff_path, allow_pickle=True)
    keys_p = [str(k) for k in cols["course_keys"]]
    keys_d = [str(k) for k in npz["course_keys"]]
    if keys_p != keys_d:
        raise SystemExit("  pack and pair_difficulty disagree on course_keys "
                         "-- they are from different runs. Re-run 08_golive "
                         "or point --diff at the matching file.")
    return (cols, np.asarray(npz["difficulty"], dtype=np.float64),
            np.asarray(npz["solved"], dtype=bool), keys_p)


# ---- self-test: a synthetic year with a known shape ------------------- #
def _synthetic(step_world):
    """Cells across the year. step_world=True: flat plateaus with a step at
    the boundary (surface). False: one smooth decline through the winter
    (fitness), indoor cells on the line."""
    rng = np.random.default_rng(3)
    keys, diff, doys, sport = [], [], [], []
    for i in range(240):
        d = (213 + i * 1.5) % 365            # Aug .. Jun
        a = (d - 213) % 365
        on_track = (a > 120)                 # December onward is track
        if step_world:
            val = 0.02 if not on_track else -0.03
        else:
            val = 0.03 - 0.06 * (a / 320.0)
        keys.append(("TF:" if on_track else "XC:") + f"v{i}:d1600")
        diff.append(val + rng.normal(0, 0.002))
        doys.append(d)
        sport.append(1 if on_track else 0)
    n = len(keys)
    rows = 20
    course = np.repeat(np.arange(n), rows)
    doy = np.repeat(np.array(doys), rows) + rng.integers(-3, 4, n * rows)
    cols = {"course": course, "doy": doy % 365 + 1,
            "athlete": np.zeros(n * rows, dtype=np.int64),
            "athlete_keys": [(1, "hs_m")]}
    return cols, np.array(diff), np.ones(n, dtype=bool), keys


def selfTest():
    print("[self-test] a surface step:")
    cols, diff, solved, keys = _synthetic(step_world=True)
    xs, ts, step, indoor = run(cols, diff, solved, keys, "hs_m")["hs_m"]
    assert abs(xs) < 0.01 and abs(ts) < 0.01, (xs, ts)
    assert step < -0.04, step
    assert abs(indoor - (-0.03)) < 0.005, indoor     # indoor on the TF plateau
    print("\n[self-test] a smooth year-long decline:")
    cols, diff, solved, keys = _synthetic(step_world=False)
    xs, ts, step, indoor = run(cols, diff, solved, keys, "hs_m")["hs_m"]
    assert xs < -0.01 and ts < -0.005, (xs, ts)
    assert step < -0.02, step
    # the indoor months sit between the plateaus, not on the spring one
    assert -0.012 < indoor < 0.008, indoor
    print("\n[self-test] ok: the two worlds print differently")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=os.path.join(_DATA, "packed_XC_TF.npz"))
    ap.add_argument("--diff", default=os.path.join(_DATA,
                                                   "pair_difficulty.npz"))
    ap.add_argument("--pool", default=None, help="one pool, e.g. hs_m")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return selfTest()

    cols, difficulty, solved, keys = loadRun(args.pack, args.diff)
    print("\n  Difficulty by academic-year bucket, both sports. A smooth "
          "decline through Dec-Feb = fitness is in the difficulties all "
          "year; a step at the boundary with flat plateaus = the level is "
          "the surface. Post-recentre numbers: XC sits above TF by the "
          "applied gap regardless.")
    run(cols, difficulty, solved, keys, args.pool)
    return 0


if __name__ == "__main__":
    sys.exit(main())
