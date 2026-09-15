/* recruiting.js -- the athlete edition (282). The filters and the subject
   (an athlete id, or an event and a time) are the query string, so a
   placement is a link; the rows come from /api/recruiting/schools. */
(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("recruiting");
  const state = { gender: "m", sport: "XC" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  function num(v, d) { return v == null ? " - " : Number(v).toFixed(d == null ? 1 : d); }

  /* ---- the subject ------------------------------------------------ */
  function subjectPairs() {
    const pid = $("r-athlete").value;
    if (pid) return [["athlete", pid]];
    if ($("r-time").value.trim()) {
      return [["event", $("r-event").value], ["time", $("r-time").value.trim()], ["gender", $("r-tgender").value]];
    }
    return [];
  }
  function subjectQuery() {
    const q = new URLSearchParams();
    for (const [k, v] of subjectPairs()) q.set(k, v);
    const s = q.toString();
    return s ? "&" + s : "";
  }
  function schoolHref(r) {
    return "/recruiting/school/" + encodeURIComponent(r.school) +
      "?gender=" + encodeURIComponent(state.gender) + (r.state ? "&state=" + r.state : "") + subjectQuery();
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
    q.set("gender", state.gender);
    q.set("sport", state.sport);
    const pairs = [["division", $("r-division").value], ["state", $("r-state").value],
                   ["q", $("r-q").value], ["min_n", $("r-minn").value]];
    for (const [k, v] of pairs) if (String(v || "").trim()) q.set(k, String(v).trim());
    q.set("sort", $("r-sort").value);
    for (const [k, v] of subjectPairs()) q.set(k, v);
    return q;
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get("gender") === "m" || p.get("gender") === "f") { state.gender = p.get("gender"); $("r-tgender").value = p.get("gender"); }
    if (p.get("sport") === "XC" || p.get("sport") === "TF") state.sport = p.get("sport");
    $("r-division").value = (p.get("division") || "").split(",")[0];
    $("r-state").value = (p.get("state") || "").split(",")[0];
    $("r-q").value = p.get("q") || "";
    if (p.get("min_n")) $("r-minn").value = p.get("min_n");
    if (p.get("sort")) $("r-sort").value = p.get("sort");
    if (p.get("athlete")) $("r-athlete").value = p.get("athlete");
    if (p.get("event")) $("r-event").value = p.get("event");
    if (p.get("time")) $("r-time").value = p.get("time");
  }

  function paintSeg() {
    document.querySelectorAll(".rc-seg-btn").forEach((b) => {
      b.classList.toggle("is-on", state[b.dataset.set] === b.dataset.value);
    });
  }

  function status(msg, isError) {
    const el = $("r-status");
    el.textContent = msg || "";
    el.className = "predict-status" + (msg ? " show" : "") + (isError ? " error" : "");
  }

  /* ---- rendering -------------------------------------------------- */
  function pill(t) {
    return `<span class="r-pill r-pill-${t.key}" title="${esc(t.blurb)}">${esc(t.label)}</span>`;
  }
  function tierCell(t) {
    if (!t) return `<td class="r-tiercell"> - </td>`;
    const gap = t.gap != null ? `<span class="r-gap">${t.gap.toFixed(1)} to ${esc(t.next_label).toLowerCase()}</span>` : "";
    return `<td class="r-tiercell">${pill(t)}${gap}</td>`;
  }

  function renderSubject(d) {
    const s = d.subject, box = $("r-subject"), form = $("rc-you-form");
    if (d.subject_error) {
      box.hidden = false; form.hidden = false;
      box.innerHTML = `<p class="predict-status show error">${esc(d.subject_error)}</p>`;
      return;
    }
    if (!s) { box.hidden = true; form.hidden = false; box.innerHTML = ""; return; }
    const t = d.subject_times || {};
    const equiv = [["5k", "5K"], ["1600", "1600"], ["3200", "3200"]].filter(([k]) => t[k]).map(([k, w]) =>
      `<span class="rc-time-chip"><span class="rc-time-lbl">${w}</span>${fmtTime(t[k])}</span>`).join("");
    /* the athlete's own PRs are the real times; the conversions are what
       the rating is worth at a typical venue, and say so */
    const prs = (s.prs || []).map((p) =>
      `<span class="rc-time-chip rc-pr" title="${esc(p.date)}"><span class="rc-time-lbl">${esc(p.label)}</span>${esc(p.time)}</span>`).join("");
    const times = (prs ? `<span class="rc-times-lbl">PRs</span>${prs}` : "") +
      (equiv ? `<span class="rc-times-lbl">${prs ? "rating equivalents" : "equivalent to"}</span>${equiv}` : "");
    let who, sub;
    if (s.kind === "athlete") {
      qbox.value = s.name;
      who = `<a href="/athlete/${s.person_id}">${esc(s.name)}</a>`;
      const bits = [];
      if (s.school_label) bits.push(esc(s.school_label));
      if (s.grad_year) bits.push("class of " + s.grad_year);
      const seasons = Object.keys(s.ratings).map((sp) =>
        `${s.ratings[sp].toFixed(1)} in ${s.seasons[sp]} ${sp === "XC" ? "XC" : "track"}`).join(", ");
      sub = bits.join(" · ") + (bits.length ? " · " : "") + seasons;
    } else {
      who = `A ${esc(s.time)} ${esc(s.event_label)}`;
      sub = `converted at a typical ${s.sport === "XC" ? "course" : "track"}`;
    }
    form.hidden = true;
    box.hidden = false;
    box.innerHTML = `
      <div class="rc-subject-main">
        <div class="rc-subject-rating"><span class="rc-subject-num">${d.rating.toFixed(1)}</span><span class="rc-subject-word">rating</span></div>
        <div class="rc-subject-who"><div class="rc-subject-name">${who}</div><div class="rc-subject-sub">${sub}</div>
          <div class="rc-subject-times">${times}</div></div>
      </div>
      <div class="rc-subject-note">Placed on the ${state.gender === "m" ? "men's" : "women's"} ${state.sport === "XC" ? "cross country" : "track"} recruits below. <button type="button" class="rc-link" id="r-clear">Change</button></div>`;
    $("r-clear").addEventListener("click", () => {
      $("r-athlete").value = ""; qbox.value = ""; $("r-time").value = "";
      load();
    });
  }

  function schoolRow(r, withTier) {
    const t = r.times || {}, med = t.median || {};
    return `<tr>
      <td class="rc-school"><a href="${schoolHref(r)}">${esc(r.label || r.school)}</a></td>
      <td class="rc-dim">${esc(r.division || "")}</td>
      <td class="rc-dim">${esc(r.conference || "")}</td>
      <td class="num">${r.n}</td>
      <td class="num rc-dim">${num(r.min)}</td>
      <td class="num rc-strong">${num(r.median)}</td>
      <td class="num rc-dim">${num(r.max)}</td>
      <td class="num">${fmtTime(med["5k"]) || " - "}</td>
      <td class="num">${fmtTime(med["3200"]) || " - "}</td>
      ${withTier ? tierCell(r.tier) : ""}
    </tr>`;
  }

  function tableHead(withTier) {
    return `<thead><tr><th>School</th><th>Division</th><th>Conference</th><th class="num" title="recruits in the classes counted">Recruits</th>
      <th class="num" title="the slowest recruit's rating: the walk-on line">Slowest</th>
      <th class="num" title="the median recruit's rating">Typical</th>
      <th class="num" title="the fastest recruit's rating">Fastest</th>
      <th class="num" title="the typical recruit's rating as a 5K at a typical course">5K</th>
      <th class="num" title="the typical recruit's rating as a 3200 on a typical track">3200</th>${withTier ? "<th>You</th>" : ""}</tr></thead>`;
  }

  function renderSuggestions(d) {
    const sec = $("r-suggest"), out = $("r-suggest-out");
    const groups = d.suggestions || [];
    if (d.rating == null) { sec.hidden = true; out.innerHTML = ""; return; }
    sec.hidden = false;
    if (!groups.length) {
      out.innerHTML = `<p class="meta">Nothing in these filters lands in a recruit or walk-on band for this rating. Widen the filters, or read the gap to each school in the table.</p>`;
      return;
    }
    out.innerHTML = groups.map((g) => `
      <div class="rc-band rc-band-${g.key}">
        <div class="rc-band-head"><span class="r-pill r-pill-${g.key}">${esc(g.label)}</span>
          <span class="rc-band-blurb">${esc(g.blurb)}</span>
          <span class="rc-band-n">${g.total} school${g.total === 1 ? "" : "s"}${g.total > g.schools.length ? ", fastest " + g.schools.length + " shown" : ""}</span></div>
        <div class="r-scroll"><table class="rk rc-tbl rc-sugg">${tableHead(false)}<tbody>${g.schools.map((r) => schoolRow(r, false)).join("")}</tbody></table></div>
      </div>`).join("");
  }

  function render(d) {
    state.gender = d.gender; state.sport = d.sport;
    $("r-tgender").value = d.gender;
    paintSeg();
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
    $("r-out").innerHTML = `<p class="meta rc-count">${rows.length} of ${d.total} ${d.gender === "m" ? "men's" : "women's"} ${d.sport === "XC" ? "cross country" : "track"} programmes. Ratings on the high-school scale; the times are the typical recruit's.</p>
      <div class="r-scroll"><table class="rk rc-tbl">${tableHead(withTier)}<tbody>${rows.map((r) => schoolRow(r, withTier)).join("")}</tbody></table></div>`;
    const div = $("r-division");
    if (d.divisions && d.divisions.length && div.options.length <= 1) {
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
  $("r-place").addEventListener("click", () => { $("r-athlete").value = ""; load(); });
  document.querySelectorAll(".rc-seg-btn").forEach((b) => b.addEventListener("click", () => {
    state[b.dataset.set] = b.dataset.value;
    paintSeg();
    load();
  }));
  for (const id of ["r-division", "r-state", "r-sort"]) $(id).addEventListener("change", load);
  for (const id of ["r-q", "r-minn", "r-time"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") { if (id === "r-time") $("r-athlete").value = ""; load(); } });
  }

  readUrl();
  paintSeg();
  if (root.dataset.athlete && !$("r-athlete").value) $("r-athlete").value = root.dataset.athlete;
  /* the accounts seam (283): a signed-in athlete with a linked page is the
     subject when the URL names nobody. The page loads at once; if /api/me
     (asked by topbar-search.js) later brings a linked page, it reloads. */
  function fromAccount(me) {
    if (!me || !me.signed_in || subjectPairs().length) return false;
    const a = (me.athletes || [])[0];
    if (!a) return false;
    $("r-athlete").value = String(a.person_id);
    return true;
  }
  if (window.xcpMe) fromAccount(window.xcpMe);
  else document.addEventListener("xcp:me", (e) => { if (fromAccount(e.detail)) load(); });
  load();
})();
