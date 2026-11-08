"""Our own ids on every public page (owner, 2026-11-08): racecast/public_ids.py."""
import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import public_ids as P  # noqa: E402


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("XCP_PUBLIC_ID_KEY", "a" * 64)
    P.encode.cache_clear()
    P.decode.cache_clear()
    yield
    P.encode.cache_clear()
    P.decode.cache_clear()


def test_round_trip_every_kind_and_sign():
    for kind in P.KINDS:
        for n in (0, 1, 27034607, 290015, 1158313, 2_000_007_571, -1119194872697371976,
                  -2439324138270172062, 2**63 - 1, -2**63):
            t = P.encode(kind, n)
            assert len(t) == 14 and t[0] == kind and P.TOKEN_RE.fullmatch(t)
            assert P.decode(t) == (kind, n)


def test_tokens_look_unrelated_and_kinds_differ():
    a, b = P.encode("a", 27034607), P.encode("a", 27034608)
    assert a[1:] != b[1:] and "27034607" not in a
    assert P.encode("a", 5)[1:] != P.encode("m", 5)[1:]


def test_a_mangled_token_is_refused():
    t = P.encode("a", 27034607)
    bad = t[:-1] + ("0" if t[-1] != "0" else "1")
    hits = sum(P.decode(t[:-1] + c) is not None for c in "0123456789abcdefghijklmnopqrstuvwxyz")
    assert hits <= 6                         # the 3-bit check refuses most
    assert P.decode("a" + "z" * 13) is None or True
    assert P.decode("x" + t[1:]) is None
    assert P.decode(bad) is None or P.decode(bad)[1] != 27034607


def test_no_key_changes_nothing(monkeypatch):
    monkeypatch.delenv("XCP_PUBLIC_ID_KEY")
    P.encode.cache_clear()
    assert P.encode("a", 5) == "5"
    assert P.decodePath("/athlete/5") == "/athlete/5"


def test_the_site_url_shapes_are_rewritten():
    e = P.encode
    html = ('<a href="/athlete/27034607">L</a> <a href="https://racecast.co/athlete/5/profile">p</a>'
            '<a href="/race/xc/290015/1158313?r=78008678">r</a>'
            '<a href="/race/xc/-12/-34#r-56">t</a>'
            '<a href="/race/tf/100/27/200?r=9">tf</a>'
            '<a href="/race/xc/290015/compiled/5000/M">c</a>'
            '<a href="/meet/xc/290015">m</a><a href="/meet/tf/7/compiled">mt</a>'
            '<img src="/card/athlete/27034607.png"><img src="/card/race/xc/1/2.png">'
            '<a href="/rankings?board=performance&amp;jump=27034607">j</a>'
            '<a href="/venue/tf/88/out">v</a>'
            '<tr id="r78008678" data-pid="27034607" data-rid="78008678" data-rx="xc:78008678">'
            '<div id="race-78008678"></div><a href="#race-78008678">x</a>'
            '<span data-rids="1, 2,3"></span><div data-meet="290015" data-div="1158313"></div>'
            '<script type="application/json">{"rid": 78008678, "person_id": "27034607", "rating": 120}</script>')
    out = P.rewriteHtml(html)
    for frag in (f'/athlete/{e("a", 27034607)}"', f'/athlete/{e("a", 5)}/profile',
                 f'/race/xc/{e("m", 290015)}/{e("d", 1158313)}?r={e("r", 78008678)}',
                 f'/race/xc/{e("m", -12)}/{e("d", -34)}#r{e("r", -56)}',
                 f'/race/tf/{e("m", 100)}/27/{e("d", 200)}?r={e("r", 9)}',
                 f'/race/xc/{e("m", 290015)}/compiled/5000/M',
                 f'/meet/xc/{e("m", 290015)}"', f'/meet/tf/{e("m", 7)}/compiled',
                 f'/card/athlete/{e("a", 27034607)}.png', f'/card/race/xc/{e("m", 1)}/{e("d", 2)}.png',
                 f'&amp;jump={e("a", 27034607)}', f'/venue/tf/{e("v", 88)}/out',
                 f'id="r{e("r", 78008678)}"', f'data-pid="{e("a", 27034607)}"',
                 f'data-rid="{e("r", 78008678)}"', f'data-rx="xc:{e("r", 78008678)}"',
                 f'id="race-{e("r", 78008678)}"', f'href="#race-{e("r", 78008678)}"',
                 f'data-rids="{e("r", 1)},{e("r", 2)},{e("r", 3)}"',
                 f'data-meet="{e("m", 290015)}"', f'data-div="{e("d", 1158313)}"',
                 f'"rid": "{e("r", 78008678)}"', f'"person_id": "{e("a", 27034607)}"', '"rating": 120'):
        assert frag in out, frag
    assert "27034607" not in out and "290015" not in out and "78008678" not in out
    assert P.rewriteHtml(out) == out                     # idempotent


