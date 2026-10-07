# Project: xc-predictor / tests
# File:    test_race_story.py
# Purpose: the race page's two sentences (racecast/race_story.py) say only
#          what the rows say. Owner, 2026-10-07: "formalize summary so I can
#          see it working".
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import race_story as rs                                         # noqa: E402


def _row(name, school, secs, place=None, **kw):
    return {"name": name, "school": school, "time_seconds": secs,
            "display_time": rs._clock(secs), "place": place, **kw}


# the real 2024 CIF State D3 boys top three and team scores
FIELD = [_row("Evan Noonan", "Dana Hills", 883.7),
         _row("Liam Miller", "South Torrance", 891.9),
         _row("Miles Cook", "Sacred Heart Cathedral", 894.5)]
TEAMS = [{"school": "Campolindo", "points": 153, "place": 1,
          "runners": [_row("Clark Gregory", "Campolindo", 925.7, place=12)]},
         {"school": "Oak Park", "points": 182, "place": 2,
          "runners": [_row("Grant Jones", "Oak Park", 948.3, place=32)]}]


def test_the_cif_d3_story_reads_off_the_rows():
    s = [str(x) for x in rs.raceStory(FIELD, TEAMS, titles=2)]
    assert s[0] == ("<b>Evan Noonan</b> (Dana Hills) won a third straight title here "
                    "in <b>14:43.7</b>, 8.2 seconds clear of Liam Miller (South Torrance).")
    assert s[1] == ("<b>Campolindo</b> took the team title with 153 points, 29 ahead "
                    "of Oak Park, led by Clark Gregory in 12th.")


def test_no_judgement_words():
    # ! the mockup's "won on depth" was false that day (Oak Park's and Hart's
    #   1-5 splits were tighter): the rules state margins, never verdicts
    text = " ".join(str(x) for x in rs.raceStory(FIELD, TEAMS, titles=0)).lower()
    for w in ("depth", "dominant", "upset", "easily", "narrowly"):
        assert w not in text


def test_no_title_streak_is_plain_won():
    s = str(rs.winnerSentence(FIELD, titles=0))
    assert " won in <b>14:43.7</b>" in s and "straight" not in s


def test_a_streak_past_ten_uses_the_ordinal():
    assert "won a 12th straight title here" in str(rs.winnerSentence(FIELD, titles=11))


def test_a_dead_heat_is_not_zero_seconds_clear():
    tie = [_row("A Runner", "X", 900.0), _row("B Runner", "Y", 900.0)]
    s = str(rs.winnerSentence(tie))
    assert "0 seconds" not in s and "on the same time" in s


def test_one_finisher_and_sentinels():
    s = str(rs.winnerSentence([_row("Solo", "X", 1000.0), _row("DNF", "Y", 999999.0)]))
    assert s == "<b>Solo</b> (X) won in <b>16:40</b>."
    assert rs.winnerSentence([_row("DNF", "Y", 999999.0)]) is None


def test_one_second_is_singular():
    s = str(rs.winnerSentence([_row("A", "X", 900.0), _row("B", "Y", 901.0)]))
    assert "1 second clear" in s


def test_a_points_tie_names_the_tiebreak():
    teams = [{"school": "A", "points": 64, "place": 1, "runners": []},
             {"school": "B", "points": 64, "place": 2, "runners": []}]
    assert str(rs.teamSentence(teams)) == ("<b>A</b> took the team title with 64 points, "
                                           "on the sixth-runner tiebreak over B.")


def test_no_scored_teams_no_team_sentence():
    assert rs.teamSentence([]) is None
    assert rs.teamSentence([{"school": "A", "points": None}]) is None
    assert len(rs.raceStory(FIELD, [], 0)) == 1


def test_names_are_escaped():
    s = str(rs.winnerSentence([_row("<script>", "A&B", 900.0)]))
    assert "<script>" not in s and "&lt;script&gt;" in s and "A&amp;B" in s


def test_ordinals():
    assert [rs.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "112th"]


def test_division_key_drops_the_number_not_the_level():
    assert rs.divisionKey("Division 3 Boys") == rs.divisionKey("Division II Boys") == "boys"
    assert rs.divisionKey("Frosh/Soph Boys") == "frosh soph boys"
    assert rs.divisionKey("Varsity Boys") != rs.divisionKey("JV Boys")
    assert rs.divisionKey(None) == ""


# ------------------------------------------------------------ priorTitles
class _Cur:
    """Answers the three queries priorTitles makes: the timeout, the
    winner's earlier rows, and each race's fastest time."""
    def __init__(self, rows, best, fail=False):
        self.rows, self.best, self.fail, self.sql = rows, best, fail, []
        self._next = None

    def execute(self, sql, params=None):
        self.sql.append(sql.strip().split()[0] + " " + sql.strip().split()[1])
        if self.fail and "FROM   results r" in sql:
            raise RuntimeError("statement timeout")
        if sql.startswith("SHOW"):
            self._next = {"statement_timeout": "60s"}
        elif "min(time_seconds)" in sql:
            self._next = {"t": self.best[(params[0], params[1])]}
        elif "FROM   results r" in sql:
            self._next = list(self.rows)

    def fetchone(self):
        return self._next

    def fetchall(self):
        return self._next


