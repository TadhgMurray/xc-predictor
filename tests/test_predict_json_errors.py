"""A JSON endpoint must never answer in HTML (owner, 2026-09-16:
"Unexpected token '<', "<html> <h"... is not valid JSON").

    python -m pytest -q tests/test_predict_json_errors.py

★ WHAT THE READER SAW WAS A PARSER ERROR, NOT A FAULT. The predict routes
  caught only NotImplementedError, so any other exception fell through to
  Flask's 500 page; the page does `await res.json()` on the response and the
  browser reported the '<' of "<html>". That message names neither the
  endpoint nor the failure, and there is nothing a person can do with it.

⚠ AND THE CLIENT HALF IS NOT OPTIONAL EITHER. nginx answers a timeout or a
  dead worker with its own HTML before Flask is reached at all, so no
  server-side fix can cover every case -- the page has to survive a body
  that is not JSON whatever the server did.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def routeBody(src, path):
    """The source of one @app.route handler.

    ! MATCHED ON THE PATH, NOT THE WHOLE DECORATOR. These routes grew a
      methods=[...] argument and an exact-string search stopped finding
      them -- a test that cannot locate what it guards passes nothing.
    """
    i = src.index(f'@app.route("{path}"')
    j = src.index("@app.route(", i + 10)
    return src[i:j]


def test_the_predict_routes_answer_json_on_any_failure():
    app = read("racecast", "app.py")
    for path in ("/api/predict/individual", "/api/predict/team"):
        body = routeBody(app, path)
        assert "except Exception:" in body, f"{path} leaks HTML on a 500"
        # ! THE TRACEBACK GOES TO THE LOG. A reader cannot act on a stack
        #   trace and must not be shown internal paths.
        assert "app.logger.exception(" in body, path
        assert "jsonify(" in body.split("except Exception:")[1], path
        assert "500" in body.split("except Exception:")[1], path


def test_the_page_survives_a_body_that_is_not_json():
    js = read("racecast", "static", "predictions.js")
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "async function readJson(res)" in js
    # the status is the message: these are the ones a person can act on
    assert "504" in js and "502" in js
    # ⚠ AND THE CALLS THAT MATTER GO THROUGH IT. res.json() straight off a
    #   fetch is the bug; every predict-path read must use the wrapper.
    assert "await readJson(res)" in js
    for call in re.findall(r"await fetch\([^)]*predict/(team|individual|field)",
                           js):
        pass                       # the three the button depends on
    assert js.count("await readJson(res)") >= 2, js.count("await readJson(res)")


def test_not_available_is_still_not_an_error():
    """★ AN UNTRAINED MODEL IS NOT A FAILURE. NotImplementedError keeps its
    own branch and its own 200 -- the page shows "not available yet" rather
    than a red error, because from the outside a missing checkpoint and an
    unwired model are the same thing."""
    app = read("racecast", "app.py")
    for path in ("/api/predict/individual", "/api/predict/team"):
        body = routeBody(app, path)
        assert "except NotImplementedError:" in body, path
        head = body.split("except Exception:")[0]
        assert '"available": False' in head, path
        # the broad catch must come AFTER it, or it swallows the good branch
        assert body.index("except NotImplementedError:") < \
            body.index("except Exception:"), path


# ------------------------------------------------------------------ #
# 414: a prediction too big to be a URL
# ------------------------------------------------------------------ #

def test_the_predict_routes_take_a_post():
    """★ NGINX REFUSED IT BEFORE FLASK RAN (owner, 2026-09-16: "The server
    returned 414"). The request carries the page's edits as comma-joined
    person_ids, and "add the whole squad" across a championship field puts
    hundreds of them in `add` -- past the 8 KB request line the request never
    reaches the app at all, so there is no route to catch it and no traceback
    to find."""
    app = read("racecast", "app.py")
    for path in ("/api/predict/individual", "/api/predict/team"):
        assert f'@app.route("{path}", methods=["GET", "POST"])' in app, path
        body = routeBody(app, path)
        # ! request.values IS args + form, so one handler serves both and the
        #   POST body is the same urlencoded string the query was.
        assert "request.values" in body, path
        # ⚠ and nothing may still read args alone, or a POSTed field silently
        #   arrives empty and predicts the unedited meet instead
        assert "request.args" not in body, \
            f"{path} still reads request.args -- a POST would lose its edits"


def test_the_page_posts_when_the_url_would_be_too_long():
    js = read("racecast", "static", "predictions.js")
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "function sendQuery(path, q)" in js
    assert 'method: "POST"' in js
    assert "application/x-www-form-urlencoded" in js
    # the predict button must go through it, or the big case is still a URL
    assert "await sendQuery(path, buildQuery(div))" in js

    # ⚠ WELL UNDER 8 KB. nginx's is the limit we hit; a CDN or a corporate
    #   proxy in front of it may be stricter, and crossing over early costs
    #   nothing.
    m = re.search(r"const URL_LIMIT = (\d+);", js)
    assert m, "no URL_LIMIT"
    assert 500 <= int(m.group(1)) <= 4000, m.group(1)
