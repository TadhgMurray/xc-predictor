"""The athlete header, redesign B (owner, 2026-10-10).

Pinned here:
  * WHAT IT TAKES in plain English, above and below the mark, with the
    seconds computed through the conversions maths (stubbed here), never
    typed in (cuts.plainWords / cuts.stampClocks, _wit_line.html);
  * the course word's cut-offs are the quartiles and top tenth of a
    distribution of course_difficulties cells, per sport, with the
    no-table fallback (difficulty_view.cutoffsFrom / courseWord);
  * the Records column keeps PR, SR and CR only, and the legend under each
    season table is gone.
No database: stub cursors and a stub seconds function."""
import html
import math
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import pytest  # noqa: E402

import cuts as C  # noqa: E402
import difficulty_view as DV  # noqa: E402


# ------------------------------------------------------------------ #
#  what it takes, in plain English
# ------------------------------------------------------------------ #

# ! A STUB OF conversions.ratingSeconds at a track 5K: time inverse to the
#   rating, scaled so 117.8 is 15:40 (940 s). The test then asserts the
#   seconds the sentence prints are THIS function's difference -- the line
#   has no seconds-per-point of its own.
K = 940.0 * 117.8


def _secs(r, pool):
    return K / float(r) if r else None


def _line(rating, mark, kind):
    rung = {"key": "podium", "mark": mark, "year": 2025, "where": "state"}
    line = {"label": "OR Class 6A", "rating": rating, "mark": mark,
            "gap": round(abs(mark - rating), 1), "kind": kind, "pool": "hs_m",
            "rung": rung, "href": "/what-it-takes/or/6a", "text": "x",
            "conv_href": "/conversions?athlete=7", "times": []}
    line.update(C.plainWords(rung, line["label"], 2026))
    return C.stampClocks(line, seconds=_secs)


def _render(line, name="Owen Castellano"):
    pytest.importorskip("flask")
    import app as A
    with A.app.test_request_context("/athlete/7"):
        out = A.app.jinja_env.get_template("_wit_line.html").render(
            wit_line=line, athlete={"name": name, "person_id": 7, "rating": line["rating"]})
    return out


def _text(markup):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", markup))).strip()


def test_plain_words_name_the_rung():
    w = C.plainWords({"key": "podium", "year": 2025}, "OR Class 6A", 2026)
    assert w == {"goal": "for the OR Class 6A state podium",
                 "goal_mark": "the OR Class 6A state podium mark",
                 "who": "last year's top 8"}
    w = C.plainWords({"key": "qualify", "year": 2024}, "CA Division 2", 2026)
    assert w["goal"] == "to make the CA Division 2 state meet"
    assert w["who"] == "the 2024 state qualifiers"
    w = C.plainWords({"key": "advance", "year": 2025, "where_label": "NCS D2"}, "CA Division 2", 2026)
    assert w["goal"] == "to advance from NCS D2" and w["goal_mark"] == "the NCS D2 advancing mark"
    assert C.plainWords({"what": "old"}, "X", 2026) == {}      # unkeyed: old words


def test_line_for_carries_the_plain_words():
    import test_what_it_takes as W
    races, rows, ratings = W._small_state()
    data = C.computeMarks("CA", races, rows, ratings)
    q = C.findDivision(data, "boys", "2")["latest"]["lines"]["qualify"]["mark"]
    ln = C.lineFor(W._season(q - 1.0, section=None), data, 2026)
    assert ln["goal_mark"] == "the CA Division 2 state qualifying mark"
    assert ln["who"] == "last year's state qualifiers"
    ln = C.lineFor(W._season(260.0), data, 2026)
    assert ln["kind"] == "above" and ln["goal"] == "for the CA Division 2 state podium"


