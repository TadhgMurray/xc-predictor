"""A team is keyed by its OWN state, not the one it raced in (owner, 2026-09-08).

    1  Air Force      OK   ...
    2  Northern Arizona FL ...
    5  BYU            WI   ... 5 athletes
    7  BYU            OK   ... 6 athletes
    8  BYU            FL   ... 6 athletes

ranking_results.state is where the RESULT happened; athlete_season.state is
the mode of a season's rows; team_rank.teamKey keys a squad (school, state).
A college races away most weekends, so one squad became several teams --
which breaks the invariant build_team_season states in its own docstring:
"A team sits in exactly one state ... two rows per team, never more."

    python tests/test_team_state.py

school_identity needs a database to load its map, so the map is stubbed
here and the RULE is exercised for real.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


import school_identity as si                                    # noqa: E402

# The map build_school_identity would produce: BYU's co-racing clusters
# merged to one (UT); the two Kingstons, which never share a race, apart.
si._LABELS["loaded"] = True
si._LABELS["map"] = {"BYU": "UT", "Air Force": "CO", "Oregon": "OR",
                     "Kingston": "WA", "Newbury Park": "CA"}
si._LABELS["clusters"] = {
    "BYU": {"UT": 1.0},                 # merged: no WI/OK/FL cluster left
    "Air Force": {"CO": 1.0},
    "Oregon": {"OR": 1.0},
    "Kingston": {"WA": 0.62, "MO": 0.38},   # two real schools, one name
    "Newbury Park": {"CA": 1.0},
}
si._LABELS["college"] = None            # no directory in this process


# ---- 1. a travel state is replaced by the school's own ---------------- #
for school, raced_in in (("BYU", "WI"), ("BYU", "OK"), ("BYU", "FL"),
                         ("Air Force", "OK"), ("Oregon", "CA")):
    got = si.teamState(school, "college_m", raced_in)
    want = si._LABELS["map"][school]
    ok(got == want,
       f"{school} raced in {raced_in} and keys as {got}; want {want}")

# and so the three BYUs collapse to ONE team key
from team_rank import teamKey                                    # noqa: E402
keys = {teamKey("BYU", si.teamState("BYU", "college_m", st))
        for st in ("WI", "OK", "FL", "UT")}
ok(len(keys) == 1,
   f"BYU still splits into {len(keys)} team keys across its travel states")


# ---- 2. but two real schools sharing a name STAY apart ---------------- #
#   This is the half that makes primaryState() alone the wrong fix: the
#   (school, state) key exists to separate them, and collapsing every row
#   to the primary would merge Kingston MO into Kingston WA.
ok(si.teamState("Kingston", "hs_m", "MO") == "MO",
   "Kingston MO has its own cluster and must keep it")
ok(si.teamState("Kingston", "hs_m", "WA") == "WA",
   "Kingston WA is the primary and keeps it")
ok(teamKey("Kingston", si.teamState("Kingston", "hs_m", "MO"))
   != teamKey("Kingston", si.teamState("Kingston", "hs_m", "WA")),
   "the two Kingstons must remain two teams")


# ---- 3. an unknown school keeps whatever it had ----------------------- #
ok(si.teamState("Nowhere Prep", "hs_m", "TX") == "TX",
   "a school the identity table does not know keeps its own state")
ok(si.teamState(None, "hs_m", "TX") == "TX", "no school: state passes through")
ok(si.teamState("BYU", "college_m", None) == "UT",
   "no context state still resolves to the primary")


# ---- 4. the KEY and the LABEL cannot disagree ------------------------- #
#   schoolLabelFor is a wrapper over teamState precisely so a board cannot
#   key a team one way and print it another.
for school, st in (("BYU", "WI"), ("Kingston", "MO"), ("Nowhere Prep", "TX")):
    lab = si.schoolLabelFor(school, "hs_m", st)
    key_state = si.teamState(school, "hs_m", st)
    ok(lab == f"{school} ({key_state})",
       f"label {lab!r} disagrees with key state {key_state!r}")


# ---- 5. the build actually calls it, and says when it did nothing ----- #
BTS = io.open(os.path.join(ROOT, "racecast", "build_team_season.py"),
              encoding="utf-8").read()
ok("resolvedStates(" in BTS, "build_team_season must resolve states")
ok("loadLabels(getConn)" in BTS,
   "the identity map has to be loaded or every row keeps its venue state")
i = BTS.index("for board in boards(")
call = BTS[i:BTS.index("\n", BTS.index("stats))", i))]
ok("resolvedStates" in call,
   "resolvedStates must be in the generator chain feeding boards()")
# ⚠ the silent-degrade guard: a zero restated count is the old behaviour
ok("restated" in BTS and "10b_school_ids" in BTS,
   "the build must report the restated count and name the step that fills "
   "school_identity -- degrading silently is how this survived")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
