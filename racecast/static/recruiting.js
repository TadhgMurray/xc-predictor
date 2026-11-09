/* recruiting.js -- the athlete edition (282). The filters and the subject
   (an athlete id, or an event and a time) are the query string, so a
   placement is a link; the rows come from /api/recruiting/schools. */
(function () {
  const $ = (id) => document.getElementById(id);
  const root = $("recruiting");
  const state = { gender: "m", sport: "XC", view: viewOf(), page: 0, last: null };
  const PER_PAGE = 25;
  function viewOf() { return location.hash === "#transfers" ? "transfers" : "recruits"; }

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
          const m = /\/athlete\/(-?\d+|[amdrv][0-9a-z]{13})/.exec(r.link || "");
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
                   ["q", $("r-q").value]];
    for (const [k, v] of pairs) if (String(v || "").trim()) q.set(k, String(v).trim());
    q.set("sort", $("r-sort").value);
    if (state.sport === "TF") q.set("ev", $("r-ev").value);
    for (const [k, v] of subjectPairs()) q.set(k, v);
    return q;
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get("gender") === "m" || p.get("gender") === "f") { state.gender = p.get("gender"); $("r-tgender").value = p.get("gender"); }
    if (p.get("sport") === "XC" || p.get("sport") === "TF") state.sport = p.get("sport");
    if (p.get("ev") && ["800", "1600", "3200"].includes(p.get("ev"))) $("r-ev").value = p.get("ev");
    $("r-division").value = (p.get("division") || "").split(",")[0];
    $("r-state").value = (p.get("state") || "").split(",")[0];
    $("r-q").value = p.get("q") || "";
    if (p.get("sort")) $("r-sort").value = p.get("sort");
    if (p.get("athlete")) $("r-athlete").value = p.get("athlete");
    if (p.get("event")) $("r-event").value = p.get("event");
    if (p.get("time")) $("r-time").value = p.get("time");
  }

  function paintSeg() {
    document.querySelectorAll(".rc-seg-btn").forEach((b) => {
      b.classList.toggle("is-on", state[b.dataset.set] === b.dataset.value);
      b.setAttribute("aria-pressed", String(state[b.dataset.set] === b.dataset.value));
    });
  }

  /* `retry`: a server or network failure offers the same request again
     (sweep 2026-10-10, B4); a bad filter does not, retrying it is pointless. */
  function status(msg, isError, retry) {
    const el = $("r-status");
    el.textContent = msg || "";
    el.className = "predict-status" + (msg ? " show" : "") + (isError ? " error" : "");
    if (retry) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "rc-retry";
      b.textContent = "Retry";
      b.addEventListener("click", retry);
      el.append(" ", b);
    }
  }

