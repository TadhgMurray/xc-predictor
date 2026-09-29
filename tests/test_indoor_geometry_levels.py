# Project: xc-predictor / tests
# File:    test_indoor_geometry_levels.py
# Purpose: indoor difficulty centred per track GEOMETRY (owner, 2026-09-29:
#          "indoor difficulty is too easy generally"). The levels are
#          measured by scripts/indoor_outdoor_check.py --by geometry
#          --write-levels, and bracket_engine's indoor_mode="geometry" pins
#          each geometry class's vote-weighted mean on its own level.
#
#   python -m pytest -q tests/test_indoor_geometry_levels.py
import contextlib
import io
import json
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                    # noqa: E402

# the planted world: two ovals per geometry class, each class at its own
# level (log-time, + = slower than a flat outdoor 400), roughly the owner's
# live measurement of 2026-09-29
PLANTED = {"banked 200m": -0.006, "flat 200m": 0.002, "under 200m": 0.009,
           "300m+": -0.005, "unknown": 0.006}
_OVALS = (  # (length, type, class)
    (200.0, "Banked", "banked 200m"), (200.0, "Banked", "banked 200m"),
    (200.0, "Flat", "flat 200m"), (200.0, "Flat", "flat 200m"),
    (160.0, "Flat", "under 200m"), (170.0, "", "under 200m"),
    (300.0, "Flat", "300m+"), (307.0, "Oversized Flat", "300m+"),
    (np.nan, "", "unknown"), (np.nan, "", "unknown"))
N_OVAL = len(_OVALS)
N_TRACK = 20
FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "indoor_pin_before_geometry.npz")


def _geometryPack(seed=5, n_ath=3000, spread=0.001):
    """One track season per athlete: three indoor races on random ovals
    (day-of-year 30-70) and five outdoor races (60-110) drawn from 12 races
    on each of 20 flat outdoor 400s, all 1600 m. Every oval carries its
    class's planted level plus a small spread of its own; outdoor tracks
    carry a small spread around zero."""
    rng = np.random.default_rng(seed)
    a_true = rng.normal(0, 0.12, n_ath)
    oval_level = np.array([PLANTED[c] for _l, _t, c in _OVALS]) + rng.normal(0, spread, N_OVAL)
    track_level = rng.normal(0, 0.002, N_TRACK)
    race_track = np.repeat(np.arange(N_TRACK), 12)
    race_doy = 60 + rng.integers(0, 50, race_track.size)
    rows = []
    for i in range(n_ath):
        for k in range(3):
            rows.append((i, int(rng.integers(0, N_OVAL)), 30 + 13 * k + int(rng.integers(0, 5))))
        for j in rng.choice(race_track.size, 5, replace=False):
            rows.append((i, N_OVAL + int(race_track[j]), int(race_doy[j])))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    doy = np.array([r[2] for r in rows])
    n = ath.size
    level = np.r_[oval_level, track_level]
    y = a_true[ath] + level[course] + rng.normal(0, 0.015, n)
    keys = ([f"TF:loc:{i}:in" for i in range(N_OVAL)]
            + [f"TF:loc:{i}:out" for i in range(N_OVAL, N_OVAL + N_TRACK)])
    length = np.r_[[o[0] for o in _OVALS], np.full(N_TRACK, 400.0)]
    ttype = np.array([o[1] for o in _OVALS] + ["Flat"] * N_TRACK, dtype="U32")
    indoor = np.r_[np.ones(N_OVAL), np.zeros(N_TRACK)]
    cols = {"athlete": ath, "year": np.full(n, 2025), "course": course,
            "days": (366 - doy).astype(np.float64), "doy": doy,
            "sport": np.ones(n, dtype=np.int64), "norm": np.exp(y),
            "dist_m": np.full(n, 1600.0), "meet_class": np.zeros(n, dtype=np.int64),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys, "track_length": length, "track_type": ttype,
            "track_indoor": indoor}
    return cols, level


# the settings every fit here shares. h_row pins the tilt at 1.0 so these
# numbers do not depend on how the tilt is computed.
def _fit(cols, **kw):
    n = np.asarray(cols["course"]).size
    base = dict(window=60, top=1.0, era_years=0, tilt=False, h_row=np.ones(n),
                verbose=False)
    base.update(kw)
    with contextlib.redirect_stdout(io.StringIO()):
        return be.fit(cols, None, **base)


