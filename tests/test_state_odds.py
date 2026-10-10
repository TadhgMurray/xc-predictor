"""State odds (racecast/state_odds.py, build_state_odds.py; owner 2026-10-10).

Pinned here:
  * the simulation is RIGHT on toy fields whose answers are known -- an
    analytic two-runner race, symmetric fields, deterministic fields --
    within the Monte Carlo error DRAWS was solved for;
  * qualifying rounds are simulated from cuts' record and only from it:
    teams then individuals, as many as last season sent; a qualifier whose
    team did not get through races state unattached;
  * where the route to state is not on record (or covers only part of the
    field) there are NO qualifying odds and the pages say "if they make
    state" -- never a made-up rule;
  * the build is idempotent (same inputs -> same fingerprint -> same seed ->
    same numbers) and incremental (an unchanged division is skipped)."""
import math
import os
import sys
from statistics import NormalDist

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import race_sim  # noqa: E402
import state_odds as S  # noqa: E402

# ★ THE TOLERANCE IS THE MONTE CARLO ERROR ITSELF: four standard errors of a
#   share of `draws` seasons -- a false failure about once in 16,000 checks.
_Z = 4.0


def _tol(p, draws):
    return _Z * math.sqrt(max(p * (1.0 - p), 1e-12) / draws)


def _ath(pid, school, rating, sigma=0.03):
    return {"person_id": pid, "school": school, "rating": float(rating),
            "sigma": float(sigma)}


def _team(prefix, school, ratings, sigma=0.03):
    return [_ath(prefix + i, school, r, sigma) for i, r in enumerate(ratings)]


# ------------------------------------------------------------------ #
#  the draw count
# ------------------------------------------------------------------ #

def test_draws_hold_mc_error_under_half_the_display_step():
    worst = math.sqrt(0.25 / S.DRAWS)
    assert worst <= S.DISPLAY_STEP / 2.0 + 1e-12
    # and not wastefully more: one fewer thousand would break the bound
    assert math.sqrt(0.25 / (S.DRAWS - 1000)) > S.DISPLAY_STEP / 2.0


# ------------------------------------------------------------------ #
#  the state meet alone: known answers
# ------------------------------------------------------------------ #

def test_two_runner_race_matches_the_analytic_probability():
    """Log-normal days: P(b beats a) = Phi(ln(rb/ra) / sqrt(sa^2 + sb^2))."""
    f = [_ath(1, "Unattached", 100.0, 0.03), _ath(2, "Unattached", 102.0, 0.04)]
    out = S.simulate(f, draws=S.DRAWS, seed=7, lines=[("podium", 1, "Win")])
    want = NormalDist().cdf(math.log(102.0 / 100.0) / math.hypot(0.03, 0.04))
    got = out["athletes"][2]["p_win"]
    assert abs(got - want) <= _tol(want, S.DRAWS), (got, want)
    assert out["athletes"][1]["p_qualify"] is None        # no rounds: no qualify
    assert out["mode"] == S.MODE_STATE


def test_symmetric_field_splits_evenly_and_shares_sum_exactly():
    f = [_ath(i, "Unattached", 110.0, 0.03) for i in range(1, 5)]
    out = S.simulate(f, draws=S.DRAWS, seed=3, lines=[("top", 2, "Top 2")])
    wins = [out["athletes"][i]["p_win"] for i in range(1, 5)]
    tops = [out["athletes"][i]["p_top"] for i in range(1, 5)]
    assert sum(wins) == pytest.approx(1.0)                # one winner a season
    assert sum(tops) == pytest.approx(2.0)                # two in the top two
    for w in wins:
        assert abs(w - 0.25) <= _tol(0.25, S.DRAWS)


