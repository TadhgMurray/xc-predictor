"""meet_compile._rejoinFalseSplits: the school-identity split must not
break one real team into short pieces (De La Salle, 2026-09-26)."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import meet_compile as M                                        # noqa: E402

K = M._KEYSEP


def rows(*spec):
    out = []
    for school, st, n in spec:
        out += [{"school": f"{school}{K}{st}", "person_id": len(out) + i}
                for i in range(n)]
    return out


def schools(rs):
    return sorted({r["school"] for r in rs})


class Rejoin(unittest.TestCase):
    def test_two_short_pieces_are_one_team_under_the_meet_state(self):
        rs = rows(("De La Salle", "LA", 4), ("De La Salle", "CA", 3))
        M._rejoinFalseSplits(rs, {"De La Salle"}, meet_state="CA")
        self.assertEqual(schools(rs), [f"De La Salle{K}CA"])

    def test_without_a_meet_state_the_bigger_piece_names_it(self):
        rs = rows(("De La Salle", "LA", 4), ("De La Salle", "CA", 3))
        M._rejoinFalseSplits(rs, {"De La Salle"})
        self.assertEqual(schools(rs), [f"De La Salle{K}LA"])

    def test_two_real_teams_stay_apart(self):
        rs = rows(("Jesuit", "CA", 7), ("Jesuit", "OR", 7))
        M._rejoinFalseSplits(rs, {"Jesuit"}, meet_state="OR",
                             published_names={"Jesuit": 2})
        self.assertEqual(schools(rs), [f"Jesuit{K}CA", f"Jesuit{K}OR"])

    def test_a_name_the_meet_published_once_is_one_team(self):
        rs = rows(("Oregon", "OR", 6), ("Oregon", "IL", 1))
        M._rejoinFalseSplits(rs, {"Oregon"}, meet_state="OR",
                             published_names={"OREGON": 1})
        self.assertEqual(schools(rs), [f"Oregon{K}OR"])

    def test_no_evidence_leaves_a_split_alone(self):
        rs = rows(("Oregon", "OR", 6), ("Oregon", "IL", 1))
        M._rejoinFalseSplits(rs, {"Oregon"}, meet_state="OR")
        self.assertEqual(schools(rs), [f"Oregon{K}IL", f"Oregon{K}OR"])


if __name__ == "__main__":
    unittest.main()
