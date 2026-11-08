/*
 * sw.js (speed pass, 2026-11-08): what the service worker may serve from its
 * cache, and what it must leave alone. The worker is off by default
 * (XCP_SW, racecast/speed.py); these are the rules it runs by when on.
 *
 *   node tests/test_sw.js
 *
 * ! NEVER: a POST, another site, a signed-in or per-request path (/account,
 *   /login, /api/me, /search ...), a download, a non-200, a response that is
 *   not "public" or says no-store/private.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PRIVATE = ["/api/", "/search", "/debug/", "/compare", "/login", "/logout", "/auth/", "/account"];
const SRC = fs.readFileSync(path.join(__dirname, "..", "racecast", "static", "sw.js"), "utf8")
  .replace("__RC_SW_VERSION__", "test")
  .replace("__RC_SW_MAX_STALE__", "3600")
  .replace("__RC_SW_PRIVATE__", JSON.stringify(PRIVATE));

const ctx = { module: { exports: {} }, URL };
vm.createContext(ctx);
vm.runInContext(SRC, ctx);
const sw = ctx.module.exports;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };
const O = "https://racecast.co";
const r = (method, p, mode) => sw.route(method, O + p, O, mode || "navigate");

/* 1. Nothing but GET is ever handled: beacons, forms, uploads go straight through. */
for (const m of ["POST", "PUT", "DELETE", "PATCH", "HEAD"]) {
  ok(r(m, "/athlete/1") === "", m + " /athlete/1 handled");
  ok(r(m, "/api/rankings?board=ability", "cors") === "", m + " board API handled");
  ok(r(m, "/api/b", "no-cors") === "", m + " /api/b handled");
}

/* 2. Signed-in and per-request paths are never cached, as pages or as fetches. */
for (const p of ["/account", "/account/uploads/new", "/account/status/activity", "/login",
                 "/login?next=/athlete/1", "/logout", "/auth/google/callback?code=x",
                 "/api/me", "/api/b", "/api/updates/latest", "/api/rankings/rank?person_id=1",
                 "/api/explain/xc/1", "/search?q=smith", "/search/api?q=smi", "/compare?a=1&b=2",
                 "/debug/x", "/sw.js"]) {
  ok(r("GET", p) === "", "GET " + p + " (navigate) handled: " + r("GET", p));
  ok(r("GET", p, "cors") === "", "GET " + p + " (fetch) handled: " + r("GET", p, "cors"));
}

/* 3. Public pages (navigations) and the board APIs are stale-while-revalidate. */
for (const p of ["/", "/athlete/26140046", "/race/xc/25861/1", "/school/Oregon",
                 "/rankings?board=performance&sport=XC&pool=hs_f", "/predictions"]) {
  ok(r("GET", p) === "page", "GET " + p + " not a page");
}
for (const p of ["/api/rankings?board=ability&sport=XC&pool=hs_m&limit=50",
                 "/api/teams?pool=hs_m", "/api/courses?x=1", "/api/rankings/units?pool=hs_m"]) {
  ok(r("GET", p, "cors") === "page", "GET " + p + " (fetch) not cached");
}
/* a public non-API GET fetched by script (a share card, an image) is left alone */
ok(r("GET", "/card/athlete/1.png", "no-cors") === "", "share card cached");

/* 4. Versioned files only are cache-first; an unversioned one is left alone. */
ok(r("GET", "/static/rankings.js?v=1789", "no-cors") === "static", "versioned js not static");
ok(r("GET", "/img/school/Oregon.png?state=OR&px=64&v=ab12", "no-cors") === "static", "versioned crest");
ok(r("GET", "/static/rankings.js", "no-cors") === "", "unversioned js cached");
ok(r("GET", "/img/school/Oregon.png?state=OR", "no-cors") === "", "unversioned crest cached");

/* 5. Another site, and a CSV download, are never touched. */
ok(sw.route("GET", "https://cdn.example.com/x.js?v=1", O, "no-cors") === "", "cross-origin handled");
ok(r("GET", "/api/rankings?board=ability&format=csv", "cors") === "", "csv download cached");
ok(r("GET", "/rankings?board=ability&format=csv") === "", "csv navigation cached");

/* 6. A response is stored only when its own headers say public, and it is a 200. */
ok(sw.storable(200, "public, max-age=300, s-maxage=900", "basic") === true, "public 200 not storable");
ok(sw.storable(200, "no-store", "basic") === false, "no-store storable");
ok(sw.storable(200, "private, max-age=60", "basic") === false, "private storable");
ok(sw.storable(200, "public, no-store", "basic") === false, "public+no-store storable");
ok(sw.storable(200, "", "basic") === false, "no header storable");
ok(sw.storable(200, null, "basic") === false, "null header storable");
ok(sw.storable(503, "public, max-age=300", "basic") === false, "503 storable");
ok(sw.storable(404, "public, max-age=300", "basic") === false, "404 storable");
ok(sw.storable(301, "public, max-age=300", "basic") === false, "redirect storable");
ok(sw.storable(0, "public", "opaque") === false, "opaque storable");
ok(sw.storable(200, "public", "opaqueredirect") === false, "opaqueredirect storable");

/* 7. A stored page is shown only while younger than MAX_STALE. */
const now = Date.now();
ok(sw.fresh(String(now - 60 * 1000), now) === true, "1 min old not fresh");
ok(sw.fresh(String(now - 3601 * 1000), now) === false, "an hour old still fresh");
ok(sw.fresh(null, now) === false, "unstamped fresh");
ok(sw.fresh("garbage", now) === false, "bad stamp fresh");

if (failed) { console.error(failed + " failed"); process.exit(1); }
console.log("test_sw: ok");
