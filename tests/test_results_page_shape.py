"""The prediction result IS a results page.

    python -m pytest -q tests/test_results_page_shape.py

Owner, 2026-09-16: "the actual ui kind of sucks donkey dick. It should read
exactly like a results page! Look at results page html and such if you need."

★ WHAT IT WAS: one table, with every scorer crammed into a single "Scorers"
  cell as a run-on line of "3. Name 15:42.1". Nobody reads a race that way.

★ WHAT A RACE PAGE IS: two tables. Team scores -- Place, Team, Points, then
  one column per scoring position 1..7 with 6 and 7 greyed -- and then every
  finisher, one per row: Place, Athlete, Grade, School, Time, Rating, Points.
  People already know how to read those, because they read them for every
  race on the site.

⚠ THE SECOND TABLE NEEDS THE WHOLE FIELD, and the API only ever returned
  each team's own seven. An unattached runner was in nobody's seven and so
  could not be shown at all. _score now hands back the finish order it
  already computes -- ONE numbering, because deriving `place` twice is how
  the page came to show 82 teams while the model scored 400.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import predict                                                  # noqa: E402

JS = io.open(os.path.join(ROOT, "racecast", "static", "predictions.js"),
             encoding="utf-8").read()
RACE_HTML = io.open(os.path.join(ROOT, "racecast", "templates", "race.html"),
                    encoding="utf-8").read()


def _row(pid, name, school, secs, **kw):
    r = {"person_id": pid, "name": name, "school": school, "seconds": secs}
    r.update(kw)
    return r


def _race():
    """Two full teams, one six-runner team, and an unattached runner who is
    predicted to finish third."""
    field, preds = [], []
    t = 900.0
    plan = ([("Alpha", i) for i in range(7)] + [("Beta", i) for i in range(7)]
            + [("Gamma", i) for i in range(6)])
    pid = 100
    for school, _i in plan:
        pid += 1
        field.append(_row(pid, f"R{pid}", school, None, school_state="MI",
                          grade="12", pool="hs_m", rating=120.0 - pid % 9,
                          entered=7 if school != "Gamma" else 6))
        preds.append({"seconds": t, "lo": t - 20, "hi": t + 20})
        t += 4.0
    # unattached, third fastest in the race
    field.append(_row(9001, "Solo Runner", "Unattached", None, grade="11",
                      pool="hs_m", rating=140.0))
    preds.append({"seconds": 905.0, "lo": 890.0, "hi": 920.0})
    return field, preds


def test_score_hands_back_the_whole_finish_order():
    field, preds = _race()
    teams, finishers = predict._score(field, preds)

    assert len(finishers) == len(field), (len(finishers), len(field))
    # one continuous numbering, in predicted order
    assert [f["place"] for f in finishers] == list(range(1, len(field) + 1))
    assert [f["seconds"] for f in finishers] == sorted(
        p["seconds"] for p in preds)
    # ⚠ THE UNATTACHED RUNNER IS IN IT. That is the whole reason the flat
    #   list exists: they are in nobody's seven, so rebuilding the order out
    #   of the teams could never show them.
    solo = [f for f in finishers if f["person_id"] == 9001]
    assert len(solo) == 1, finishers
    assert solo[0]["place"] == 3, solo[0]
    # ...and they take no scoring place, so they displace nobody
    assert solo[0]["score_place"] is None, solo[0]
    assert solo[0]["school"] is None, solo[0]
    assert teams, teams
    print("  the finish order comes back whole ................. OK")


def test_one_numbering_serves_both_tables():
    """A team's shown scoring places have to add up to its own score. They
    only do if the two tables are numbered by the same pass."""
    field, preds = _race()
    teams, finishers = predict._score(field, preds)
    by_id = {f["person_id"]: f for f in finishers}
    for t in teams:
        for r in t["runners"]:
            f = by_id[r["person_id"]]
            assert f["place"] == r["place"], (f, r)
            assert f["score_place"] == r.get("score_place"), (f, r)
        if t["score"] is not None:
            scorers = [r for r in t["runners"] if r.get("score_place")][:5]
            assert sum(r["score_place"] for r in scorers) == t["score"], t
    print("  one numbering serves both tables .................. OK")


def test_the_row_carries_what_a_results_row_shows():
    """Place, Athlete, Grade, School, Time, Rating, Points -- so every column
    race.html has is on the row rather than looked up again in the browser."""
    field, preds = _race()
    _teams, finishers = predict._score(field, preds)
    f = finishers[0]
    for key in ("place", "name", "person_id", "grade", "school",
                "school_state", "rating", "seconds", "lo", "hi",
                "score_place"):
        assert key in f, (key, f)
    print("  the row carries a results row's columns ........... OK")


def _renderBody():
    i = JS.index("function teamScoreTable(")
    j = JS.index("function renderTeam(")
    return JS[i:JS.index("\n}", j)]


def test_the_markup_is_the_race_pages_markup():
    """! NOT A LOOKALIKE. race.html uses plain <table> with these columns, so
    the global rules in style.css style both identically and they cannot
    drift. A second set of rules that merely LOOKED like it is what drifts.
    """
    body = "\n".join(l.split("//")[0] for l in _renderBody().splitlines())

    for col in ("Place", "Team", "Points", "Athlete", "Grade", "School",
                "Time", "Rating"):
        assert f"<th>{col}</th>" in body, col
    # the same greying race.html puts on 6 and 7, from the same class
    assert 'class="displacer"' in body, body
    assert 'class="displacer"' in RACE_HTML, "race.html stopped using it"
    # ...on positions 6 and 7 only
    assert "k >= 5" in body, body
    # score_place, never the raw finishing place, in the scorer columns
    assert "score_place" in body, body
    print("  the markup is race.html's markup .................. OK")


def test_an_incomplete_team_is_a_sentence_not_a_row():
    """⚠ A team that cannot score has no scoring places, so its seven cells
    fell back to FINISHING places -- a lone qualifier read "4 13 18 21"
    across the scorer columns, which is what a team that took those places
    looks like. race.html names them underneath instead, in words.
    """
    body = _renderBody()
    assert "t.score !== null" in body, body
    assert "Not scored:" in body, body
    # and the all-incomplete case has race.html's own wording.
    # ! WHITESPACE-NORMALISED, because both of these are wrapped source and
    #   the sentence is split across lines in each.
    flat = " ".join(body.split())
    assert "nothing can be scored" in flat, flat
    assert "nothing can be scored" in " ".join(RACE_HTML.split()), \
        "race.html reworded it"
    print("  an incomplete team is a sentence, not a row ....... OK")


def test_the_band_survives_the_redesign():
    """★ A single race carries about +-4 rating points, so a time quoted to a
    tenth with no range claims a precision the model does not have. It moved
    under the time rather than into a column race.html does not have."""
    body = _renderBody()
    assert "pred-band" in body, body
    css = io.open(os.path.join(ROOT, "racecast", "static", "style.css"),
                  encoding="utf-8").read()
    assert ".pred-band" in css, "the band has no style"
    print("  the band survived the redesign .................... OK")


if __name__ == "__main__":
    for fn in [test_score_hands_back_the_whole_finish_order,
               test_one_numbering_serves_both_tables,
               test_the_row_carries_what_a_results_row_shows,
               test_the_markup_is_the_race_pages_markup,
               test_an_incomplete_team_is_a_sentence_not_a_row,
               test_the_band_survives_the_redesign]:
        fn()
