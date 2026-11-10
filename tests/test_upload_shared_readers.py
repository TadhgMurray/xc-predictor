"""Uploaded results links go through the scrapers' own readers first
(racecast/upload_fetch.sharedRead), and what they land reaches the upload
parser unchanged. Off a real page: iResultsLIVE's MVC XC Championship 2026
boys varsity CSV (tests/fixtures/iresults).

    python -m pytest -q tests/test_upload_shared_readers.py
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import results_parse as RP                                       # noqa: E402
import upload_fetch as UF                                        # noqa: E402
from sources import iresults as I                                # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "iresults")


class _Site:
    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        with open(os.path.join(FIX, "csv_mvc_2026_boys_varsity.csv"), "rb") as fh:
            body = fh.read() if "BoysVarsity&" in url else b"Place,First,Last\n"
        return Fetched(url, 200, body, True, "")


def _mvc():
    with open(os.path.join(FIX, "index_latest_2026-11-10.html"), encoding="utf-8") as fh:
        ev = {e["eid"]: e for e in I.parseIndex(fh.read())}[6907]
    return asyncio.run(I.readMeet(_Site(), ev, 0))[0]


def test_what_the_readers_land_is_what_the_upload_parser_reads():
    lm = _mvc()
    name, data = "fetched.csv", UF.landedToCsv(lm)
    out = RP.parse(name, data)
    assert out["meet"]["name"] == "MVC XC Championship Meet 2026"
    assert out["meet"]["date"] == "2026-10-31"
    assert len(out["rows"]) == 88 == lm.rowCount()
    first = out["rows"][0]
    assert (first["place"], first["name"], first["school"], first["time_seconds"]) == (
        1, "Niles Mason", "Billerica", 927.0)
    (ev,) = out["events"]
    assert ev["gender"] == "M" and ev["sport"] == "XC"


def test_a_link_the_readers_do_not_know_goes_the_old_way(monkeypatch):
    async def none(url):
        return None
    monkeypatch.setattr(UF, "_sharedLanded", none)
    monkeypatch.setattr(UF, "checkUrl", lambda u: u)
    assert UF.sharedRead("https://example.org/results.htm") is None


def test_a_link_they_do_know_comes_back_as_the_upload_csv(monkeypatch):
    lm = _mvc()

    async def landed(url):
        return lm
    monkeypatch.setattr(UF, "_sharedLanded", landed)
    monkeypatch.setattr(UF, "checkUrl", lambda u: u)
    name, data = UF.sharedRead("https://www.iresultslive.com/results/?eid=6907")
    assert name == "fetched.csv" and data.startswith(b"meet_name,meet_date,location,event,gender")