# the three pin-mode configurations the fixture was taken from
PIN_CONFIGS = {
    "flat400_pin_clamp": dict(gauge="flat400"),
    "outdoor_pin_report": dict(gauge="outdoor", indoor_gate_mode="report"),
    "flat400_shrink_clamp": dict(gauge="flat400", indoor_mode="shrink"),
}
PIN_FIELDS = ("D", "D_fit", "level_shift", "pin", "D_base", "votes", "group_mean")


def writeFixture(path=FIXTURE):
    cols, _level = _geometryPack()
    out = {}
    for name, kw in PIN_CONFIGS.items():
        f = _fit(cols, **kw)
        for fld in PIN_FIELDS:
            out[f"{name}__{fld}"] = np.asarray(f[fld], dtype=np.float64)
    np.savez(path, **out)


def _levels(shift=None):
    """A levels document at the planted levels, each class moved by
    shift[class]; n_rows differ by class so a weighted mean is not a plain one."""
    shift = shift or {}
    return {"measured": "2026-09-29", "classes": {
        c: {"level": v + shift.get(c, 0.0), "n_rows": 1000 * (i + 1), "n_pairs": 100}
        for i, (c, v) in enumerate(PLANTED.items())}}


def _classMeans(f, values):
    """Per geometry class, the vote-weighted mean of `values` over its
    indoor cells with votes."""
    import track_geometry as tg
    cls, w = np.asarray(f["indoor_class"]), np.asarray(f["votes"])
    out = {}
    for i, name in enumerate(tg.GEO_CLASSES):
        m = (cls == i) & (w > 0)
        if m.any():
            out[name] = float(np.average(values[m], weights=w[m]))
    return out


# ===================================================================== #
#  THE PER-CLASS PIN                                                    #
# ===================================================================== #

def test_the_per_class_pin_lands_each_class_on_its_level():
    """★ One additive shift per class: each class's vote-weighted mean IS its
    level. The levels are set away from the planted ones, so the pin has to
    move every class and cannot pass by the data happening to agree."""
    cols, _level = _geometryPack()
    doc = _levels({"banked 200m": 0.002, "flat 200m": -0.001,
                   "under 200m": 0.003, "300m+": 0.001})
    f = _fit(cols, gauge="flat400", indoor_mode="geometry", indoor_levels=doc)
    want = {c: doc["classes"][c]["level"] for c in PLANTED}
    # D_fit is the engine's arithmetic at the fixed point: the pin exactly,
    # and nothing clamped in this world (checked below)
    got = _classMeans(f, np.asarray(f["D_fit"]))
    assert set(got) == set(PLANTED), got
    for c, v in got.items():
        assert abs(v - want[c]) < 1e-12, (c, v, want[c])
    rep = f["indoor_gate_report"]
    assert rep["mode"] == "geometry" and rep["n_outside"] == 0, rep
    for r in rep["classes"]:
        assert abs(r["pinned"] - want[r["cls"]]) < 1e-12, r
        # the published iterate is the damped fixed point, within tolerance
        assert abs(r["mean"] - want[r["cls"]]) < 1e-4, r
    # ! THE SPREAD INSIDE A CLASS IS STILL THE RACES': the two banked ovals
    #   are planted a little apart and keep reading apart
    D = np.asarray(f["D"])
    keys = [str(k) for k in f["cell_keys"]]
    assert D[keys.index("TF:loc:0:in")] != D[keys.index("TF:loc:1:in")]


def test_a_banked_oval_can_sit_below_the_old_floor_gate():
    """★ The error the mode exists to fix: under "pin" the -0.3% floor gate
    holds a banked 200 at -0.3% whatever its athletes say; under "geometry"
    its class's gates sit around its own measured level."""
    cols, _level = _geometryPack()
    f_pin = _fit(cols, gauge="flat400")
    f_geo = _fit(cols, gauge="flat400", indoor_mode="geometry", indoor_levels=_levels())
    keys = [str(k) for k in f_geo["cell_keys"]]
    banked = [keys.index(f"TF:loc:{i}:in") for i in (0, 1)]
    floor = be.INDOOR_GATES[0]
    assert (np.asarray(f_pin["D"])[banked] > floor - 1e-5).all(), np.asarray(f_pin["D"])[banked]
    assert (np.asarray(f_geo["D"])[banked] < floor - 0.002).all(), np.asarray(f_geo["D"])[banked]
    # the class's gates: the global gates' offsets from the centre, around
    # the class level -- the same width, a new middle
    r = {x["cls"]: x for x in f_geo["indoor_gate_report"]["classes"]}["banked 200m"]
    lo, hi = r["gates"]
    assert abs((hi - lo) - (be.INDOOR_GATES[1] - be.INDOOR_GATES[0])) < 1e-12
    assert abs(lo - (PLANTED["banked 200m"] + be.INDOOR_GATES[0] - be.INDOOR_CENTRE)) < 1e-12


