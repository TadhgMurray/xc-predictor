"""The scale probe is read-only, and it measures rather than assumes.

Owner, 2026-09-09, on the 200-400 ratings: "I think its prolly just rating
them and normalizing on diff scales/anchors. we should try to recreate it
tho."

★ THE CLAIM IT RESTS ON IS ARITHMETIC, AND IT WAS MEASURED FIRST. Over 38
  rows spanning 1995-2026, four athletes and eight events:

      rating x normalized_time = 165,645 +/- 25   (0.03%)

  So `rating` is exactly 100 x anchor / normalized_time -- no per-row term,
  no seasonal component, no course difficulty. An impossible rating is an
  impossible normalized_time arriving at a correct division, which is why
  the probe measures normalized_time and nothing else.

⚠ AND IT RUNS ON THE LIVE DATABASE WITH THE SITE UP, so read-only is not a
  style preference. This test is the guard.

    python tests/test_rating_scale_probe.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "scripts", "rating_scale_probe.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. read-only, and provably so ------------------------------------ #
#   ! THE SQL, NOT THE COMMENTS. The docstring says "READ-ONLY"; a docstring
#     cannot enforce it, and the word DROP appears in prose elsewhere in this
#     repo's diagnostics.
sql = "\n".join(re.findall(r'"""\n(\s*(?:WITH|SELECT).*?)"""', SRC, re.S))
ok(sql.strip(), "no SQL found -- the extraction is broken, not the script")
for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
            "TRUNCATE"):
    ok(bad not in sql.upper(),
       f"{bad} in the probe's SQL -- this runs on the live database with the "
       f"site up")

# the two writes it IS allowed are session settings, and only those
sets = re.findall(r'cur\.execute\(f?"(SET [^"]*)"', SRC)
ok(all(s.startswith("SET max_parallel_workers_per_gather")
       or s.startswith("SET work_mem") for s in sets),
   f"the only SETs allowed are the scan settings, got {sets}")


# ---- 2. it asks the catalog instead of guessing ------------------------ #
#   athlete_dump died on athletes.state, a column that does not exist. Same
#   rule here: a diagnostic that cannot survive a missing column is useless
#   exactly when it is needed.
ok("_have(cur, \"results_tf\"" in SRC,
   "the probe must introspect results_tf rather than assume its columns")
ok("event_short" in SRC and "'(no event column)'" in SRC,
   "with no event column every row falls in one cell -- it must say so "
   "rather than report a single meaningless number")


# ---- 3. the measurement is a measurement ------------------------------ #
# ⚠ THE ANCHOR IS RECOVERED FROM THE ROWS, NOT READ OUT OF THE ENGINE. If it
#   were imported, the probe would agree with the code by construction and
#   could never show the code disagreeing with the stored data.
ok("speed_rating * normalized_time" in SRC,
   "the anchor must be recovered as rating x normalized_time from the rows")
ok("import pair_write_results" not in SRC and "from pair_" not in SRC,
   "the probe must not import the rating code it is auditing")

# the cell test is a SPREAD test, and the threshold is stated
ok("normalized_time / time_seconds" in SRC,
   "the multiplier under test is normalized/raw, per (pool, event)")
ok("0.20 * m.m" in SRC,
   "the 'more than one scale' threshold must be explicit in the SQL")
# ! IN _CELLS SPECIFICALLY. Checking SRC as a whole passed with the median
#   swapped for an average, because _ROWS carries one too.
cells = re.search(r'_CELLS = """(.*?)"""', SRC, re.S).group(1)
ok("percentile_disc(0.5)" in cells and "avg(" not in cells,
   "each row must be compared against its own cell's MEDIAN, not its mean -- "
   "the broken rows drag a mean toward themselves and hide behind it")


# ---- 4. the arithmetic the docstring claims ---------------------------- #
#   Re-derived here so the number in the prose cannot rot silently.
rows = [(407.01, 407.0), (414.84, 399.3), (479.1, 345.7), (508.17, 326.0),
        (509.64, 325.0), (515.73, 321.2), (818.92, 202.3), (843.85, 196.3),
        (857.5, 193.2), (863.51, 191.8), (530.02, 312.5), (523.43, 316.5)]
ks = [n * r for n, r in rows]
ok(max(ks) / min(ks) < 1.001,
   f"rating x normalized_time is not constant across the sample: "
   f"{min(ks):,.0f}..{max(ks):,.0f}")
ok("165,645" in SRC,
   "the measured constant must be written down where the next reader is")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
