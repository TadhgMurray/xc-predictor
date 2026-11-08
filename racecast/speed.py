"""
speed.py -- pages that FEEL instant in the browser, without changing what
any page shows (owner, 2026-11-08: "make the site feel instant").

Two switches, both read from the environment at start-up, and one line in
_meta.html ({{ speed_head() }}) that prints what they ask for:

  XCP_SPECULATION   what the browser may fetch BEFORE a link is clicked
      "prefetch"  (default) the page's HTML, once the pointer has rested on
                  the link ~200 ms or pressed it ("moderate"). No script of
                  the next page runs, so nothing is counted, stored or sent.
      "prerender" the same moment, the whole next page rendered in the
                  background, for the pages whose scripts are known to wait
                  for the reader (see PRERENDER_PATHS); prefetch for the rest.
      "off"       nothing beyond Cloudflare's own rules (its Speed Brain
                  header already prefetches on pointer-down, "conservative").
  XCP_SW            the service worker (static/sw.js), served at /sw.js
      "off"       (default) the page registers nothing, and /sw.js is the
                  KILL SWITCH: a worker that deletes its caches and
                  unregisters itself, so a browser that had one is clean on
                  its next visit. Turning the flag off IS the kill switch.
      "on"        pages register /sw.js; see the header of static/sw.js for
                  what it caches (versioned ?v= files; public pages and board
                  answers, stale-while-revalidate, never anything private).
  XCP_SW_MAX_STALE  seconds a stored page may be shown while a fresh one is
                    fetched behind it (default 3600).

! WHAT A PREFETCH MAY NEVER TOUCH: the private paths (app._PRIVATE_PREFIXES:
  /api/, /search, /account, /login, /logout ...), a download (?format=csv,
  .csv, .png cards, .ics), a link marked rel=nofollow / download / a new tab,
  or anything carrying class no-prefetch. Prefetches are plain GETs of public,
  edge-cached pages; the browser sends Sec-Purpose: prefetch with them.
"""
import json
import os

from markupsafe import Markup

SPECULATION = os.environ.get("XCP_SPECULATION", "prefetch").strip().lower()
SW = os.environ.get("XCP_SW", "off").strip().lower()
SW_MAX_STALE = int(os.environ.get("XCP_SW_MAX_STALE", "3600") or 3600)

# downloads and machine files a hover must never fetch
_NEVER_EXT = ("csv", "png", "jpg", "webp", "svg", "ics", "xml", "txt", "json",
              "pdf", "zip", "webmanifest")
# ★ PRERENDER ONLY WHERE THE SCRIPTS WAIT FOR THE READER. A prerendered page
#   runs its scripts before anyone looks at it, so a page that stores
#   something on load (/updates marks itself read, the rankings board keeps
#   the reader's level, the follow help marks itself seen) would do it for a
#   page nobody opened. These four only read, and the two that write on load
#   (usage.js, follow.js) wait for the page to be shown (static/usage.js,
#   static/follow.js: document.prerendering).
PRERENDER_PATHS = ("/athlete/*", "/race/*", "/meet/*", "/school/*")


def speculationRules(mode, private_prefixes):
    """The <script type="speculationrules"> body for `mode`, or None."""
    if mode not in ("prefetch", "prerender"):
        return None
    # "/api/" -> "/api/*", "/search" -> "/search*" (also /search?q=...)
    never = [p + "*" for p in private_prefixes]
    never += ["/*.%s" % e for e in _NEVER_EXT]
    never += ["/*\\?*format=*", "/img/*", "/static/*", "/sw.js"]

    def where(extra=None):
        clauses = [{"href_matches": "/*"},
                   {"not": {"href_matches": never}},
                   {"not": {"selector_matches":
                            "[download], [rel~=nofollow], [target=_blank], .no-prefetch"}}]
        if extra:
            clauses.append(extra)
        return {"and": clauses}

    rules = {"prefetch": [{"source": "document", "where": where(),
                           "eagerness": "moderate"}]}
    if mode == "prerender":
        rules["prerender"] = [{"source": "document",
                               "where": where({"href_matches": list(PRERENDER_PATHS)}),
                               "eagerness": "moderate"}]
    return rules


def swVersion(static_folder):
    """Changes with every deploy: the newest mtime among the static files
    (a git pull touches what it changes), plus the settings that shape it."""
    newest = 0
    for root, _dirs, files in os.walk(static_folder):
        for f in files:
            if f.endswith((".js", ".css")):
                try:
                    newest = max(newest, int(os.path.getmtime(os.path.join(root, f))))
                except OSError:
                    pass
    return "%d-%d" % (newest, SW_MAX_STALE)


KILL_SW = """/* the service worker is switched off (XCP_SW): drop every cache it kept
   and unregister, so this browser is back to plain HTTP caching */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (ev) {
  ev.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n.indexOf("rc-") === 0; })
                            .map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.registration.unregister(); }));
});
"""

# registers the worker after load, so it never competes with the page itself
_REGISTER = ('<script>if("serviceWorker"in navigator){addEventListener("load",function(){'
             'navigator.serviceWorker.register("/sw.js").catch(function(){});});}</script>')
