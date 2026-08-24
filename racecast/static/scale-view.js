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

  function load() {
    /* localStorage throws in some private-browsing modes; the default view
       must survive that, so every touch is wrapped. */
    try {
      return localStorage.getItem(SCALE_KEY) === "hs" ? "hs" : "pool";
    } catch (err) {
      return "pool";
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
  }
  /* Exposed for scripts that inject .rv spans after load (JS boards). */
  window.rcScale.applySpans = applySpans;

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
