"""The Oct 23 factorial's tooling: the variant pack undoes only the tfrrs
placement, an arm runs the pipeline's own 08_golive command dry, and an
arm's ratings convert through its own tables without the live ones."""
import os
import subprocess
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def _fakeDatabase():
    if "database" not in sys.modules:
        db = types.ModuleType("database")

        def getConn(*a, **k):
            raise RuntimeError("no database in the tests")
        db.getConn = getConn
        sys.modules["database"] = db


def _pack():
    keys = ["XC:123:d8000", "XC:name:Foo Park:d8000", "XC:champ:ncaa:d10000",
            "TF:loc:5:out", "XC:77:d6000"]
    course = np.array([0, 0, 1, 2, 3, 4, 0, -1])
    return {
        "course_keys": keys,
        "course": course.astype(np.int32),
        "result_id": np.array([10, 11, 12, 13, 10, 14, 15, 16]),
        "year": np.full(8, 2026), "doy": np.array([280, 281, 280, 300, 100, 280, 280, 280]),
        "course_lat": np.array([40.0, np.nan, np.nan, 41.0, 39.0]),
        "track_type": np.array(["", "", "", "Flat", ""], dtype="U32"),
        "cell_days": np.array([2, 1, 1, 1, 1], dtype=np.int32),
        "norm": np.linspace(1500, 1600, 8),
    }


def test_rekey_moves_only_placed_canonical_rows():
    import oct23_variant_pack as vp
    cols = _pack()
    # 10 and 11 placed at 123 (Foo Park, which also has a name cell), 13 a
    # champ race, 14 placed at 77 under a new name; result 10 is also a
    # track row's id (index 4), which must not move
    rows = {10: (1, None, "Foo Park", 1500.0), 11: (1, None, "Foo Park", 1510.0),
            13: (2, None, "NCAA", 1700.0), 14: (3, None, "Bar Farm", 1100.0)}
    idx, course, keys, n = vp.rekey(cols, rows)
    assert sorted(idx.tolist()) == [0, 1, 5]
    assert keys[course[0]] == keys[course[1]] == "XC:name:Foo Park:d8000"
    assert keys[course[5]] == "XC:name:Bar Farm:d6000"
    assert course[3] == 2 and course[4] == 3 and course[6] == 0 and course[7] == -1
    assert n["re-keyed"] == 3 and n["champ or name key kept"] == 1
    assert n["new name keys"] == 1 and n["name keys already there"] == 1
    vp.extendPerCourse(cols, 5, len(keys))
    assert cols["course_lat"].size == 6 and np.isnan(cols["course_lat"][5])
    assert cols["track_type"][5] == ""
    days = vp.cellDays(course, cols["year"], cols["doy"], len(keys))
    assert days.tolist() == [1, 2, 1, 1, 0, 1]       # 123 keeps row 6; 77 empties


def test_dump_parse(monkeypatch):
    import oct23_variant_pack as vp
    text = ("-- dump\nCOPY public.meets_tfrrs (meet_id, sport, venue_name, gps_lat, gps_long) "
            "FROM stdin;\n1\tXC\tA\t40.1\t-80\n2\tXC\tB\t\\N\t\\N\n3\tTF\tC\t40\t-80\n"
            "4\tXC\tD\t0\t0\n\\.\n")
    monkeypatch.setattr(vp.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=text))
    assert vp.dumpGps("/x") == {1: True, 2: False, 4: False}


def test_arm_command_is_the_pipelines_own(tmp_path):
    script = os.path.join(ROOT, "scripts", "oct23_arm.sh")
    awk = subprocess.run(["awk", r'/^ *step 08_golive /{on=1} on{print; if ($0 !~ /\\$/) exit}',
                          os.path.join(ROOT, "deploy", "run_pipeline.sh")],
                         capture_output=True, text=True, check=True).stdout
    assert "engine/run_joint.py --golive " in awk and "--tilt-scale" in awk
    # the same transformation the script makes, read from the script itself
    body = open(script).read()
    assert "s/ --golive / --golive-dry /" in body and "unset XCP_FROM_STATE" in body
    r = subprocess.run(["bash", script, ROOT, str(tmp_path / "none.npz"), "own",
                        str(tmp_path / "A")], capture_output=True, text=True)
    assert r.returncode == 2 and "no pack" in r.stderr
    pack = tmp_path / "p.npz"
    pack.write_bytes(b"")
    r = subprocess.run(["bash", script, ROOT, str(pack), "own",
                        os.path.join(ROOT, "engine", "data", "arm")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "engine/data" in r.stderr


def test_arm_converts_through_its_own_tables(tmp_path):
    _fakeDatabase()
    sys.argv = ["x"]
    import oct23_dry as od
    import oct23_score as sc
    live = {"per_sport": {
        "XC": (np.array([1, 2]), np.array([120.5, 130.25]),
               np.array(["college_m|XC", "college_m|XC"], dtype=object)),
        "TF": (np.array(sc.HAMMERAND), np.array([140.0, 138.0]),
               np.array(["college_m|TF"] * 2, dtype=object))},
        "scale_rows": [("college_m|XC", "XC", 1500.0, 0.01, 0.0, 100),
                       ("college_m|TF", "TF", 1480.0, 0.0, 0.0, 100)],
        "gain_rows": [("college_m|TF", "TF", 1, 120.0, 0.01, 0.01, 0.005, 50),
                      ("college_m|TF", "TF", 0, 90.0, 0.01, 0.01, 0.002, 50)],
        "dist_rows": [("college_m", "TF", 5000, 1, 0.0, 10)]}
    arrays = od.armArrays(live)
    np.savez(tmp_path / "arm_ratings.npz", meta=np.array([repr({"tilt_scale": "own"})]),
             **arrays)
    sc.modeArm(types.SimpleNamespace(arm_dir=str(tmp_path)))
    import conversions as C
    # pinned: the arm's numbers, sorted by anchor as the loader sorts them
    assert C._scale["map"][("college_m", "TF")][0] == 1480.0
    assert C._gain["map"][("college_m", "TF")] == [(90.0, 0.002), (120.0, 0.005)]
    z = np.load(tmp_path / "five.npz")
    assert z["result_id"].size == 4 and np.all(z["five_k_s"] > 0)
    # a faster rating is a faster 5K, and both legs agree with fiveKForRating
    xc = z["five_k_s"][z["sport"] == 0]
    assert xc[1] < xc[0]
    assert abs(xc[0] - C.fiveKForRating(120.5, "college_m|XC")["track"]) < 1e-6
