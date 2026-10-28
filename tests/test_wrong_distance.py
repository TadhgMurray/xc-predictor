"""engine/wrong_distance.py: the finder of cross country races stored at the
wrong distance. Pure parts here; the whole pass on a synthetic corpus on a
scratch Postgres when XCP_TWIN_TEST_DSN is set."""
import math
import os
import random
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "tests"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import _env  # noqa: E402,F401
import wrong_distance as WD  # noqa: E402


def _x(true, listed):
    """the field excess a race of `true` metres stored at `listed` shows"""
    return WD.K * math.log(true / listed)


def _div(listed, x, n=30, share=0.95, name="Varsity Girls", pace=None):
    return {"listed": listed, "n_ref": n, "res": x,
            "sh_fast": share if (x or 0) < 0 else 0.0, "sh_slow": share if (x or 0) > 0 else 0.0,
            "div_name": name, "n_rows": n, "pace_pct": pace}


# ---------------------------------------------------------------- pure

def test_a_5k_listed_as_6k_is_pinned_down_with_corroboration():
    v = WD.judge(_div(6000, _x(5000, 6000)),
                 {"venue_meets": [0.01, -0.01, 0.0], "sibs": [0.0, 0.01],
                  "sib_labels": [5000.0], "venue_labels": [5000.0]})
    assert (v["kind"], v["direction"], v["proposed"]) == ("wrong_distance", "down", 5000.0)
    assert v["action"] == "pin" and v["confidence"] == "high"


def test_confidence_does_not_gate_a_downward_pin():
    # Miner 2007's shape: one race, no siblings, no venue history. Pinned,
    # and since nothing says 5000 rather than 3 miles, to the lower of the two
    v = WD.judge(_div(6000, _x(5000, 6000)), {})
    assert (v["proposed"], v["action"], v["confidence"]) == (4828.0, "pin", "low")
    # but not on a handful of runners
    v = WD.judge(_div(6000, _x(5000, 6000), n=8), {})
    assert v["action"] == "review" and "only 8 runners" in v["evidence"]


def test_never_a_distance_longer_than_the_times_say():
    # implied 4700: 4828 sits 2.7% above it, and nothing real is just below
    v = WD.judge(_div(6000, _x(4700, 6000)), {"venue_meets": [0.0, 0.0]})
    assert v["action"] == "review" and "longer than the times say" in v["evidence"]
    # implied 4750: 4828 is within the estimate's noise
    v = WD.judge(_div(6000, _x(4750, 6000)), {"venue_meets": [0.0, 0.0]})
    assert (v["action"], v["proposed"]) == ("pin", 4828.0)
    # the venue's own 5000 wins a tie only when the times reach it
    v = WD.judge(_div(6000, _x(4880, 6000)), {"venue_labels": [5000.0]})
    assert v["proposed"] == 4828.0


def test_label_differences_are_never_acted_on():
    # Mt. SAC: 2.93 mi (4715) stored as its 3 miles (4828), or as 5000
    for true, listed in ((4715, 4828), (4715, 5000), (4828, 5000), (3000, 3218),
                         (6000, 6437)):
        v = WD.judge(_div(listed, _x(true, listed) - 0.06),   # and a fast day on top
                     {"venue_meets": [0.0, 0.0], "venue_labels": [4715.0]})
        assert v["action"] in ("ignore", "review"), (true, listed, v)
    # a championship field 3% fast at a true 4828 is nothing at all
    v = WD.judge(_div(4828, -0.03), {"venue_meets": [0.0, 0.01]})
    assert v["kind"] is None


def test_a_hard_course_is_the_course_not_the_label():
    # every year at this venue reads +9%: this race at +10% is normal here
    v = WD.judge(_div(5000, 0.10), {"venue_meets": [0.09, 0.08, 0.10]})
    assert v["kind"] is None
    # the same day 18% fast against a venue that is normally +9%: a label
    v = WD.judge(_div(6000, _x(5000, 6000) + 0.09),
                 {"venue_meets": [0.09, 0.08, 0.10], "venue_labels": [5000.0],
                  "sibs": [0.09]})
    assert v["proposed"] == 5000.0 and v["direction"] == "down"


