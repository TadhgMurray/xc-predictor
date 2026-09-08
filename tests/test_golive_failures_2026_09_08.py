"""Run 20260908_030942: the joint go-live crashed three hours in, and the
pipeline published anyway.

    FAILED STEPS: 08_golive        ...and CHECKLIST PASS: 0 fail

Three independent faults, pinned here. All three share the shape that keeps
costing this project a night: a path that nothing had actually executed.

    python tests/test_golive_failures_2026_09_08.py

Text checks throughout -- joint_golive, speed_ratings_db and run_checklist
all need numpy or a database to import, and the pipeline is bash.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ================================================================== #
# 1. writeLive unpacked two arrays out of a three-array tuple
# ================================================================== #
#   ValueError: too many values to unpack (expected 2), at
#   joint_golive.py:510, AFTER course_difficulties and athlete_ratings were
#   written and before one result rating was -- so the database was left
#   holding the joint solve's cells and abilities over the OLD engine's
#   results.speed_rating. fbf4419 gave buildLive a third array (issue 171,
#   rating_pool on the row) and left the consumer at two; XCP_JOINT_LIVE was
#   off for the two days between, so no run reached the line (issue 310).
JG = _src("engine", "joint_golive.py")

_producer = re.search(r"per_sport\[name\] = \((.+?)\)\n", JG)
ok(_producer is not None, "buildLive no longer assigns per_sport[name]")


def _topLevelArity(expr):
    """Elements of a tuple body, counting only commas OUTSIDE any bracket.
    `np.round(chosen[m], 2)` is one element and carries a comma of its
    own; a naive count reads this tuple as four."""
    depth = n = 0
    for ch in expr:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return n + 1


if _producer:
    arity = _topLevelArity(_producer.group(1))
    ok(arity == 3,
       f"buildLive writes a {arity}-tuple into per_sport; this test and "
       f"writeLive both assume 3 (result_id, rating, pool)")

_loop = re.search(r"for name, (.+?) in live\[\"per_sport\"\]\.items\(\):", JG)
ok(_loop is not None, "writeLive no longer loops over live['per_sport']")
if _loop:
    target = _loop.group(1).strip()
    # ★ THE POINT OF THE FIX. Not "unpack three" -- unpack NOTHING. A name
    #   bound to the whole tuple cannot go stale the next time buildLive
    #   learns to carry another array, and saveResultSpeedRatings has taken
    #   the tuple whole since _asPairs learned the pool.
    ok(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target) is not None,
       f"writeLive destructures per_sport as `{target}`; bind the whole "
       f"tuple instead -- that is exactly the coupling that broke run17")
    body = JG[_loop.end():JG.index("\n    writeDistOffsets", _loop.end())]
    ok(f"saveResultSpeedRatings(name, {target})" in body,
       "writeLive must hand saveResultSpeedRatings the whole tuple")

# and the write path really does accept it, so the fix is not wishful
SRDB = _src("engine", "speed_ratings_db.py")
_ap = SRDB[SRDB.index("def _asPairs("):SRDB.index("def _stagingFor(")]
ok("len(pairs) > 2" in _ap and "len(row) > 2" in _ap,
   "_asPairs must accept (rid, val, pool) as well as (rid, val)")
ok('"pool" text' in SRDB or "pool text)" in SRDB,
   "the staging table needs its pool column for the triple to land")


# ================================================================== #
# 2. a failed 08_golive did not stop the run
# ================================================================== #
#   step() records the failure and carries on. Everything after 08
#   republishes results.speed_rating -- 09b_fill prices against it, 10_*
#   rebuild the boards, 13c/13d/13e push it to the search index, the
#   sitemap and Bing -- so a dead go-live means four more hours spent
#   dressing the PREVIOUS run's ratings up as today's.
PIPE = _src("deploy", "run_pipeline.sh")

ok("failed() {" in PIPE, "run_pipeline needs a `failed <step>` predicate")
_abort = PIPE.index("if failed 08_golive; then")
ok(_abort < PIPE.index("step 09b_fill"),
   "the 08_golive guard must come BEFORE 09b_fill -- the fill is the first "
   "step that reads results.speed_rating back out")
_guard = PIPE[_abort:PIPE.index("step 09b_fill")]
ok("summarise" in _guard,
   "the abort must print the usual closing summary and exit non-zero")
ok("XCP_IGNORE_GOLIVE_FAIL" in _guard,
   "keep the deliberate override for publishing the ratings as they stand")
# the override must be opt-IN: a bare run aborts
ok(re.search(r'XCP_IGNORE_GOLIVE_FAIL:-0', _guard) is not None,
   "XCP_IGNORE_GOLIVE_FAIL must default to 0")


# ================================================================== #
# 3. the wheelchair check cannot fail the way it keeps failing
# ================================================================== #
#   Checks 4 and 4a ask whether anyone on the exclusion list carries a
#   rating -- and the engine builds its refusals from that same list. They
#   catch a propagation failure and are blind to a detection failure, so a
#   wheelchair_person that has gone stale passes them both while chair
#   athletes rank. It goes stale whenever step 04b does not run, and the
#   standard --from 8 recipe had it on --skip.
ok("_ALWAYS=" in PIPE, "run_pipeline needs the un-skippable step list")
_always = re.search(r'_ALWAYS="([^"]*)"', PIPE)
ok(_always is not None and "04b_wheelchair" in _always.group(1),
   "04b_wheelchair must be immune to --from: wheelchair_person is a fact "
   "later steps consult, not work they redo")
ok(_always is not None and "02_drop_old" in _always.group(1),
   "02_drop_old must stay immune to --from")
# --skip is explicit and still honoured -- but not quietly
ok("IS ON THE --skip LIST" in PIPE,
   "skipping 04b_wheelchair must say what it costs")

# ★ AND THE LIST ONLY REACHES THE RATINGS THROUGH THE PACK. _chairFilter()
#   is interpolated into both pack queries, so rebuilding wheelchair_person
#   without rebuilding the pack leaves the chair athletes in the solve.
#   Step 07 runs with --cache, which reuses packed_XC_TF.npz when the file
#   exists -- so the clear must fire whenever 07 is going to run, or `--from
#   7` is a step that rebuilds nothing.
ok("_chairFilter()" in SRDB.split("def _chairFilter(")[-1] or
   SRDB.count("_chairFilter()") >= 3,
   "_chairFilter must still be interpolated into the pack queries")
# the gate line immediately above the 06_clear_cache block
_clear = PIPE.rindex('if [ "$FROM" -le ', 0, PIPE.index("06_clear_cache"))
_gate = PIPE[_clear:PIPE.index("]", _clear)].split("-le")[1].strip()
ok(_gate == "7",
   f"06_clear_cache is gated at --from {_gate}; it must fire for 7 too, "
   f"since 07_pack reuses the cached pack and `--from 7` would then "
   f"rebuild nothing")

CK = _src("scripts", "run_checklist.py")
ok("def checkWheelchairFresh(" in CK,
   "run_checklist needs the freshness check (4a-ii)")
ok("checkWheelchairFresh(cur)" in CK.split("def main()")[1],
   "checkWheelchairFresh must actually be called from main()")
_fresh = CK[CK.index("def checkWheelchairFresh("):CK.index("# ---- 4b.")]
# ★ IT MUST CONSULT THE RULE, NOT THE TABLE. Reading wheelchair_person
#   again is what makes 4 and 4a tautological; the whole value of 4a-ii is
#   that it re-derives the answer from the corpus with the engine's own
#   regex -- which is also WIDER than the checklist's label regex (it has
#   the para words and the T/F class codes).
ok("WHEELCHAIR_RX" in _fresh,
   "4a-ii must apply the engine's own rule, not re-read the stored list")
ok("from wheelchair_flag import" in _fresh,
   "import the rule from the module that owns it, so the two cannot drift")
ok("statement_timeout" in _fresh,
   "the rescan must be bounded -- it runs at the end of a five-hour night")
# just the rescan's own try/except, not the reporting below it
_timeout_branch = _fresh[_fresh.index("cur.execute(_FRESH_SQL"):
                         _fresh.index('statement_timeout = 0')]
ok('_mark("WARN"' in _timeout_branch and '_mark("PASS"' not in _timeout_branch,
   "a rescan that times out is a WARN, never a PASS: 'we did not look' and "
   "'we looked and it was clean' are different answers")


# ================================================================== #
# 4. the race-day term stays OUT of the per-result rating
# ================================================================== #
#   Owner, 2026-09-06: a slow race is a slow race, and the model cannot
#   tell mud from a jog. The tilt visible on the site after run17 is not
#   this term coming back -- it is the OLD engine's ratings still sitting
#   in results.speed_rating because the go-live died before writing (1).
RJ = _src("engine", "run_joint.py")
_flag = re.search(r'ap\.add_argument\("--race-effect-sports",\s*default="([^"]*)"',
                  RJ)
ok(_flag is not None and _flag.group(1) == "",
   "--race-effect-sports must default to none: the solve keeps the term, "
   "the rating does not carry it for either sport")
ok("--race-effect-sports" not in PIPE,
   "run_pipeline must not put the race-day term back into the ratings")
# and the go-live has to SAY which way it went, in the log
ok("the rating\"" in JG and "'IN' if name in race_effect_sports" in JG,
   "joint_golive must log IN/OUT OF the rating per sport")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: "
          f"{len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
