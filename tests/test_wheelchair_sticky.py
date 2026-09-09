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


# ---- 5b. BOTH track columns are read ---------------------------------- #
#   Owner, 2026-09-09, pasting the race page: "800m · Boys · Wheelchair ·
#   Outdoor". The word is in meets_tf.division and the event_short is a
#   plain "800m" -- the anet track feed puts the class in the DIVISION and
#   leaves the event bare, while the tfrrs one puts it in the event name.
#   Reading only event_short missed every anet chair track race, which is
#   how a 1:42.68 800m (beside a 16.54 100m, in the same meet) came out
#   150.8 and first on the high-school boys performance board.
races = re.search(r'_RACES = """(.*?)"""', SRC, re.S).group(1)
tf_half = races[races.index("SELECT 'TF'"):]
# ! THE PREDICATE, NOT THE SELECT LIST. An earlier version of this check
#   only looked for "mt.division" anywhere in the branch -- which the
#   COALESCE in the SELECT satisfies, so deleting the WHERE clause that
#   actually MATCHES on it still passed. Assert what filters.
where = tf_half[tf_half.index("WHERE"):]
ok("mt.division, '')  ~* " in where or "mt.division, '') ~* " in where,
   "the TF branch must MATCH on meets_tf.division, not merely select it")
ok("event_short, '') ~* " in where,
   "...and must still match event_short: tfrrs puts the class there")
ok("mt.div_id" in tf_half and "mt.source" in tf_half,
   "the meets_tf join must be narrowed by div_id AND source -- that table "
   "holds one row per event and would otherwise fan out")

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
