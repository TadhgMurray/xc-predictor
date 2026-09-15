/* recruiting-search.js -- the coach's search (282, /recruiting/search).
   The filters are the query string, so a search is a link; the rows
   come from /api/recruiting. */
(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("recruiting");
  const YEARS = { XC: root.dataset.yearXc, TF: root.dataset.yearTf };
  const LIMIT = 100;
  const state = { pool: "hs_m", sport: "XC", grad: new Set() };
  let offset = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* the classes on offer follow the season: the seniors of the season
     first, then the three classes under them, as toggles */
  function fillGrad() {
    const label = parseInt($("r-year").value || YEARS[state.sport] || "", 10);
    const box = $("r-grad");
    box.innerHTML = "";
    if (!label) return;
    const senior = state.sport === "TF" ? label : label + 1;
    for (let y = senior; y <= senior + 3; y++) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "rc-chip" + (state.grad.has(String(y)) ? " is-on" : "");
      b.dataset.year = String(y); b.textContent = String(y);
      b.addEventListener("click", () => {
        if (state.grad.has(b.dataset.year)) state.grad.delete(b.dataset.year); else state.grad.add(b.dataset.year);
        b.classList.toggle("is-on");
        offset = 0; load();
      });
      box.appendChild(b);
    }
    $("r-year").placeholder = String(YEARS[state.sport] || "");
  }

  function paintSeg() {
    document.querySelectorAll(".rc-seg-btn").forEach((b) => b.classList.toggle("is-on", state[b.dataset.set] === b.dataset.value));
  }

  function query() {
    const q = new URLSearchParams();
    q.set("pool", state.pool);
    q.set("sport", state.sport);
    const pairs = [["year", $("r-year").value], ["grad", [...state.grad].sort().join(",")],
                   ["state", $("r-state").value], ["min_rating", $("r-min").value],
                   ["max_rating", $("r-max").value], ["min_gain", $("r-gain").value],
                   ["min_races", $("r-races").value], ["school", $("r-school").value]];
    for (const [k, v] of pairs) if (String(v || "").trim()) q.set(k, String(v).trim());
    q.set("sort", $("r-sort").value);
    return q;
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get("pool") === "hs_m" || p.get("pool") === "hs_f") state.pool = p.get("pool");
    if (p.get("sport") === "XC" || p.get("sport") === "TF") state.sport = p.get("sport");
    $("r-year").value = p.get("year") || "";
    state.grad = new Set((p.get("grad") || "").split(",").filter(Boolean));
    $("r-state").value = (p.get("state") || "").split(",")[0];
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
    const label = d.season, prev = d.season - 1;
    const body = rows.map((r, i) => `
      <tr>
        <td class="num rc-dim">${offset + i + 1}</td>
        <td class="rc-school"><a href="/recruit/${r.person_id}">${esc(r.name || "Unknown")}</a></td>
        <td>${r.school ? `<a href="/school/${encodeURIComponent(r.school)}${r.state ? "?state=" + r.state : ""}">${esc(r.school_label || r.school)}</a>` : " - "}</td>
        <td class="rc-dim">${esc(r.grade_label || "")}</td>
        <td class="num">${r.grad_year || " - "}</td>
        <td class="num rc-strong">${r.mean_rating == null ? " - " : r.mean_rating.toFixed(1)}</td>
        <td class="num rc-dim">${r.prev_rating == null ? " - " : r.prev_rating.toFixed(1)}</td>
        ${gainCell(r.gain)}
        <td class="num">${r.best_rating == null ? " - " : r.best_rating.toFixed(1)}</td>
        <td class="num rc-dim">${r.n_races}</td>
      </tr>`).join("");
    $("r-out").innerHTML = `<p class="meta rc-count">${d.pool === "hs_f" ? "Girls" : "Boys"}, ${label} ${d.sport === "XC" ? "cross country" : "track"}. Ratings are season averages; the gain is against ${prev}.</p>
      <div class="r-scroll"><table class="rk rc-tbl">
      <thead><tr><th class="num">#</th><th>Athlete</th><th>School</th><th>Grade</th>
        <th class="num">Class of</th><th class="num" title="this season's rating">${label}</th><th class="num" title="the same athlete a year earlier">${prev}</th>
        <th class="num">Gain</th><th class="num" title="best single race">Best</th><th class="num">Races</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
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
  document.querySelectorAll(".rc-seg-btn").forEach((b) => b.addEventListener("click", () => {
    state[b.dataset.set] = b.dataset.value;
    paintSeg();
    if (b.dataset.set === "sport") { state.grad = new Set(); fillGrad(); }
    offset = 0; load();
  }));
  $("r-year").addEventListener("change", () => { state.grad = new Set(); fillGrad(); offset = 0; load(); });
  for (const id of ["r-state", "r-sort"]) $(id).addEventListener("change", () => { offset = 0; load(); });
  for (const id of ["r-year", "r-min", "r-max", "r-gain", "r-races", "r-school"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { offset = 0; load(); } });
  }

  readUrl();
  paintSeg();
  fillGrad();
  load();
})();