def test_clocks_come_from_the_conversion():
    ln = _line(124.6, 117.8, "above")
    assert ln["mark_time"] == "15:40"
    assert ln["gap_secs"] == int(round(abs(_secs(124.6, "hs_m") - _secs(117.8, "hs_m"))))
    # a conversion that cannot be made leaves the brackets out, not a guess
    bad = C.stampClocks(dict(ln, mark_time=None, gap_secs=None), seconds=lambda r, p: None)
    assert bad["mark_time"] is None and bad["gap_secs"] is None
    boom = C.stampClocks(dict(ln), seconds=lambda r, p: 1 / 0)
    assert boom["mark_time"] is None and boom["gap_secs"] is None


def test_above_the_mark_wording():
    out = _render(_line(124.6, 117.8, "above"))
    assert 'class="wit-ok">Fast enough for the OR Class 6A state podium</span>' in out
    assert _text(out).startswith(
        "What it takes Fast enough for the OR Class 6A state podium — last year's top 8 "
        "needed 117.8 (about a 15:40 5K on a track); Owen is at 124.6.")
    assert 'href="/what-it-takes/or/6a">Past marks</a>' in out
    assert 'href="/goal?athlete=7">Any goal, any course</a>' in out
    assert "wit-gap" not in out


def test_below_the_mark_wording():
    line = _line(124.6, 126.7, "off")
    secs = int(round(_secs(124.6, "hs_m") - _secs(126.7, "hs_m")))
    t = _text(_render(line))
    assert t.startswith(
        f"What it takes About 2.1 points (≈ {secs} s over 5K) short of the OR Class 6A "
        f"state podium mark: last year's top 8 needed 126.7 (about a {line['mark_time']} "
        "5K on a track); Owen is at 124.6.")
    assert 'class="wit-gap">About 2.1 points' in _render(line)
    assert "Past marks" in t and "Any goal, any course" in t


def test_an_unkeyed_line_keeps_the_old_words():
    line = {"kind": "off", "gap": 1.0, "gap_pct": 0.8, "text": "last year's mark",
            "label": "OR Class 6A", "mark": 120.0, "rating": 119.0,
            "href": "/what-it-takes/or/6a", "times": []}
    t = _text(_render(line))
    assert "points (0.8%) off last year's mark" in t and "Fast enough" not in t


# ------------------------------------------------------------------ #
#  the course word: cut-offs from the distribution
# ------------------------------------------------------------------ #

def _stub_rows():
    # 101 XC cells, +5.0% .. +15.0% in 0.1-point steps, and 101 track cells
    # -1.0% .. +1.0%: on log(1+d) the quartiles land on cells 25 and 75 and
    # the top tenth on cell 90, so every cut is a known cell
    xc = [("XC", math.log1p(0.05 + i * 0.001)) for i in range(101)]
    tf = [("TF", math.log1p(-0.01 + i * 0.0002)) for i in range(101)]
    return xc + tf


def test_cutoffs_are_the_quartiles_and_top_tenth():
    cuts = DV.cutoffsFrom(_stub_rows())
    xc = cuts["XC"]
    assert xc["n"] == 101
    assert math.expm1(xc["q25"]) == pytest.approx(0.075)
    assert math.expm1(xc["median"]) == pytest.approx(0.100)
    assert math.expm1(xc["q75"]) == pytest.approx(0.125)
    assert math.expm1(xc["q90"]) == pytest.approx(0.140)
    assert math.expm1(cuts["TF"]["median"]) == pytest.approx(0.0, abs=1e-12)
    assert DV.COURSE_WORD_MIN_N == 10              # 1 / (1 - Q90), derived


def test_course_words_fall_where_the_distribution_says():
    cuts = DV.cutoffsFrom(_stub_rows())
    w = lambda d, s="XC": DV.courseWord(d, s, cuts)[0]   # noqa: E731
    # (half a cell off each cut, so float rounding cannot pick the side)
    assert w(0.060) == "Fast" and w(0.0745) == "Fast"
    assert w(0.0755) == "Typical" and w(0.100) == "Typical" and w(0.1245) == "Typical"
    assert w(0.1255) == "Hard" and w(0.1395) == "Hard"
    assert w(0.1405) == "Very hard"
    # the same +0.9% is "Very hard" for a track and "Fast" for cross country:
    # each sport against its own courses
    assert w(0.009, "TF") == "Very hard" and w(0.009) == "Fast"
    assert w(0.0, "TF") == "Typical"
    # the % is against the sport's typical (median) course, not the track zero
    assert DV.courseWord(0.100, "XC", cuts)[1] == "0.0%"
    assert DV.courseWord(1.10 * 1.034 - 1, "XC", cuts)[1] == "+3.4%"


