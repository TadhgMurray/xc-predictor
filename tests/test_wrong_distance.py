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


def test_the_field_alone_is_not_enough_to_pin():
    # Miner 2007's shape: one race, no siblings, no venue history
    v = WD.judge(_div(6000, _x(5000, 6000)), {})
    # nothing says 5000 rather than 3 miles: the lower of the two
    assert v["proposed"] == 4828.0 and v["action"] == "review"
    # ... plus an impossible pace for the level: still only "pin?"
    v = WD.judge(_div(6000, _x(5000, 6000), pace=0.01), {})
    assert v["action"] == "pin?"


def test_label_differences_are_never_acted_on():
    # Mt. SAC: 2.93 mi (4715) stored as its 3 miles (4828), or as 5000
    for true, listed in ((4715, 4828), (4715, 5000), (4828, 5000), (3000, 3218),
                         (6000, 6437)):
        v = WD.judge(_div(listed, _x(true, listed) - 0.06),   # and a fast day on top
                     {"venue_meets": [0.0, 0.0], "venue_labels": [4715.0]})
        assert v["action"] in ("ignore", "review"), (true, listed, v)
        assert v["action"] != "pin"
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


def test_a_venue_whose_every_race_is_off_is_listed_not_used():
    v = WD.judge(_div(5000, -0.20), {"venue_meets": [-0.20, -0.19, -0.21]})
    # the baseline is not used: the race itself is judged against nothing
    assert v["proposed"] == 4000.0 and "venue reads" in v["evidence"]
    assert v["action"] != "pin"          # the field alone, plus nothing


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
    # Hole in the Wall 2022: a true 5000 "corrected" to 4506
    assert WD.overrideVerdict(5000, 4506, 5000.0, 300)[0] == "drop?"
    # a right one
    assert WD.overrideVerdict(6000, 5000, 5050.0, 40)[0] == "keep"
    # a raise is named as one
    act, why = WD.overrideVerdict(5000, 8000, 5000.0, 40)
    assert act == "drop?" and "RAISES" in why
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
                rating_pool text, grade text, athlete_name text);
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
        assert champ is None or champ["action"] not in ("pin", "pin?"), champ
        small = [v for k, v in got.items() if k[0] == faults["small"][0]]
        assert all(v["action"] not in ("pin", "pin?") for v in small)

        v = got[tuple(bad)]
        assert v["override_verdict"][0] == "drop?", v

        # nothing else in the corpus is pinned
        pinned = {k for k, v in got.items() if v["action"] in ("pin", "pin?")}
        assert pinned == {faults["wrong6k"]}, [got[k]["evidence"] for k in pinned]

        WD.report(verdicts, top=10)
        WD.explain(c, *faults["wrong6k"], "anet")
    scratch.rollback()
