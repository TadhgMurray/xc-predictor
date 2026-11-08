/*
 * sw.js -- the service worker (speed pass, 2026-11-08). OFF unless the
 * site runs with XCP_SW=on (racecast/speed.py), which serves this file at
 * /sw.js with the three values below filled in. With the flag off, /sw.js is
 * the kill switch instead (speed.KILL_SW): every browser that had this worker
 * drops it and its caches on its next visit.
 *
 * ★ WHAT IT DOES, AND ALL IT DOES.
 *   - /static/ and /img/ files whose URL carries ?v= (staticV, crestUrl):
 *     the URL changes when the file does, so they are kept and served from
 *     the worker's cache without asking (cache first).
 *   - A public page or board API answer (GET, same site, 200, its own
 *     Cache-Control says "public"): the last copy is shown AT ONCE and a fresh
 *     one is fetched behind it for next time (stale-while-revalidate), as long
 *     as the stored copy is younger than MAX_STALE seconds. Older than that,
 *     or a reload, or nothing stored: the network, as without a worker.
 *   - The network failing or answering 5xx (a swap's maintenance page): the
 *     stored copy if there is one -- what the edge does with stale-if-error.
 * ! NEVER: anything but GET (POST beacons, forms, uploads go straight
 *   through); another site; a private path (the site's own _PRIVATE_PREFIXES:
 *   /account, /login, /api/me, /search ...); a response that is not
 *   "public" or says no-store/private; a non-200.
 * ! VERSIONED: VERSION changes with every deploy (the newest static mtime),
 *   so a new file installs, takes over and drops the old caches.
 *
 *   node tests/test_sw.js
 */
"use strict";

var VERSION = "__RC_SW_VERSION__";
var MAX_STALE = +"__RC_SW_MAX_STALE__" || 900;           /* seconds */
var PRIVATE = __RC_SW_PRIVATE__;                          /* path prefixes */
/* the board APIs are under /api/ but public (app._BOARD_CACHE_CONTROL) */
var PUBLIC_API = ["/api/rankings", "/api/teams", "/api/courses"];

var STATIC_CACHE = "rc-static-" + VERSION;
var PAGE_CACHE = "rc-pages-" + VERSION;
var MAX_PAGES = 120;
var MAX_STATIC = 400;
var STAMP = "x-rc-sw-stored";

function isPrivatePath(p) {
  for (var i = 0; i < PUBLIC_API.length; i++) {
    var a = PUBLIC_API[i];
    if (p === a || p.indexOf(a + "/") === 0) {
      /* /api/rankings/rank and the like are still answers about one person */
      return p !== a && p !== "/api/rankings/units";
    }
  }
  if (p === "/sw.js") return true;
  for (var j = 0; j < PRIVATE.length; j++) {
    if (p.indexOf(PRIVATE[j]) === 0) return true;
  }
  return false;
}

/* What kind of request this is, for the worker: "static" (cache first),
   "page" (stale-while-revalidate) or "" (leave it alone). mode is the
   request's: only a page being opened ("navigate") or a board API answer
   is a "page" -- a share card or a crest without ?v= is left alone. */
function route(method, url, origin, mode) {
  if (method !== "GET") return "";
  var u;
  try { u = new URL(url); } catch (e) { return ""; }
  if (u.origin !== origin) return "";
  if (/(^|&)format=csv(&|$)/.test(u.search.slice(1))) return "";
  if ((u.pathname.indexOf("/static/") === 0 || u.pathname.indexOf("/img/") === 0)) {
    return /(^|&)v=/.test(u.search.slice(1)) ? "static" : "";
  }
  if (isPrivatePath(u.pathname)) return "";
  if (mode === "navigate") return "page";
  return u.pathname.indexOf("/api/") === 0 ? "page" : "";
}

/* May this response be stored? Its own headers decide, on top of route(). */
function storable(status, cacheControl, type) {
  if (status !== 200) return false;
  if (type && type !== "basic" && type !== "default") return false;
  var cc = String(cacheControl || "").toLowerCase();
  if (cc.indexOf("public") < 0) return false;
  if (cc.indexOf("no-store") >= 0 || cc.indexOf("private") >= 0) return false;
  return true;
}