def test_no_distribution_no_word():
    assert DV.courseWord(0.07, "XC", None) == (None, None)
    assert DV.courseWord(None, "XC", DV.cutoffsFrom(_stub_rows())) == (None, None)
    thin = DV.cutoffsFrom([("XC", 0.07)] * (DV.COURSE_WORD_MIN_N - 1))
    assert thin == {}
    assert DV.courseWord(0.07, "XC", thin) == (None, None)


def test_cutoffs_are_read_once_and_survive_a_failed_read(monkeypatch):
    monkeypatch.setitem(DV._cuts_state, "cuts", None)
    monkeypatch.setitem(DV._cuts_state, "at", 0.0)

    class Cur:
        n = 0

        def execute(self, sql):
            Cur.n += 1
            assert "course_difficulties" in sql and "'TF:%%'" in sql

        def fetchall(self):
            return _stub_rows()

    got = DV.courseCutoffs(Cur())
    assert got["XC"]["n"] == 101 and Cur.n == 1
    assert DV.courseCutoffs(Cur()) is got and Cur.n == 1       # cached

    class Broken:
        class connection:
            @staticmethod
            def rollback():
                pass

        def execute(self, sql):
            raise RuntimeError("table is being rebuilt")

    monkeypatch.setitem(DV._cuts_state, "at", 0.0)              # stale
    assert DV.courseCutoffs(Broken()) is got                   # last good kept
    assert DV.courseCutoffs() is got                           # no cursor: cache


# ------------------------------------------------------------------ #
#  the results table: Course and Records
# ------------------------------------------------------------------ #

def _season_block(race):
    pytest.importorskip("flask")
    import jinja2
    import app as A
    env = A.app.jinja_env.overlay(undefined=jinja2.ChainableUndefined)
    with A.app.test_request_context("/athlete/7"):
        m = env.get_template("athlete.html").make_module(
            vars={"athlete": {"name": "Owen X", "person_id": 7,
                              "rating": 124.6, "rating_hs": 124.6}})
        return str(m.season_block(2025, "XC", {"races": [race], "rating": 124.8,
                                                "rating_hs": 124.8, "school": "Jesuit",
                                                "pool": "hs_m"}))


def _race(**kw):
    r = dict(result_id=5, date="2025-11-01", meet="State", sport="XC", event="5000m",
             result="14:38.50", time_raw=878.5, place=2, speed_rating=125.8,
             hs_rating=125.8, difficulty=0.134, day_effect=None)
    r.update(kw)
    return r


def test_records_are_pr_sr_cr_only_and_no_legend():
    out = _season_block(_race(is_pr=True, is_sr=True, is_course_pr=True,
                              is_rating_pr=True, is_rating_sr=True, is_course_sr=True,
                              course_word="Hard", course_pct="+3.4%"))
    assert "<th>Records</th>" in out and "<th>Course</th>" in out
    for code in ("PR", "SR", "CR"):
        assert f'<span aria-hidden="true">{code}</span>' in out
    for css in ("flag--rpr", "flag--rsr", "flag--csr"):
        assert css not in out
    assert "flag-legend" not in out and "Difficulty" not in out


def test_course_cell_is_a_word_with_its_percent_in_the_tooltip():
    out = _season_block(_race(course_word="Hard", course_pct="+3.4%"))
    assert 'data-title="Hard course · +3.4%"' in out
    assert ('data-blurb="Times here run +3.4% against a typical cross country course. '
            'The rating already adjusts for it."') in out
    assert '<span aria-hidden="true">Hard</span>' in out
    # no cut-offs read: the old percentage, never a word
    out = _season_block(_race(course_word=None, course_pct=None))
    assert "flag cw" not in out and 'class="dv"' in out
