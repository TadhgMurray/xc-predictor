# ★ The 5K column checked against the track (owner, 2026-10-23: "the 5k
#   equivs on my race were abt 15-20s faster").
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "racecast"), os.path.join(ROOT, "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)
import _env  # noqa: F401,E402
import conversions as C                                           # noqa: E402
import build_clock_calibration as B                               # noqa: E402


def test_bands_take_the_median_per_quantile_and_drop_thin_ones():
    pairs = [(100 + i * 0.1, -1.0) for i in range(300)] + [(130 + i * 0.1, -2.0) for i in range(300)]
    got = B.bands(pairs, k=2, min_n=100)
    assert [(round(m), n, b) for m, _lo, _hi, n, b in got] == [(115, 300, -1.0), (145, 300, -2.0)]
    assert B.bands(pairs[:50], k=2, min_n=100) == []
    # a broken input is clamped, not trusted
    assert B.bands([(100.0, -40.0)] * 200, k=1, min_n=100)[0][4] == -B.CLAMP_PCT


def test_a_serious_best_needs_the_distance_within_one_percent():
    rows = [("3200 Meters", 640.0), ("3200 Meters", 630.0), ("1600 Meters", 300.0), ("3200m", 2000.0)]
    dist = {"3200 Meters": 3200.0, "1600 Meters": 1600.0, "3200m": 3200.0}.get
    assert B.bestAndCount(rows, 3200.0, dist) == (630.0, 2)


def test_the_bias_interpolates_between_bands_and_holds_at_the_ends(monkeypatch):
    monkeypatch.setitem(C._cal, "map", {("hs_m", "XC"): [(110.0, -0.5), (130.0, -1.5)]})
    monkeypatch.setitem(C._cal, "at", 9e18)
    assert C.clockBias(100, "hs_m", "XC") == -0.5
    assert abs(C.clockBias(120, "hs_m", "XC") - -1.0) < 1e-9
    assert C.clockBias(150, "hs_m", "XC") == -1.5
    assert C.clockBias(120, "hs_f", "XC") == 0.0
    monkeypatch.setenv("XCP_CLOCK_CAL", "0")
    assert C.clockBias(120, "hs_m", "XC") == 0.0


def test_the_track_5k_takes_the_gap_back_out(monkeypatch):
    monkeypatch.setattr(C, "ratingSeconds", lambda r, pool, sport, d: 1000.0)
    monkeypatch.setitem(C._cal, "map", {("hs_m", "XC"): [(117.0, -1.5)]})
    monkeypatch.setitem(C._cal, "at", 9e18)
    monkeypatch.setattr(C, "readerDistance", lambda pool, sport: 5000.0)
    c = C.ratingClock(117.0, "hs_m", "XC")
    assert c["where"] == "on a track"
    # 1000 s read 1.5% fast: shown as 1000 * e^0.015 = 1015.1 s
    assert c["time"] == "16:55"
    # a time "on a typical course" is not a track time: untouched
    monkeypatch.setitem(C._cal, "map", {("hs_m", "XC"): [(117.0, -1.5)]})
    c2 = C.ratingClock(117.0, "hs_m", "XC", 8000.0)
    assert c2["time"] == "16:40"


def test_the_top_five_percent_is_its_own_band():
    # owner, 2026-10-24: the elite were read inside a band whose median is ~128
    pairs = [(100.0 + i / 100.0, 0.0 if i < 3800 else 2.0) for i in range(4000)]
    got = B.bands(pairs, min_n=150)
    assert len(got) == 6
    assert got[-1][4] == 2.0 and got[-1][3] == 200
    # a pool too small for 150 at the top keeps its top fifth whole
    assert len(B.bands(pairs[:1000], min_n=150)) == 5
