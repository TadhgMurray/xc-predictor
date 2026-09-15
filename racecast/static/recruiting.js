/* recruiting.js -- the athlete edition (282). The filters and the subject
   (an athlete id, or an event and a time) are the query string, so a
   placement is a link; the rows come from /api/recruiting/schools. */
(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("recruiting");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function selected(sel) { return [...sel.selectedOptions].map((o) => o.value); }
  function setSelected(sel, values) {
    const want = new Set(values);
    for (const o of sel.options) o.selected = want.has(o.value);
  }
  function fmtTime(sec) {
    if (sec == null || sec <= 0) return "";
    if (sec < 900) {
      const s = Math.floor(sec * 10 + 0.5) / 10;
      const m = Math.floor(s / 60), r = s - 60 * m;
      return m + ":" + (r < 10 ? "0" : "") + r.toFixed(1);
    }
    const s = Math.round(sec), m = Math.floor(s / 60), r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }
  function schoolHref(r) {
    return "/recruiting/school/" + encodeURIComponent(r.school) +
      "?gender=" + encodeURIComponent(current.gender) + (r.state ? "&state=" + r.state : "") + subjectQuery();
  }

  /* ---- the subject ------------------------------------------------ */
  const current = { gender: "m" };

  function subjectQuery() {
    const q = new URLSearchParams();
    const pid = $("r-athlete").value;
    if (pid) q.set("athlete", pid);
    else if ($("r-time").value.trim()) {
      q.set("event", $("r-event").value);
      q.set("time", $("r-time").value.trim());
      q.set("gender", $("r-tgender").value);
    }
    const s = q.toString();
    return s ? "&" + s : "";
  }

  /* the athlete picker: the site's own typeahead, athletes only; the id
     is read off the row's link (/athlete/<id>) */
  let seq = 0, timer = null;
  const qbox = $("r-athlete-q"), list = $("r-athlete-list");
  qbox.addEventListener("input", () => {
    $("r-athlete").value = "";
    const q = qbox.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { list.hidden = true; return; }
    const mine = ++seq;
    timer = setTimeout(async () => {
      try {
        const res = await fetch("/search/api?kind=athlete&q=" + encodeURIComponent(q));
        const rows = await res.json();
        if (mine !== seq) return;
        list.innerHTML = rows.slice(0, 8).map((r) => {
          const m = /\/athlete\/(\d+)/.exec(r.link || "");
          if (!m) return "";
          return `<button type="button" class="r-combo-item" data-id="${m[1]}" data-label="${esc(r.label)}">${esc(r.label)}${r.sublabel ? ` <span class="sr-sub">${esc(r.sublabel)}</span>` : ""}</button>`;
        }).join("");
        list.hidden = !list.innerHTML;
      } catch (e) { list.hidden = true; }
    }, 150);
  });
  list.addEventListener("click", (e) => {
    const b = e.target.closest(".r-combo-item");
    if (!b) return;
    $("r-athlete").value = b.dataset.id;
    qbox.value = b.dataset.label;
    $("r-time").value = "";
    list.hidden = true;
    load();
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".r-combo")) list.hidden = true; });

  /* ---- the query string ------------------------------------------ */
  function query() {
    const q = new URLSearchParams();
    q.set("gender", $("r-gender").value);
    q.set("sport", $("r-sport").value);
    const pairs = [["division", selected($("r-division")).join(",")], ["state", selected($("r-state")).join(",")],
                   ["q", $("r-q").value], ["min_n", $("r-minn").value]];
    for (const [k, v] of pairs) if (String(v || "").trim()) q.set(k, String(v).trim());
    q.set("sort", $("r-sort").value);
    const pid = $("r-athlete").value;
    if (pid) q.set("athlete", pid);
    else if ($("r-time").value.trim()) {
      q.set("event", $("r-event").value);
      q.set("time", $("r-time").value.trim());
      q.set("gender", $("r-tgender").value);
    }
    return q;
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get("gender")) { $("r-gender").value = p.get("gender"); $("r-tgender").value = p.get("gender"); }
    if (p.get("sport")) $("r-sport").value = p.get("sport");
    setSelected($("r-division"), (p.get("division") || "").split(",").filter(Boolean));
    setSelected($("r-state"), (p.get("state") || "").split(",").filter(Boolean));
    $("r-q").value = p.get("q") || "";
    if (p.get("min_n")) $("r-minn").value = p.get("min_n");
    if (p.get("sort")) $("r-sort").value = p.get("sort");
    if (p.get("athlete")) $("r-athlete").value = p.get("athlete");
    if (p.get("event")) $("r-event").value = p.get("event");
    if (p.get("time")) $("r-time").value = p.get("time");
  }

  function status(msg, isError) {
    const el = $("r-status");
    el.textContent = msg || "";
    el.className = "predict-status" + (msg ? " show" : "") + (isError ? " error" : "");
  }

  /* ---- rendering -------------------------------------------------- */
  function tierCell(t) {
    if (!t) return "";
    const gap = t.gap != null ? ` <span class="r-gap" title="rating points to ${esc(t.next_label)}">+${t.gap.toFixed(1)} to ${esc(t.next_label)}</span>` : "";
    return `<td class="r-tiercell"><span class="r-pill r-pill-${t.key}" title="${esc(t.blurb)}">${esc(t.label)}</span>${gap}</td>`;
  }

  function renderSubject(d) {
    const s = d.subject, box = $("r-subject");
    $("r-clear").hidden = !s && !d.subject_error;
    if (d.subject_error) { box.innerHTML = `<p class="predict-status show error">${esc(d.subject_error)}</p>`; return; }
    if (!s) { box.innerHTML = ""; return; }
    const t = d.subject_times || {};
    const times = ["5k", "1600", "3200"].filter((k) => t[k]).map((k) =>
      `${k === "5k" ? "5K" : k} <strong>${fmtTime(t[k])}</strong>`).join(" · ");
    let who;
    if (s.kind === "athlete") {
      qbox.value = s.name;
      const seasons = Object.keys(s.ratings).map((sp) =>
        `${s.ratings[sp].toFixed(1)} in ${s.seasons[sp]} ${sp === "XC" ? "XC" : "track"}`).join(", ");
      who = `<a href="/athlete/${s.person_id}">${esc(s.name)}</a>${s.school_label ? " (" + esc(s.school_label) + ")" : ""}${s.grad_year ? ", class of " + s.grad_year : ""}: ${seasons}.`;
    } else {
      who = `A ${esc(s.time)} ${esc(s.event_label)} is a <strong>${s.ratings.XC.toFixed(1)}</strong> rating.`;
    }
    box.innerHTML = `<p class="r-subject-line">${who} On the ${d.sport === "XC" ? "cross country" : "track"} recruits below you are placed at <strong>${d.rating.toFixed(1)}</strong>${times ? " (about " + times + ")" : ""}.</p>`;
  }

  function schoolRow(r, withTier) {
    const t = r.times || {};
    const med = t.median || {}, p25 = t.p25 || {};
    return `<tr>
      <td><a href="${schoolHref(r)}">${esc(r.label || r.school)}</a></td>
      <td>${esc(r.division || "")}</td>
      <td>${esc(r.conference || "")}</td>
      <td class="num">${r.n}${r.n_hs ? `<span class="dist-note-quiet" title="recruits with a linked high-school season"> (${r.n_hs})</span>` : ""}</td>
      <td class="num">${r.min == null ? " - " : r.min.toFixed(1)}</td>
      <td class="num"><strong>${r.median == null ? " - " : r.median.toFixed(1)}</strong></td>
      <td class="num">${r.max == null ? " - " : r.max.toFixed(1)}</td>
      <td class="num">${fmtTime(p25["5k"]) || " - "} / ${fmtTime(med["5k"]) || " - "}</td>
      <td class="num">${fmtTime(p25["3200"]) || " - "} / ${fmtTime(med["3200"]) || " - "}</td>
      <td class="num">${fmtTime(p25["1600"]) || " - "} / ${fmtTime(med["1600"]) || " - "}</td>
      ${withTier ? tierCell(r.tier) : ""}
    </tr>`;
  }

  function tableHead(withTier) {
    return `<thead><tr><th>School</th><th>Division</th><th>Conference</th><th class="num">Recruits</th>
      <th class="num">Slowest</th><th class="num">Typical</th><th class="num">Fastest</th>
      <th class="num" title="the recruit range starts / the typical recruit">5K XC in / typical</th>
      <th class="num">3200 in / typical</th><th class="num">1600 in / typical</th>${withTier ? "<th>You</th>" : ""}</tr></thead>`;
  }

  function renderSuggestions(d) {
    const sec = $("r-suggest"), out = $("r-suggest-out");
    const groups = d.suggestions || [];
    if (!groups.length) {
      sec.hidden = d.rating == null;
      out.innerHTML = d.rating == null ? "" : `<p class="meta">Nothing in the filters lands in a recruit or walk-on band for this rating. Widen the filters, or look at the table for the gap to each school.</p>`;
      return;
    }
    sec.hidden = false;
    out.innerHTML = groups.map((g) => `
      <h3 class="r-band r-band-${g.key}"><span class="r-pill r-pill-${g.key}">${esc(g.label)}</span> <span class="dist-note">${esc(g.blurb)} · ${g.total} school${g.total === 1 ? "" : "s"}${g.total > g.schools.length ? ", the fastest " + g.schools.length + " shown" : ""}</span></h3>
      <div class="r-scroll"><table class="rk r-sugg">${tableHead(false)}<tbody>${g.schools.map((r) => schoolRow(r, false)).join("")}</tbody></table></div>`).join("");
  }

  function render(d) {
    current.gender = d.gender;
    $("r-gender").value = d.gender;
    renderSubject(d);
    renderSuggestions(d);
    const rows = d.rows || [];
    if (!d.built) {
      $("r-out").innerHTML = `<p class="meta">The recruiting table has not been built yet.</p>`;
      return;
    }
    if (!rows.length) {
      $("r-out").innerHTML = `<p class="meta">No school matches. Widen the filters.</p>`;
      return;
    }
    const withTier = d.rating != null;
    $("r-out").innerHTML = `<p class="meta">${rows.length} of ${d.total} ${d.gender === "m" ? "men's" : "women's"} ${d.sport === "XC" ? "cross country" : "track"} programmes. Ratings are on the high-school scale; the times are the recruit range's start and the typical recruit.</p>
      <div class="r-scroll"><table class="rk r-schools-tbl">${tableHead(withTier)}<tbody>${rows.map((r) => schoolRow(r, withTier)).join("")}</tbody></table></div>`;
    // the division picker learns its options from the table itself
    const div = $("r-division");
    if (d.divisions && d.divisions.length && div.options.length === 0) {
      for (const v of d.divisions) { const o = document.createElement("option"); o.value = v; o.textContent = v; div.appendChild(o); }
    }
  }

  async function load() {
    const q = query();
    try { history.replaceState(null, "", location.pathname + "?" + q.toString()); } catch (e) { /* fine */ }
    status("Loading…", false);
    try {
      const res = await fetch("/api/recruiting/schools?" + q.toString());
      const d = await res.json();
      if (!res.ok || d.error) { status(d.error || res.statusText, true); $("r-out").innerHTML = ""; return; }
      status("", false);
      render(d);
    } catch (err) {
      status("Could not reach the server: " + err.message, true);
    }
  }

  $("r-go").addEventListener("click", load);
  $("r-place").addEventListener("click", () => {
    if ($("r-time").value.trim()) $("r-athlete").value = "";
    load();
  });
  $("r-clear").addEventListener("click", () => {
    $("r-athlete").value = ""; qbox.value = ""; $("r-time").value = "";
    load();
  });
  for (const id of ["r-gender", "r-sport", "r-sort"]) $(id).addEventListener("change", load);
  for (const id of ["r-q", "r-minn", "r-time"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { if (id === "r-time") $("r-athlete").value = ""; load(); } });
  }

  readUrl();
  if (root.dataset.athlete && !$("r-athlete").value) $("r-athlete").value = root.dataset.athlete;
  load();
})();
