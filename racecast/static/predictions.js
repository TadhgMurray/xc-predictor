/*
 * predictions.js -- behaviour for the meet-first predictions page.
 *
 * Talks to:
 *   /search/api?kind=meet|school|athlete   the pickers, already indexed
 *   /api/predict/field                     who ran a meet, by school
 *   /api/predict/individual|team           the predictions themselves
 *
 * ★ THE STEPS REVEAL THEMSELVES. Step 2 appears when a meet is chosen, step 3
 *   when a date is settled. An empty form with every input visible asks the
 *   reader to work out the order; showing one thing at a time answers it.
 *
 * No framework, no build step.
 */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  meet: null,         // {id, div, sport, label, year, date}
  when: "thisyear",   // thisyear | asran
  who: "team",        // team | individual
  athletes: [],       // [{id, name}] -- several can be compared at once
  field: null,        // {season_year, teams:[...]}
  removed: new Set(),  // person_ids taken out of the field by hand
  open: new Set(),     // schools whose roster is expanded
  // ★ REMOVED TEAMS ARE KEPT, NOT DISCARDED. A destructive action with no way
  //   back makes people hesitate over every click; holding the team means undo
  //   is free and the button can stay a single tap.
  droppedTeams: [],
  // How much of the field is on screen: nothing, the team cards, or every
  // roster open. `open` still tracks individual cards the user toggled.
  view: "teams",
  added: [],          // {person_id, name, school} put back or brought in
  busy: false,
};


/*
 * Escape before interpolating into innerHTML.
 *
 * Athlete, school and meet names are SCRAPED FREE TEXT and genuinely contain &
 * and <. "Arcadia 'Holte' Invitational" and "Arcadia & Notre Dame" are both
 * real rows.
 */
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

/* Seconds -> 16:27.8. The model predicts seconds; a time is what people read. */
function fmtTime(s) {
  if (s === null || s === undefined) return "\u2014";
  const n = Number(s);
  const m = Math.floor(n / 60);
  return `${m}:${(n - m * 60).toFixed(1).padStart(4, "0")}`;
}


/* ------------------------------------------------------------------ *
 *  PICKERS
 * ------------------------------------------------------------------ */

/*
 * One search-as-you-type picker, reused three times. `render` turns the API
 * rows into markup, `onPick` decides what the choice does -- those are the
 * only things that differ between them.
 *
 * Debounced at 180ms: a request per keystroke fires a dozen for one name.
 */
function makePicker(inputId, boxId, kind, render, onPick) {
  const input = $(inputId);
  const box = $(boxId);
  let timer = null;

  async function search() {
    const q = input.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); return; }
    try {
      const res = await fetch(`/search/api?kind=${kind}&q=` + encodeURIComponent(q));
      const rows = (await res.json() || []).filter((r) => r.kind === kind);
      box.innerHTML = render(rows);
      box.classList.toggle("hidden", rows.length === 0);
    } catch (err) {
      box.classList.add("hidden");
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 180);
  });

  // mousedown, not click: blur fires first and would hide the list.
  box.addEventListener("mousedown", (e) => {
    const opt = e.target.closest(".pick-opt");
    if (!opt) return;
    e.preventDefault();
    onPick(opt.dataset);
    input.value = "";
    box.classList.add("hidden");
  });

  input.addEventListener("blur", () => {
    setTimeout(() => box.classList.add("hidden"), 150);
  });
}


/*
 * The sport and the meet id, out of whatever URL the search index returned.
 *
 * ⚠ A MEET LINKS TO /meet/xc/<id>, NOT /race/xc/<id>/<div>. The first version
 *   here only matched the RACE shape -- one division of one meet -- so every
 *   meet the picker returned failed to parse, parseMeetLink returned null, and
 *   chooseMeet quietly did nothing. Clicking a result appeared to do nothing
 *   at all, which is the worst way for this to fail: no error, no console, no
 *   clue.
 *
 *   Both shapes are accepted now, because /search/api returns both kinds and
 *   either is a usable target.
 *
 * ★ div IS OPTIONAL. A meet-level link has no division -- an XC meet has
 *   several (boys varsity, girls JV...) and the field endpoint treats a
 *   missing div_id as "all of them", which is the right default for
 *   re-running a whole meet.
 */
function parseMeetLink(link) {
  const race = /^\/race\/(xc|tf)\/(\d+)\/(\d+)(?:\/(\d+))?/.exec(link || "");
  if (race) {
    return { sport: race[1].toUpperCase(), id: race[2],
             div: race[1] === "tf" ? (race[4] || race[3]) : race[3] };
  }
  const meet = /^\/meet\/(xc|tf)\/(\d+)/.exec(link || "");
  if (meet) {
    return { sport: meet[1].toUpperCase(), id: meet[2], div: null };
  }
  return null;
}

/* A year out of the label or the sublabel. Meets are stored with it in the
   name ("2026 Arcadia Invitational") and it is the only thing that tells
   thirty editions apart. */
