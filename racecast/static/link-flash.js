/*
 * link-flash.js -- the row a link pointed at lights up for a moment.
 *
 * ★ WHY (owner, 2026-10-09: "when you click a link to go to a result ...
 *   that result should light up for a second like the other light up
 *   things"). The race page flashed its row only for #r<id> from the meet
 *   finder, and the athlete chart flashes its own rows; every other link
 *   to a result (?r=<result id>, or any #fragment) just scrolled, or not.
 *   Loaded from _topbar.html, so every page gets it.
 *
 * Target: the element named by the #fragment, else the row #r<id> for
 * ?r=<id> -- a table row, list item or r<id> element only. A row the page already flashed itself (race-page.js's rc-flash)
 * is left alone.
 */
"use strict";

(function () {
  function target() {
    var h = location.hash.slice(1);
    if (h) {
      var el = null;
      try { el = document.getElementById(decodeURIComponent(h)); } catch (e) { el = null; }
      if (el) return el;
    }
    var m = /[?&]r=(\d+)/.exec(location.search);
    return m ? document.getElementById("r" + m[1]) : null;
  }

  function go() {
    var el = target();
    if (!el || el.classList.contains("rc-flash")) return;
    // ! RESULT ROWS ONLY: a section anchor (#track, #reports) is a place
    //   to scroll to, not a result to point at
    if (!(el.tagName === "TR" || el.tagName === "LI" || /^r\d+$/.test(el.id))) return;
    // a row is the thing to centre; anything else keeps the browser's own
    // fragment scroll
    if (el.tagName === "TR" || !location.hash) {
      el.scrollIntoView({ block: "center" });
    }
    el.classList.remove("link-flash");
    void el.offsetWidth;                     // restart the animation
    el.classList.add("link-flash");
    setTimeout(function () { el.classList.remove("link-flash"); }, 1700);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", go);
  } else {
    go();
  }
})();
