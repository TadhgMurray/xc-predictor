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


class _Cur:
    """Answers splitCollisionTeams' two questions: the tables exist, and
    Penn has an Indiana cluster (the high school, biggest) and a PA one."""
    def __init__(self):
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"present": True}

    def fetchall(self):
        if "FROM school_identity" in self.sql:
            return [{"school": "Penn", "state": "IN"}, {"school": "Penn", "state": "PA"}]
        return []


class CollegeTeams(unittest.TestCase):
    def test_a_college_team_is_not_put_under_a_high_school_namesake(self):
        """2026-11-12: the Ivy League men's 8K scored Penn as "Penn (IN)"."""
        rs = [{"school": "Penn", "person_id": i, "rating_pool": "college_m",
               "school_state": "PA"} for i in range(7)]
        M.splitCollisionTeams(_Cur(), rs, meet_state="NY")
        self.assertEqual(schools(rs), [f"Penn{K}PA"])

    def test_high_school_runners_still_split(self):
        rs = [{"school": "Penn", "person_id": i, "rating_pool": "hs_m"} for i in range(7)]
        M.splitCollisionTeams(_Cur(), rs, meet_state="IN")
        self.assertTrue(all(r["school"].startswith("Penn" + K) for r in rs))
