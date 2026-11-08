#!/usr/bin/env python3
"""
build_static_pages.py -- the most-requested pages and board answers, rendered
by the real app and written to disk, so nginx can send them without Python.

    # the owner's switch: nothing is published unless this is 1
    XCP_STATIC_PAGES=1 /srv/venv/bin/python racecast/build_static_pages.py
    ... --list                      # the URLs it would build, and their keys
    ... --from-log /var/log/nginx/access.log --top 60    # plus the log's hottest
    ... --print-nginx http|server   # the config deploy/nginx_static.sh installs
    ... --verify http://127.0.0.1   # every built URL through nginx, static vs app
    ... --clear                     # stop serving now (nginx falls through)

★ WHY (2026-11-08, "serve the most-requested responses without Python").
  The front page, the board pages and the first page of each board are the
  same bytes for everybody between two runs of the pipeline -- the top bar
  renders signed out on every page (app.sharedPageKey) and the boards read no
  session (tests/test_board_api_cache.py). Each still costs a gunicorn worker
  and a render whenever the edge misses. A file costs nginx a stat and a
  sendfile.

★ RENDERED BY THE APP ITSELF, NEVER BY A COPY OF IT. Each URL is a GET through
  app.test_client() with the Host and X-Forwarded-Proto the live site sees, so
  every before/after_request hook -- the feed scrub (feed_scrub.py), the
  hidden-athlete mask, the cache and security headers -- runs exactly as it
  does for a visitor. This process imports the app fresh, so nothing it
  serves comes out of a worker's 15-minute board copy: after a go-live the
  files are the new boards at once.

★ ONLY WHAT NGINX CAN REPRODUCE EXACTLY IS WRITTEN. A response is kept only
  when it is a 200 whose headers are exactly the set nginx adds for its
  profile (staticHeaders: the app's own _headers run on a probe, so a change
  there changes both) plus a Content-Type of HTML or JSON. Anything else -- a
  Set-Cookie, a Vary, an X-Robots-Tag, a redirect, a 503 during a swap, a body
  holding "<!--#" (nginx's SSI pass removes Last-Modified, see nginxServer) --
  is left to gunicorn. And nothing is published while the installed nginx
  snippet was written for other header values (its signature line): a
  changed XCP_PAGE_S_MAXAGE then falls through to the app until
  deploy/nginx_static.sh is run again.

★ HOW STALE A FILE MAY BE: AT MOST TWO TEN-MINUTE WINDOWS. nginx reads the
  file from b/<window>/, where <window> is the local time cut to ten minutes
  (2026-11-08T14:2). A build links the window it ran in and the next one to
  its generation. Built every five minutes (deploy/install_static_timer.sh)
  a file is at most ~5 minutes older than a fresh render -- the front page's
  own shared copy is HOME_TTL (300 s) and a worker's board copy 900 s, so
  this is no staler than today. If the builder stops, nothing extends the
  windows and within twenty minutes every request is the app's again: a dead
  timer can never pin an old front page.

★ INVALIDATION. The pipeline runs this right after the boards go live and
  before the edge purge (run_pipeline.sh 10_static_pages / 19_static_pages,
  nightly_update.sh 14_static_pages), so the edge refills from new files.
  --clear unlinks every window at once.

! THE KEY IS THE EXACT REQUEST LINE. nginx maps $request_uri to a file name
  (NGINX_KEY_RULES below, mirrored by keyFor): the same path and the same
  query string, character for character, or no file. A request that sends
  the arguments in another order, adds one, or carries the session cookie,
  another host, plain http or the X-Racecast-Static bypass header goes to
  gunicorn as before.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

ENABLED = os.environ.get("XCP_STATIC_PAGES", "0") == "1"
STATIC_DIR = os.environ.get("XCP_STATIC_DIR") or "/var/cache/racecast-static"
SITE_HOST = os.environ.get("XCP_STATIC_HOST") or "racecast.co"
NGINX_SNIPPET = (os.environ.get("XCP_STATIC_NGINX_SNIPPET")
                 or "/etc/nginx/snippets/racecast-static.conf")
# the session cookie (accounts.COOKIE): a request carrying it goes to the app
SESSION_COOKIE = "xcp_session"
BYPASS_HEADER = "X-Racecast-Static"
# a generation no window points at is removed once it is this old
KEEP_OLD_GEN_S = 1800
# ten minutes: the local time cut after the tens digit of the minute
WINDOW_LEN = len("2026-11-08T14:2")
WINDOW_S = 600
USER_AGENT = "racecast-static/1.0 (racecast@racecast.co)"
# ★ THE SERVER'S gzip_min_length (deploy/nginx_compress.sh sets 1024). The
#   app's answer carries a Content-Length, so nginx leaves one shorter than
#   this uncompressed and with no Vary; a file through the SSI pass has no
#   length, and nginx would compress it and add Vary. So a page-profile file
#   must be at least this long, and the "u" profile (always tiny) is sent
#   with gzip off. deploy/nginx_static.sh passes the value nginx -T shows.
def gzipMin():
    """XCP_STATIC_GZIP_MIN_LENGTH, else what the installed snippet was written
    for, else 1024."""
    return int(os.environ.get("XCP_STATIC_GZIP_MIN_LENGTH")
               or _snippetValue("gzip-min-length") or 1024)

_POOLS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")

# ★ THE LIST. Pages most visitors land on (scripts/warm_site.py's list), and
#   the board requests rankings.js makes on load, spelled exactly as it spells
#   them (URLSearchParams in buildQuery; checked by loading /rankings in a
#   browser: board, pool, sport, scope, scale, limit, offset, sort, then the
#   board's own extras). A first visit picks hs_m or hs_f at random
#   (rc_level), a returning one its last level. /api/updates/latest is asked
#   by every page's top bar.
PAGES = ["/", "/rankings", "/scale", "/records", "/meets", "/predictions",
         "/recruiting", "/conversions", "/breakouts", "/projections", "/data",
         "/rankings/xc/hs-boys", "/rankings/xc/hs-girls",
         "/rankings/xc/college-men", "/rankings/xc/college-women",
         "/rankings/xc/ms-boys", "/rankings/xc/ms-girls"]


def _board(board, pool, sport, extra=""):
    return (f"/api/rankings?board={board}&pool={pool}&sport={sport}&scope=usa"
            f"&scale=hs&limit=50&offset=0&sort=rating{extra}")


def defaultUrls():
    urls = list(PAGES)
    for sport in ("XC", "TF"):
        urls += [_board("ability", p, sport) for p in _POOLS]
    urls += [_board("performance", p, "XC", "&distance=5000") for p in _POOLS]
    urls += [f"/api/teams?board=teams&pool={p}&sport=XC&scope=usa&scale=hs"
             f"&limit=50&offset=0&min_athletes=5" for p in _POOLS]
    urls.append("/api/rankings?board=pr&pool=all&sport=TF&scope=usa&scale=hs"
                "&limit=50&offset=0&sort=time&distance=1600")
    # warm_site.py's short spellings (links from other pages and the docs)
    urls += [f"/api/rankings?board={b}&sport=XC&pool={p}&limit=50"
             for b in ("ability", "performance") for p in _POOLS]
    urls.append("/api/updates/latest")
    # ★ AND EACH ONE'S CANONICAL SPELLING (2026-11-08): rankings.js now builds
    #   board URLs through rcBoardUrl (edge_cache.canonicalUrl: parameters
    #   sorted, offset=0 dropped), so a browser with the new script asks the
    #   canonical form and one with the old script the spelling above. Both
    #   are built; a file is only ever served for its exact request line.
    import edge_cache
    seen = set(urls)
    for u in list(urls):
        path, _, qs = u.partition("?")
        if not qs:
            continue
        c = edge_cache.canonicalUrl(path, qs)
        if c and c not in seen:
            seen.add(c)
            urls.append(c)
    return urls


# ------------------------------------------------------------------ #
#  the key: one request line -> one file name (nginx and Python agree)
# ------------------------------------------------------------------ #
# ! ONE CHARACTER CLASS FOR BOTH SIDES. A path segment is never empty (so
#   /rankings/ -- a 301 in the app -- cannot read /rankings' file), a query
#   holds no "/" (so it is one file name), and nothing with "/." in it is
#   served at all (no "..", no dot segments). "@" cannot occur in either, so
#   the "/@" that joins them cannot be faked.
_SEG = r"[A-Za-z0-9_.,%=&+~:-]"
_QCH = r"[A-Za-z0-9_.,%=&+~:-]"
_PATH = rf"(?:/{_SEG}+)+|/"
NGINX_KEY_RULES = [
    (r"/\.", None),
    (rf"^(?<rcs_p>{_PATH})\?(?<rcs_q>{_QCH}*)$", "$rcs_p/@$rcs_q"),
    (rf"^(?<rcs_p2>{_PATH})$", "$rcs_p2/@"),
]
_PY_RULES = [(re.compile(rx.replace("(?<", "(?P<")), out) for rx, out in NGINX_KEY_RULES]
# one file-name component on every filesystem nginx might sit on
_MAX_NAME = 240


def keyFor(request_uri):
    """The file key nginx computes for this request line ("/x/@a=1"), or None
    when nginx would not look for a file at all."""
    for rx, out in _PY_RULES:
        m = rx.search(request_uri)
        if not m:
            continue
        if out is None:
            return None
        key = out
        for name, val in m.groupdict().items():
            key = key.replace("$" + name, val)
        if any(len(part) > _MAX_NAME for part in key.split("/")):
            return None
        return key
    return None


def _relFile(key, profile, ext):
    """b/<window>/<this> for a key: k<key>.<profile>.<ext>, with the root's
    double slash ("//@") folded the way the filesystem folds it."""
    return os.path.normpath("k" + key + f".{profile}.{ext}").lstrip("/")


# ------------------------------------------------------------------ #
#  the headers nginx must send
# ------------------------------------------------------------------ #
_TYPES = {"text/html; charset=utf-8": "html", "application/json": "json"}
# headers that describe the transfer, not the answer: nginx and the app set
# them each in their own way, and no cache or browser keys on them
_TRANSPORT = {"content-length", "content-type", "date", "server", "connection",
              "transfer-encoding", "content-encoding", "vary", "keep-alive",
              # nginx's own instruction (edge_cache.microcacheHeader): nginx
              # acts on it and strips it, no visitor ever receives it
              "x-accel-expires"}


def _app():
    """The site's Flask app, imported the way gunicorn imports it."""
    os.environ.setdefault("XCP_PREWARM", "0")        # no predict warm-up thread
    for d in ("racecast", "scripts", "engine"):
        p = os.path.join(_ROOT, d)
        if p not in sys.path:
            sys.path.insert(0, p)
    # ! THE IMPORT TALKS ON STDOUT (the distance curves, the pool), and
    #   --print-nginx's stdout IS the config file
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        import app as A
    return A


