/*
 * h2h.js -- the "Predicted" tab on /compare (owner, 2026-10-10, item 10):
 * a course and a date, both athletes' predicted times, the margin.
 *
 * ★ ONE REQUEST FOR BOTH. /api/predict/individual takes person_id=a,b and
 *   answers {available, athletes: [a, b]} -- the same predictor, the same
 *   manual target the predictions page sends, so the two pages cannot give
 *   one athlete two times for one race.
 *
 * ★ THE COURSE IS OPTIONAL. Empty means a typical course at the distance;
 *   a picked course offers the distances it has a fitted difficulty at
 *   (/api/course_search, the conversions page's own picker source).
 *
 * ! TRACK HAS NO COURSE. The predictor does not model a venue per track
 *   (predict._applyCourse is XC only), so the box hides for track.
 */
"use strict";

(function () {
  var host = document.getElementById("predict");
  if (!host || !host.classList.contains("h2h-pred")) return;

  var $ = function (id) { return document.getElementById(id); };
  var sport = $("h2h-sport"), course = $("h2h-course"), sug = $("h2h-course-sug");
  var dist = $("h2h-dist"), date = $("h2h-date"), out = $("h2h-pred-out");
  var go = $("h2h-predict");
  var picked = null;          // {name, distances: [{distance, ...}]}

  // the distances each sport offers with no course picked: the race
  // distances the boards and the conversions page already name
  var XC_DIST = [3000, 4000, 5000, 6000, 8000, 10000];
  var TF_DIST = [800, 1500, 1600, 3000, 3200, 5000];
  var DEF = { XC: Number(host.dataset.dist) || 5000, TF: 1600 };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* 15:42.3 / 4:05.1 / 1:02:03.4 -- a prediction to the tenth, as the
     predictions page prints one */
  function clock(sec) {
    if (sec == null || !isFinite(sec)) return "–";
    var t = Math.round(sec * 10) / 10;
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    var ss = (s < 10 ? "0" : "") + s.toFixed(1);
    return h ? h + ":" + (m < 10 ? "0" : "") + m + ":" + ss : m + ":" + ss;
  }

  function distLabel(m) {
    if (m % 1000 === 0 && m >= 3000) return m / 1000 + "K";
    return m + "m";
  }

  function fillDistances() {
    var s = sport.value, want = Number(dist.value) || DEF[s];
    var list = s === "TF" ? TF_DIST.slice()
             : picked && picked.distances && picked.distances.length
               ? picked.distances.map(function (d) { return Math.round(d.distance); })
               : XC_DIST.slice();
    list = list.filter(function (v, i) { return list.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
    if (list.indexOf(want) < 0) want = list.indexOf(DEF[s]) >= 0 ? DEF[s] : list[0];
    dist.innerHTML = list.map(function (m) {
      return '<option value="' + m + '"' + (m === want ? " selected" : "") + ">" + distLabel(m) + "</option>";
    }).join("");
  }

  function today() {
    var d = new Date();
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  /* ---------------- the course picker ---------------- */
  var timer = null, seq = 0, closed = false;
  course.addEventListener("focus", function () { closed = false; });
  course.addEventListener("input", function () {
    picked = null;
    closed = false;
    clearTimeout(timer);
    var q = course.value.trim();
    if (q.length < 2) { sug.classList.add("hidden"); fillDistances(); return; }
    var mine = ++seq;
    timer = setTimeout(function () {
      fetch("/api/course_search?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (rows) {
          if (mine !== seq || closed) return;
          sug.innerHTML = "";
          (rows || []).forEach(function (r, i) {
            var d = document.createElement("div");
            d.className = "h2h-sug-row";
            d.textContent = r.name;
            d.dataset.i = i;
            sug.appendChild(d);
          });
          sug._rows = rows || [];
          sug.classList.toggle("hidden", !sug.children.length);
        })
        .catch(function () { sug.classList.add("hidden"); });
    }, 200);
  });
  sug.addEventListener("mousedown", function (e) {
    var row = e.target.closest(".h2h-sug-row");
    if (!row) return;
    picked = (sug._rows || [])[Number(row.dataset.i)] || null;
    course.value = picked ? picked.name : "";
    closed = true;
    clearTimeout(timer);
    sug.classList.add("hidden");
    fillDistances();
  });
  course.addEventListener("blur", function () {
    closed = true;
    clearTimeout(timer);
    setTimeout(function () { sug.classList.add("hidden"); }, 150);
  });

  sport.addEventListener("change", function () {
    var tf = sport.value === "TF";
    host.querySelector(".h2h-course-field").hidden = tf;
    dist.value = "";
    fillDistances();
  });

  /* ---------------- the prediction ---------------- */
  function row(name, cls, p) {
    if (!p || !p.available || p.seconds == null) {
      return '<tr><td><span class="h2h-dot ' + cls + '"></span>' + esc(name) + "</td>" +
             '<td class="h2h-none" colspan="2">' + esc((p && p.reason) || "No prediction") + "</td></tr>";
    }
    var band = p.lo != null && p.hi != null
      ? clock(p.lo) + " – " + clock(p.hi) : "";
    return '<tr><td><span class="h2h-dot ' + cls + '"></span>' + esc(name) + "</td>" +
           '<td class="h2h-t">' + clock(p.seconds) + "</td>" +
           '<td class="h2h-band">' + band + "</td></tr>";
  }

  function render(body, ctx) {
    var a = host.dataset.aName, b = host.dataset.bName;
    var list = body && body.athletes ? body.athletes : null;
    if (!list) {
      out.innerHTML = '<p class="meta">' + esc((body && (body.reason || body.error)) ||
        "The prediction is not available just now.") + "</p>";
      return;
    }
    var pa = list[0], pb = list[1];
    var html = '<table class="h2h-pred-tbl"><thead><tr><th>Athlete</th><th>Predicted</th>' +
               '<th class="h2h-band">Likely range</th></tr></thead><tbody>' +
               row(a, "dot-a", pa) + row(b, "dot-b", pb) + "</tbody></table>";
    if (pa && pb && pa.seconds != null && pb.seconds != null) {
      var d = pb.seconds - pa.seconds;
      var lead = Math.abs(d) < 0.05 ? null : d > 0 ? a : b;
      html += '<p class="h2h-margin">' + (lead
        ? "<b>" + esc(lead) + "</b> by <b>" + Math.abs(d).toFixed(1) + " s</b>"
        : "<b>Dead even</b>") +
        ' <span class="h2h-muted">' + esc(ctx) + "</span></p>";
    }
    out.innerHTML = html;
  }

  go.addEventListener("click", function () {
    var s = sport.value, m = Number(dist.value) || DEF[s];
    var q = new URLSearchParams({
      mode: "manual", person_id: host.dataset.a + "," + host.dataset.b,
      sport: s, distance: String(m), date: date.value || today()
    });
    var where = "typical " + (s === "TF" ? "track" : "course");
    if (s === "XC" && picked) { q.set("course", picked.name); where = picked.name; }
    var ctx = distLabel(m) + " · " + where + " · " + (date.value || today());
    out.innerHTML = '<p class="meta">Predicting&hellip;</p>';
    go.disabled = true;
    fetch("/api/predict/individual?" + q.toString())
      .then(function (r) { return r.json(); })
      .then(function (body) { render(body, ctx); })
      .catch(function () {
        out.innerHTML = '<p class="meta">The prediction failed. Try again in a moment.</p>';
      })
      .then(function () { go.disabled = false; });
  });

  date.value = today();
  fillDistances();
})();
