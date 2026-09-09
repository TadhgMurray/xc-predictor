"""The difficulty audit is read-only, cheap, and asks the CA question both ways.

Owner, 2026-09-09: "can you get started on teh difficulty, look at if it's a
CA thing as well pls."

★ THE SYMPTOM, from Lex Young's dump -- his six California courses:

      Woodbridge 0.1%   Clovis 0.2%   CIF State 0.2%
      Marmonte 1.7%     CIF-SS 5.8%   NXN 5.9%

  A flat September 5k and a hilly November one do not differ by a fifth of
  one percent, and the three with the LARGEST fields are the ones reading as
  exactly average.

★ TWO EXPLANATIONS, DIFFERENT FIXES, AND THE SCRIPT SEPARATES THEM. Shrinkage
  (thin courses pulled to zero by a prior) makes the sd of difficulty RISE
  with n_results; a flat scale does not. Section 2 is that test and it is why
  the buckets have to be ordered by evidence rather than by anything else.

⚠ AND THE CA QUESTION IS ASKED BOTH WAYS ROUND, because both answers are
  plausible and they need opposite fixes: a WIDER California means the
  estimator works where the data is dense and the rest of the country is
  shrunk; a NARROWER one means the big CA courses are defining zero. A script
  that only checks for one would find it.

    python tests/test_difficulty_spread.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "scripts", "difficulty_spread.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


sql = "\n".join(re.findall(r'"""\n(\s*(?:DROP|SELECT|WITH).*?)"""', SRC, re.S))
bare = "\n".join(ln.split("--")[0] for ln in sql.splitlines())

# ---- 1. read-only, on a live database ---------------------------------- #
#   The one exception is a TEMP table, which dies with the transaction.
for bad in ("INSERT", "UPDATE", "DELETE", "ALTER", "TRUNCATE"):
    ok(bad not in bare.upper(), f"{bad} in a read-only audit")
ok("CREATE TEMP TABLE" in bare and "CREATE TABLE " not in bare,
   "the scratch tables must be TEMP, or a diagnostic leaves tables behind")
ok("conn.rollback()" in SRC, "and the transaction must be rolled back")
ok(bare.upper().count("DROP TABLE") == bare.upper().count("CREATE TEMP TABLE"),
   "every temp table must be dropped first, so a second run in one session "
   "does not fail on an existing one")

# ---- 2. it stays cheap ------------------------------------------------- #
#   ⚠ THE LESSON FROM --split-indoor: an indexed per-row lookup over millions
#     of rows is millions of random seeks, and "it uses an index" is not the
#     same claim as "it is fast". meets is touched ONCE, as an aggregate.
ok("LATERAL" not in bare,
   "no per-row lookups -- this reads a small table plus one pass over meets")
ok(bare.count("FROM   meets") == 1,
   f"meets must be read exactly once, found {bare.count('FROM   meets')}")
ok("GROUP  BY course_name" in bare,
   "the state map must be an aggregate, one row per course")
ok("mode() WITHIN GROUP" in bare,
   "a course whose state was mis-scraped once must not be relabelled by the "
   "mistake -- take the modal state, not any state")
ok("max_parallel_workers_per_gather" in SRC and "'128MB'" in SRC,
   "the scan settings must be set explicitly rather than inherited")

# ---- 3. the sport prefix ----------------------------------------------- #
#   course_difficulties is keyed "<SPORT>:<course>"; meets holds the bare
#   name. Joining them without stripping matches nothing and the whole report
#   reads as "no_state" for every row.
ok("position(':' in d.course_name)" in bare,
   "the 'XC:' prefix must be stripped before joining to meets")
ok("split_part(d.course_name, ':', 1)" in bare,
   "...and kept, so the report can separate XC from anything else")

# ---- 4. the shrinkage test is ordered by evidence ---------------------- #
buckets = re.findall(r"'(\d)\. [^']*'", SRC)
ok(buckets == sorted(buckets),
   f"the n_results buckets must print in increasing order or 'sd rises down "
   f"the table' means nothing: {buckets}")
ok("stddev_samp(pct)" in bare, "the test is on the SD of difficulty")

# ---- 5. the CA question is open, not leading --------------------------- #
ok("WIDER" in SRC and "NARROWER" in SRC,
   "both answers must be spelled out -- a script that only looks for one "
   "will find it")
ok("sum(n_results * pct)" in bare,
   "the CA comparison must also weight by RESULTS: California has many small "
   "courses and a few enormous ones, and 'what does a random race look like' "
   "is the question")

# ---- 6. difficulty is shown as a percent, once ------------------------- #
ok(bare.count("* 100.0") == 1,
   "difficulty is a fraction in the table and a percent on the page; it must "
   "be converted in exactly one place")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