def staticHeaders(A=None):
    """{profile: [(header, value)...]} -- what nginx adds to a file, in the
    app's own order. Profile "p" is a public page or board (the app's
    PUBLIC_CACHE_CONTROL), "u" the top bar's /api/updates/latest
    ("public, max-age=300"). The rest is app._headers run on a probe 200 for
    an https request, so the security headers can never drift from it."""
    A = A or _app()
    import flask
    out = {}
    for profile, cc in (("p", A.PUBLIC_CACHE_CONTROL), ("u", "public, max-age=300")):
        with A.app.test_request_context("/static-probe", base_url=f"https://{SITE_HOST}",
                                        headers={"X-Forwarded-Proto": "https"}):
            resp = flask.Response("x")
            resp.headers["Cache-Control"] = cc
            resp = A._headers(resp)
            out[profile] = sorted((k, v) for k, v in resp.headers.items()
                                  if k.lower() not in _TRANSPORT)
    return out


def signature(headers):
    """A short hash of the header sets and the key rules: the installed
    nginx snippet carries it, and a build publishes only when they agree."""
    blob = json.dumps({"h": headers, "k": NGINX_KEY_RULES, "t": _TYPES, "gz": gzipMin(),
                       "host": SITE_HOST, "dir": STATIC_DIR}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def profileOf(status, headers, body, want):
    """(profile, ext) when nginx can send this response byte for byte, else
    (None, reason)."""
    if status != 200:
        return None, f"status {status}"
    ext = _TYPES.get(headers.get("Content-Type", ""))
    if ext is None:
        return None, f"content type {headers.get('Content-Type')!r}"
    rest = sorted((k, v) for k, v in headers.items()
                  if k.lower() not in ("content-length", "content-type", "x-accel-expires"))
    for profile, hs in want.items():
        if rest == hs:
            break
    else:
        return None, "headers " + ", ".join(f"{k}: {v}" for k, v in rest)
    if b"<!--#" in body:
        return None, "body holds an SSI marker"
    if (profile == "u") != (len(body) < gzipMin()):
        return None, f"{len(body)} bytes: nginx would not compress it the same way"
    return (profile, ext), None


# ------------------------------------------------------------------ #
#  build and publish
# ------------------------------------------------------------------ #

def renderAll(urls, A=None, log=print):
    """[(url, status, headers, body, seconds)] through the real app."""
    A = A or _app()
    client = A.app.test_client()
    out = []
    for url in urls:
        t = time.time()
        try:
            r = client.get(url, base_url=f"https://{SITE_HOST}",
                           headers={"X-Forwarded-Proto": "https",
                                    "X-Real-IP": "127.0.0.1",
                                    "User-Agent": USER_AGENT})
            out.append((url, r.status_code, r.headers, r.get_data(), time.time() - t))
        except Exception as exc:                        # noqa: BLE001 -- one URL never stops the rest
            log(f"[static] {url}: {type(exc).__name__}: {exc}")
            out.append((url, None, {}, b"", time.time() - t))
    return out


def window(ts=None):
    """The ten-minute window nginx is in at `ts` (local time, as
    $time_iso8601 prints it)."""
    return _dt.datetime.fromtimestamp(ts if ts is not None else time.time()).isoformat()[:WINDOW_LEN]


def writeGeneration(root, rendered, want, log=print):
    """Write the keepable responses into a new generation directory under
    root/gen/ (complete before anything points at it). Returns (gen_dir,
    manifest)."""
    gens = os.path.join(root, "gen")
    os.makedirs(gens, exist_ok=True)
    gen = tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=gens)
    os.chmod(gen, 0o755)
    manifest = {"built_at": time.time(), "host": SITE_HOST, "files": {}, "skipped": {}}
    for url, status, headers, body, secs in rendered:
        key = keyFor(url)
        if key is None:
            manifest["skipped"][url] = "no nginx key"
            continue
        if status is None:
            manifest["skipped"][url] = "render failed"
            continue
        got, why = profileOf(status, headers, body, want)
        if got is None:
            manifest["skipped"][url] = why
            continue
        profile, ext = got
        rel = _relFile(key, profile, ext)
        path = os.path.join(gen, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        os.chmod(path, 0o644)
        manifest["files"][url] = {"file": rel, "bytes": len(body), "seconds": round(secs, 3),
                                  "sha256": hashlib.sha256(body).hexdigest()}
    for d, _subs, _files in os.walk(gen):
        os.chmod(d, 0o755)
    with open(os.path.join(gen, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    os.chmod(os.path.join(gen, "manifest.json"), 0o644)
    for url, why in manifest["skipped"].items():
        log(f"[static] not static: {url} ({why})")
    return gen, manifest


def _relink(link, target):
    """Point `link` at `target` atomically (a symlink renamed over the old)."""
    tmp = f"{link}.tmp{os.getpid()}"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, link)


def publish(root, gen, now=None):
    """Point this window and the next at `gen`; drop windows that are over
    and generations nothing points at any more."""
    now = time.time() if now is None else now
    bdir = os.path.join(root, "b")
    os.makedirs(bdir, exist_ok=True)
    os.chmod(bdir, 0o755)
    live = {window(now), window(now + WINDOW_S)}
    rel = os.path.relpath(gen, bdir)
    for w in sorted(live):
        _relink(os.path.join(bdir, w), rel)
    _sweep(root, live, now)


def clear(root):
    """Unlink every window: nginx finds no file and every request goes to the
    app. The generations are swept on the next build."""
    bdir = os.path.join(root, "b")
    if not os.path.isdir(bdir):
        return 0
    n = 0
    for name in os.listdir(bdir):
        try:
            os.unlink(os.path.join(bdir, name))
            n += 1
        except (IsADirectoryError, FileNotFoundError):
            pass
    return n


def _sweep(root, live, now):
    bdir = os.path.join(root, "b")
    used = set()
    for name in os.listdir(bdir):
        path = os.path.join(bdir, name)
        if name not in live and name < min(live):
            try:
                os.unlink(path)
            except (IsADirectoryError, FileNotFoundError):
                pass
            continue
        try:
            used.add(os.path.realpath(path))
        except OSError:
            pass
    gens = os.path.join(root, "gen")
    # ! THE TWO NEWEST RETIRED GENERATIONS STAY, whatever their age, so a
    #   request that found its file a moment before the windows moved still
    #   finds it when nginx opens it; older ones go once KEEP_OLD_GEN_S old
    #   (names start with the build time, so they sort by age)
    retired = sorted((n for n in os.listdir(gens)
                      if os.path.realpath(os.path.join(gens, n)) not in used), reverse=True)
    for name in retired[2:]:
        path = os.path.join(gens, name)
        try:
            if now - os.stat(path).st_mtime > KEEP_OLD_GEN_S:
                shutil.rmtree(path, ignore_errors=True)
        except FileNotFoundError:
            pass


def _snippetValue(name, path=None):
    try:
        with open(path or NGINX_SNIPPET) as fh:
            for line in fh:
                m = re.match(rf"#\s*racecast-static-{name}:\s*(\S+)", line)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def installedSignature(path=None):
    """The signature line of the installed nginx snippet, or None."""
    return _snippetValue("signature", path)


def _ownLike(root, *paths):
    """Run as root (the pipeline), hand what was written to the directory's
    owner (the site user the timer runs as), so its sweep can remove it."""
    if os.geteuid() != 0:
        return
    st = os.stat(root)
    for top in paths:
        os.lchown(top, st.st_uid, st.st_gid)
        if os.path.isdir(top) and not os.path.islink(top):
            for d, subs, files in os.walk(top):
                for n in subs + files:
                    os.lchown(os.path.join(d, n), st.st_uid, st.st_gid)


# ------------------------------------------------------------------ #
#  the nginx side (deploy/nginx_static.sh writes these two)
# ------------------------------------------------------------------ #

def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def nginxHttp(headers=None):
    """conf.d/racecast-static.conf: the maps, at http level."""
    host = re.escape(SITE_HOST)
    lines = [
        "# written by deploy/nginx_static.sh from racecast/build_static_pages.py --print-nginx http",
        f"# racecast-static-signature: {signature(headers or staticHeaders())}",
        f"# racecast-static-gzip-min-length: {gzipMin()}",
        "# the ten-minute window: the local time cut after the tens of minutes",
        "map $time_iso8601 $rcs_window {",
        '    "~^(?<rcs_w>\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d)" $rcs_w;',
        "    default -;",
        "}",
        "# who may get a file: GET/HEAD for the site's host over https, with no",
        "# session cookie and no bypass header; everyone else reaches the app",
        f'map "$request_method|$host|$http_x_forwarded_proto|$http_{BYPASS_HEADER.lower().replace("-", "_")}|$cookie_{SESSION_COOKIE}" $rcs_ok {{',
        f'    "~^(GET|HEAD)\\|{host}\\|https\\|\\|$" 1;',
        "    default \"\";",
        "}",
        "# the request line as a file key (build_static_pages.keyFor)",
        "map $request_uri $rcs_key {",
    ]
    for rx, out in NGINX_KEY_RULES:
        lines.append(f"    {_q('~' + rx)} {_q(out) if out else '-'};")
    lines += [
        "    default -;",
        "}",
        "map $rcs_ok $rcs_file {",
        f'    1 "{STATIC_DIR}/b/$rcs_window/k$rcs_key";',
        '    default "/nonexistent/racecast-static";',
        "}",
        "",
    ]
    return "\n".join(lines)


def nginxServer(headers=None):
    """snippets/racecast-static.conf: included at the top of the site's
    server { } block. Three stats on the way in; a hit is rewritten to an
    internal location that sends the file with the app's headers."""
    headers = headers or staticHeaders()
    lines = [
        "# written by deploy/nginx_static.sh from racecast/build_static_pages.py --print-nginx server",
        f"# racecast-static-signature: {signature(headers)}",
        f"# racecast-static-gzip-min-length: {gzipMin()}",
        'set $rcs_hit "";',
        "if (-f $rcs_file.p.html) { set $rcs_hit p; }",
        "if (-f $rcs_file.p.json) { set $rcs_hit p; }",
        "if (-f $rcs_file.u.json) { set $rcs_hit u; }",
        "if ($rcs_hit) { rewrite ^ /__racecast_static_$rcs_hit last; }",
    ]
    for profile, exts in (("p", ("html", "json")), ("u", ("json",))):
        tries = " ".join(f"/k$rcs_key.{profile}.{e}" for e in exts)
        lines += [
            f"location = /__racecast_static_{profile} {{",
            "    internal;",
            f"    root {STATIC_DIR}/b/$rcs_window;",
            f"    try_files {tries} =404;",
            "    types { text/html html; application/json json; }",
            "    default_type application/octet-stream;",
            "    charset utf-8;",
            "    # charset_types: the default (text/html always, never application/json)",
            "    # the app sends no ETag and no Last-Modified, and answers a",
            "    # Range or If-Modified-Since with the whole 200; so does this.",
            "    # The SSI pass is what drops Last-Modified (ssi_last_modified",
            "    # off); the builder never writes a body holding an SSI marker.",
            "    etag off;",
            "    if_modified_since off;",
            "    max_ranges 0;",
            "    ssi on;",
            "    ssi_types application/json;",
        ]
        if profile == "u":
            lines.append(f"    gzip off;   # always shorter than gzip_min_length ({gzipMin()})")
        for k, v in headers[profile]:
            lines.append(f"    add_header {k} {_q(v)};")
        lines.append("}")
    lines.append("")
    return "\n".join(lines)


INCLUDE_MARK = "# racecast-static (deploy/nginx_static.sh)"
_UPSTREAM = re.compile(r"^\s*proxy_pass\s+http://(127\.0\.0\.1|localhost):8000\b", re.M)


def patchSite(text, snippet=None):
    """The site file with `include <snippet>;` as the first line of every
    server { } block that proxies to gunicorn (127.0.0.1:8000) -- the TLS
    block certbot wrote, and the plain one on a box without it. Blocks that
    only redirect are left alone. Idempotent: an existing include is kept."""
    snippet = snippet or NGINX_SNIPPET
    line = f"    include {snippet};  {INCLUDE_MARK}\n"
    text = unpatchSite(text)
    out, i = [], 0
    for m in re.finditer(r"(?m)^[ \t]*server\s*\{[^\n]*\n", text):
        if m.start() < i:
            continue
        # the block's end: braces counted from its opening one
        depth, k = 0, text.index("{", m.start())
        while k < len(text):
            c = text[k]
            if c == "#":
                k = text.find("\n", k)
                if k < 0:
                    k = len(text)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        block = text[m.start():k + 1]
        out.append(text[i:m.end()])
        if _UPSTREAM.search(block):
            out.append(line)
        i = m.end()
    out.append(text[i:])
    return "".join(out)


def unpatchSite(text):
    """The site file without the include patchSite added."""
    return "".join(ln for ln in text.splitlines(keepends=True) if INCLUDE_MARK not in ln)


# ------------------------------------------------------------------ #
#  the hottest URLs in an access log
# ------------------------------------------------------------------ #
_LOG_LINE = re.compile(r'"(GET|HEAD) (\S+) HTTP/[0-9.]+" (\d{3}) ')


def hotFromLog(path, top, A=None):
    """The `top` most requested URLs in an nginx access log that answered 200
    and that a static file could serve (public, keyable). Private paths
    (app._PRIVATE_PREFIXES) are left out except the board APIs."""
    A = A or _app()
    import collections
    counts = collections.Counter()
    boards = ("/api/rankings?", "/api/teams?", "/api/updates/latest")
    with open(path, errors="replace") as fh:
        for line in fh:
            m = _LOG_LINE.search(line)
            if not m or m.group(3) != "200":
                continue
            uri = m.group(2)
            if uri.startswith(("/static/", "/img/")):
                continue
            if any(uri.startswith(p) for p in A._PRIVATE_PREFIXES) and not uri.startswith(boards):
                continue
            if keyFor(uri) is None:
                continue
            counts[uri] += 1
    return [u for u, _n in counts.most_common(top)]


# ------------------------------------------------------------------ #
#  verify: every built URL through nginx, as a file and through the app
# ------------------------------------------------------------------ #

VERIFY_PACE_S = float(os.environ.get("XCP_STATIC_VERIFY_PACE") or 0.12)


def _connect(u):
    """A connection to nginx at `u` (http or https). Over https the
    certificate is checked as the site's own (SNI and hostname SITE_HOST),
    so https://127.0.0.1 reaches the TLS server block certbot wrote."""
    import http.client
    if u.scheme != "https":
        return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=90)
    import socket
    import ssl

    class _Conn(http.client.HTTPSConnection):
        def connect(self):
            sock = socket.create_connection((u.hostname, u.port or 443), timeout=90)
            self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=SITE_HOST)

    return _Conn(u.hostname, u.port or 443, timeout=90)


