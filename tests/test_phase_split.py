"""Indoor against outdoor separates real fitness from a sport-scale error.

Owner, 2026-09-09: "Could track fitness gain be messing up on indoor to
outdoor switch?"

★ THE SANDWICH DOES NOT CANCEL A PHASE-LOCKED SEASON. It cancels a linear
  trend exactly (interpolating between two same-sport seasons that bracket
  the middle) and a quadratic between its two bread types. But a real
  "sharper every spring than every autumn" enters BOTH arrangements with the
  same sign, because both interpolate between two same-phase seasons and
  compare against the opposite phase. That part of D is fitness the rating is
  SUPPOSED to carry -- joint_golive's own docstring leaves the form curve out
  of the rating for exactly that reason.

★ INDOOR AGAINST OUTDOOR IS THE CONTROL. They are one sport to the engine --
  one `sport` indicator, one mu, one pool anchor -- so no sport-LEVEL scale
  error can sit between them, and whatever gap they show is phase.

    python tests/test_phase_split.py
"""
import datetime as dt
import io
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "scripts", "measure_sport_gap.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. the arms are a parameter, and indoor is one of them ------------ #
ok("--split-indoor" in SRC, "the mode must exist")
ok("_ARM_INOUT" in SRC and "is_indoor" in SRC,
   "the indoor arm must come from meets_tf.is_indoor")
# ⚠ meets_tf IS ONE ROW PER EVENT. A plain join fans a result across its
#   whole meet; the rest of this repo has been bitten by that twice.
ok("LEFT   JOIN LATERAL" in SRC and "LIMIT  1" in SRC,
   "the is_indoor lookup must be a LATERAL with LIMIT 1, or one result "
   "becomes one row per event of its meet")
ok("GROUP  BY k.person_id, k.pool, {arm}, k.year" in SRC,
   "the blocks must be grouped by the ARM, not by k.sport -- otherwise "
   "indoor and outdoor land in one block and the split does nothing")


# ---- 2. the arithmetic, on a corpus with planted answers --------------- #
os.environ.setdefault("XCP_DB_HOST", "127.0.0.1")
os.environ.setdefault("XCP_DB_PORT", "1")
os.environ.setdefault("XCP_DB_USER", "u")
os.environ.setdefault("XCP_DB_NAME", "n")
os.environ.setdefault("XCP_DB_PASSWORD", "x")
os.environ.setdefault("XCP_DB_QUIET", "1")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    import contextlib                                          # noqa: E402
    import measure_sport_gap as m                              # noqa: E402
except Exception as e:                                         # noqa: BLE001
    print(f"  (skipping section 2: {e})")
else:
    # ★ A LINEAR IMPROVEMENT THAT MUST CANCEL, ON TOP OF THREE LEVELS THAT
    #   MUST BE RECOVERED. 0.05/yr is a real rate of improvement for a young
    #   athlete and is twice the size of the effect being measured, so a
    #   estimator that does not remove it cannot pass this.
    LEVEL = {"XC": -0.030, "TF-in": -0.012, "TF-out": 0.0}
    TREND = 0.05 / 365.0
    D0 = dt.date(2024, 1, 1)

    def _r(arm, day):
        return math.exp(LEVEL[arm] + TREND * day)

    rows = []
    for ends, mid in (("TF-out", "XC"), ("XC", "TF-out"), ("TF-out", "TF-in"),
                      ("TF-in", "TF-out"), ("TF-in", "XC"), ("XC", "TF-in")):
        for st in range(0, 300, 7):
            a, b, c = st, st + 180, st + 365
            rows.append({"a_sport": ends, "sport": mid,
                         "a_date": D0 + dt.timedelta(a),
                         "mid_date": D0 + dt.timedelta(b),
                         "c_date": D0 + dt.timedelta(c),
                         "a_rating": _r(ends, a), "rating": _r(mid, b),
                         "c_rating": _r(ends, c)})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = m._reportPairs(m._collectPairs(rows))
    for pair, want in ((("XC", "TF-in"), LEVEL["XC"] - LEVEL["TF-in"]),
                       (("XC", "TF-out"), LEVEL["XC"] - LEVEL["TF-out"]),
                       (("TF-in", "TF-out"),
                        LEVEL["TF-in"] - LEVEL["TF-out"])):
        g = got.get(pair)
        ok(g is not None and abs(g - want) < 1e-9,
           f"{pair[0]} - {pair[1]} came back {g}, planted {want} -- either "
           f"the pairing or the 0.05/yr improvement is leaking in")

    # ---- 3. the verdict's ratio is the right way up -------------------- #
    #   ⚠ THE FIRST VERSION PRINTED -1.00 ON A FIXTURE BUILT TO BE EXACTLY
    #     PHASE. (XC-out) - (XC-in) = in - out; the other order is the right
    #     answer with the sign inverted, which reads as "explains nothing".
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m._phaseVerdict(got)
    out = buf.getvalue()
    ok("ratio = +1.00" in out,
       f"a corpus that is PURELY phase must report a ratio of +1.00; got:\n"
       f"{[ln for ln in out.splitlines() if 'ratio' in ln]}")
    ok("upper bound" in out,
       "the verdict must say the XC-arm average is an UPPER bound on the "
       "scale error -- indoor/outdoor bounds the phase WITHIN track only")


# ---- 4. the log reader ------------------------------------------------- #
CW = io.open(os.path.join(ROOT, "scripts", "curve_window_gap.py"),
             encoding="utf-8").read()
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import curve_window_gap as cw                                   # noqa: E402

sample = ("junk\n"
          "[joint] a, recentred by -0.03912, curve TF-XC window gap "
          "[ 0.0011 -0.0007]\n"
          "[joint] b, recentred by -0.03924, curve TF-XC window gap "
          "[ 0.0009 -0.0005]\n")
gaps = cw.gapsIn(sample)
ok(len(gaps) == 2, f"both passes must be read, got {len(gaps)}")
ok(gaps[-1][1] == [0.0009, -0.0005],
   f"numpy's array repr is whitespace-separated: got {gaps[-1][1]}")
ok(cw.gapsIn("nothing here") == [],
   "a log without the line must come back empty, not raise")
# ! WRITES NOTHING, READS NO DATABASE. It is pointed at production logs.
for bad in ("getConn", "psycopg2", "UPDATE", "INSERT"):
    ok(bad not in CW, f"{bad} in a log reader")


if __name__ == "__main__":
    for msg in failed:
        print("FAIL:", msg)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
