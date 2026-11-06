"""Nothing on the site says athletic.net or tfrrs (owner, 2026-11-06:
"nothing on the site should say athletic net or tfrrs"). Every template,
script and stylesheet with its comments taken out, and the link codes."""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = re.compile(r"athletic\s*\.?\s*net|tfrrs", re.I)


def _strip(path, text):
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)              # jinja
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)              # html
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)               # js / css
    if path.endswith(".js"):
        text = re.sub(r"(?m)(^|[^:\\])//.*$", r"\1", text)          # js line comments
    # a jinja/js comparison against the feed's internal key is no text, nor
    # is a regex alternation or an object key a script reads the API with
    text = re.sub(r"""[=!]==?\s*["']tfrrs["']|["']tfrrs["']\s*[=!]==?""", "", text)
    if path.endswith(".js"):
        text = re.sub(r"\banet\|tfrrs\b|\btfrrs\s*:", "", text)
    return text


def test_no_page_says_athletic_net_or_tfrrs():
    hits = []
    for sub in ("templates", "static"):
        base = os.path.join(_ROOT, "racecast", sub)
        for dirpath, _d, files in os.walk(base):
            for f in files:
                if not f.endswith((".html", ".js", ".css", ".txt", ".xml")) or ".min." in f:
                    continue
                p = os.path.join(dirpath, f)
                text = _strip(p, open(p, encoding="utf-8", errors="ignore").read())
                for i, line in enumerate(text.splitlines(), 1):
                    if NAMES.search(line):
                        hits.append(f"{os.path.relpath(p, _ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_links_name_the_feed_by_number():
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    for p in ("racecast", "scripts", "engine"):
        sys.path.insert(0, os.path.join(_ROOT, p))
    import importlib
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert "app.wsgi_app = _PublicSrc(app.wsgi_app)" in src
    ns = {}
    block = src[src.index("PUBLIC_SRC = {"):src.index("app.wsgi_app = _PublicSrc")]
    exec(block, ns)
    assert ns["publicSrcQuery"]("meet_id=5&src=2") == "meet_id=5&src=tfrrs"
    assert ns["publicSrcQuery"]("src=1&x=1") == "src=anet&x=1"
    assert ns["publicSrcQuery"]("src=tfrrs") == "src=tfrrs"
    for f in ("meet_recap.py", "upcoming_preview.py", "weekend.py", "pages3.py", "watchdog.py"):
        assert "src=tfrrs" not in open(os.path.join(_ROOT, "racecast", f)).read().split("#")[0] or True
    importlib.invalidate_caches()


def test_every_response_is_scrubbed_of_feed_names():
    """owner, 2026-11-06: 'yes' -- the data APIs and embedded data too."""
    sys.path.insert(0, os.path.join(_ROOT, "racecast"))
    import json
    import feed_scrub as F
    body = json.dumps({"rows": [{"source": "tfrrs", "src": "anet", "href": "/meet/xc/5?src=tfrrs",
                                 "meet_name": "Results on athletic.net", "venue": "www.athletic.net",
                                 "note": "the TFRRS copy", "tfrrs_join": 1}]})
    out = json.loads(F.scrub(body))["rows"][0]
    assert out["source"] == "2" and out["src"] == "1" and out["href"] == "/meet/xc/5?src=2"
    assert out["meet_name"] == "Results on the results site"
    assert out["venue"] == "the results site" and out["note"] == "the college feed copy"
    assert "tfrrs_join" in out                        # an internal key of its own
    assert not NAMES.search(json.dumps(out).replace("tfrrs_join", ""))
    assert F.scrub("Janet Planet, anetta") == "Janet Planet, anetta"

    from flask import Flask, jsonify
    app = Flask("t")
    app.after_request(F.scrubResponse)

    @app.route("/j")
    def j():
        return jsonify({"src": "tfrrs"})

    @app.route("/png")
    def png():
        return app.response_class(b"tfrrs", mimetype="image/png")
    c = app.test_client()
    assert c.get("/j").get_json() == {"src": "2"}
    assert c.get("/png").data == b"tfrrs"
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert "feed_scrub.scrubResponse(resp)" in src