def test_a_venue_that_reads_fast_every_day_is_left_to_its_difficulty():
    # every day here reads 20% fast. The solve's difficulty for the venue
    # learns that, and a pin would only take the races' course away -- so
    # the last run's ratings decide (owner, 2026-10-25: "should this not
    # just have the difficulty?")
    ctx = {"venue_meets": [-0.20, -0.19, -0.21]}
    d = _div(5000, -0.20)
    d.update(rating_gap=0.01, rating_n=30)          # ratings normal: absorbed
    v = WD.judge(d, ctx)
    assert (v["kind"], v["action"]) == ("venue_wide", "review")
    assert "difficulty takes it" in v["evidence"]
    d.update(rating_gap=None, rating_n=0)           # no ratings: can't tell
    assert WD.judge(d, ctx)["action"] == "review"
    d.update(rating_gap=0.15, rating_n=30)          # a thin venue still reads high
    v = WD.judge(d, ctx)
    assert (v["proposed"], v["action"]) == (4000.0, "pin")
    assert "does not take it" in v["evidence"]
    # the same venue reading 20% SLOW is a hard course or a short label:
    # never raised, at most listed
    v = WD.judge(_div(5000, 0.20), {"venue_meets": [0.20, 0.19, 0.21]})
    assert v["action"] == "review"


def test_upward_is_never_a_pin():
    # men's 8K stored at the women's 5K: the men read slow (underrated, safe)
    v = WD.judge(_div(5000, _x(8000, 5000), name="Men"),
                 {"venue_meets": [0.0, 0.01], "sib_labels": [8000.0],
                  "venue_labels": [8000.0], "sibs": [0.0]})
    assert v["direction"] == "up" and v["proposed"] == 8000.0
    assert v["action"] == "pin-up?"      # review only, never written by itself
    v = WD.judge(_div(5000, _x(8000, 5000), name="Men"), {"sibs": [0.0, 0.0]})
    assert v["action"] in ("novote", "review")


def test_ambiguous_candidates_take_the_lower_distance():
    assert WD.snap(4900.0, WD.candidates()) == 4828.0
    assert WD.snap(3100.0, WD.candidates()) == 3000.0
    assert WD.snap(4400.0, WD.candidates()) is None
    # the venue's own label is a candidate
    assert WD.snap(4720.0, WD.candidates([4715.0])) == 4715.0


def test_a_split_field_is_a_combined_division():
    random.seed(1)
    rows = ([(random.gauss(_x(4000, 5000), 0.02), "f", "hs") for _ in range(12)]
            + [(random.gauss(0.0, 0.02), "m", "hs") for _ in range(15)])
    mix = WD.mixVerdict(rows, 5000.0, 0.0, WD.candidates())
    assert mix and mix["by"] == ("gender", ["f"], ["m"])
    assert [c["snap"] for c in mix["clusters"]] == [4000.0, 5000.0]
    v = WD.judge(_div(5000, -0.05), {"mix": mix})
    assert (v["kind"], v["action"], v["confidence"]) == ("combined", "split", "high")
    # one smear of a field is not two races
    smear = [(-0.25 + 0.5 * i / 26, "f", "hs") for i in range(27)]
    assert WD.mixVerdict(smear, 5000.0, 0.0, WD.candidates()) is None
    # nor is a field with three outliers among thirty
    few = [(0.0, "m", "hs")] * 27 + [(-0.3, "m", "hs")] * 3
    assert WD.mixVerdict(few, 5000.0, 0.0, WD.candidates()) is None