/* ★ A SERVER ERROR IS NOT JSON (sweep 2026-10-10, B4). A 502 from the proxy
       is an HTML page, and res.json() on it threw "Unexpected token '<' ...
       is not valid JSON" -- which is what the reader was shown. This reads the
       body as JSON only when it is JSON, and otherwise throws a sentence a
       reader can use (err.friendly) instead. */
  async function readJson(res) {
    const type = (res.headers.get("content-type") || "").toLowerCase();
    if (!type.includes("json")) {
      const err = new Error(res.ok
        ? "The server sent an answer this page could not read."
        : `The server had a problem (error ${res.status}).`);
      err.friendly = true;
      throw err;
    }
    return res.json();
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
    /* ★ REAL PRs ONLY WHEN THERE ARE ANY (owner, 2026-11-01: "it shows that
       my 3200m PR is 9:06. I have never run 9:06, but I have run 9:01"):
       the rating's conversions sat beside the PRs and read as PRs. They
       stay only for a typed time, where they are what the time is worth. */
    const times = prs ? `<span class="rc-times-lbl">PRs</span>${prs}`
      : (equiv ? `<span class="rc-times-lbl">equivalent to</span>${equiv}` : "");
    let who, sub;
    if (s.kind === "athlete") {
      qbox.value = s.name;
      who = `<a href="/athlete/${s.person_id}">${esc(s.name)}</a>`;
      const bits = [];
      if (s.school_label) bits.push(esc(s.school_label));
      if (s.grad_year) bits.push("class of " + s.grad_year);
      const seasons = Object.keys(s.ratings).map((sp) =>
        `best ${s.ratings[sp].toFixed(1)} in ${s.seasons[sp]} ${sp === "XC" ? "XC" : "track"}`).join(", ");
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
      <div class="rc-subject-note">Placed on the ${state.gender === "m" ? "men's" : "women's"} ${state.sport === "XC" ? "cross country" : "track"} ${state.view === "transfers" ? "transfers below, from their latest college season (as its HS-equivalent rating)" : "recruits below"}. <button type="button" class="rc-link" id="r-clear">Change</button></div>`;
    $("r-clear").addEventListener("click", () => {
      $("r-athlete").value = ""; qbox.value = ""; $("r-time").value = "";
      load();
    });
  }

  /* ★ THE CREST BEFORE THE NAME (owner, 2026-10-11), as the other boards
     draw it (rankings.js crestMark): the API stamps r.crest
     (school_logo.stampCrests) only where a crest exists, so a school
     without one gets no broken <img>. */
  function crestMark(url) {
    return url ? `<img class="school-mark" src="${esc(url)}" alt=""`
               + ` width="18" height="18" loading="lazy" decoding="async">` : "";
  }

  /* ★ A DIVISION OR CONFERENCE WITH ITS LOGO, LINKED TO ITS PAGE (owner,
     2026-11-03): the API stamps r.division_logo / r.conference_logo only
     where a logo is stored (unit_board.stampUnitMarks), and the href where
     the unit has a page. */
  function unitBit(r, kind) {
    const v = r[kind];
    if (!v) return "";
    const logo = r[kind + "_logo"], href = r[kind + "_href"];
    const img = logo ? `<img class="unit-chip-logo" src="${esc(logo)}" alt=""`
      + ` width="14" height="14" loading="lazy" decoding="async">` : "";
    return href ? `<a class="rc-unit" href="${esc(href)}">${img}${esc(v)}</a>` : img + esc(v);
  }

  /* ★ "5K EQUIV" WITH ITS ⓘ (owner, 2026-10-11): _scale.html's fk_th, in JS --
     the same markup, so a board the browser draws reads exactly as the
     server's. The ⓘ is the CSS .flag tooltip (data-title / data-blurb in a
     ::after), so a header drawn later needs no wiring. */
  function fkTh(label, cls) {
    const lb = label || "5K", track = lb.startsWith("Track");
    return `<th class="fk no-break${cls ? " " + cls : ""}">${esc(track ? lb : lb + " equiv")}`
      + `<a class="flag info-i" href="/scale" data-title="Track ${track ? "" : esc(lb) + " "}equivalent"`
      + ` data-blurb="The time on a track ${track ? "5K (3200 m in middle school)" : esc(lb)} that this rating is worth.`
      + ` It reads the rating, not this race's course or distance."`
      + ` aria-label="Track equivalent: the time on a track this rating is worth">i</a></th>`;
  }

  /* ★ THE BOARD, REDESIGNED (owner, 2026-11-01): one row per college --
     its typical recruit as a time with the rating beside it, a bar over
     every recruit fastest to slowest with the typical recruit ticked and
     the reader marked, the recruit count, and the reader's band. */
  // cross country reads as the track 5K the athlete pages show
  const EV_LABEL = { "5k": "5K (track)", "800": "800m", "1600": "1600m", "3200": "3200m" };
  function evKey() { return state.sport === "XC" ? "5k" : $("r-ev").value; }
  function fitTag(t) {
    if (!t) return "";
    return `<span class="rc-fit rc-fit-${t.key}" title="${esc(t.blurb)}">${esc(t.label)}</span>`;
  }
  function boardRow(r, ev, ax, you) {
    const t = r.times || {}, tm = (k) => fmtTime((t[k] || {})[ev]);
    const x = (v) => (((ax.hi - v) / (ax.hi - ax.lo)) * 100).toFixed(1);
    const sub = ["division", "conference"].map((k) => unitBit(r, k)).filter(Boolean).join(" · ");
    const bar = (r.max != null && r.min != null) ? `
      <div class="rc-rb"><i style="left:${x(r.max)}%;width:${(x(r.min) - x(r.max)).toFixed(1)}%"></i>
        <b style="left:${x(r.median)}%"></b>${you != null ? `<u style="left:${x(you)}%"></u>` : ""}</div>
      <div class="rc-rbe"><span style="left:${x(r.max)}%">${tm("max")}</span><span style="left:${x(r.min)}%">${tm("min")}</span></div>` : "";
    return `<tr class="rc-br">
      <td class="rc-school">${crestMark(r.crest)}<a href="${schoolHref(r)}">${esc(r.label || r.school)}</a>${sub ? `<span class="rc-bsub">${sub}</span>` : ""}</td>
      <td class="num rc-btyp"><b>${tm("median") || num(r.median)}</b><span class="rc-brt">${num(r.median)}</span></td>
      <td class="rc-bbar">${bar}</td>
      <td class="num rc-bn">${r.n}</td>
      ${you != null ? `<td class="rc-bfit">${fitTag(r.tier)}</td>` : ""}
    </tr>`;
  }

  function paintBoard() {
    const d = state.last;
    if (!d) return;
    const rows = d.rows || [];
    const ev = evKey(), you = d.rating;
    const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    state.page = Math.min(state.page, pages - 1);
    const shown = rows.slice(state.page * PER_PAGE, (state.page + 1) * PER_PAGE);
    // one axis for the page: every bar, and the reader, on the same scale
    const vals = [];
    for (const r of shown) { if (r.max != null) vals.push(r.max); if (r.min != null) vals.push(r.min); }
    if (you != null) vals.push(you);
    const ax = { lo: Math.min(...vals) - 1, hi: Math.max(...vals) + 1 };
    const who = `${d.gender === "m" ? "men's" : "women's"} ${d.sport === "XC" ? "cross country" : "track"}`;
    const what = $("r-division").value ? esc($("r-division").value) + " " : "";
    const order = ($("r-sort").value === "closest" && you != null) ? `, closest to your ${esc(d.subject && d.subject.time ? d.subject.time : you.toFixed(1))} first`
      : "";
    const pager = pages > 1 ? `<div class="rc-pager"><span class="meta">${state.page * PER_PAGE + 1}–${state.page * PER_PAGE + shown.length} of ${rows.length}</span>
        <span><button type="button" class="rc-seg-btn" data-page="-1"${state.page ? "" : " disabled"}>← Prev</button>
        <button type="button" class="rc-seg-btn" data-page="1"${state.page < pages - 1 ? "" : " disabled"}>Next →</button></span></div>` : "";
    $("r-out").innerHTML = `
      <p class="meta rc-count"><b>${rows.length} ${what}${who} programs</b>${order}.</p>
      <div class="rc-legend"><span class="k1"></span>their recruits, fastest → slowest<span class="k2"></span>typical recruit${you != null ? '<span class="k3"></span>you' : ""}</div>
      <div class="r-scroll"><table class="rk rc-tbl rc-board"><thead><tr><th>College</th><th class="num">Typical recruit</th>
        <th>Recruits' ${EV_LABEL[ev]} times</th><th class="num">Recruits</th>${you != null ? "<th>You</th>" : ""}</tr></thead>
        <tbody>${shown.map((r) => boardRow(r, ev, ax, you)).join("")}</tbody></table></div>${pager}`;
    $("r-out").querySelectorAll("[data-page]").forEach((b) => b.addEventListener("click", () => {
      state.page += Number(b.dataset.page);
      paintBoard();
      $("r-out").scrollIntoView({ block: "start", behavior: "smooth" });
    }));
  }

  function render(d) {
    state.gender = d.gender; state.sport = d.sport;
    $("r-tgender").value = d.gender;
    paintSeg();
    renderSubject(d);
    if (!d.built) {
      $("r-out").innerHTML = `<p class="meta">The recruiting numbers aren't ready yet; check back tomorrow.</p>`;
      return;
    }
    if (!(d.rows || []).length) {
      $("r-out").innerHTML = `<p class="meta">No college matches. Widen the filters.</p>`;
      return;
    }
    state.last = d;
    paintBoard();
    const div = $("r-division");
    if (d.divisions && d.divisions.length && div.options.length <= 1) {
      for (const v of d.divisions) { const o = document.createElement("option"); o.value = v; o.textContent = v; div.appendChild(o); }
    }
  }

  /* ★ THE TRANSFERS SECTION (owner, 2026-10-30): the same filters, every
     college's transfers in and out, on the COLLEGE scale -- the in-transfers'
     first season at their new school -- from /api/recruiting/transfers. */
  function transferRow(r, withTier) {
    const href = schoolHref(r) + "#transfers";
    return `<tr>
      <td class="rc-school">${crestMark(r.crest)}<a href="${href}">${esc(r.label || r.school)}</a></td>
      <td class="rc-dim">${unitBit(r, "division")}</td>
      <td class="rc-dim">${unitBit(r, "conference")}</td>
      <td class="num">${r.n || 0}</td>
      <td class="num rc-dim">${r.n_out || 0}</td>
      <td class="num rc-dim">${num(r.before)}</td>
      <td class="num rc-strong">${num(r.median)}</td>
      <td class="num">${r.gain == null ? " - " : (r.gain > 0 ? "+" : "") + Number(r.gain).toFixed(1)}</td>
      <td class="num rc-dim">${num(r.max)}</td>
      <td class="fk">${r.median_5k ? esc(r.median_5k) + (r.median_5k_dist && r.median_5k_dist !== "5K" ? ` <span class="fk-d">${esc(r.median_5k_dist)}</span>` : "") : '<span class="fk-none">-</span>'}</td>
      ${withTier ? (r.tier ? tierCell(r.tier) : "<td></td>") : ""}
    </tr>`;
  }

  function transferHead(withTier) {
    return `<thead><tr><th>School</th><th>Division</th><th>Conference</th>
      <th class="num" title="athletes who arrived from another college, in the classes counted">In</th>
      <th class="num" title="athletes who left for another college">Out</th>
      <th class="num" title="the typical transfer's last season at their old college (HS-equivalent rating)">Before</th>
      <th class="num" title="the typical transfer's first season here (HS-equivalent rating)">Typical here</th>
      <th class="num" title="the typical change from the old college to this one">Change</th>
      <th class="num" title="the fastest transfer's first season here (HS-equivalent rating)">Fastest</th>
      ${fkTh("5K")}${withTier ? "<th>You</th>" : ""}</tr></thead>`;
  }

  function renderTransfers(d) {
    state.gender = d.gender; state.sport = d.sport;
    $("r-tgender").value = d.gender;
    paintSeg();
    renderSubject(d);
    const rows = (d.rows || []).filter((r) => r.n || r.n_out);
    if (!d.built) {
      $("r-out-t").innerHTML = `<p class="meta">The transfer numbers aren't ready yet; they arrive with the next recruiting build.</p>`;
      return;
    }
    if (!rows.length) {
      $("r-out-t").innerHTML = `<p class="meta">No college matches. Widen the filters.</p>`;
      return;
    }
    $("r-out-t").innerHTML = `<p class="meta rc-count">${rows.length} of ${d.total} ${d.gender === "m" ? "men's" : "women's"} ${d.sport === "XC" ? "cross country" : "track & field"} programmes with transfers. College seasons as HS-equivalent ratings, the scale the rest of the site shows: a transfer's last season before and first season here; the 5K is the typical transfer's.</p>
      <div class="r-scroll"><table class="rk rc-tbl">${transferHead(d.rating != null)}<tbody>${rows.map((r) => transferRow(r, d.rating != null)).join("")}</tbody></table></div>`;
  }

  async function load() {
    const q = query();
    $("r-ev").hidden = state.sport !== "TF" || state.view === "transfers";
    const closest = $("r-sort").querySelector('option[value="closest"]');
    if (closest) closest.hidden = state.view === "transfers";
    if (state.view === "transfers" && $("r-sort").value === "closest") $("r-sort").value = "median";
    // the sort menu speaks the section's noun
    const words = state.view === "transfers"
      ? { median: "Fastest typical transfer", top: "Fastest top transfers", n: "Most transfers in" }
      : { median: "Fastest typical recruit", top: "Fastest top recruits", n: "Most recruits" };
    for (const o of $("r-sort").options) if (words[o.value]) o.textContent = words[o.value];
    // and the finder says which season it places
    const hint = document.querySelector(".rc-hint");
    if (hint) hint.textContent = state.view === "transfers"
      ? "Your latest college season's rating is what gets placed among the transfers."
      : "Your latest high-school season's rating is what gets placed.";
    // ! THE HASH IS KEPT (owner, 2026-10-10): the page's tabs live in it
    //   (race-page.js), and dropping it broke /recruiting#fit links.
    try { history.replaceState(null, "", location.pathname + "?" + q.toString() + location.hash); } catch (e) { /* fine */ }
    // ★ SHARE SENDS THE LIVE URL (owner, 2026-10-10): a placement is the
    //   query string, so Share carries it rather than the bare page.
    document.querySelectorAll(".share-btn[data-share-url]").forEach((b) => {
      b.dataset.shareUrl = b.dataset.shareUrl.split(/[?#]/)[0] + "?" + q.toString();
    });
    status("Loading…", false);
    try {
      const api = state.view === "transfers" ? "/api/recruiting/transfers?" : "/api/recruiting/schools?";
      const res = await fetch(api + q.toString());
      const d = await readJson(res);
      if (!res.ok || d.error) { status(d.error || res.statusText || `The server had a problem (error ${res.status}).`, true, res.status >= 500 ? load : null); $(state.view === "transfers" ? "r-out-t" : "r-out").innerHTML = ""; return; }
      status("", false);
      state.page = 0;
      if (state.view === "transfers") renderTransfers(d); else render(d);
    } catch (err) {
      status(err.friendly ? err.message : "Could not reach the server. Check your connection and try again.", true, load);
      $("r-out").innerHTML = "";
    }
  }

  $("r-place").addEventListener("click", () => { $("r-athlete").value = ""; load(); });
  document.querySelectorAll(".rc-seg-btn").forEach((b) => b.addEventListener("click", () => {
    state[b.dataset.set] = b.dataset.value;
    paintSeg();
    load();
  }));
  for (const id of ["r-division", "r-state", "r-sort"]) $(id).addEventListener("change", load);
  // the track event only changes the times shown: no new request
  $("r-ev").addEventListener("change", () => { paintBoard(); try { history.replaceState(null, "", location.pathname + "?" + query().toString() + location.hash); } catch (e) { /* fine */ } });
  // Colleges / Transfers are tabs (race-page.js shows the panel); the data follows
  window.addEventListener("hashchange", () => {
    const v = viewOf();
    if (v !== state.view && (location.hash === "#transfers" || location.hash === "#schools" || location.hash === "")) { state.view = v; load(); }
  });
  for (const id of ["r-q", "r-time"]) {
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