def fetchBoth(base, url, encoding=None, method="GET"):
    """One URL through nginx twice: as a visitor (a file when there is one)
    and with the bypass header (always the app). Returns two (hit, status,
    body, headers); a gzip body comes back decompressed. Without an
    encoding `hit` is read off the transfer: the app's answer carries a
    Content-Length, a file through the SSI pass goes out chunked."""
    import gzip
    import http.client
    import urllib.parse
    u = urllib.parse.urlsplit(base)
    out = []
    for bypass in (False, True):
        hdrs = {"Host": SITE_HOST, "X-Forwarded-Proto": "https", "User-Agent": USER_AGENT}
        if encoding:
            hdrs["Accept-Encoding"] = encoding
        if bypass:
            hdrs[BYPASS_HEADER] = "1"
        # ! PACED, AND A 429 WAITED OUT: deploy/nginx_limits.sh allows one
        #   address 10 /api/ requests a second, and this asks six per URL
        for attempt in range(6):
            time.sleep(VERIFY_PACE_S)
            conn = _connect(u)
            conn.request(method, url, headers=hdrs)
            r = conn.getresponse()
            body = r.read()
            h = {k.lower(): v for k, v in r.getheaders()}
            conn.close()
            if r.status != 429:
                break
            time.sleep(2 + attempt * 2)
        if h.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        hit = "content-length" not in h
        keep = {k: v for k, v in h.items()
                if k not in ("date", "content-length", "connection", "transfer-encoding",
                             "keep-alive")}
        out.append((hit, r.status, body, keep))
    return out


