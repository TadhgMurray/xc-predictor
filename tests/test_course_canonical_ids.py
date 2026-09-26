"""build_course_canonical.keepIds: a new venue never renumbers a course."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import build_course_canonical as B  # noqa: E402


def test_existing_ids_survive_a_new_venue_sorting_first():
    old = {("Beta Park", 1.0, 1.0): (1, "Beta Park"),
           ("Gamma Park", 2.0, 2.0): (2, "Gamma Park")}
    # the fresh clustering numbers by sort order, so "Alpha" takes 1
    rows = [("Alpha Park", 3.0, 3.0, 1, "Alpha Park", 1, 5),
            ("Beta Park", 1.0, 1.0, 2, "Beta Park", 1, 50),
            ("Gamma Park", 2.0, 2.0, 3, "Gamma Park", 1, 40)]
    out, nv, nid = B.keepIds(rows, old)
    ids = {r[0]: r[3] for r in out}
    assert ids == {"Beta Park": 1, "Gamma Park": 2, "Alpha Park": 3}
    assert (nv, nid) == (1, 1)


def test_a_new_spelling_joins_its_course():
    old = {("Beta Park", 1.0, 1.0): (7, "Beta Park")}
    rows = [("Beta Park", 1.0, 1.0, 1, "Beta Park", 2, 50),
            ("Beta Pk", 1.0001, 1.0, 1, "Beta Park", 2, 3)]
    out, nv, nid = B.keepIds(rows, old)
    assert {r[3] for r in out} == {7} and all(r[5] == 2 for r in out)
    assert (nv, nid) == (1, 0)