function meetYear(row) {
  const m = /\b(19|20)\d{2}\b/.exec(`${row.label} ${row.sublabel || ""}`);
  return m ? m[0] : "";
}


/*
 * ★ GROUPED BY YEAR, NEWEST FIRST. A name matches many editions and an
 *   ungrouped list interleaves them, so picking "the 2024 one" means reading
 *   every row. Undated meets sort last under their own heading rather than
 *   being dropped -- they are still real races.
 */
function renderMeets(rows) {
  const byYear = new Map();
  for (const r of rows.slice(0, 40)) {
    const y = meetYear(r) || "undated";
    if (!byYear.has(y)) byYear.set(y, []);
    byYear.get(y).push(r);
  }
  const years = [...byYear.keys()].sort((a, b) => {
    if (a === "undated") return 1;
    if (b === "undated") return -1;
    return Number(b) - Number(a);
  });

  return years.map((y) =>
    `<div class="pick-group">${esc(y)}</div>` +
    byYear.get(y).map((r) =>
      `<button class="pick-opt" data-link="${esc(r.link || "")}" ` +
      `data-label="${esc(r.label)}" data-year="${esc(y)}" ` +
      `data-sub="${esc(r.sublabel || "")}">${esc(r.label)}` +
      `<span class="pick-sub">${esc(r.sublabel || "")}</span></button>`
    ).join("")
  ).join("");
}

function renderSimple(rows) {
  return rows.slice(0, 8).map((r) =>
    `<button class="pick-opt" data-link="${esc(r.link || "")}" ` +
    `data-label="${esc(r.label)}">${esc(r.label)}` +
    `<span class="pick-sub">${esc(r.sublabel || "")}</span></button>`).join("");
}


/*
 * ★ THE SAME ADD / REMOVE AFFORDANCE AS THE SCHOOL SEARCH. An athlete already
 *   chosen used to be silently ignored on a second click, which looks broken.
 *   Naming the action -- and letting the same click undo it -- means one
 *   control does both and the list always reflects what is selected.
 */
function renderAthleteRows(rows) {
  return rows.slice(0, 8).map((r) => {
    const id = (/^\/athlete\/(\d+)/.exec(r.link || "") || [])[1];
    const chosen = id && state.athletes.some((a) => a.id === id);
    return `<button class="pick-opt${chosen ? " is-in" : ""}" ` +
      `data-link="${esc(r.link || "")}" data-label="${esc(r.label)}">` +
      `<span class="pick-name">${esc(r.label)}</span>` +
      `<span class="pick-act">${chosen ? "Remove" : "Add"}</span>` +
      `<span class="pick-sub">${esc(r.sublabel || "")}</span></button>`;
  }).join("");
}


/* ------------------------------------------------------------------ *
 *  STEPS
 * ------------------------------------------------------------------ */

function showStep(name, on) {
  document.querySelector(`.step[data-step="${name}"]`)
          .classList.toggle("hidden", !on);
}

async function chooseMeet(data) {
  const parsed = parseMeetLink(data.link);
  if (!parsed) {
    // ⚠ SAY SO. This used to `return` silently, so an unrecognised link made
    //   clicking a result do nothing whatsoever -- no message, no console, no
    //   way to tell a broken parse from a slow request.
    setStatus(`Could not read that meet's link (${data.link || "none"}).`, true);
    return;
  }

  // The sublabel carries the date where there is one; the label carries the
  // year. Either is enough to default the re-run date.
  const iso = /\b((19|20)\d{2})-(\d{2})-(\d{2})\b/.exec(data.sub || "");
  state.meet = { ...parsed, label: data.label, year: data.year,
                 date: iso ? iso[0] : null };

  /*
   * The answered state of step 1: the search box is replaced by what was
   * chosen, in the same place, with a way back.
   *
   * ★ THE YEAR IS PULLED OUT OF THE NAME. They are stored as "2025 Colorado
   *   State Championships", so leaving it inline means thirty editions read
   *   as thirty different meets. Stripped from the name, shown once as its
   *   own thing.
   */
  const bare = data.label.replace(/^\s*(19|20)\d{2}\s+/, "").trim();
  $("meet-chosen").innerHTML =
    `<div class="mc-main">
       <span class="mc-name">${esc(bare)}</span>
       ${state.meet.year ? `<span class="mc-year">${esc(state.meet.year)}</span>` : ""}
     </div>
     <div class="mc-sub">
       ${state.meet.date ? `ran ${esc(state.meet.date)} \u00b7 ` : ""}
       ${esc(state.meet.sport === "XC" ? "Cross Country" : "Track & Field")}
     </div>
     <button class="mc-change" data-clear="meet">Change</button>
     <div class="mc-races" id="mc-races"></div>`;
  $("meet-chosen").classList.remove("hidden");
  $("meet-search").classList.add("hidden");

  defaultDate();
  showStep("when", true);
  showStep("who", true);
  $("actions").classList.remove("hidden");

  loadRaces();
  await loadField();
}


