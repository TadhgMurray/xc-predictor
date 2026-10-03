/* layout.js -- phones (<= 700px): every table in a box the page's width.
 *
 * ★ THE PHONE LAYOUT, THIRD TAKE (owner, 2026-10-03: "mobile UI is kind of
 *   fucked, lots of text/tables overlapping"). The second take (2026-10-02)
 *   laid the page out as wide as its widest table -- 1,000 to 1,550px -- and
 *   set CSS `zoom` on <html> to shrink it onto the screen. Three things
 *   broke on a real phone:
 *     - iOS Safari's text autosizing (the likely cause of the overlap;
 *       desktop engines never do it, which is why the review screenshots
 *       were clean) sees a 1,500px-wide block shown at a quarter size and
 *       enlarges ITS text to be readable, block by block -- a cell yes, its
 *       neighbour maybe not -- so the enlarged text runs over the cells
 *       beside and below it.
 *     - WebKit zooms form controls and rem lengths differently from the
 *       rest (the pills and toggles came out full size inside a page at
 *       0.4), and measures scrollWidth before or after the zoom depending
 *       on the version, so the fit loop settled on the wrong width: race
 *       pages filled 60% of the screen with every table squeezed.
 *     - At 0.25 (an athlete page) the text is 3-4px: nothing reads without
 *       a pinch, and a pinch brings back the sideways scroll.
 *   So no zoom. The page is the screen's width; every block on it (the
 *   mural, headings, cards, tables) is that one width; a table wider than
 *   the screen scrolls sideways inside its own box instead of dragging the
 *   page. The phone type is a step smaller (style.css, same date) so most
 *   tables fit without scrolling at all. Desktop is untouched: nothing here
 *   runs above 700px.
 *
 * ★ THE ATHLETE SIDEBAR, FOLDED, AT THE TOP. Beside the results it would be
 *   off the right edge; under them, 3,000px down. Folded above them as
 *   "Bests & PRs" it costs one line until opened.
 *
 * ! A WRAPPER, NOT `table { display: block; overflow-x: auto }`. A table
 *   shown as a block stops stretching to 100%, so a short table came out
 *   narrower than its neighbours -- the "tables are not the same width"
 *   complaint (2026-09-06). Inside the box the table is a table again:
 *   at least the box's width, and wider only when its columns need it.
 * ! Tables drawn after load (boards, predictions, compare) are wrapped
 *   when they arrive, once per animation frame however many arrive.
 * ! To go back to the zoomed page: git show ac072eb:racecast/static/layout.js.
 */
(function () {
  "use strict";
  if (!window.matchMedia || !window.matchMedia("(max-width: 700px)").matches) return;

  function wrapTables() {
    var tables = document.querySelectorAll("body table");
    for (var i = 0; i < tables.length; i++) {
      var t = tables[i], p = t.parentElement;
      if (!p || p.classList.contains("tscroll") ||
          t.closest(".tscroll, .topbar, header, nav, .chart-slot")) continue;
      var w = document.createElement("div");
      w.className = "tscroll";
      p.insertBefore(w, t);
      w.appendChild(t);
      w.addEventListener("scroll", hint, {passive: true});
    }
    hintAll();
  }

  /* .has-more while the box hides columns to its right (style.css fades the
     edge): without it a cut-off column reads as the end of the table */
  function hint(e) {
    var w = e && e.currentTarget ? e.currentTarget : this;
    w.classList.toggle("has-more", w.scrollLeft + w.clientWidth < w.scrollWidth - 2);
  }
  function hintAll() {
    var boxes = document.querySelectorAll("div.tscroll");
    for (var i = 0; i < boxes.length; i++) { fit(boxes[i]); hint.call(boxes[i]); }
  }

  /* one-line rows unless wrapping makes the table fit the screen, or
     nearly halves its width (the rule is in style.css). Decided once per box, while it is visible: a hidden
     board measures 0 and is decided when it is shown. */
  function fit(w) {
    if (w.dataset.fit || !w.clientWidth) return;
    var t = w.firstElementChild;
    if (!t) return;
    w.classList.remove("tw-wrap");
    var one = t.offsetWidth;
    if (one > w.clientWidth + 1) {
      w.classList.add("tw-wrap");
      var wrapped = t.offsetWidth;
      // wrapped: kept when it fits, or when it at least saves the reader
      // most of the scrolling (an athlete's season: 1,250px -> 650px);
      // a squeeze that saves a little is not worth three-line names
      if (wrapped > w.clientWidth + 1 && wrapped > one * 0.6) w.classList.remove("tw-wrap");
    }
    w.dataset.fit = "1";
  }
  var lastW = window.innerWidth;
  function refit() {
    // iOS fires resize as the address bar slides away on every scroll; only
    // a new width (a turned phone) is worth measuring every table again
    if (window.innerWidth === lastW) return;
    lastW = window.innerWidth;
    var boxes = document.querySelectorAll("div.tscroll[data-fit]");
    for (var i = 0; i < boxes.length; i++) delete boxes[i].dataset.fit;
    hintAll();
  }

  function foldSidebar() {
    var layout = document.querySelector(".page-layout");
    var side = layout && layout.querySelector(":scope > .sidebar");
    if (!side || side.closest(".side-fold")) return;
    var d = document.createElement("details");
    d.className = "side-fold";
    var s = document.createElement("summary");
    s.textContent = "Bests & PRs";
    d.appendChild(s);
    layout.insertBefore(d, layout.firstElementChild);
    d.appendChild(side);
  }

  function run() {
    wrapTables();
    foldSidebar();
    window.addEventListener("load", hintAll);
    window.addEventListener("resize", refit);   // a turned phone is a new width
    // a tab or chip that shows a hidden board changes no DOM, only a class
    document.addEventListener("click", function () { setTimeout(hintAll, 60); });
    if (!window.MutationObserver) return;
    var queued = false;
    new MutationObserver(function (muts) {
      var any = false;
      for (var i = 0; i < muts.length; i++) {
        if (!muts[i].addedNodes.length && !muts[i].removedNodes.length) continue;
        any = true;
        // rows drawn into a table already boxed: decide its fit again
        var t = muts[i].target, box = t.closest && t.closest("div.tscroll");
        if (box && box !== t) delete box.dataset.fit;
      }
      if (!any || queued) return;
      queued = true;
      (window.requestAnimationFrame || setTimeout)(function () { queued = false; wrapTables(); });
    }).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