function fresh(storedAt, now) {
  var t = +storedAt;
  return t > 0 && (now - t) / 1000 <= MAX_STALE;
}

/* for tests/test_sw.js */
if (typeof module !== "undefined" && module.exports) {
  module.exports = { route: route, storable: storable, fresh: fresh, isPrivatePath: isPrivatePath };
}

if (typeof self !== "undefined" && self.registration) {
  self.addEventListener("install", function () { self.skipWaiting(); });

  self.addEventListener("activate", function (ev) {
    ev.waitUntil((async function () {
      var keep = [STATIC_CACHE, PAGE_CACHE];
      var names = await caches.keys();
      await Promise.all(names.filter(function (n) {
        return n.indexOf("rc-") === 0 && keep.indexOf(n) < 0;
      }).map(function (n) { return caches.delete(n); }));
      if (self.registration.navigationPreload) {
        try { await self.registration.navigationPreload.enable(); } catch (e) { /* older browser */ }
      }
      await self.clients.claim();
      /* the page that registered the worker loaded before it was there:
         keep that one too (most often the HTTP cache answers), so going
         back to it is the first thing that is instant */
      var wins = await self.clients.matchAll({ type: "window" });
      await Promise.all(wins.map(function (c) {
        if (route("GET", c.url, self.location.origin, "navigate") !== "page") return null;
        return fetch(c.url, { credentials: "same-origin" }).then(function (resp) {
          if (storable(resp.status, resp.headers.get("cache-control"), resp.type)) {
            return store(PAGE_CACHE, new Request(c.url), resp, MAX_PAGES);
          }
          return null;
        }).catch(function () { return null; });
      }));
    })());
  });

  async function trim(cache, max) {
    var keys = await cache.keys();
    for (var i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
  }

  async function store(cacheName, req, resp, max) {
    try {
      var cache = await caches.open(cacheName);
      var h = new Headers(resp.headers);
      h.set(STAMP, String(Date.now()));
      var body = await resp.blob();
      await cache.put(req, new Response(body, { status: resp.status, statusText: resp.statusText, headers: h }));
      await trim(cache, max);
    } catch (e) { /* quota, a closed stream: the page is not ours to break */ }
  }

  async function fromNetwork(ev) {
    if (ev.request.mode === "navigate" && ev.preloadResponse) {
      try { var pre = await ev.preloadResponse; if (pre) return pre; } catch (e) { /* fall through */ }
    }
    return fetch(ev.request);
  }

  async function staticFirst(ev) {
    var cache = await caches.open(STATIC_CACHE);
    var hit = await cache.match(ev.request);
    if (hit) return hit;
    var resp = await fetch(ev.request);
    if (storable(resp.status, resp.headers.get("cache-control"), resp.type)) {
      ev.waitUntil(store(STATIC_CACHE, ev.request, resp.clone(), MAX_STATIC));
    }
    return resp;
  }

  async function page(ev) {
    var req = ev.request;
    var cache = await caches.open(PAGE_CACHE);
    var hit = await cache.match(req);
    /* a reload asks for the network: the reader wants new numbers */
    var reload = req.cache === "reload" || req.cache === "no-cache" || req.cache === "no-store";
    var net = fromNetwork(ev).then(function (resp) {
      if (storable(resp.status, resp.headers.get("cache-control"), resp.type)) {
        return store(PAGE_CACHE, req, resp.clone(), MAX_PAGES).then(function () { return resp; });
      }
      return resp;
    });
    if (hit && !reload && fresh(hit.headers.get(STAMP), Date.now())) {
      ev.waitUntil(net.catch(function () {}));
      return hit;
    }
    try {
      var resp = await net;
      if (resp.status >= 500 && hit) return hit;      /* stale-if-error */
      return resp;
    } catch (e) {
      if (hit) return hit;
      throw e;
    }
  }

  self.addEventListener("fetch", function (ev) {
    var kind = route(ev.request.method, ev.request.url, self.location.origin, ev.request.mode);
    if (kind === "static") ev.respondWith(staticFirst(ev));
    else if (kind === "page") ev.respondWith(page(ev));
    /* anything else: not handled, the browser does what it always does */
  });
}
