"""Issue #95: a team's state is DISPLAYED beside its name and never KEYED on.

The state has to ride alongside the school rather than inside it. Every data
path -- the squad endpoint, teamIsIn, _score's grouping, ranking_results.school
-- matches on the bare name, and folding the state into it is precisely the
bug that broke add/remove (#87).

    python tests/test_predict_school_state.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = io.open(os.path.join(ROOT, "racecast", "predict.py"), encoding="utf-8").read()
JS = io.open(os.path.join(ROOT, "racecast", "static", "predictions.js"),
             encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# 1. The state is a SEPARATE field. Nothing writes it into `school`.
ok('"state": states.get(school) or _stateOf(school)' in PY,
   "meetField's teams carry a state -- their runners' first, the name's second "
   "(owner, 2026-09-25: the wrong Antioch)")
ok(PY.count("_stateOf(") >= 4,
   "every team-building path stamps one, not just the first")
ok(not re.search(r'"school":\s*f"\{[^"]*\}\s*\(\{_stateOf', PY),
   "the state is never folded into the school name server-side")

# 2. It is stamped BEFORE _combinedRoster rewrites the name. A school in two
#    divisions comes back as "Broughton (Varsity)", which the identity table
#    has never heard of.
stamp = PY.index('e["school_state"] = states.get(e.get("school")) or _stateOf(e.get("school"))')
suffix = PY.index('e["school"] = f"{sch} ({labels[d]})"')
ok(stamp < suffix,
   "roster entries are stamped before the division suffix is applied -- "
   "after it, the name is a scoring key and not a school")

# 3. _score carries it to the team result without re-deriving it from `team`,
#    which by then may be a suffixed key.
# Just _score's own body: the slice must not run on into _stateOf, which is
# defined further down and would satisfy the "no lookup here" check falsely.
_i = PY.index("def _score(")
sc = PY[_i:PY.index("\ndef ", _i + 1)]
ok('state.setdefault(team, runner.get("school_state"))' in sc,
   "_score takes the state off a RUNNER, not off the grouping key")
ok(sc.count('"state": state.get(team)') == 2,
   "both the scoring and the incomplete-team branches carry it")
ok("_stateOf" not in sc,
   "_score does not look the state up again from a name that may be suffixed")

# 4. The client composes for DISPLAY ONLY. The composed string must never
#    reach an href, a data- attribute, or a lookup.
ok("function schoolWithState(" in JS, "the client has one composer")
card = JS[JS.index('<details class="team-card"'):JS.index('</details>')]
ok('href="/school/${encodeURIComponent(t.school)}"' in card,
   "the link still uses the BARE name")
ok('data-team="${esc(t.school)}"' in card, "and so does the card's key")
ok('data-squad="${esc(t.school)}"' in card, "and the squad button")
ok("schoolWithState(t.school, t.state)" in card,
   "only the visible text is composed")
for bad in ('data-team="${esc(schoolWithState',
            'data-squad="${esc(schoolWithState',
            'encodeURIComponent(schoolWithState'):
    ok(bad not in JS, f"the composed name must never be keyed on: {bad}")

# 5. A school with no known state renders bare, not as "Name (null)".
ok(re.search(r"return st \?[^;]*: school", JS) is not None,
   "a missing state falls back to the bare name")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  the state rides alongside the school, never inside .. OK")
print("  stamped before the division suffix is applied ....... OK")
print("  _score carries it off a runner, not off the key ..... OK")
print("  the client composes for display only ................ OK")
print("\nall school-state checks passed")
