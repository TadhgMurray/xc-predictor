"""copy_align: a runner only one copy of a race lists is priced off the other
copy's field (owner, 2026-11-06: Dalton Mortensen 128.9 on tfrrs's BYU
Autumn Classic, Noah Jenkins 115.2 on athletic.net's, one 19:55)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import copy_align as C                                          # noqa: E402

A = ("anet", 1, 11, "2025-09-05")
T = ("tfrrs", 25861, 1, "2025-09-05")


def _divs():
    anet = [(100 + i, 10 + i, "college_m", 1195.0 + 6 * i, 115.2 - 0.5 * i) for i in range(8)]
    tf = [(200 + i, 10 + i, "college_m", 1195.0 + 6 * i, None) for i in range(6)]   # twins
    tf.append((300, 99, "college_m", 1195.0, 128.9))                # Mortensen, tfrrs only
    tf.append((301, 98, "college_m", 1215.0, 114.0))                # already in line
    return {A: anet, T: tf}


def test_the_lone_copy_row_takes_the_reference_fields_price():
    moves = C.plan(_divs(), {(A, T): 6})
    assert [m[0] for m in moves] == [300]
    assert abs(moves[0][2] - 115.2) < 1.0 and moves[0][3] == A


def test_not_copies_under_min_shared_and_a_row_in_both_is_left():
    assert C.plan(_divs(), {(A, T): 4}) == []
    d = _divs()
    d[A].append((150, 99, "college_m", 1195.0, 115.0))              # Mortensen in both
    assert C.plan(d, {(A, T): 6}) == []


def test_expected_is_the_fields_rating_scaled_by_time():
    ref = [(1200.0, 110.0), (1210.0, 109.1), (1190.0, 110.9)]
    assert abs(C.expected(1200.0, ref) - 110.0) < 0.2
    assert C.expected(1200.0, ref[:2]) is None
