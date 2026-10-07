/* theme-rc.js -- the dark header band on every page, for the ?theme=rc
   preview (owner, 2026-10-07: "feels half-assed ... only some things were put
   into new theme"). Loaded by _topbar.html only when the theme is on.

   ★ A WHITELIST, NOT A GUESS. The band takes the page's title row (the h1, or
     the .hdr-row holding it with its Share button) and then only the siblings
     that follow it AND are header pieces by class: the meta lines, the lead,
     the athlete's stat strip and rank lines, a paragraph of meet-link
     buttons. The first sibling that is anything else ends the band. It moves
     to the top of the page's own container, so tabs that sat above the title
     (the rankings board strip, the predictions switch) now sit under it.
   ! IF THE THEME IS APPROVED this becomes template markup and this file
     goes: a preview may move nodes after load; the real page should not. */
(function () {
  var de = document.documentElement;
  if (!de.classList.contains("rc-theme")) return;
  var HEADISH = "p.meta, p.sub, .page-lead, .p2-lead, p.rc-sub, .conv-intro, " +
                ".stat-strip, p.rank-line, p.paces-link, .unit-chips";

  function isActions(el) {
    // <p><a class="meet-link">…</a> <a class="meet-link">…</a></p>
    if (el.tagName !== "P") return false;
    var links = el.querySelectorAll("a.meet-link");
    if (!links.length) return false;
    var txt = el.textContent.replace(/\s+/g, "");
    var own = Array.prototype.map.call(links, function (a) { return a.textContent; }).join("").replace(/\s+/g, "");
    return txt === own;
  }

  function build() {
    if (document.querySelector("main.rc, .rc-band")) return;     // the race page has its own
    var h1 = document.querySelector("body h1");
    if (!h1 || h1.closest(".topbar")) return;
    var start = (h1.parentElement && h1.parentElement.classList.contains("hdr-row"))
                ? h1.parentElement : h1;
    var host = start.parentElement;
    if (!host) return;
    var anchor = host === document.body ? start : host.firstChild;   // the athlete page's header is the body's own child
    var parts = [start], n = start.nextElementSibling;
    while (n && (n.matches(HEADISH) || isActions(n))) { parts.push(n); n = n.nextElementSibling; }

    var band = document.createElement("div");
    band.className = "rc-band";
    var rule = document.createElement("div");
    rule.className = "rc-band-rule";
    host.insertBefore(band, anchor);
    host.insertBefore(rule, band.nextSibling);
    parts.forEach(function (p) { band.appendChild(p); });
    if (host === document.body) band.classList.add("rc-band-body");
    de.classList.add("rc-has-band");

    // up against the black bar, whatever padding the container starts with
    function seat() {
      band.style.marginTop = "0px";
      var bar = document.querySelector(".topbar");
      if (!bar) return;
      var gap = band.getBoundingClientRect().top - bar.getBoundingClientRect().bottom;
      band.style.marginTop = (-gap) + "px";
    }
    seat();
    window.addEventListener("resize", seat);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