def test_relays_are_never_judged_as_one_race():
    v = WD.judge(_div(6437, -0.6, name="4x1 Mile Relay"), {})
    assert v["kind"] == "relay" and v["action"] == "review"


def test_a_field_without_other_races_speaks_only_through_its_pace():
    d = _div(5000, None, n=0, pace=0.002)
    d["n_rows"] = 40
    v = WD.judge(d, {})
    assert v["kind"] == "pace_outlier" and v["action"] == "review"
    d["pace_pct"] = 0.5
    assert WD.judge(d, {})["kind"] is None


def test_existing_overrides_are_judged_by_the_field():
    # Hole in the Wall 2022: a true 5000 "corrected" to 4506. It underrates:
    # a generated one is replaced, a hand-typed one is listed for the owner
    assert WD.overrideVerdict(5000, 4506, 5000.0, 300)[0] == "replace"
    assert WD.overrideVerdict(5000, 4506, 5000.0, 300, hand=True)[0] == "list"
    # longer than the times say overrates: replaced whoever typed it
    act, why = WD.overrideVerdict(6000, 5000, 4000.0, 40, hand=True)
    assert act == "replace" and "overrates" in why
    act, why = WD.overrideVerdict(5000, 8000, 5000.0, 40, hand=True)
    assert act == "replace" and "RAISES" in why
    # a right one
    assert WD.overrideVerdict(6000, 5000, 5050.0, 40, hand=True)[0] == "keep"
    assert WD.overrideVerdict(5000, 4000, None, 2)[0] == "unjudged"


def test_pace_percentile():
    pc = [(0.01, 1.0), (0.5, 2.0), (0.99, 3.0)]
    assert WD.pacePct(0.5, pc) == 0.01 and WD.pacePct(9, pc) == 0.99
    assert abs(WD.pacePct(2.5, pc) - 0.745) < 1e-9


def test_venue_context_leaves_the_meet_out():
    rows = [("v", 5000, "anet", 1, "hs_f", "2024-09-01", -0.19, 7.0),
            ("v", 5000, "anet", 2, "hs_f", "2023-09-01", 0.01, 7.2),
            ("v", 5000, "anet", 3, "hs_f", "2022-09-01", 0.0, 7.21),
            ("v", 4000, "anet", 4, "hs_f", "2022-09-01", 0.0, 7.2),
            ("v", 4000, "anet", 5, "hs_f", "2021-09-01", 0.0, 7.2)]
    vi = WD.venueIndex(rows)["v"]
    vm, labels, raw = WD.venueContext(vi, 5000, ("anet", 1), "hs_f", "2024")
    assert sorted(vm) == [0.0, 0.01] and labels == [4000.0] and sorted(raw) == [7.2, 7.21]


# ---------------------------------------------------------------- database

