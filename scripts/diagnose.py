#!/usr/bin/env python3
"""
diagnose.py -- every pack-and-solve-file diagnostic in ONE process, timed.

    scripts/diagnose.py --era-years 2 --venue "Foot Locker" --venue Glendoveer
    scripts/diagnose.py --era-years 2 --key "XC:12345:" --out-dir logs
    scripts/diagnose.py --era-years 2 --only indoor,tracks,holdout

Loads the pack (the nine columns the diagnostics read) and the solve file
once, numbers the athlete-seasons, the races and the solve file's cells
once over the whole pack (engine/bracket.packCodes), and runs on that:

  venues    the same-athlete bracket per race day at the named venues,
            against the board (scripts/course_bracket.py)
  indoor    indoor against outdoor for the same athlete-season at the
            same distance (scripts/indoor_outdoor_check.py)
  tracks    why outdoor tracks differ: board, bracket, the meets each
            hosts (scripts/track_variance.py)
  holdout   the bracket engine scored on the ladder's held-out races
            (scripts/bracket_holdout.py)

Each report goes to <out-dir>/<stage>.txt (--out-dir "" prints them);
stdout gets the progress and a timing table. A stage that fails writes
its traceback to its file and the rest still run.

★ WHY ONE PROCESS (2026-09-12: "check that they will take 5 mins max, all
  of them together"). Four scripts each loaded the corpus and numbered
  its races with np.unique(axis=0): ninety seconds a script before any
  diagnostic began. The load, the codes and the ratings-per-row are
  shared here; each stage then works on its own rows (a venue's
  athlete-seasons, the seasons with an indoor row, a quarter of the
  athletes' track rows, the ladder's 15% sample). The timing table at the
  end is the check; `tests/test_diagnose.py` runs the whole thing on a
  two-million-row pack against a clock.
"""
import argparse
import contextlib
import os
import sys
import time
import traceback

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                            # noqa: E402
import run_joint as rj                                          # noqa: E402
import bracket_holdout as bh                                    # noqa: E402
import course_bracket as cb                                     # noqa: E402
import indoor_outdoor_check as ioc                              # noqa: E402
import track_variance as tv                                     # noqa: E402

STAGES = ("venues", "indoor", "tracks", "holdout")
BUDGET_S = 300.0


def _stageFile(out_dir, stage):
    names = {"venues": "bracket_venues.txt", "indoor": "indoor.txt",
             "tracks": "tracks.txt", "holdout": "bracket_holdout.txt"}
    return os.path.join(out_dir, names[stage]) if out_dir else None


def run(cols, npz, era_years=0, match=(), only=None, out_dir="logs", window=21.0,
        top=0.25, indoor_windows=(21, 35, 49), dists=(800, 1600, 3200, 5000),
        indoor_sample=30.0, track_sample=25.0, track_min_rows=100, pct=15.0,
        seed=11, holdout_top=0.5, iters=30, use_curve=True, names=None,
        timings=None, log=print):
    """Runs the stages in `only` (all four by default) and returns
    {stage: seconds} with 'codes' and 'total' added; `timings` (a dict)
    may carry the caller's load time, which the table then includes."""
    stages = [s for s in STAGES if only is None or s in only]
    times = dict(timings or {})
    files = {}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    t = time.time()
    try:
        cols, codes = bk.packCodes(cols, npz, era_years)
    except ValueError as exc:
        raise SystemExit(str(exc))
    times["codes"] = time.time() - t
    log(f"[diagnose] codes: {np.asarray(cols['norm']).size:,} rows, "
        f"{codes['n_season']:,} athlete-seasons, {codes['n_race']:,} races, "
        f"{codes['n_cell']:,} cells  [{times['codes']:.1f}s]")

    def stage(name, fn):
        path = _stageFile(out_dir, name)
        t = time.time()
        log(f"[diagnose] {name} ...")
        if path:
            with open(path, "w") as fh, contextlib.redirect_stdout(fh):
                ok = _guarded(fn)
        else:
            ok = _guarded(fn)
        times[name] = time.time() - t
        files[name] = path
        log(f"[diagnose] {name} {'done' if ok else 'FAILED'} in {times[name]:.1f}s"
            + (f"  -> {path}" if path else ""))

    if "venues" in stages:
        def venues():
            if not match:
                print("no --key and no --venue given; nothing to bracket")
                return
            res = cb.bracket(cols, npz, list(match), window=window, top=top,
                             era_years=era_years, use_curve=use_curve, codes=codes)
            if not res:
                print(f"no cell key matched {list(match)}")
                return
            cb.report(res, names, top=top)
        stage("venues", venues)
    if "indoor" in stages:
        def indoor():
            res, pools = ioc.measure(cols, npz, indoor_windows, dists, use_curve=use_curve,
                                     sample_pct=indoor_sample, seed=seed, codes=codes)
            ioc.report(res, pools, indoor_windows, dists)
        stage("indoor", indoor)
    if "tracks" in stages:
        def tracks():
            r = tv.analyse(cols, npz, era_years=era_years, min_rows=track_min_rows,
                           window=window, use_curve=use_curve, sample_pct=track_sample,
                           seed=seed, codes=codes)
            tv.report(r)
        stage("tracks", tracks)
    if "holdout" in stages:
        def holdout():
            bh.score(cols, npz, codes=codes, pct=pct, seed=seed, era_years=era_years,
                     window=window, top=holdout_top, iters=iters, use_curve=use_curve)
        stage("holdout", holdout)
    times["total"] = time.time() - t0 + float(times.get("load", 0.0))
    log("\n[diagnose] stage        seconds   output")
    for k in ["load", "codes"] + stages + ["total"]:
        if k not in times:
            continue
        extra = files.get(k) or ""
        if k == "total":
            extra = f"(budget {BUDGET_S:.0f})"
        log(f"[diagnose] {k:<10} {times[k]:10.1f}   {extra}")
    if times["total"] > BUDGET_S:
        log(f"[diagnose] OVER BUDGET by {times['total'] - BUDGET_S:.0f}s")
    return times