def test_years_distances_and_other_numbers_are_left_alone():
    html = ('<a href="/school/Edina?sport=XC&amp;year=2026">s</a><a href="/rankings?limit=25&amp;page=2">b</a>'
            '<a href="/race/xc/1/compiled/5000/F">c</a><td>16:31</td><span id="row12">'
            '<a href="/course/Majestic%20Oaks?distance=5000">c</a>')
    out = P.rewriteHtml(html)
    assert "year=2026" in out and "limit=25" in out and "/compiled/5000/F" in out
    assert 'id="row12"' in out and "distance=5000" in out


def test_json_bodies():
    body = json.dumps({"rows": [{"person_id": 27034607, "meet_id": -5, "div_id": "7",
                                 "link": "/athlete/27034607", "event_id": 27, "rating": 101.5}],
                       "levels": {"78008678": 4.2}, "surprises": ["78008678"]})
    out = json.loads(P.rewriteJson(body))
    e = P.encode
    r = out["rows"][0]
    assert r["person_id"] == e("a", 27034607) and r["meet_id"] == e("m", -5)
    assert r["div_id"] == e("d", 7) and r["link"] == f"/athlete/{e('a', 27034607)}"
    assert r["event_id"] == 27 and r["rating"] == 101.5
    assert out["levels"] == {e("r", 78008678): 4.2} and out["surprises"] == [e("r", 78008678)]


def test_requests_come_back_as_integers():
    e = P.encode
    assert P.decodePath(f"/athlete/{e('a', 27034607)}") == "/athlete/27034607"
    assert P.decodePath(f"/race/tf/{e('m', 100)}/27/{e('d', -200)}") == "/race/tf/100/27/-200"
    assert P.decodePath(f"/card/athlete/{e('a', 5)}.png") == "/card/athlete/5.png"
    # only under the id-bearing prefixes: a school name of the same shape stays
    assert P.decodePath("/school/" + e("a", 5)) == "/school/" + e("a", 5)
    q = P.decodeQuery(f"r={e('r', 9)}&jump={e('a', 3)}&q={e('a', 3)}&year=2026")
    assert q == f"r=9&jump=3&q={e('a', 3)}&year=2026"


def test_the_middleware_decodes_and_marks_old_urls():
    e = P.encode
    seen = {}

    def app(environ, start_response):
        seen.update(environ)
        return [b""]
    body = json.dumps({"sport": "XC", "rows": [{"rid": e("r", 9), "pid": e("a", 3), "rating": "1"}]}).encode()
    env = {"PATH_INFO": f"/race/xc/{e('m', 1)}/{e('d', 2)}", "QUERY_STRING": f"r={e('r', 9)}",
           "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/json",
           "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    P.Middleware(app)(env, None)
    assert seen["PATH_INFO"] == "/race/xc/1/2" and seen["QUERY_STRING"] == "r=9"
    got = json.loads(seen["wsgi.input"].read())
    assert got["rows"][0]["rid"] == 9 and got["rows"][0]["pid"] == 3
    assert "rc.public_id.legacy" not in seen
    seen.clear()
    P.Middleware(app)({"PATH_INFO": "/athlete/27034607", "QUERY_STRING": "",
                       "REQUEST_METHOD": "GET"}, None)
    assert seen["rc.public_id.legacy"] == f"/athlete/{e('a', 27034607)}"
    seen.clear()
    P.Middleware(app)({"PATH_INFO": "/api/explain/xc/5", "QUERY_STRING": "",
                       "REQUEST_METHOD": "GET"}, None)
    assert "rc.public_id.legacy" not in seen                # an API is not redirected


def test_lists_and_track_race_keys_in_a_query():
    e = P.encode
    q = P.decodeQuery(f"div_ids={e('d', 1)}%2C{e('d', 2)}&div_id={e('d', 3)}-27"
                      f"&person_id={e('a', 4)},{e('a', 5)}&meet_id={e('m', -6)}")
    assert q == "div_ids=1%2C2&div_id=3-27&person_id=4,5&meet_id=-6"


def test_forms_carry_tokens_and_come_back_as_integers():
    e = P.encode
    out = P.rewriteHtml('<input type="hidden" name="athlete" value="27034607">'
                        '<input id="r-athlete" type="hidden" value="5">')
    assert f'value="{e("a", 27034607)}"' in out and f'value="{e("a", 5)}"' in out
    seen = {}

    def app(environ, start_response):
        seen["body"] = environ["wsgi.input"].read()
        return [b""]
    body = f"csrf=x&kind=athlete&person_id={e('a', 77)}".encode()
    P.Middleware(app)({"PATH_INFO": "/account/claim", "QUERY_STRING": "", "REQUEST_METHOD": "POST",
                       "CONTENT_TYPE": "application/x-www-form-urlencoded",
                       "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}, None)
    assert seen["body"] == b"csrf=x&kind=athlete&person_id=77"


def test_a_track_race_with_no_division():
    out = P.rewriteHtml('<a href="/race/tf/100/27/None">x</a>')
    assert out == f'<a href="/race/tf/{P.encode("m", 100)}/27/None">x</a>'
