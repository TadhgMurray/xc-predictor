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
  // ★ SEVERAL DIVISIONS, EACH ITS OWN RACE (issue #85). The divisions picked
  //   to be predicted. Empty means the whole meet, which is what a single
  //   unpicked race has always meant. `meet.div` stays what it always was:
  //   the ONE division whose field is on screen to be edited.
  divs: [],
  // "separate" scores each picked division on its own (issue #85);
  // "combined" scores the whole meet as one race.
  raceMode: "separate",
  // ★ OFF BY DEFAULT. Two entries is what actually happened on the day;
  //   merging them is the what-if, and a what-if should be asked for.
  coalesce: false,
  // How much of the field is on screen: nothing, the team cards, or every
  // roster open. `open` still tracks individual cards the user toggled.
  view: "teams",
  // ★ null MEANS THE MEET'S OWN COURSE. There is no "unset" to represent --
  //   clearing the box is how you go back, so absence is the default rather
  //   than a sentinel the query has to strip out.
  course: null,
  busy: false,
};


/* ------------------------------------------------------------------ *
 *  PER-DIVISION EDITS
 * ------------------------------------------------------------------ */

/*
 * ★ EVERY DIVISION KEEPS ITS OWN FIELD AND ITS OWN EDITS (issue #85). D1 and
 *   D2 are separate races: removing a team from one must not remove it from
 *   the other, and each is scored on its own.
 *
 * ! THE ACCESSORS BELOW ARE WHY THIS IS A SMALL CHANGE. state.field,
 *   state.removed, state.added, state.open and state.droppedTeams used to be
 *   plain properties, read in thirty places. They are now getters onto the
 *   record for the division being edited, so every one of those call sites
 *   keeps working unchanged and quietly became per-division.
 */
const _edits = new Map();

/*
 * Add or remove one division from the selection.
 *
 * ! "ALL RACES" IS EXCLUSIVE. Picking it clears the rest, and picking any
 *   division clears it -- "everything at this meet" and "these two races"
 *   are different questions and holding both would answer neither.
 * ! REMOVING THE LAST ONE FALLS BACK TO ALL RACES rather than to an empty
 *   selection that predicts nothing.
 */
function toggleDiv(divs, div) {
  if (div === null) return [];                    // "All races"
  return divs.includes(div) ? divs.filter((d) => d !== div)
                            : divs.concat([div]);
}

/* Say what the current mode will actually do, in the terms of what is
   picked -- "combined" with nothing picked and with two picked are the same
   request, and that is worth stating rather than leaving to be discovered. */
function updateModeHint() {
  const el = $("mc-mode-hint");
  if (!el) return;
  const n = state.divs.length;
  const co = $("mc-coalesce");
  if (co) co.classList.toggle("hidden",
                              !(state.raceMode === "combined" && n > 1));
  /* ★ EVERY MESSAGE IS ONE SHORT LINE (owner, 2026-09-01: the jolt, third
     attempt). Reserving height for a hint that swung between one line and
     three was treating the symptom -- the real fix is that it does not swing.
     What a coalesced school does is now written on the CHECKBOX, where it is
     static, instead of being appended here where it was not. */
  el.textContent = state.raceMode === "combined"
    ? (n > 1 ? `Scoring ${n} divisions as one race.`
             : "Scoring the whole meet as one race.")
    : (n > 1 ? `Scoring ${n} divisions separately.`
             : "Scoring each picked division on its own.");
}

/* The name a division goes by, for a result heading. Falls back to the id so
   a section is never headed by nothing. */
const _divLabels = new Map();
function divLabel(div) {
  if (div === null || div === undefined) return "All races";
  return _divLabels.get(String(div)) || `Division ${div}`;
}

/* "" is the whole meet -- a real key, not a missing one. */
function divKey(div) {
  return div === null || div === undefined ? "" : String(div);
}

function editsFor(div) {
  const k = divKey(div);
  if (!_edits.has(k)) {
    _edits.set(k, { field: null, removed: new Set(), open: new Set(),
                    // ★ REMOVED TEAMS ARE KEPT, NOT DISCARDED. A destructive
                    //   action with no way back makes people hesitate over
                    //   every click; holding the team means undo is free.
                    droppedTeams: [], added: [] });
  }
  return _edits.get(k);
}

/* The union of several divisions' edits, for a combined race. Read-only:
   nothing writes through it, so the per-division records stay the truth. */
function mergedEdits(divs) {
  const removed = new Set();
  const added = [];
  const seen = new Set();
  for (const d of divs) {
    const e = editsFor(d);
    e.removed.forEach((x) => removed.add(x));
    for (const a of e.added) {
      // ! ONE ROW PER PERSON: a runner added to two divisions is still one
      //   person in a single combined race.
      if (seen.has(String(a.person_id))) continue;
      seen.add(String(a.person_id));
      added.push(a);
    }
  }
  return { removed, added };
}

/* ------------------------------------------------------------------ *
 *  SESSION PERSISTENCE
 * ------------------------------------------------------------------ */

/*
 * ★ WHY THIS EXISTS. Every name on this page is a link, and they open in the
 *   SAME tab -- so a click used to destroy a half-built prediction: the
 *   field, the removals, the added runners, the picked divisions, all of it
 *   lived only in memory. They were opened in a new tab to dodge that, which
 *   is a workaround for missing state, not a design.
 *
 * ! sessionStorage, NOT localStorage. This belongs to one tab's visit; two
 *   tabs predicting different meets must not overwrite each other, and none
 *   of it should still be here tomorrow.
 * ! EVERY ACCESS IS WRAPPED. Private mode and quota both throw, and a page
 *   that cannot remember is worth far more than one that will not load.
 */
