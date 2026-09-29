#!/usr/bin/env python3
"""
scorecard.py -- one engine change, one number: two flag sets fitted on the
past and scored on the SAME validation season, the change with its SE.

    # today's production model, scored forward on the validation season
    python scripts/scorecard.py --base production

    # a switch against it, paired on identical held-out rows
    python scripts/scorecard.py --base production --try "production --season-tie"
    python scripts/scorecard.py --base "--altitude --era-years 2" \
        --try "--altitude --era-years 2 --tilt-scale hs" --pct 25

    # the SEALED test season -- once, when the choices are made
    python scripts/scorecard.py --base production --sealed

Run from the PROJECT ROOT. Each side is one run_joint --holdout-only
--holdout-kind forward run on --pct of the athletes (the ablation ladder's
runner, streamed to <out-dir>/ladder_logs/<side>.log with its heartbeat and
timeout); the comparison is scripts/switch_scorecard.py --holdout on the two
dumps. Writes the logs and dumps; nothing the site reads.

★ WHY (owner, 2026-09-29). The plan: estimate what the data can identify
  inside the joint solve, tune the few real hyperparameters and switches on
  ONE fixed validation scorecard, and look at a sealed test season once.
  This is the one command that asks the scorecard about a change.
  engine/forward_holdout.py says what is held out and how it is scored.

★ "production" IN A FLAG STRING is the model 08_golive fits under
  deploy/solve_env.sh's settings (and anything already in the environment,
  as sourcing it would leave it): productionFlags. Only the flags that
  change the JOINT fit are taken -- the bracket engine, the gauge and the
  publishing choices change what is written, not the solve scored here
  (scripts/bracket_holdout.py scores the bracket engine).

⚠ BOTH SIDES RUN ON THE SAME SAMPLE AND SEED, so the held-out rows match
  and the pairing is exact. A comparison across different --pct, --seed or
  windows is refused by the dumps' own records (switch_scorecard prints
  the mismatch).
"""
import argparse
import os
import re
import shlex
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SOLVE_ENV = os.path.join(_ROOT, "deploy", "solve_env.sh")

# ★ THE JOINT FIT'S FLAGS IN 08_golive (deploy/run_pipeline.sh), variable ->
#   (run_joint flag, takes a value). tests/test_scorecard.py reads 08_golive
#   and fails if a variable there is in neither this nor PUBLISH_ONLY, so a
#   new model flag cannot reach the go-live and miss the scorecard.
PRODUCTION_ENV = (
    ("XCP_SPORT_LEVEL", "--sport-level", True),
    ("XCP_IMPORTANCE", "--importance", True),
    ("XCP_NO_IMPORTANCE", "--no-importance", False),
    ("XCP_NO_INDOOR", "--no-indoor", False),
    ("XCP_INDOOR_LEVEL", "--indoor-level", True),
    ("XCP_ERA_YEARS", "--era-years", True),
    ("XCP_ERA_DRIFT", "--era-drift", True),
    ("XCP_DIST_WALK", "--dist-walk", True),
    ("XCP_NO_DIST_TABLE", "--no-dist-table", False),
    ("XCP_MERGE_SPORTS", "--merge-sports", False),
    ("XCP_CENTRE_CURVE", "--centre-curve", False),
    ("XCP_TAU_MAX", "--tau-max", True),
    ("XCP_SPLIT_ABILITY", "--split-ability", False),
    ("XCP_WINTER_GAIN", "--winter-gain", True),
    ("XCP_WINTER_GAIN_BANDS", "--winter-gain-bands", True),
    ("XCP_TILT_SCALE", "--tilt-scale", True),
)
# what 08_golive passes that does not change the joint fit: the bracket
# engine, the gauge, the publishing, the solve's warm start
PUBLISH_ONLY = {
    "XCP_DIFFICULTY", "XCP_BRACKET_PRIOR", "XCP_BRACKET_WINDOW",
    "XCP_TRACK_LEVEL_BY_POOL", "XCP_BRACKET_PLACE_RADIUS",
    "XCP_BRACKET_SIBLING_TOL", "XCP_GAUGE", "XCP_GAUGE_SCOPE",
    "XCP_BRACKET_INDOOR_GATES", "XCP_DAY_NOISE", "XCP_BRACKET_INDOOR_CENTRE",
    "XCP_BRACKET_INDOOR_MODE", "XCP_BRACKET_INDOOR_LEVELS",
    "XCP_BRACKET_XC_LEVEL", "XCP_BRACKET_XC_LEVEL_MODE",
    "XCP_BRACKET_PLACE_PRIOR", "XCP_BRACKET_PLACE_VENUE", "XCP_COURSE_SCALE",
    "XCP_RACE_EFFECT_SPORTS", "XCP_SPORT_LEVEL_POOLS", "XCP_FROM_STATE",
}