def test_two_identical_teams_are_a_coin_flip():
    a = _team(100, "Alpha", [120, 118, 116, 114, 112, 110, 108])
    b = _team(200, "Beta", [120, 118, 116, 114, 112, 110, 108])
    out = S.simulate(a + b, draws=S.DRAWS, seed=11)
    pa, pb = out["teams"]["Alpha"]["p_win"], out["teams"]["Beta"]["p_win"]
    assert pa + pb == pytest.approx(1.0)
    assert abs(pa - 0.5) <= _tol(0.5, S.DRAWS)
    assert out["teams"]["Alpha"]["p_top3"] == pytest.approx(1.0)
    assert out["teams"]["Alpha"]["p_qualify"] is None


def test_far_apart_field_is_deterministic():
    """Gaps of 20% against sigmas at race_sim's floor: nothing ever moves."""
    fast = _team(100, "Fast", [200, 199, 198, 197, 196])
    mid = _team(200, "Mid", [150, 149, 148, 147, 146])
    slow = _team(300, "Slow", [100, 99, 98, 97, 96])
    short = _team(400, "Short", [300, 240])               # two: no team score
    out = S.simulate(fast + mid + slow + short, draws=2000, seed=1,
                     lines=[("podium", 2, "Top 2")])
    assert out["teams"]["Fast"]["p_win"] == 1.0
    assert out["teams"]["Slow"]["p_top3"] == 1.0          # third of three
    assert out["teams"]["Short"]["p_win"] == 0.0          # cannot score
    assert out["teams"]["Short"]["score_mean"] is None
    assert out["athletes"][400]["p_win"] == 1.0           # but its runner wins
    # Fast's scorers take places 1-5 among scorers: 1+2+3+4+5 (Short's two
    # finish first but take no scoring place)
    assert out["teams"]["Fast"]["score_mean"] == 15.0


def test_same_seed_same_numbers():
    f = [_ath(i, f"S{i % 3}", 100 + i, 0.04) for i in range(30)]
    a = S.simulate(f, draws=500, seed=42)
    b = S.simulate(f, draws=500, seed=42)
    assert a == b


# ------------------------------------------------------------------ #
#  qualifying rounds
# ------------------------------------------------------------------ #

def _round(key, members, teams, individuals, label=None):
    return {"key": key, "label": label or key, "kind": "section",
            "year": 2025, "teams": teams, "individuals": individuals,
            "members": list(members)}


def test_teams_then_individuals_as_many_as_last_season():
    a = _team(100, "A", [200, 199, 198, 197, 196, 195, 194])
    b = _team(200, "B", [150, 149, 148, 147, 146, 145, 144])
    # C's front runner is faster than anyone on A or B; the rest are slow
    c = _team(300, "C", [250, 120, 119, 118, 117, 116, 115])
    u = [_ath(400, "Unattached", 130.0)]                  # next-best individual
    field = a + b + c + u
    rd = _round("sec", [f["person_id"] for f in field], teams=2, individuals=1)
    out = S.simulate(field, [rd], draws=2000, seed=5)
    assert out["mode"] == S.MODE_QUAL
    assert out["teams"]["A"]["p_qualify"] == 1.0
    assert out["teams"]["B"]["p_qualify"] == 1.0
    assert out["teams"]["C"]["p_qualify"] == 0.0
    assert out["athletes"][300]["p_qualify"] == 1.0       # C's star, individually
    assert out["athletes"][301]["p_qualify"] == 0.0
    assert out["athletes"][400]["p_qualify"] == 0.0       # the one slot is taken
    # exactly 7 + 7 + 1 qualifiers every season
    assert sum(x["p_qualify"] for x in out["athletes"].values()) == pytest.approx(15.0)
    # ★ C's star races state UNATTACHED: wins the race, scores no points
    assert out["athletes"][300]["p_win"] == 1.0
    assert out["teams"]["C"]["p_win"] == 0.0 and out["teams"]["C"]["score_mean"] is None
    assert out["teams"]["A"]["p_win"] == 1.0
    # A's five take scoring places 1-5 (the unattached winner takes none)
    assert out["teams"]["A"]["score_mean"] == 15.0
    # a non-qualifier is not in the state race at all
    assert out["athletes"][301]["p_top"] == 0.0