const SESSION_KEY = "rc-predict-v1";

/* ! THROTTLED, AND THE FIELDS ARE NOT IN IT. renderField calls this, and
     renderField runs several times a load -- serialising every cached field
     each time was most of the cost of picking a second division. The fields
     are refetchable, so only the EDITS are stored and a restored session
     loads them again. What is kept is small and constant-size. */
let _saveTimer = null;
function saveState() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(writeState, 250);
}

function writeState() {
  try {
    const edits = {};
    for (const [k, e] of _edits) {
      edits[k] = { removed: [...e.removed], open: [...e.open],
                   droppedTeams: e.droppedTeams, added: e.added };
    }
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({
      meet: state.meet, when: state.when, who: state.who,
      divs: state.divs, raceMode: state.raceMode,
      coalesce: state.coalesce, course: state.course,
      athletes: state.athletes, view: state.view,
      date: $("t-date") ? $("t-date").value : null,
      labels: [..._divLabels], edits,
    }));
  } catch (err) { /* nothing here is worth breaking the page for */ }
}

function restoreState() {
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
  } catch (err) { return; }
  if (!saved || !saved.meet) return;

  state.meet = saved.meet;
  state.when = saved.when || "thisyear";
  state.who = saved.who || "team";
  state.divs = saved.divs || [];
  state.raceMode = saved.raceMode || "separate";
  state.coalesce = !!saved.coalesce;
  state.course = saved.course || null;
  state.athletes = saved.athletes || [];
  state.view = saved.view || "teams";

  resetEdits();
  for (const [k, e] of Object.entries(saved.edits || {})) {
    const rec = editsFor(k === "" ? null : k);
    rec.removed = new Set(e.removed || []);
    rec.open = new Set(e.open || []);
    rec.droppedTeams = e.droppedTeams || [];
    rec.added = e.added || [];
  }
  _divLabels.clear();
  for (const [k, v] of (saved.labels || [])) _divLabels.set(k, v);

  const bare = (state.meet.label || "")
    .replace(/^\s*(19|20)\d{2}\s+/, "").trim();
  renderChosenMeet(bare);
  $("meet-chosen").classList.remove("hidden");
  $("meet-search").classList.add("hidden");
  showStep("when", true);
  showStep("who", true);
  $("actions").classList.remove("hidden");

  if (saved.date && $("t-date")) $("t-date").value = saved.date;
  /* ! THE BOX SHOWS ONLY A COURSE THAT WAS ACTUALLY PICKED. Leaving typed
       text in it after a reload was the "still in the search bar but not
       picked" report -- it looked chosen and was not. */
  if ($("t-course")) $("t-course").value = state.course || "";

  loadRaces();            // rebuilds the chips, and re-applies the mode
  /* The fields are not stored -- they are refetched, which is also what keeps
     a restored session from showing a roster that has since changed. */
  loadField();
}

/* Forget every division's edits -- a different meet is a different world. */
function resetEdits() { _edits.clear(); }

Object.defineProperties(state, {
  field:        { get: () => editsFor(state.meet && state.meet.div).field,
                  set: (v) => { editsFor(state.meet && state.meet.div).field = v; } },
  removed:      { get: () => editsFor(state.meet && state.meet.div).removed },
  open:         { get: () => editsFor(state.meet && state.meet.div).open },
  droppedTeams: { get: () => editsFor(state.meet && state.meet.div).droppedTeams,
                  set: (v) => { editsFor(state.meet && state.meet.div).droppedTeams = v; } },
  added:        { get: () => editsFor(state.meet && state.meet.div).added,
                  set: (v) => { editsFor(state.meet && state.meet.div).added = v; } },
});


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
/* The id-taking form, for the pickers that live in the template. */
function makePicker(inputId, boxId, kind, render, onPick, keepValue) {
  bindPicker($(inputId), $(boxId), kind, render, onPick, keepValue);
}

/*
 * ★ BOUND TO NODES, NOT IDS (owner: "there is only one search bar for
 *   multiple divisions"). Each race on screen gets its own Add/Remove box,
 *   which means the box is markup renderField creates and destroys, so it
 *   cannot be addressed by a fixed id.
 */
function bindPicker(input, box, kind, render, onPick, keepValue) {
  if (!input || !box) return;
  let timer = null;

  async function search() {
    const q = input.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); return; }
    try {
      /* ⚠ THE DEFAULT LIMIT IS 10, AND THAT IS A SHORT LIST FOR A NAME MANY
           SCHOOLS SHARE. search_index splits a school into one row per state,
           so "De La Salle" is a dozen rows before any other school matches --
           and the ordering is by athlete count, not by how well the name fits.
           A row that was visible on a shorter query can be pushed off the end
           by a longer one, which is what "it shows up until I type the e"
           looks like. Asking for more costs one indexed scan and the box only
           ever renders eight. */
      const res = await fetch(`/search/api?kind=${kind}&limit=30&q=`
                              + encodeURIComponent(q));
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
    /* ★ CLEARING IS RIGHT FOR AN ACTION, WRONG FOR A SELECTION. Picking a
       meet, a school or an athlete DOES something and the box goes back to
       being a search box. Picking a course SETS a value that has to stay
       visible -- otherwise the only sign it worked is a line of hint text
       underneath, which is what the owner reported. */
    input.value = keepValue ? (opt.dataset.label || "") : "";
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
  /* A different meet is a different world: its divisions, its fields and
     every edit made to them belong to the old one. */
  state.divs = [];
  resetEdits();
  _divLabels.clear();
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
  renderChosenMeet(bare);
  $("meet-chosen").classList.remove("hidden");
  $("meet-search").classList.add("hidden");

  defaultDate();
  showStep("when", true);
  showStep("who", true);
  $("actions").classList.remove("hidden");

  loadRaces();
  await loadField();
  saveState();
}