def test_the_class_gates_clamp_around_the_class_level():
    """One oval far from its classmate: the pin puts the class mean on the
    level, the clamp then holds each oval inside THAT class's gates (not the
    global ones), and the report counts it."""
    import track_geometry as tg
    cols, _level = _geometryPack()
    # oval 4 run 3% slower than its under-200 classmate: a within-class
    # spread wider than the 2.3% gate window, so one of them must clamp
    cols = dict(cols)
    course = np.asarray(cols["course"])
    norm = np.asarray(cols["norm"], dtype=np.float64).copy()
    norm[course == 4] *= np.exp(0.03)                    # one small oval 3% slower
    cols["norm"] = norm
    f = _fit(cols, gauge="flat400", indoor_mode="geometry", indoor_levels=_levels())
    r = {x["cls"]: x for x in f["indoor_gate_report"]["classes"]}["under 200m"]
    lo, hi = r["gates"]
    assert r["n_clamped"] >= 1, r
    D_fit = np.asarray(f["D_fit"])
    m = np.asarray(f["indoor_class"]) == tg.GEO_CLASSES.index("under 200m")
    assert ((D_fit[m] >= lo - 1e-12) & (D_fit[m] <= hi + 1e-12)).all(), (D_fit[m], lo, hi)


def test_the_shrinkage_target_is_the_class_level_per_course():
    """A course with nothing of its own to say rests on its CLASS's level,
    not the indoor group's centre: with an overwhelming group prior every
    oval's history (D_base) is its class's target."""
    cols, _level = _geometryPack()
    f = _fit(cols, gauge="flat400", indoor_mode="geometry", indoor_levels=_levels(),
             prior_group=1e6)
    base = np.asarray(f["D_base"])
    b_of = np.asarray(f["base_of_cell"])
    keys = [str(k) for k in f["cell_keys"]]
    for i, (_l, _t, c) in enumerate(_OVALS):
        assert abs(base[b_of[keys.index(f"TF:loc:{i}:in")]] - PLANTED[c]) < 1e-4, (i, c)


def test_the_trace_still_adds_up_under_geometry(capsys):
    """tests/test_bracket_golive.py's identity -- rebuilt - pin + level ==
    fit -- through run_joint.bracketDifficulties with the geometry mode on."""
    import run_joint as rj
    from test_bracket_golive import _solve
    from test_era_publish import _era_pack
    cols, keep, keys, _s = _era_pack()
    n_key = len(keys)
    length = np.full(n_key, 400.0)
    ttype = np.array(["Flat"] * n_key, dtype="U16")
    ind = np.array([k.endswith(":in") for k in keys], dtype=float)
    i_in = [i for i, k in enumerate(keys) if k.endswith(":in")]
    length[i_in[0]], ttype[i_in[0]] = 200.0, "Banked"
    length[i_in[1]] = 200.0
    cols = dict(cols, track_length=length, track_type=ttype, track_indoor=ind)
    D, athlete_pool, pool_names, y, out = _solve(cols, keep)
    rj.bracketDifficulties(out, D, cols, keep, y, athlete_pool, pool_names,
                           window=60, top=1.0, indoor_mode="geometry",
                           indoor_levels=_levels())
    assert "mode=geometry" in capsys.readouterr().out
    votes = out["bracket_votes"]
    raw, base = out["bracket_cell_raw"], out["bracket_base"]
    pin, fit = out["bracket_pin"], out["bracket_cell_fit"]
    level = out["bracket_level_shift"]
    m = votes > 0
    rebuilt = (raw[m] * votes[m] + be.PRIOR_RACES * base[m]) / (votes[m] + be.PRIOR_RACES)
    assert np.allclose(rebuilt - pin[m] + level[m], fit[m], atol=1e-12)
    # and the level shift is what moved the indoor cells
    is_in = np.array([str(k).split("@", 1)[0].endswith(":in") for k in D.course_keys])
    assert np.abs(level[m & is_in]).max() > 0


# ===================================================================== #
#  "pin" IS UNTOUCHED                                                    #
# ===================================================================== #