def verify(base, root=STATIC_DIR, log=print):
    """Every URL of the live generation through nginx at `base`, as a file
    and as the app's answer -- plain, gzip, and HEAD: same status, body and
    headers (Content-Encoding and Vary among them), and the plain one really
    was a file. Returns the number that fail."""
    bdir = os.path.join(root, "b", window())
    with open(os.path.join(bdir, "manifest.json")) as fh:
        manifest = json.load(fh)
    bad = 0
    for url in sorted(manifest["files"]):
        fails = []
        for enc, method in ((None, "GET"), ("gzip", "GET"), (None, "HEAD")):
            (hit, st1, b1, h1), (_m, st2, b2, h2) = fetchBoth(base, url, enc, method)
            if enc is None and method == "GET" and not hit:
                fails.append("not served as a file")
            if st1 != st2:
                fails.append(f"{enc or method}: status file={st1} app={st2}")
            if b1 != b2:
                fails.append(f"{enc or method}: body file {len(b1)} bytes, app {len(b2)} bytes")
            for k in sorted(set(h1) | set(h2)):
                if h1.get(k) != h2.get(k):
                    fails.append(f"{enc or method}: {k}: file={h1.get(k)!r} app={h2.get(k)!r}")
        bad += bool(fails)
        log(f"[verify] {'DIFFERENT' if fails else 'same'}  {url}")
        for f in fails:
            log(f"           {f}")
    log(f"[verify] {len(manifest['files']) - bad} of {len(manifest['files'])} identical")
    return bad


