""""Add anyone" could not search for anything.

    python -m pytest -q tests/test_athlete_search_index.py

Owner, 2026-09-16: "the add anyone box says it could not search for anything
I write into it."

⚠ TWO BUGS IN ONE EXPRESSION, and both are invisible by reading the results:

  1. idx_athletes_name_trgm is a GIN trigram index ON THE EXPRESSION
     `COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')`. A planner
     uses an expression index only when the query's expression matches it
     CHARACTER FOR CHARACTER -- and the query said `a.first_name || ' ' ||
     a.last_name`. So the index built for this one query was never used, and
     every keystroke sequentially scanned `athletes` joined to
     `athlete_season`. add_page_indexes.py's own comment warns about exactly
     this: "THE EXPRESSION MUST MATCH THE QUERY'S CHARACTER FOR CHARACTER."

  2. NULL || ' ' || 'Smith' IS NULL, and NULL ILIKE anything is NULL, never
     true. Anyone missing a first or last name was unfindable by any
     spelling of their name.

★ AND THE BOX COULD NOT SAY SO. The route had no exception handler, so a
  timeout became nginx's HTML page, `await res.json()` threw, and the catch
  reported "Could not search." -- the one message true of a timeout, a 500,
  a bad query and a dropped connection alike.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


APP = read("racecast", "app.py")
IDX = read("scripts", "add_page_indexes.py")
JS = read("racecast", "static", "predictions.js")


def _norm(s):
    """Whitespace-flattened, so a line break inside the SQL cannot make two
    identical expressions look different."""
    return " ".join(s.split())


def test_the_filter_matches_the_index_expression():
    """The whole point of the index is that these two strings are equal."""
    i = APP.index("def api_predict_athletes(")
    route = APP[i:APP.index("\ndef ", i + 10)]
    # the expression the query filters on, as written
    m = re.search(r"where\.append\(\s*(\"[^\n]*\"\s*\n?\s*(?:f?\"[^\n]*\")?)",
                  route)
    assert m, route
    filt = _norm(m.group(1).replace('"', "").replace("f ", " "))
    assert "COALESCE(a.first_name,'')" in filt, filt
    assert "COALESCE(a.last_name,'')" in filt, filt

    # the expression the index is built on
    j = IDX.index("idx_athletes_name_trgm")
    idx = _norm(IDX[j:j + 220])
    assert "COALESCE(first_name,'')" in idx, idx
    assert "COALESCE(last_name,'')" in idx, idx

    # ! THE SAME SHAPE, ignoring only the `a.` qualifier the query needs and
    #   the index cannot have.
    assert (filt.replace("a.first_name", "first_name")
                .replace("a.last_name", "last_name")
                .find("COALESCE(first_name,'') || ' ' "
                      "|| COALESCE(last_name,'')") >= 0), filt
    print("  the filter matches the index expression ........... OK")


def test_the_route_answers_json_when_it_fails():
    """An HTML error page out of a JSON route is a failure the page cannot
    report and a reader cannot act on."""
    i = APP.index("def api_predict_athletes(")
    route = APP[i:APP.index("\ndef ", i + 10)]
    body = "\n".join(l.split("#")[0] for l in route.splitlines())
    assert "except Exception" in body, body
    assert "jsonify" in body.split("except Exception")[1], body
    print("  the route answers JSON when it fails .............. OK")


def test_the_box_reports_what_actually_happened():
    i = JS.index('"/api/predict/athletes?"')
    block = JS[i:JS.index("}, 180);", i)]
    body = "\n".join(l.split("//")[0] for l in block.splitlines())
    # readJson, not res.json: nginx's html must become a message, not a throw
    assert "readJson(res)" in body, body
    # and the message reaches the reader rather than a fixed sentence
    assert "err.message" in body, body
    print("  the box reports what actually happened ............ OK")


if __name__ == "__main__":
    for fn in [test_the_filter_matches_the_index_expression,
               test_the_route_answers_json_when_it_fails,
               test_the_box_reports_what_actually_happened]:
        fn()