def _guarded(fn):
    try:
        fn()
        return True
    except SystemExit as exc:
        print(f"stopped: {exc}")
        return False
    except Exception:                                            # noqa: BLE001
        traceback.print_exc(file=sys.stdout)
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    d = rj.buildParser()
    ap.add_argument("--pack", default=d.get_default("pack"))
    ap.add_argument("--npz", default=d.get_default("out"))
    ap.add_argument("--era-years", type=int, default=0,
                    help="the era width the solve used (XCP_ERA_YEARS)")
    ap.add_argument("--only", default=None,
                    help="comma list of stages: " + ",".join(STAGES))
    ap.add_argument("--out-dir", default="logs",
                    help='reports go to <out-dir>/<stage>.txt; "" prints them')
    ap.add_argument("--key", action="append", default=[],
                    help="venues: substring of a cell key; repeatable")
    ap.add_argument("--venue", action="append", default=[],
                    help="venues: part of a canonical course name (needs the "
                         "database); repeatable")
    ap.add_argument("--window", type=float, default=21.0,
                    help="days either side, venues, tracks and holdout")
    ap.add_argument("--top", type=float, default=0.25,
                    help="venues: also the top fraction of each field")
    ap.add_argument("--windows", default="21,35,49", help="indoor: windows")
    ap.add_argument("--dists", default="800,1600,3200,5000", help="indoor: distances")
    ap.add_argument("--indoor-sample", type=float, default=30.0)
    ap.add_argument("--track-sample", type=float, default=25.0)
    ap.add_argument("--track-min-rows", type=int, default=100)
    ap.add_argument("--pct", type=float, default=15.0, help="holdout: athlete sample")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--holdout-top", type=float, default=0.5)
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--no-curve", action="store_true")
    args = ap.parse_args()
    only = None if not args.only else [s.strip() for s in args.only.split(",")]
    if only and any(s not in STAGES for s in only):
        ap.error(f"--only takes stages from {STAGES}")
    names = None
    match = list(args.key)
    if args.venue:
        try:
            from speed_ratings_db import loadCanonicalNames
            names = loadCanonicalNames()
        except Exception as exc:                                 # noqa: BLE001
            print(f"[diagnose] --venue needs the database for the name list; "
                  f"skipping names: {type(exc).__name__}: {exc}")
        for text in args.venue:
            hits = [cid for cid, nm in (names or {}).items()
                    if text.lower() in str(nm).lower()]
            if not hits:
                print(f"[diagnose] --venue {text!r}: no canonical course name contains "
                      f"it; skipped")
                continue
            for cid in hits:
                print(f"[diagnose] --venue {text!r}: {names[cid]}  ->  XC:{cid}:")
                match.append(f"XC:{cid}:")
    t = time.time()
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        sys.exit(f"no solve file at {args.npz}")
    load_s = time.time() - t
    print(f"[diagnose] loaded {np.asarray(cols['norm']).size:,} rows and the solve "
          f"file in {load_s:.1f}s", flush=True)
    run(cols, npz, era_years=args.era_years, match=match, only=only,
        out_dir=args.out_dir, window=args.window, top=args.top,
        indoor_windows=tuple(int(x) for x in args.windows.split(",")),
        dists=tuple(int(x) for x in args.dists.split(",")),
        indoor_sample=args.indoor_sample, track_sample=args.track_sample,
        track_min_rows=args.track_min_rows, pct=args.pct, seed=args.seed,
        holdout_top=args.holdout_top, iters=args.iters, use_curve=not args.no_curve,
        names=names, timings={"load": load_s})


if __name__ == "__main__":
    main()
