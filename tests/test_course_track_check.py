"""course_track_check: a course read against its runners' own track level
(owner, 2026-11-06: Seitz / Jazwinski vs Hassan / Mantecon)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import course_track_check as C                                  # noqa: E402


def test_a_generous_course_reads_above_its_level():
    rows = ([("Flat Park", 5000, "hs_m", 0.02)] * 25 + [("Hill Farm", 5000, "hs_m", 0.03)] * 25
            + [("LaVern Gibson", 5000, "hs_m", 0.07)] * 25 + [("Tiny", 5000, "hs_m", 0.5)] * 3)
    base, out = C.readings(rows, 20)
    assert abs(base["hs_m"] - 0.03) < 1e-9
    assert out[0][1] == "LaVern Gibson" and abs(out[0][5] - 0.04) < 1e-9
    assert all(r[1] != "Tiny" for r in out)              # under --min


def test_the_controls_tell_a_real_course_from_noise():
    import random
    rnd = random.Random(5)
    real, noise = [], []
    for c in range(40):
        bias = 0.06 if c < 10 else 0.0                    # ten courses truly generous
        for i in range(60):
            pid = c * 1000 + i
            real.append((f"C{c}", 5000, "hs_m", bias + rnd.gauss(0, 0.04), pid))
            noise.append((f"C{c}", 5000, "hs_m", rnd.gauss(0, 0.04), pid))
    r_real, n = C.splitHalf(real, 10)
    r_noise, _ = C.splitHalf(noise, 10)
    assert n == 40 and r_real > 0.7 and abs(r_noise) < 0.4
    _b, null = C.shuffled(real, 20)
    assert max(x[5] for x in null) < max(x[5] for x in C.readings(real, 20)[1])