def test_a_coin_flip_for_the_last_individual_place():
    f = [_ath(1, "Unattached", 150.0), _ath(2, "Unattached", 110.0, 0.03),
         _ath(3, "Unattached", 110.0, 0.03)]
    rd = _round("sec", [1, 2, 3], teams=0, individuals=2)
    out = S.simulate(f, [rd], draws=S.DRAWS, seed=9)
    assert out["athletes"][1]["p_qualify"] == 1.0
    p2, p3 = out["athletes"][2]["p_qualify"], out["athletes"][3]["p_qualify"]
    assert p2 + p3 == pytest.approx(1.0)
    assert abs(p2 - 0.5) <= _tol(0.5, S.DRAWS)


def test_two_rounds_feed_one_state_meet():
    """Each round sends one: the two winners meet at state, and only they."""
    f = [_ath(1, "Unattached", 150.0, 0.03), _ath(2, "Unattached", 100.0),
         _ath(3, "Unattached", 153.0, 0.03), _ath(4, "Unattached", 100.0)]
    rds = [_round("r1", [1, 2], 0, 1), _round("r2", [3, 4], 0, 1)]
    out = S.simulate(f, rds, draws=S.DRAWS, seed=2,
                     lines=[("podium", 2, "Top 2")])
    assert out["athletes"][1]["p_qualify"] == 1.0
    assert out["athletes"][3]["p_qualify"] == 1.0
    assert out["athletes"][1]["p_podium"] == 1.0          # field of two
    want = NormalDist().cdf(math.log(153.0 / 150.0) / math.hypot(0.03, 0.03))
    assert abs(out["athletes"][3]["p_win"] - want) <= _tol(want, S.DRAWS)
    assert out["athletes"][2]["p_win"] == 0.0


def test_runner_outside_every_round_is_no_path_not_guessed():
    f = [_ath(1, "Unattached", 150.0), _ath(2, "Unattached", 160.0)]
    out = S.simulate(f, [_round("r", [1], 0, 1)], draws=200, seed=1)
    assert out["no_path"] == [2]
    assert 2 not in out["athletes"]
    assert out["athletes"][1]["p_win"] == 1.0             # 2 is not in the race


# ------------------------------------------------------------------ #
#  rounds from cuts' record
# ------------------------------------------------------------------ #

def _cell(unit, slug, teams, ind, feeds="2", kind="section", flags=None):
    return {"kind": kind, "unit": unit, "slug": slug, "value": slug.upper(),
            "label": f"{unit} D{slug}", "feeds": feeds,
            "latest": {"year": 2025, "flags": flags or [],
                       "teams_advanced": teams, "individuals_advanced": ind}}


def _data(cells, gender="boys"):
    return {"state": "CA", "genders": {gender: {"divisions": [], "sections": cells}}}


def _units(**by_school):
    return {s: {"section": sec, "section_div": div} for s, (sec, div) in by_school.items()}


def test_rounds_built_when_every_school_has_a_route():
    field = _team(100, "A", [120] * 5) + _team(200, "B", [118] * 5) \
        + [_ath(900, "Unattached", 140.0)]
    data = _data([_cell("NCS", "2", 2, 5), _cell("CCS", "2", 1, 4)])
    rounds, why = S.roundsFor(field, data, "boys", "2",
                              _units(A=("NCS", "2"), B=("CCS", "2")))
    assert why is None
    assert [r["label"] for r in rounds] == ["CCS D2", "NCS D2"]
    ncs = next(r for r in rounds if r["label"] == "NCS D2")
    assert ncs["teams"] == 2 and ncs["individuals"] == 5
    assert sorted(ncs["members"]) == list(range(100, 105))
    # the unattached runner has no section: left out of the season, counted
    out = S.simulate(field, rounds, draws=200, seed=1)
    assert out["no_path"] == [900]