/*
 * ★ A MEET IS SEVERAL RACES, AND MIXING THEM IS A RACE NOBODY RAN. An XC
 *   championship holds Boys D1 next to Girls D5 under one meet_id;
 *   predicting "the meet" merges fields that never raced each other. The
 *   picker defaults to one race when there is only one, and otherwise asks
 *   -- "All races" stays available because compiled predictions are still a
 *   thing people want.
 */
async function loadRaces() {
  const box = document.getElementById("mc-races");
  if (!box) return;
  box.innerHTML = "";
  try {
    const q = new URLSearchParams({ meet_id: state.meet.id,
                                    sport: state.meet.sport });
    const res = await fetch("/api/predict/races?" + q.toString());
    const data = await res.json();
    const races = (data.races || []);
    if (races.length < 2) {
      if (races.length === 1) state.meet.div = String(races[0].div_id);
      return;
    }
    const chip = (label, div, on) =>
      `<button class="race-chip${on ? " is-on" : ""}" data-div="${div}">` +
      `${esc(label)}</button>`;
    box.innerHTML =
      `<span class="mc-races-label">Race:</span>` +
      chip("All races", "", !state.meet.div) +
      races.map((r) => {
        const bits = [r.label];
        if (r.gender) bits.push(r.gender === "M" ? "Boys" : "Girls");
        if (r.distance) bits.push(`${Math.round(r.distance)}m`);
        return chip(`${bits.join(" \u00b7 ")} (${r.n_results})`,
                    String(r.div_id), state.meet.div === String(r.div_id));
      }).join("");
    box.querySelectorAll(".race-chip").forEach((b) => {
      b.addEventListener("click", () => {
        state.meet.div = b.dataset.div || null;
        box.querySelectorAll(".race-chip").forEach((x) =>
          x.classList.toggle("is-on", x === b));
        loadField();          // a different race is a different field
      });
    });
  } catch (err) { /* no picker is just the whole meet, as before */ }
}


/*
 * ★ SAME MONTH AND DAY, THIS YEAR. That is what "run it again" means -- a
 *   meet keeps its weekend far more reliably than its date. Editable, because
 *   they do move, and because the model reads day-of-year as a feature so the
 *   difference is not cosmetic.
 */
function defaultDate() {
  const now = new Date();
  const md = state.meet.date ? state.meet.date.slice(5) : null;
  const year = now.getFullYear();
  $("t-date").value = md ? `${year}-${md}`
                         : now.toISOString().slice(0, 10);

  $("asran-hint").textContent = state.meet.date
    ? `Predicts the ${state.meet.date} running of this meet, with the field `
      + `that actually raced it. The real result is already known, so the `
      + `prediction is scored against it.`
    : `Predicts this meet as it ran, with the field that actually raced it.`;
}


/* ------------------------------------------------------------------ *
 *  THE FIELD
 * ------------------------------------------------------------------ */

async function loadField() {
  $("field-summary").textContent = "Loading the field\u2026";
  $("field").innerHTML = "";
  state.removed.clear();
  state.added = [];

  const q = new URLSearchParams({ meet_id: state.meet.id,
                                  sport: state.meet.sport,
                                  when: state.when });
  // Omitted, not sent empty: URLSearchParams turns null into the STRING
  // "null", which the server would try to parse as a division id.
  if (state.meet.div) q.set("div_id", state.meet.div);
  try {
    const res = await fetch("/api/predict/field?" + q.toString());
    const data = await res.json();
    if (!res.ok) {
      $("field-summary").textContent = data.error || res.statusText;
      return;
    }
    state.field = data;
    renderField();
  } catch (err) {
    $("field-summary").textContent = "Could not load the field.";
  }
}


/*
 * ★ NON-RETURNERS ARE SHOWN, NOT HIDDEN. "Returner" here means "has raced this
 *   season", which drops a graduated senior automatically -- but it drops an
 *   injured athlete identically, and the data cannot tell them apart. Listing
 *   them greyed with an add button puts that judgement where it belongs.
 */
