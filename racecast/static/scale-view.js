/*
 * scale-view.js -- the HS-equivalent rating view, site-wide.
 *
 * Every rating a page renders goes through the rv() macro (_scale.html):
 * a span whose text is the own-pool number and whose data-hs carries the
 * HS-equivalent. This file owns the switch between the two, everywhere:
 * it remembers the choice (one key for the whole site), swaps the spans,
 * keeps any #scale-toggle control in sync, and tells chart scripts and
 * board renderers to redraw via a "rc-scale-change" event on document.
 *
 * Load this BEFORE any script that reads window.rcScale (athlete-charts.js,
 * rankings.js). It has no dependencies of its own.
 */

"use strict";

(function () {
  var SCALE_KEY = "rc-rating-scale";

  /*
   * ★ HS-EQUIVALENT IS THE DEFAULT (issue #50, owner 2026-09-01). A rating's
   *   own pool is the scale it was FITTED on, but it is not the scale anyone
   *   reads it on: 100 means "average for your pool", so an ms_m 130 and an
   *   hs_m 130 look alike and are not, and a reader comparing two athletes
   *   from different pools is comparing nothing. The HS-equivalent view puts
   *   every rating on one scale, which is what a number on a page is for.
   *
   * ⚠ AN EXPLICIT CHOICE STILL WINS, IN BOTH DIRECTIONS. Anyone who has
   *   pressed "Own pool" has "pool" stored and keeps it; the flip only moves
   *   readers who never expressed a preference. Testing for "pool" rather
   *   than defaulting to it is the whole of that.
   */
  function load() {
    /* localStorage throws in some private-browsing modes; the default view
       must survive that, so every touch is wrapped. */
    try {
      return localStorage.getItem(SCALE_KEY) === "pool" ? "pool" : "hs";
    } catch (err) {
      return "hs";
    }
  }

  /* The one object other scripts read. mode is 'pool' or 'hs'. */
  window.rcScale = { mode: load() };

  /* Swap every .rv span between its two values. The own-pool number is the
     span's server-rendered text; it is stashed in data-own on first touch so
     the swap is reversible without a reload. A span with no data-hs keeps
     its own number in both views. */
  function applySpans() {
    var spans = document.querySelectorAll(".rv");
    for (var i = 0; i < spans.length; i++) {
      var el = spans[i];
      if (el.dataset.own === undefined) el.dataset.own = el.textContent;
      el.textContent = (window.rcScale.mode === "hs" && el.dataset.hs)
        ? el.dataset.hs
        : el.dataset.own;
    }
    /* The numbers changed, so the order of any opted-in table may have. */
    if (typeof resortTables === "function") resortTables();
  }
  /* ★ RE-RANK SERVER-RENDERED TABLES ON THE SCALE BEING SHOWN. A school
     page mixes pools whose HS factors differ, so the order of its rows is
     a property of the scale: the server sorts for the default view and this
     re-sorts on every flip. A table opts in with data-scale-sort="N", the
     1-based column holding the rating's .rv span. The first cell is
     renumbered when it is a rank, and the staged reveal is preserved by
     position: however many rows were visible stay visible, at the top. */
  function shownValue(tr, col) {
    var td = tr.children[col - 1];
    var span = td ? td.querySelector(".rv") : null;
    var v = parseFloat(span ? span.textContent : "");
    return isNaN(v) ? null : v;
  }

  function resortTables() {
    var tables = document.querySelectorAll("table[data-scale-sort]");
    for (var t = 0; t < tables.length; t++) {
      var table = tables[t];
      var col = parseInt(table.dataset.scaleSort, 10);
      var body = table.tBodies[0];
      if (!body || !col) continue;
      var rows = Array.prototype.slice.call(body.querySelectorAll("tr"))
        .filter(function (tr) { return !tr.classList.contains("more-row"); });
      if (rows.length < 2) continue;
      var visible = rows.filter(function (tr) {
        return !tr.classList.contains("row-hidden");
      }).length;
      var keyed = rows.map(function (tr, i) {
        return { tr: tr, v: shownValue(tr, col), i: i };
      });
      /* Descending by the shown number; rows with none keep their relative
         order at the bottom. A stable tiebreak on the original index. */
      keyed.sort(function (a, b) {
        if (a.v === null && b.v === null) return a.i - b.i;
        if (a.v === null) return 1;
        if (b.v === null) return -1;
        return (b.v - a.v) || (a.i - b.i);
      });
      var footer = body.querySelector("tr.more-row");
      for (var k = 0; k < keyed.length; k++) {
        var tr = keyed[k].tr;
        tr.classList.toggle("row-hidden", k >= visible);
        var first = tr.children[0];
        if (first && /^\d+$/.test(first.textContent.trim())) {
          first.textContent = String(k + 1);
        }
        body.insertBefore(tr, footer || null);
      }
    }
  }

  /* Exposed for scripts that inject .rv spans after load (JS boards). */
  window.rcScale.applySpans = applySpans;
  window.rcScale.resortTables = resortTables;

  function syncButtons(box) {
    if (!box) return;
    var btns = box.querySelectorAll("button[data-scale]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("is-active",
                               btns[i].dataset.scale === window.rcScale.mode);
    }
  }

  function init() {
    var box = document.getElementById("scale-toggle");
    syncButtons(box);
    applySpans();

    /* A stored "hs" preference must reach charts/boards drawn at load. */
    if (window.rcScale.mode === "hs") {
      document.dispatchEvent(new CustomEvent("rc-scale-change"));
    }

    if (!box) return;
    box.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-scale]");
      if (!btn || btn.dataset.scale === window.rcScale.mode) return;
      window.rcScale.mode = btn.dataset.scale;
      try {
        localStorage.setItem(SCALE_KEY, window.rcScale.mode);
      } catch (err) { /* private mode: the choice just won't persist */ }
      syncButtons(box);
      applySpans();
      document.dispatchEvent(new CustomEvent("rc-scale-change"));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
