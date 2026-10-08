# Project: xc-predictor / tests
# File:    test_race_day_divisions.py
# Purpose: the day guard (owner, 2026-10-08; joint_solve.raceDayDivisions).
#          Production pools the race-day term over a whole venue-day
#          (XCP_RACE_KEY=venue). At Midlothian James Smith HS Invitational,
#          Aug 27 2026, three of eight divisions were stored at 3218 m but
#          run at 5000 m: 344 of 1,091 rows read ~40% slow, the pooled day
#          came out "slow 10.3%", and every CORRECT race was credited ~12%.
#          A synthetic venue-day reproducing it: the correct rows' day must
#          come back ~0 in the joint solve AND in the bracket refit the
#          ratings carry, the three divisions must be flagged and named with
#          the distance that reconciles them, and a world with no broken
#          division must come out bit for bit as before.
#
#   python -m pytest -q tests/test_race_day_divisions.py
import contextlib
import io
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402

K = 1.0707                         # distance_pin.DISTANCE_EXPONENT
WRONG = K * np.log(5000.0 / 3218.0)  # a 5000 normalised as a 3218: ~0.47 log slow
MID_DAYS = 30
NOISE = 0.04


def _pack(seed=0, broken=True, n_venue=12, days_per_venue=6, n_ath=1500):
    """A venue-keyed XC season. Every venue has a 5000 and a 3200 cell and
    races six days, six divisions a day (four 5000s, two 3200s, boys and
    girls). Venue 0's first day is Midlothian: eight divisions, five
    correct 5000s (747 rows) and three 3218 m divisions run at 5000 m (344
    rows) -- `broken=False` stores those three at their true distance."""
    rng = np.random.default_rng(seed)
    keys = []
    for v in range(n_venue):
        keys += [f"XC:{100 + v}:d5000", f"XC:{100 + v}:d3200"]
    d_cell = rng.normal(0, 0.03, len(keys))
    pool_of = np.r_[np.zeros(n_ath // 2, int), np.ones(n_ath - n_ath // 2, int)]
    by_pool = [np.flatnonzero(pool_of == p) for p in (0, 1)]
    a_true = rng.normal(0, 0.12, n_ath)
    rows = dict(ath=[], cell=[], days=[], dist=[], meet=[], div=[], y=[])
    wrong_rows = []
    mid = None
    div_id = 0
    for v in range(n_venue):
        # Midlothian's venue races all season: its courses are known from
        # their other days, so the broken afternoon can only land on the day
        for k in range(MID_DAYS if v == 0 else days_per_venue):
            days_ago = 30 + 3 * k + v
            u = rng.normal(0, 0.015)
            meet_id = 10_000 + v * 100 + k
            if v == 0 and k == 0:
                mid = (days_ago, meet_id)
                u = 0.0                            # an ordinary afternoon
                plan = ([(0, 0, n) for n in (150, 150, 150, 150, 147)]
                        + [(1, p, n) for p, n in ((0, 115), (1, 115), (0, 114))])
                wrong = [False] * 5 + [True] * 3
            else:
                plan = [(0, 0, 30), (0, 1, 30), (0, 0, 25), (0, 1, 25),
                        (1, 0, 30), (1, 1, 30)]
                wrong = [False] * 6
            for (c_off, p, n), bad in zip(plan, wrong):
                c = 2 * v + c_off
                who = rng.choice(by_pool[p], n, replace=False)
                y = a_true[who] + d_cell[c] + u + rng.normal(0, NOISE, n)
                stored = 3218.0 if c_off else 5000.0
                if bad and broken:
                    y = y + WRONG                  # stored 3218, run at 5000
                    wrong_rows.extend(range(len(rows["ath"]), len(rows["ath"]) + n))
                elif bad:
                    c = 2 * v                      # stored at its true 5000
                    y = y - d_cell[2 * v + 1] + d_cell[c]
                    stored = 5000.0
                div_id += 1
                rows["ath"] += who.tolist()
                rows["cell"] += [c] * n
                rows["days"] += [days_ago] * n
                rows["dist"] += [stored] * n
                rows["meet"] += [meet_id] * n
                rows["div"] += [div_id] * n
                rows["y"] += y.tolist()
    n = len(rows["y"])
    cols = {"athlete": np.array(rows["ath"]), "year": np.full(n, 2026),
            "course": np.array(rows["cell"]), "days": np.array(rows["days"], dtype=np.float64),
            "sport": np.zeros(n, dtype=np.int64), "norm": np.exp(np.array(rows["y"])),
            "doy": np.full(n, 270), "dist_m": np.array(rows["dist"], dtype=np.float32),
            "meet_id": np.array(rows["meet"], dtype=np.int64),
            "div_id": np.array(rows["div"], dtype=np.int64),
            "result_id": np.arange(n) + 700_000,
            "athlete_keys": [(5000 + i, ("hs_m", "hs_f")[pool_of[i]]) for i in range(n_ath)],
            "course_keys": keys}
    wrong_mask = np.zeros(n, dtype=bool)
    wrong_mask[wrong_rows] = True
    mid_mask = (cols["days"] == mid[0]) & (cols["meet_id"] == mid[1])
    return cols, np.ones(n, dtype=bool), wrong_mask, mid_mask


def _design(cols, keep):
    rj._RACE_KEY["by"] = "venue"
    with contextlib.redirect_stdout(io.StringIO()):
        return rj.buildDesign(cols, keep, sport_offset=False, curve=False, rust=False,
                              dist=False, slope=False, link=False, altitude=False,
                              era_years=0, importance="none", indoor=False,
                              dist_table=False)


def _solve(cols, keep, guard, monkeypatch):
    monkeypatch.setenv("XCP_DAY_DIVISION_GUARD", "1" if guard else "0")
    D, athlete_pool, pool_names = _design(cols, keep)
    y = np.log(cols["norm"][keep])
    with contextlib.redirect_stdout(io.StringIO()):
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=6,
                            tilt=False, n_probe=0)
    return D, athlete_pool, pool_names, y, out


@pytest.fixture(autouse=True)
def _race_key():
    old = rj._RACE_KEY["by"]
    yield
    rj._RACE_KEY["by"] = old


# ------------------------------------------------------------------ #
# the rule itself
# ------------------------------------------------------------------ #

def test_the_rule_on_midlothians_numbers():
    """Eight divisions on one venue-day, three reading ~47% (log) slow:
    the day is the five correct ones', the three are flagged."""
    rng = np.random.default_rng(1)
    sizes = [150, 150, 150, 150, 147, 115, 115, 114]
    sub = np.repeat(np.arange(8), sizes)
    r = rng.normal(0, 0.03, sub.size) + np.where(sub >= 5, WRONG, 0.0)
    w = np.ones(sub.size)
    race = np.zeros(sub.size, dtype=np.int64)
    pen = 1e-3 / 0.01 ** 2
    plain = js.raceDayDivisions(r, w, 1.0, race, None, 1, pen)
    dd = js.raceDayDivisions(r, w, 1.0, race, sub, 1, pen)
    assert plain["u"][0] > 0.10                       # the pooled day: the bug
    assert abs(dd["u"][0]) < 0.01                     # the guarded day: ~0
    assert dd["flag"].tolist() == [False] * 5 + [True] * 3
    assert np.array_equal(dd["keep"] == 0, sub >= 5)
    # the flagged rows' excess, held off: their own day less the race's
    assert np.allclose(dd["offset"][sub >= 5], dd["m"][sub[sub >= 5]] - dd["u"][0])
    assert np.all(dd["offset"][sub < 5] == 0)


def test_a_lone_runner_or_a_lone_division_is_never_flagged():
    r = np.r_[np.zeros(40), 0.5, np.zeros(30) + 0.3]
    sub = np.r_[np.zeros(40, int), 1, np.full(30, 2)]
    race = np.r_[np.zeros(41, int), np.ones(30, int)]
    dd = js.raceDayDivisions(r, np.ones(r.size), 1.0, race, sub, 2, 100.0)
    # division 1 is one runner (0.5 off): not flagged; division 2 is the
    # only division of race 1: nothing to compare, not flagged
    assert not dd["flag"].any()


def test_no_broken_division_means_the_old_sums_bit_for_bit():
    rng = np.random.default_rng(3)
    n = 5000
    race = rng.integers(0, 50, n)
    sub = race * 4 + rng.integers(0, 4, n)
    r = rng.normal(0, 0.03, n) + rng.normal(0, 0.02, 50)[race]
    w = rng.uniform(0.5, 1, n)
    h = rng.uniform(0.9, 1.1, n)
    pen = rng.uniform(5, 20, 50)
    dd = js.raceDayDivisions(r, w, h, race, sub, 50, pen)
    assert not dd["flag"].any()
    num = np.bincount(race, weights=w * h * r, minlength=50)
    P = np.bincount(race, weights=w * h * h, minlength=50) + pen
    assert np.array_equal(dd["u"], num / P)
    assert np.array_equal(dd["P"], P) and np.all(dd["offset"] == 0)


def test_division_codes_from_the_pack_and_the_proxy():
    cols, keep, _w, _m = _pack()
    D, _ap, _pn = _design(cols, keep)
    assert D.subrace_from == "pack"
    # one division per (meet, div): the pack planted 11 * 6 + MID_DAYS - 1
    # days of six and Midlothian's eight
    assert D.n_subrace == (11 * 6 + MID_DAYS - 1) * 6 + 8
    proxy = {k: v for k, v in cols.items() if k not in ("meet_id", "div_id")}
    Dp, _ap, _pn = _design(proxy, keep)
    assert Dp.subrace_from == "proxy"
    # (race, cell, pool): Midlothian's 5000s are one boys' division, its
    # wrong 3218s a boys' and a girls'
    assert Dp.n_subrace < D.n_subrace
    # a row without a div_id falls back to (cell, pool) inside its race
    part = dict(cols)
    part["div_id"] = cols["div_id"].copy()
    part["div_id"][:10] = -1
    Dq, _ap, _pn = _design(part, keep)
    assert Dq.subrace_from == "pack"
    assert len(set(Dq.subrace[:10].tolist()) & set(Dq.subrace[10:].tolist())) <= 1


# ------------------------------------------------------------------ #
# the joint solve
# ------------------------------------------------------------------ #

def test_the_joint_solve_keeps_the_broken_divisions_out_of_the_day(monkeypatch):
    cols, keep, wrong, mid = _pack()
    D, ap, pn, y, off = _solve(cols, keep, False, monkeypatch)
    race_m = int(np.unique(D.race[mid])[0])
    assert np.unique(D.race[mid]).size == 1           # one venue-day race
    assert off["race_effect"][race_m] > 0.04          # the bug, reproduced
    D, ap, pn, y, on = _solve(cols, keep, True, monkeypatch)
    assert abs(on["race_effect"][race_m]) < 0.02, on["race_effect"][race_m]
    assert np.array_equal(on["race_day_div_flag"].astype(bool), wrong)
    tab = on["race_day_div_table"]
    assert tab.shape == (3, len(js.DIVISION_TABLE_COLS))
    # the leave-self-out day the joint path's ratings carry: ~0 on every
    # correct Midlothian row, and the venue-day's own u on the flagged ones
    u_loo, info = js.raceEffectLeaveOneOut(on, D, y)
    good = mid & ~wrong
    assert np.abs(u_loo[good]).max() < 0.02
    assert np.allclose(u_loo[wrong], on["race_effect"][race_m])
    assert info["max_conditional_gap"] < 0.01
    # and the correct runners are not over-credited: their abilities are
    # unmoved by the broken divisions (the guarded solve against the bug)
    a_err_on = np.abs(on["ability"] - off["ability"])
    assert np.median(a_err_on) < 0.01


def test_a_world_without_a_broken_division_solves_bit_for_bit(monkeypatch):
    cols, keep, wrong, mid = _pack(broken=False)
    assert not wrong.any()
    _D, _ap, _pn, _y, off = _solve(cols, keep, False, monkeypatch)
    _D, _ap, _pn, _y, on = _solve(cols, keep, True, monkeypatch)
    assert on["race_day_div_flag"] is None and on["race_day_div_offset"] is None
    for k in ("theta", "race_effect", "ability", "delta", "weights"):
        assert np.array_equal(on[k], off[k]), k


# ------------------------------------------------------------------ #
# the bracket refit: the day production's ratings carry
# ------------------------------------------------------------------ #

def _bracket(cols, keep, guard, monkeypatch):
    D, ap, pn, y, out = _solve(cols, keep, guard, monkeypatch)
    with contextlib.redirect_stdout(io.StringIO()):
        rj.bracketDifficulties(out, D, cols, keep, y, ap, pn, window=60, top=1.0)
    return D, ap, pn, out


def test_the_bracket_refit_keeps_the_broken_divisions_out_of_the_day(monkeypatch):
    cols, keep, wrong, mid = _pack()
    D, ap, pn, off = _bracket(cols, keep, False, monkeypatch)
    good = mid & ~wrong
    assert np.median(off["race_effect_row_bracket"][good]) > 0.04     # the bug
    D, ap, pn, on = _bracket(cols, keep, True, monkeypatch)
    u_good = on["race_effect_row_bracket"][good]
    assert np.abs(u_good).max() < 0.02, (np.median(u_good), np.abs(u_good).max())
    assert np.array_equal(on["race_day_div_flag_bracket"].astype(bool), wrong)
    # ★ TASK 2: the three named, with the distance that reconciles them
    rows, src = rj.raceDaySuspectRows(on, D, cols, keep, ap, pn)
    assert src == "bracket" and len(rows) == 3
    for r in rows:
        assert r["meet_id"] == int(cols["meet_id"][mid][0])
        assert r["distance_m"] == pytest.approx(3218.0)
        assert r["reconciled_m"] == pytest.approx(5000.0, rel=0.06), r
        # distance_pin's snap refuses 4828 vs 5000 when both are within its
        # 8% (snapStandard: a coin flip dressed as an answer)
        assert r["snapped_m"] in (None, 5000.0)
        print(f"  reconciled {r['reconciled_m']:.0f} m, snapped {r['snapped_m']}")
        assert r["division_day"] - r["race_u"] > js.RACE_DAY_CAP
    assert sum(r["n_rows"] for r in rows) == 344
    lines = rj.raceDaySuspectSummary(rows, src, D.n)
    assert "3 divisions (344 rows" in lines[0]


def test_the_bracket_refit_without_a_broken_division_is_bit_for_bit(monkeypatch):
    cols, keep, _wrong, _mid = _pack(broken=False)
    _D, _ap, _pn, off = _bracket(cols, keep, False, monkeypatch)
    _D, _ap, _pn, on = _bracket(cols, keep, True, monkeypatch)
    for k in ("race_effect_row_bracket", "race_effect", "ability", "delta"):
        assert np.array_equal(on[k], off[k]), k
    _D2, ap, pn, _y, _o = _solve(cols, keep, True, monkeypatch)
    rows, _src = rj.raceDaySuspectRows(on, _D2, cols, keep, ap, pn)
    assert rows == []


# ------------------------------------------------------------------ #
# the table and the read-only listing (scratch Postgres)
# ------------------------------------------------------------------ #

def test_the_table_is_written_and_listed_biggest_first(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    import datetime as _dt
    import psycopg2
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    import database
    import speed_ratings_db as sdb
    import joint_golive as jg
    import diag_race_day_suspects as diag
    cx = psycopg2.connect(dsn)

    @contextlib.contextmanager
    def _conn():
        yield cx
    monkeypatch.setattr(database, "getConn", _conn)
    monkeypatch.setattr(sdb, "loadCanonicalNames", lambda: {"4242": "Midlothian ISD MPC"})
    day = _dt.date(2026, 8, 27)
    base = dict(cell_key="XC:4242:d3200", race_date=day, sport="XC", meet_id=None,
                div_id=None, distance_m=3218.0, race_u=0.002, centre=0.01,
                log_excess=0.47, reconciled_m=4990.0, snapped_m=None, pool="hs_m",
                division_source="proxy", source="bracket")
    rows = [dict(base, sample_result_id=1, n_rows=115, division_day=0.47, excess=0.468),
            dict(base, sample_result_id=2, n_rows=114, division_day=0.46, excess=0.458),
            dict(base, sample_result_id=3, n_rows=40, division_day=0.15, excess=0.148,
                 cell_key="XC:4242:d5000", distance_m=5000.0)]
    try:
        jg.writeSuspectDivisions(rows)
        cur = cx.cursor()
        cur.execute("""
            CREATE TEMP TABLE results (result_id bigint, meet_id bigint, div_id bigint);
            CREATE TEMP TABLE results_tf (result_id bigint, meet_id bigint, div_id bigint);
            CREATE TEMP TABLE meets (div_id bigint, meet_id bigint, meet_name text,
                                     division text, source text);
            CREATE TEMP TABLE meets_tfrrs (meet_id bigint, meet_name text, sport text,
                                           division_distances jsonb);
            CREATE TEMP TABLE meets_tf (meet_id bigint, div_id bigint, meet_name text,
                                        event_short text);
            INSERT INTO results VALUES (1, 77, 701), (2, 77, 702), (3, 77, 703);
            INSERT INTO meets VALUES
                (701, 77, 'James Smith HS Invitational', 'Varsity Boys 2 Mile', 'anet'),
                (702, 77, 'James Smith HS Invitational', 'JV Boys 2 Mile', 'anet'),
                (703, 77, 'James Smith HS Invitational', 'Freshman Boys', 'anet');
        """)
        got = diag.fetch(cur)
        assert [r["sample_result_id"] for r in got] == [1, 2, 3]       # biggest first
        assert got[0]["course_name"] == "XC:Midlothian ISD MPC"
        assert got[0]["meet_id"] == 77 and got[0]["div_id"] == 701
        assert got[0]["division"] == "Varsity Boys 2 Mile"
        text = "\n".join(diag.render(got))
        assert "3 divisions (269 rows) on 1 race day," in text
        assert "James Smith HS Invitational / Varsity Boys" in text
        cx.rollback()
    finally:
        cur = cx.cursor()
        cur.execute("DROP TABLE IF EXISTS race_day_suspect_division")
        cx.commit()
        cx.close()
