"""The XC->TF mapping keeps the calendar instead of cancelling it.

Owner, 2026-09-09: "there must just be some way to say that this is a 145 in
xc, and it correlates to a 4:00 mile fitness wise at that moment."

★ THERE IS, AND IT IS A DIFFERENT QUESTION FROM THE ONE THE SOLVE ASKS.

    the solve      one ability per athlete plus a sport offset -- "how good
                   are they, and how much do they specialise?"
    this           a MAPPING -- "an XC rating of R at this time of year goes
                   with what track performance?"

★ AND THE FIRST QUESTION HAS NO ANSWER IN THIS DATA. Measured 2026-09-09:

      (XC-out) - (XC-in)  +0.00633
      indoor  - outdoor   -0.00807
      CLOSURE ERROR       +0.01440    (20-90 SE)

  Two ways of writing one quantity, disagreeing. If each sport had ONE level
  they would be equal. The closure error is not noise -- it is the mapping
  varying with the calendar, which is what an athlete actually experiences.

⚠ SO THE ESTIMATOR MUST NOT REMOVE TIME. measure_sport_gap interpolates
  between bracketing seasons precisely to cancel it, which is right for
  recovering a constant and wrong here. This groups BY month and reports the
  drift, and that difference is what these checks defend.

    python tests/test_xc_tf_bridge.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "scripts", "xc_tf_bridge.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


sql = "\n".join(re.findall(r'"""\n(\s*(?:DROP|SELECT|WITH).*?)"""', SRC, re.S))
bare = "\n".join(ln.split("--")[0] for ln in sql.splitlines())

# ---- 1. read-only ------------------------------------------------------ #
for bad in ("INSERT INTO", "UPDATE ", "DELETE ", "ALTER ", "TRUNCATE"):
    ok(bad not in bare.upper(), f"{bad.strip()} in a measurement")
ok("CREATE TEMP TABLE" in bare and "conn.rollback()" in SRC,
   "temp tables only, and rolled back")

# ---- 2. time is KEPT, not cancelled ------------------------------------ #
ok("tf_month" in bare and "xc_month" in bare,
   "the pair must carry BOTH months -- the drift across them is the whole "
   "result")
ok("GROUP  BY 1 HAVING" in bare and "_BY_MONTH" in SRC,
   "the headline table must be grouped by month")
# ⚠ NO INTERPOLATION. That is measure_sport_gap's job and it is the wrong
#   tool here: it exists to remove the calendar.
ok("lag(" not in bare and "lead(" not in bare,
   "no bracketing or interpolation -- this keeps time as a variable")

# ---- 3. and the ratio is not assumed constant -------------------------- #
ok("_BY_LEVEL" in SRC and "width_bucket" in bare,
   "the conversion must also be reported BY LEVEL: a constant ratio is an "
   "assumption, and this is where it gets checked")

# ---- 4. the seconds half needs no join --------------------------------- #
#   results_tf carries speed_rating, event_short and time_seconds on the row,
#   so section 3 is one aggregate. The last two scripts were slow because I
#   reached for a join that was not needed.
ok("FROM   results_tf" in bare and bare.count("JOIN") <= 1,
   "the mile table must come straight off results_tf, with no join")
ok("LATERAL" not in bare, "no per-row lookups anywhere")
ok("(person_id %% %(mod)s) = 0" in bare and "%(mod)s" in bare,
   "every pass must be sampled by person")

# ⚠ SINGLE QUOTES IN SQL. "Men's Mile" in double quotes is a COLUMN NAME to
#   Postgres, and section 3 died with 'column "Men's Mile" does not exist'.
ok('"Men' not in bare,
   "string literals must use single quotes with the apostrophe doubled")
ok("'Men''s Mile'" in bare, "...like this one")

# ---- 5. the arithmetic of the composition ------------------------------ #
#   Section 1 gives a percentage, section 3 a table of seconds. The reader
#   composes them, so the instruction has to be there and has to be right.
ok("R * (1 + pct/100)" in SRC,
   "the report must say how to compose the two halves")

# ---- 6. months are numbered from August -------------------------------- #
#   ! OR THE WINTER WRAPS. A track season running Dec-May crosses the
#     calendar new year, and month numbers 12, 1, 2 do not sort.
ok("+ 4) %% 12" in SRC, "the month index must be shifted so August is 0")
months = re.search(r'_MONTHS = \((.*?)\)', SRC, re.S).group(1)
ok(months.split(",")[0].strip().strip('"') == "Aug",
   f"and the labels must start at August: {months.split(',')[0]}")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
