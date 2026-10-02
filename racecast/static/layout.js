/* layout.js -- phones: one width for the whole page, opened zoomed out.
 *
 * ★ THE OWNER'S PHONE (2026-10-02): "make the phone start as zoomed out as
 *   possible and make every element on the page have the same width. Like
 *   on the home page making the mural the same width as the tables."
 *
 *   The 2026-09-06 phone pass already makes the page as wide as its widest
 *   table (no table scrolls inside itself, nothing is squeezed). What it
 *   left was a ragged page -- tables 984px wide, the mural, headings and
 *   paragraphs at the screen's 390 -- opened at 100% on a corner of it.
 *   This measures the page's natural width W and zooms the page to
 *   screen / W: laid out W wide, every block (mural, header, headings,
 *   cards, the 100%-wide tables) is the same W, and the whole width is on
 *   the screen. Pinch to read.
 *
 * ! CSS zoom ON THE ROOT, NOT THE VIEWPORT TAG. Zooming out through
 *   <meta name=viewport> widens the layout viewport past 700px in Chrome, so
 *   the phone stylesheet stops applying and the page reflows as a desktop
 *   one. CSS zoom leaves the viewport (and every media query) at the phone's
 *   width and only scales the page.
 * ! RE-MEASURED when the page changes (boards and predictions draw their
 *   tables after load) and on rotation.
 */
(function () {
  "use strict";
  var root = document.documentElement;
  var busy = false, timer = null;

  function isPhone() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 700px)").matches);
  }

  function fit() {
    if (busy) return;
    busy = true;
    try {
      root.style.zoom = "";
      if (!isPhone()) return;
      var screen = root.clientWidth || window.innerWidth;
      var w = Math.ceil(root.scrollWidth);
      if (w <= screen + 2) return;
      // laid out W wide, a grid or a mural can widen a little: settle first
      for (var i = 0; i < 4; i++) {
        root.style.zoom = String(screen / w);
        var w2 = Math.ceil(root.scrollWidth);
        if (w2 <= w + 1) break;
        w = w2;
      }
    } finally {
      busy = false;
    }
  }

  function soon() {
    clearTimeout(timer);
    timer = setTimeout(fit, 150);
  }

  function start() {
    fit();
    window.addEventListener("load", fit);
    window.addEventListener("orientationchange", function () { setTimeout(fit, 250); });
    if (window.MutationObserver) {
      new MutationObserver(function () { if (!busy) soon(); })
        .observe(document.body, {childList: true, subtree: true});
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
