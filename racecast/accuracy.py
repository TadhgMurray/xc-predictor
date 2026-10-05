"""accuracy.py -- the About page's "How accurate is it", read from the run
scorecard (scripts/run_report.py appends one line per pipeline run to
engine/data/run_history.jsonl).

★ ONLY THE FAIR TESTS, IN PLAIN WORDS (owner, 2026-10-05: "accuracy
  section on About"). run_report's FAIR TESTS are the ones that score the
  ratings on results the solve did not see; the HEALTH lines and the
  owner's sentinels are for us, not readers. Each test below says what was
  hidden and what was predicted, so the number means something without
  the log behind it.

! OURS ONLY. The scorecard also records a public comparison's numbers on
  the same tests; this page does not name or score anyone else.

! NEVER A 500. No history file (a fresh server, a run that never reached
  the report) or a bad line: the section is left out, the page renders.
"""
import datetime
import json
import os

HISTORY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "engine", "data", "run_history.jsonl")

# (key, headline, what was hidden and predicted, how to print the value)
TESTS = [
    ("holdout_race_sd", "Predicting a race it never saw",
     "Whole races are held out of the solve; each runner's time in them is "
     "predicted from their other races and the course. This is the typical "
     "miss (one standard deviation), and it includes the runner's own "
     "day-to-day variation, which no rating can remove.",
     lambda v: f"±{100 * v:.1f}%"),
    ("lacctic_order_ours", "Who finishes ahead",
     "Two runners from the same race, rated only from their other races: "
     "how often the higher-rated one finished ahead.",
     lambda v: f"{v:.0f}%"),
    ("lacctic_5000_ours", "Cross country to the track 5000",
     "A runner's cross country rating alone, converted to a 5000m track "
     "time, against the 5000 they actually ran: the typical spread.",
     lambda v: f"±{v:.1f}%"),
    ("sport_holdout_bias", "A track season from cross country alone",
     "Every track result is held out and predicted from cross country: how "
     "far off the predictions are on average, either way.",
     lambda v: f"{100 * abs(v):.1f}%"),
]

# the history table's column heads, in TESTS order (the full labels wrap
# to four lines a column on a phone)
SHORT = ("Unseen race", "Who's ahead", "XC to 5000", "Track from XC")
assert len(SHORT) == len(TESTS)


def _runDate(run, at):
    """The run's day: its log directory is YYYYMMDD_HHMMSS; else the stamp."""
    for s, fmt in ((str(run or "")[:8], "%Y%m%d"), (str(at or "")[:10], "%Y-%m-%d")):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def loadAccuracy(path=HISTORY):
    """{"as_of": date, "tests": [{label, value, text}], "history": [{date,
    values: [str|None per test]}], "labels": [...]} or None. History is
    every run that reported at least one test, newest first."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    runs = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        m = rec.get("metrics") or {}
        vals = [m.get(k) for k, *_ in TESTS]
        if any(isinstance(v, (int, float)) for v in vals):
            runs.append((_runDate(rec.get("run"), rec.get("at")), vals))
    if not runs:
        return None
    fmt = [f for *_, f in TESTS]
    latest_date, latest = runs[-1]
    tests = [{"label": lab, "text": text, "value": f(v)}
             for (k, lab, text, f), v in zip(TESTS, latest)
             if isinstance(v, (int, float))]
    history = [{"date": d, "values": [f(v) if isinstance(v, (int, float)) else None
                                      for f, v in zip(fmt, vals)]}
               for d, vals in reversed(runs)]
    return {"as_of": latest_date, "tests": tests, "history": history,
            "labels": list(SHORT)}
