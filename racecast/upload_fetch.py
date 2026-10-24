# Project: xc-predictor / racecast
# File:    upload_fetch.py
# Purpose: A results LINK sent instead of a file (owner, 2026-10-23: "add a
#          way to upload a url ... then we can manually do it (just say file
#          is preferred)"). The link waits in the owner's queue; the owner's
#          "Fetch and read" comes here: fetch the page once, safely, turn it
#          into text results_parse reads, and hand back (filename, bytes).
#
#            fetchResults(url) -> (filename, data)   raises FetchError
#
# ★ WHAT IT TURNS INTO TEXT
#     a PDF                      pdftotext -layout (poppler-utils), as .txt
#     Crystal Reports HTML       the positioned <div>s rebuilt into lines
#                                (sources/hytek.py), every "Next Page" file
#                                of the report followed, as .txt
#     a MileSplit results page   every "raw" results file it links to
#                                (/results/<id>/raw, same host), joined, as
#                                .htm -- results_parse reads their tables
#     anything else              as it came: .htm for HTML (results_parse
#                                reads the <pre>), .csv / .txt by type
#
# ! A LINK FROM A STRANGER IS FETCHED BY THE SERVER, so it may only reach the
#   public internet: http(s), ports 80/443, and every address the host
#   resolves to must be global (no loopback, private, link-local or metadata
#   addresses). Redirects are followed by hand, at most 4, each one checked
#   the same way. 9 MB at most (uploads.MAX_UPLOAD_BYTES), 20 s a request.
#   Only the owner's button runs it, never the uploader's submit.

import ipaddress
import os
import re
import socket
import subprocess
import sys
import urllib.parse

import requests

MAX_BYTES = 9 * 1024 * 1024
TIMEOUT = 20
MAX_REDIRECTS = 4
MAX_PAGES = 40                       # a Crystal Reports report's page files
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FetchError(Exception):
    """A message for the owner: why the link could not be read."""


def _userAgent():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from sources.fetch import USER_AGENT
    return USER_AGENT


def checkUrl(url):
    """The url if it is a public http(s) address; FetchError otherwise."""
    u = urllib.parse.urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("Only http and https links can be fetched.")
    if u.port not in (None, 80, 443):
        raise FetchError("Only the usual web ports (80, 443) can be fetched.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError(f"{u.hostname} does not resolve.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise FetchError(f"{u.hostname} is not a public address.")
    return url.strip()


def _get(url, getter=None):
    """One GET, redirects followed by hand and checked -> (final url, bytes, type)."""
    getter = getter or (lambda u: requests.get(u, headers={"User-Agent": _userAgent()},
                                               timeout=TIMEOUT, allow_redirects=False, stream=True))
    for _ in range(MAX_REDIRECTS + 1):
        checkUrl(url)
        try:
            resp = getter(url)
        except requests.RequestException as exc:
            raise FetchError(f"The site did not answer ({type(exc).__name__}).") from None
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            url = urllib.parse.urljoin(url, resp.headers["Location"])
            continue
        if resp.status_code != 200:
            raise FetchError(f"The site answered {resp.status_code}.")
        data = b""
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                raise FetchError("The page is over 9 MB.")
        return url, data, (resp.headers.get("Content-Type") or "").lower()
    raise FetchError("Too many redirects.")


def _pdfText(data):
    try:
        out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                             capture_output=True, check=True, timeout=60)
    except FileNotFoundError:
        raise FetchError("pdftotext is not installed (apt install poppler-utils).") from None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise FetchError("The PDF could not be read.") from None
    return out.stdout


_RAW_LINK = re.compile(r'href="([^"]*/results/\d+/raw)"', re.I)


def _rawLinks(page, base):
    """MileSplit: the page's links to its results files' raw text, same
    host only, in page order, each once."""
    host = urllib.parse.urlsplit(base).hostname
    out = []
    for href in _RAW_LINK.findall(page):
        u = urllib.parse.urljoin(base, href)
        if urllib.parse.urlsplit(u).hostname == host and u not in out:
            out.append(u)
    return out


def fetchResults(url, getter=None):
    """A results link -> (filename, data) for results_parse.parse."""
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from sources import hytek
    final, data, ctype = _get(url, getter)
    path = urllib.parse.urlsplit(final).path.lower()
    if data[:5] == b"%PDF-" or "pdf" in ctype:
        return "fetched.txt", _pdfText(data)
    if "html" in ctype or path.endswith((".htm", ".html")) or b"<html" in data[:4000].lower():
        page = data.decode("utf-8", "replace")
        if not re.search(r"<pre", page, re.I) and hytek._DIV.search(page):
            # Crystal Reports: every page file of the report, in order
            parts, seen, here = [hytek.textFromPositionedHtml(page)], {final}, page
            base = final
            for _ in range(MAX_PAGES - 1):
                nxt = hytek.nextPageLinks(here)
                if not nxt:
                    break
                nurl = urllib.parse.urljoin(base, nxt[0])
                if nurl in seen or urllib.parse.urlsplit(nurl).hostname != urllib.parse.urlsplit(final).hostname:
                    break
                seen.add(nurl)
                base, more, _t = _get(nurl, getter)
                here = more.decode("utf-8", "replace")
                parts.append(hytek.textFromPositionedHtml(here))
            return "fetched.txt", "\n".join(parts).encode("utf-8")
        if not re.search(r"<pre", page, re.I):
            raws = _rawLinks(page, final)
            if raws:
                pages = []
                for rurl in raws[:MAX_PAGES]:
                    _f, more, _t = _get(rurl, getter)
                    pages.append(more)
                return "fetched.htm", b"\n".join(pages)
        return "fetched.htm", data
    if path.endswith(".csv") or "csv" in ctype:
        return "fetched.csv", data
    return "fetched.txt", data