def solveEnvDefaults(path=SOLVE_ENV):
    """{variable: default} from solve_env.sh's `: "${VAR:=value}"` lines."""
    out = {}
    pat = re.compile(r'^\s*:\s*"\$\{(XCP_\w+):=([^}]*)\}"')
    with open(path) as f:
        for line in f:
            m = pat.match(line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def productionFlags(env=None, path=SOLVE_ENV):
    """The run_joint flags of the model 08_golive fits: solve_env.sh's
    defaults under whatever the environment already sets (sourcing it keeps
    a set value), mapped as 08_golive maps them."""
    e = dict(solveEnvDefaults(path))
    e.update({k: v for k, v in (os.environ if env is None else env).items()
              if k.startswith("XCP_")})
    flags = []
    for var, flag, takes in PRODUCTION_ENV:
        val = e.get(var, "")
        if val:
            flags += [flag, val] if takes else [flag]
    # $([ "${XCP_SEASON_TIE:-0}" = "1" ] && echo --season-tie)
    if e.get("XCP_SEASON_TIE", "0") == "1":
        flags.append("--season-tie")
    # $([ "${XCP_ALTITUDE:-1}" != "0" ] && echo --altitude)
    if (e.get("XCP_ALTITUDE") or "1") != "0":
        flags.append("--altitude")
    return flags


def expandFlags(text, env=None):
    """A flag string, with the word 'production' replaced by productionFlags."""
    out = []
    for tok in shlex.split(text or ""):
        out += productionFlags(env) if tok == "production" else [tok]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True,
                    help="run_joint flags of the baseline ('production' = "
                         "08_golive's model under deploy/solve_env.sh)")
    ap.add_argument("--try", dest="try_", default=None,
                    help="run_joint flags of the change; omit to score --base alone")
    ap.add_argument("--pct", type=float, default=15.0,
                    help="percent of ATHLETES (default 15, the ladder's)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--outer", type=int, default=5)
    ap.add_argument("--holdout-from", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--holdout-until", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--sealed", action="store_true",
                    help="score the SEALED test season. Once, deliberately")
    ap.add_argument("--boot", type=int, default=None,
                    help="race-bootstrap resamples (default "
                         "forward_holdout.bootReps(): the SE to 5%% of itself)")
    ap.add_argument("--rung-timeout", type=float, default=7200.0)
    ap.add_argument("--heartbeat", type=float, default=180.0)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out-dir", default=os.path.join(_ROOT, "engine", "data",
                                                      "scorecard"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import ablation_ladder as al
    import forward_holdout as fh
    import switch_scorecard as ss

    # the ladder's runner reads its settings off one namespace
    run = argparse.Namespace(kind="forward", pct=a.pct, seed=a.seed,
                             outer=a.outer, holdout_from=a.holdout_from,
                             holdout_until=a.holdout_until, sealed=a.sealed,
                             python=a.python, dry_run=a.dry_run,
                             out=os.path.join(a.out_dir, "scorecard.json"),
                             heartbeat=a.heartbeat, rung_timeout=a.rung_timeout)
    sides = [("base", expandFlags(a.base))]
    if a.try_ is not None:
        sides.append(("try", expandFlags(a.try_)))
    print(f"\nscorecard: forward split, {a.pct}% of athletes (seed {a.seed})"
          + (", THE SEALED SEASON" if a.sealed else ", the validation season"))
    for name, flags in sides:
        print(f"  {name:<5} {' '.join(flags) or '(run_joint defaults)'}")
    print()
    t0 = time.time()
    dumps = {}
    for name, flags in sides:
        rec = al.runRung(name, flags, run)
        if a.dry_run:
            continue
        if not rec or "error" in rec:
            sys.exit(f"scorecard: the {name} run failed: "
                     f"{(rec or {}).get('error', 'no record')}")
        path = os.path.join(a.out_dir, "ladder_logs", f"{name}_holdout.npz")
        with np.load(path, allow_pickle=False) as z:
            dumps[name] = {k: z[k] for k in z.files}
    if a.dry_run:
        return 0
    for name, d in dumps.items():
        print(f"\n{name.upper()} ({' '.join(dict(sides)[name])})")
        for line in fh.scoreLines(d, prefix="  "):
            print(line)
    if "try" in dumps:
        print("\nTRY AGAINST BASE, ROW FOR ROW")
        for line in ss.compareHoldout(dumps["base"], dumps["try"], n_rep=a.boot):
            print(line)
    print(f"\n  total {time.time() - t0:.0f}s; logs and dumps in "
          f"{os.path.join(a.out_dir, 'ladder_logs')}")
    print("  ⚠ This REPORTS. A human reads it and decides what the next run "
          "carries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