function renderField() {
  const f = state.field;
  const teams = f.teams.filter((t) => t.runners.length || t.dropped.length);
  const kept = teams.reduce((n, t) => n + t.runners.length, 0);

  $("field-summary").innerHTML =
    `<strong>${teams.length}</strong> teams, <strong>${kept}</strong> runners ` +
    (f.when === "asran"
      ? `\u2014 the field that actually raced this meet.`
      : `\u2014 each team's current squad, top ${7} predicted; the rest and ` +
        `the original runners without a ${esc(f.season_year)} season are ` +
        `listed to add by hand.`) +
    // ★ ONE CONTROL, THREE STATES, MUTUALLY EXCLUSIVE. "Expand all" and
    //   "Hide teams" were two independent toggles whose combinations did not
    //   all mean anything -- hidden-and-expanded is not a state, and neither
    //   button told you what the other had done. A single Show: control has
    //   exactly the three views that exist, and the selected one is visible
    //   without pressing anything.
    ` <span class="viewsel">Show:` +
    ["none", "teams", "all"].map((v) =>
      `<button class="vbtn${state.view === v ? " is-on" : ""}" data-view="${v}">` +
      `${ {none: "Nothing", teams: "Teams", all: "Rosters"}[v] }</button>`
    ).join("") + `</span>` +
    (state.droppedTeams.length
      ? ` <button class="linkish undo" id="undo-team">` +
        `Undo removing ${esc(state.droppedTeams.at(-1).school)}</button>`
      : "");

  /*
   * ★ ROSTERS ARE COLLAPSED BY DEFAULT. A championship meet is thirty teams
   *   at seven runners each -- 210 names -- and the common case is scanning
   *   the TEAMS, not reading every athlete. Open the ones you want to edit.
   *
   *   <details>, not a click handler: it is open/closed state the browser
   *   already owns, keyboard-operable for free, and it survives the fact that
   *   this whole block is re-rendered from scratch.
   */
  // "Nothing" keeps the summary line and its controls, so the field can be
  // brought back without losing the edits underneath.
  $("field").classList.toggle("hidden", state.view === "none");
  if (state.view === "none") { $("field").innerHTML = ""; return; }

  $("field").innerHTML = teams.map((t, i) => `
    <details class="team-card" data-team="${esc(t.school)}"
             ${state.open.has(t.school) ? "open" : ""}>
      <summary class="team-name">
        <span class="t-label">${esc(t.school)}</span>
        <span class="team-n">${t.runners.length}</span>
        <button class="team-x" data-drop-team="${esc(t.school)}"
                title="Remove this team">&times;</button>
      </summary>
      <div class="team-body">
        ${t.runners.map((r) => `
          <div class="runner-row" data-pid="${r.person_id}">
            <a class="r-name" href="/athlete/${r.person_id}"
               target="_blank" rel="noopener">${esc(r.name)}</a>
            <span class="r-rating">${r.rating === null ? "" : r.rating}</span>
            <button class="r-x" data-remove="${r.person_id}"
                    title="Remove">&times;</button>
          </div>`).join("")}
        <button class="squad-btn" data-squad="${esc(t.school)}">
          + Add from squad</button>
        <div class="squad-list hidden" data-squad-for="${esc(t.school)}"></div>
        <button class="squad-btn" data-anyone="${esc(t.school)}">
          + Add anyone</button>
        <div class="squad-list hidden" data-anyone-for="${esc(t.school)}"></div>
        ${t.dropped.length ? `
          <details class="dropped">
            <summary>${t.dropped.length} not racing this season</summary>
            ${t.dropped.map((r) => `
              <div class="runner-row is-out">
                <a class="r-name" href="/athlete/${r.person_id}"
                   target="_blank" rel="noopener">${esc(r.name)}</a>
                <button class="r-add" data-add="${r.person_id}"
                        data-name="${esc(r.name)}"
                        data-rating="${r.rating === null ? "" : r.rating}"
                        data-school="${esc(t.school)}">add</button>
              </div>`).join("")}
          </details>` : ""}
      </div>
    </details>`).join("");
}


/* ------------------------------------------------------------------ *
 *  PREDICT
 * ------------------------------------------------------------------ */

function whatIsMissing() {
  if (!state.meet) return "Pick a meet.";
  if (state.who === "individual" && !state.athletes.length)
    return "Pick at least one athlete.";
  if (state.when === "thisyear" && !$("t-date").value) return "Pick a date.";
  return null;
}

function buildQuery() {
  const q = new URLSearchParams({
    mode: state.when === "asran" ? "rerun_exact" : "rerun",
    meet_id: state.meet.id,
    sport: state.meet.sport,
  });
  if (state.meet.div) q.set("div_id", state.meet.div);
  if (state.when === "thisyear") q.set("date", $("t-date").value);

  if (state.who === "individual") {
    // One parameter, one or many values -- the endpoint splits it.
    q.set("person_id", state.athletes.map((a) => a.id).join(","));
  } else {
    if ($("head_to_head").checked) q.set("head_to_head", "1");
    // Only the edits are sent. The server already knows the meet's own field,
    // so shipping the whole roster back would be a large request that says
    // the same thing.
    if (state.removed.size) q.set("remove", [...state.removed].join(","));
    if (state.added.length)
      q.set("add", state.added.map((a) => a.person_id).join(","));
  }
  return q;
}

