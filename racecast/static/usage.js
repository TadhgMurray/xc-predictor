/*
 * usage.js -- privacy-friendly usage counts and JS error reports (owner,
 * 2026-10-10, approved). Loaded once per page by _topbar.html.
 *
 * ★ WHAT IT SENDS, AND ALL IT SENDS. One beacon after the page has loaded:
 *   the route's template (data-tpl, e.g. /athlete/<int:person_id> -- never
 *   the address in the bar), the referring SITE when it is another one,
 *   and "p" or "d" for a phone- or desktop-width screen. Named UI events
 *   (rcUse('predict:run')) go in one batched beacon a few seconds later or
 *   when the tab is hidden. A JS error sends its message, the same-origin
 *   script's path and the line -- nothing else.
 * ! NO COOKIE, NO STORAGE, NO ID. Nothing is written in the browser, so
 *   nothing ties one beacon to the next. The server only adds 1 to a daily
 *   count (racecast/usage.py).
 * ! DO NOT TRACK / GLOBAL PRIVACY CONTROL: NOTHING AT ALL is sent, errors
 *   included, and rcUse becomes a no-op. So does an automated browser
 *   (navigator.webdriver).
 * ! NEVER IN THE WAY. sendBeacon does not hold up the page or its unload;
 *   every step is wrapped, and a page calls rcUse as
 *   `window.rcUse && rcUse('x')`, so a page without this file is unchanged.
 */
(function () {
  "use strict";
  var w = window, n = navigator, d = document;
  var me = d.currentScript;
  var TPL = (me && me.getAttribute("data-tpl")) || "";
  var URL_ = "/api/b";

  function optedOut(nav, win) {
    return nav.doNotTrack === "1" || nav.doNotTrack === "yes" ||
      win.doNotTrack === "1" || nav.msDoNotTrack === "1" ||
      nav.globalPrivacyControl === true || nav.webdriver === true;
  }
  var OFF = optedOut(n, w) || typeof n.sendBeacon !== "function";

  var queue = [], timer = 0, errs = 0;
  var MAX_ERRS = 3;            /* one broken page is three reports, not a flood */

  function screenClass() {
    try { return w.matchMedia("(max-width: 700px)").matches ? "p" : "d"; }
    catch (e) { return "d"; }
  }

  function send(obj) {
    if (OFF) return;
    obj.t = TPL;
    obj.s = screenClass();
    try {
      n.sendBeacon(URL_, new Blob([JSON.stringify(obj)], { type: "text/plain" }));
    } catch (e) { /* never the page's problem */ }
  }

  function flush() {
    if (timer) { clearTimeout(timer); timer = 0; }
    if (queue.length) send({ k: "e", e: queue.splice(0, 20) });
  }

  /* rcUse('area:action') -- the one helper every script calls. The names
     the server counts are listed in racecast/usage.py (EVENTS). */
  w.rcUse = function (name) {
    if (OFF || typeof name !== "string") return;
    if (queue.length < 20) queue.push(name);
    if (!timer) timer = setTimeout(flush, 4000);
  };

  function refHost() {
    try {
      if (!d.referrer) return "";
      var h = new URL(d.referrer).hostname;
      return h === location.hostname ? "" : h;
    } catch (e) { return ""; }
  }

  /* the script's path when it is ours; "" (= do not send) otherwise --
     an extension's or another site's error is not ours to collect */
  function scriptOf(u) {
    try {
      if (!u) return "(inline)";
      var x = new URL(u, location.href);
      if (x.origin !== location.origin) return "";
      return x.pathname === location.pathname ? "(inline)" : x.pathname;
    } catch (e) { return ""; }
  }

  function reportError(message, file, line) {
    if (OFF || errs >= MAX_ERRS || !message) return;
    if (!file) return;
    errs++;
    send({ k: "x", x: { m: String(message).slice(0, 200), f: file, l: line | 0 } });
  }

  /* for tests/test_usage_beacon.js */
  w.rcUse._optedOut = optedOut;

  if (OFF) return;

  function view() { send({ k: "v", r: refHost() }); }
  if (d.readyState === "complete") setTimeout(view, 0);
  else w.addEventListener("load", function () { setTimeout(view, 0); });

  d.addEventListener("visibilitychange", function () {
    if (d.visibilityState === "hidden") flush();
  });
  w.addEventListener("pagehide", flush);

  w.addEventListener("error", function (ev) {
    if (!ev || !ev.message) return;          /* a failed image, not a script */
    reportError(ev.message, scriptOf(ev.filename), ev.lineno);
  });
  w.addEventListener("unhandledrejection", function (ev) {
    var r = ev && ev.reason;
    var msg = r && r.message ? r.message : String(r);
    reportError("Unhandled rejection: " + msg, "(promise)", 0);
  });
})();
