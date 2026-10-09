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
    // the page's own answer first: the athlete page names the row standing
    // in for the clicked result (app.athlete_page, data-flash-id)
    var named = document.querySelector("[data-flash-id]");
    if (named) {
      var own = document.getElementById(named.getAttribute("data-flash-id"));
      if (own) return own;
    }
    var h = location.hash.slice(1);
    if (h) {
      var el = null;
      try { el = document.getElementById(decodeURIComponent(h)); } catch (e) { el = null; }
      if (el) return el;
    }
    // ?r=<result id>: a results table's row is r<id>, the athlete page's
    // season row is race-<id> (owner, 2026-10-09: "doesn't work on an
    // athlete page")
    var m = /[?&]r=(\d+)/.exec(location.search);
    return m ? (document.getElementById("r" + m[1]) ||
                document.getElementById("race-" + m[1])) : null;
  }

  // ★ AFTER THE PAGE SETTLES (owner, 2026-10-09: "still not working" on the
  //   athlete page). Its charts draw in above the season tables after load,
  //   so a row centred at DOMContentLoaded was pushed off-screen while it
  //   flashed. Wait for load, centre it, and centre it again once the charts
  //   have landed -- unless the reader has started scrolling themselves.
  var touched = false;
  ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (ev) {
    window.addEventListener(ev, function () { touched = true; }, { passive: true, once: true });
  });

  function flash(el) {
    el.classList.remove("link-flash");
    void el.offsetWidth;                     // restart the animation
    el.classList.add("link-flash");
    clearTimeout(el._lfT);
    el._lfT = setTimeout(function () { el.classList.remove("link-flash"); }, 1700);
  }

  function centre(el) {
    // a row is the thing to centre; anything else keeps the browser's own
    // fragment scroll
    if (el.tagName === "TR" || !location.hash) el.scrollIntoView({ block: "center" });
  }

  function go() {
    var el = target();
    if (!el || el.classList.contains("rc-flash")) return;
    // ! RESULT ROWS ONLY: a section anchor (#track, #reports) is a place
    //   to scroll to, not a result to point at
    if (!(el.tagName === "TR" || el.tagName === "LI" || /^(r|race-)\d+$/.test(el.id))) return;
    centre(el);
    flash(el);
    var top = el.getBoundingClientRect().top;
    setTimeout(function () {
      if (touched) return;
      var moved = Math.abs(el.getBoundingClientRect().top - top) > 40;
      if (moved) { centre(el); flash(el); }
    }, 900);
  }

  // ★ A LINK ON THE SAME PAGE TOO (owner, 2026-10-09: the athlete page's
  //   "Best race" -- "it should show it in the middle of the screen and it
  //   should flash"). Those are #race-<id> anchors: the browser jumped the
  //   row to the top edge and nothing ran. A click on an in-page anchor to
  //   a result row centres it and flashes it; back/forward does the same.
  function isRow(el) {
    return el && (el.tagName === "TR" || el.tagName === "LI" ||
                  /^(r|race-)\d+$/.test(el.id));
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    var id = a.getAttribute("href").slice(1);
    var el = id && document.getElementById(id);
    if (!isRow(el)) return;
    e.preventDefault();
    history.pushState(null, "", "#" + id);
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    flashAfterScroll(el);
  });

  // ★ THE FLASH WAITS FOR THE SCROLL (owner, 2026-10-09: "if it scrolls down
  //   a lot it's hard to see bcs it starts going as soon as you click"). A
  //   smooth scroll of a long page outlasts most of the 1.6s flash; start
  //   it when the scroll ends ('scrollend'), or after a fallback for
  //   browsers without that event, or at once when nothing needs to move.
  function flashAfterScroll(el) {
    var r = el.getBoundingClientRect();
    var centred = Math.abs((r.top + r.bottom) / 2 - window.innerHeight / 2) < 40;
    if (centred) { flash(el); return; }
    var done = false;
    function go2() { if (done) return; done = true; flash(el); }
    if ("onscrollend" in window) {
      window.addEventListener("scrollend", go2, { once: true });
      setTimeout(go2, 2500);               // a scroll that never ends
    } else {
      setTimeout(go2, 700);
    }
  }
  window.addEventListener("hashchange", function () {
    var el = document.getElementById(location.hash.slice(1));
    if (isRow(el)) { el.scrollIntoView({ block: "center" }); flash(el); }
  });

  if (document.readyState === "complete") {
    go();
  } else {
    window.addEventListener("load", function () { setTimeout(go, 50); });
  }
})();