def _corpus(seed=7):
    """A synthetic corpus with known faults. Returns (sql rows by table, the
    faults {name: (meet, div)})."""
    rnd = random.Random(seed)
    venues = {"A": 0.0, "B": 0.06, "C": -0.03, "D": 0.0, "E": 0.0, "F": 0.0}
    gps = {v: (30.0 + i, -90.0 - i) for i, v in enumerate(venues)}
    people = []
    pid = 0
    for g in ("m", "f"):
        for _ in range(260):
            pid += 1
            base = (16.5 if g == "m" else 19.5) * 60
            people.append((pid, g, base * math.exp(rnd.gauss(0, 0.07))))
    meets, divs = [], []          # (meet, venue, date), (meet, div, listed, true, gender, name)
    div_id = 1000
    for yr in (2023, 2024):
        for wk in range(10):
            for v in venues:
                meet = len(meets) + 1
                date = f"{yr}-09-{wk + 1:02d}"
                meets.append((meet, v, date))
                listed = {"E": 5000.0}.get(v, 5000.0)
                for g in ("m", "f"):
                    div_id += 1
                    divs.append([meet, div_id, listed, listed, g, f"Varsity {'Boys' if g == 'm' else 'Girls'}"])
    faults = {}
    by = {(d[0], d[4]): d for d in divs}
    m = next(mt for mt in meets if mt[1] == "A" and mt[2] == "2024-09-05")[0]
    by[(m, "f")][2] = 6000.0                              # a 5K stored as 6K
    faults["wrong6k"] = (m, by[(m, "f")][1])
    m = next(mt for mt in meets if mt[1] == "C" and mt[2] == "2024-09-03")[0]
    by[(m, "m")][3] = 8000.0                              # men's 8K stored at 5K
    faults["men8k"] = (m, by[(m, "m")][1])
    m = next(mt for mt in meets if mt[1] == "D" and mt[2] == "2024-09-10")[0]
    for g in ("m", "f"):                                  # a championship, 3% fast
        by[(m, g)].append(-0.03)
    faults["champ"] = (m, by[(m, "f")][1])
    # E: every race there is a 2.93 mile course stored as 5000
    for d in divs:
        if next(mt for mt in meets if mt[0] == d[0])[1] == "E":
            d[3] = 4715.0
    faults["small"] = (next(mt for mt in meets if mt[1] == "E")[0], None)
    # a combined division at F: girls ran 4000 inside the boys' 5000
    m = next(mt for mt in meets if mt[1] == "F" and mt[2] == "2024-09-06")[0]
    faults["combined"] = (m, by[(m, "m")][1])
    comb_girls = by[(m, "f")]
    comb_girls.append("merge")
    # results: each runner in ~7 meets a season
    results = []
    rid = 0
    for yr in (2023, 2024):
        ms = [mt for mt in meets if mt[2].startswith(str(yr))]
        for p, g, ab in people:
            for mt in rnd.sample(ms, 7):
                d = by[(mt[0], g)]
                true = d[3]
                extra = d[6] if len(d) > 6 and isinstance(d[6], float) else 0.0
                div = d[1]
                if len(d) > 6 and d[6] == "merge":
                    true, div = 4000.0, by[(mt[0], "m")][1]
                t = ab * (true / 5000.0) ** WD.K * math.exp(
                    venues[mt[1]] + extra + rnd.gauss(0, 0.02))
                rid += 1
                results.append((rid, mt[0], div, "anet", p, p, round(t, 1), mt[2],
                                f"hs_{g}"))
    meets_rows = [(d[1], d[0], f"Meet {d[0]}", next(mt for mt in meets if mt[0] == d[0])[1],
                   d[2], d[5]) + gps[next(mt for mt in meets if mt[0] == d[0])[1]]
                  for d in divs]
    return {"results": results, "meets": meets_rows,
            "athletes": [(p, g.upper()) for p, g, _ in people],
            "course_canonical": [(v, la, lo, i + 1) for i, (v, (la, lo)) in enumerate(gps.items())]}, faults


@pytest.fixture
def scratch():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS wd_test")
        c.execute("CREATE DATABASE wd_test")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + " dbname=wd_test"
    cx = psycopg2.connect(test_dsn)
    try:
        yield cx
    finally:
        cx.close()
        with admin.cursor() as c:
            c.execute("DROP DATABASE IF EXISTS wd_test")
        admin.close()