/* ★ THE CHOSEN-MEET BLOCK, RENDERED FROM state.meet ALONE -- so a restored
   session can rebuild it without the search row it was first chosen from.
   ! THE NAMES ARE LINKS and they open in the SAME tab now. saveState is what
     makes that safe: the field, the edits and the picked divisions survive
     the trip and come back on the way in. */
function renderChosenMeet(bare) {
  $("meet-chosen").innerHTML =
    `<div class="mc-main">
       <a class="mc-name"
          href="/meet/${state.meet.sport.toLowerCase()}/${esc(state.meet.id)}"
          >${esc(bare)}</a>
       ${state.meet.year ? `<span class="mc-year">${esc(state.meet.year)}</span>` : ""}
     </div>
     <div class="mc-sub">
       ${state.meet.date ? `ran ${esc(state.meet.date)} \u00b7 ` : ""}
       ${esc(state.meet.sport === "XC" ? "Cross Country" : "Track & Field")}
       <span id="mc-course"></span>
     </div>
     <button class="mc-change" data-clear="meet">Change</button>
     <div class="mc-races" id="mc-races"></div>
     <div class="mc-mode hidden" id="mc-mode">
       <div class="mc-modes">
         <label><input type="radio" name="racemode" value="separate" checked>
           Separate races</label>
         <label><input type="radio" name="racemode" value="combined">
           One combined race</label>
       </div>
       <label class="mc-coalesce hidden" id="mc-coalesce">
         <input type="checkbox" id="coalesce">
         Coalesce a school in two divisions into one squad
       </label>
       <span class="mc-mode-hint" id="mc-mode-hint"></span>
     </div>`;
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
    /* ★ THE MEET'S REAL DATE (owner, 2026-09-01). state.meet.date was scraped
       out of the search SUBLABEL with a regex, so a meet whose sublabel
       carried no ISO date had no date at all and the re-run date fell back to
       today -- which is why an August meet proposed a September date.
       ! BEFORE THE EARLY RETURN BELOW: a meet with one race or none still has
         a date, and that is the common case for a championship. */
    if (data.date) {
      state.meet.date = data.date;
      defaultDate();          // re-propose, now that we know when it ran
    }
    /* ★ NAME THE COURSE RATHER THAN DESCRIBING IT (owner, 2026-09-01).
       "the meet's own course" tells you nothing you did not already know;
       the course it actually ran on tells you what you are changing FROM. */
    if (data.course) {
      state.meet.course = data.course;
      $("t-course").placeholder = data.course;
      const mc = $("mc-course");
      if (mc) {
        mc.innerHTML = ` \u00b7 <a href="/course/`
          + `${encodeURIComponent(data.course)}"`
          + `>${esc(data.course)}</a>`;
      }
      if (!state.course) {
        $("t-course-hint").innerHTML =
          `Defaults to <a href="/course/${encodeURIComponent(data.course)}"`
          + `>${esc(data.course)}</a>.`
          + ` Pick another to run this same field somewhere else.`;
      }
    }
    const races = (data.races || []);
    if (races.length < 2) {
      if (races.length === 1) state.meet.div = String(races[0].div_id);
      return;
    }
    const chip = (label, div, on) =>
      `<button class="race-chip${on ? " is-on" : ""}" data-div="${div}">` +
      `${esc(label)}</button>`;
    box.innerHTML =
      `<span class="mc-races-label">Races:</span>` +
      chip("All races", "", !state.divs.length) +
      races.map((r) => {
        const bits = [r.label];
        if (r.gender) bits.push(r.gender === "M" ? "Boys" : "Girls");
        if (r.distance) bits.push(`${Math.round(r.distance)}m`);
        _divLabels.set(String(r.div_id), bits.join(" \u00b7 "));
        return chip(`${bits.join(" \u00b7 ")} (${r.n_results})`,
                    String(r.div_id), state.divs.includes(String(r.div_id)));
      }).join("");
    /* ★ MULTI-SELECT (issue #85). Each chosen division is its own race, with
       its own field, its own edits and its own scoring -- picking two does
       NOT merge them. "All races" is the whole meet and is exclusive with
       the rest, because "everything" and "these two" are different questions.
       ! CLICKING ALSO OPENS that division for editing, so the field below
         always shows one race and it is obvious which. */
    /* ★ SEPARATE OR COMBINED IS A CHOICE, NOT AN INFERENCE (owner,
       2026-09-01). Picking two divisions used to mean "two races" purely by
       implication, and there was no way to say "run them as one". The control
       says which is happening.
       ⚠ COMBINED MEANS THE WHOLE MEET, not a chosen subset. Merging two
         NAMED divisions into one scored race is issue #86 and is not built:
         it needs a ruling on a school entered in both, which would otherwise
         field fourteen. So combined drops the division filter entirely. */
    /* ! ONLY WHEN THERE IS A CHOICE. One race at a meet cannot be separate
         OR combined -- it is just the race -- and showing the control there
         is two radio buttons and a sentence that mean nothing. */
    $("mc-mode").classList.toggle("hidden", races.length < 2);
    $("mc-mode").querySelectorAll("input[name=racemode]").forEach((r) => {
      r.addEventListener("change", () => {
        state.raceMode = r.value;
        updateModeHint();
        saveState();
      });
    });
    $("coalesce").addEventListener("change", (e) => {
      state.coalesce = e.target.checked;
      updateModeHint();
      saveState();
    });
    /* A restored session had its mode in memory before these controls
       existed; put it back on them. */
    const chosen = $("mc-mode")
      .querySelector(`input[name=racemode][value="${state.raceMode}"]`);
    if (chosen) chosen.checked = true;
    $("coalesce").checked = state.coalesce;
    updateModeHint();

    box.querySelectorAll(".race-chip").forEach((b) => {
      b.addEventListener("click", () => {
        const div = b.dataset.div || null;
        state.divs = toggleDiv(state.divs, div);
        state.meet.div = state.divs.length
          ? (state.divs.includes(div) ? div : state.divs[0])
          : null;
        box.querySelectorAll(".race-chip").forEach((x) => {
          const d = x.dataset.div || null;
          x.classList.toggle("is-on", d === null ? !state.divs.length
                                                 : state.divs.includes(d));
          x.classList.toggle("is-editing",
                             state.divs.length > 1 && d === state.meet.div);
        });
        updateModeHint();
        saveState();
        loadField();          // a different race is a different field
      });
    });
  } catch (err) { /* no picker is just the whole meet, as before */ }
}


