/* recruiting.js -- the coach's search (282). The filters are the query
   string, so a search is a link; the rows come from /api/recruiting. */
(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("recruiting");
  const YEARS = { XC: root.dataset.yearXc, TF: root.dataset.yearTf };
  const LIMIT = 100;
  let offset = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function selected(sel) {
    return [...sel.selectedOptions].map((o) => o.value);
  }
  function setSelected(sel, values) {
    const want = new Set(values);
    for (const o of sel.options) o.selected = want.has(o.value);
  }

  /* the graduation years on offer follow the season: seniors of the season
     first, then the three classes under them */
  function fillGrad(keep) {
    const label = parseInt($("r-year").value || YEARS[$("r-sport").value] || "", 10);
    const sel = $("r-grad");
    const was = keep ? new Set(selected(sel)) : new Set();
    sel.innerHTML = "";
    if (!label) return;
    const senior = $("r-sport").value === "TF" ? label : label + 1;
    for (let y = senior; y <= senior + 3; y++) {
      const o = document.createElement("option");
      o.value = String(y); o.textContent = "Class of " + y;
      if (was.has(o.value)) o.selected = true;
      sel.appendChild(o);
    }
  }

  function query() {
    const q = new URLSearchParams();
    q.set("pool", $("r-pool").value);
    q.set("sport", $("r-sport").value);
    const pairs = [["year", $("r-year").value], ["grad", selected($("r-grad")).join(",")],
                   ["state", selected($("r-state")).join(",")], ["min_rating", $("r-min").value],
                   ["max_rating", $("r-max").value], ["min_gain", $("r-gain").value],
                   ["min_races", $("r-races").value], ["school", $("r-school").value]];
    for (const [k, v] of pairs) if (String(v || "").trim()) q.set(k, String(v).trim());
    q.set("sort", $("r-sort").value);
    return q;
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get("pool")) $("r-pool").value = p.get("pool");
    if (p.get("sport")) $("r-sport").value = p.get("sport");
    $("r-year").value = p.get("year") || "";
    fillGrad(false);
    setSelected($("r-grad"), (p.get("grad") || "").split(",").filter(Boolean));
    setSelected($("r-state"), (p.get("state") || "").split(",").filter(Boolean));
    $("r-min").value = p.get("min_rating") || "";
    $("r-max").value = p.get("max_rating") || "";
    $("r-gain").value = p.get("min_gain") || "";
    $("r-races").value = p.get("min_races") || "";
    $("r-school").value = p.get("school") || "";
    if (p.get("sort")) $("r-sort").value = p.get("sort");
    offset = parseInt(p.get("offset") || "0", 10) || 0;
  }

  function status(msg, isError) {
    const el = $("r-status");
    el.textContent = msg || "";
    el.className = "predict-status" + (msg ? " show" : "") + (isError ? " error" : "");
  }

  function gainCell(g) {
    if (g == null) return `<td class="num"> - </td>`;
    const cls = g > 0 ? "gain-up" : (g < 0 ? "gain-down" : "");
    return `<td class="num ${cls}">${g > 0 ? "+" : ""}${g.toFixed(1)}</td>`;
  }

  function render(d) {
    const rows = d.rows || [];
    if (!rows.length) {
      $("r-out").innerHTML = `<p class="meta">Nothing matches. Widen the search.</p>`;
      $("r-pager").hidden = true;
      return;
    }
    const body = rows.map((r, i) => `
      <tr>
        <td class="num">${offset + i + 1}</td>
        <td><a href="/recruit/${r.person_id}">${esc(r.name || "Unknown")}</a></td>
        <td>${r.school ? `<a href="/school/${encodeURIComponent(r.school)}${r.state ? "?state=" + r.state : ""}">${esc(r.school_label || r.school)}</a>` : " - "}</td>
        <td>${esc(r.state || "")}</td>
        <td>${esc(r.grade_label || "")}</td>
        <td class="num">${r.grad_year || " - "}</td>
        <td class="num">${r.mean_rating == null ? " - " : r.mean_rating.toFixed(1)}</td>
        <td class="num">${r.prev_rating == null ? " - " : r.prev_rating.toFixed(1)}</td>
        ${gainCell(r.gain)}
        <td class="num">${r.best_rating == null ? " - " : r.best_rating.toFixed(1)}</td>
        <td class="num">${r.n_races}</td>
      </tr>`).join("");
    const label = d.season, prev = d.season - 1;
    $("r-out").innerHTML = `<table class="rk">
      <thead><tr><th class="num">#</th><th>Athlete</th><th>School</th><th>State</th><th>Grade</th>
        <th class="num">Class of</th><th class="num">${label}</th><th class="num">${prev}</th>
        <th class="num">Gain</th><th class="num">Best</th><th class="num">Races</th></tr></thead>
      <tbody>${body}</tbody></table>`;
    $("r-pager").hidden = false;
    $("r-prev").disabled = offset === 0;
    $("r-next").disabled = rows.length < LIMIT;
    $("r-note").textContent = `${offset + 1} to ${offset + rows.length}`;
  }

  async function load() {
    const q = query();
    if (offset) q.set("offset", String(offset));
    try { history.replaceState(null, "", location.pathname + "?" + q.toString()); } catch (e) { /* fine */ }
    q.set("limit", String(LIMIT));
    status("Searching…", false);
    try {
      const res = await fetch("/api/recruiting?" + q.toString());
      const d = await res.json();
      if (!res.ok || d.error) { status(d.error || res.statusText, true); $("r-out").innerHTML = ""; $("r-pager").hidden = true; return; }
      status("", false);
      render(d);
    } catch (err) {
      status("Could not reach the server: " + err.message, true);
    }
  }

  $("r-go").addEventListener("click", () => { offset = 0; load(); });
  $("r-prev").addEventListener("click", () => { offset = Math.max(0, offset - LIMIT); load(); });
  $("r-next").addEventListener("click", () => { offset += LIMIT; load(); });
  $("r-sport").addEventListener("change", () => fillGrad(true));
  $("r-year").addEventListener("change", () => fillGrad(true));
  for (const id of ["r-year", "r-min", "r-max", "r-gain", "r-races", "r-school"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { offset = 0; load(); } });
  }

  readUrl();
  load();
})();
