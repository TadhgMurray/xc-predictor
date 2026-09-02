"""The athlete page's bests include field events by best MARK, after the
running events, and never from a mark the parser refuses.

    python tests/test_athlete_bests_marks.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import athlete_bests as ab                                       # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)


def race(rid, sport, event, result, time_raw=None, is_field=0, rating=None,
         date="2025-04-12", season="2025"):
    return {"result_id": rid, "sport": sport, "event": event, "result": result,
            "time_raw": time_raw, "is_field": is_field, "speed_rating": rating,
            "date": date, "season_label": season}


races = [
    race(1, "TF", "1600m", "4:30.1", 270.1, rating=120),
    race(2, "TF", "110H", "15.20", 15.2, rating=None),
    race(3, "TF", "3000m Steeplechase", "9:40.0", 580.0, rating=118),
    race(4, "TF", "Shot Put", "45-03.25", is_field=1),
    race(5, "TF", "Shot Put", "48-00", is_field=1),          # the best
    race(6, "TF", "Shot Put", "FOUL", is_field=1),           # sentinel
    race(7, "TF", "Long Jump", "21-06.50", is_field=1),
    race(8, "TF", "Discus", "999-00", is_field=1),           # implausible
    race(9, "TF", "High Jump", "6-02", is_field=1),
    race(10, "XC", "5000", "16:10.0", 970.0, rating=125, date="2024-10-05",
         season="2024"),
]

alltime = ab.all_time_bests(races)
tf = alltime["TF"]["events"]
keys = list(tf)

ok("Shot Put" in tf and tf["Shot Put"]["result_id"] == 5,
   f"the longest parsed shot put is the best, got {tf.get('Shot Put', {}).get('result_id')}")
ok("Long Jump" in tf and "High Jump" in tf, "jumps are listed")
ok("Discus" not in tf, "an implausible mark never becomes a best")
ok(keys[-3:] == ["High Jump", "Long Jump", "Shot Put"],
   f"field events come after the running events, jumps then throws: {keys}")
ok(keys[0] == "110H" or keys[0].startswith("110"),
   f"hurdles still lead the running events by distance: {keys}")
ok("3000m Steeple" in keys, f"the steeple keeps its own row: {keys}")
ok(alltime["XC"]["events"] and list(alltime["XC"]["events"])[0] == "5000",
   "XC unchanged")

seasons = {("2025", "TF"): {"rating": 119.0}, ("2024", "XC"): {"rating": 124.0}}
sb = dict(ab.season_bests_flat(races, seasons))
ok(("2025", "TF") in sb and sb[("2025", "TF")]["events"]["Shot Put"]["result_id"] == 5,
   "season bests carry the field marks too")
ok(sb[("2025", "TF")]["season_rating"] == 119.0, "season rating lookup unchanged")

# a race dict with no is_field key behaves as before
ok(ab.fieldMark({"event": "Shot Put", "result": "48-00"}) == (None, None),
   "fieldMark is a no-op on non-field rows")

if failed:
    print("FAILED:")
    for m in failed:
        print("  -", m)
    sys.exit(1)
print("test_athlete_bests_marks: all checks passed")