/*
 * ★ SAME WEEKDAY, NOT SAME DATE (owner, 2026-09-01). The note this replaces
 *   already had the right reason -- "a meet keeps its weekend far more
 *   reliably than its date" -- and then defaulted to the same month and day
 *   anyway, which is the one thing that does NOT hold. A year is 52 weeks
 *   plus a day, so keeping the date moves the meet one weekday every year
 *   (two across a leap day): a Saturday invitational came back proposed on a
 *   Sunday, and cross country is not run on Sundays.
 *
 * ! MINIMAL SHIFT, EITHER DIRECTION. The nearest matching weekday is at most
 *   three days away, so the answer is the residue of the weekday difference
 *   taken into [-3, +3] rather than always rolling forward -- rolling one way
 *   only would move a Saturday meet six days and into the next weekend.
 * ! UTC THROUGHOUT. Parsing "2025-09-13" as local time and formatting back
 *   can land a day out either side of the date line; Date.UTC and
 *   toISOString never disagree with each other.
 * ⚠ A 29 FEBRUARY ORIGINAL rolls to 1 March in a common year, before the
 *   weekday shift is applied. No cross country meet is run on 29 February,
 *   and pretending otherwise would cost more than it buys.
 */
/* A calendar day in the VIEWER's timezone. toISOString() is UTC and will
   name a different day for most of the world for part of every day. */
function localISO(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function sameWeekdayNextYear(iso, targetYear) {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return null;
  const orig = new Date(Date.UTC(y, m - 1, d));
  const cand = new Date(Date.UTC(targetYear, m - 1, d));
  if (isNaN(orig) || isNaN(cand)) return null;

  let delta = orig.getUTCDay() - cand.getUTCDay();      /* -6 .. 6 */
  if (delta > 3) delta -= 7;
  if (delta < -3) delta += 7;
  cand.setUTCDate(cand.getUTCDate() + delta);
  return cand.toISOString().slice(0, 10);
}

/*
 * ★ THE PROPOSED DATE is the meet's own running, moved to this year and then
 *   pulled onto the weekday it was actually run on. Editable, because meets
 *   do move, and because the model reads day-of-year as a feature so the
 *   difference is not cosmetic.
 */
function defaultDate() {
  const now = new Date();
  const aligned = state.meet.date
    ? sameWeekdayNextYear(state.meet.date, now.getFullYear())
    : null;
  /* ⚠ LOCAL, NOT toISOString(). That formats in UTC, so west of Greenwich an
     evening visit proposed TOMORROW -- the other half of the August-31st
     report. <input type="date"> speaks calendar days, not instants. */
  $("t-date").value = aligned || localISO(now);

  $("asran-hint").textContent = state.meet.date
    ? `Predicts the ${state.meet.date} running of this meet, with the field `
      + `that actually raced it. The real result is already known, so the `
      + `prediction is scored against it.`
    : `Predicts this meet as it ran, with the field that actually raced it.`;
}


/* ------------------------------------------------------------------ *
 *  THE FIELD
 * ------------------------------------------------------------------ */

/*
 * ★ EVERY PICKED DIVISION IS LOADED, and the cost of that is why this is
 *   shaped the way it is (owner, 2026-09-01: "insanely slowly if you press
 *   more than one").
 *
 *   The first version awaited each division in turn and re-rendered after
 *   every one, and renderField calls saveState, which serialises every cached
 *   field to JSON. N divisions meant N sequential round trips and O(N)
 *   full-state serialisations on top.
 *
 * ! FETCHED IN PARALLEL, RENDERED ONCE. The requests do not depend on each
 *   other, so they go together and the page is rendered twice in total: once
 *   showing "Loading", once with the answers.
 */
async function loadField() {
  const blocks = activeBlocks();

  renderField();                         // the blocks appear, saying Loading

  const missing = blocks.filter((d) => !editsFor(d).field);
  if (missing.length) {
    const got = await Promise.all(missing.map((d) => fetchField(d)));
    missing.forEach((d, i) => {
      if (got[i]) editsFor(d).field = got[i];
    });
  }
  renderField();
}


/* One division's field. Returns the data, or null; renders nothing and
   mutates no state, so the caller decides when the page changes. */
async function fetchField(div) {
  const q = new URLSearchParams({ meet_id: state.meet.id,
                                  sport: state.meet.sport,
                                  when: state.when });
  // Omitted, not sent empty: URLSearchParams turns null into the STRING
  // "null", which the server would try to parse as a division id.
  if (div) q.set("div_id", div);
  try {
    const res = await fetch("/api/predict/field?" + q.toString());
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || res.statusText, true);
      return null;
    }
    return data;
  } catch (err) {
    setStatus("Could not load the field.", true);
    return null;
  }
}


