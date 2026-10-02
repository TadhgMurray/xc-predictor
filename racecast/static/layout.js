/* layout.js -- the phone layout (<= 700px), on every page with the topbar.
 *
 * ★ THE PAGE FITS THE SCREEN; A WIDE TABLE SCROLLS IN ITS OWN BOX (owner,
 *   2026-10-02, after the browser review). Until now the page grew to the
 *   widest table (style.css, "every table is the same width", 2026-09-06),
 *   so on a 390px phone every heading and paragraph ran off the right edge
 *   ("National High School Boys Cr..."). Each table is now wrapped in a
 *   .tscroll box as wide as the page: every table is still the same width as
 *   every other (the page's) and still never squeezed (min-width:
 *   max-content), and it scrolls sideways inside that box instead of the
 *   whole page doing so. The CSS half is in style.css under the same date.
 *
 * ★ THE ATHLETE SIDEBAR, FOLDED, AT THE TOP. Beside the results it sat
 *   ~1,200px to the right on a phone, reachable only by scrolling the page
 *   sideways. Above them, folded as "Bests & PRs", it is the first thing a
 *   runner looks for and costs one line until opened. Desktop is untouched.
 */
(function () {
  "use strict";
  if (!window.matchMedia || !window.matchMedia("(max-width: 700px)").matches) return;

  function wrapTables(root) {
    var tables = (root || document).querySelectorAll("body table");
    for (var i = 0; i < tables.length; i++) {
      var t = tables[i], p = t.parentElement;
      if (!p || t.closest(".tscroll, .topbar, header, nav, .chart-slot")) continue;
      var w = document.createElement("div");
      w.className = "tscroll";
      p.insertBefore(w, t);
      w.appendChild(t);
    }
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
    wrapTables(document);
    foldSidebar();
    // tables drawn later (rankings boards, predictions, compare) get the same
    var seen = window.MutationObserver && new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        if (muts[i].addedNodes.length) { wrapTables(document); return; }
      }
    });
    if (seen) seen.observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
