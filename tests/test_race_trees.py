"""model/race_trees.py: the scoring pieces, and an end-to-end fit on a
synthetic table where season openers move with a known input -- the trees
must find it and beat the ratings on order. No database.

    python -m pytest -q tests/test_race_trees.py
"""
import datetime
import math
import os
import subprocess
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
import race_rows as RR                                       # noqa: E402
import race_trees as T                                       # noqa: E402


def test_pairs_and_slices():
    assert T.pairShare(np.array([1., 2, 3]), np.array([10., 20, 30])) == 1.0
    assert T.pairShare(np.array([1., 1]), np.array([10., 20])) == 0.5
    assert T.pairShare(np.array([1., 2]), np.array([10., 10])) is None
    assert T.raceSlices(np.array([0, 0, 1, 2, 2])) == [(0, 2), (2, 3), (3, 5)]
    assert T.bandOf(0, 7) == T.BANDS[0] and T.bandOf(1, 200) == T.BANDS[3]


def _table(path, course=None):
    rng = np.random.default_rng(0)
    F = list(RR.FEATURES)
    fi = {f: i for i, f in enumerate(F)}
    out = {k: [] for k in ("X", "y", "base", "y_abs", "base_abs", "race", "day", "tfrrs")}
    d0 = datetime.date(2019, 9, 1).toordinal()
    d1 = datetime.date(2024, 9, 1).toordinal()
    for k in range(900):
        day = d0 + int(rng.integers(0, 1500)) if k < 700 else d1 + int(rng.integers(0, 80))
        n = int(rng.integers(15, 40))
        X = np.full((n, len(F)), np.nan, np.float32)
        form = rng.normal(0, 0.05, n)
        opener = (rng.random(n) < 0.2).astype(float)
        trend = rng.normal(0, 0.03, n)
        X[:, fi["form_ln"]] = 4.6 - form
        X[:, fi["opener"]] = opener
        X[:, fi["prev_late_minus_early"]] = trend
        X[:, fi["last_days"]] = np.where(opener > 0, 200, 7)
        X[:, fi["distance"]] = 5000.0
        X[:, fi["level"]] = 2
        t = form - opener * trend + rng.normal(0, 0.015, n)
        out["X"].append(X)
        out["y"].append(t - t.mean())
        out["base"].append(form - form.mean())
        out["y_abs"].append(7.0 + t)
        out["base_abs"].append(7.0 + form)
        out["race"] += [k] * n
        out["day"] += [day] * n
        out["tfrrs"] += [0] * n
    np.savez_compressed(
        path, X=np.vstack(out["X"]),
        **{k: np.concatenate(out[k]).astype(np.float32) for k in ("y", "base", "y_abs", "base_abs")},
        race=np.array(out["race"], np.int32), day=np.array(out["day"], np.int32),
        tfrrs=np.array(out["tfrrs"], np.int8), pid=np.arange(len(out["race"])),
        features=np.array(F), race_keys=np.array([str(i) for i in range(900)]),
        args=np.array(["synthetic"]),
        **({"course": np.array([course(k) for k in out["race"]], np.int64)} if course else {}))


def test_the_trees_find_the_openers_and_beat_the_ratings(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_trees.py"),
                          "--rows", str(path)], capture_output=True, text=True, timeout=300)
    assert got.returncode == 0, got.stderr[-2000:]
    line = next(ln for ln in got.stdout.splitlines() if "model - ratings, pairs" in ln)
    gain = float(line.split(":")[1].split("points")[0])
    assert gain > 0.5, got.stdout


def test_without_leaves_the_named_inputs_and_prefixes_out(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    t = T.loadTable(str(path), "2024-08-01", own=False, without=["opener", "prev_"])
    assert "opener" not in t["feats"] and not any(f.startswith("prev_") for f in t["feats"])
    assert "form_ln" in t["feats"] and "opener" in t["feats_all"]


def test_the_course_level_record_reads_only_earlier_races_at_the_course(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path), course=lambda k: 7 if k % 2 else -1)
    t = T.loadTable(str(path), "2024-08-01", own=False)
    fi, X, day, race = t["fidx"], t["X_all"], t["day"], t["race"]
    assert not any(f.startswith("clv_") for f in t["feats"])      # never the runner trees
    at7 = (race % 2 == 1)
    n = X[:, fi["clv_n"]]
    assert np.all(np.isnan(n[~at7]))                              # no course, no record
    first = day[at7].min()
    assert np.all(n[at7 & (day == first)] == 0)
    # a race reads exactly the earlier days' races at the course
    k = np.flatnonzero(at7)[-1]
    earlier = len(np.unique(race[at7 & (day < day[k])]))
    assert n[k] == earlier
    assert T.courseLevelKey([7, -1, 7], [5000.0, 5000.0, np.nan]).tolist()[1:] == [-1, -1]


