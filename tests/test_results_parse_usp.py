"""Ultimate Sports Productions' results on MileSplit (owner, 2026-11-05:
DSAC Championships 2026, "No individual results were found in this file"):
'O'all / Place  Score Bib No Name  Team  Time  Pace  Grade', a '(> 7)' score,
a '5:59/M' pace, the grade after the time, per-team blocks after the list."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
import results_parse as R                                    # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "milesplit_usp_dsac_2026.html")


def test_the_whole_finish_list_reads():
    out = R.parse("fetched.htm", open(FIX, "rb").read())
    rows = out["rows"]
    assert len(out["events"]) == 1 and len(rows) == 74 and not out["warnings"]
    ev = out["events"][0]
    ev = ev if isinstance(ev, dict) else vars(ev)
    assert ev["distance_m"] == 5000.0 and ev["gender"] == "M" and out["sport"] == "XC"
    first = rows[0]
    assert (first["place"], first["name"], first["grade"], first["school"], first["time_seconds"]) \
        == (1, "Jainil Sheth", "12", "Newark Charter", 1113.9)
    tenth = rows[9]                                   # '(> 7)' in the score column
    assert (tenth["place"], tenth["name"], tenth["time_text"]) == (10, "Lincoln Barlow", "20:21.90")
    assert rows[-1]["place"] == 74                    # no team-block rows after the list
    assert out["meet"]["date"] == "2026-10-28"
    assert out["meet"]["name"] == "2026 Diamond State Conference XC Championships"


def test_the_pages_structured_data_fills_the_venue_state_and_sport():
    out = R.parse("fetched.htm", open(FIX, "rb").read())
    assert out["meet"]["location"] == "White Clay Creek State Park, Newark"
    assert out["meet"]["state"] == "DE" and out["sport"] == "XC"


def test_every_listed_results_file_is_fetched():
    import upload_fetch as UF
    page = ('<a href="/meets/781840-dsac/results/1337192/raw">raw</a>'
            '<script>let meetResultFiles = [{"id":1337192,"name":"Boys Junior Varsity"},'
            '{"id":1337191,"name":"Boys Varsity"},{"id":1337200,"name":"Girls Junior Varsity"},'
            '{"id":1337199,"name":"Girls Varsity"}];</script>')
    got = UF._rawLinks(page, "https://de.milesplit.com/meets/781840-dsac/results?type=formatted")
    assert [u.rsplit("/", 2)[-2] for u in got] == ["1337192", "1337191", "1337200", "1337199"]
    assert all(u.startswith("https://de.milesplit.com/meets/781840-dsac/results/") for u in got)
