/*
 * goal.js -- /goal's form (goal.py, owner 2026-10-10, item 4). The page is
 * server-rendered from its query string; this only
 *   - shows the fields the chosen goal uses (state and division for a state
 *     goal, the college for a recruit goal),
 *   - re-asks when the state changes, so the division list is that state's,
 *   - runs the course picker: /api/course_search, the conversions page's
 *     own source, whose answer names each course's rated distances. The pick
 *     is the course's canonical id (cid), which the server resolves again.
 */
"use strict";

(function () {
  var form = document.getElementById("goal-form");
  if (!form) return;
  var $ = function (id) { return document.getElementById(id); };
  var goal = $("goal-goal"), state = $("goal-state");
  var course = $("goal-course"), cid = $("goal-cid"), sug = $("goal-course-sug");
  var dist = $("goal-dist");

  function sync() {
    var recruit = goal.value === "recruit";
    form.querySelectorAll(".goal-state").forEach(function (el) { el.hidden = recruit; });
    form.querySelectorAll(".goal-college").forEach(function (el) { el.hidden = !recruit; });
  }
  goal.addEventListener("change", sync);
  sync();

  // a new state is a new list of divisions: ask the server for it
  state.addEventListener("change", function () {
    var d = $("goal-div");
    if (d) d.value = "";
    form.submit();
  });

  function label(m) { return m % 1000 === 0 ? m / 1000 + "K" : m.toLocaleString() + " m"; }

  var timer = null, seq = 0, closed = false;
  course.addEventListener("focus", function () { closed = false; });
  course.addEventListener("input", function () {
    cid.value = "";                               // typing drops the pick
    closed = false;
    clearTimeout(timer);
    var q = course.value.trim();
    if (q.length < 2) { sug.classList.add("hidden"); return; }
    var mine = ++seq;
    timer = setTimeout(function () {
      fetch("/api/course_search?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (rows) {
          if (mine !== seq || closed) return;
          sug.innerHTML = "";
          sug._rows = rows || [];
          sug._rows.forEach(function (r, i) {
            var d = document.createElement("div");
            d.className = "h2h-sug-row";
            d.textContent = r.name;
            d.dataset.i = i;
            sug.appendChild(d);
          });
          sug.classList.toggle("hidden", !sug.children.length);
        })
        .catch(function () { sug.classList.add("hidden"); });
    }, 200);
  });
  sug.addEventListener("mousedown", function (e) {
    var row = e.target.closest(".h2h-sug-row");
    if (!row) return;
    var c = (sug._rows || [])[Number(row.dataset.i)];
    if (!c) return;
    course.value = c.name;
    cid.value = c.canonical_id;
    closed = true;
    sug.classList.add("hidden");
    // the course's own rated distances, the one nearest the current pick lit
    var have = (c.distances || []).map(function (d) { return Math.round(d.distance); });
    if (have.length) {
      var cur = Number(dist.value) || have[0];
      var best = have.reduce(function (a, b) { return Math.abs(b - cur) < Math.abs(a - cur) ? b : a; });
      have = have.filter(function (v, i) { return have.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
      dist.innerHTML = have.map(function (m) {
        return '<option value="' + m + '"' + (m === best ? " selected" : "") + ">" + label(m) + "</option>";
      }).join("");
    }
  });
  course.addEventListener("blur", function () {
    closed = true;
    clearTimeout(timer);
    setTimeout(function () { sug.classList.add("hidden"); }, 150);
  });
})();