async function predict() {
  const missing = whatIsMissing();
  if (missing) { setStatus(missing, true); return; }
  if (state.busy) return;

  state.busy = true;
  $("predict").disabled = true;
  setStatus("Predicting\u2026", false);
  $("output").innerHTML = "";

  const path = state.who === "individual"
    ? "/api/predict/individual" : "/api/predict/team";

  try {
    const res = await fetch(path + "?" + buildQuery().toString());
    const data = await res.json();
    if (!res.ok) { setStatus(data.error || res.statusText, true); return; }

    // available:false is the expected answer until the model is trained, and
    // it carries its own reason. Not an error.
    if (data.available === false) {
      setStatus(data.reason || "Not available yet.", false);
      return;
    }
    setStatus("", false);
    $("output").innerHTML = state.who === "individual"
      ? renderIndividual(data) : renderTeam(data);
  } catch (err) {
    setStatus("Could not reach the server: " + err.message, true);
  } finally {
    state.busy = false;
    $("predict").disabled = false;
  }
}

function setStatus(msg, isError) {
  const el = $("status");
  el.textContent = msg || "";
  el.className = "predict-status" + (msg ? " show" : "") + (isError ? " error" : "");
}


/* ------------------------------------------------------------------ *
 *  RESULTS
 * ------------------------------------------------------------------ */

/*
 * ★ THE BAND IS SHOWN, NOT JUST THE NUMBER. A single race carries about +-4
 *   rating points -- roughly 3.3% -- so a time quoted to a tenth with no range
 *   claims a precision the data does not have.
 *
 * ★ AND THE ACTUAL RESULT, WHEN THERE IS ONE. In "as it ran" mode the answer
 *   is already in the database, so the page shows it beside the prediction
 *   with the error between them. A prediction you cannot check is not
 *   evidence of anything.
 */
function renderAthleteSet(d) {
  /* Several athletes at one race: a table, sorted fastest first, because the
     question people ask with two names is "who wins". */
  const rows = [...(d.athletes || [])]
    .filter((a) => a.seconds !== undefined)
    .sort((a, b) => a.seconds - b.seconds)
    .map((a, i) => `<tr><td class="rank">${i + 1}</td>
      <td>${esc(a.name || "")}</td>
      <td>${fmtTime(a.seconds)}</td>
      <td>${a.low !== undefined
            ? `${fmtTime(a.low)} \u2013 ${fmtTime(a.high)}` : ""}</td></tr>`)
    .join("");
  return `<table class="rk"><thead><tr>
      <th>#</th><th>Athlete</th><th>Predicted</th><th>Range</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}


function renderIndividual(d) {
  if (d.athletes) return renderAthleteSet(d);
  const err = d.actual !== undefined && d.actual !== null
    ? `<div class="scored">actual ${fmtTime(d.actual)}
         <em>${d.seconds > d.actual ? "+" : ""}${(d.seconds - d.actual).toFixed(1)}s</em></div>`
    : "";
  return `<div class="result">
    <div class="result-big">${fmtTime(d.seconds)}</div>
    ${d.low !== undefined
      ? `<div class="result-band">likely ${fmtTime(d.low)} \u2013 ${fmtTime(d.high)}</div>` : ""}
    ${err}
  </div>`;
}

function renderTeam(d) {
  const scored = (d.teams || []).some((t) => t.actual_score !== undefined
                                          && t.actual_score !== null);
  const note = d.mode === "head_to_head"
    ? `<p class="hint">Scored as if only these teams raced.</p>`
    : `<p class="hint">Scored against the full field.</p>`;

  const rows = (d.teams || []).map((t, i) => `
    <tr>
      <td class="rank">${t.score === null ? "\u2014" : i + 1}</td>
      <td>${esc(t.team)}</td>
      <td>${t.score === null ? esc(t.note || "incomplete") : t.score}</td>
      ${scored ? `<td class="actual">${t.actual_score ?? "\u2014"}</td>` : ""}
      <td class="runners">${(t.runners || []).map((r) =>
        /* ! score_place, NOT place -- the number the points are summed from.
             A complete team's displayed places have to add up to its own
             score, and they only do once unattached runners and incomplete
             teams are lifted out. An incomplete team has no scoring place,
             so it falls back to where its runners finish. */
        `<span class="runner">${r.score_place || r.place}. ${esc(r.name || "")}` +
        ` <em>${fmtTime(r.seconds)}</em></span>`).join("")}</td>
    </tr>`).join("");

  return note + `<table class="rk">
    <thead><tr><th>#</th><th>Team</th><th>Predicted</th>
      ${scored ? "<th>Actual</th>" : ""}<th>Scorers</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}


/* ------------------------------------------------------------------ *
 *  WIRING
 * ------------------------------------------------------------------ */

makePicker("meet-input", "meet-results", "meet", renderMeets, chooseMeet);

makePicker("athlete-input", "athlete-results", "athlete", renderAthleteRows, (d) => {
  const m = /^\/athlete\/(\d+)/.exec(d.link || "");
  if (!m) {
    setStatus(`Could not read that athlete's link (${d.link || "none"}).`, true);
    return;
  }
  // Same control both ways: clicking a chosen athlete takes them out again.
  if (state.athletes.some((a) => a.id === m[1])) {
    state.athletes = state.athletes.filter((a) => a.id !== m[1]);
  } else {
    state.athletes.push({ id: m[1], name: d.label });
  }
  renderAthletes();
});

