# Project: xc-predictor / tests
# File:    test_scorecard.py
# Purpose: scripts/scorecard.py scores the model 08_golive fits, and every
#          ladder rung can be scored forward.
#
# ★ THE DRIFT THIS PINS. deploy/run_pipeline.sh's 08a holdout passes a
#   HAND-PICKED subset of the go-live's model flags (no --split-ability, no
#   --tau-max, no --winter-gain ...), so a run carrying one of them scores a
#   different model from the one it publishes. scorecard.py's 'production'
#   reads 08_golive's list instead, and this test fails when 08_golive gains
#   a variable scorecard.py has not classified.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_scorecard.py
import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

import argparse
import contextlib
import io
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scorecard as sc                                          # noqa: E402


def _golive_vars():
    src = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    block = src[src.index("step 08_golive"):src.index("step 08a_holdout")]
    return set(re.findall(r"\$\{(XCP_\w+):\+", block))


def test_every_golive_variable_is_classified():
    known = {v for v, _f, _t in sc.PRODUCTION_ENV} | sc.PUBLISH_ONLY
    missing = _golive_vars() - known
    assert not missing, (f"08_golive passes {sorted(missing)}: add each to "
                         "scorecard.PRODUCTION_ENV (it changes the joint fit) "
                         "or PUBLISH_ONLY (it does not)")


def test_production_is_solve_env_under_the_environment():
    flags = sc.productionFlags(env={})
    d = sc.solveEnvDefaults()
    assert d["XCP_ERA_YEARS"] == "2"
    assert flags[flags.index("--era-years") + 1] == "2"
    assert flags[flags.index("--sport-level") + 1] == d["XCP_SPORT_LEVEL"]
    assert "--altitude" in flags and "--season-tie" not in flags
    # a set variable wins, as sourcing solve_env.sh would leave it
    f2 = sc.productionFlags(env={"XCP_SEASON_TIE": "1", "XCP_ALTITUDE": "0",
                                 "XCP_ERA_YEARS": "3"})
    assert "--season-tie" in f2 and "--altitude" not in f2
    assert f2[f2.index("--era-years") + 1] == "3"
    assert sc.expandFlags("production --tilt-scale hs", env={})[-2:] == \
        ["--tilt-scale", "hs"]


def test_production_flags_parse():
    import run_joint as rj
    ap = rj.buildParser()
    with contextlib.redirect_stdout(io.StringIO()):
        args = rj.applyImplications(ap.parse_args(sc.productionFlags(env={})), ap)
    assert args.altitude and args.era_years == 2


def test_every_rung_can_run_forward():
    import ablation_ladder as al
    ns = argparse.Namespace(python="python", kind="forward", pct=15.0, seed=11,
                            outer=5, holdout_from=None, holdout_until=None,
                            sealed=True)
    cmd = al.rungCommand(["--altitude", "--season-tie"], ns)
    assert cmd[cmd.index("--holdout-kind") + 1] == "forward"
    assert "--sealed" in cmd and cmd[-2:] == ["--altitude", "--season-tie"]
    ns.kind, ns.sealed = "race", False
    cmd = al.rungCommand(["--altitude"], ns)
    assert "--sealed" not in cmd and cmd[cmd.index("--holdout-kind") + 1] == "race"
    # every rung's flags parse under the forward split
    import run_joint as rj
    ap = rj.buildParser()
    for _name, flags, _why in al.LADDER:
        a = ap.parse_args(["--holdout-kind", "forward"] + list(flags))
        assert a.holdout_kind == "forward"
