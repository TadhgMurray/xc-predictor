"""Chair athletes are not training data.

Owner, 2026-09-09: "Can we train the model yet?"

★ THE ENGINE HAS EXCLUDED THEM FOR WEEKS AND THE MODEL DID NOT. The pack
  reads wheelchair_person through speed_ratings_db._chairFilter(); the
  transformer's feature query never did, so every chair race has been in the
  training set the whole time as a valid athlete trajectory.

  A racing chair covers 1500m far faster than a pair of legs. It is not a
  fast runner or a slow one -- it is a different sport in the same units.
  Kohen Grantom's 1:42.68 800m sits beside a 16.54 100m in the same meet;
  a sequence model fed both learns that a person can do that.

⚠ AND THE POPULATION GREW BY AN ORDER OF MAGNITUDE. Reading the anet track
  feed's division column took the list from 481 people to 6,056 -- 39,555
  races. Whatever this cost before, it costs eight times that now.

! ONE LIST, ONE RULE, THREE READERS. wheelchair_person is a fact about a
  PERSON, not a race: one confirmed chair race condemns the career, because
  an ordinary race by a chair athlete is still that athlete. The boards, the
  engine and now the model all consult the same table, so they cannot drift.

    python tests/test_model_excludes_chairs.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "model", "feature_extraction.py"),
              encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. both sports, or half the training set keeps them --------------- #
ok(SRC.count("FROM wheelchair_person wp") == 2,
   f"both the XC and TF queries must exclude chair athletes, found "
   f"{SRC.count('FROM wheelchair_person wp')}")

# ⚠ PERSON, NOT athlete_id. The list is kept per person_id because one
#   athlete_id is one (name, school) pairing and a person can have several;
#   keying the exclusion on athlete_id would leak every chair athlete who
#   ever changed schools.
ok(SRC.count("wp.person_id = r.person_id") == 2,
   "both must key on person_id -- an athlete_id is one (name, school) "
   "pairing and a person can hold several")

# ! NOT EXISTS, NOT A JOIN. A join to a list would drop rows whose person_id
#   is NULL as a side effect, silently changing the sample.
ok(SRC.count("NOT EXISTS (SELECT 1 FROM wheelchair_person") == 2,
   "an anti-join, so a NULL person_id is kept rather than quietly dropped")


# ---- 2. it sits with the other row filters ----------------------------- #
for marker in ("_XC_SQL", "_TF_SQL"):
    i = SRC.index(marker)
    j = SRC.index("_ORDER_BY", i)
    ok("wheelchair_person" in SRC[i:j],
       f"{marker} must carry the exclusion inside its own WHERE, not rely "
       f"on a caller filtering afterwards")


# ---- 3. the same table the engine reads -------------------------------- #
#   If the model ever grew its own chair list, the two would drift and
#   nothing would say which was right.
ENG = io.open(os.path.join(ROOT, "engine", "speed_ratings_db.py"),
              encoding="utf-8").read()
ok("wheelchair_person" in ENG,
   "the engine must still read the same table -- if this ever moves, the "
   "model's filter has to move with it")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
