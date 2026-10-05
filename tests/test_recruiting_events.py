"""A college's recruiting thresholds in the reader's own events (owner,
2026-10-05: "should prolly be based on athlete")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import recruiting as R                                         # noqa: E402


def test_no_reader_is_the_default_three():
    assert R.subjectEvents(None) == list(R.THRESHOLD_EVENTS)


def test_an_athlete_gets_the_events_they_have_prs_in_in_order():
    sub = {"kind": "athlete", "prs": [{"key": "3200"}, {"key": "800"}, {"key": "5k"},
                                      {"key": "800"}, {"key": "nonsense"}]}
    assert [k for k, _d, _s in R.subjectEvents(sub)] == ["5k", "800", "3200"]
    assert R.subjectEvents(sub)[1] == ("800", 800.0, "TF")


def test_an_athlete_with_no_usable_pr_falls_back():
    assert R.subjectEvents({"kind": "athlete", "prs": []}) == list(R.THRESHOLD_EVENTS)


def test_a_typed_time_leads_with_its_event():
    keys = [k for k, _d, _s in R.subjectEvents({"kind": "time", "event": "mile"})]
    assert keys[0] == "5k" and "mile" in keys and "1600" in keys


def test_labels():
    assert R.eventLabel("5k") == "5K XC"
    assert R.eventLabel("2mi") == "2 mile"