def _past(yr, meet, div, t, mid):
    return {"meet_id": mid, "div_id": 1, "source": "anet", "yr": yr, "time_seconds": t,
            "meet_name": meet, "division": div}


def test_prior_titles_counts_the_unbroken_chain():
    rows = [_past(2023, "2023 CIF State Cross Country Championships", "Division 2 Boys", 875.3, 1),
            _past(2022, "2022 CIF State Cross Country Championships", "Division 2 Boys", 904.3, 2),
            _past(2021, "2021 CIF State Cross Country Championships", "Division 2 Boys", 930.0, 3)]
    best = {(1, 1): 875.3, (2, 1): 904.3, (3, 1): 925.0}       # 2021: beaten
    cur = _Cur(rows, best)
    n = rs.priorTitles(cur, 29465443, "2024-11-30",
                       "2024 CIF State Cross Country Championships", "Division 3 Boys")
    assert n == 2
    # the caller's timeout is put back before the savepoint is released
    assert "SET LOCAL" in " ".join(cur.sql[-2:]) and cur.sql[-1].startswith("RELEASE")


def test_a_gap_year_breaks_the_chain():
    rows = [_past(2022, "CIF State XC", "Boys", 900.0, 2)]
    assert rs.priorTitles(_Cur(rows, {(2, 1): 900.0}), 1, "2024-11-30", "CIF State XC", "Boys") == 0


def test_another_meet_or_level_does_not_count():
    rows = [_past(2023, "Clovis Invitational", "Boys", 900.0, 1),
            _past(2023, "2023 CIF State XC", "Frosh/Soph Boys", 900.0, 2)]
    cur = _Cur(rows, {(1, 1): 900.0, (2, 1): 900.0})
    assert rs.priorTitles(cur, 1, "2024-11-30", "2024 CIF State XC", "Varsity Boys") == 0


def test_a_failed_query_is_zero_and_rolls_back():
    cur = _Cur([], {}, fail=True)
    assert rs.priorTitles(cur, 1, "2024-11-30", "CIF State XC", "Boys") == 0
    assert cur.sql[-1].startswith("ROLLBACK TO")


def test_no_person_no_query():
    cur = _Cur([], {})
    assert rs.priorTitles(cur, None, "2024-11-30", "CIF State XC", "Boys") == 0
    assert cur.sql == []


# ------------------------------------------------------------ track events
def _tf(name, school, secs=None, mark=None, pr=False, relay=False):
    return {"athlete_name": name, "school": school, "time_seconds": secs, "mark": mark,
            "display_result": mark if mark else rs._clock(secs), "is_pr": pr, "is_relay": relay}


def test_tf_one_field_wins_with_hundredths():
    secs = [{"label": "", "rows": [_tf("Tyler Renteria", "El Toro", 273.07, pr=True),
                                   _tf("Evan Noonan", "Dana Hills", 273.12, pr=True),
                                   _tf("Mike Ayala", "Dana Hills", 286.04)]}]
    w, s = rs.tfStory(secs, False)
    assert w["athlete_name"] == "Tyler Renteria"
    assert str(s[0]) == ("<b>Tyler Renteria</b> (El Toro) won in <b>4:33.07</b>, "
                         "0.05 seconds clear of Evan Noonan (Dana Hills).")
    assert str(s[1]) == "2 of 3 set personal records."


def test_tf_the_final_decides_not_the_prelims():
    final = [_tf("A", "X", 128.0), _tf("B", "Y", 128.1)]
    prelim = [_tf("A", "X", 127.5), _tf("C", "Z", 127.9)]       # faster prelim, not a win
    w, s = rs.tfStory([{"label": "Finals", "rows": final}, {"label": "Prelims", "rows": prelim}], False)
    assert "won in <b>2:08</b>, 0.1 seconds clear of B (Y)" in str(s[0])


def test_tf_heats_without_a_final_are_not_a_win():
    s = rs.tfStory([{"label": "Heat 1", "rows": [_tf("A", "X", 600.0)]},
                    {"label": "Heat 2", "rows": [_tf("B", "Y", 590.0)]}], False)[1]
    assert str(s[0]).startswith("<b>B</b> (Y) ran the fastest time across 2 heats, <b>9:50</b>")
    assert " won " not in str(s[0])


def test_tf_field_marks_are_not_subtracted():
    secs = [{"label": "", "rows": [_tf("A", "X", mark="20-6"), _tf("B", "Y", mark="6.20m")]}]
    s = str(rs.tfStory(secs, True)[1][0])
    assert "won with" in s and "was next with" in s and "clear" not in s


def test_tf_relay_is_the_team():
    secs = [{"label": "", "rows": [_tf(None, "Dana Hills", 200.5, relay=True),
                                   _tf(None, "El Toro", 201.0, relay=True)]}]
    assert str(rs.tfStory(secs, False)[1][0]) == ("<b>Dana Hills</b> won in <b>3:20.5</b>, "
                                                  "0.5 seconds clear of El Toro.")


def test_tf_no_marks_no_story():
    w, s = rs.tfStory([{"label": "", "rows": [_tf("A", "X", 999999.0)]}], False)
    assert w is None and s == []
