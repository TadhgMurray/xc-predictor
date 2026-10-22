"""scripts/run_changes.py: the pure parts (2026-10-22)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import run_changes as RC                                         # noqa: E402


def test_course_changes_counts_and_names_only_well_raced_courses():
    rows = [("XC:Greiner Family XC Course|36.1", 8000, 900, 0.050, 0.0286),   # -2.14%
            ("XC:Tiny Park", 5000, 30, 0.02, 0.06),                           # thin: not named
            ("XC:Mt. SAC", 4700, 5000, 0.080, 0.0855),                        # +0.55%
            ("XC:New Farm", 5000, 400, None, 0.04),                           # new
            ("XC:Flat", 5000, 5000, 0.01, 0.0102)]                            # no move
    c = RC.courseChanges(rows)
    assert c["compared"] == 4 and c["new"] == 1 and c["moved_1pct"] == 2
    assert [b["course"] for b in c["biggest"]] == ["Greiner Family XC Course", "Mt. SAC"]
    assert c["biggest"][0] == {"course": "Greiner Family XC Course", "distance_m": 8000,
                               "change_pct": -2.14}


def test_athlete_changes_shape_and_order():
    rows = [("college", "XC", 60000, 0.52, 0.4, 4100, 300, 40000, 19000, 0),
            ("hs", "XC", 400000, 0.31, 0.1, 21400, 3100, 230000, 160000, 9000),
            ("ms", "TF", 0, None, None, 0, 0, 0, 0, 0)]
    a = RC.athleteChanges(rows)
    assert [x["level"] for x in a] == ["hs", "college"]
    assert a[0]["median_abs"] == 0.31 and a[0]["new"] == 9000


def test_previous_run_start(tmp_path):
    for n in ("20261019_143556", "20261012_154900", "20261022_010000", "nightly_20261021_011700"):
        (tmp_path / n).mkdir()
    import datetime
    assert RC.previousRunStart(str(tmp_path / "20261022_010000")) == datetime.datetime(2026, 10, 19, 14, 35, 56)
    assert RC.previousRunStart(str(tmp_path / "20261012_154900")) is None


def test_engine_changes_reads_the_previous_runs_commit(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(RC, "OUT_DIR", str(tmp_path))
    (tmp_path / "20261019_143556.json").write_text(json.dumps({"commit": "aaa"}))
    (tmp_path / "20261030_010000.json").write_text(json.dumps({"commit": "zzz"}))   # later: ignored
    calls = []

    def git(*args):
        calls.append(args)
        if args[0] == "rev-parse":
            return "bbb"
        return "engine/joint_solve.py\nracecast/predict.py\n"
    monkeypatch.setattr(RC, "_git", git)
    out = RC.engineChanges("20261026_010000")
    assert out["commit"] == "bbb" and out["previous_commit"] == "aaa"
    assert out["engine_files"] == ["engine/joint_solve.py", "racecast/predict.py"]
    assert ("diff", "--name-only", "aaa..bbb", "--") == calls[1][:4]
