/* race-page.js -- the XC race page's tabs, team lighting and finder.

   ★ NOTHING HERE IS NEEDED TO READ THE PAGE. Without this script every
     panel shows, one under another (race.css hides panels only under
     .rc-js), the team rows are plain rows and the finder box is inert. */
(function () {
  var main = document.querySelector("main.rc");
  if (!main) return;
  main.classList.add("rc-js");

  // ---- tabs: #results / #teams / #track, kept in the URL hash
  var tabs = main.querySelectorAll("[data-tab]");
  var panels = main.querySelectorAll("[data-panel]");
  // ! THE FIRST TAB IS THE DEFAULT, not "results": the meet page's first
  //   tab is "races", and an unknown name used to hide every panel. The
  //   tab bar's order, not the panels' order in the source, says which.
  var firstTab = main.querySelector(".rc-tabs [data-tab]");
  var first = firstTab ? firstTab.dataset.tab
            : (panels.length ? panels[0].dataset.panel : "results");
  function show(name) {
    var found = false;
    panels.forEach(function (p) { if (p.dataset.panel === name) found = true; });
    if (!found) name = first;
    panels.forEach(function (p) { p.hidden = p.dataset.panel !== name; });
    // ! THE FULL TEAMS TABLE AND THE SIDEBAR'S TOP TEN ARE ONE LIST TWICE
    //   (owner, 2026-10-07, on "All teams"): with the Teams tab open the
    //   sidebar steps aside and the table takes the width
    main.classList.toggle("rc-on-teams", name === "teams");
    main.querySelectorAll(".rc-tabs [data-tab]").forEach(function (a) {
      a.classList.toggle("is-on", a.dataset.tab === name);   // the site's .seg-btn.is-on
    });
  }
  tabs.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      show(a.dataset.tab);
      history.replaceState(null, "", "#" + a.dataset.tab);
      if (a.classList.contains("rc-more")) {
        main.querySelector(".rc-tabs").scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
  });
  show((location.hash || "#" + first).slice(1));

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

  teamRows.forEach(function (t) {
    function go() {
      var on = t.classList.contains("rc-lit");
      light(on ? "" : t.dataset.team, on ? "" : t.dataset.rids);
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

  // ---- arriving at #r<result id> (from the meet page's finder): that row
  if (/^#r\d+$/.test(location.hash)) {
    var target = document.getElementById(location.hash.slice(1));
    if (target) {
      target.scrollIntoView({ block: "center" });
      target.classList.add("rc-flash");
    }
  }
})();