def test_pin_mode_is_byte_identical_to_before_the_geometry_mode():
    """★ The fixture was written by writeFixture() on the engine as it stood
    before indoor_mode="geometry" existed (on top of d73646b). Every array,
    every bit. If an INTENDED engine change breaks this, regenerate it with
    `python tests/test_indoor_geometry_levels.py --write-fixture` in the same
    commit and say why in the message."""
    cols, _level = _geometryPack()
    fx = np.load(FIXTURE)
    for name, kw in PIN_CONFIGS.items():
        f = _fit(cols, **kw)
        for fld in PIN_FIELDS:
            want = fx[f"{name}__{fld}"]
            got = np.asarray(f[fld], dtype=np.float64)
            assert np.array_equal(want, got), (name, fld, np.abs(want - got).max())


# ===================================================================== #
#  REFUSING LOUDLY                                                       #
# ===================================================================== #

def test_a_missing_levels_file_raises(tmp_path):
    cols, _level = _geometryPack()
    with pytest.raises(ValueError, match="write-levels"):
        _fit(cols, indoor_mode="geometry",
             indoor_levels=str(tmp_path / "no_such_levels.json"))


def test_a_levels_file_missing_a_class_raises():
    cols, _level = _geometryPack()
    doc = _levels()
    del doc["classes"]["unknown"]
    with pytest.raises(ValueError, match="unknown"):
        _fit(cols, indoor_mode="geometry", indoor_levels=doc)


def test_a_pack_without_geometry_raises():
    cols, _level = _geometryPack()
    cols = {k: v for k, v in cols.items() if k not in ("track_length", "track_type")}
    with pytest.raises(ValueError, match="no track geometry"):
        _fit(cols, indoor_mode="geometry", indoor_levels=_levels())


def test_run_joint_refuses_before_the_solve(tmp_path):
    """A missing file stops run_joint at argument time, not hours later in
    the bracket swap (which catches its own exceptions and publishes the
    joint's courses)."""
    import run_joint as rj
    ap = rj.buildParser()
    argv = ["--difficulty", "bracket", "--bracket-indoor-mode", "geometry",
            "--bracket-indoor-levels", str(tmp_path / "missing.json")]
    with pytest.raises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
        rj.applyImplications(ap.parse_args(argv), ap)


# ===================================================================== #
#  ONE INDOOR LEVEL FOR BOTH ENGINES                                     #
# ===================================================================== #

def test_the_joint_level_is_the_row_weighted_mean_of_the_classes(tmp_path):
    import run_joint as rj
    import track_geometry as tg
    doc = _levels()
    want = (sum(doc["classes"][c]["level"] * doc["classes"][c]["n_rows"] for c in PLANTED)
            / sum(doc["classes"][c]["n_rows"] for c in PLANTED))
    assert abs(tg.jointIndoorLevel(doc)[0] - want) < 1e-15
    path = tmp_path / "levels.json"
    path.write_text(json.dumps(doc))
    ap = rj.buildParser()
    with contextlib.redirect_stdout(io.StringIO()):
        args = rj.applyImplications(ap.parse_args(
            ["--difficulty", "bracket", "--bracket-indoor-mode", "geometry",
             "--bracket-indoor-levels", str(path), "--indoor-level", "0.003"]), ap)
    assert abs(args.indoor_level - want) < 1e-15
    # pin leaves the asserted number alone
    args = rj.applyImplications(ap.parse_args(
        ["--difficulty", "bracket", "--indoor-level", "0.003"]), ap)
    assert args.indoor_level == 0.003


# ===================================================================== #
#  THE MEASUREMENT AND THE FILE                                          #
# ===================================================================== #

def test_one_definition_of_the_classes():
    import indoor_outdoor_check as ioc
    import track_geometry as tg
    assert ioc.geometryClass is tg.geometryClass and ioc.GEO_CLASSES is tg.GEO_CLASSES
    for length, ttype, cls in _OVALS:
        assert tg.geometryClass(length, ttype) == cls
    assert tg.geometryClass(0.0, "Flat") == "unknown"
    assert tg.geometryClass(None, "Banked") == "unknown"


def _res(estimates_by_class, n=5000):
    """A measure()-shaped result whose curve medians and zero-day readings
    are the given estimates: three windows x two designs from the curve,
    then the two zero-day readings, planted as the intercepts of straight
    lines through the raw medians against the gap."""
    res = {}
    for g, est in estimates_by_class.items():
        curve, zero = est[:6], est[6:]
        for j, W in enumerate((21, 35, 49)):
            res[("curve", g, W, "all")] = {
                "pairs": (n, curve[2 * j], np.nan),
                "transition": (n, curve[2 * j + 1], np.nan), "gap": (np.nan, np.nan)}
            res[("raw", g, W, "all")] = {
                "pairs": (n, zero[0] + 0.0001 * W, np.nan),
                "transition": (n, zero[1] + 0.0002 * W, np.nan), "gap": (W, W)}
    return res