# ------------------------------------------------------------------ #

def build(urls, root=STATIC_DIR, check_nginx=True, log=print, A=None):
    """Render, write, publish. Returns the manifest, or None when nothing
    was published (and why is logged)."""
    A = A or _app()
    try:
        from maintenance import isMaintenance
        if isMaintenance():
            log("[static] the site is in maintenance (a table swap): nothing built; "
                "the windows run out and nginx falls through to the app")
            return None
    except Exception:                                   # noqa: BLE001
        pass
    want = staticHeaders(A)
    if check_nginx:
        have, need = installedSignature(), signature(want)
        if have != need:
            n = clear(root)
            log(f"[static] nginx snippet {NGINX_SNIPPET} signature {have} != {need} "
                f"(its headers or rules are not this build's): {n} windows cleared, nothing "
                f"published. Run: sudo bash deploy/nginx_static.sh")
            return None
    t0 = time.time()
    rendered = renderAll(urls, A, log)
    gen, manifest = writeGeneration(root, rendered, want, log)
    _ownLike(root, os.path.join(root, "gen"), gen)
    publish(root, gen)
    _ownLike(root, os.path.join(root, "b"),
             *[os.path.join(root, "b", w) for w in os.listdir(os.path.join(root, "b"))])
    secs = time.time() - t0
    log(f"[static] {len(manifest['files'])} of {len(urls)} URLs written in {secs:.1f}s "
        f"to {gen}; window {window()} and the next")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=STATIC_DIR)
    ap.add_argument("--urls", help="a file of extra URLs, one per line")
    ap.add_argument("--from-log", help="an nginx access log: add its hottest URLs")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--list", action="store_true", help="print the URLs and keys, build nothing")
    ap.add_argument("--clear", action="store_true", help="unlink every window and stop")
    ap.add_argument("--print-nginx", choices=("http", "server"))
    ap.add_argument("--verify", metavar="BASE_URL")
    ap.add_argument("--force", action="store_true", help="build even with XCP_STATIC_PAGES unset")
    ap.add_argument("--no-nginx-check", action="store_true")
    ap.add_argument("--patch-site", metavar="FILE", help="add the include to an nginx site file")
    ap.add_argument("--unpatch-site", metavar="FILE", help="take it out again")
    a = ap.parse_args(argv)

    if a.patch_site or a.unpatch_site:
        path = a.patch_site or a.unpatch_site
        with open(path) as fh:
            text = fh.read()
        new = patchSite(text) if a.patch_site else unpatchSite(text)
        if new != text:
            with open(path, "w") as fh:
                fh.write(new)
        n = new.count(INCLUDE_MARK)
        print(f"[static] {path}: {n} server block(s) include the snippet")
        return 0 if (n or a.unpatch_site) else 1

    if a.clear:
        print(f"[static] {clear(a.dir)} windows cleared under {a.dir}")
        return 0
    if a.print_nginx:
        sys.stdout.write(nginxHttp() if a.print_nginx == "http" else nginxServer())
        return 0
    if a.verify:
        return 1 if verify(a.verify, a.dir) else 0

    urls = defaultUrls()
    if a.urls:
        with open(a.urls) as fh:
            urls += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if a.from_log:
        urls += hotFromLog(a.from_log, a.top)
    urls = list(dict.fromkeys(urls))
    if a.list:
        for u in urls:
            print(keyFor(u) or "(not keyable)", u)
        return 0
    if not (ENABLED or a.force):
        n = clear(a.dir)
        print(f"[static] XCP_STATIC_PAGES is not 1: nothing built, {n} windows cleared")
        return 0
    return 0 if build(urls, a.dir, check_nginx=not a.no_nginx_check) is not None else 2


if __name__ == "__main__":
    sys.exit(main())
