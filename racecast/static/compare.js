/*
 * compare.js -- the head-to-head page: two athlete pickers and the
 * two-career rating chart.
 *
 * The pickers ride /search/api?kind=athlete, the same endpoint every other
 * picker uses; person_id is recovered from the row's /athlete/<id> link,
 * the same trick conversions.js already relies on.
 *
 * The chart itself lives in athlete-charts.js (drawCompareChart), so
 * /compare and the athlete page draw from one set of chart code and
 * cannot drift apart in look. This file only wires the pickers.
 */

"use strict";

(function () {

  /* ---------------- pickers ---------------- */

  function wirePicker(inputId, sugId) {
    var input = document.getElementById(inputId);
    var sug = document.getElementById(sugId);
    if (!input || !sug) return;
    var timer = null;

    /* ⚠ A LATE RESPONSE MUST NOT RE-OPEN A BOX THE READER HAS LEFT. blur
         hid the list after 150ms, the debounce fired at 200ms, and the fetch
         landed later still and showed it again -- over whatever they had
         moved on to, with no focus left to blur a second time. Same guard as
         predictions.js bindPicker: `seq` for a superseded keystroke,
         `closed` for a box nothing is waiting on. */
    var seq = 0;
    var closed = false;

    input.addEventListener("focus", function () { closed = false; });
    input.addEventListener("input", function () {
      input.dataset.pid = "";                    // typing invalidates the pick
      closed = false;
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { sug.classList.add("hidden"); return; }
      var mine = ++seq;
      timer = setTimeout(function () {
        fetch("/search/api?kind=athlete&q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (rows) {
            if (mine !== seq || closed) return;
            sug.innerHTML = "";
            rows.slice(0, 8).forEach(function (r) {
              var m = /\/athlete\/(\d+)/.exec(r.link || "");
              if (!m) return;
              var d = document.createElement("div");
              d.className = "h2h-sug-row";
              d.textContent = r.label + (r.sublabel ? " · " + r.sublabel : "");
              d.dataset.pid = m[1];
              d.dataset.name = r.label;
              sug.appendChild(d);
            });
            sug.classList.toggle("hidden", !sug.children.length);
          });
      }, 200);
    });

    sug.addEventListener("mousedown", function (e) {
      var row = e.target.closest(".h2h-sug-row");
      if (!row) return;
      input.value = row.dataset.name;
      input.dataset.pid = row.dataset.pid;
      closed = true;
      clearTimeout(timer);
      sug.classList.add("hidden");
    });

    input.addEventListener("blur", function () {
      closed = true;                 // at once, so an in-flight fetch is dropped
      clearTimeout(timer);
      setTimeout(function () { sug.classList.add("hidden"); }, 150);
    });
  }

  wirePicker("h2h-a", "h2h-a-sug");
  wirePicker("h2h-b", "h2h-b-sug");

  var go = document.getElementById("h2h-go");
  if (go) {
    go.addEventListener("click", function () {
      var a = document.getElementById("h2h-a").dataset.pid;
      var b = document.getElementById("h2h-b").dataset.pid;
      if (a && b) window.location = "/compare?a=" + a + "&b=" + b;
    });
  }
})();
