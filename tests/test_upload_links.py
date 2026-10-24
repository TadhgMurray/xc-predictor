# Links instead of files (owner, 2026-10-23): racecast/uploads.py's link
# path and racecast/upload_fetch.py's safe fetch.
import io
import json
import os
import socket
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_uploads_fixes import (AC, ORIGIN, OWNER, Cur, client, fakeDb, fixture,  # noqa: E402,F401
                                recorder, UP)

import upload_fetch as UF                                          # noqa: E402


def test_a_link_is_cleaned_and_nothing_is_fetched_on_submit():
    assert UP.cleanUrl("timer.example.com/2025/r.htm") == "https://timer.example.com/2025/r.htm"
    assert UP.cleanUrl("http://a.b/x") == "http://a.b/x"
    for bad in ("", "not a link", "javascript:alert(1)", "ftp://a.b/x", "https://localhost/x",
                "https://a.b/" + "x" * 1000):
        assert UP.cleanUrl(bad) is None, bad


def test_a_signed_in_link_goes_straight_to_the_queue(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 0}]), (r"INSERT INTO result_upload \(", [{"id": 9}])])
    conn = fakeDb(monkeypatch, cur)
    r = c.post("/account/uploads/new", headers=ORIGIN, content_type="multipart/form-data",
               data={"csrf": "tok123", "url": "https://timer.example.com/r.pdf", "sport": "XC",
                     "meet_date": "2025-10-04"})
    assert r.status_code == 302 and "/account/uploads/9" in r.headers["Location"]
    ins = cur.ran(r"'submitted', %s, 'url'")[0]
    assert ins[0] == 41 and ins[1] == "timer.example.com" and ins[2] == "XC"
    assert ins[4] == "2025-10-04" and ins[-1] == "https://timer.example.com/r.pdf"
    assert conn.commits == 1 and not cur.wrote("result_upload_row")
    # a link that is not one is refused; neither file nor link asks for one
    r = c.post("/account/uploads/new", headers=ORIGIN, content_type="multipart/form-data",
               data={"csrf": "tok123", "url": "nope"})
    assert r.status_code == 400
    r = c.post("/account/uploads/new", headers=ORIGIN, content_type="multipart/form-data",
               data={"csrf": "tok123"})
    assert r.status_code == 400 and "link" in r.headers["Location"]


def test_a_file_wins_over_a_link(monkeypatch):
    c = client(monkeypatch)
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 0}]), (r"INSERT INTO result_upload \(", [{"id": 7}])])
    fakeDb(monkeypatch, cur)
    r = c.post("/account/uploads/new", headers=ORIGIN, content_type="multipart/form-data",
               data={"csrf": "tok123", "url": "https://timer.example.com/r.pdf",
                     "file": (io.BytesIO(fixture("mm_xc_lakeside.txt")), "results.txt")})
    assert r.status_code == 302 and not cur.ran(r"'url'") and len(cur.wrote("result_upload_row")[0]) == 22


def test_a_guest_can_send_a_link(monkeypatch):
    mails = []
    c = client(monkeypatch, sess=None)
    monkeypatch.setitem(UP._READY, "guest", True)
    monkeypatch.setattr(AC, "turnstileOk", lambda tok: True)
    monkeypatch.setattr(UP, "mailGuestLink", lambda e, u, m: mails.append(e) or "sent")
    cur = Cur([(r"count\(\*\)", [{"h": 0, "d": 0}]), (r"INSERT INTO result_upload \(", [{"id": 9}])])
    fakeDb(monkeypatch, cur)
    r = c.post("/account/uploads/new", headers=ORIGIN, content_type="multipart/form-data",
               data={"email": "coach@example.com", "url": "https://timer.example.com/r.htm"})
    assert r.status_code == 302 and "/account/uploads/g/" in r.headers["Location"]
    ins = cur.ran(r"'submitted', %s, 'url'")[0]
    assert ins[0] is None and ins[-3] == "coach@example.com" and mails == ["coach@example.com"]


def test_the_owner_queues_an_athletic_net_link(monkeypatch):
    up = {"id": 9, "status": "submitted", "source_url": "https://www.athletic.net/CrossCountry/meet/288317/results",
          "n_rows": 0}
    cur = Cur([(r"FOR UPDATE", [up])])
    status, err = UP.decide(cur, 9, "queue", "owner@racecast.co")
    assert (status, err) == ("approved", None)
    assert cur.ran(r"INSERT INTO meet_queue")[0] == (288317, "XC", "anet")
    cur = Cur([(r"FOR UPDATE", [dict(up, source_url="https://timer.example.com/r.htm")])])
    assert UP.decide(cur, 9, "queue", "o")[1].startswith("Only a waiting")


def test_a_link_upload_cannot_be_approved_before_it_is_read():
    up = {"meet_name": "X", "meet_date": "2025-10-04", "sport": "XC", "n_rows": 0,
          "source_url": "https://t.example.com/r"}
    got = UP.readyToApply(up, [])
    assert len(got) == 1 and "Fetch and read" in got[0] and "Attach the file" in got[0]


