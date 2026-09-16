"""The model scores the lineup the page is showing.

    python -m pytest -q tests/test_field_from_page.py

Owner, 2026-09-16: "you can literally see the correct list there. Why not
just take those athletes?"

⚠ BECAUSE THE PAGE SENT ONLY ITS EDITS, and the server derived the field
  again from scratch. Two derivations of one question, and when they
  disagreed the model scored a race nobody asked for: at the D3
  championships the page showed 82 teams and 294 runners while the
  prediction scored 400+ teams, middle schoolers included, with a team's
  predicted scorers being seven people who were not on its card.

★ THE CARDS ARE THE TRUTH. They are what a person read, edited and pressed
  Predict on. Anything the server re-derives is a second opinion about a
  question the reader has already answered.

! AND IT ONLY FITS BECAUSE OF THE 414 FIX. Sending the roster was rejected
  as "a large request" while every prediction had to be a URL; sendQuery
  POSTs past 1,800 bytes and a 294-runner field is about 2.3 KB.
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def target(**args):
    """_target, lifted out of app.py so this needs no database."""
    src = read("racecast", "app.py")
    i = src.index("MAX_FIELD_TEAMS = 400")
    j = src.index('@app.route("/api/predict/field")')
    ns = {}
    exec(compile(src[i:j], "app-slice", "exec"), ns)

    class Args(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    base = {"mode": "rerun", "meet_id": "271911", "sport": "XC",
            "date": "2026-11-21"}
    return ns["_target"](Args({**base, **args}))


def test_the_field_arrives_as_school_and_ids():
    t, err = target(field=json.dumps([["Amherst (MA)", ["11", "12"]],
                                      ["Chicago (IL)", ["21"]]]))
    assert err is None, err
    assert t["field"] == [("Amherst (MA)", [11, 12]), ("Chicago (IL)", [21])]


def test_a_malformed_field_is_refused_not_ignored():
    """! NEVER A SILENT FALLBACK. Quietly dropping a bad field would go back
    to deriving one -- scoring a different lineup than the one asked for,
    which is the bug being fixed."""
    for bad in ("not json", '{"a": 1}', '[["Amherst", "11"]]',
                '[["Amherst", ["1; DROP TABLE"]]]', '[[5, [1]]]',
                json.dumps([["S", [1]]] * 401)):
        t, err = target(field=bad)
        assert err is not None and t is None, bad[:40]


def test_no_field_still_derives_one():
    """A shared link, a curl, an older cached page: all still work."""
    t, err = target()
    assert err is None and "field" not in t


def test_the_server_prefers_it_over_deriving():
    pred = read("racecast", "predict.py")
    i = pred.index("def _teamRosters")
    body = re.sub(r"#.*", "", pred[i:pred.index("\ndef ", i + 10)])
    assert 'explicit = target.get("field")' in body
    # ! THE SCHOOL COMES FROM THE PAGE TOO. Re-resolving it could put a
    #   runner on a different team and re-open the divergence one level down.
    assert '"school": school' in body, body[:600]
    # and it returns before the derivation below can run
    assert body.index("return entries") < body.index("_exactField"), \
        "the derived field must not run when the page sent one"


def test_the_page_sends_what_it_shows_and_not_what_it_removed():
    js = re.sub(r"/\*.*?\*/", "", read("racecast", "static", "predictions.js"),
                flags=re.S)
    i = js.index("function buildQuery")
    body = js[i:js.index("\nfunction ", i + 10)]
    assert 'q.set("field", JSON.stringify(shown))' in body
    # ⚠ removing a runner deletes its row from the DOM but leaves it in
    #   t.runners; the removal lives in e.removed, so reading t.runners raw
    #   would send back the very people the reader took out
    assert "!e.removed.has(id)" in body, body[-700:]
