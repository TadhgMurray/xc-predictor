"""/meets "Coming up" (weekend.py, 2026-10-05): the posted meets of the
next seven days, biggest first within a day, a missing table skipped, and
each a predictions link that carries the source (anet/tfrrs ids collide)."""
import datetime
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

try:
    import psycopg2                                            # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


class _Cur:
    """Answers each feed's SQL from canned rows; 'tfrrs' raises like a box
    without the table."""
    def __init__(self, by_key, missing=()):
        self.by_key, self.missing, self.rows = by_key, missing, []

    def execute(self, sql, p=None):
        import psycopg2
        if "SAVEPOINT" in sql:
            return
        key = ("tfrrs" if "meets_tfrrs" in sql else
               "anet_TF" if "meets_tf_meta" in sql else "anet_XC")
        if key in self.missing:
            raise psycopg2.ProgrammingError("relation does not exist")
        self.rows = self.by_key.get(key, [])

    def fetchall(self):
        return self.rows


@unittest.skipUnless(_HAVE, "psycopg2 not installed")
class ComingUp(unittest.TestCase):
    def test_days_order_and_links(self):
        import weekend
        cur = _Cur({
            "anet_XC": [
                {"meet_id": 1, "meet_name": "Dual", "date": "2026-10-10", "venue": "H",
                 "state": "OR", "n_races": 1, "level_mask": 4},
                {"meet_id": 2, "meet_name": "Invite", "date": "2026-10-10", "venue": "W",
                 "state": "CA", "n_races": 12, "level_mask": 12}],
            "anet_TF": [],
        }, missing=("tfrrs",))
        days = weekend.comingUp(cur, datetime.date(2026, 10, 9))
        self.assertEqual([d["label"] for d in days], ["Tomorrow"])
        self.assertEqual([m["name"] for m in days[0]["meets"]], ["Invite", "Dual"])
        self.assertEqual(days[0]["meets"][0]["level"], "HS & college")
        self.assertEqual(days[0]["meets"][0]["predict_href"],
                         "/predictions?meet_id=2&sport=XC")

    def test_tfrrs_link_names_its_source(self):
        import weekend
        cur = _Cur({"tfrrs": [{"meet_id": 7, "sport": "XC", "meet_name": "Pre-Nats",
                               "date": "2026-10-05", "venue": "V", "state": "WI",
                               "n_races": 2}]})
        days = weekend.comingUp(cur, datetime.date(2026, 10, 5))
        m = days[0]["meets"][0]
        self.assertEqual(days[0]["label"], "Today")
        self.assertEqual(m["level"], "College")
        self.assertIn("src=tfrrs", m["predict_href"])

    def test_outside_the_week_is_not_shown(self):
        import weekend
        cur = _Cur({"anet_XC": [{"meet_id": 1, "meet_name": "Later", "date": "2026-10-20",
                                 "venue": None, "state": None, "n_races": 1,
                                 "level_mask": 4}]})
        self.assertEqual(weekend.comingUp(cur, datetime.date(2026, 10, 5)), [])

    def test_a_failure_is_no_block(self):
        import weekend

        def boom():
            raise RuntimeError("db down")
        self.assertEqual(weekend.comingUpCached(boom, datetime.date(2026, 1, 1)), [])


if __name__ == "__main__":
    unittest.main()