def test_fetch_and_read_restages_the_rows_into_the_same_upload(monkeypatch):
    c = client(monkeypatch, sess=OWNER)
    up = {"id": 9, "status": "submitted", "source_url": "https://timer.example.com/r.txt", "n_rows": 0,
          "meet_name": None, "meet_date": None, "meet_date_end": None, "location": None, "sport": None}
    cur = Cur([(r"LEFT JOIN account", [up]), (r"FOR UPDATE", [up])])
    conn = fakeDb(monkeypatch, cur)
    monkeypatch.setattr(UF, "fetchResults", lambda url: ("fetched.txt", fixture("mm_xc_lakeside.txt")))
    r = c.post("/account/admin/uploads/9/fetch", data={"csrf": "own456"}, headers=ORIGIN)
    assert r.status_code == 302 and "Fetched" in r.headers["Location"]
    assert cur.ran(r"DELETE FROM result_upload_row")[0] == (9,)
    assert cur.ran(r"UPDATE result_upload SET file_name")
    assert len(cur.wrote("result_upload_row")[0]) == 22 and conn.commits >= 1
    # a fetch that fails says so and writes nothing
    cur = Cur([(r"LEFT JOIN account", [up])])
    fakeDb(monkeypatch, cur)

    def boom(url):
        raise UF.FetchError("The site answered 404.")
    monkeypatch.setattr(UF, "fetchResults", boom)
    r = c.post("/account/admin/uploads/9/fetch", data={"csrf": "own456"}, headers=ORIGIN)
    assert r.status_code == 400 and "404" in r.headers["Location"] and not cur.ran("DELETE")


# ---------------------------------------------------------------- the fetch

class Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


def _resolve(monkeypatch, table):
    def gai(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
    monkeypatch.setattr(socket, "getaddrinfo", gai)


def test_only_public_addresses_are_fetched(monkeypatch):
    _resolve(monkeypatch, {"timer.example.com": "93.184.216.34", "inside.example.com": "10.0.0.5",
                           "meta.example.com": "169.254.169.254", "lo.example.com": "127.0.0.1"})
    assert UF.checkUrl("https://timer.example.com/r") == "https://timer.example.com/r"
    for bad in ("https://inside.example.com/", "http://meta.example.com/latest", "http://lo.example.com/",
                "file:///etc/passwd", "https://timer.example.com:8080/r"):
        with pytest.raises(UF.FetchError):
            UF.checkUrl(bad)


def test_a_redirect_to_an_inside_address_is_refused(monkeypatch):
    _resolve(monkeypatch, {"timer.example.com": "93.184.216.34", "inside.example.com": "10.0.0.5"})
    seen = []

    def getter(u):
        seen.append(u)
        return Resp(302, headers={"Location": "http://inside.example.com/admin"})
    with pytest.raises(UF.FetchError):
        UF.fetchResults("https://timer.example.com/r", getter=getter)
    assert seen == ["https://timer.example.com/r"]


def test_crystal_reports_pages_are_followed_and_joined(monkeypatch):
    _resolve(monkeypatch, {"timer.example.com": "93.184.216.34"})
    div = '<div style="position:absolute;top:{t}px;left:10px;">{x}</div>'
    p1 = ("<html>" + div.format(t=10, x="Event 1 Girls 5000 Meter Run CC") +
          '<a href="R2.html">Next Page</a></html>').encode()
    p2 = ("<html>" + div.format(t=10, x="  1 Lane, Ada   11 Westfield   19:01.2") + "</html>").encode()
    pages = {"https://timer.example.com/2025/R.html": p1, "https://timer.example.com/2025/R2.html": p2}
    name, data = UF.fetchResults("https://timer.example.com/2025/R.html",
                                 getter=lambda u: Resp(200, pages[u], {"Content-Type": "text/html"}))
    assert name == "fetched.txt"
    assert data.decode().splitlines() == ["Event 1 Girls 5000 Meter Run CC", "  1 Lane, Ada   11 Westfield   19:01.2"]


def test_a_page_too_big_is_refused(monkeypatch):
    _resolve(monkeypatch, {"timer.example.com": "93.184.216.34"})
    monkeypatch.setattr(UF, "MAX_BYTES", 10)
    with pytest.raises(UF.FetchError):
        UF.fetchResults("https://timer.example.com/r.txt", getter=lambda u: Resp(200, b"x" * 50))


def test_a_milesplit_results_page_follows_its_raw_files(monkeypatch):
    # owner, 2026-10-24: the queue's link was MileSplit's formatted page,
    # which carries no results itself -- only links to each file's raw text
    _resolve(monkeypatch, {"ms.milesplit.com": "93.184.216.34", "evil.example.com": "93.184.216.35"})
    base = "https://ms.milesplit.com/meets/1-x-2026"
    fmt = (f'<html><a href="{base}/results/11/raw">Raw</a><a href="{base}/results/11/raw">again</a>'
           f'<a href="/meets/1-x-2026/results/12/raw">Raw 2</a>'
           f'<a href="https://evil.example.com/results/13/raw">elsewhere</a></html>').encode()
    pages = {f"{base}/results?type=formatted&all=1": fmt,
             f"{base}/results/11/raw": b"<html><pre>A</pre></html>",
             f"{base}/results/12/raw": b"<html><pre>B</pre></html>"}
    seen = []

    def getter(u):
        seen.append(u)
        return Resp(200, pages[u], {"Content-Type": "text/html"})
    name, data = UF.fetchResults(f"{base}/results?type=formatted&all=1", getter=getter)
    assert name == "fetched.htm"
    assert seen[1:] == [f"{base}/results/11/raw", f"{base}/results/12/raw"]
    assert b"<pre>A</pre>" in data and b"<pre>B</pre>" in data
