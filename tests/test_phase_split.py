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
# ⚠ meets_tf IS ONE ROW PER EVENT (PRIMARY KEY (div_id, event_id)), so a
#   plain join fans a result across its whole meet. DISTINCT ON collapses it
#   to one row per (meet, feed) BEFORE anything joins to it -- the same guard
#   wheelchair_flag needed, and cheaper than a per-row LIMIT 1.
ok("DISTINCT ON (meet_id, source)" in SRC,
   "the indoor map must be one row per (meet, feed) or every track result "
   "is counted once per event of its meet")

# ⚠ AND IT MUST NOT BE A PER-ROW LOOKUP. The first version was a LATERAL
#   into results_tf. result_id is a PRIMARY KEY so it LOOKED harmless, but
#   over millions of ranking_results rows an indexed lookup is millions of
#   RANDOM SEEKS into a 191M-row table plus another into meets_tf, and that
#   ran for hours. It is two sequential passes with hash joins now.
ok("LATERAL" not in SRC[SRC.index("_INDOOR_MAP"):SRC.index("_BLOCK_SELECT")],
   "the indoor map must be built in bulk, not with a correlated lookup")
ok("sg_ind ind ON ind.result_id = k.result_id" in SRC,
   "the arm must read a prebuilt temp table")

# ---- 1b. and it samples, on the PERSON --------------------------------- #
#   A sandwich needs all three of a person's seasons, so sampling rows would
#   shred them and bias what survived toward athletes with more races.
ok("(r.person_id %% %(mod)s) = 0" in SRC,
   "the indoor map must be sampled by person")
ok("(k.person_id %% %(mod)s) = 0" in SRC,
   "...and the blocks by the SAME rule, or an athlete's XC seasons survive "
   "while their track ones do not")
ok('"--sample"' in SRC and "mod = 20" in SRC,
   "--split-indoor must default to a sample rather than the whole corpus")
ok("work_mem = '256MB'" in SRC and "'1GB'" not in SRC,
   "a gigabyte of work_mem per parallel worker is how a diagnostic takes "
   "the box down")
ok("max_parallel_workers_per_gather" in SRC,
   "these passes are big sequential scans -- that is what workers are for")
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