def test_the_finder_on_a_synthetic_corpus(scratch):
    from psycopg2.extras import execute_values
    data, faults = _corpus()
    with scratch.cursor() as c:
        c.execute("""
            CREATE TABLE results (result_id bigint, meet_id bigint, div_id bigint, source text,
                person_id bigint, athlete_id bigint, time_seconds real, date text,
                rating_pool text, grade text, athlete_name text, speed_rating real);
            CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text, course_name text,
                distance real, division text, gps_lat real, gps_long real,
                source text DEFAULT 'anet');
            CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text,
                venue_name text, division_distances jsonb, gps_lat real, gps_long real);
            CREATE TABLE athletes (athlete_id bigint, gender text, first_name text, last_name text);
            CREATE TABLE course_canonical (course_name text, gps_lat double precision,
                gps_long double precision, canonical_id int);
            CREATE TABLE dist_override (meet_id bigint, div_id bigint,
                distance double precision, dict_name text);
        """)
        execute_values(c, "INSERT INTO results (result_id, meet_id, div_id, source, person_id,"
                          " athlete_id, time_seconds, date, rating_pool) VALUES %s",
                       data["results"])
        c.execute("UPDATE results SET speed_rating = 100 * exp(random() * 0.02)")
        execute_values(c, "INSERT INTO meets (div_id, meet_id, meet_name, course_name, distance,"
                          " division, gps_lat, gps_long) VALUES %s", data["meets"])
        execute_values(c, "INSERT INTO athletes (athlete_id, gender) VALUES %s", data["athletes"])
        execute_values(c, "INSERT INTO course_canonical VALUES %s", data["course_canonical"])
        # a bad old override: a true 5000 "corrected" to 4000
        c.execute("SELECT meet_id, div_id FROM meets WHERE course_name = 'B' ORDER BY div_id LIMIT 1")
        bad = c.fetchone()
        c.execute("INSERT INTO dist_override VALUES (%s, %s, 4000, 'DIST_PROPOSED')", bad)
        pools, has_ovr = WD.build(c, "64MB")
        assert has_ovr
        verdicts = WD.gather(c, pools, has_ovr)
        got = {(int(v["div"]["meet_id"]), int(v["div"]["div_id"])): v for v in verdicts}

        v = got[faults["wrong6k"]]
        assert (v["kind"], v["proposed"], v["action"]) == ("wrong_distance", 5000.0, "pin"), v

        v = got[faults["men8k"]]
        assert v["direction"] == "up" and v["proposed"] == 8000.0
        assert v["action"] in ("novote", "review", "pin-up?"), v

        v = got[faults["combined"]]
        assert v["kind"] == "combined", v
        assert "gender" in v["evidence"], v

        # the championship 3% fast and the 2.93 mile course are never acted on
        champ = got.get(faults["champ"])
        assert champ is None or champ["action"] != "pin", champ
        small = [v for k, v in got.items() if k[0] == faults["small"][0]]
        assert all(v["action"] != "pin" for v in small)

        v = got[tuple(bad)]
        assert v["override_verdict"][0] == "replace", v
        assert "-> the listed 5000" in v["override_verdict"][1], v

        # nothing else in the corpus is pinned
        pinned = {k for k, v in got.items() if v["action"] == "pin"}
        assert pinned == {faults["wrong6k"]}, [got[k]["evidence"] for k in pinned]

        WD.report(verdicts, top=10)
        WD.explain(c, *faults["wrong6k"], "anet")

        # ---- the writer on the same verdicts ----
        res = WD.write(c, verdicts, allow_many=True)
        assert res and res["written"] > 0
        c.execute("SELECT meet_id, div_id, part, action, distance, status FROM distance_pin")
        pins = {(m, d, p): (a, dist, st) for m, d, p, a, dist, st in c.fetchall()}
        assert pins[faults["wrong6k"] + ("",)] == ("pin", 5000.0, "active")
        assert pins[faults["combined"] + ("",)][0] == "novote"
        assert pins[tuple(bad) + ("",)][0] == "veto"
        # a second run on the same evidence changes nothing
        again = WD.write(c, verdicts)
        assert again["written"] == 0 and again["retired"] == 0
        c.execute("SELECT count(*) FROM distance_pin_log")
        n_log = c.fetchone()[0]
        assert n_log == res["written"]
    scratch.rollback()


def _v(source="anet", meet=1, div=2, listed=6000.0, action="pin", proposed=5000.0,
       x=-0.19, kind="wrong_distance", **kw):
    v = {"div": {"source": source, "meet_id": meet, "div_id": div, "listed": listed},
         "action": action, "proposed": proposed, "x": x, "kind": kind,
         "confidence": "high", "points": 4, "evidence": "e"}
    v.update(kw)
    return v


