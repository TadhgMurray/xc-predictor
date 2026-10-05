"""The TF weather fit compares the daily peak with the window mean (61af379).
The two candidate columns are load-only: run 20261004_125010 failed
04f_weather_fit_tf with KeyError 'apparent_temp_max' because they stayed in
QUERIED_FEATURES. This runs main() end to end on synthetic rows."""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

fw = pytest.importorskip("fit_weather_correction")


def _cols(n_ath=300, seed=0):
    rng = np.random.default_rng(seed)
    rows = {k: [] for k in ("ath", "course", "event", "date", "nt", "dist",
                            "apparent_temp", "apparent_temp_max", "apparent_temp_avg",
                            "wind", "precip", "soil", "snow")}
    meets = [(f"c{m % 12}", f"2025-04-{1 + m % 28:02d}") for m in range(60)]
    wx = {mt: (rng.uniform(5, 35), rng.uniform(0, 8), rng.uniform(0, 3),
               rng.uniform(0.1, 0.4)) for mt in meets}
    for a in range(n_ath):
        skill = rng.normal(0, 0.05)
        for k in rng.choice(len(meets), 6, replace=False):
            course, date = meets[k]
            tmax, wind, precip, soil = wx[meets[k]]
            tavg = tmax - rng.uniform(3, 8)
            rows["ath"].append(a)
            rows["course"].append(course)
            rows["event"].append(f"{course}|{date}")
            rows["date"].append(date)
            rows["dist"].append(3200.0)
            rows["nt"].append(600 * np.exp(skill + 0.002 * max(tmax - 20, 0)
                                           + rng.normal(0, 0.01)))
            for f, v in (("apparent_temp", tmax), ("apparent_temp_max", tmax),
                         ("apparent_temp_avg", tavg), ("wind", wind),
                         ("precip", precip), ("soil", soil), ("snow", 0.0)):
                rows[f].append(v)
    out = {k: np.asarray(v) for k, v in rows.items()}
    out["ath"] = out["ath"].astype(np.int64)
    out["date"] = out["date"].astype(object)
    out["course"] = out["course"].astype(object)
    out["event"] = out["event"].astype(object)
    return out


def test_tf_compare_runs_and_picks_one(monkeypatch, capsys):
    monkeypatch.delenv("XCP_TF_TEMP_AGG", raising=False)
    monkeypatch.setattr(fw, "getColumns", lambda *a, **k: _cols())
    monkeypatch.setattr(fw, "appliedArtifact", lambda sport: (None, "none (test)"))
    monkeypatch.setattr(sys, "argv", ["fit_weather_correction.py", "--sport", "TF",
                                      "--measure-only"])
    saved = (fw.QUERIED_FEATURES, dict(fw.WX_AGG))
    try:
        fw.main()
        assert "apparent_temp_max" not in fw.QUERIED_FEATURES
        assert fw.WX_AGG["apparent_temp"] in ("max(apparent_temperature)",
                                              "avg(apparent_temperature)")
    finally:
        fw.QUERIED_FEATURES, fw.WX_AGG = saved
    out = capsys.readouterr().out
    assert out.count("[temp] TF temperature as the") == 2
    assert "<- used" in out
