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
    """The source of one @app.route handler."""
    i = src.index(f'@app.route("{path}")')
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