def test_one_school_without_a_route_sends_the_division_to_state_only():
    field = _team(100, "A", [120] * 5) + _team(200, "B", [118] * 5)
    data = _data([_cell("NCS", "2", 2, 5)])
    rounds, why = S.roundsFor(field, data, "boys", "2",
                              _units(A=("NCS", "2"), B=("CCS", "2")))
    assert rounds is None
    assert "1 school" in why and "no section or regional route" in why


def test_no_record_at_all_is_state_only_with_a_reason():
    field = _team(100, "A", [120] * 5)
    for data in (None, {"genders": {}}, _data([])):
        rounds, why = S.roundsFor(field, data, "boys", "2", {})
        assert rounds is None and why


def test_flagged_or_unsplit_cells_are_not_used():
    field = _team(100, "A", [120] * 5)
    units = _units(A=("NCS", "2"))
    thin = _data([_cell("NCS", "2", 2, 5, flags=["only 4 rated runners"])])
    assert S.roundsFor(field, thin, "boys", "2", units)[0] is None
    unsplit = _data([_cell("NCS", "2", None, None)])
    assert S.roundsFor(field, unsplit, "boys", "2", units)[0] is None
    # a cell feeding ANOTHER state division is not this division's route
    other = _data([_cell("NCS", "2", 2, 5, feeds="3")])
    assert S.roundsFor(field, other, "boys", "2", units)[0] is None
    # provenance notes are not sample flags (cuts._hardFlags)
    prov = _data([_cell("NCS", "2", 2, 5,
                        flags=["division read from the schools, not the race title"])])
    assert S.roundsFor(field, prov, "boys", "2", units)[0] is not None


def test_regional_cell_matches_on_region():
    field = _team(100, "A", [120] * 5)
    data = _data([_cell("REGION II", "6a", 3, 8, feeds="6a", kind="region")])
    rounds, why = S.roundsFor(field, data, "boys", "6a",
                              {"A": {"region": "Region II"}})
    assert why is None and rounds[0]["kind"] == "region"


def test_advancer_split_counts_teams_of_five():
    import cuts
    adv = ([{"school": "A"}] * 7 + [{"school": "B"}] * 4
           + [{"school": "Unattached"}] * 6 + [{"school": "C"}])
    assert cuts.advancerSplit(adv) == (1, 11)


# ------------------------------------------------------------------ #
#  race_sim: the per-draw team map is the same scorer
# ------------------------------------------------------------------ #

def test_score_draws_two_d_team_matches_one_d():
    rng = np.random.default_rng(0)
    n, draws, n_teams = 40, 50, 6
    team = rng.integers(-1, n_teams, size=n)
    times = rng.random((draws, n))
    full = np.ones(n_teams, dtype=bool)
    cap = np.full(n_teams, 7)
    s1, p1 = race_sim._scoreDraws(times, team, full, cap, n_teams)
    s2, p2 = race_sim._scoreDraws(times, np.tile(team, (draws, 1)), full, cap, n_teams)
    assert np.array_equal(p1, p2)
    assert np.array_equal(np.isnan(s1), np.isnan(s2))
    assert np.allclose(np.nan_to_num(s1), np.nan_to_num(s2))


# ------------------------------------------------------------------ #
#  words and the fallback label
# ------------------------------------------------------------------ #

def test_fmt_pct_never_prints_certainty():
    assert S.fmtPct(None) == ""
    assert S.fmtPct(0.0) == "<1%" and S.fmtPct(0.004) == "<1%"
    assert S.fmtPct(1.0) == ">99%" and S.fmtPct(0.996) == ">99%"
    assert S.fmtPct(0.94) == "94%" and S.fmtPct(0.305) in ("30%", "31%")