# flag off: a browser still running an old worker is told to drop it now,
# rather than at its next update check (no request when there is none)
_UNREGISTER = ('<script>if(navigator.serviceWorker&&navigator.serviceWorker.controller){'
               'navigator.serviceWorker.getRegistrations().then(function(r){'
               'r.forEach(function(x){x.unregister();});}).catch(function(){});}</script>')


def headHtml(private_prefixes, speculation=None, sw=None):
    """What {{ speed_head() }} prints inside <head>."""
    speculation = SPECULATION if speculation is None else speculation
    sw = SW if sw is None else sw
    out = []
    rules = speculationRules(speculation, private_prefixes)
    if rules:
        out.append('<script type="speculationrules">%s</script>'
                   % json.dumps(rules, separators=(",", ":")).replace("</", "<\\/"))
    out.append(_REGISTER if sw == "on" else _UNREGISTER)
    return Markup("".join(out))


def swBody(static_folder, private_prefixes, sw=None):
    """The JavaScript /sw.js answers with."""
    sw = SW if sw is None else sw
    if sw != "on":
        return KILL_SW
    with open(os.path.join(static_folder, "sw.js"), encoding="utf-8") as f:
        src = f.read()
    return (src.replace("__RC_SW_VERSION__", swVersion(static_folder))
               .replace("__RC_SW_MAX_STALE__", str(SW_MAX_STALE))
               .replace("__RC_SW_PRIVATE__", json.dumps(list(private_prefixes))))


# ★ THE HOME PAGE'S NINE PHOTOS, NO BIGGER THAN THE SCREEN SHOWS THEM
#   (speed pass, 2026-11-08). The tiles are 727-1100 px files shown in a box
#   ~270 px wide on a desktop and ~135 px on a phone; m2 (Bolt) is the page's
#   largest paint. A 640 px copy (mN-640.webp, made where it saves > 15%) is
#   offered beside the original, and `sizes` tells the browser how wide each
#   tile really draws, so it takes the copy only where the copy is at least
#   as sharp as the screen -- a phone, a 1x desktop -- and the original on a
#   2x desktop or tablet. Nothing on screen changes.
# ! COVER-CROPPED, SO A WIDE PHOTO DRAWS WIDER THAN ITS TILE: the tile's
#   height times the photo's aspect. The geometry is style.css's .hm-hero /
#   .hm-photos: 470 px tall in two rows on a desktop (a row ~233.5 px), 3x3 at
#   400 px on a phone (<= 760 px, a row ~131 px); the heights below carry ~2%
#   to spare. Change those rules and these numbers go with them.
# ! A browser that cannot read max() in sizes falls back to 100vw and takes
#   the original: never a blurrier picture, only no saving.
_HERO_ROW_DESKTOP = 236
_HERO_ROW_PHONE = 134
_HERO_SMALL = 640
_heroDims = {}


def _imgSize(path):
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    hit = _heroDims.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = im.size
    except Exception:                                   # noqa: BLE001
        size = None
    _heroDims[path] = (stamp, size)
    return size


def heroSrcset(static_folder, url_for_static, slot, span=1):
    """' srcset="..." sizes="..."' for a hero tile, or '' when there is no
    640 px copy of it (or anything about it cannot be read)."""
    big = os.path.join(static_folder, slot + ".webp")
    small = os.path.join(static_folder, "%s-%d.webp" % (slot, _HERO_SMALL))
    if not os.path.exists(small):
        return Markup("")
    size = _imgSize(big)
    if not size or not size[0] or not size[1] or size[0] <= _HERO_SMALL:
        return Markup("")
    w, h = size
    aspect = w / h
    phone = -(-_HERO_ROW_PHONE * aspect // 1)
    desk = -(-_HERO_ROW_DESKTOP * aspect // 1)
    sizes = "(max-width: 760px) max(34vw, %dpx), max(%dvw, %dpx)" % (
        phone, 20 * span, desk)
    srcset = "%s %dw, %s %dw" % (url_for_static("%s-%d.webp" % (slot, _HERO_SMALL)),
                                 _HERO_SMALL, url_for_static(slot + ".webp"), w)
    return Markup(' srcset="%s" sizes="%s"' % (srcset, sizes))


def install(app, private_prefixes):
    """The speed_head() and hero_srcset() globals and the /sw.js route."""
    app.jinja_env.globals["speed_head"] = lambda: headHtml(private_prefixes)

    def _heroSrcset(slot, span=1):
        static_v = app.jinja_env.globals["static_v"]
        return heroSrcset(app.static_folder, static_v, slot, span)
    app.jinja_env.globals["hero_srcset"] = _heroSrcset

    @app.route("/sw.js")
    def service_worker():
        resp = app.response_class(swBody(app.static_folder, private_prefixes),
                                  mimetype="application/javascript")
        # ! NEVER HELD: the browser re-reads it on its own schedule (at most a
        #   day), and a stored copy would keep a switched-off worker alive
        resp.headers["Cache-Control"] = "no-cache, max-age=0"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp
