"""The predictions page on the HS-equivalent scale (owner, 2026-09-15:
"the speed ratings for the ppl on the teams is not hs-equivalent when it
should be if the scale is hs-equivalent").

    python -m pytest -q tests/test_predict_scale_view.py

★ WHAT THIS GUARDS IS THE SPLIT, not the arithmetic. pool_view owns the
  conversion and is tested where it lives; the fault here was that half the
  site had a rating view and this page had never joined it, and the way it
  can break again is a new display site rendering a bare number, or -- far
  worse -- a converted number reaching a data-rating attribute, where the
  page reads it BACK and predicts on it.

⚠ SOURCE-PINNED, because there is no database in the test run and the
  rosters this page shows arrive by fetch. What can be checked without one
  is exactly what silently rots: the script order, the two endpoints that
  must stamp, and the attribute invariant.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def strip_comments(js):
    """Block and line comments out, so prose about data-rating cannot pass
    or fail a test about data-rating."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def test_the_page_loads_the_view_before_the_script_that_reads_it():
    html = read("racecast", "templates", "predictions.html")
    assert "scale-view.js" in html, "the page had no rating view at all"
    # ! ORDER IS THE DEPENDENCY. predictions.js calls window.rcScale on its
    #   first render; scale-view.js is what defines it.
    tags = re.findall(r"<script src=\"\{\{ static_v\('([^']+)'\)", html)
    assert "scale-view.js" in tags and "predictions.js" in tags
    assert tags.index("scale-view.js") < tags.index("predictions.js"), tags
    assert 'from "_scale.html" import scale_toggle' in html
    # hidden at render: the rosters are fetched, so the server cannot know
    # whether this meet has a pool the factor moves
    assert "scale_toggle(hidden=True)" in html


def test_every_rating_the_page_draws_goes_through_rv():
    js = strip_comments(read("racecast", "static", "predictions.js"))
    assert "function rv(rating, hs)" in js
    # the six display sites, by the class each one paints into
    for cls in ("r-rating", "ar-rating"):
        for m in re.finditer(r'class="%s">\$\{([^}]*)' % cls, js):
            assert "rv(" in m.group(1), f"{cls} renders a bare number: {m.group(1)}"
    # and the paint has to be re-applied, since rows arrive after load
    assert "function applyScale()" in js
    assert js.count("applyScale()") >= 7      # one definition, six+ callers


def test_no_converted_number_ever_reaches_a_data_attribute():
    """⚠ THE ONE THAT MATTERS. data-rating is read back by addRunner and by
    the athlete picker, and what it feeds is the prediction. An hs value in
    there converts the maths, silently, and the page would look right."""
    js = strip_comments(read("racecast", "static", "predictions.js"))
    for m in re.finditer(r'data-rating="\$\{([^}]*)\}"', js):
        assert "hs" not in m.group(1), f"hs value in data-rating: {m.group(1)}"
    # the display twin travels in its own attribute, never in that one
    assert 'data-hs="${' in js


def test_both_roster_endpoints_stamp_the_hs_number():
    predict = read("racecast", "predict.py")
    app = read("racecast", "app.py")
    # the squads (runners on a card, and /api/predict/squad through them)
    assert predict.count('stampBoardRows(flat, rating_keys=("rating",), sport=sport)') == 2
    # ! AND THE DROPPED, which come from _lastKnownRatings and not from the
    #   squad build -- the one column that had two scales in it.
    assert 's.person_id, s.mean_rating, s.n_races, s.year, s.pool' in predict
    assert '"pool": prev.get("pool"),' in predict
    # "add anyone" reads its own endpoint, which had no pool at all
    assert 'stampBoardRows(out, rating_keys=("rating",), sport=sport)' in app


def test_the_card_sorts_on_the_comparable_number():
    """A rating is pool-relative, so a card spanning pools sorted on the raw
    number puts the best eighth-graders above the varsity -- the same fault
    _bestFirst exists to prevent on the server side."""
    js = strip_comments(read("racecast", "static", "predictions.js"))
    assert "const cmp = (r) => (r.hs_rating ?? r.rating ?? -Infinity);" in js
    assert "team.runners.sort((a, b) => cmp(b) - cmp(a));" in js