/* The races currently on screen. One entry per block, and [null] -- the whole
   meet -- when the divisions are not being raced separately. */
function activeBlocks() {
  return (state.raceMode === "separate" && state.divs.length > 0)
    ? state.divs.slice() : [state.meet.div ?? null];
}


/*
 * ★ ONE SECTION PER RACE (owner, 2026-09-01). Racing divisions separately
 *   means each has its own field to edit, so each gets its own WHO block.
 *   Combined is ONE race and gets ONE block.
 *
 * ! THE ACCESSORS ARE KEYED ON state.meet.div, so each block is rendered with
 *   that set to its own division, and restored afterwards. The click handlers
 *   do the same on the way in (focusBlock), so every existing handler keeps
 *   working unchanged and edits land on the right race.
 */
function renderField() {
  saveState();            // every edit path lands here
  const separate = state.raceMode === "separate" && state.divs.length > 0;
  const blocks = activeBlocks();
  const was = state.meet.div;

  /* ! A HALF-TYPED SEARCH SURVIVES THE RE-RENDER. The Add/Remove box lives
       INSIDE the block now, so every edit -- opening a card, removing a
       runner -- destroys the node you may be typing into. Carried over by
       division, with the caret, so it is not a trap. */
  const typed = new Map();
  let refocus = null;
  $("field").querySelectorAll(".div-field").forEach((sec) => {
    const inp = sec.querySelector(".school-input");
    if (!inp || !inp.value) return;
    const k = sec.dataset.divBlock;
    typed.set(k, inp.value);
    if (document.activeElement === inp) refocus = k;
  });

  $("field").innerHTML = blocks.map((d) =>
    `<section class="div-field" data-div-block="${divKey(d)}">
       ${separate ? `<h4 class="div-field-h">${esc(divLabel(d))}</h4>` : ""}
       <div class="fs"></div><div class="fg"></div>
       ${/* ★ ONE BOX PER RACE. It used to sit below every block, so with
             several divisions on screen a single bar had to guess which
             race you meant. Inside the block, the race IS the answer. */""}
       <div class="pick-wrap fieldpick">
         <input class="school-input" type="search" autocomplete="off"
                placeholder="Add or remove a team">
         <div class="pick-results hidden"></div>
       </div>
     </section>`).join("");

  blocks.forEach((d, i) => {
    state.meet.div = d;
    const sec = $("field").querySelectorAll(".div-field")[i];
    if (!sec) return;
    const sumEl = separate ? sec.querySelector(".fs") : $("field-summary");
    const gridEl = sec.querySelector(".fg");
    bindSchoolPicker(sec, d);
    const keep = typed.get(divKey(d));
    if (keep) {
      const inp = sec.querySelector(".school-input");
      inp.value = keep;
      if (refocus === divKey(d)) { inp.focus(); inp.select(); }
    }
    if (!state.field) { sumEl.textContent = "Loading\u2026"; return; }
    renderFieldBlock(sumEl, gridEl);
  });
  state.meet.div = was;

  /*
   * ★ THE HEADER ALWAYS SAYS SOMETHING (owner: "now score separately and
   *   stuff is bad again"). Racing divisions separately moves each race's
   *   count into its own block, which used to HIDE this line -- so the
   *   header collapsed to a lone "Head to head" checkbox floating at the far
   *   right of an otherwise empty row, above a race it did not belong to.
   *   In separate mode it carries the roll-up across every race instead:
   *   still the answer to "how big is this", just one level up.
   */
  if (separate) renderFieldRollup(blocks);
}


/* The whole-meet totals, for the header above the per-race blocks. Plain
   text: the Show / Undo controls belong to a race, and there is no one race
   here to apply them to. */
function renderFieldRollup(blocks) {
  const loaded = blocks.filter((d) => editsFor(d).field);
  const n = (v) => `<strong>${v}</strong>`;
  if (!loaded.length) {
    $("field-summary").innerHTML = `${n(blocks.length)} races \u2014 loading\u2026`;
    return;
  }
  let teams = 0, runners = 0;
  for (const d of loaded) {
    const ts = editsFor(d).field.teams
      .filter((t) => t.runners.length || t.dropped.length);
    teams += ts.length;
    runners += ts.reduce((k, t) => k + t.runners.length, 0);
  }
  $("field-summary").innerHTML =
    `${n(blocks.length)} races \u00b7 ${n(teams)} teams, ` +
    `${n(runners)} runners` +
    (loaded.length < blocks.length ? ` \u2014 loading the rest\u2026` : "");
}