def test_field_inputs_rebuilt_from_another_clock():
    fi = {"rank_pct": 0, "gap_to_median": 1, "gap_ahead": 2, "gap_behind": 3}
    X = np.zeros((3, 4))
    T.fieldFromBase(X, np.array([0.02, -0.01, 0.0]), [(0, 3)], fi)
    assert list(X[:, 0]) == [1.0, 0.0, 0.5]
    assert X[1, 1] == pytest.approx(-0.01) and np.isnan(X[1, 2]) and np.isnan(X[0, 3])
    assert X[0, 2] == pytest.approx(0.02)


def test_no_ratings_needs_raw_times_and_is_never_saved(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    for extra in (["--no-ratings"], ["--no-ratings", "--out", str(tmp_path / "x.pkl")]):
        got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_trees.py"),
                              "--rows", str(path)] + extra, capture_output=True, text=True)
        assert got.returncode != 0


def test_own_record_reads_only_earlier_days_and_carries_to_the_page():
    pid = np.array([7, 7, 7, 8, 7])
    day = np.array([100, 110, 110, 105, 130])          # two races of 7's on day 110
    resid = np.array([0.02, 0.04, -0.01, 0.5, 0.0])
    champ = np.array([False, True, False, False, False])
    f, states = T.ownHistory(pid, day, resid, champ)
    fi = {k: i for i, k in enumerate(T.OWN_FEATURES)}
    assert f[0, fi["own_n"]] == 0                       # the first race knows nothing
    assert f[1, fi["own_n"]] == 1 and f[2, fi["own_n"]] == 1     # not each other's
    assert f[1, fi["own_last"]] == pytest.approx(0.02)
    assert f[4, fi["own_n"]] == 3 and f[4, fi["own_champ_n"]] == 1
    assert f[4, fi["own_mean"]] == pytest.approx(0.05 / (3 + T.OWN_SHRINK))
    assert f[3, fi["own_n"]] == 0                       # 8's own 0.5 never leaks to 7
    pack = T.ownPack(states, 0)
    idx = {int(p): i for i, p in enumerate(pack["pid"])}
    again = T.ownFeatures(T.ownState(pack, idx, 7), 130)
    assert again[fi["own_n"]] == 4 and again[fi["own_last"]] == pytest.approx(0.0)
    assert T.ownState(pack, idx, 99) is None


def test_records_in_context_read_only_earlier_real_results_of_the_same_context():
    keys = T.ctxKeys(pid=[1, 1, 1, 1, 2], course=[5, 5, 9, 5, 5],
                     cell=[0.03, 0.03, 0.10, 0.03, 0.03], has_cell=[1, 1, 1, 1, 1],
                     distance=[5000, 5000, 5000, 8000, 5000], temp=[10, 26, 26, np.nan, 10])
    assert keys["crs"][0] == keys["crs"][1] != keys["crs"][2]      # same runner, same course
    assert keys["crs"][4] != keys["crs"][0]                         # another runner there
    assert keys["dif"][2] != keys["dif"][0] and keys["hot"][3] == -1
    day = np.array([100, 110, 120, 130, 100])
    real = np.array([True, True, True, False, True])     # row 3 is a forecast copy
    resid = np.array([0.02, 0.04, -0.03, 0.5, 0.9])
    f, pack = T.ctxRecords(keys["crs"], day, day, real, resid, 0)
    n, mean, last = f[:, 0], f[:, 1], f[:, 2]
    assert n[0] == 0 and n[1] == 1 and last[1] == pytest.approx(0.02)
    assert n[3] == 2 and mean[3] == pytest.approx(0.06 / (2 + T.OWN_SHRINK))
    assert n[4] == 0                                     # runner 2 never sees runner 1
    assert T.ctxLookup(pack, int(keys["crs"][0]))[0] == 2         # the copy never counts
    assert T.ctxLookup(pack, 123456789)[0] == 0


