"""The college directory's name key keeps 'State' and reads 'U. of' (owner,
2026-11-05: /school/Chicago read "NCAA DI" -- Chicago State's -- and
'U. of Chicago' was a page of its own)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import build_college_directory as B                           # noqa: E402


def test_state_universities_are_not_their_states_flagships():
    assert B.normName("Chicago State University") == "chicago state" != B.normName("University of Chicago")
    assert B.normName("Ohio State University") == "ohio state" != B.normName("Ohio University")
    assert B.normName("Kent State University") == B.normName("Kent State") == "kent state"


def test_u_of_is_university_of():
    assert B.normName("U. of Chicago") == B.normName("University of Chicago") == "chicago"
    assert B.normName("Univ. of Iowa") == "iowa"
    assert B.normName("Connecticut College") == "connecticut college"     # unchanged
