"""One spelling per school in a qualifier field, never across states
(owner, 2026-11-16: Trinity (Tex.) listed as "Trinity (Conn.)")."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racecast"))

import qualifying as Q  # noqa: E402


def test_two_states_stay_two_schools():
    rounds = {"west": [(1, "Trinity (Tex.)", 1, 1), (2, "Trinity (Tex.)", 2, 1)],
              "mideast": [(3, "Trinity (Conn.)", 1, 1)]}
    out, _ = Q._oneSpelling(rounds, {})
    assert {r[1] for r in out["west"]} == {"Trinity (Tex.)"}
    assert {r[1] for r in out["mideast"]} == {"Trinity (Conn.)"}


def test_a_bare_spelling_joins_its_one_state():
    out = Q.oneSpellingField({1: "Trinity", 2: "Trinity (Conn.)", 3: "Trinity (Conn.)"})
    assert set(out.values()) == {"Trinity (Conn.)"}


def test_a_bare_spelling_between_two_states_stays():
    out = Q.oneSpellingField({1: "Trinity", 2: "Trinity (Conn.)", 3: "Trinity (Tex.)"})
    assert out == {1: "Trinity", 2: "Trinity (Conn.)", 3: "Trinity (Tex.)"}


def test_decoration_still_merges():
    out, _ = Q._oneSpelling({"x": [(1, "Washington St.", 1, 1), (2, "Washington State (WA)", 1, 1),
                                   (3, "Washington St.", 1, 1)]}, {})
    assert {r[1] for r in out["x"]} == {"Washington St."}