function renderAthletes() {
  $("athlete-chosen").innerHTML = state.athletes.map((a) =>
    `<span class="chip">${esc(a.name)}` +
    `<button class="chip-x" data-drop-athlete="${esc(a.id)}">&times;</button></span>`
  ).join("");
  $("athlete-chosen").classList.toggle("hidden", state.athletes.length === 0);
}

/*
 * ★ A TEAM ALREADY IN THE FIELD SHOWS AS SUCH, AND CLICKING REMOVES IT.
 *   Silently ignoring a second click looks broken; hiding the row makes the
 *   search lie about what exists. Marking it and letting the same click undo
 *   it means one control does both, and the list always reflects the field.
 */
function teamIsIn(school) {
  return (state.field?.teams || []).some((t) => t.school === school)
      || state.added.some((a) => a.school === school);
}

function renderSchools(rows) {
  return rows.slice(0, 8).map((r) => {
    const inField = teamIsIn(r.label);
    /* ★ THE ACTION IS NAMED, AND IT IS THE SAME CONTROL BOTH WAYS. A row that
       is already in the field says "Remove"; one that is not says "Add". The
       earlier version said "in field · remove", which described a STATE and an
       action at once and read as neither. */
    return `<button class="pick-opt${inField ? " is-in" : ""}" ` +
      `data-label="${esc(r.label)}" data-link="${esc(r.link || "")}">` +
      `<span class="pick-name">${esc(r.label)}</span>` +
      `<span class="pick-act">${inField ? "Remove" : "Add"}</span>` +
      `<span class="pick-sub">${esc(r.sublabel || "")}</span></button>`;
  }).join("");
}

makePicker("school-input", "school-results", "school", renderSchools, (d) => {
  const school = d.label;

  if (teamIsIn(school)) {
    // Remove: drop it from the field and from any pending addition, and mark
    // its runners removed so the request says the same thing either way.
    const team = (state.field?.teams || []).find((t) => t.school === school);
    if (team) for (const r of team.runners) state.removed.add(String(r.person_id));
    if (state.field)
      state.field.teams = state.field.teams.filter((t) => t.school !== school);
    state.added = state.added.filter((a) => a.school !== school);
    renderField();
    setStatus(`Removed ${school}.`, false);
    return;
  }

  addTeam(school);
});


/*
 * ★ AN ADDED TEAM APPEARS IN THE FIELD, NOT AS A PROMISE. The first version
 *   recorded the school name and told the reader their runners would be
 *   "resolved when you predict" -- so the roster you were looking at was not
 *   the roster you would get, and there was nothing to edit. Fetching the
 *   squad puts a real card in the grid, with the same seven-and-edit
 *   behaviour as every other team.
 */
async function addTeam(school) {
  if (!state.field) return;
  setStatus(`Loading ${school}\u2026`, false);
  try {
    const squad = await loadSquad(school);
    if (!squad.runners.length) {
      setStatus(`No one from ${school} has raced this season.`, true);
      return;
    }
    state.field.teams.push({
      school: school,
      runners: squad.runners.slice(0, 7),
      // The rest of the squad, offered under the card rather than discarded --
      // "add anyone from their squad" is the point of having fetched it.
      dropped: squad.runners.slice(7),
      added: true,
    });
    state.field.teams.sort((a, b) => a.school.localeCompare(b.school));
    state.open.add(school);          // open it: it is the thing just added
    for (const r of squad.runners.slice(0, 7))
      state.added.push({ person_id: r.person_id, name: r.name, school: school });
    renderField();
    setStatus("", false);
  } catch (err) {
    setStatus(`Could not load ${school}.`, true);
  }
}


/* One fetch per school, cached: a card can be opened and closed repeatedly,
   and the squad does not change while the page is open. */
const squadCache = new Map();

async function loadSquad(school) {
  if (squadCache.has(school)) return squadCache.get(school);
  const q = new URLSearchParams({ school: school, sport: state.meet.sport });
  const res = await fetch("/api/predict/squad?" + q.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  squadCache.set(school, data);
  return data;
}

document.querySelectorAll(".card[data-when]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const changed = state.when !== btn.dataset.when;
    state.when = btn.dataset.when;
    document.querySelectorAll(".card[data-when]").forEach((b) =>
      b.classList.toggle("is-on", b === btn));
    document.querySelectorAll(".when-pane").forEach((p) =>
      p.classList.toggle("hidden", p.dataset.pane !== state.when));
    // The WHEN decides WHO: "as it ran" is the original field, "this
    // year" is the current squads -- so flipping it reloads the field
    // (and clears the edits, which described the other population).
    if (changed && state.meet) loadField();
  });
});

document.querySelectorAll(".card[data-who]").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.who = btn.dataset.who;
    document.querySelectorAll(".card[data-who]").forEach((b) =>
      b.classList.toggle("is-on", b === btn));
    document.querySelectorAll(".who-pane").forEach((p) =>
      p.classList.toggle("hidden", p.dataset.pane !== state.who));
    $("output").innerHTML = "";
    setStatus("", false);
  });
});