def test_what_each_verdict_writes():
    assert WD.wants(_v()) == [("", "pin", 5000.0)]
    assert WD.wants(_v(action="novote", proposed=8000.0, x=0.5)) == [("", "novote", None)]
    assert WD.wants(_v(action="review")) == []
    # a combined division explained by gender: the fast side pinned, the whole voting on nothing
    mix = {"by": ("gender", ["f"], ["m"]),
           "clusters": [{"name": "fast", "snap": 4000.0, "implied": 4010.0},
                        {"name": "slow", "snap": 5000.0, "implied": 5000.0}]}
    assert WD.wants(_v(listed=5000.0, action="split", kind="combined", mix=mix)) == \
        [("g:f", "pin", 4000.0), ("", "novote", None)]
    # never a pin longer than the times say, never a small move, never up
    mix["clusters"][0]["implied"] = 3800.0
    assert WD.wants(_v(listed=5000.0, action="split", kind="combined", mix=mix)) == [("", "novote", None)]
    # an override the times reject, to be replaced by the listed distance
    assert WD.wants(_v(action="replace", kind="override", proposed=None,
                       override_verdict=("replace", "longer than ...; -> the listed 5000"))) == \
        [("", "veto", None)]


def test_a_pin_stays_on_less_than_it_takes_and_comes_off_when_the_evidence_goes():
    row = {"part": "", "action": "pin", "distance": 5000.0, "listed": 6000.0,
           "status": "active", "set_by": "auto"}
    key = ("anet", 1, 2, "")
    # 7% fast: not enough to write (8%), enough to keep (6%) -- but 5000 is
    # longer than the 5617 a 7% field implies? no: 5000 < 5617, kept
    up, ret, kept = WD.plan([_v(action="review", proposed=None, x=-0.07)], {key: row})
    assert (up, ret) == ([], []) and kept[0][0] == key
    # the field now reads true: retired with its reason
    up, ret, kept = WD.plan([_v(action="ignore", proposed=None, x=-0.01)], {key: row})
    assert ret and ret[0][0] == key and "reads" in ret[0][1]
    # an owner's hold is never retired
    held = dict(row, status="held", set_by="owner")
    up, ret, kept = WD.plan([_v(action="ignore", proposed=None, x=-0.01)], {key: held})
    assert ret == [] and up == []
    # refused: the writer never activates it, whatever the verdict
    refused = dict(row, status="refused", set_by="owner")
    up, ret, kept = WD.plan([_v()], {key: refused})
    assert up == [] and ret == []


def test_the_dump_reads_the_pins_first():
    rows = {(1, 2): (4000.0, "_DISTANCE_OVERRIDES_XC"), (3, 4): (4500.0, "DIST_PROPOSED"),
            (5, 6): (6000.0, "_DISTANCE_PIN_GENDER")}
    pins = [(1, 2, "pin", 5000.0, 6000.0),        # outranks the hand-typed 4000
            (3, 4, "veto", None, 5000.0),         # the proposal goes, kept apart
            (5, 6, "novote", None, 5000.0),       # an existing row, flagged
            (7, 8, "novote", None, 3218.0),       # a new row at the listed distance
            (9, 9, "none", None, None)]           # an owner's hold with nothing to apply
    got, novote, vetoed = WD.applyPins(rows, pins)
    assert got[(1, 2)] == (5000.0, "distance_pin")
    assert (3, 4) not in got and vetoed == [(3, 4, 4500.0, "DIST_PROPOSED")]
    assert got[(5, 6)] == (6000.0, "_DISTANCE_PIN_GENDER") and (5, 6) in novote
    assert got[(7, 8)] == (3218.0, "novote") and (7, 8) in novote
    assert (9, 9) not in got
