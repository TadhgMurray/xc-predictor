"""
Owner, 2026-09-16: "if a team does not have 5-7 ppl in the race at the start,
it should not get ranked/placed as a team. For example, if there's an indiv who
qualifies and runs, and then we add entire roster, that team should not get a
place or displace anybody else... gotta make it respect the top 7 who actually
ran thing."

Two rules, and they are both about the START LINE rather than the list on the
screen:

  1. A school is a team where it ENTERED five. One qualifier plus six what-ifs
     is one entry, so it cannot score and cannot displace.
  2. A team takes at most as many scoring places as it entered, never more than
     seven. Adding four to a six-runner squad does not buy it ten displacers.
"""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import predict                                                  # noqa: E402


def _row(pid, school, entered=None):
    r = {"person_id": pid, "school": school, "name": f"R{pid}"}
    if entered is not None:
        r["entered"] = entered
    return r


def _run(field):
    preds = [{"seconds": 900 + i} for i in range(len(field))]
    return {t["team"]: t for t in predict._score(field, preds)[0]}


def test_a_lone_qualifier_plus_a_whole_squad_is_still_a_lone_qualifier():
    """The exact case the owner hit: Solo entered ONE runner and a person
    pressed "add whole squad" to see the other six. Seven runners are on the
    screen; one of them entered."""
    field = ([_row(1, "Solo", 1)]                      # the qualifier
             + [_row(100 + i, "Solo") for i in range(6)]   # the what-ifs
             + [_row(200 + i, "Deep", 7) for i in range(7)])
    by = _run(field)

    assert by["Solo"]["score"] is None, by["Solo"]
    assert by["Solo"]["note"] == "only 1 entered", by["Solo"]["note"]
    # And not one of those seven took a scoring place.
    assert not any(r.get("score_place") for r in by["Solo"]["runners"]), \
        by["Solo"]["runners"]
    print("  a lone qualifier plus a squad is still one entry .. OK")


def test_the_added_runners_do_not_displace_a_real_team():
    """Solo's six what-ifs are predicted AHEAD of every real runner. If they
    took places, Deep's score would be inflated by all six."""
    field = ([_row(1, "Solo", 1)]
             + [_row(100 + i, "Solo") for i in range(6)]
             + [_row(200 + i, "Deep", 7) for i in range(7)])
    by = _run(field)

    # Solo's seven finish 1-7 on the road; Deep scores 1+2+3+4+5 anyway.
    assert by["Deep"]["score"] == 15, by["Deep"]["score"]
    assert [r["place"] for r in by["Deep"]["runners"]][0] == 8
    print("  added runners take no places off a real team ...... OK")


def test_an_eighth_runner_cannot_displace():
    """A team enters seven. Adding four to a six-runner entry must not hand it
    ten displacers -- the four extras have no scoring place at all."""
    field = ([_row(i, "Padded", 6) for i in range(1, 11)]   # 6 entered, 10 shown
             + [_row(100 + i, "Honest", 7) for i in range(7)])
    by = _run(field)

    scoring = [r for r in by["Padded"]["runners"] if r.get("score_place")]
    # runners is trimmed to 7 for display; the cap is what matters:
    assert len(scoring) <= predict.MAX_PER_TEAM, scoring
    # Padded entered six, so six places are taken before Honest's first.
    assert by["Honest"]["score"] == 7 + 8 + 9 + 10 + 11, by["Honest"]["score"]
    print("  an eighth runner cannot displace .................. OK")


def test_no_entry_count_falls_back_to_who_is_there():
    """A manual target has no meet to count, and a team ADDED to a field was
    genuinely put in the race. Neither carries `entered`, and both must score
    exactly as they did before this rule existed."""
    field = ([_row(i, "Alpha") for i in range(1, 8)]
             + [_row(100 + i, "Beta") for i in range(7)])
    by = _run(field)

    assert by["Alpha"]["score"] == 15, by["Alpha"]["score"]
    assert by["Beta"]["score"] == 8 + 9 + 10 + 11 + 12, by["Beta"]["score"]
    print("  no entry count still scores the runners present ... OK")


def test_a_genuinely_short_team_still_says_runners():
    """Four runners, four entered: "only 4 entered" would be a strange way to
    say it when four is also all there is to see."""
    field = ([_row(i, "Short", 4) for i in range(1, 5)]
             + [_row(100 + i, "Full", 7) for i in range(7)])
    by = _run(field)

    assert by["Short"]["note"] == "only 4 runners", by["Short"]["note"]
    print("  a genuinely short team reads as short ............. OK")


def test_the_note_names_the_limiting_number():
    """Entered six, three left on the card after removals: "only 6 runners"
    beside three runners reads as a bug. The note names whichever truth is
    actually short."""
    field = ([_row(i, "Thinned", 6) for i in range(1, 4)]
             + [_row(100 + i, "Full", 7) for i in range(7)])
    by = _run(field)
    assert by["Thinned"]["note"] == "only 3 runners", by["Thinned"]["note"]
    print("  the note names the limiting number ................ OK")


def test_the_page_sends_the_entry_count():
    """The rule is worthless if the count never leaves the browser: the field
    the page sends must be [school, ids, entered]."""
    js = open(os.path.join(_ROOT, "racecast", "static",
                           "predictions.js")).read()
    i = js.index("q.set(\"field\"")
    body = js[js.index("const shown = teams", i - 2000):i]
    body = "\n".join(l.split("//")[0] for l in body.splitlines())
    assert "t.entered" in body, body
    print("  the page sends the entry count .................... OK")


def test_the_server_parses_it():
    """And the parser has to accept the third element instead of 400ing."""
    app = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    i = app.index("field entries are [school, [person_id, ...]]")
    body = app[i - 500:i]
    assert "len(pair) not in (2, 3)" in body, body
    print("  the server accepts a three-element entry .......... OK")


if __name__ == "__main__":
    for fn in [test_a_lone_qualifier_plus_a_whole_squad_is_still_a_lone_qualifier,
               test_the_added_runners_do_not_displace_a_real_team,
               test_an_eighth_runner_cannot_displace,
               test_no_entry_count_falls_back_to_who_is_there,
               test_a_genuinely_short_team_still_says_runners,
               test_the_note_names_the_limiting_number,
               test_the_page_sends_the_entry_count,
               test_the_server_parses_it]:
        fn()
