"""Results uploads, the reader (owner, 2026-10-10): Hy-Tek Meet Manager /
Team Manager text, Hy-Tek HTML, and the CSV template, on hand-written
fixtures in the layouts those programs print (tests/fixtures/hytek/).

    python -m pytest -q tests/test_results_parse.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

import pytest                                                    # noqa: E402
import results_parse as RP                                       # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "hytek")


def load(name):
    with open(os.path.join(FIX, name), "rb") as fh:
        return RP.parse(name, fh.read())


def rows_of(out, event_n):
    return [r for r in out["rows"] if r["event"] == event_n]


def by_name(out, name):
    return next(r for r in out["rows"] if r["name"] == name)


# ---------------------------------------------------------------- Meet Manager XC

def test_meet_manager_cross_country_header_events_and_rows():
    out = load("mm_xc_lakeside.txt")
    assert out["format"] == "hytek_text" and out["software"] == "Meet Manager"
    assert out["licensee"] == "Lincoln High School"
    assert out["meet"] == {"name": "Lakeside Cross Country Invitational", "date": "2025-10-04",
                           "date_end": None, "location": "Lakeside Park, Fairview, OR"}
    assert out["sport"] == "XC"
    titles = [(e["title"], e["gender"], e["distance_m"], e["division"]) for e in out["events"]]
    assert titles == [("Girls 5000 Meter Run CC Varsity", "F", 5000.0, "Varsity"),
                      ("Boys 5000 Meter Run CC Varsity", "M", 5000.0, "Varsity"),
                      ("Boys 2 Mile Run CC Freshman", "M", 3218.7, "Freshman")]
    # nine girls (eight placed + a DNF): the team-score block is NOT read as runners
    girls = rows_of(out, 1)
    assert len(girls) == 9 and all(r["school"] != "Sunset  13" for r in girls)
    assert not any(r["name"].startswith(("Sunset", "Jesuit")) for r in out["rows"])
    first = girls[0]
    assert (first["place"], first["name"], first["grade"], first["school"], first["time_seconds"]) == \
        (1, "Ann Doe", "10", "Sunset", 1065.21)
    assert by_name(out, "Kim Brown")["status"] == "DNF" and by_name(out, "Kim Brown")["place"] is None


def test_a_race_continued_on_the_next_page_stays_one_race():
    out = load("mm_xc_lakeside.txt")
    boys = rows_of(out, 2)
    # page 3 opens with "(Event 2 ...)": its two rows join event 2, not a new one
    assert len(out["events"]) == 3 and len(boys) == 11
    assert [r["place"] for r in boys if r["place"]][-2:] == [9, 10]
    assert by_name(out, "Ben Sato")["status"] == "DNS"


def test_names_years_and_schools_survive_awkward_columns():
    out = load("mm_xc_lakeside.txt")
    jr = by_name(out, "John Rivera Jr")             # 'Rivera Jr, John': Jr is not the year
    assert jr["grade"] == "12" and jr["school"] == "Sunset"
    # a school too long for its column pushes the time right; still read
    ronan = by_name(out, "Ronan McMahon-Staggs")
    assert ronan["school"] == "Mountain View Christian Academy" and ronan["time_seconds"] == 940.02
    assert by_name(out, "Gabe Lindqvist")["grade"] == "SR"
    assert by_name(out, "Kate O'Brien")["school"] == "Jesuit"
    # tenths, as many XC timers print them
    assert by_name(out, "Chidi Abara")["time_seconds"] == 655.4


# ---------------------------------------------------------------- Meet Manager track

def test_track_seed_column_is_not_the_result():
    out = load("mm_track_county.txt")
    assert out["sport"] == "TF"
    assert out["meet"]["name"] == "2025 Washington County Championships"
    assert (out["meet"]["date"], out["meet"]["date_end"]) == ("2025-05-09", "2025-05-10")
    mile = rows_of(out, 1)
    ann = mile[0]
    assert ann["name"] == "Ann Doe" and ann["time_seconds"] == 298.31   # 4:58.31*, not the 5:01.22 seed
    assert by_name(out, "Olivia Grant")["time_seconds"] == 315.03      # seed 'NT'
    assert by_name(out, "Riya Patel")["status"] == "SCR"
    # the record line above the first runner is not a runner
    assert not any("Moore" in r["name"] for r in out["rows"])


def test_prelims_and_finals_are_both_kept_and_told_apart():
    out = load("mm_track_county.txt")
    dash = rows_of(out, 2)
    assert [(r["name"], r["round"], r["time_seconds"]) for r in dash] == [
        ("Marcus Johnson", "P", 10.95), ("Daniel Kim", "P", 11.08), ("Tobi Adeyemi", "P", 11.19),
        ("Marcus Johnson", "F", 10.88), ("Daniel Kim", "F", 11.01)]


def test_relays_and_field_events_are_skipped_and_said():
    out = load("mm_track_county.txt")
    kinds = {e["title"]: e["kind"] for e in out["events"]}
    assert kinds["Boys 4x400 Meter Relay"] == "relay" and kinds["Girls Long Jump"] == "field"
    relay_n = next(e["n"] for e in out["events"] if e["kind"] == "relay")
    field_n = next(e["n"] for e in out["events"] if e["kind"] == "field")
    assert not rows_of(out, relay_n) and not rows_of(out, field_n)
    texts = " ".join(w["text"] for w in out["warnings"])
    assert "relays are not imported" in texts and "field events are not imported" in texts


def test_split_lines_under_a_runner_are_not_runners():
    out = load("mm_track_county.txt")
    two = rows_of(out, 5)
    assert [r["name"] for r in two] == ["Owen Castellano", "Ezra Goldfarb", "Alan Wu", "Mateo Garcia Lopez"]
    assert by_name(out, "Mateo Garcia Lopez")["time_seconds"] == 598.2


# ---------------------------------------------------------------- Team Manager, padding lost

def test_team_manager_with_single_spaced_lines():
    out = load("tm_xc_collapsed.txt")
    assert out["software"] == "Team Manager" and out["sport"] == "XC"
    assert out["meet"]["name"] == "Riverside Twilight XC" and out["meet"]["date"] == "2025-09-20"
    assert out["meet"]["location"] == "Riverside Golf Course"
    assert [e["distance_m"] for e in out["events"]] == [6000.0, 8000.0]
    k = by_name(out, "Kerem Ayhan")
    assert (k["grade"], k["school"], k["time_seconds"]) == ("SO-2", "Lehigh", 1300.5)
    assert by_name(out, "Erika Suhy")["school"] == "Robert Morris"
    assert by_name(out, "Soheib Dissa")["school"] == "UNAT-Duke"
    assert by_name(out, "Cole Sprout")["grade"] == "SR"      # 'SPROUT, COLE' title-cased
    assert by_name(out, "David Hemery")["school"] == "Club Northwest"
    assert by_name(out, "Regan Holmes")["status"] == "DNF"


# ---------------------------------------------------------------- HTML

def test_hytek_html_reads_the_pre_block_and_nothing_else():
    out = load("mm_xc_stmarys.htm")
    assert out["format"] == "hytek_html"
    assert out["meet"]["name"] == "St. Mary's Invitational"
    assert out["meet"]["location"] == "Hidden Valley Park & Trails"
    names = [r["name"] for r in out["rows"]]
    assert names == ["Mai Tran", "Isabel Ochoa", "Siobhan O'Neil"]
    assert out["rows"][0]["school"] == "St. Mary's Academy"
    # the script's fake runner and the commented-out one are never read
    assert "Injected Fake" not in names and "Out Commented" not in names
    assert out["events"][0]["distance_m"] == 4828.0 and out["events"][0]["division"] == "JV"


# ---------------------------------------------------------------- CSV

def test_csv_template_reads_itself():
    out = RP.parse("template.csv", RP.CSV_TEMPLATE.encode())
    assert out["format"] == "csv" and out["sport"] == "XC"
    assert out["meet"] == {"name": "Lakeside Invitational", "date": "2025-10-04", "date_end": None,
                           "location": "Lakeside Park"}
    assert [r["name"] for r in out["rows"]] == ["Owen Castellano", "Ezra Goldfarb", "Ann Doe"]
    assert out["rows"][2]["status"] == "DNF" and out["rows"][2]["place"] is None
    assert {e["gender"] for e in out["events"]} == {"M", "F"}
    assert not [w for w in out["warnings"] if w["line"]]
    for col in RP.CSV_COLUMNS:
        assert col in RP.CSV_TEMPLATE.splitlines()[0]


def test_csv_with_other_column_names_and_bad_rows():
    out = load("results_synonyms.csv")
    assert out["meet"]["name"] == "Harbor League Finals" and out["meet"]["date"] == "2025-10-18"
    assert [r["name"] for r in out["rows"]] == ["Maya Torres", "Sun Kim", "Lena Fox"]
    texts = [w["text"] for w in out["warnings"]]
    assert any("eighteen" in t for t in texts)                   # a time nobody can read
    assert any(t.startswith("Not a name") for t in texts)        # =HYPERLINK(...) is not a runner
    # the girls' race from its distance column; the boys' only row was not a
    # runner, so there is no boys' race at all
    assert [(e["gender"], e["distance_m"]) for e in out["events"]] == [("F", 5000.0)]
    assert RP.distanceOf("3 mi") == 4828.0


def test_csv_without_a_name_or_time_column_is_refused():
    with pytest.raises(RP.ParseError):
        RP.parse("x.csv", b"meet,date\nA,2025-01-01\n")


# ---------------------------------------------------------------- the file itself

@pytest.mark.parametrize("data,why", [
    (b"MZ\x90\x00\x03", "Windows program"), (b"\x7fELF\x02\x01", "program"),
    (b"PK\x03\x04rest", "zip"), (b"%PDF-1.7", "PDF"), (b"#!/bin/sh\nrm -rf /", "script"),
    (b"Event 1 Boys\x00\x00", "not a text file")])
def test_programs_archives_and_binaries_are_refused_unread(data, why):
    with pytest.raises(RP.ParseError) as e:
        RP.parse("results.txt", data)
    assert why in str(e.value)


def test_only_results_file_types():
    for name in ("r.exe", "r.js", "r.xlsx", "r.php", "r", "r.txt.exe"):
        with pytest.raises(RP.ParseError):
            RP.parse(name, b"Event 1 Boys 5000 Meter Run\n")


def test_text_that_is_not_hytek_is_refused():
    with pytest.raises(RP.ParseError):
        RP.parse("notes.txt", b"Dear coach,\nthe meet went well.\n")


def test_cp1252_files_read():
    raw = ("Hy-Tek's MEET MANAGER\nCaf\xe9 Invite - 9/6/2025\nResults\n"
           "Event 1  Girls 5000 Meter Run CC\n"
           "    Name                    Year School                  Finals  Points\n"
           "  1 Mu\xf1oz, Ana              11 Sunset                 19:01.00    1\n").encode("cp1252")
    out = RP.parse("r.txt", raw)
    assert out["meet"]["name"] == "Café Invite" and out["rows"][0]["name"] == "Ana Muñoz"


def test_impossible_times_are_flagged_not_dropped():
    raw = (b"Hy-Tek's MEET MANAGER\nFast Meet - 9/6/2025\nResults\n"
           b"Event 1  Boys 5000 Meter Run CC\n"
           b"    Name                    Year School                  Finals  Points\n"
           b"  1 Fast, Way               11 Sunset                  9:01.00    1\n"
           b"  2 Normal, Kid             11 Sunset                 16:01.00    2\n"
           b"  3 Normal, Kid             11 Sunset                 16:02.00    3\n")
    out = RP.parse("r.txt", raw)
    assert len(out["rows"]) == 3
    assert "world-record" in out["rows"][0]["warning"]
    assert "warning" not in out["rows"][1] and "twice" in out["rows"][2]["warning"]


# ---------------------------------------------------------------- the small readers

def test_times_dates_distances():
    assert RP.parseTime("15:12.40") == 912.4 and RP.parseTime("1:02:03.4") == 3723.4
    assert RP.parseTime("58.31q") == 58.31 and RP.parseTime("x4:21.5") == 261.5
    assert RP.parseTime("DNF") is None and RP.parseTime("") is None and RP.parseTime("12:3a:00") is None
    assert RP.statusOf("dnf") == "DNF" and RP.statusOf("DSQ") == "DQ" and RP.statusOf("NT") is None
    assert RP.parseDate("10/4/2025") == "2025-10-04" and RP.parseDate("2025-10-04") == "2025-10-04"
    assert RP.parseDate("October 4, 2025") == "2025-10-04" and RP.parseDate("13/45/2025") is None
    assert RP.parseDate("10/4/25") == "2025-10-04"
    assert RP.distanceOf("Boys 5000 Meter Run CC") == 5000.0 and RP.distanceOf("Girls 5K") == 5000.0
    assert abs(RP.distanceOf("1 Mile Run") - 1609.3) < 0.1 and RP.distanceOf("Two Mile") == 3218.7
    assert RP.distanceOf("Boys 3200") == 3200.0 and RP.distanceOf("Varsity Girls") is None
    assert RP.genderOf("Women 6000") == "F" and RP.genderOf("Men's 8K") == "M" and RP.genderOf("Open") is None
    assert RP.normName("CASTELLANO, OWEN") == "Owen Castellano" and RP.normName("Ann Doe*") == "Ann Doe"


def test_the_reader_runs_nothing():
    src = io.open(os.path.join(ROOT, "racecast", "results_parse.py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r"\bre\.compile\(", "", code)
    for banned in ("eval(", "exec(", "compile(", "__import__", "subprocess", "os.system", "pickle",
                   "urlopen", "requests.", "yaml.load"):
        assert banned not in code, banned
    assert re.search(r"convert_charrefs=True", src)
