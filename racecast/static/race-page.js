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
  function show(name) {
    var found = false;
    panels.forEach(function (p) { if (p.dataset.panel === name) found = true; });
    if (!found) name = "results";
    panels.forEach(function (p) { p.hidden = p.dataset.panel !== name; });
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
  show((location.hash || "#results").slice(1));

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
    //   share); the reader sees the school.
    if (tipName) tipName.textContent = (key || "").split("\u0000")[0];
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
        var c = el("circle", { class: "d" + (p.lit ? " lit" : ""), cx: x(+k).toFixed(1),
                               cy: (H - AX - r - 1 - i * 2 * r).toFixed(1), r: r.toFixed(2) });
        var tt = el("title", {});
        tt.textContent = p.label;
        c.appendChild(tt);
        c.addEventListener("click", function () {
          show("results");
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
  var first = me && me.dataset.light;
  if (first) {
    var match = null;
    teamRows.forEach(function (t) { if (!match && t.dataset.team === first) match = t; });
    light(first, match ? match.dataset.rids : "");
  } else {
    drawField();
  }

  // ---- finder: hide rows whose name or school does not contain the text
  var find = main.querySelector(".rc-find");
  if (find) {
    find.addEventListener("input", function () {
      var q = find.value.trim().toLowerCase();
      if (q) show("results");
      rows.forEach(function (tr) {
        tr.hidden = !!q && tr.textContent.toLowerCase().indexOf(q) < 0;
      });
    });
  }
})();
