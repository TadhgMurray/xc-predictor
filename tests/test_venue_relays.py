"""app.get_tf_venue_relay_records: a track venue's team records are its
relays, best time per school, by event and gender (owner, 2026-09-26)."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

try:
    import flask                                               # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


def row(ev, school, t, g):
    import re
    return {"result_id": hash((ev, school, t)), "school": school,
            "time_seconds": t, "date": "2025-04-12", "event_short": ev,
            "meet_id": 1, "meet_name": "Arcadia", "gender": g,
            "ekey": re.sub(r"[^a-z0-9]", "", ev.lower())}


class Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *a):
        pass

    def fetchall(self):
        return self.rows


@unittest.skipUnless(_HAVE, "flask not installed")
class Relays(unittest.TestCase):
    def test_best_per_school_by_event_and_gender(self):
        import app as A
        rows = [row("4x400m Relay", "Jesuit", 200.1, "M"),
                row("4 x 400 Meter Relay", "Jesuit", 198.3, "M"),   # better
                row("4x400", "Loyola", 199.0, "M"),
                row("4x800m", "Carondelet", 560.0, "F"),
                row("DMR", "Loyola", 640.2, None),
                row("100m", "Jesuit", 10.9, "M")]                   # not a relay
        got = A.get_tf_venue_relay_records(Cur(rows), 5, False)
        heads = [(g["gender"], g["event"]) for g in got]
        self.assertEqual(heads, [("M", "4x400"), ("F", "4x800"),
                                 ("?", "Distance medley")])
        m400 = got[0]["rows"]
        self.assertEqual([r["school"] for r in m400], ["Jesuit", "Loyola"])
        self.assertEqual(m400[0]["display_time"], "3:18.3")

    def test_4x1600_is_not_4x100(self):
        import app as A
        got = A.get_tf_venue_relay_records(
            Cur([row("4x1600m Relay", "Jesuit", 1050.0, "M")]), 5, False)
        self.assertEqual(got[0]["event"], "4x1600")


if __name__ == "__main__":
    unittest.main()