def test_the_writer_takes_the_median_of_the_estimates(tmp_path):
    import indoor_outdoor_check as ioc
    import track_geometry as tg
    # the owner's live table of 2026-09-29, as fractions: curve medians
    # (pairs, transition) at 21/35/49 days, then zero-day (pairs, transition)
    est = {
        "under 200m": [0.0088, 0.0092, 0.0099, 0.0094, 0.0104, 0.0090, 0.0068, 0.0075],
        "flat 200m": [0.0013, 0.0022, 0.0026, 0.0022, 0.0032, 0.0019, -0.0003, 0.0032],
        "banked 200m": [-0.0056, -0.0040, -0.0064, -0.0067, -0.0071, -0.0078, -0.0064, -0.0024],
        "300m+": [-0.0041, -0.0072, -0.0046, -0.0069, -0.0052, -0.0069, -0.0036, -0.0056],
        "unknown": [0.0028, 0.0101, 0.0048, 0.0090, 0.0055, 0.0080, -0.0005, 0.0122],
    }
    lv = ioc.classLevels(_res(est), list(ioc.GEO_CLASSES))
    path = str(tmp_path / "levels.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ioc.writeLevels(lv, path, "pack.npz", n_rows={c: 10 for c in est})
    assert "written to" in buf.getvalue()
    with open(path) as fh:
        on_disk = json.load(fh)
    for c, e in est.items():
        # the zero-day readings are recovered from the raw lines, not copied
        assert np.allclose(sorted(on_disk["classes"][c]["estimates"]), sorted(e), atol=1e-12)
        assert on_disk["classes"][c]["trusted"], (c, on_disk["classes"][c])
        assert abs(on_disk["classes"][c]["level"] - float(np.median(e))) < 1e-12
    assert on_disk["pack"]["path"] == "pack.npz" and on_disk["measured"]
    # and the engine reads what was written
    assert tg.classLevels(tg.loadIndoorLevels(path)) == [
        on_disk["classes"][c]["level"] for c in tg.GEO_CLASSES]


def test_a_class_its_own_spread_cannot_separate_falls_back_to_the_pool():
    """★ The trust rule: a class whose estimates spread wider (IQR) than it
    sits from the other classes takes the pairs-weighted level of all."""
    import indoor_outdoor_check as ioc
    tight = lambda c: [c + d for d in (-2e-4, -1e-4, 0, 0, 1e-4, 2e-4, 0, 0)]
    est = {"under 200m": tight(0.009), "flat 200m": tight(0.002),
           "banked 200m": tight(-0.006), "300m+": tight(-0.005),
           # a wide class, sitting near the others' pool
           "unknown": [-0.02, 0.02, -0.015, 0.015, -0.01, 0.01, 0.0, 0.001]}
    lv = ioc.classLevels(_res(est), list(ioc.GEO_CLASSES))
    assert not lv["unknown"]["trusted"]
    pool = np.mean([lv[c]["own_level"] for c in est])     # equal n_pairs here
    assert abs(lv["unknown"]["level"] - pool) < 1e-12
    for c in ("under 200m", "flat 200m", "banked 200m", "300m+"):
        assert lv[c]["trusted"] and lv[c]["level"] == lv[c]["own_level"]


def test_the_levels_need_the_form_curve():
    import indoor_outdoor_check as ioc
    res = {k: v for k, v in _res({"flat 200m": [0.0] * 8}).items() if k[0] == "raw"}
    with pytest.raises(SystemExit):
        ioc.classLevels(res, list(ioc.GEO_CLASSES))


def test_the_measurement_reads_the_planted_class_levels():
    """End to end on the synthetic world: measure(by="geometry"), then
    classLevels. The world has no fitness trend, so the raw medians stand in
    for the curve variant."""
    import indoor_outdoor_check as ioc
    cols, _level = _geometryPack(n_ath=4000)
    with contextlib.redirect_stdout(io.StringIO()):
        res, groups = ioc.measure(cols, None, windows=(21, 35, 49), dists=(1600,),
                                  use_curve=False, by="geometry")
    assert groups == list(ioc.GEO_CLASSES)
    res.update({("curve",) + k[1:]: v for k, v in list(res.items()) if k[0] == "raw"})
    lv = ioc.classLevels(res, groups)
    for c, v in PLANTED.items():
        assert abs(lv[c]["own_level"] - v) < 0.0025, (c, lv[c])


if __name__ == "__main__" and "--write-fixture" in sys.argv:
    writeFixture()
    print(f"wrote {FIXTURE}")