document.addEventListener("click", (e) => {
  /* ★ A WHOLE TEAM AT ONCE. Removing seven runners one at a time to drop a
     team that is not coming is the single most tedious thing on this page.
     Every one of its person_ids goes into `removed`, so the request says the
     same thing it would have said either way.

     preventDefault because the button lives inside a <summary> -- without it
     the click also toggles the disclosure open on its way out. */
  const dropTeam = e.target.closest("[data-drop-team]");
  if (dropTeam) {
    e.preventDefault();
    const school = dropTeam.dataset.dropTeam;
    const team = (state.field?.teams || []).find((t) => t.school === school);
    if (team) {
      for (const r of team.runners) state.removed.add(String(r.person_id));
      state.field.teams = state.field.teams.filter((t) => t.school !== school);
      state.droppedTeams.push(team);
      renderField();
    }
    return;
  }

  /* ★ "ADD ANYONE": THE TRANSFER CASE (owner, 2026-09-01). "Add from squad"
     can only offer people the DATA already places at this school, so a
     transfer -- the exact person a human is most likely to be correcting for
     -- was unreachable. This searches every athlete instead.

     ! THE SERVER ALREADY ACCEPTED THIS. _teamRosters takes an `add` set and
       _athleteEntries resolves arbitrary person_ids, falling back to the
       athletes table for anyone without a season row. Only the UI was
       missing, so nothing server-side changes.
     ! person_id COMES OUT OF THE LINK, the way compare.js does it --
       /search/api returns {kind,label,sublabel,link} and the link is
       /athlete/<id>. */
  const anyone = e.target.closest("[data-anyone]");
  if (anyone) {
    const school = anyone.dataset.anyone;
    const box = document.querySelector(
      `[data-anyone-for="${CSS.escape(school)}"]`);
    if (!box) return;
    if (!box.classList.contains("hidden")) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    box.innerHTML =
      `<input class="squad-find" type="search" autocomplete="off"
              placeholder="Search every athlete\u2026"
              aria-label="Search every athlete">
       <div class="squad-rows"></div>`;
    const find = box.querySelector(".squad-find");
    const rows = box.querySelector(".squad-rows");
    let timer = null;
    find.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = find.value.trim();
        if (q.length < 2) { rows.innerHTML = ""; return; }
        try {
          const res = await fetch("/search/api?kind=athlete&q="
                                  + encodeURIComponent(q));
          const hits = (await res.json() || [])
            .filter((r) => r.kind === "athlete");
          rows.innerHTML = hits.length
            ? hits.map((r) => {
                const m = /\/athlete\/(\d+)/.exec(r.link || "");
                if (!m) return "";
                return `<div class="runner-row is-out">
                   <a class="r-name" href="/athlete/${m[1]}"
                      target="_blank" rel="noopener">${esc(r.label)}</a>
                   <span class="r-rating">${esc(r.sublabel || "")}</span>
                   <button class="r-add" data-add="${m[1]}"
                           data-name="${esc(r.label)}" data-rating=""
                           data-school="${esc(school)}">add</button>
                 </div>`;
              }).join("")
            : `<div class="squad-loading">No athlete by that name.</div>`;
        } catch (err) {
          rows.innerHTML =
            `<div class="squad-loading">Could not search.</div>`;
        }
      }, 180);
    });
    find.focus();
    return;
  }

  /* "Add from squad": everyone racing for this school, minus whoever is
     already on the card. Fetched on demand -- thirty teams is thirty requests
     if this were eager, and most cards are never opened. */
  const sq = e.target.closest("[data-squad]");
  if (sq) {
    const school = sq.dataset.squad;
    const list = document.querySelector(`[data-squad-for="${CSS.escape(school)}"]`);
    if (!list) return;
    if (!list.classList.contains("hidden")) {
      list.classList.add("hidden");
      return;
    }
    list.classList.remove("hidden");
    list.innerHTML = `<div class="squad-loading">Loading\u2026</div>`;
    loadSquad(school).then((squad) => {
      const team = (state.field?.teams || []).find((t) => t.school === school);
      const have = new Set((team?.runners || []).map((r) => String(r.person_id)));
      const rest = squad.runners.filter((r) => !have.has(String(r.person_id)));
      /* ★ ISSUE #84: A SEARCH, NOT A WALL. schoolSquad returns up to forty
         names and the card is already crowded, so scanning for one runner
         meant reading all of them. The filter is client-side because the
         whole squad is already in hand -- a round trip per keystroke would
         be slower and would fight the "fetched on demand" design above. */
      const rows = (items) => items.length
        ? items.map((r) =>
            `<div class="runner-row is-out">
               <a class="r-name" href="/athlete/${r.person_id}"
                  target="_blank" rel="noopener">${esc(r.name)}</a>
               <span class="r-rating">${r.rating}</span>
               <button class="r-add" data-add="${r.person_id}"
                       data-name="${esc(r.name)}"
                       data-rating="${r.rating === null ? "" : r.rating}"
                       data-school="${esc(school)}">add</button>
             </div>`).join("")
        : `<div class="squad-loading">No runner by that name.</div>`;

      if (!rest.length) {
        list.innerHTML =
          `<div class="squad-loading">Everyone racing is already listed.</div>`;
        return;
      }
      list.innerHTML =
        `<input class="squad-find" type="search" autocomplete="off"
                placeholder="Search ${esc(school)}'s squad\u2026"
                aria-label="Search this squad">
         <div class="squad-rows">${rows(rest)}</div>`;
      const find = list.querySelector(".squad-find");
      const body = list.querySelector(".squad-rows");
      find.addEventListener("input", () => {
        const q = find.value.trim().toLowerCase();
        body.innerHTML = rows(
          q ? rest.filter((r) => r.name.toLowerCase().includes(q)) : rest);
      });
      find.focus();
    }).catch(() => {
      list.innerHTML = `<div class="squad-loading">Could not load the squad.</div>`;
    });
    return;
  }

  const rm = e.target.closest("[data-remove]");
  if (rm) {
    state.removed.add(rm.dataset.remove);
    rm.closest(".runner-row").remove();
    return;
  }
  const add = e.target.closest("[data-add]");
  if (add) {
    /* ★ THE RUNNER GOES ONTO THE CARD, not just into a list of intentions.
       The first version only greyed the button and pushed an id -- so the
       roster on screen still showed seven while the request would send eight,
       and there was no way to see or undo what you had added. */
    const school = add.dataset.school;
    const pid = add.dataset.add;
    const team = (state.field?.teams || []).find((t) => t.school === school);
    if (team && !team.runners.some((r) => String(r.person_id) === String(pid))) {
      /* ★ THE RATING COMES WITH THEM. It was hardcoded null, so an added
         runner showed a blank where every other row has a number -- and the
         rating is how you judge whether adding them was right. Empty string
         means genuinely unrated (someone who has not raced this season),
         which stays blank on purpose. */
      const rating = add.dataset.rating;
      team.runners.push({
        person_id: pid,
        name: add.dataset.name,
        rating: rating === "" || rating === undefined ? null : Number(rating),
        added: true,
      });
      team.dropped = (team.dropped || [])
        .filter((r) => String(r.person_id) !== String(pid));
      // Sorted like every other card: best first, unrated last. Appending
      // would drop a 138 below a 132 purely because it was added later.
      team.runners.sort((a, b) =>
        (b.rating ?? -Infinity) - (a.rating ?? -Infinity));
    }
    state.removed.delete(pid);
    state.added.push({ person_id: pid, name: add.dataset.name, school: school });
    state.open.add(school);          // keep the card you are editing open
    renderField();
    return;
  }
  const x = e.target.closest(".chip-x, .mc-change");
  if (!x) return;
  if (x.dataset.dropAthlete) {
    state.athletes = state.athletes.filter((a) => a.id !== x.dataset.dropAthlete);
    renderAthletes();
  } else if (x.dataset.clear === "meet") {
    state.meet = null;
    state.field = null;
    $("meet-chosen").classList.add("hidden");
    $("meet-search").classList.remove("hidden");
    $("meet-input").value = "";
    showStep("when", false);
    showStep("who", false);
    $("actions").classList.add("hidden");
    $("output").innerHTML = "";
  }
});

