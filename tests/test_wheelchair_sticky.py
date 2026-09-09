"""wheelchair_person is permanent: once flagged, always flagged.

Owner, 2026-09-08: "for wheelchair it's not a detection gap bcs we've
detected these ppl so many times b4, which is an issue that keeps popping
up in them stop getting detected."

That is the whole diagnosis, and it is right. The rebuild was DROP TABLE +
CREATE TABLE AS, re-deriving the list from whatever labels survive in
results TODAY -- while this module's own rule is the opposite: "ANY
CONFIRMED CHAIR RACE CONDEMNS THE WHOLE CAREER" and "IT FLAGS, IT NEVER
DELETES". A snapshot cannot honour "once confirmed".

⚠ AND THE EVIDENCE GOES FOR ORDINARY REASONS, one of which is a feedback
  loop the pipeline drives itself:

      flagged -> (a run without the flag) rated -> the chair time looks
      impossible on the running scale -> the rowguard drops that result ->
      the proof is gone -> the next rebuild does not flag them -> rated

  plus a re-scrape renaming a division, a meet's div_id changing, or a
  person merge renumbering person_id.

So the fresh scan is UNIONED with what the table already held. The only
way off the list is --forget, which is deliberate and logged.

    python tests/test_wheelchair_sticky.py

Text checks: the module needs psycopg2 to import.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "engine", "wheelchair_flag.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. the previous list is captured BEFORE the rebuild drops it ----- #
i_prev = SRC.index("wheelchair_person_prev")
i_people = SRC.index("cur.execute(_PEOPLE)")
ok(SRC.index("CREATE TEMP TABLE wheelchair_person_prev") < i_people,
   "the old list must be copied before _PEOPLE drops the table, or there "
   "is nothing left to carry forward")
ok("ensureTable(cur)" in SRC[:i_people],
   "the table has to exist before it is copied -- a first ever run has no "
   "wheelchair_person and must not crash")


# ---- 2. the carry is INSERT-ONLY -------------------------------------- #
carry = re.search(r'_CARRY = """(.*?)"""', SRC, re.S).group(1)
ok(carry.strip().upper().startswith("INSERT INTO WHEELCHAIR_PERSON"),
   "the carry must add people, never rewrite the table")
ok("DELETE" not in carry.upper() and "DROP" not in carry.upper(),
   "nothing in the carry may remove a person")
ok("NOT EXISTS" in carry,
   "only people the fresh scan missed should be re-inserted, or the "
   "primary key blows up")
ok("wheelchair_person_prev" in carry, "the carry must read the saved copy")


# ---- 3. a carried person is marked, and the mark does not stack ------- #
ok("'carried:'" in carry,
   "a carried person must be visible as such in `via`, so --census and "
   "--review show the churn instead of hiding it")
ok("LIKE 'carried:%%'" in carry,
   "the prefix must not stack on every run (carried:carried:carried:...), "
   "and the %% is psycopg2 escaping, not a typo")


# ---- 4. --forget is the only way off, and it wins ---------------------- #
ok('"--forget"' in SRC, "a permanent list needs a deliberate escape hatch")
i_forget = SRC.index("args.forget")
i_carry = SRC.index("cur.execute(_CARRY)")
ok(i_forget < i_carry,
   "--forget must run BEFORE the carry, or a person just un-flagged is "
   "immediately carried back in from the previous table")
forget_block = SRC[i_forget:i_carry]
ok("wheelchair_person_prev" in forget_block,
   "--forget must delete from the saved copy too, for the same reason")
ok(forget_block.count("DELETE FROM") == 2,
   "--forget deletes from both the live table and the carry source")


# ---- 5. the count is reported ----------------------------------------- #
#   A nonzero carry is the churn this exists to survive. Silent would put
#   it right back where it was: invisible until the owner sees a chair
#   athlete on a board.
ok("carried forward" in SRC, "the carry count must be printed")
ok("--forget any" in SRC or "--forget" in SRC[SRC.index("carried forward"):
                                              SRC.index("carried forward") + 400],
   "the message should name the escape hatch")


# ---- 5b. BOTH track columns are read, and the second one cheaply -------- #
#   Owner, 2026-09-09, pasting the race page: "800m · Boys · Wheelchair ·
#   Outdoor". The word is in meets_tf.division and the event_short is a
#   plain "800m" -- the anet track feed puts the class in the DIVISION and
#   leaves the event bare, while the tfrrs one puts it in the event name.
#   Reading only event_short missed every anet chair track race, which is
#   how a 1:42.68 800m (beside a 16.54 100m, in the same meet) came out
#   150.8 and first on the high-school boys performance board.
#
# ⚠ THE FIRST FIX WAS ONE PREDICATE OVER A JOIN AND WAS UNUSABLE (owner:
#   "wheelchair flag is taking forever"). results_tf LEFT JOIN meets_tf is
#   61M rows against 14M with both regexes on the join output, and nothing
#   narrows either side first. The division has to be resolved on meets_tf
#   ALONE and the survivors joined back -- so this section asserts the
#   SHAPE, not just the columns.
# ! ASSERT ON THE SQL, NOT ON THE COMMENTS ABOUT IT. The first draft of this
#   section checked for "!~*" anywhere in the TF half -- and the comment
#   explaining why the "!~*" is there satisfied it, so deleting the predicate
#   itself still passed. Every check below reads the stripped text.
def _bare(sql):
    return "\n".join(ln.split("--")[0] for ln in sql.splitlines())


def _sql(name):
    return _bare(re.search(name + r' = """(.*?)"""', SRC, re.S).group(1))


cte = _sql("_SQL_DIV")
tf_half = _sql("_SQL_EV") + _sql("_SQL_DV")

# the division match happens on meets_tf by itself
ok("FROM   meets_tf" in cte and "division ~* " in cte,
   "the division regex must be applied to meets_tf on its own, before any "
   "join to results_tf")
ok("wc_div" in tf_half,
   "...and the TF half must then read what that step found")

# ! AND NOT ALSO THE OTHER WAY. A LEFT JOIN from results_tf to meets_tf in
#   the TF half is the slow shape coming back, whatever else is true.
ok("meets_tf" not in tf_half,
   "the TF branches must not join meets_tf directly -- that is the 61M x "
   "14M join this was rewritten to avoid")

ok("event_short, '') ~* " in tf_half,
   "the TF half must still match event_short: tfrrs puts the class there")

# ⚠ meets_tf IS PRIMARY KEY (div_id, event_id) -- ONE ROW PER EVENT. A
#   division that ran four events is four rows there, so joining the raw
#   matches back multiplies every result of that division by four and
#   n_chair (which --review divides by n_total) is inflated to match.
ok("DISTINCT ON (meet_id, div_id, source)" in cte,
   "the division set must be one row per (meet, division, feed) or the "
   "join fans out and n_chair is wrong")
ok("mt.div_id" in cte or "div_id" in cte, "and be keyed by div_id")
ok("source" in cte,
   "...and by source: meet_id is not one meet across feeds, the id spaces "
   "collide")

# ⚠ ONE ROW PER RESULT. Two branches over the same table can emit the same
#   result_id twice -- a race whose event name AND division both say
#   wheelchair -- and that is the same double-count by another route.
ok("!~* " in tf_half,
   "the division branch must exclude rows the event_short branch already "
   "took, or a race labelled in both columns is counted twice")


# ---- 5c. the run says where it is, and the scans go wide ---------------- #
#   Owner, 2026-09-09: "can we get more prints for it or somethgn it's slow
#   asf. I need it to go." The build was one multi-statement execute, so
#   nothing printed until it was over and a working run looked exactly like
#   a wedged one.
# ! COMMENTS STRIPPED, for the same reason as _bare above: the comment
#   saying "flush=True ON EVERY ONE" is not a flushed print.
build = "\n".join(
    ln.split("#")[0]
    for ln in SRC[SRC.index("_BUILD = ["):SRC.index("def main()")]
    .splitlines())
ok(build.count("_SQL_") >= 5,
   "the build must be a LIST of steps -- one execute for the lot is what "
   "made it silent")
# ! EVERY print, NOT SOME. A presence check passes with one flushed print
#   and three buffered ones, which is the same silence with extra steps.
ok(build.count("print(") == build.count("flush=True") > 0,
   f"{build.count('print(')} prints in the build, "
   f"{build.count('flush=True')} flushed -- the pipeline redirects stdout to "
   f"a log, and a block-buffered pipe holds every unflushed print until the "
   f"process exits, which is the whole problem")
ok("time.time()" in build and "import time" in SRC,
   "each step must report how long it took, or the prints say where it is "
   "but not what is expensive")

# ⚠ UNLOGGED, NEVER TEMP, AND THIS IS NOT A STYLE POINT. A parallel worker
#   cannot read a TEMP table, so making the scratch tables temporary turns
#   parallelism off on exactly the scans that need it.
for name in ("_SQL_DIV", "_SQL_XC", "_SQL_EV", "_SQL_DV"):
    body = _sql(name)
    ok("CREATE UNLOGGED TABLE" in body and "CREATE TEMP" not in body,
       f"{name} must build an UNLOGGED table, not a TEMP one: a parallel "
       f"worker cannot read a temp table")
ok("max_parallel_workers_per_gather" in SRC,
   "a regex over a whole table is the shape a parallel seq scan is for, and "
   "the default is 2")

# ! AND THE SCRATCH TABLES ARE CLEANED UP. They are real tables now.
ok("DROP TABLE wc_xc" in _sql("_SQL_RACE"),
   "the scratch tables must be dropped once wheelchair_race is built")


# ---- 5d. the totals are counted for the FLAGGED people only ------------- #
#   n_total used to be a hash aggregate over results + results_tf -- ~73M
#   rows into millions of groups, big enough to spill -- to then LEFT JOIN
#   the six hundred rows it wanted. Neither table has a person_id index, so
#   the scans stay; the aggregate does not have to.
people = _bare(re.search(r'_PEOPLE = """(.*?)"""', SRC, re.S).group(1))
ok("flagged" in people and "wheelchair_race" in people[:people.index("tot AS")],
   "the totals must be narrowed to the people already in wheelchair_race")
ok(people.count("IN (SELECT person_id FROM flagged)") == 2,
   "BOTH tables must be narrowed -- one of them left wide is the same scan")
# ⚠ AND STILL BOTH SPORTS. The share is about a CAREER: an athlete with one
#   chair XC race and forty track races is exactly the row --review exists
#   to show, and counting only XC would hide them at 100%.
ok("FROM results\n" in people or "FROM results " in people,
   "results must still be counted")
ok("results_tf" in people, "and results_tf")

# ! AND THE CENSUS HAS TO COUNT IT. The census read healthy for weeks
#   because a source nobody reads has no line to be zero on.
ok("tf.division" in SRC[SRC.index("_SUMMARY"):],
   "the census must count the TF-division source separately")
ok("via_tf_div" in SRC, "and print it")


# ---- 6. the rule itself is unchanged ---------------------------------- #
#   This is a stickiness fix, not a widening. If the regex ever narrows,
#   that is a different bug and should not hide behind the carry.
ok("_WORDS = " in SRC and "paralymp" in SRC,
   "the detection words must still be there")
ok("_CODES = " in SRC and r"\m[TF]" in SRC,
   "the T/F class codes must still be there")
ok("WHEELCHAIR_RX = " in SRC, "the exported rule must still be there")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
