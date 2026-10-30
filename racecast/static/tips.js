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
 *   Without hover (a phone): a tap opens it as a sheet from the bottom of the
 *   screen, the same shape as "How this was rated" (rating-explain.js); a
 *   badge that is a link gets a "More" link in the sheet instead of leaving.
 *   Escape, the ×, the dimmed page or a second tap closes it.
 *
 * ! With this script running, html.js-tips turns the CSS bubbles off
 *   (style.css); without it they are the fallback, as before.
 * ! Delegated from the document, so badges drawn later (boards, predictions,
 *   meet previews) need nothing.
 */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("js-tips");

  var noHover = !!(window.matchMedia && window.matchMedia("(hover: none)").matches);
  var floatEl = null, sheet = null, backdrop = null, openFor = null, lastFocus = null;

  // ! NOT THE COURSE CELL (.dv-day): rating-explain.js opens it in the
  //   "How this was rated" panel (owner, 2026-10-30)
  function badgeOf(t) {
    var el = t && t.closest ? t.closest(".flag[data-blurb], .flag[data-title]") : null;
    return el && el.classList.contains("dv-day") ? null : el;
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

  /* ---- without hover: a sheet from the bottom ----------------------- */
  function closeSheet() {
    if (sheet) sheet.hidden = true;
    if (backdrop) backdrop.hidden = true;
    openFor = null;
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus({ preventScroll: true }); } catch (e) { /* old browser */ } }
  }
  function showSheet(el) {
    if (!sheet) {
      backdrop = document.createElement("div");
      backdrop.className = "rc-tips-backdrop";
      backdrop.addEventListener("click", closeSheet);
      sheet = document.createElement("div");
      sheet.className = "rc-tips";
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      document.body.appendChild(backdrop);
      document.body.appendChild(sheet);
    }
    var t = text(el);
    sheet.innerHTML = '<div class="rc-tips-head"><b></b><button type="button" class="rc-tips-x" aria-label="Close">&times;</button></div><p></p>';
    sheet.querySelector("b").textContent = t.title;
    sheet.querySelector("p").textContent = t.blurb;
    sheet.setAttribute("aria-label", t.title || "About this");
    var href = el.tagName === "A" ? el.getAttribute("href") : null;
    if (href) {
      var a = document.createElement("a");
      a.className = "rc-tips-more";
      a.href = href;
      a.textContent = "More →";
      sheet.appendChild(a);
    }
    sheet.querySelector(".rc-tips-x").addEventListener("click", closeSheet);
    lastFocus = el;
    backdrop.hidden = false;
    sheet.hidden = false;
    sheet.querySelector(".rc-tips-x").focus({ preventScroll: true });
    openFor = el;
  }

  if (noHover) {
    // capture, so a badge inside a link or a sortable header opens the sheet
    // instead of following the link or sorting
    document.addEventListener("click", function (e) {
      var el = badgeOf(e.target);
      if (!el) return;
      e.preventDefault();
      e.stopPropagation();
      if (openFor === el) { closeSheet(); return; }
      showSheet(el);
    }, true);
  } else {
    document.addEventListener("mouseover", function (e) {
      var el = badgeOf(e.target);
      if (el && el !== openFor) showFloat(el);
      else if (!el && openFor) hideFloat();
    });
    document.addEventListener("focusin", function (e) {
      var el = badgeOf(e.target);
      if (el) showFloat(el); else if (openFor) hideFloat();
    });
    window.addEventListener("scroll", function () { if (openFor) hideFloat(); }, { passive: true });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || !openFor) return;
    if (sheet && !sheet.hidden) closeSheet(); else hideFloat();
  });
})();
