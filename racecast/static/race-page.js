/* race-page.js -- the XC race page's tabs, team lighting and finder.

   ★ NOTHING HERE IS NEEDED TO READ THE PAGE. Without this script every
     panel shows, one under another (race.css hides panels only under
     .rc-js), the team rows are plain rows and the finder box is inert. */
(function () {
  var main = document.querySelector("main.rc");
  if (!main) return;
  main.classList.add("rc-js");

  // ---- tabs: #results / #teams / #track, kept in the URL hash
  // ★ ONE SET OF TABS PER ROOT (owner, 2026-10-10: the predictions page lays
  //   its predicted race out as a race page, and that markup arrives by
  //   fetch, long after this script ran). wireTabs(root) wires the tabs and
  //   panels inside one root; the page's own <main> is the first root, and
  //   window.rcTabs(root) wires one drawn later. A root other than <main>
  //   carries its own rc-on-teams / rc-on-track, so two predicted races on
  //   one page do not fold each other's side cards.
  var roots = [];
  function wireTabs(root) {
    var tabs = root.querySelectorAll("[data-tab]");
    var panels = root.querySelectorAll("[data-panel]");
    // ! THE FIRST TAB IS THE DEFAULT, not "results": the meet page's first
    //   tab is "races", and an unknown name used to hide every panel. The
    //   tab bar's order, not the panels' order in the source, says which.
    var firstTab = root.querySelector(".rc-tabs [data-tab]");
    var first = firstTab ? firstTab.dataset.tab
              : (panels.length ? panels[0].dataset.panel : "results");
    function has(name) {
      var found = false;
      panels.forEach(function (p) { if (p.dataset.panel === name) found = true; });
      return found;
    }
    function show(name) {
      if (!has(name)) name = first;
      panels.forEach(function (p) { p.hidden = p.dataset.panel !== name; });
      // ! THE FULL TEAMS TABLE AND THE SIDEBAR'S TOP TEN ARE ONE LIST TWICE
      //   (owner, 2026-10-07, on "All teams"): with the Teams tab open the
      //   sidebar steps aside and the table takes the width
      root.classList.toggle("rc-on-teams", name === "teams");
      // the conversion tab swaps the sidebar for the finishers (fillConv)
      root.classList.toggle("rc-on-track", name === "track");
      if (name === "track" && root === main && typeof fillConv === "function") fillConv();
      root.querySelectorAll(".rc-tabs [data-tab]").forEach(function (a) {
        a.classList.toggle("is-on", a.dataset.tab === name);   // the site's .seg-btn.is-on
      });
    }
    tabs.forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        show(a.dataset.tab);
        history.replaceState(null, "", "#" + a.dataset.tab);
        if (a.classList.contains("rc-more")) {
          root.querySelector(".rc-tabs").scrollIntoView({ block: "start", behavior: "smooth" });
        }
      });
    });
    var entry = { root: root, panels: panels, show: show, first: first };
    roots.push(entry);
    // ! A TAB THAT IS NOT OFFERED YET IS NOT OPENED (the conversions page's
    //   Paces tab stays hidden until there are paces): #paces on arrival
    //   would open an empty panel
    //   A #tab names its tab; otherwise the panel holding the linked row or
    //   season (#race-<id>, ?r=<id>, data-flash-id), else the first tab.
    var want = location.hash.slice(1);
    // ! AN OLD ANCHOR CAN NAME A TAB (owner, 2026-10-10): /recruit/<id>#like
    //   predates the tabs; an element with that id and data-opens="<tab>"
    //   opens that tab instead of falling back to the first.
    var alias = want && !has(want) && document.getElementById(want);
    if (alias && alias.dataset.opens && !alias.dataset.panel && has(alias.dataset.opens))
      want = alias.dataset.opens;
    var offered = want && root.querySelector('.rc-tabs [data-tab="' + want + '"]');
    show(offered && offered.hidden ? first
         : (want && has(want) ? want : (panelOf(target()) || first)));
    return entry;
  }

  // ★ A LINK TO A ROW OPENS THE TAB THAT HOLDS IT (owner, 2026-10-10, the
  //   athlete page in the race shape: each sport's seasons sit in their own
  //   tab, so #race-<id>, #2025-TF, ?r=<id> and the route's data-flash-id
  //   pointed into a hidden panel). The panel holding the target opens
  //   first; link-flash.js then centres and flashes the row as before.
  //   Same order of lookup as link-flash.js's target().
  function target() {
    var named = document.querySelector("[data-flash-id]");
    var el = named && document.getElementById(named.getAttribute("data-flash-id"));
    if (el) return el;
    var h = location.hash.slice(1);
    if (h) {
      try { el = document.getElementById(decodeURIComponent(h)); } catch (e) { el = null; }
      if (el) return el;
    }
    var m = /[?&]r=(-?\d+)/.exec(location.search);
    return m ? (document.getElementById("r" + m[1]) ||
                document.getElementById("race-" + m[1])) : null;
  }
  // the name of the panel (of a wired root) that holds el, or ""
  function panelOf(el) {
    var p = el && el.closest && el.closest("[data-panel]");
    while (p) {
      for (var i = 0; i < roots.length; i++) {
        if (Array.prototype.indexOf.call(roots[i].panels, p) >= 0) return p.dataset.panel;
      }
      p = p.parentElement && p.parentElement.closest("[data-panel]");
    }
    return "";
  }
  // open every hidden panel on the way to el; true when one opened
  function reveal(el) {
    var opened = false;
    var p = el && el.closest && el.closest("[data-panel]");
    while (p) {
      for (var i = 0; i < roots.length; i++) {
        if (p.hidden && Array.prototype.indexOf.call(roots[i].panels, p) >= 0) {
          roots[i].show(p.dataset.panel);
          opened = true;
        }
      }
      p = p.parentElement && p.parentElement.closest("[data-panel]");
    }
    return opened;
  }
  // ! CAPTURE PHASE: link-flash.js's own click handler (loaded earlier,
  //   from _topbar.html) measures and scrolls to the row, so the panel has
  //   to be open before it runs
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || a.hasAttribute("data-tab") || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    var id = a.getAttribute("href").slice(1);
    var el = null;
    try { el = id && document.getElementById(decodeURIComponent(id)); } catch (err) { el = null; }
    if (el) reveal(el);
  }, true);
  // back/forward to a row or a tab: capture on window, so it runs before
  // link-flash.js's hashchange handler
  window.addEventListener("hashchange", function () {
    var h = location.hash.slice(1);
    var el = null;
    try { el = h && document.getElementById(decodeURIComponent(h)); } catch (err) { el = null; }
    // an old anchor naming a tab (data-opens, as on arrival)
    if (el && el.dataset.opens && !el.hasAttribute("data-panel")) {
      var tab = document.getElementById(el.dataset.opens);
      if (tab && tab.hasAttribute("data-panel")) el = tab;
    }
    if (el && el.hasAttribute("data-panel")) {
      roots.forEach(function (r) {
        if (Array.prototype.indexOf.call(r.panels, el) >= 0) r.show(el.dataset.panel);
      });
    } else if (el && reveal(el) && !el.matches("tr, li")) {
      el.scrollIntoView({ block: "start" });
    }
  }, true);

  var mainTabs = wireTabs(main);
  var first = mainTabs.first;
  var show = mainTabs.show;
  window.rcTabs = function (root) { return wireTabs(root || main).show; };

  // ---- "All N teams" extends the sidebar's list in place, and folds it back
  var moreTeams = main.querySelector(".rc-more-teams");
  if (moreTeams) {
    moreTeams.addEventListener("click", function () {
      var extra = main.querySelectorAll(".rc-tscores .rc-team-more");
      var open = moreTeams.getAttribute("aria-expanded") === "true";
      extra.forEach(function (tr) { tr.hidden = open; });
      moreTeams.setAttribute("aria-expanded", open ? "false" : "true");
      moreTeams.textContent = open ? moreTeams.dataset.all : "Top 10 only";
    });
  }

  // ---- team lighting: a team's runners lit in the results
  // ★ BY RESULT ID AND BY NAME. The scorers' ids are exact (the grafted
  //   seven); the school string catches the rest of the squad. A school
  //   string that names two schools (a collision split) lights only the
  //   ids, because the name would light both.
  var rows = main.querySelectorAll(".rc-table tbody tr");
  var teamRows = main.querySelectorAll(".rc-tscores tr[data-team]");
  var tipName = main.querySelector(".rc-tip-name");
  var tipLit = main.querySelector(".rc-tip-lit");
  function light(key, rids) {
    var ids = {};
    (rids || "").split(/\s+/).forEach(function (r) { if (r) ids["r" + r] = true; });
    var dup = 0;
    teamRows.forEach(function (t) { if (t.dataset.team === key) dup++; });
    rows.forEach(function (tr) {
      var on = !!key && (ids[tr.id] || (dup <= 1 && tr.dataset.team === key));
      tr.classList.toggle("rc-lit", on);
    });
    teamRows.forEach(function (t) {
      t.classList.toggle("rc-lit", t.dataset.team === key && (!rids || t.dataset.rids === rids));
    });
    // ! A team key can be "school\u0000state" (a name schools in two states
    //   share); the reader sees the school. ⚠ AND THE BROWSER HAS ALREADY
    //   TURNED THE NUL INTO U+FFFD by the time the attribute is read (the
    //   HTML parser replaces it), so splitting on \u0000 alone never fired:
    //   "Williams\uFFFDMA's runners are lit" (owner, 2026-10-07).
    if (tipName) tipName.textContent = (key || "").split(/[\u0000\uFFFD]/)[0];
    if (tipLit) tipLit.hidden = !key;
    drawField();
  }
  // ---- the field on one scale: every rated finisher, one dot each
  // ★ OWNER, 2026-10-07: "I kind of like the field on one scale". Dots are
  //   stacked per whole rating point -- the unit the ratings are read in --
  //   so the height of a column is how many ran that rating. The lit team's
  //   runners are dark; a dot names its runner on hover and jumps to the row.
  var field = main.querySelector(".rc-field");
  var fieldPlot = field && field.querySelector(".rc-field-plot");
  var fieldKey = field && field.querySelector(".rc-field-key");
  var SVGNS = "http://www.w3.org/2000/svg";
  function rated() {
    var out = [];
    rows.forEach(function (tr) {
      var rv = tr.querySelector(".rc-rv .rv");
      if (!rv) return;
      var own = rv.dataset.own !== undefined ? rv.dataset.own : rv.textContent;
      var hs = rv.dataset.hs;
      var mode = window.rcScale ? window.rcScale.mode : "pool";
      var v = parseFloat(mode === "hs" && hs ? hs : own);
      if (isNaN(v)) return;
      var nm = tr.querySelector("td.nm a, td.nm");
      out.push({ tr: tr, v: v, lit: tr.classList.contains("rc-lit"),
                 label: tr.querySelector("td.pl").textContent.trim() + ". " +
                        (nm ? nm.childNodes[0].textContent.trim() : "") +
                        (tr.dataset.team ? ", " + tr.dataset.team : "") + " \u2014 " + v.toFixed(1) });
    });
    return out;
  }
  function el(name, attrs) {
    var e = document.createElementNS(SVGNS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function drawField() {
    if (!fieldPlot) return;
    var pts = rated();
    if (pts.length < 2) { field.hidden = true; return; }
    field.hidden = false;
    var W = Math.max(260, fieldPlot.clientWidth || 600);
    var AX = 18, PADX = 10, H;
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p.v); hi = Math.max(hi, p.v); });
    lo = Math.floor(lo) - 1; hi = Math.ceil(hi) + 1;
    var step = (W - 2 * PADX) / (hi - lo);
    var x = function (v) { return PADX + (v - lo) * step; };
    var bins = {};
    pts.sort(function (a, b) { return (a.lit - b.lit) || (a.v - b.v); });
    pts.forEach(function (p) { var k = Math.round(p.v); (bins[k] = bins[k] || []).push(p); });
    var tall = 0;
    for (var k in bins) tall = Math.max(tall, bins[k].length);
    // As tall as the deepest column needs at a readable dot (4px, or what a
    // whole point of width allows), between a short strip and 160px.
    var want = Math.min(step * 0.45, 4);
    H = Math.max(96, Math.min(160, Math.ceil(2 * want * tall + AX + 4)));
    var r = Math.max(1.2, Math.min(want, (H - AX - 4) / (2 * tall)));
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
                          "aria-label": "Every finisher by rating" });
    var tickEvery = step * 5 >= 28 ? 5 : 10;
    for (var t = Math.ceil(lo / tickEvery) * tickEvery; t <= hi; t += tickEvery) {
      svg.appendChild(el("line", { class: "tk", x1: x(t), x2: x(t), y1: H - AX, y2: H - AX + 4 }));
      var tx = el("text", { class: "tl", x: x(t), y: H - 3, "text-anchor": "middle" });
      tx.textContent = t;
      svg.appendChild(tx);
    }
    svg.appendChild(el("line", { class: "ax", x1: PADX, x2: W - PADX, y1: H - AX, y2: H - AX }));
    Object.keys(bins).forEach(function (k) {
      bins[k].forEach(function (p, i) {
        // the dot wears its rating tier's colour; the lit
        // team's are ink, so a team reads against the heat
        // tiers of 10 points (10% faster than the pool average, the scale's
        // own unit) from 100 (average) to 140 (national class); below 100
        // stays grey. One orange ramp, validated as ordinal (dataviz).
        var tierCls = p.v >= 100 ? " t" + Math.min(5, Math.floor((p.v - 100) / 10) + 1) : "";
        var c = el("circle", { class: "d" + tierCls + (p.lit ? " lit" : ""), cx: x(+k).toFixed(1),
                               cy: (H - AX - r - 1 - i * 2 * r).toFixed(1), r: r.toFixed(2) });
        var tt = el("title", {});
        tt.textContent = p.label;
        c.appendChild(tt);
        c.addEventListener("click", function () {
          show(first);
          p.tr.hidden = false;
          p.tr.scrollIntoView({ block: "center", behavior: "smooth" });
          p.tr.classList.remove("rc-flash"); void p.tr.offsetWidth; p.tr.classList.add("rc-flash");
        });
        svg.appendChild(c);
      });
    });
    fieldPlot.innerHTML = "";
    fieldPlot.appendChild(svg);
    var litName = tipName ? tipName.textContent : "";
    if (fieldKey) fieldKey.innerHTML = pts.some(function (p) { return p.lit; }) && litName
      ? '<i class="lit"></i>' + litName.replace(/[&<>]/g, "") : "";
  }
  if (field) {
    document.addEventListener("rc-scale-change", function () { requestAnimationFrame(drawField); });
    if (typeof ResizeObserver !== "undefined") {
      var pend = false, lastW = 0;
      new ResizeObserver(function () {
        var w = fieldPlot.clientWidth;
        if (pend || w === lastW) return;
        pend = true; lastW = w;
        requestAnimationFrame(function () { pend = false; drawField(); });
      }).observe(fieldPlot);
    }
  }

  // ---- a team's race in a popup: its runners, their points, the score
  // ★ OWNER, 2026-10-07: "a pop up that has the teams results ... in that
  //   race, where you can then click to the team". Read off the results
  //   table -- the same rows the team lights -- so the two cannot disagree.
  //   Where a team has no popup (the track page) a tap only lights.
  var pop = main.querySelector(".rc-teampop");
  function teamRowsOf(key, rids) {
    var ids = {}, dup = 0;
    (rids || "").split(/\s+/).forEach(function (r) { if (r) ids["r" + r] = true; });
    teamRows.forEach(function (t) { if (t.dataset.team === key) dup++; });
    return Array.prototype.filter.call(rows, function (tr) {
      return ids[tr.id] || (dup <= 1 && tr.dataset.team === key);
    });
  }
  function cellText(tr, sel) {
    var c = tr.querySelector(sel);
    return c ? c.textContent.replace(/\s+/g, " ").trim() : "";
  }
  function openTeam(t) {
    if (!pop || typeof pop.showModal !== "function") return false;
    var label = t.dataset.label || (t.dataset.team || "").split(/[\u0000\uFFFD]/)[0];
    pop.querySelector(".rc-pop-h").textContent =
      (t.dataset.pl ? t.dataset.pl + ". " : "") + label +
      (t.dataset.pts ? " \u00b7 " + t.dataset.pts + " pts" : "");
    var body = pop.querySelector(".rc-pop-t tbody");
    body.innerHTML = "";
    teamRowsOf(t.dataset.team, t.dataset.rids).forEach(function (tr) {
      var out = document.createElement("tr");
      var a = tr.querySelector("td.nm > a");
      var nm = document.createElement("td");
      if (a) {
        var link = document.createElement("a");
        link.href = a.getAttribute("href");
        link.textContent = a.textContent.trim();
        nm.appendChild(link);
      } else {
        nm.textContent = (tr.querySelector("td.nm").firstChild.textContent || "").trim();
      }
      var pts = cellText(tr, "td.pts");
      [cellText(tr, "td.pl"), nm, cellText(tr, "td.tm"), cellText(tr, "td.rt .rc-rv"),
       pts === "-" ? "" : pts].forEach(function (v, i) {
        var td = v instanceof Node ? v : document.createElement("td");
        if (!(v instanceof Node)) td.textContent = v;
        if (i >= 2) td.className = "n";
        out.appendChild(td);
      });
      body.appendChild(out);
    });
    var go = pop.querySelector(".rc-pop-go");
    go.hidden = !t.dataset.href;
    if (t.dataset.href) go.href = t.dataset.href;
    pop.showModal();
    return true;
  }
  if (pop) {
    pop.addEventListener("click", function (e) { if (e.target === pop) pop.close(); });
    main.querySelectorAll(".rc-teams-full tr[data-team]").forEach(function (t) {
      var a = t.querySelector(".rc-team-open");
      if (!a) return;
      a.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;   // new tab: the page
        if (openTeam(t)) e.preventDefault();
      });
    });
  }

  // ★ THE ROW LIGHTS, THE NAME OPENS (owner, 2026-10-08: "the actual name
  //   should prolly be linked not the entire box, so you can highlight
  //   without pressing team results"). A tap on the row toggles the team's
  //   runners lit, as before; a tap on the name lights them and opens the
  //   popup.
  teamRows.forEach(function (t) {
    function go() {
      var on = t.classList.contains("rc-lit");
      light(on ? "" : t.dataset.team, on ? "" : t.dataset.rids);
    }
    var nameBtn = t.querySelector(".rc-team-name");
    if (nameBtn && pop) {
      nameBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        light(t.dataset.team, t.dataset.rids);
        openTeam(t);
      });
    }
    t.addEventListener("click", go);
    t.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
    });
  });
  var me = document.currentScript;
  var lightFirst = me && me.dataset.light;
  if (lightFirst) {
    var match = null;
    teamRows.forEach(function (t) { if (!match && t.dataset.team === lightFirst) match = t; });
    light(lightFirst, match ? match.dataset.rids : "");
  } else {
    drawField();
  }

  // ---- finder: hide rows whose name or school does not contain the text
  var find = main.querySelector(".rc-find");
  var list = main.querySelector(".rc-findlist");
  var pending = null, asked = "", raceHit = false;
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  // ★ THE MEET PAGE ASKS THE SERVER (data-api): every finisher at the meet
  //   whose name or school matches, each a link to their row in their race.
  function askServer(q) {
    if (!list) return;
    if (q.length < 2) { list.hidden = true; list.innerHTML = ""; return; }
    clearTimeout(pending);
    pending = setTimeout(function () {
      asked = q;
      var url = find.dataset.api + (find.dataset.api.indexOf("?") < 0 ? "?" : "&") +
                "q=" + encodeURIComponent(q);
      fetch(url).then(function (r) { return r.ok ? r.json() : []; }).then(function (got) {
        if (asked !== q) return;                       // a newer query is out
        if (!got.length && raceHit) { list.hidden = true; return; }   // the table answered
        list.innerHTML = got.length
          ? got.map(function (m) {
              return '<li><a href="' + esc(m.href) + '"><b>' + esc(m.name) + "</b>" +
                     '<span>' + esc([m.school, m.race, m.time].filter(Boolean).join(" \u00b7 ")) +
                     "</span></a></li>"; }).join("")
          : '<li class="none">No runner or school by that name ran here.</li>';
        list.hidden = false;
      }).catch(function () { list.hidden = true; });
    }, 180);
  }
  if (find) {
    find.addEventListener("input", function () {
      var q = find.value.trim().toLowerCase();
      if (q) show(first);
      // with a server behind the box, the table filters on the RACE name
      // only: a runner's name belongs in the list, not in hiding races
      function hit(tr) {
        var cell = find.dataset.api ? (tr.querySelector("td.nm") || tr) : tr;
        return cell.textContent.toLowerCase().indexOf(q) >= 0;
      }
      // ! a name or school that is no race's name leaves the table whole:
      //   emptying it under a list of runners read as "nothing found"
      var anyHit = !q || Array.prototype.some.call(rows, hit);
      raceHit = !!q && anyHit;
      rows.forEach(function (tr) {
        tr.hidden = !!q && (anyHit || !find.dataset.api) && !hit(tr);
      });
      if (find.dataset.api) askServer(find.value.trim());
    });
    find.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { find.value = ""; find.dispatchEvent(new Event("input")); }
    });
  }

  // ---- the conversion tab's sidebar: every timed finisher, tap to convert
  // ★ OWNER, 2026-10-08: "it should show indiv results in the sidebar so you
  //   can find your result so you can convert it". Built once, the first
  //   time the tab opens, from the results table's timed rows (td.tm
  //   data-t); a tap types that time into the line's box and commits it,
  //   exactly as typing it would.
  var conv = main.querySelector(".rc-conv");
  var convBuilt = false;
  function fillConv() {
    if (!conv || convBuilt) return;
    convBuilt = true;
    var ol = conv.querySelector(".rc-conv-list");
    var n = 0;
    rows.forEach(function (tr) {
      var tm = tr.querySelector("td.tm[data-t]");
      if (!tm) return;
      var nmCell = tr.querySelector("td.nm");
      var a = nmCell && nmCell.querySelector("a");
      var name = a ? a.textContent.trim()
               : (nmCell && nmCell.firstChild ? nmCell.firstChild.textContent.trim() : "");
      var sub = nmCell && nmCell.querySelector(".rc-subline");
      var school = sub ? sub.textContent.replace(/\s+/g, " ").trim() : "";
      var shown = (tm.firstChild ? tm.firstChild.textContent : tm.textContent).trim();
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.time = shown;
      b.innerHTML = '<span class="p"></span><span class="w"><b></b><small></small></span><i></i>';
      b.querySelector(".p").textContent = cellText(tr, "td.pl");
      b.querySelector("b").textContent = name || "Unknown";
      b.querySelector("small").textContent = school;
      b.querySelector("i").textContent = shown;
      li.appendChild(b);
      ol.appendChild(li);
      n++;
    });
    if (!n) { conv.hidden = true; return; }
    ol.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      var input = main.querySelector("#track .eq-time");
      if (!input) return;
      ol.querySelectorAll("button.is-on").forEach(function (x) { x.classList.remove("is-on"); });
      b.classList.add("is-on");
      input.value = b.dataset.time;
      input.dispatchEvent(new Event("change"));
      // on a phone the list sits under the line: bring the line back
      if (window.matchMedia("(max-width: 900px)").matches) {
        main.querySelector("#track").scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
    var cf = conv.querySelector(".rc-conv-find");
    cf.addEventListener("input", function () {
      var q = cf.value.trim().toLowerCase();
      ol.querySelectorAll("li").forEach(function (li) {
        li.hidden = !!q && li.textContent.toLowerCase().indexOf(q) < 0;
      });
    });
  }
  if (main.classList.contains("rc-on-track")) fillConv();

  // ---- arriving at #r<result id> (from the meet page's finder): that row
  if (/^#r-?\d+$/.test(location.hash)) {           // -? : tfrrs ids are negative
    var target = document.getElementById(location.hash.slice(1));
    if (target) {
      target.scrollIntoView({ block: "center" });
      target.classList.add("rc-flash");
    }
  }
})();