def test_athlete_line_state_only_has_no_qualify():
    row = {"state": "OR", "division": "6a", "gender": "boys", "p_qualify": None,
           "p_podium": 0.31, "path": "state", "mode": S.MODE_STATE,
           "division_label": "OR Class 6A"}
    line = S.athleteLine(row)
    assert line["qualify"] is None and line["podium"] == "31%"
    assert line["href"] == "/state-odds/or?division=6a&gender=boys"
    q = S.athleteLine(dict(row, mode=S.MODE_QUAL, p_qualify=0.94))
    assert q["qualify"] == "94%"
    assert S.athleteLine(dict(row, path="none")) is None


def _render_line(so):
    import jinja2
    tdir = os.path.join(_ROOT, "racecast", "templates")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(tdir))
    env.globals["state_odds_athlete"] = lambda pid: so
    return env.get_template("_state_odds_line.html").render(athlete={"person_id": 1})


def test_athlete_line_words():
    base = {"state": "OR", "division": "6a", "gender": "boys", "p_podium": 0.31,
            "path": "state", "division_label": "OR Class 6A"}
    qual = " ".join(_render_line(S.athleteLine(dict(base, mode=S.MODE_QUAL,
                                                    p_qualify=0.94))).split())
    assert "94%</strong> to qualify" in qual and "31%</strong> to finish top 8" in qual
    assert "OR Class 6A" in qual and "model estimate" in qual
    only = " ".join(_render_line(S.athleteLine(dict(base, mode=S.MODE_STATE,
                                                    p_qualify=None))).split())
    assert "if they make it" in only and "to qualify" not in only
    assert _render_line(None).strip() == ""


# ------------------------------------------------------------------ #
#  the page
# ------------------------------------------------------------------ #

def _meta(mode, rounds=None, why=None):
    return [{"year": 2026, "state": "OR", "division": "6a", "gender": "boys",
             "division_label": "OR Class 6A", "mode": mode, "rounds": rounds,
             "why": why, "draws": S.DRAWS, "n_field": 3, "n_no_path": 0,
             "built_at": "2026-10-10T04:00:00", "as_of": "2026-10-04"}]


_ATH = [{"person_id": 1, "name": "Ann Lee", "school": "Jesuit", "school_state": "OR",
         "grade": "12", "rating": 140.2, "sigma": 0.03, "p_qualify": 0.94,
         "p_top": 0.88, "p_podium": 0.31, "p_win": 0.04, "place_mean": 9.1,
         "path": "x"},
        {"person_id": 2, "name": "Bo Diaz", "school": "Jesuit", "school_state": "OR",
         "grade": "9", "rating": 90.0, "sigma": 0.05, "p_qualify": 0.001,
         "p_top": 0.0, "p_podium": 0.0, "p_win": 0.0, "place_mean": None,
         "path": "x"}]
_TEAMS = [{"school": "Jesuit", "school_state": "OR", "n_runners": 7, "p_qualify": 0.97,
           "p_top3": 0.6, "p_win": 0.2, "score_mean": 98.4, "path": "x"}]


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("flask")
    import contextlib
    import app as A
    import database
    import ttlcache

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self, **k):
            return Cur()

    @contextlib.contextmanager
    def fake():
        yield Conn()
    monkeypatch.setattr(database, "getConn", fake)
    state = {"meta": _meta(S.MODE_STATE, why="No round before state is on record.")}
    monkeypatch.setattr(S, "stateMeta", lambda cur, st: state["meta"] if st == "OR" else [])
    monkeypatch.setattr(S, "divisionOdds", lambda cur, st, d, g, y: (
        [dict(a, p_qualify=None) if state["meta"][0]["mode"] != S.MODE_QUAL else a
         for a in _ATH],
        [dict(t, p_qualify=None) if state["meta"][0]["mode"] != S.MODE_QUAL else t
         for t in _TEAMS]))
    ttlcache.clear()
    yield A.app.test_client(), state
    ttlcache.clear()


