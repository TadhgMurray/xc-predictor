"""The timing-company registry (sources/registry.py): which parser each
source needs, whether it is independent of athletic.net and MileSplit,
and whether a run walks it.

    python -m pytest -q tests/test_sources_registry.py
"""
import csv
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import registry as R                                 # noqa: E402


@pytest.mark.parametrize("platform,name,urls,first", [
    ("MeetPro HTML (results.tfmeetpro.com/B__B_Race_KC/...)", "B & B Race KC", "", "meetpro"),
    ("Hy-Tek report HTML (own site, OPEN DIRECTORY LISTING)", "YenTiming Services", "", "hytek"),
    ("Custom site (own meet db, /meets/<id>)", "Finished Results", "", "custom"),
    ("AthleticLIVE", "Some Timing", "https://x.anet.live", "athleticlive"),
    ("MileSplit timer profile (results posted to MileSplit / milesplit.live); own site unconfirmed",
     "Acme Timing", "", "milesplit"),
    ("PTTiming platform (own JS app + Firebase)", "PrimeTime Timing", "", "blocked"),
    ("white label", "Leone Timing and Results Services", "", "blocked"),
    ("RunSignup timer site", "Road Co", "", "road"),
    ("dead (domain now unrelated blog)", "Gone Timing", "", "dead"),
    ("unconfirmed", "Mystery Timing", "", "unknown"),
    ("MileSplit only (no own site found)", "Quiet Timing", "", "milesplit"),
    ("MileSplit only (own site is a contact-form landing page, no results)", "Q2", "", "milesplit"),
    ("Road only on own site (RunSignup results); HS XC/TF only via MileSplit", "Road Two", "", "road"),
    ("Hy-Tek PDF (own site blacksquirrelresults.com); AthleticLIVE", "Black Squirrel", "", "hytek"),
])
def test_kinds(platform, name, urls, first):
    assert R.kindsOf(platform, name, urls)[0] == first


def test_athleticlive_with_its_own_hytek_pdfs_is_still_independent():
    assert R.kindsOf("Hy-Tek PDF (own site) + AthleticLIVE live results", "X", "")[:2] == \
        ["hytek", "athleticlive"]


def test_states():
    assert R.statesOf("CA (LA/Ventura/Orange); AZ, NON-US") == ["AZ", "CA"]


def test_load_and_flags(tmp_path):
    p = tmp_path / "t.csv"
    cols = ["name", "states", "website", "results_url", "platform", "sample_meet_url",
            "grade_and_school", "sports", "volume", "notes"]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for name, plat in (("A Timing", "MeetPro HTML"), ("A Timing", "AthleticLIVE"),
                           ("C Results", "Custom site"), ("PrimeTime Timing", "PTTiming platform")):
            w.writerow({"name": name, "states": "KS", "platform": plat})
    src = R.load(str(p))
    assert [s.slug for s in src] == ["a-timing", "a-timing-2", "c-results", "primetime-timing"]
    # MeetPro is enabled (its parser exists) but FloSports-owned, not independent
    assert [(s.independent, s.enabled) for s in src] == \
        [(False, True), (False, False), (True, False), (False, False)]
    sm = R.summary(src)
    assert sm["independent"] == 1 and sm["enabled"] == 1 and "KS" not in sm["states_without_independent"]


def test_the_real_file_loads():
    src = R.load()
    assert len(src) > 1000 and not any(s.kind == "blocked" and s.enabled for s in src)


# ---- the timers pass (2026-10-23) ---------------------------------------

def test_arrow_and_race_roster_are_their_own_independent_kinds():
    from sources.registry import kindsOf, INDEPENDENT
    assert kindsOf("Arrow Live Results (arrowliveresults.com; Agee's platform)")[0] == "arrow"
    assert kindsOf("PDF on own Wix event pages (+ RaceRoster for road)", urls="https://raceroster.com/x")[0] \
        == "raceroster"
    assert {"arrow", "raceroster"} <= INDEPENDENT


def test_a_later_pass_leads_and_own_alone_is_an_own_site():
    from sources.registry import kindsOf
    assert kindsOf("Own yearly event index pages || before 2026-10-23: AthleticLIVE")[0] == "custom"
    assert kindsOf("AthleticLIVE + OWN ColdFusion results DB with PDFs")[0] == "custom"
    # not an own site: a RunSignup page, TimerHub (terms forbid collection)
    assert "custom" not in kindsOf("RunSignup results (own RunSignup timer site; road) + MileSplit")
    assert "custom" not in kindsOf("MileSplit-hosted only; TimerHub (Red Cap's own multi-timer service)")


def test_off_limits_in_the_notes_is_blocked():
    from sources.registry import kindsOf
    assert kindsOf("Own yearly 'Event Results' pages (Wix)", notes="OFF LIMITS: bot challenge") == ["blocked"]


def test_a_timers_own_copy_of_meetpro_is_independent():
    from sources.registry import Source
    own = Source(slug="s", name="Snake River", states=["ID"], website="https://snakerivertiming.com",
                 results_url="https://snakerivertiming.com/2016/", sample_url="", kind="meetpro",
                 platform="MeetPro-style HTML (own site, historic)")
    hosted = Source(slug="m", name="M", states=["OH"], website="", sample_url="",
                    results_url="https://results.tfmeetpro.com/M/", kind="meetpro", platform="MeetPro (own site links)")
    assert own.independent and not hosted.independent
