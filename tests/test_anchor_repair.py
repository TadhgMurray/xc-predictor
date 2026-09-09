"""The repair moves the pool scale and NOTHING else.

Owner, 2026-09-09, choosing between the two fixes: "(a)". Re-normalise on the
pool the row is rated in, rather than pinning the backfill's pool.

★ THE TRAP THIS FILE EXISTS FOR. anchor_check's `expected` is a bare distance
  factor -- normalizeTime with no season, no weather, no track geometry, no
  course. The stored value has all of those baked in. Writing `expected` back
  would fix the anchor and silently strip every correction the pipeline
  spent hours computing, and the result would look right: same magnitude,
  same pool, no error anywhere. So the repair RESCALES:

      new = stored x factor(d, rated_pool) / factor(d, pool_it_was_on)

  and section 1 proves that a correction present in `stored` survives it
  exactly.

    python tests/test_anchor_repair.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "engine", "anchor_repair.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 0. it cannot write by accident ------------------------------------ #
ok('"--apply"' in SRC and 'action="store_true"' in SRC,
   "the repair must be a dry run by default")
ok("if writes and args.apply" in SRC,
   "the UPDATE must be gated on --apply, not merely reported")
ok("conn.rollback()" in SRC,
   "a dry run must roll back rather than trust that it wrote nothing")
ok("normalized_time = v.nt" in SRC,
   "the update is keyed by result_id through a VALUES join")
# ⚠ THE ONE COLUMN. A repair that also touched speed_rating would be writing
#   a rating from a stale pool mean.
ok("speed_rating" not in SRC[SRC.index("_UPDATE ="):SRC.index("def main")],
   "the repair must write normalized_time and nothing else")


# ---- 1. the corrections survive, exactly ------------------------------- #
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
try:
    import anchor_repair as ar                                  # noqa: E402
    from normalize_distance import normalizeTime                # noqa: E402
except Exception as e:                                          # noqa: BLE001
    print(f"  (spline artifacts absent, skipping sections 1-3: {e})")
else:
    T, D = 244.60, 1609
    CORR = 1.017                       # stand-in for era x weather x geometry
    stored = normalizeTime(T, D, "hs_m", "TF") * CORR
    want = normalizeTime(T, D, "college_m", "TF") * CORR
    got = ar.rescale(stored, D, "hs_m", "college_m", "TF")
    # ! 1e-4, NOT 1e-6, AND THE SLACK IS normalizeTime's OWN ROUNDING.
    #   normalizeTime rounds its result to two decimals, so `want` here is
    #   built from a rounded number while the repair's factor comes from
    #   normalizeTime(1000.0, ...)/1000 -- accurate to eight digits. The gap
    #   is 6e-6 relative and it is in the FIXTURE, not the repair.
    ok(got is not None and abs(got / want - 1.0) < 1e-4,
       f"rescaling hs_m -> college_m gave {got}, want {want} -- the 1.7% "
       f"correction inside `stored` must survive untouched")

    # and the naive repair -- writing `expected` -- would NOT have survived it
    naive = ar._expected(T, D, "college_m", "TF")
    ok(abs(naive / want - 1.0) > 0.01,
       "the fixture is pointless unless writing `expected` really would "
       "lose the correction")

    # ---- 2. it abstains rather than guessing --------------------------- #
    ok(ar.IDENTIFY_TOL < ar.ac.TOLERANCE,
       f"identifying WHICH scale a row came from ({ar.IDENTIFY_TOL}) must be "
       f"tighter than deciding it is wrong ({ar.ac.TOLERANCE}) -- the repair "
       f"divides by that pool's factor")
    # ⚠ 1.35, CHOSEN NOT GUESSED. Pool factors at this distance sit at
    #   roughly 0.44 (elem), 0.62 (ms), 1.00 (hs), 1.21 (college_f) and 1.62
    #   (college_m) times hs. 1.35 is >10% from college_m so the row FAILS
    #   the check, and >3% from every one of them so nothing identifies it.
    #   1.5x was the first try and it sits 7% from college_m -- inside
    #   TOLERANCE, so the row read as already right and the test proved
    #   nothing.
    junk = normalizeTime(T, D, "hs_m", "TF") * 1.35
    new, why = ar.repairRow(T, D, junk, "college_m", "TF", ar.ac._POOLS)
    ok(new is None and why == "scale not identified",
       f"a value on no known pool scale must be left alone, got ({new}, "
       f"{why!r}) -- rescaling it would be a guess dressed as a repair")

    # ---- 3. and it is idempotent --------------------------------------- #
    new, was = ar.repairRow(T, D, stored, "college_m", "TF", ar.ac._POOLS)
    ok(new is not None and was == "hs_m|TF", f"the real row must move: {was}")
    again, why2 = ar.repairRow(T, D, new, "college_m", "TF", ar.ac._POOLS)
    ok(again is None and why2 == "already right",
       f"a repaired row must not move a second time, got ({again}, {why2!r})")
    # a row that was always right is never touched
    clean = normalizeTime(242.22, D, "hs_m", "TF")
    n3, why3 = ar.repairRow(242.22, D, clean, "hs_m", "TF", ar.ac._POOLS)
    ok(n3 is None and why3 == "already right",
       f"a correct row must be left alone, got ({n3}, {why3!r})")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