/* One race's field, rendered into the elements it was handed. */
function renderFieldBlock(sumEl, gridEl) {
  const f = state.field;
  const teams = f.teams.filter((t) => t.runners.length || t.dropped.length);
  const kept = teams.reduce((n, t) => n + t.runners.length, 0);

  /* ★ THE COUNT IS THE HEADLINE; THE GLOSS IS A TOOLTIP (owner: "can we get
   *   a bit more space from the explaining text at the top of who and the
   *   teams list"). The long form ran to three lines above EVERY race block,
   *   so with three divisions on screen it was nine lines of the same
   *   sentence pushing the teams off the page. It says the same thing on
   *   hover, once you want it. */
  const gloss = f.when === "asran"
    ? "The field that actually raced this meet."
    : `Each team's current squad, top 7 predicted. The rest, and the `
      + `original runners without a ${f.season_year} season, are listed `
      + `under each team to add by hand.`;
  sumEl.innerHTML =
    `<span class="fs-n" title="${esc(gloss)}">` +
    `<strong>${teams.length}</strong> teams, ` +
    `<strong>${kept}</strong> runners` +
    (f.when === "asran" ? ` \u2014 as raced` : ` \u2014 current squads`) +
    `</span>` +
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
  gridEl.classList.toggle("hidden", state.view === "none");
  if (state.view === "none") { gridEl.innerHTML = ""; return; }

  gridEl.innerHTML = teams.map((t, i) => `
    <details class="team-card" data-team="${esc(t.school)}"
             ${state.open.has(t.school) ? "open" : ""}>
      <summary class="team-name">
        <span class="t-label"><a class="lnk"
           href="/school/${encodeURIComponent(t.school)}"
           >${esc(schoolWithState(t.school, t.state))}</a></span>
        <span class="team-n">${t.runners.length}</span>
        <button class="team-x" data-drop-team="${esc(t.school)}"
                title="Remove this team">&times;</button>
      </summary>
      <div class="team-body">
        ${t.runners.map((r) => `
          <div class="runner-row" data-pid="${r.person_id}">
            <span class="r-name"><a class="lnk"
               href="/athlete/${r.person_id}">${esc(r.name)}</a></span>
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
                <span class="r-name"><a class="lnk"
                   href="/athlete/${r.person_id}">${esc(r.name)}</a></span>
                <span class="r-rating">${r.rating === null
                    || r.rating === undefined ? "" : r.rating}${r.rating_year
                    ? `<span class="r-year">\u2009'${
                        String(r.rating_year).slice(2)}</span>` : ""}</span>
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

/* The request for ONE division. Each selected division is scored on its own,
   so each gets its own query built from its own edits (issue #85). */
function buildQuery(div) {
  /* ★ COMBINED IS ONE REQUEST FOR SEVERAL DIVISIONS, so it must carry every
     one of their edits. `div` is null there, and editsFor(null) is the
     ALL-RACES record -- so a team removed while D1 was open was silently
     dropped from the request and raced anyway. */
  const combining = state.raceMode === "combined" && state.divs.length > 1;
  const e = combining ? mergedEdits(state.divs) : editsFor(div);
  const q = new URLSearchParams({
    mode: state.when === "asran" ? "rerun_exact" : "rerun",
    meet_id: state.meet.id,
    sport: state.meet.sport,
  });
  if (div) q.set("div_id", div);
  /* ★ COMBINED WITH DIVISIONS PICKED IS ONE RACE OUT OF SEVERAL (issue #86).
     Separate mode never sends div_ids -- it sends one div_id per request. */
  if (state.raceMode === "combined" && state.divs.length > 1) {
    q.set("div_ids", state.divs.join(","));
    if (state.coalesce) q.set("coalesce", "1");
  }
  if (state.when === "thisyear") q.set("date", $("t-date").value);
  /* ★ THE COURSE OVERRIDE. Empty means the meet's own, which is what the
     server does with an absent value -- so nothing is sent unless a
     different venue was actually chosen. "As it ran" never sends one: that
     mode means the race that happened, on the course it happened on. */
  if (state.when === "thisyear" && state.course) q.set("course", state.course);

  if (state.who === "individual") {
    // One parameter, one or many values -- the endpoint splits it.
    q.set("person_id", state.athletes.map((a) => a.id).join(","));
  } else {
    if ($("head_to_head").checked) q.set("head_to_head", "1");
    // Only the edits are sent. The server already knows the meet's own field,
    // so shipping the whole roster back would be a large request that says
    // the same thing.
    if (e.removed.size) q.set("remove", [...e.removed].join(","));
    if (e.added.length)
      q.set("add", e.added.map((a) => a.person_id).join(","));
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

  /* ★ ONE REQUEST PER DIVISION, and one section per result (issue #85). The
     divisions are SEPARATE RACES -- scoring them together would be a
     different feature -- so nothing is merged: each is the existing
     single-division request, run once per selection.
     ! INDIVIDUAL MODE HAS NO DIVISIONS: the athletes were named directly. */
  const targets = (state.who === "team" && state.raceMode === "separate"
                   && state.divs.length)
    ? state.divs.slice() : [state.who === "team" ? null : state.meet.div];

  try {
    const parts = [];
    for (const div of targets) {
      const res = await fetch(path + "?" + buildQuery(div).toString());
      const data = await res.json();
      if (!res.ok) { setStatus(data.error || res.statusText, true); return; }

      // available:false is the expected answer until the model is trained,
      // and it carries its own reason. Not an error.
      if (data.available === false) {
        setStatus(data.reason || "Not available yet.", false);
        return;
      }
      const body = state.who === "individual"
        ? renderIndividual(data) : renderTeam(data);
      parts.push(targets.length > 1
        ? `<section class="div-result">
             <h3 class="div-result-h">${esc(divLabel(div))}</h3>${body}
           </section>`
        : body);
    }
    setStatus("", false);
    $("output").innerHTML = parts.join("");
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
      <td>${esc(schoolWithState(t.team, t.state))}</td>
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
/* Whether one race already has this school, counting a pending addition. */
function teamInDiv(div, school) {
  const e = editsFor(div);
  return (e.field ? e.field.teams : []).some((t) => t.school === school)
      || e.added.some((a) => a.school === school);
}

/*
 * Every race on screen that already has this school.
 *
 * ⚠ THE PREDECESSOR READ state.field, WHICH IS ONE RACE'S FIELD. state.field
 *   is an accessor keyed on state.meet.div, and the school box used to sit
 *   OUTSIDE the division blocks -- so it answered for whichever block was
 *   focused last. Each race owns its box now, but this is still asked across
 *   all of them, to tell you a school is already racing elsewhere.
 */
function blocksWith(school) {
  return activeBlocks().filter((d) => teamInDiv(d, school));
}

/*
 * ⚠ THE LABEL IS NOT THE NAME. /search/api indexes a school as "DeWitt (MI)"
 *   -- the site-wide display convention -- while athlete_season.school and
 *   ranking_results.school store the bare "DeWitt". The API hands back BOTH,
 *   as .label and .value, precisely so a picker does not have to know the
 *   rule (search_index.bareSchool owns it).
 *
 *   Using .label for identity broke this two ways at once, both reported:
 *     - adding sent "DeWitt (MI)" to /api/predict/squad, which matches no
 *       row, so every add died as "No one from DeWitt (MI) has raced this
 *       season" -- a real school with a real squad, reported as empty;
 *     - matching compared "DeWitt (MI)" against the field's bare "DeWitt",
 *       so a school ALREADY IN THE RACE never matched, the row always said
 *       Add, and there was no way to remove anything ("still no delete").
 *
 *   .value is the name. .label is for reading.
 */
const schoolValue = (r) => r.value || r.label;

/*
 * ★ THE STATE IS DISPLAY ONLY (issue #95). Every other surface writes
 *   "Broughton (NC)" and this page wrote the bare "Broughton". The bare name
 *   is the KEY, though -- teamIsIn, the squad endpoint, _score's grouping and
 *   ranking_results.school all match on it -- so it is composed here, at the
 *   moment of rendering, and the composed string never goes back into a
 *   lookup or an href.
 *
 * ⚠ THE SERVER MAY ALREADY HAVE SUFFIXED THE NAME. A school entered in two
 *   divisions of a combined race comes back as "Broughton (Varsity)" (#86),
 *   which is a scoring key rather than a school -- the server sends no state
 *   for those, so this leaves them exactly as they are.
 */
function schoolWithState(school, st) {
  return st ? `${school} (${st})` : school;
}

/* One race's Add/Remove list. The box lives inside that race's block, so
   the action is unambiguous and needs no division suffix. */
function renderSchoolsFor(div) {
  return (rows) => rows.slice(0, 8).map((r) => {
    const school = schoolValue(r);
    const here = teamInDiv(div, school);
    // Not in THIS race but in another one on screen: say so, or adding it
    // here looks like it did nothing to the block you were just looking at.
    const elsewhere = here ? [] : blocksWith(school);
    const sub = elsewhere.length
      ? `already in ${elsewhere.map(divLabel).join(", ")}`
      : (r.sublabel || "");
    return `<button class="pick-opt${here ? " is-in" : ""}" ` +
      `data-label="${esc(r.label)}" data-school="${esc(school)}" ` +
      `data-link="${esc(r.link || "")}">` +
      `<span class="pick-name">${esc(r.label)}</span>` +
      `<span class="pick-act">${here ? "Remove" : "Add"}</span>` +
      `<span class="pick-sub">${esc(sub)}</span></button>`;
  }).join("");
}

/* Wire the box inside one rendered race block. */
function bindSchoolPicker(sec, div) {
  bindPicker(sec.querySelector(".school-input"),
             sec.querySelector(".pick-results"),
             "school", renderSchoolsFor(div), (d) => {
    const school = d.school || d.label;
    if (teamInDiv(div, school)) { removeTeam(div, school); return; }
    addTeam(school, div);
  });
}

/*
 * Out of one race: the team goes, and its runners are marked removed so the
 * request says what the screen says.
 *
 * ! WRITTEN THROUGH editsFor, NOT THE state ACCESSORS, which answer for
 *   state.meet.div -- not necessarily the race being edited.
 */
function removeTeam(div, school) {
  const e = editsFor(div);
  const team = (e.field ? e.field.teams : []).find((t) => t.school === school);
  if (team) for (const r of team.runners) e.removed.add(String(r.person_id));
  if (e.field) e.field.teams = e.field.teams.filter((t) => t.school !== school);
  e.added = e.added.filter((a) => a.school !== school);
  renderField();
  setStatus(`Removed ${school}.`, false);
}


/* ------------------------------------------------------------------ *
 *  COURSE OVERRIDE
 * ------------------------------------------------------------------ */

/*
 * ★ RUN THIS FIELD SOMEWHERE ELSE (owner, 2026-09-01). The meet supplies the
 *   field, the division and the date; only the venue changes. Clearing the
 *   box goes back to the meet's own course, which is why the empty state is
 *   a placeholder rather than a value -- there is nothing to "unset".
 */
/* ⚠ .pick-opt, NOT a class of its own. makePicker listens for mousedown on
   `.pick-opt` and reads the row's dataset -- so the first version of this,
   which rendered `.pick-row`, looked like a list and did nothing at all when
   clicked. Same failure mode parseMeetLink had, and just as silent.
   The markup matches renderSchoolsFor exactly, which is also why it now looks
   like the rest of the search bars instead of like something else. */
makePicker("t-course", "t-course-results", "course",
  (rows) => rows.slice(0, 8).map((r) =>
    `<button class="pick-opt" data-label="${esc(r.label)}">` +
    `<span class="pick-name">${esc(r.label)}</span>` +
    `<span class="pick-sub">${esc(r.sublabel || "")}</span></button>`).join(""),
  (d) => {
    state.course = d.label;
    const own = (state.meet && state.meet.course) || "the meet\u2019s own course";
    $("t-course-hint").innerHTML =
      `Instead of ${esc(own)}. <a href="/course/`
      + `${encodeURIComponent(d.label)}">View ${esc(d.label)}</a>`;
    saveState();
  }, true);   /* keepValue: the box shows the chosen course */

/* Emptying the box is the way back to the meet's own course. */
$("t-course").addEventListener("input", () => {
  if ($("t-course").value.trim() === "" && state.course) {
    state.course = null;
    const own = state.meet && state.meet.course;
    $("t-course-hint").innerHTML = own
      ? `Defaults to <a href="/course/${encodeURIComponent(own)}"`
        + `>${esc(own)}</a>.`
        + ` Pick another to run this same field somewhere else.`
      : "Defaults to the course this meet was run on.";
    saveState();
  }
});


/*
 * ★ AN ADDED TEAM APPEARS IN THE FIELD, NOT AS A PROMISE. The first version
 *   recorded the school name and told the reader their runners would be
 *   "resolved when you predict" -- so the roster you were looking at was not
 *   the roster you would get, and there was nothing to edit. Fetching the
 *   squad puts a real card in the grid, with the same seven-and-edit
 *   behaviour as every other team.
 */
async function addTeam(school, div) {
  /* ! THE TARGET RACE IS HELD, NOT READ BACK. There is an await in the
       middle of this, and state.meet.div can move under it (another block
       clicked, the mode flipped). Taking the edit record once means the
       squad lands in the race the click asked for, whatever happened
       meanwhile. */
  const e = editsFor(div === undefined ? (state.meet.div ?? null) : div);
  if (!e.field) return;
  setStatus(`Loading ${school}\u2026`, false);
  try {
    const squad = await loadSquad(school, e.field.gender);
    if (!squad.runners.length) {
      setStatus(`No one from ${school} has raced this season.`, true);
      return;
    }
    e.field.teams.push({
      school: school,
      runners: squad.runners.slice(0, 7),
      // The rest of the squad, offered under the card rather than discarded --
      // "add anyone from their squad" is the point of having fetched it.
      dropped: squad.runners.slice(7),
      added: true,
    });
    e.field.teams.sort((a, b) => a.school.localeCompare(b.school));
    e.open.add(school);              // open it: it is the thing just added
    for (const r of squad.runners.slice(0, 7))
      e.added.push({ person_id: r.person_id, name: r.name, school: school });
    renderField();
    setStatus("", false);
  } catch (err) {
    setStatus(`Could not load ${school}.`, true);
  }
}


/* One fetch per school, cached: a card can be opened and closed repeatedly,
   and the squad does not change while the page is open. */
const squadCache = new Map();

async function loadSquad(school, gender) {
  /* ! THE GENDER IS PART OF THE CACHE KEY. A school has a boys team and a
       girls team; keying on the name alone would serve one race's squad to
       the other. It is PASSED IN rather than read off state.field, which
       answers for the focused block and not necessarily the one being
       added to. */
  const g = gender || state.field?.gender || "";
  const key = `${school}\u0000${g}`;
  if (squadCache.has(key)) return squadCache.get(key);
  const q = new URLSearchParams({ school: school, sport: state.meet.sport });
  if (g) q.set("gender", g);
  const res = await fetch("/api/predict/squad?" + q.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  squadCache.set(key, data);
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

/* ★ WHICH RACE WAS CLICKED. Every accessor -- state.field, state.removed,
   state.added -- is keyed on state.meet.div, so a click inside a division's
   block must set it before anything else reads it. Done once here, and every
   handler downstream keeps working unchanged. */
function focusBlock(e) {
  const blk = e.target.closest && e.target.closest("[data-div-block]");
  if (!blk) return;
  const d = blk.dataset.divBlock;
  state.meet.div = d === "" ? null : d;
}

document.addEventListener("click", (e) => {
  focusBlock(e);
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
          /* ★ GENDER-FILTERED, and rated. /search/api serves search_index,
             which carries neither -- so this box offered the whole corpus to
             a boys race, and offered people with no rating to predict from. */
          const qs = new URLSearchParams({ q: q, sport: state.meet.sport });
          if (state.field?.gender) qs.set("gender", state.field.gender);
          const res = await fetch("/api/predict/athletes?" + qs.toString());
          const hits = ((await res.json()) || {}).athletes || [];
          rows.innerHTML = hits.length
            /* ★ TWO LINES, NOT ONE (owner, 2026-09-01). Name, rating and
               school on a single flex row left the name about forty pixels
               wide -- every result read "Tadhg M...". The school is the thing
               that tells two same-named athletes apart, so it cannot be the
               part that gets dropped. */
            ? hits.map((r) => `<div class="anyone-row">
                   <a class="ar-name" href="/athlete/${r.person_id}"
                      >${esc(r.name || "Unknown")}</a>
                   <span class="ar-rating">${r.rating}</span>
                   <button class="r-add" data-add="${r.person_id}"
                           data-name="${esc(r.name || "Unknown")}"
                           data-rating="${r.rating}"
                           data-school="${esc(school)}">add</button>
                   <span class="ar-school">${esc(r.school || "")}${r.year
                     ? ` \u00b7 ${esc(r.year)}` : ""}</span>
                 </div>`).join("")
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
               <span class="r-name"><a class="lnk"
                  href="/athlete/${r.person_id}">${esc(r.name)}</a></span>
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
    state.course = null;
    state.divs = [];
    resetEdits();
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
function onSummaryClick(e) {
  focusBlock(e);
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
}

$("field-summary").addEventListener("click", onSummaryClick);
/* ! AND ON #field, because in separate mode each division's summary is
     rendered INSIDE it rather than in the single #field-summary. */
$("field").addEventListener("click", onSummaryClick);

$("field").addEventListener("toggle", (e) => {
  const card = e.target.closest(".team-card");
  if (!card) return;
  if (card.open) state.open.add(card.dataset.team);
  else state.open.delete(card.dataset.team);
}, true);

$("predict").addEventListener("click", predict);

/* ★ LAST, so every control it writes back into already exists. */
document.addEventListener("DOMContentLoaded", restoreState);
