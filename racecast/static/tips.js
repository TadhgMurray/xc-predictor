/* tips.js -- every badge and ⓘ tooltip (.flag[data-blurb]), drawn so nothing
 * can clip it.
 *
 * ★ WHY (owner and the UX review, 2026-10-24: "tooltips get cut off very
 *   easily, especially on phones"). The tooltips were CSS ::after bubbles
 *   above their badge, so any box that clips its contents cut them off --
 *   every phone table sits in one (layout.js's div.tscroll), and the header
 *   row's and first row's bubbles opened straight into its top edge. A tap
 *   on the rating ⓘ, a link to /scale, also left the page on a phone.
 *
 *   With a mouse: the tooltip is one element on <body>, placed by the
 *   badge's position on screen -- above it, or below when there is no room,
 *   kept inside the window -- so no box around the badge can clip it.
 *   A press opens the panel described below. Escape, the ×, a click
 *   outside or a second press closes it.
 *
 * ★ A PRESS OPENS A PANEL BESIDE THE BADGE (owner, 2026-10-30: "the
 *   course difficulty thing shows up at the bottom rather than like the how
 *   it was rated tab ... rated not ranked should be hoverable? maybe others
 *   should too"). The sheet came from (hover: none), which a touch screen
 *   wider than a phone also answers -- so a badge opened at the bottom of the
 *   screen while the rating beside it (rating-explain.js, decided by width)
 *   opened by its cell. Now: a hover shows the small tooltip wherever a
 *   pointer is hovering (pointer events), and a click or tap on any badge
 *   opens a compact panel in rating-explain's look, anchored below or above
 *   the badge on every screen -- never a sheet from the bottom (owner,
 *   2026-10-30: "That should never be an option for any pop-up"). A badge
 *   that is a link still follows it with a mouse, and offers "More" in the
 *   panel on a touch screen.
 * ! With this script running, html.js-tips turns the CSS bubbles off
 *   (style.css); without it they are the fallback, as before.
 * ! Delegated from the document, so badges drawn later (boards, predictions,
 *   meet previews) need nothing.
 */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("js-tips");

  // ★ THE POINTER, NOT THE DEVICE (owner, 2026-10-30: "the hover doesn't
  //   work"). A media query says what a device COULD do, and a touch screen
  //   with a mouse or trackpad answers it either way; the pointer events say
  //   what is happening. A mouse or pen hovering shows the tooltip; a finger
  //   never does (its tap opens the panel).
  var lastPointer = "mouse";
  document.addEventListener("pointerdown", function (e) { lastPointer = e.pointerType || "mouse"; }, true);
  var floatEl = null, panel = null, backdrop = null, openFor = null, panelFor = null, lastFocus = null;


  function badgeOf(t) {
    return t && t.closest ? t.closest(".flag[data-blurb], .flag[data-title]") : null;
  }

  function text(el) {
    return { title: el.getAttribute("data-title") || "", blurb: el.getAttribute("data-blurb") || "" };
  }

  /* ---- with a mouse: one floating tooltip on <body> ----------------- */
  function showFloat(el) {
    if (!floatEl) {
      floatEl = document.createElement("div");
      floatEl.className = "rc-tipf";
      floatEl.setAttribute("role", "tooltip");
      document.body.appendChild(floatEl);
    }
    var t = text(el);
    floatEl.innerHTML = "";
    if (t.title) { var b = document.createElement("b"); b.textContent = t.title; floatEl.appendChild(b); }
    if (t.blurb) { var p = document.createElement("span"); p.textContent = t.blurb; floatEl.appendChild(p); }
    floatEl.style.setProperty("--tip", getComputedStyle(el).getPropertyValue("--tip") || "#111");
    floatEl.hidden = false;
    var r = el.getBoundingClientRect(), w = floatEl.offsetWidth, h = floatEl.offsetHeight;
    var vw = document.documentElement.clientWidth, gap = 10;
    var top = r.top - h - gap;
    if (top < 8) top = r.bottom + gap;                     // no room above: below
    var left = Math.min(Math.max(8, r.left), vw - w - 8);
    floatEl.style.top = (top + window.pageYOffset) + "px";
    floatEl.style.left = (left + window.pageXOffset) + "px";
    openFor = el;
  }
  function hideFloat() {
    if (floatEl) floatEl.hidden = true;
    openFor = null;
  }

  /* ---- a press: the panel beside the badge (a sheet on a phone) ---- */
  function closePanel() {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    backdrop.hidden = true;
    panelFor = null;
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus({ preventScroll: true }); } catch (e) { /* old browser */ } }
  }
  function placePanel() {
    if (!panel || panel.hidden || !panelFor) return;
    var r = panelFor.getBoundingClientRect();
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var w = Math.min(320, vw - 16);
    panel.style.width = w + "px";
    var h = panel.offsetHeight;
    var top = (vh - r.bottom - 12 >= h || r.top - 12 < h) ? r.bottom + 6 : r.top - 6 - h;
    panel.style.top = Math.max(8, top) + "px";
    panel.style.left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, vw - w - 8)) + "px";
  }
  var raf = 0;
  function follow() {
    if (raf || !panel || panel.hidden) return;
    raf = window.requestAnimationFrame(function () { raf = 0; placePanel(); });
  }
  function showPanel(el) {
    if (!panel) {
      backdrop = document.createElement("div");
      backdrop.className = "rx-backdrop";
      backdrop.addEventListener("click", closePanel);
      panel = document.createElement("div");
      panel.className = "rx-pop rx-tip";
      panel.setAttribute("role", "dialog");
      document.body.appendChild(backdrop);
      document.body.appendChild(panel);
      window.addEventListener("resize", follow);
      window.addEventListener("scroll", follow, { passive: true });
      document.addEventListener("scroll", follow, { passive: true, capture: true });
    }
    var t = text(el);
    panel.innerHTML = '<div class="rx-head"><div class="rx-title"></div>' +
      '<button type="button" class="rx-close" aria-label="Close">&times;</button></div>' +
      '<div class="rx-body"><p></p></div>';
    panel.querySelector(".rx-title").textContent = t.title;
    panel.querySelector(".rx-body p").textContent = t.blurb;
    panel.setAttribute("aria-label", t.title || "About this");
    var href = el.tagName === "A" ? el.getAttribute("href") : null;
    if (href) {
      var a = document.createElement("a");
      a.className = "rc-tips-more";
      a.href = href;
      a.textContent = "More →";
      panel.querySelector(".rx-body").appendChild(a);
    }
    panel.querySelector(".rx-close").addEventListener("click", function (e) {
      e.stopPropagation();
      closePanel();
    });
    // never a sheet from the bottom (owner, 2026-10-30): beside the badge
    panel.classList.remove("rx-sheet");
    backdrop.hidden = true;
    panel.hidden = false;
    panelFor = el;
    lastFocus = el;
    hideFloat();
    placePanel();
  }

  // capture, so a badge inside a link or a sortable header opens its panel
  // instead of following the link or sorting
  document.addEventListener("click", function (e) {
    var el = badgeOf(e.target);
    if (!el) {
      if (panel && !panel.hidden && !panel.contains(e.target)) closePanel();
      return;
    }
    if (lastPointer !== "touch" && el.tagName === "A") return;   // a link, with a mouse: follow it
    e.preventDefault();
    e.stopPropagation();
    if (panelFor === el) { closePanel(); return; }
    showPanel(el);
  }, true);

  document.addEventListener("pointerover", function (e) {
    if (e.pointerType === "touch" || panelFor) return;      // a finger, or a panel open: no bubble
    var el = badgeOf(e.target);
    if (el && el !== openFor) showFloat(el);
    else if (!el && openFor) hideFloat();
  });
  document.addEventListener("focusin", function (e) {
    if (panelFor || lastPointer === "touch") return;         // keyboard focus, not a tap
    var el = badgeOf(e.target);
    if (el) showFloat(el); else if (openFor) hideFloat();
  });
  window.addEventListener("scroll", function () { if (openFor) hideFloat(); }, { passive: true });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (panel && !panel.hidden) closePanel(); else if (openFor) hideFloat();
      return;
    }
    var el = badgeOf(e.target);
    if (el && (e.key === "Enter" || e.key === " ") && el.tagName !== "A") {
      e.preventDefault();
      showPanel(el);
    }
  });
})();
