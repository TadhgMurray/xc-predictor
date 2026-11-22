"""The go-live's keyed warm start (engine/warm_start.py, 2026-11-22).

Night 1 is solved cold and saved with its keys; night 2 has new athletes,
new races, a new course and every dense id renumbered. Its unknowns map
by key, and a theta warm start lands on night 2's cold answer.

    python -m pytest -q tests/test_warm_start.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import joint_solve as js                                        # noqa: E402
import pair_engine as pe                                        # noqa: E402
import run_joint as rj                                          # noqa: E402
import warm_start as ws                                         # noqa: E402

KW = dict(n_outer=3, tilt=False, tau_max=None, n_probe=0)


def _universe(seed=3, n_p=260, n_course=36, days_per_course=4, per=8):
    rng = np.random.default_rng(seed)
    dates = rng.integers(0, 120, (n_course, days_per_course)) + 20000
    rows = []
    for p in range(n_p):
        for _ in range(per):
            c = int(rng.integers(n_course))
            rows.append((p, c, int(dates[c, rng.integers(days_per_course)])))
    p, c, d = (np.array(x) for x in zip(*rows))
    a, dc = rng.normal(0, 0.1, n_p), rng.normal(0, 0.04, n_course)
    y = a[p] + dc[c] + rng.normal(0, 0.01, d.max() + 1)[d] + rng.normal(0, 0.01, p.size)
    return p, c, d, y


def _night(U, rows, pack_day, perm_seed):
    """A pack and its design over the given universe rows, dense ids drawn
    afresh (keys in a shuffled order), as each night's 07_pack does."""
    p, c, d, y = (x[rows] for x in U)
    rng = np.random.default_rng(perm_seed)
    pu, cu = rng.permutation(np.unique(p)), rng.permutation(np.unique(c))
    p_code = {v: i for i, v in enumerate(pu)}
    c_code = {v: i for i, v in enumerate(cu)}
    cols = {"athlete": np.array([p_code[v] for v in p]),
            "course": np.array([c_code[v] for v in c]),
            "year": np.full(p.size, 2026), "days": pack_day - d,
            "athlete_keys": np.array([(f"pid{v}", "hs_m") for v in pu]),
            "course_keys": np.array([f"XC:venue{v}:d5000" for v in cu])}
    ath, n_ath = pe.athleteSeasonCodes(cols["athlete"], cols["year"])
    race, n_race = rj.raceCodes(cols["course"], cols["days"])
    D = js.Design(ath, cols["course"], race, n_ath=n_ath, n_race=n_race)
    D.course_keys = list(cols["course_keys"])
    keys = ws.designKeys(D, cols, race, "cell", False, [], pack_day - cols["days"])

    def fresh():                         # solveJoint mutates its design
        return js.Design(ath, cols["course"], race, n_ath=n_ath, n_race=n_race)
    return cols, D, keys, y, fresh


def test_theta_warm_start_maps_by_key_and_keeps_the_cold_answer(tmp_path, monkeypatch):
    U = _universe()
    p, c, d, _ = U
    new = (p >= 240) | (c == 35) | (d % 9 == 0)      # new athletes, a course, races
    cols1, D1, k1, y1, _f1 = _night(U, np.flatnonzero(~new), 30000, 1)
    cols2, D2, k2, y2, fresh2 = _night(U, np.arange(p.size), 30001, 2)
    assert k1 is not None and k2 is not None
    out1 = js.solveJoint(y1, design=D1, **KW)
    parts, lay = {"code": "x", "args": {}, "env": {}}, ws.packLayout(cols2)
    path = str(tmp_path / "joint_warm.npz")
    assert ws.save(path, out1, D1, k1, parts, lay, "theta", {"mode": "cold"}, D1.cg_iters)

    # by key: every athlete, course and race of night 1 carries its value
    th, cov, idx_as = ws.mapTheta(k1, ws._layout(D1), out1["theta"], k2, ws._layout(D2))
    pid1 = {k[0]: i for i, k in enumerate(cols1["athlete_keys"])}
    for i, key in enumerate(cols2["athlete_keys"]):
        j = pid1.get(key[0])
        assert th[i] == (0.0 if j is None else out1["theta"][j]) and cov[i] == (j is not None)
    cell1 = {k: i for i, k in enumerate(cols1["course_keys"])}
    for i, key in enumerate(cols2["course_keys"]):
        j = cell1.get(key)
        assert th[D2.o_d + i] == (0.0 if j is None else out1["theta"][D1.o_d + j])
    race_key = lambda cols, D: {(str(cols["course_keys"][cols["course"][r]]),  # noqa: E731
                                 30000 + (cols is cols2) - int(cols["days"][r])): D.race[r]
                                for r in range(D.n)}
    r1, r2 = race_key(cols1, D1), race_key(cols2, D2)
    for key, i in r2.items():
        j = r1.get(key)
        assert th[D2.o_u + i] == (0.0 if j is None else out1["theta"][D1.o_u + j])
    assert 0 < (~cov).sum() < 0.25 * cov.size

    # the safety checks go cold: another model, too little cover
    monkeypatch.setenv("XCP_WARM_MIN_COVER", "0.5")
    assert ws.prepare(path, "theta", D2, k2, dict(parts, code="y"), lay)[0] is None
    monkeypatch.setenv("XCP_WARM_MIN_COVER", "0.99")
    assert ws.prepare(path, "theta", D2, k2, parts, lay)[0] is None
    monkeypatch.setenv("XCP_WARM_MIN_COVER", "0.5")
    warm, n_outer, info = ws.prepare(path, "theta", D2, k2, parts, lay)
    assert warm["mode"] == "theta" and n_outer is None and info["mode"] == "theta"

    # theta: tonight's cold answer, to the CG tolerance
    monkeypatch.delenv("XCP_WARM_START", raising=False)
    cold = js.solveJoint(y2, design=fresh2(), **KW)
    Dw = fresh2()
    Dw.warm_start = warm
    hot = js.solveJoint(y2, design=Dw, **KW)
    assert np.max(np.abs(hot["delta"] - cold["delta"])) < 1e-6
    assert np.max(np.abs(hot["ability"] - cold["ability"])) < 1e-6
    print(f"  pass-1 CG: warm {Dw.cg_iters[0]} vs cold {D1.cg_iters[0]} (night 1)")