def test_state_only_page_says_if_they_make_state(client):
    c, _state = client
    r = c.get("/state-odds/or?division=6a&gender=boys")
    assert r.status_code == 200, r.data[:400]
    html = r.get_data(as_text=True)
    assert "Model estimates" in html and "last season" in html
    assert "If they make state" in html
    assert ">Qualify<" not in html                        # no qualifying column
    assert "Ann Lee" in html and "31%" in html
    assert "Bo Diaz" not in html                          # every column under 1%
    assert "1 more athlete" in html


def test_qualifying_page_shows_rounds_and_qualify(client):
    c, state = client
    state["meta"] = _meta(S.MODE_QUAL, rounds=[{"key": "k", "label": "Metro 6A",
                                                "kind": "section", "year": 2025,
                                                "teams": 2, "individuals": 5}])
    html = c.get("/state-odds/or?division=6a&gender=boys").get_data(as_text=True)
    assert "Qualifying is simulated" in html
    assert "Metro 6A: 2 teams + 5 individuals (2025)" in html
    assert ">Qualify<" in html and "94%" in html and "97%" in html
    assert "If they make state" not in html


def test_bad_state_404_and_case_redirect(client):
    c, _ = client
    assert c.get("/state-odds/zz").status_code == 404
    r = c.get("/state-odds/OR?division=6a")
    assert r.status_code == 301 and "/state-odds/or?division=6a" in r.headers["Location"]


# ------------------------------------------------------------------ #
#  the build: idempotent and incremental
# ------------------------------------------------------------------ #

def test_fingerprint_is_stable_and_sensitive():
    f = [_ath(1, "A", 120.0), _ath(2, "B", 110.0)]
    a = S.fingerprint(f, None)
    assert a == S.fingerprint(list(reversed(f)), None)     # order-free
    assert a != S.fingerprint([_ath(1, "A", 121.0), _ath(2, "B", 110.0)], None)
    assert a != S.fingerprint(f, [_round("r", [1, 2], 0, 1)])
    assert S.seedOf(a) == S.seedOf(a)


def test_build_skips_an_unchanged_division(monkeypatch):
    import build_state_odds as B
    field = [dict(_ath(i, "A", 100 + i), name=f"R{i}", school_state="OR",
                  grade="11", last_race="2026-10-04") for i in range(6)]
    monkeypatch.setattr(B, "plan", lambda cur, st, y: [
        (None, "all", "OR (one race)", "boys", "hs_m")])
    monkeypatch.setattr(B.P, "_fieldUncached", lambda *a: [dict(f) for f in field])
    monkeypatch.setattr(B, "_sigmas", lambda cur, ids: {i: 0.03 for i in ids})
    monkeypatch.setattr(B, "_schoolUnits", lambda cur, st, s: {})
    monkeypatch.setattr(B, "_marks", lambda cur, st: None)
    writes = []
    monkeypatch.setattr(B, "_write", lambda cur, *a: writes.append(a))
    monkeypatch.setattr(B.S, "DRAWS", 200)
    store = {}
    monkeypatch.setattr(B, "_old", lambda cur, st, y: dict(store))

    class Cur:
        def execute(self, *a):
            pass
    built, skipped = B.buildState(Cur(), "OR", 2026, log=lambda *a: None)
    assert (built, skipped) == (1, 0)
    year, st, slug, label, g, f_, rounds, why, res, fp = writes[0]
    assert res["mode"] == S.MODE_STATE and rounds is None and why
    store[("all", "boys")] = fp
    built, skipped = B.buildState(Cur(), "OR", 2026, log=lambda *a: None)
    assert (built, skipped) == (0, 1)
    built, skipped = B.buildState(Cur(), "OR", 2026, force=True, log=lambda *a: None)
    assert built == 1
    # forced again: the same inputs give the same numbers
    assert writes[-1][8] == res