def test_a_race_miss_takes_the_first_cause_the_data_shows():
    f = {"miss": 0.2, "n": 40, "distance": 5000.0, "known": True, "seen": [3000.0, 5000.0],
         "mixed": 0.0, "name": "Big Invitational Varsity"}
    assert T.missCause(f) == "no cause found"
    assert T.missCause(dict(f, distance=float("nan"))) == "no distance on file"
    assert T.missCause(dict(f, distance=4828.0)) == "a distance new to this course"
    assert T.missCause(dict(f, distance=3010.0)) == "no cause found"     # the MS race there
    assert T.missCause(dict(f, name="Canterbury Time Trial")) == "a name that is not a normal race"
    assert T.missCause(dict(f, name="Fast Track 800")) == "a name that is not a normal race"
    assert T.missCause(dict(f, mixed=0.3)) == "mixed levels in one field"
    assert T.missCause(dict(f, seen=[])) == "a course with no history"
    assert not T.isOddRace("Santa Clara vs Saint Mary's Duel Meet")
    lines = T.missCauses([f, dict(f, miss=0.01)])
    assert "100.0%" in lines[-1] and "2" in lines[-1]


def test_drop_odd_leaves_odd_training_races_out_and_scores_every_val_race(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    z = dict(np.load(path))
    z["race_names"] = np.array(["Time Trial" if i % 3 == 0 else "Invitational"
                                for i in range(900)])
    np.savez(path, **z)
    t = T.loadTable(str(path), "2024-08-01", own=False, drop_odd=True)
    assert not np.any((t["race"] % 3 == 0) & ~t["val"])
    assert np.any((t["race"] % 3 == 0) & t["val"])


def test_the_sequence_reads_earlier_real_results_only_oldest_first():
    # one runner (pid 5) in races 0..3 on days 10, 20, 30, 30; race 3 is a
    # forecast copy made 15 days ahead of day 30; pid 6 runs beside them
    race = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    pid = np.array([5, 6, 5, 6, 5, 6, 5, 6])
    day = np.array([10, 10, 20, 20, 30, 30, 30, 30])
    lead = np.array([0, 0, 0, 0, 0, 0, 15, 15])
    y = np.array([-.01, .01, .02, -.02, 0, 0, .03, -.03])
    base = np.zeros(8)
    X = np.zeros((8, 1))
    ev, idx = T.seqTable(pid, day, lead, race, y, base, X, {"x": 0}, np.zeros(8), k=3)
    assert len(ev["day"]) == 6                                   # the copy is no result
    got = [[int(ev["day"][j]) if j >= 0 else None for j in row] for row in idx]
    assert got[0] == [None, None, None]
    assert got[4] == [None, 10, 20]                              # never its own day
    assert got[6] == [None, None, 10]                            # cut at day 15
    assert ev["F"][idx[4][-1], 0] == pytest.approx(0.02)         # resid of day 20
    assert ev["F"][idx[4][-1], 1] == 1.0                         # last of two that day


def test_seq_member_runs_on_the_synthetic_table(tmp_path):
    pytest.importorskip("torch")
    path = tmp_path / "rows.npz"
    _table(str(path))
    got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_blend.py"),
                          "--rows", str(path), "--members", "hgb,seq", "--seq-epochs", "1"],
                         capture_output=True, text=True, timeout=600)
    assert got.returncode == 0, got.stderr[-2000:]
    assert "seq: fitted" in got.stdout and "seq " in got.stdout.split("blend weights")[1]


def test_a_distance_is_read_against_what_the_course_ran_before():
    d = T.courseDistances([1, 1, 1, 2, -1], [10, 20, 30, 10, 10],
                          [4828.0, 3000.0, 4830.0, 5000.0, 5000.0],
                          np.array([True, True, True, False, True]))
    assert d == {1: [(4830.0, 10), (3000.0, 20)]}                 # course 2 was no real race
    assert T.distVsCourse(d[1], 5000.0, 15) == pytest.approx(math.log(5000 / 4830))
    assert T.distVsCourse(d[1], 3010.0, 25) == pytest.approx(math.log(3010 / 3000))
    assert math.isnan(T.distVsCourse(d[1], 5000.0, 10))           # nothing before the cut
    assert math.isnan(T.distVsCourse(None, 5000.0, 99))


def test_ranges_per_runner_stay_honest_where_one_width_per_band_is_not():
    rng = np.random.default_rng(0)
    n = 60_000
    X = rng.random((n, 3)).astype(np.float32)
    sd = 0.005 + 0.04 * X[:, 0]                      # some runners swing far more
    em = rng.normal(0, 1, n) * sd
    band = np.full(n, np.percentile(np.abs(em), 68.27))
    even = np.arange(n) % 2 == 0
    _ps, _z, lines = T.spreadReport(X, em, em, even, np.ones(n, dtype=bool), band, 0.0)
    per = [float(v.rstrip("%")) for v in lines[3].split()[2:]]
    bnd = [float(v.rstrip("%")) for v in lines[4].split()[2:]]
    assert max(abs(v - 68) for v in per) < 6
    assert bnd[0] > 90 and bnd[-1] < 50
