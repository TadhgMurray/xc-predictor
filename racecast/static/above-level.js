/* above-level.js -- fills the race page's "vs level" column and the
   "Ran above their level" box after the page has rendered (above_level.py,
   app.api_above_level). The page never waits for it: no answer, no column. */
(function () {
  var box = document.getElementById("above-box");
  if (!box) return;
  var rows = [], byRid = {};
  document.querySelectorAll("tr[data-pid]").forEach(function (tr) {
    var rid = tr.id.replace(/^r/, "");
    rows.push({ rid: rid, pid: tr.dataset.pid, rating: tr.dataset.rating,
                pool: tr.dataset.pool });
    byRid[rid] = tr;
  });
  function hideColumn() {
    document.querySelectorAll(".vs-level, .vs-level-h").forEach(function (el) {
      el.style.display = "none";
    });
  }
  if (!rows.length) { hideColumn(); return; }
  fetch("/api/above-level", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sport: box.dataset.sport, date: box.dataset.date,
                           distance: box.dataset.distance ? Number(box.dataset.distance) : null,
                           rows: rows })
  }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (!d || !d.levels || !Object.keys(d.levels).length) { hideColumn(); return; }
    var above = {};
    (d.surprises || []).forEach(function (rid) { above[rid] = true; });
    document.querySelectorAll("td.vs-level[data-rid]").forEach(function (td) {
      var v = d.levels[td.dataset.rid];
      if (v === undefined || v === null) return;
      td.textContent = (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1) + "%";
      if (above[td.dataset.rid]) td.classList.add("is-above");
      else if (v < 0) td.classList.add("is-under");
    });
    if (!d.surprises || !d.surprises.length || d.sigma == null) return;
    box.querySelector(".ab-sigma").textContent = d.sigma.toFixed(1) + "%";
    var ul = box.querySelector("ul");
    d.surprises.forEach(function (rid) {
      var tr = byRid[rid];
      if (!tr) return;
      var link = tr.querySelector("td:nth-child(2) a") || tr.querySelector("td:nth-child(2)");
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#r" + rid;
      a.textContent = link ? link.textContent.trim() : "Runner";
      var b = document.createElement("strong");
      b.textContent = "+" + d.levels[rid].toFixed(1) + "%";
      li.appendChild(a); li.appendChild(document.createTextNode(" ")); li.appendChild(b);
      ul.appendChild(li);
    });
    box.hidden = false;
  }).catch(hideColumn);
})();
