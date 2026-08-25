/*
 * compare.js -- the head-to-head page: two athlete pickers and the
 * two-career rating chart.
 *
 * The pickers ride /search/api?kind=athlete, the same endpoint every other
 * picker uses; person_id is recovered from the row's /athlete/<id> link,
 * the same trick conversions.js already relies on.
 *
 * The chart listens for rc-scale-change and swaps each point to its
 * hs_rating when the HS-equivalent view is on -- same contract as
 * athlete-charts.js, without that file's single-athlete machinery.
 */

"use strict";

(function () {

  /* ---------------- pickers ---------------- */

  function wirePicker(inputId, sugId) {
    var input = document.getElementById(inputId);
    var sug = document.getElementById(sugId);
    if (!input || !sug) return;
    var timer = null;

    input.addEventListener("input", function () {
      input.dataset.pid = "";                    // typing invalidates the pick
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { sug.classList.add("hidden"); return; }
      timer = setTimeout(function () {
        fetch("/search/api?kind=athlete&q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (rows) {
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
      sug.classList.add("hidden");
    });

    input.addEventListener("blur", function () {
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

  /* ---------------- the chart ---------------- */

  var dataEl = document.getElementById("h2h-data");
  var svg = document.getElementById("h2h-svg");
  if (!dataEl || !svg) return;
  var data = JSON.parse(dataEl.textContent);

  function hsOn() {
    return Boolean(window.rcScale && window.rcScale.mode === "hs");
  }

  function val(p) {
    /* HS view when on AND the point has an alternate; own-scale otherwise --
       the same per-point rule the rv() spans follow. */
    if (hsOn() && p.hs_rating !== null && p.hs_rating !== undefined) {
      return p.hs_rating;
    }
    return p.speed_rating;
  }

  var W = 720, H = 200, L = 44, R = 10, T = 12, B = 22;

  function draw() {
    var pts = { a: data.a || [], b: data.b || [] };
    var all = pts.a.concat(pts.b);
    if (!all.length) return;

    var xs = all.map(function (p) { return Date.parse(p.date); });
    var vs = all.map(val);
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var v0 = Math.min.apply(null, vs), v1 = Math.max.apply(null, vs);
    if (x1 === x0) x1 = x0 + 1;
    var pad = Math.max((v1 - v0) * 0.08, 1.0);
    v0 -= pad; v1 += pad;

    function X(p) { return L + (Date.parse(p.date) - x0) / (x1 - x0) * (W - L - R); }
    function Y(p) { return T + (v1 - val(p)) / (v1 - v0) * (H - T - B); }

    var out = [];
    out.push('<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) +
             '" y2="' + (H - B) + '" stroke="#e2e4e8"/>');
    out.push('<line x1="' + L + '" y1="' + T + '" x2="' + L + '" y2="' +
             (H - B) + '" stroke="#e2e4e8"/>');
    out.push('<text x="4" y="' + (T + 8) + '" font-size="10" fill="#6b7280">' +
             v1.toFixed(0) + "</text>");
    out.push('<text x="4" y="' + (H - B) + '" font-size="10" fill="#6b7280">' +
             v0.toFixed(0) + "</text>");

    var colors = { a: "#14477d", b: "#b45309" };
    ["a", "b"].forEach(function (side) {
      var s = pts[side];
      if (!s.length) return;
      var line = s.map(function (p) {
        return X(p).toFixed(1) + "," + Y(p).toFixed(1);
      }).join(" ");
      out.push('<polyline fill="none" stroke="' + colors[side] +
               '" stroke-width="2" points="' + line + '"/>');
      if (s.length <= 80) {
        s.forEach(function (p) {
          out.push('<circle cx="' + X(p).toFixed(1) + '" cy="' +
                   Y(p).toFixed(1) + '" r="2.5" fill="' + colors[side] + '"/>');
        });
      }
    });
    svg.innerHTML = out.join("");
  }

  draw();
  document.addEventListener("rc-scale-change", draw);
})();
