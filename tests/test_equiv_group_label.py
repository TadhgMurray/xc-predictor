# Project: xc-predictor / tests
# File:    test_equiv_group_label.py
# Purpose: on an equivalents card whose group is a CHOICE (the course page),
#          the group moves only the own-scale rating -- never the course
#          time's track equivalent or its HS-equivalent.
#
# ★ THE REPORT (owner, 2026-09-29): "I still don't get why the course
#   conversions putting it to diff pools changes the hs-equivalent rating
#   and the predicted time." 28:34 over Gans Creek's 10000m read 12:47.4 /
#   HS 156.6 as HS Boys, 13:16.1 / HS 150.0 as College Men and 13:36.1 /
#   HS 147.6 as MS Boys: three per-group model terms (distance curve, the
#   tilt at the group's own number, grass-to-track constants), none about
#   the run. app._equivOnHs has the breakdown.
#
# ! A RACE PAGE IS NOT A LABEL: its group is the pool the race was rated in,
#   and it keeps converting as that group so the card matches its results.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_equiv_group_label.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                 # noqa: E402
import app as A                                               # noqa: E402
import conversions as cv                                      # noqa: E402
import pool_view as PV                                        # noqa: E402

# the live factors on 2026-09-29
_FAC = {"hs_m": 1.0, "hs_f": 1.0, "college_m": 1.2063, "ms_m": 0.8729,
        "college_f": 1.2056, "ms_f": 0.9430}
# per-group constants that differ, as the engine's do; the ENGINE's own
# distance curves are used unstubbed, and they differ by group too
_MEAN = {"hs_m": 1211.5, "college_m": 1650.0, "ms_m": 900.0,
         "hs_f": 1466.0, "college_f": 1900.0, "ms_f": 1100.0}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(cv, "pool_mean", lambda pool, sport=None: _MEAN[pool.split("|")[0]])
    monkeypatch.setattr(cv, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(cv, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "XC", 0.05)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "TF", 0.0)
    monkeypatch.setattr(PV, "repFactor", lambda pool, sport=None: _FAC.get(pool))
    A._EQUIV_CACHE.clear()
    client = A.app.test_client()

    def get(pool, ref="hs", dist=10000, target=5000):
        q = {"pool": pool, "dist": dist, "target": target,
             "difficulty": 0.05646264, "course": "Gans Creek Recreation Area"}
        if ref:
            q["ref"] = ref
        body = client.get("/api/equivalence", query_string=q).get_json()
        assert body.get("points"), body
        return body
    return get


def _at(body, t):
    """(own rating, HS-equivalent, track time) at course time t, the way
    equiv-line.js reads the points."""
    pts = sorted(body["points"], key=lambda p: p[1])
    for a, b in zip(pts, pts[1:]):
        if a[1] <= t <= b[1]:
            f = (t - a[1]) / (b[1] - a[1])
            r = a[0] + (b[0] - a[0]) * f
            return r, r * body["hs_factor"], a[2] + (b[2] - a[2]) * f
    raise AssertionError(f"{t} off the line")


def test_a_chosen_group_moves_only_the_own_rating(api):
    t = 28 * 60 + 34.0
    got = {p: _at(api(p), t) for p in ("hs_m", "college_m", "ms_m")}
    hs_own, hs_eq, hs_track = got["hs_m"]
    for p, (own, eq, track) in got.items():
        assert track == pytest.approx(hs_track, abs=0.05), p     # same track 5K
        assert eq == pytest.approx(hs_eq, abs=0.05), p           # same HS-equivalent
        assert own == pytest.approx(hs_eq / _FAC[p], abs=0.05), p
    assert got["college_m"][0] < hs_own < got["ms_m"][0]         # own scales differ


def test_girls_convert_on_the_girls_model(api):
    t = 17 * 60.0
    a, b = _at(api("hs_f", dist=5000), t), _at(api("college_f", dist=5000), t)
    assert a[2] == pytest.approx(b[2], abs=0.05) and a[1] == pytest.approx(b[1], abs=0.05)
    assert api("college_f")["converted_as"] == "hs_f"


def test_without_ref_the_group_is_the_model(api):
    """The race page's call (no ref): the race's own group converts, so the
    card agrees with the ratings in the results table -- and, the reason
    ref exists, two groups then disagree about the same run."""
    t = 28 * 60 + 34.0
    hs, col = _at(api("hs_m", ref=None), t), _at(api("college_m", ref=None), t)
    assert api("college_m", ref=None)["converted_as"] == "college_m"
    assert abs(hs[2] - col[2]) > 1.0                             # track times differ


def test_a_bad_ref_is_refused(api):
    body = A.app.test_client().get("/api/equivalence", query_string={
        "pool": "hs_m", "dist": 5000, "ref": "college"}).get_json()
    assert "error" in body


def test_the_course_card_sends_ref_and_the_race_card_does_not():
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.join(_ROOT, "racecast", "templates")))
    mac = env.get_template("_equiv_line.html").module.equiv_line
    free = str(mac("hs_m", 5000, 0.05, "X"))
    fixed = str(mac("college_m", 10000, 0.05, "X", fixed_pool=True))
    assert 'data-ref="hs"' in free and 'data-ref=""' in fixed
    assert "a little" not in free        # the old hint said the group mattered
    js = open(os.path.join(_ROOT, "racecast", "static", "equiv-line.js")).read()
    assert 'if (st.ref) q.set("ref", st.ref);' in js
    assert "ref: root.dataset.ref" in js
