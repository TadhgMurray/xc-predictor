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
      a.classList.toggle("on", a.dataset.tab === name);
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
    if (tipName) tipName.textContent = key || "";
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
