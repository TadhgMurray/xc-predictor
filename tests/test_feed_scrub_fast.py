"""feed_scrub.scrub finds its matches with str.find and runs each pattern
anchored (2026-11-08: ~130 ms -> ~10 ms of CPU on a 600 KB page). The bar is
the SAME OUTPUT: every body below goes through the fast scrub and through
scrubRef, the five plain re.sub passes it replaced, and the two must agree
character for character -- random strings built from the pieces a match is
made of (prefix runs, case, Unicode spaces, the four re.I case folds, word
boundaries), the repo's own templates, scripts and docs, and board-shaped
JSON. The response hook must give byte-identical bodies too.

    python -m pytest -q tests/test_feed_scrub_fast.py
"""
import glob
import json
import os
import random
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import feed_scrub as F                                       # noqa: E402

PIECES = ["the", "The", "THE", " ", "  ", "\t", "\n", " ", " ", "www.", "WWW.",
          "athletic", "Athletic", "ATHLETIC", ".", "net", "Net", ".com", ".org", "tfrrs",
          "TFRRS", "Tfrrs", "anet", "Anet", "planet", "Janet", "src=", "src=anet",
          "src=tfrrs", "src=anetx", "=anet", '"anet"', '"tfrrs"', '"', "x", "_", "-", "e",
          "w", "h", "t", "a", "s", "r", "f", "1", "é", "★", "ı", "İ", "ſ",
          "K", "athletıc", "tfrrſ", "AthleticNet", "athletic.net",
          "the athletic.net", "on athletic net", "the tfrrs copy", "tfrrs_join", "anet_id",
          "the anet", "thethe ", '<a href="/x?src=anet&y=1">', "&"]


def _same(text):
    assert F.scrub(text) == F.scrubRef(text), repr(text)[:300]


def test_random_bodies_built_from_match_pieces():
    r = random.Random(20261108)
    for _ in range(40000):
        _same("".join(r.choice(PIECES) for _ in range(r.randint(0, 16))))


def test_the_repos_own_text():
    paths = (glob.glob(os.path.join(_ROOT, "racecast", "templates", "*.html"))
             + glob.glob(os.path.join(_ROOT, "racecast", "static", "*.js"))
             + glob.glob(os.path.join(_ROOT, "racecast", "*.py"))
             + glob.glob(os.path.join(_ROOT, "docs", "*.md")))
    assert len(paths) > 100
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            _same(fh.read())


def _boardRows(n):
    r = random.Random(n)
    names = ["José Núñez", "Janet Planet", "Zoë Müller", "Ali Yıldız", "Sam Lee"]
    meets = ["Results on athletic.net", "TFRRS import", "Clovis Invitational",
             "Saint Louis Athletic Club Open", "Planet Fitness 5K"]
    return [{"name": r.choice(names), "meet_name": r.choice(meets),
             "source": r.choice(["anet", "tfrrs"]), "rating": r.uniform(400, 1300),
             "href": "/race/xc/%d/1?src=%s" % (i, r.choice(["anet", "tfrrs"]))}
            for i in range(n)]


def test_board_json_both_encodings():
    for n in (1, 50, 2000):
        rows = _boardRows(n)
        _same(json.dumps(rows, sort_keys=True, separators=(",", ":")))
        _same(json.dumps(rows, ensure_ascii=False))
        # without the Turkish name the fast path is the one taken
        plain = [dict(x, name=x["name"].replace("ı", "i")) for x in rows]
        _same(json.dumps(plain, ensure_ascii=False))


def test_the_needle_is_the_regex():
    old = re.compile(rb"anet|tfrrs|thletic", re.I)
    r = random.Random(5)
    for _ in range(20000):
        b = "".join(r.choice(PIECES) for _ in range(r.randint(0, 8))).encode("utf-8")
        assert F.hasFeedWord(b) == bool(old.search(b)), b


def test_the_response_hook_bytes():
    flask = __import__("pytest").importorskip("flask")

    def oldHook(resp):                    # scrubResponse before 2026-11-08
        body = resp.get_data()
        if re.search(rb"anet|tfrrs|thletic", body, re.I):
            resp.set_data(F.scrubRef(body.decode("utf-8")).encode("utf-8"))
        return resp

    bodies = [json.dumps(_boardRows(300)), "<p>Janet</p>", "<p>nothing here</p>",
              '<a href="/race/xc/1/2?src=tfrrs">TFRRS</a> on athletic.net', ""]
    for body in bodies:
        for mt in ("text/html", "application/json"):
            a = F.scrubResponse(flask.Response(body, mimetype=mt))
            b = oldHook(flask.Response(body, mimetype=mt))
            assert a.get_data() == b.get_data()
            assert a.headers.get("Content-Length") == b.headers.get("Content-Length")
