"""The measured sport-gap constant is applied by the ridge it was measured
at, never by the sign of the solve's own estimate.

At ridge 0.5 the solve's own bbar is ~0 by construction, so a sign guard
fired or stayed silent at random: silent, it double-counted the gap
(issue #73); fired, it discarded the loop's own correction, and once D had
pushed measured_bbar across zero it discarded every one after it.

    python tests/test_bbar_ridge_guard.py
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pair_recenter as prc                                    # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def read(*p):
    with io.open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- #
# 1. measuredFor: the ridge decides, the sign only comments
# ---------------------------------------------------------------- #
# The 2026-08-26 file: measured at ridge 0, no ridge recorded.
b, note = prc.measuredFor(0.5, own_bbar=-0.002, measured=-0.045,
                          measured_ridge=0.0)
ok(b is None, "a ridge-0 constant must NOT apply at ridge 0.5 even when the "
              "signs agree -- that is the #73 double count")
ok(note and "ridge 0" in note and "ridge 0.5" in note,
   "the fallback must say which two ridges disagreed")

# Same file at the ridge it was measured at, opposite-sign own estimate:
# applies, with a note, never a fallback.
b, note = prc.measuredFor(0.0, own_bbar=+0.003, measured=-0.045,
                          measured_ridge=0.0)
ok(b == -0.045, "at the matching ridge the measured constant applies "
                "regardless of the sign of the solve's own estimate")
ok(note and "note" in note, "a sign disagreement at the same ridge is "
                            "telemetry, printed, not a fallback")

# The stall case: the loop pushed measured across zero at ridge 0.5 and the
# solve's own estimate is a hair negative. The correction must be applied.
b, note = prc.measuredFor(0.5, own_bbar=-0.0015, measured=+0.021,
                          measured_ridge=0.5)
ok(b == 0.021, "the loop's corrected value must apply at its own ridge even "
               "when the solve's own near-zero estimate has the other sign")

# Same sign, same ridge: applies silently.
b, note = prc.measuredFor(0.5, own_bbar=+0.004, measured=+0.021,
                          measured_ridge=0.5)
ok(b == 0.021 and note is None, "clean application prints nothing extra")

# No file: the solve's own estimate, no note.
b, note = prc.measuredFor(0.5, own_bbar=+0.004, measured=None,
                          measured_ridge=0.5)
# measured=None falls through to the module constant; force the absent case
b2, note2 = prc.measuredFor(0.5, own_bbar=+0.004, measured=None,
                            measured_ridge=None) if prc.MEASURED_BBAR is None \
    else (None, None)
ok(b2 is None and note2 is None, "with no measured constant there is nothing "
                                 "to apply and nothing to say")

# ---------------------------------------------------------------- #
# 2. the file round trip: recordApplied stamps the ridge, the emit copies
#    it onto the measured value, and a file with neither reads as ridge 0
# ---------------------------------------------------------------- #
saved = prc._GAP_JSON
try:
    with tempfile.TemporaryDirectory() as tmp:
        prc._GAP_JSON = os.path.join(tmp, "sport_gap_bbar.json")
        ok(prc._loadMeasuredRidge() == 0.0,
           "no file at all reads as ridge 0 (every file before 2026-09-02 "
           "was written at ridge 0)")
        with open(prc._GAP_JSON, "w", encoding="utf-8") as f:
            json.dump({"measured_bbar": -0.045, "measured_date": "2026-08-26"},
                      f)
        ok(prc._loadMeasuredRidge() == 0.0,
           "a file with no ridge key reads as ridge 0")
        prc.recordApplied(+0.0037, ridge=0.5)
        with open(prc._GAP_JSON, encoding="utf-8") as f:
            doc = json.load(f)
        ok(doc.get("applied_ridge") == 0.5 and doc.get("measured_bbar") == -0.045,
           "recordApplied stamps applied_ridge and keeps measured_bbar")
        # applied at 0.5 but the measured value predates that golive: it is
        # still the ridge-0 constant, and must not be read as a 0.5 one
        ok(prc._loadMeasuredRidge() == 0.0,
           "a measured value older than the last application keeps its own "
           "(ridge-0) provenance -- applied_ridge is not its ridge")
        # the emit step after that golive: measured now follows applied
        with open(prc._GAP_JSON, encoding="utf-8") as f:
            doc = json.load(f)
        doc.update({"measured_bbar": 0.0037 + 0.017,
                    "measured_date": doc["applied_date"]})
        with open(prc._GAP_JSON, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        ok(prc._loadMeasuredRidge() == 0.5,
           "measured on or after the last application, with no explicit "
           "measured_ridge (the pre-stamp emit), belongs to applied_ridge")
        doc["measured_ridge"] = 0.5
        with open(prc._GAP_JSON, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        ok(prc._loadMeasuredRidge() == 0.5, "an explicit measured_ridge wins")

finally:
    prc._GAP_JSON = saved

# ---------------------------------------------------------------- #
# 3. both golives go through measuredFor; the sign hard-stop is gone
# ---------------------------------------------------------------- #
for path in (("engine", "linkage_check.py"), ("engine", "pair_all.py")):
    src = read(*path)
    fn = src[src.index("def recenterSport(D):"):src.index("def solve(D")]
    ok("prc.measuredFor(D.get(\"ridge\", 0.0), own_bbar)" in fn,
       f"{path[1]}: recenterSport must resolve the constant through "
       f"pair_recenter.measuredFor")
    ok("own_bbar * use_bbar < 0" not in fn,
       f"{path[1]}: the sign hard-stop must be gone")
    ok("recordApplied(bbar, ridge=" in fn or "recordApplied(bbar" not in fn,
       f"{path[1]}: recordApplied must carry the ridge")

emit = read("scripts", "measure_sport_gap.py")
ok('doc["measured_ridge"] = float(doc["applied_ridge"])' in emit,
   "--emit must copy applied_ridge onto measured_ridge so the next solve "
   "knows which ridge the constant is valid at")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_bbar_ridge_guard: all checks passed")
