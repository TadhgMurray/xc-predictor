# Project: xc-predictor / tests
# File:    test_explain_joint_row.py
# Purpose: scripts/explain_joint_row.py rebuilds the design the go-live solve
#          fitted, with the solve's own settings, so a row lands in the SAME
#          (course, era) cell the solve gave it.
#
# ⚠ THE FAILURE THIS PINS (2026-09-29, on the server):
#     [explain] joint_difficulty.npz does not fit this pack (223097 cells vs
#     76010, 577430 races vs 577430)
#   The races matched, so the pack was the solve's. The explainer rebuilt the
#   design without the era split deploy/solve_env.sh runs under
#   (XCP_ERA_YEARS=2), so its 76,010 base courses faced 223,097 era cells.
#
#   python -m pytest -q tests/test_explain_joint_row.py
import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

import contextlib
import io
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_joint as rj                                         # noqa: E402
import explain_joint_row as ej                                 # noqa: E402
from test_era_publish import _era_pack                         # noqa: E402

# what deploy/solve_env.sh exports that shapes the design
_SOLVE_ENV = {"XCP_ERA_YEARS": "2", "XCP_SPORT_LEVEL": "0.0583",
              "XCP_IMPORTANCE": "none", "XCP_ALTITUDE": "0"}


def _pack():
    cols, _keep, keys, _s = _era_pack(n_ath=300)
    rng = np.random.default_rng(5)
    cols["doy"] = rng.integers(1, 365, cols["norm"].size)       # the curve's day
    return rj.sortRowsByAthlete(cols), keys


def _solve(cols, env):
    """run_joint.main's design and the design half of the file it writes,
    from the argv step 08_golive builds out of `env`. No solve: the arrays
    are sized as the solve's are, which is all the explainer indexes."""
    ap = rj.buildParser()
    with contextlib.redirect_stdout(io.StringIO()):
        args = rj.applyImplications(ap.parse_args(ej.goLiveArgv(env)), ap)
        keep = (cols["course"] >= 0) & (cols["norm"] > 0)
        D, _ap, pool_names = rj.buildDesign(cols, keep, **rj.designKwargs(args))
    npz = dict(delta=np.zeros(D.n_cell), race_effect=np.zeros(D.n_race),
               ability=np.zeros(D.n_ath, dtype=np.float32),
               rating=np.full(D.n_ath, 100.0, dtype=np.float32),
               pool_names=np.array(pool_names))
    if D.mu_fixed is not None:
        npz["mu_fixed"] = D.mu_fixed
    npz.update(rj.designRecord(args, cols, D))
    return D, npz


def _rebuild(cols, npz, env):
    with contextlib.redirect_stdout(io.StringIO()):
        return ej.rebuildDesign(cols, npz, env=env)


def _same(D, E):
    assert list(E.course_keys) == list(D.course_keys)
    assert np.array_equal(E.cell, D.cell)
    assert np.array_equal(E.race, D.race)
    assert np.array_equal(E.athlete, D.athlete)


def test_the_go_live_argv_is_the_pipelines():
    argv = ej.goLiveArgv({"XCP_ERA_YEARS": "2", "XCP_SPORT_LEVEL": "0.0583",
                          "XCP_NO_INDOOR": "", "XCP_DIFFICULTY": "bracket"})
    # ${X:+...}: an empty variable passes nothing; unset XCP_ALTITUDE is on
    assert argv == ["--sport-level", "0.0583", "--era-years", "2", "--altitude"]
    assert "--altitude" not in ej.goLiveArgv({"XCP_ALTITUDE": "0"})


def test_the_rebuild_maps_every_row_to_the_solves_era_cell():
    cols, keys = _pack()
    D, npz = _solve(cols, _SOLVE_ENV)
    assert D.n_cell > len(keys)                          # the era split is on
    E, keep, _pn, settings, _notes = _rebuild(cols, npz, _SOLVE_ENV)
    _same(D, E)
    assert ej.designMismatch(E, npz, settings) is None
    got = {k: v for k, v, _s in settings}
    assert got["era_years"] == 2 and got["sport_level"] == 0.0583
    # and the cell IS the row's (course, era): era = (year - base) // 2
    base_year = int(npz["era_base_year"][0])
    course = np.asarray(cols["course"])[keep]
    year = np.asarray(cols["year"])[keep]
    for j in range(0, E.n, 97):
        assert E.course_keys[E.cell[j]] == (
            f"{keys[course[j]]}@e{(year[j] - base_year) // 2}")


def test_the_file_decides_when_the_environment_was_not_sourced():
    """The owner runs the explainer without `. deploy/solve_env.sh`: the file
    records the era width, the level and the rest, so it still fits."""
    cols, _keys = _pack()
    D, npz = _solve(cols, _SOLVE_ENV)
    E, _k, _pn, settings, notes = _rebuild(cols, npz, {})
    _same(D, E)
    assert ej.designMismatch(E, npz, settings) is None
    assert any(n.startswith("era_years") for n in notes)    # said, not silent
    assert dict((k, s) for k, _v, s in settings)["era_years"] == "npz"


def test_an_old_file_names_the_era_split_and_takes_the_width_from_the_env():
    """A file from before designRecord: '@e' keys, era_years saved (run_joint
    has written it since the era split), but no split_ability. Strip the
    width too and nothing says how wide an era is -- the message must say
    it is the era split, not 'the pack was rebuilt'."""
    cols, _keys = _pack()
    D, npz = _solve(cols, _SOLVE_ENV)
    old = {k: v for k, v in npz.items()
           if k not in ("era_years", "era_base_year", "split_ability",
                        "sport_offset")}
    E, _k, _pn, settings, notes = _rebuild(cols, old, {})
    msg = ej.designMismatch(E, old, settings, notes)
    assert msg is not None
    assert "eras" in msg and "XCP_ERA_YEARS" in msg
    assert "rebuilt after the solve" not in msg
    # sourced, it fits; split_ability comes from the ability count
    E2, _k, _pn, settings2, _n = _rebuild(cols, old, _SOLVE_ENV)
    _same(D, E2)
    assert ej.designMismatch(E2, old, settings2) is None
    assert dict((k, s) for k, _v, s in settings2)["split_ability"] == "npz"


def test_a_split_ability_solve_is_rebuilt_split():
    cols, _keys = _pack()
    env = dict(_SOLVE_ENV, XCP_SPLIT_ABILITY="1")
    D, npz = _solve(cols, env)
    E, _k, _pn, settings, _notes = _rebuild(cols, npz, _SOLVE_ENV)   # flag not sourced
    _same(D, E)
    assert ej.designMismatch(E, npz, settings) is None
    # and a file whose ability count fits neither says which setting
    bad = dict(npz, ability=np.zeros(D.n_ath + 7, dtype=np.float32))
    bad.pop("split_ability")
    E3, _k, _pn, s3, n3 = _rebuild(cols, bad, _SOLVE_ENV)
    assert "split_ability" in ej.designMismatch(E3, bad, s3, n3)


def test_the_base_design_is_refused_with_the_reason():
    """The 2026-09-29 failure, reproduced: the base-course design against
    the era file is refused, and the reason is the era split."""
    cols, _keys = _pack()
    _D, npz = _solve(cols, _SOLVE_ENV)
    with contextlib.redirect_stdout(io.StringIO()):
        keep = (cols["course"] >= 0) & (cols["norm"] > 0)
        B, _ap, _pn = rj.buildDesign(cols, keep, altitude=False)   # the old rebuild
    msg = ej.designMismatch(B, npz, [("era_years", 0, "env")])
    assert msg and "the solve split courses into eras" in msg