/* ★ THE OPEN/CLOSED STATE HAS TO SURVIVE A RE-RENDER. renderField() rebuilds
   the whole grid, so removing one team used to snap every other roster shut --
   which is exactly when you are least likely to want that, since you opened it
   to decide what to remove. `toggle` fires on <details>, so the browser still
   owns the interaction and this only records it.

   Capture phase: `toggle` does not bubble. */
/* Collapse-all / expand-all, and undo. Delegated from the summary line, which
   is re-rendered on every field change. */
$("field-summary").addEventListener("click", (e) => {
  const vb = e.target.closest("[data-view]");
  if (vb) {
    state.view = vb.dataset.view;
    // Picking a view sets every card, which is what makes the three states
    // exclusive -- otherwise "Rosters" would leave cards the user had shut.
    state.open.clear();
    if (state.view === "all")
      for (const t of state.field?.teams || []) state.open.add(t.school);
    renderField();
    return;
  }
  if (e.target.id === "undo-team") {
    const team = state.droppedTeams.pop();
    if (!team) return;
    for (const r of team.runners) state.removed.delete(String(r.person_id));
    state.field.teams.push(team);
    state.field.teams.sort((a, b) => a.school.localeCompare(b.school));
    renderField();
  }
});

$("field").addEventListener("toggle", (e) => {
  const card = e.target.closest(".team-card");
  if (!card) return;
  if (card.open) state.open.add(card.dataset.team);
  else state.open.delete(card.dataset.team);
}, true);

$("predict").addEventListener("click", predict);