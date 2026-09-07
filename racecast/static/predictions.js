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
  // Which picked divisions are scored as ONE race. A partition of divs; see
  // normalizeGroups. The two presets are groupings, not a separate mode.
  groups: [],
  // ⚠ `view` IS NOT HERE. How much of a field is on screen is PER RACE now,
  //   so it lives in each division's edit record and reaches this object
  //   through an accessor, like field/open/added. A plain key here would
  //   shadow that accessor and silently make it global again.
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
/*
 * ★ "ALL RACES" MEANS EVERY DIVISION, NOT "NO DIVISION" (owner, 2026-09-01:
 *   "pressing all races should still separate each div unless combined").
 *   It used to CLEAR the selection, which made it a third thing the rest of
 *   the page had to special-case: one unheaded block covering the whole meet,
 *   no per-division fields to edit, and -- because coalesce only appears with
 *   more than one division picked -- no coalesce option on the one selection
 *   where a school is most likely to be entered twice.
 *
 *   Selecting them all instead makes it an ordinary selection. Separate mode
 *   gives a race per division; combined scores them as one; coalesce appears
 *   because the count is right. Nothing downstream needs to know it was a
 *   shortcut.
 *
 * ! PRESSING IT AGAIN CLEARS, so it is still a toggle rather than a trap.
 */
/* ==================================================================== *
 *  RACE GROUPS (issue #89)
 *
 *  ★ A GROUP IS A SCORING CONCEPT, NOT A LAYOUT ONE. Each picked division
 *    keeps its own block, its own field and its own edits -- that is what
 *    the owner asked for and what the server already does, since
 *    _combinedRoster builds a combined race division by division. A GROUP
 *    just says which of those blocks are scored as one race.
 *
 *    So the two old modes are presets over the same structure:
 *      separate  -> every division in a group of its own
 *      combined  -> every division in one group
 *      custom    -> anything else
 *    and nothing downstream had to learn a new idea: buildQuery already took
 *    div_ids, mergedEdits already unioned several divisions' edits, and the
 *    server already loops divisions for a combined race.
 * ==================================================================== */

/*
 * ★ QUICK-SELECT (owner, 2026-09-01: "a button to click all girls/all boys
 *   for each distance a race runs... a quick way to get compiled"). A
 *   championship is a dozen races, and building "every boys 5000m" out of
 *   them one chip at a time is the work this page exists to save.
 *
 * ! THE SETS COME FROM THE MEET, NOT FROM A FIXED LIST. Whatever
 *   (gender, distance) pairs the races actually carry are the buttons that
 *   appear -- so a meet with one distance offers "All boys" and "All girls",
 *   and one with a 5k and a 3200 offers four, and a meet whose races carry
 *   no gender offers none rather than an empty promise.
 *
 * ! A SET OF ONE IS NOT A SHORTCUT. It would duplicate the division's own
 *   chip sitting next to it, so it is skipped.
 */
function quickSets(races) {
  const by = new Map();
  for (const r of races) {
    if (!r.gender) continue;
    const dist = r.distance ? `${Math.round(r.distance)}m` : "";
    const key = `${r.gender}\u0000${dist}`;
    if (!by.has(key)) {
      by.set(key, { gender: r.gender, dist: dist, ids: [] });
    }
    by.get(key).ids.push(String(r.div_id));
  }
  // Only one distance in the whole meet? Then naming it is noise.
  const dists = new Set([...by.values()].map((v) => v.dist));
  const one = dists.size <= 1;
  return [...by.values()]
    .filter((v) => v.ids.length > 1)
    .map((v) => ({
      label: `All ${v.gender === "M" ? "boys" : "girls"}`
             + (one || !v.dist ? "" : ` \u00b7 ${v.dist}`),
      ids: v.ids,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}


/*
 * ★ ONE COLOUR PER RACE (owner, 2026-09-01: "easier to find/get in the
 *   grouped section"). With six divisions on screen, working out which
 *   blocks belong to the same race meant reading every heading. A shared
 *   colour answers it before you read anything.
 *
 * ! OKABE-ITO, WHICH IS THE POINT OF CHOOSING A PALETTE RATHER THAN PICKING
 *   HUES. It is the standard categorical set designed to stay distinguishable
 *   under the common colour-vision deficiencies -- no red/green pair, which
 *   is exactly the mistake a hand-picked "six obvious colours" makes.
 *
 * ⚠ AND COLOUR IS NEVER THE ONLY SIGNAL. Each block still carries its
 *   division name and, when it shares a race, the "scored with ..." note.
 *   The colour is a shortcut to information that is also written down, which
 *   is the only way it is safe to use one.
 */
const _RACE_COLOURS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7",
                       "#56B4E9", "#D55E00", "#8C6D31", "#5D3A9B"];

function raceColour(i) {
  return _RACE_COLOURS[((i % _RACE_COLOURS.length) + _RACE_COLOURS.length)
                       % _RACE_COLOURS.length];
}

/* {division -> its group's index}, for colouring and for the "scored with"
   note. One pass, so callers do not each walk the groups. */
function groupIndex() {
  const at = new Map();
  state.groups.forEach((g, i) => g.forEach((d) => at.set(d, i)));
  return at;
}


/* The grouping the two presets describe. */
function groupsForMode(divs, mode) {
  if (!divs.length) return [];
  return mode === "combined" ? [divs.slice()] : divs.map((d) => [d]);
}

/*
 * Keep groups a partition of divs: nothing grouped that is not picked,
 * nothing picked that is not grouped, no empty groups.
 *
 * ! CALLED AFTER EVERY CHANGE TO EITHER, because the two are separate state
 *   and a division picked or unpicked must not leave a group referring to it
 *   -- a stale id there would be sent to the server as part of a race.
 */
function normalizeGroups(divs, groups) {
  const want = new Set(divs);
  const out = [];
  const seen = new Set();
  for (const g of groups || []) {
    const kept = g.filter((d) => want.has(d) && !seen.has(d));
    kept.forEach((d) => seen.add(d));
    if (kept.length) out.push(kept);
  }
  // Anything newly picked joins as a race of its own -- the safe default,
  // since merging two fields is the thing that needs to be asked for.
  for (const d of divs) if (!seen.has(d)) out.push([d]);
  return out;
}

/* Move one division into a group by index; -1 means a new race of its own. */
function moveDiv(groups, div, to) {
  const out = groups.map((g) => g.filter((d) => d !== div))
                    .filter((g) => g.length);
  if (to >= 0 && to < out.length) out[to].push(div);
  else out.push([div]);
  return out;
}

/* The mode a grouping actually describes, so the radios cannot lie about
   what is on screen after a division is unpicked. */
function modeOfGroups(divs, groups) {
  if (groups.length <= 1 && divs.length > 1) return "combined";
  if (groups.every((g) => g.length === 1)) return "separate";
  return "custom";
}

function toggleDiv(divs, div, all) {
  if (div === null) {
    const every = (all || []).slice();
    return divs.length === every.length && every.length ? [] : every;
  }
  return divs.includes(div) ? divs.filter((d) => d !== div)
                            : divs.concat([div]);
}

/*
 * ★ THE GROUPING CONTROL (issue #89). One row per picked division, each with
 *   the race it belongs to. A dropdown rather than drag-and-drop: drag needs
 *   a drop-target design, is poor on a phone -- which is already its own
 *   open issue -- and is far more code for a control that has to say exactly
 *   one thing, which race this division is in.
 *
 * ! "New race" IS ALWAYS AVAILABLE, so a grouping can be built up from
 *   scratch without first having to make room for it.
 *
 * ⚠ MIXED GENDERS ARE ALLOWED (owner's ruling on #86, reaffirmed for #89),
 *   because racing a boys division against a girls one is a question people
 *   genuinely ask. It is NOTED on the group rather than blocked, since a
 *   score across it means something different from a score within one.
 */
function renderGroups() {
  const box = $("mc-groups");
  if (!box) return;
  box.classList.toggle("hidden",
                       state.raceMode !== "custom" || state.divs.length < 2);
  if (box.classList.contains("hidden")) { box.innerHTML = ""; return; }

  const at = groupIndex();

  box.innerHTML = state.divs.map((d) => {
    const mine = at.get(d);
    const opts = state.groups.map((g, i) =>
      `<option value="${i}"${i === mine ? " selected" : ""}>Race ${i + 1}` +
      `</option>`).join("")
      + `<option value="-1">New race</option>`;
    return `<div class="grp-row" style="--race:${
          raceColour(mine === undefined ? 0 : mine)}">
        <span class="grp-dot"></span>
        <span class="grp-name">${esc(divLabel(d))}</span>
        <select class="grp-sel" data-div="${esc(d)}">${opts}</select>
      </div>`;
  }).join("") + state.groups.map((g, i) => {
    if (g.length < 2) return "";
    const genders = new Set(g.map((d) => (_divLabels.get(String(d)) || ""))
      .map((l) => /Girls/.test(l) ? "F" : (/Boys/.test(l) ? "M" : "?")));
    const mixed = genders.has("M") && genders.has("F");
    /* ★ A LIST STOPS BEING A SUMMARY AT ABOUT THREE. Ten divisions joined
     *   with " + " is four wrapped lines of "Division 1 · Boys · 5000m +
     *   Division 1 · Girls · 5000m + ..." -- which says less than "10
     *   divisions" does and buries the one line worth reading. The full list
     *   stays on the tooltip, for when it is actually wanted. */
    const full = divLabel(g);
    const short = g.length > 3 ? `${g.length} divisions` : full;
    /* ! WHAT COALESCE WILL AND WILL NOT DO WITH IT: it merges a school's
         entries WITHIN a gender and never across one, because a boys team
         and a girls team are two teams sharing a name. Its own line, and no
         leading dash -- as a flex sibling with an em-dash it was laid out as
         a second COLUMN of text beside the list. */
    return `<div class="grp-sum" style="--race:${raceColour(i)}" ` +
      `title="${esc(full)}">` +
      `<span class="grp-dot"></span>Race ${i + 1}: ${esc(short)}` +
      (mixed ? `<span class="grp-warn">Boys and girls score together in `
             + `this race.</span>` : "") + `</div>`;
  }).join("");
}


/* Say what the current mode will actually do, in the terms of what is
   picked -- "combined" with nothing picked and with two picked are the same
   request, and that is worth stating rather than leaving to be discovered. */
function updateModeHint() {
  const el = $("mc-mode-hint");
  if (!el) return;
  const n = state.divs.length;
  renderGroups();
  /* The chips summarise the grouping, so they follow it -- from the mode
     radios and the per-division dropdown as well as from a chip click. */
  if (_repaintChips) _repaintChips();
  /* ★ COALESCE IS ABOUT A SCHOOL IN TWO DIVISIONS OF ONE RACE, so it applies
     wherever a GROUP has more than one division -- not only to the combined
     preset. One checkbox for all such groups: per-group checkboxes would
     triple the size of this control for a case most meets never hit, and the
     rule it sets is the same one either way. */
  const merged = state.groups.some((g) => g.length > 1);
  const co = $("mc-coalesce");
  if (co) co.classList.toggle("hidden", !merged);
  /* ★ EVERY MESSAGE IS ONE SHORT LINE (owner, 2026-09-01: the jolt, third
     attempt). Reserving height for a hint that swung between one line and
     three was treating the symptom -- the real fix is that it does not swing.
     What a coalesced school does is now written on the CHECKBOX, where it is
     static, instead of being appended here where it was not. */
  const races = state.groups.length;
  el.textContent =
    !n ? "Scoring the whole meet as one race."
    : races === 1 ? `Scoring ${n} divisions as one race.`
    : races === n ? `Scoring ${n} divisions separately.`
    : `Scoring ${n} divisions as ${races} races.`;
}

/* The name a division goes by, for a result heading. Falls back to the id so
   a section is never headed by nothing. */
const _divLabels = new Map();

/* ! THE CHIP REPAINT, REACHABLE FROM OUTSIDE loadRaces. The chips are built
     inside it and close over `races`, so the function that recolours them
     cannot be defined anywhere else -- but every control that regroups the
     races needs to call it. Set on render, called from updateModeHint. */
let _repaintChips = null;
function divLabel(div) {
  if (div === null || div === undefined) return "All races";
  /* ★ A GROUP NAMES ITS DIVISIONS, NOT ITS INDEX (issue #89). "Race 2" is
     fine inside the control where you are assigning, and useless above a
     result: what you want to read is which races these are. */
  if (Array.isArray(div)) {
    if (!div.length) return "All races";
    return div.map(divLabel).join(" + ");
  }
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
                    droppedTeams: [], added: [],
                    // How much of THIS race is on screen: nothing, the team
                    // cards, or every roster open.
                    view: "teams" });
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
                   droppedTeams: e.droppedTeams, added: e.added,
                   view: e.view };
    }
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({
      meet: state.meet, when: state.when, who: state.who,
      divs: state.divs, raceMode: state.raceMode,
      groups: state.groups,
      coalesce: state.coalesce, course: state.course,
      athletes: state.athletes,
      date: $("t-date") ? $("t-date").value : null,
      labels: [..._divLabels], edits,
    }));
  } catch (err) { /* nothing here is worth breaking the page for */ }
}

/* A shared prediction link (/predictions?meet_id=...&div_id=...&sport=...)
   picks that meet, and its race, as if it had been chosen from the search:
   the person it was sent to sees the same field and presses Predict. */
async function restoreFromLink() {
  const p = new URLSearchParams(location.search);
  const id = (p.get("meet_id") || "").trim();
  if (!/^\d+$/.test(id)) return false;
  const sport = (p.get("sport") || "XC").toUpperCase() === "TF" ? "tf" : "xc";
  const div = (p.get("div_id") || "").trim();
  let name = "", date = null;
  try {
    const res = await fetch("/api/predict/races?" + new URLSearchParams({ meet_id: id, sport: sport.toUpperCase() }));
    const data = await res.json();
    name = data.meet_name || "";
    date = data.date || null;
  } catch (err) { /* the meet still opens, unnamed */ }
  await chooseMeet({ link: div ? `/race/${sport}/${id}/${div}` : `/meet/${sport}/${id}`,
                     label: name || `Meet ${id}`, sub: date || "" });
  if (div) {
    state.divs = [div];
    state.groups = normalizeGroups(state.divs, groupsForMode(state.divs, "separate"));
    loadRaces();
  }
  return true;
}

function restoreState() {
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
  } catch (err) { return; }
  if (!saved || !saved.meet) {
    if (/[?&]meet_id=/.test(location.search)) restoreFromLink();
    return;
  }

  state.meet = saved.meet;
  state.when = saved.when || "thisyear";
  state.who = saved.who || "team";
  state.divs = saved.divs || [];
  state.raceMode = saved.raceMode || "separate";
  /* A session saved before groups existed carries none; derive them from the
     mode it did save, which is exactly what that mode meant. */
  state.groups = normalizeGroups(
    state.divs,
    saved.groups || groupsForMode(state.divs, state.raceMode));
  state.coalesce = !!saved.coalesce;
  state.course = saved.course || null;
  state.athletes = saved.athletes || [];

  resetEdits();
  for (const [k, e] of Object.entries(saved.edits || {})) {
    const rec = editsFor(k === "" ? null : k);
    rec.removed = new Set(e.removed || []);
    rec.open = new Set(e.open || []);
    rec.droppedTeams = e.droppedTeams || [];
    rec.added = e.added || [];
    rec.view = e.view || "teams";
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
  /* ★ PER RACE, WITH AN OVERALL CONTROL ON TOP (owner, 2026-09-01: "there
   *   should be one for each division and also overall"). It was a single
   *   global value, so "Rosters" on one division opened all of them and
   *   there was no way to read one race's teams with another shut. Each
   *   block owns its view; the header's control writes to every block.
   * ⚠ AND THERE MUST BE NO PLAIN `view` KEY on state -- it would shadow this
   *   accessor and silently make the setting global again. */
  view:         { get: () => editsFor(state.meet && state.meet.div).view,
                  set: (v) => { editsFor(state.meet && state.meet.div).view = v; } },
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
  if (s === null || s === undefined) return " - ";
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
function bindPicker(input, box, kind, render, onPick, keepValue, opts) {
  if (!input || !box) return;
  opts = opts || {};
  let timer = null;

  /*
   * ⚠ A RESPONSE MUST NOT RE-OPEN A BOX THE READER HAS LEFT (owner,
   *   2026-09-01: "type something, then exit the search bar, the popup will
   *   show a few seconds later and be hard to click off").
   *
   *   The race: blur hid the box after 150ms, the debounce fired at 180ms,
   *   the fetch returned later still, and the last line of search() showed
   *   the box again -- over whatever the reader had moved on to, with no
   *   focus left to blur a second time.
   *
   *   `seq` drops a response a newer keystroke has superseded. `closed`
   *   drops one that nothing is waiting for any more. Two different
   *   staleness questions; the second is the reported bug.
   */
  let seq = 0;
  let closed = false;

  async function search() {
    const q = input.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); return; }
    /* Every path below that paints goes through this: a response may only
       land if it is still the latest AND the box is still open. */
    const mine = ++seq;
    const live = () => mine === seq && !closed;
    /* ! AN ALTERNATIVE SOURCE, for a picker whose rows come from somewhere
         richer than the search index. /search/api carries a label, a
         sublabel and a link and nothing else -- no rating, no school as a
         field, no gender -- so a picker that needs those reads its own
         endpoint and hands back rows in the same shape. */
    if (opts.rows) {
      try {
        const rows = await opts.rows(q);
        if (!live()) return;
        box.innerHTML = render(rows);
        box.classList.toggle("hidden", rows.length === 0);
      } catch (err) { if (live()) box.classList.add("hidden"); }
      return;
    }
    try {
      /* ! BACK TO THE DEFAULT LIMIT. This asked for 30 while "a row falls
           past the LIMIT" was thought to be the cause of #97; the cause was
           the RANKING, which is fixed at the source, so a longer list buys
           nothing and costs three times the rows on every keystroke. */
      const res = await fetch(`/search/api?kind=${kind}&q=`
                              + encodeURIComponent(q));
      const rows = (await res.json() || []).filter((r) => r.kind === kind);
      if (!live()) return;
      box.innerHTML = render(rows);
      box.classList.toggle("hidden", rows.length === 0);
    } catch (err) {
      if (live()) box.classList.add("hidden");
    }
  }

  input.addEventListener("input", () => {
    closed = false;                 // typing re-opens what leaving shut
    clearTimeout(timer);
    timer = setTimeout(search, 180);
  });
  input.addEventListener("focus", () => { closed = false; });

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
    closed = true;                  // and no late response may re-open it
    clearTimeout(timer);
    box.classList.add("hidden");
  });

  /* ! CLOSED AT ONCE, HIDDEN A MOMENT LATER. The flag has to be set
       immediately so an in-flight response is dropped; the visual hide keeps
       its delay because a pick is a mousedown and the click that follows it
       still has to land. */
  input.addEventListener("blur", () => {
    closed = true;
    clearTimeout(timer);
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
/*
 * ★ THE PICKER SHOWS WHAT THE TABLE SHOWS (owner, 2026-09-01: "there should
 *   be info about each athlete beyond just their name"). /search/api carries
 *   a label, a sublabel and a link -- no rating, no gender, no season -- so
 *   this reads /api/predict/athletes instead, which is the same source the
 *   team side's "add anyone" already uses. Two people with one name are told
 *   apart by their school and their rating, which is exactly the case a bare
 *   name cannot answer.
 */
async function athleteRows(q) {
  const p = new URLSearchParams({ q: q, sport: state.meet.sport });
  const g = state.field && state.field.gender;
  if (g) p.set("gender", g);
  const res = await fetch("/api/predict/athletes?" + p.toString());
  const data = await res.json();
  return data.athletes || [];
}

function renderAthleteRows(rows) {
  return rows.slice(0, 10).map((r) => {
    const id = String(r.person_id);
    const chosen = state.athletes.some((a) => a.id === id);
    const sub = [r.school, r.year, r.rating == null ? null : `${r.rating}`]
      .filter(Boolean).join(" \u00b7 ");
    return `<button class="pick-opt${chosen ? " is-in" : ""}" ` +
      `data-pid="${esc(id)}" data-label="${esc(r.name)}" ` +
      `data-school="${esc(r.school || "")}" ` +
      `data-year="${esc(r.year == null ? "" : r.year)}" ` +
      `data-rating="${esc(r.rating == null ? "" : r.rating)}">` +
      `<span class="pick-name">${esc(r.name)}</span>` +
      `<span class="pick-act">${chosen ? "Remove" : "Add"}</span>` +
      `<span class="pick-sub">${esc(sub)}</span></button>`;
  }).join("");
}


/* ------------------------------------------------------------------ *
 *  STEPS
 * ------------------------------------------------------------------ */

function showStep(name, on) {
  /* a step that is not yet reachable stays on the page, greyed (pending),
     so the reader sees the whole road from the start */
  const el = document.querySelector(`.step[data-step="${name}"]`);
  el.classList.remove("hidden");
  el.classList.toggle("pending", !on);
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
  /* A different meet is a different world: its divisions, its fields, its
     course override and every edit made to them belong to the old one. */
  resetMeetScoped();
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
         <label><input type="radio" name="racemode" value="custom">
           Custom groups</label>
       </div>
       <div class="mc-groups hidden" id="mc-groups"></div>
       <label class="mc-coalesce hidden" id="mc-coalesce">
         <input type="checkbox" id="coalesce">
         <span class="co-lab">Coalesce a school in two divisions into one
           squad<span class="co-note">Boys and girls are never merged -
           they always score as two teams.</span></span>
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
    /* A picked chip wears its race's colour, so the chips and the blocks
       below them agree at a glance about which races exist. */
    const chip = (label, div, on, col) =>
      `<button class="race-chip${on ? " is-on" : ""}" data-div="${div}"` +
      `${col ? ` style="--race:${col}"` : ""}>${esc(label)}</button>`;
    const quick = quickSets(races);
    /* A quick-select is LIT when everything it names is already picked, the
       same rule "All races" uses -- so it reads as a state, not a verb. */
    const setOn = (ids) => ids.every((i) => state.divs.includes(i));
    box.innerHTML =
      `<span class="mc-races-label">Races:</span>` +
      chip("All races", "",
           races.length > 0 && state.divs.length === races.length) +
      quick.map((qs, i) =>
        `<button class="race-chip quick${setOn(qs.ids) ? " is-on" : ""}" ` +
        `data-set="${i}">${esc(qs.label)}</button>`).join("") +
      races.map((r) => {
        const bits = [r.label];
        if (r.gender) bits.push(r.gender === "M" ? "Boys" : "Girls");
        if (r.distance) bits.push(`${Math.round(r.distance)}m`);
        _divLabels.set(String(r.div_id), bits.join(" \u00b7 "));
        const id = String(r.div_id);
        const gi = groupIndex().get(id);
        return chip(`${bits.join(" \u00b7 ")} (${r.n_results})`, id,
                    state.divs.includes(id),
                    gi === undefined ? null : raceColour(gi));
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
        // A preset REWRITES the grouping; custom keeps whatever is there.
        state.groups = r.value === "custom"
          ? normalizeGroups(state.divs, state.groups)
          : groupsForMode(state.divs, r.value);
        updateModeHint();
        saveState();
        renderField();          // the group labels above the blocks move
      });
    });
    /* Delegated: the rows are re-rendered on every change, so a listener
       per <select> would be rebound each time and leak the old ones. */
    $("mc-groups").addEventListener("change", (e) => {
      const sel = e.target.closest(".grp-sel");
      if (!sel) return;
      state.groups = normalizeGroups(
        state.divs,
        moveDiv(state.groups, sel.dataset.div, parseInt(sel.value, 10)));
      /* ! THE MODE FOLLOWS THE GROUPING, not the other way round. Dropping
           back to one-per-division by hand IS separate mode, and the radios
           should say so rather than claiming "custom" for a grouping that is
           not custom at all. */
      state.raceMode = modeOfGroups(state.divs, state.groups);
      const back = $("mc-mode")
        .querySelector(`input[name=racemode][value="${state.raceMode}"]`);
      if (back) back.checked = true;
      updateModeHint();
      saveState();
      renderField();
    });
    $("coalesce").addEventListener("change", (e) => {
      state.coalesce = e.target.checked;
      updateModeHint();
      saveState();
      /* ! THE CARDS SAY WHAT THE CHECKBOX DOES, so they have to be redrawn
           when it changes -- otherwise the one place that explains coalesce
           is stale exactly when someone is toggling it to find out. */
      renderField();
    });
    /* A restored session had its mode in memory before these controls
       existed; put it back on them. */
    const chosen = $("mc-mode")
      .querySelector(`input[name=racemode][value="${state.raceMode}"]`);
    if (chosen) chosen.checked = true;
    $("coalesce").checked = state.coalesce;
    updateModeHint();

    /* ! ONE REPAINT, TWO CALLERS. A division chip and a quick-select change
         the same three things about every chip -- lit or not, which race's
         colour it wears, and which one is open for editing -- so working
         that out twice is two chances to disagree. */
    /* ⚠ HOISTED SO EVERY PATH CAN CALL IT (owner, 2026-09-01: "the list
         just doesn't update color at all unless you change team in
         combined"). It was reachable only from the chip click handler, so
         switching Separate/Combined, or moving a division between races,
         regrouped everything and left the chips wearing the OLD colours --
         the one place the grouping is summarised was the one place that did
         not follow it. updateModeHint runs on all of those, so the hook
         hangs there. */
    _repaintChips = repaintChips;
    function repaintChips() {
      const every = races.map((r) => String(r.div_id));
      const at = groupIndex();
      box.querySelectorAll(".race-chip").forEach((x) => {
        if (x.dataset.set !== undefined) {
          const ids = quick[Number(x.dataset.set)].ids;
          x.classList.toggle("is-on",
                             ids.every((i) => state.divs.includes(i)));
          return;
        }
        const d = x.dataset.div || null;
        // Regrouping moves colours around, so they are re-set here rather
        // than only at render.
        const gi = d === null ? undefined : at.get(d);
        if (gi === undefined) x.style.removeProperty("--race");
        else x.style.setProperty("--race", raceColour(gi));
        /* "All races" is lit when everything is picked -- it is a selection
           now, not the absence of one. */
        x.classList.toggle("is-on",
                           d === null
                             ? (every.length > 0
                                && state.divs.length === every.length)
                             : state.divs.includes(d));
        x.classList.toggle("is-editing",
                           state.divs.length > 1 && d === state.meet.div);
      });
    }

    box.querySelectorAll(".race-chip").forEach((b) => {
      b.addEventListener("click", () => {
        const every = races.map((r) => String(r.div_id));

        /* ★ A QUICK-SELECT IS A TOGGLE OVER A SET, and additive on the way
             in. Picking "All boys" and then "All girls" gives you both,
             which is the point -- a replacing selection would make the
             second click undo the first. Everything already picked?
             Unpick it, so the same button gets you back. */
        if (b.dataset.set !== undefined) {
          const ids = quick[Number(b.dataset.set)].ids;
          const on = ids.every((i) => state.divs.includes(i));
          state.divs = on ? state.divs.filter((d) => !ids.includes(d))
                          : state.divs.concat(
                              ids.filter((i) => !state.divs.includes(i)));
          state.groups = state.raceMode === "custom"
            ? normalizeGroups(state.divs, state.groups)
            : groupsForMode(state.divs, state.raceMode);
          state.meet.div = state.divs.length
            ? (state.divs.includes(state.meet.div) ? state.meet.div
                                                   : state.divs[0])
            : null;
          repaintChips();
          updateModeHint();
          saveState();
          loadField();
          return;
        }

        const div = b.dataset.div || null;
        state.divs = toggleDiv(state.divs, div, every);
        /* ! REGROUPED, NOT REBUILT. A division added or removed must not
             disturb a grouping the user set by hand -- normalizeGroups keeps
             the surviving groups and gives anything new a race of its own.
             Under a preset the mode decides, so it is rebuilt there. */
        state.groups = state.raceMode === "custom"
          ? normalizeGroups(state.divs, state.groups)
          : groupsForMode(state.divs, state.raceMode);
        state.meet.div = state.divs.length
          ? (state.divs.includes(div) ? div : state.divs[0])
          : null;
        repaintChips();
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
  /* ★ THE MODE DECIDES SCORING, NOT LAYOUT (owner, 2026-09-01: "I wonder if
   *   in combined we should separate each div still in appearance"). Yes --
   *   and it is the same answer as #85's, applied consistently. A combined
   *   race is still built division by division on the server
   *   (_combinedRoster loops them and unions the result), so showing one
   *   merged block hid the structure the request actually has: which teams
   *   came from which division, and which school is in two of them.
   *
   *   Every picked division gets its own block either way. Separate scores
   *   them apart, combined scores them together, and the edits are per
   *   division in both -- which is what mergedEdits was already written to
   *   read. */
  return state.divs.length ? state.divs.slice() : [state.meet.div ?? null];
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
  const blocks = activeBlocks();
  /* Headed whenever there is more than one race on screen -- a single block
     is the page as it always looked and needs no label to tell it apart. */
  const separate = blocks.length > 1;
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

  /* Which group each division is scored in, and whether that group has more
     than one member -- a block scored WITH another has to say so, or the two
     look independent and the prediction surprises you. */
  const groupOf = groupIndex();

  $("field").innerHTML = blocks.map((d) => {
    const gi = groupOf.get(d);
    const g = gi === undefined ? null : state.groups[gi];
    const shared = g && g.length > 1;
    /* The block wears its race's colour. Two blocks the same colour are one
       race -- which is the question this answers without being read. */
    const col = gi === undefined ? "" : ` style="--race:${raceColour(gi)}"`;
    return `<section class="div-field${shared ? " in-group" : ""}" `
      + `${col} data-div-block="${divKey(d)}">
       ${separate ? `<h4 class="div-field-h">${esc(divLabel(d))}${
          shared ? `<span class="grp-note">scored with ${
            esc(g.filter((x) => x !== d).map(divLabel).join(", "))}</span>`
                 : ""}</h4>` : ""}
       <div class="fs"></div><div class="fg"></div>
       ${/* ★ ONE BOX PER RACE. It used to sit below every block, so with
             several divisions on screen a single bar had to guess which
             race you meant. Inside the block, the race IS the answer. */""}
       <div class="pick-wrap fieldpick">
         <input class="school-input" type="search" autocomplete="off"
                placeholder="Add or remove a team">
         <div class="pick-results hidden"></div>
       </div>
     </section>`;
  }).join("");

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
  renderViewSel();
}


/*
 * ★ ONE Show: CONTROL FOR THE WHOLE FIELD, in the header. state.view is a
 *   single global value and always was; rendering the control inside each
 *   race block meant N copies of one setting, all of them below the fold on
 *   a meet with several divisions.
 *
 * ! IT SITS BESIDE THE COUNTS in every mode, including a single race -- one
 *   place to look for it rather than a control that moves when a division is
 *   picked.
 */
const _VIEW_NAMES = {none: "Nothing", teams: "Teams", all: "Rosters"};

/* The three buttons. `on` is the value to light, or null when the races
   disagree -- the overall control must not claim a state that is not true of
   all of them. */
function viewButtons(on) {
  return ` <span class="viewsel">Show:` + ["none", "teams", "all"].map((v) =>
    `<button class="vbtn${on === v ? " is-on" : ""}" data-view="${v}">` +
    `${_VIEW_NAMES[v]}</button>`).join("") + `</span>`;
}

function renderViewSel() {
  const el = $("view-sel");
  if (!el) return;
  const views = activeBlocks().map((d) => editsFor(d).view);
  // Every race agreeing is a state the overall control can show; a mix is
  // not, so nothing is lit rather than one button lying about the others.
  const all = views.every((v) => v === views[0]) ? views[0] : null;
  el.innerHTML = viewButtons(all);
  renderSquadBoxes();
}


/* The whole-meet totals, for the header above the per-race blocks. Plain
   text: Undo belongs to a race, and there is no one race here to apply it
   to. */
function renderFieldRollup(blocks) {
  const loaded = blocks.filter((d) => editsFor(d).field);
  const n = (v) => `<strong>${v}</strong>`;
  if (!loaded.length) {
    $("field-summary").innerHTML = `${n(blocks.length)} races - loading\u2026`;
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
    (loaded.length < blocks.length ? ` - loading the rest\u2026` : "");
}


/* One race's field, rendered into the elements it was handed. */
/*
 * ★ WHICH SCHOOLS ARE ENTERED TWICE IN THIS RACE (owner, 2026-09-01: "I
 *   thought you were putting [the division] on the schools to make coalesce
 *   more obvious?"). It went on the RESULTS table only, which is the wrong
 *   half: by then the decision has been made. The place it matters is the
 *   field editor, BEFORE predicting, on the card itself.
 *
 * ! {school -> [other divisions of the SAME RACE it is also in]}. Only the
 *   same race, because a school in two divisions that are scored separately
 *   is simply two teams and coalesce has nothing to say about it.
 */
function alsoInThisRace(div) {
  const gi = groupIndex().get(div);
  const g = gi === undefined ? null : state.groups[gi];
  const out = new Map();
  if (!g || g.length < 2) return out;

  /* ⚠ INTERSECTED WITH THIS DIVISION'S OWN TEAMS. Without this it returned
       every school in the other divisions too -- harmless, since only this
       block's teams are looked up, but a map that does not mean what its
       name says is a trap for the next reader. */
  const here = new Set(
    (editsFor(div).field ? editsFor(div).field.teams : [])
      .filter((t) => t.runners.length || t.dropped.length)
      .map((t) => t.school));
  if (!here.size) return out;

  for (const other of g) {
    if (other === div) continue;
    const e = editsFor(other);
    for (const t of (e.field ? e.field.teams : [])) {
      if (!here.has(t.school)) continue;
      if (!t.runners.length && !t.dropped.length) continue;
      if (!out.has(t.school)) out.set(t.school, []);
      out.get(t.school).push(divLabel(other));
    }
  }
  return out;
}

function renderFieldBlock(sumEl, gridEl) {
  const f = state.field;
  const teams = f.teams.filter((t) => t.runners.length || t.dropped.length);
  const alsoIn = alsoInThisRace(state.meet.div);
  /* Whether this race has more than one division at all -- which decides
     whether the cards carry a second line, not whether THIS school does. */
  const _gi = groupIndex().get(state.meet.div);
  const grouped = _gi !== undefined && (state.groups[_gi] || []).length > 1;
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
    (f.when === "asran" ? ` - as raced` : ` - current squads`) +
    `</span>` +

    /* ⚠ THE Show: CONTROL IS NOT HERE ANY MORE -- see renderViewSel. It was
         rendered per block, so with several divisions on screen there were
         several copies of one global setting and you had to scroll to reach
         the nearest (owner, 2026-09-01: "maybe we should have like an
         overall show/team/rosters rule just so you don't have to scroll all
         the way"). state.view was ALWAYS global; only the control was not.
       ! UNDO STAYS PER BLOCK, because droppedTeams is per division: "undo
         removing X" has to name a race to be true. */
    /* ★ THIS RACE'S OWN Show:, beside its counts. The header carries an
     *   overall one that writes to every block; this sets one race, which is
     *   what lets you read D2's teams with D3 still shut. */
    viewButtons(state.view) +
    squadButtons(wholeOn(state.meet.div) ? "whole" : "fielded", f.when === "asran") +
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
    <details class="team-card" data-team="${esc(t.school)}" data-div="${esc(state.meet.div || "")}"
             ${state.open.has(t.school) ? "open" : ""}>
      <summary class="team-name">
        <span class="t-label">
          ${/* ★ THE NAME OWNS THE TOP LINE AND IS CLIPPED THERE (owner,
                2026-09-01: "the name on its top line, with ellipses if it
                is done as so"). Letting it wrap pushed the note down and
                made the card taller than its neighbours. */""}
          <span class="t-name"><a class="lnk"
             href="/school/${encodeURIComponent(t.school)}"
             >${esc(schoolWithState(t.school, t.state))}</a></span>
          ${/* ★ AND THE SECOND LINE IS ALWAYS THERE IN A GROUPED RACE, even
                for a school entered only once ("if they don't have one just
                say so"). A note that only SOME cards carry gives every card
                a different height, which is the ragged grid the owner is
                looking at -- and its absence is information too: this school
                is in one division of the race, not two. */""}
          ${grouped ? `<span class="t-also${
              alsoIn.has(t.school) ? (state.coalesce ? " is-merged" : "")
                                   : " is-solo"}" title="${
              !alsoIn.has(t.school)
                ? "Entered in this division only."
                : state.coalesce
                  ? "Coalesce is on: these entries score as ONE squad, "
                    + "capped at seven."
                  : "Coalesce is off: these entries score as SEPARATE teams."
            }">${alsoIn.has(t.school)
                  ? `${state.coalesce ? "+ " : "also in "}${
                      esc(alsoIn.get(t.school).join(", "))}`
                  : `${esc(divLabel(state.meet.div))} only`}</span>` : ""}
        </span>
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
        <details class="add-menu">
          <summary class="squad-btn">+ Add</summary>
          <div class="add-menu-body">
            <button class="squad-btn" data-squad="${esc(t.school)}">from the squad</button>
            <button class="squad-btn" data-squad-all="${esc(t.school)}"
                    title="Every current runner of ${esc(t.school)} onto this card">the whole squad</button>
            <button class="squad-btn" data-anyone="${esc(t.school)}">anyone</button>
          </div>
        </details>
        <div class="squad-list hidden" data-squad-for="${esc(t.school)}"></div>
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
  /* ★ `div` IS A GROUP NOW -- an array of divisions scored as one race, or a
     single division, or null for the whole meet. A group of several carries
     EVERY one of their edits, because editsFor(null) is the all-races record
     and a team removed while D1 was open would otherwise be dropped from the
     request and race anyway. */
  const group = Array.isArray(div) ? div : (div == null ? [] : [div]);
  const combining = group.length > 1;
  const e = combining ? mergedEdits(group)
                      : editsFor(group.length ? group[0] : null);
  const q = new URLSearchParams({
    mode: state.when === "asran" ? "rerun_exact" : "rerun",
    meet_id: state.meet.id,
    sport: state.meet.sport,
  });
  if (group.length === 1) q.set("div_id", group[0]);
  /* ★ SEVERAL DIVISIONS AS ONE RACE (issues #86, #89). A group of one sends
     div_id and is the ordinary single-division request; a group of several
     sends div_ids and the server unions them. Coalesce only means anything
     for the second kind. */
  if (combining) {
    q.set("div_ids", group.join(","));
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
  loadWeather(state.meet && state.meet.div);

  const path = state.who === "individual"
    ? "/api/predict/individual" : "/api/predict/team";

  /* ★ ONE REQUEST PER DIVISION, and one section per result (issue #85). The
     divisions are SEPARATE RACES -- scoring them together would be a
     different feature -- so nothing is merged: each is the existing
     single-division request, run once per selection.
     ! INDIVIDUAL MODE HAS NO DIVISIONS: the athletes were named directly. */
  /* ★ ONE REQUEST PER GROUP, and one section per result (issues #85, #89).
     A group is a race: one division or several, scored together. With
     nothing picked there is one race -- the whole meet.
     ! INDIVIDUAL MODE HAS NO DIVISIONS: the athletes were named directly. */
  const targets = state.who !== "team" ? [state.meet.div]
                : (state.groups.length ? state.groups.map((g) => g.slice())
                                       : [null]);

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
        ? `<section class="div-result"
                    style="--race:${raceColour(targets.indexOf(div))}">
             <h3 class="div-result-h">${esc(divLabel(div))}</h3>${body}
           </section>`
        : body);
    }
    setStatus("", false);
    /* ★ SHARE, ABOVE A TEAM RESULT (281). The link carries the first race's
         request: the page restores the meet from it and its preview is the
         prediction card drawn from the same request. */
    const share = state.who === "team" ? shareBox(buildQuery(targets[0])) : "";
    $("output").innerHTML = share + parts.join("");
  } catch (err) {
    setStatus("Could not reach the server: " + err.message, true);
  } finally {
    state.busy = false;
    $("predict").disabled = false;
  }
}

function shareBox(q) {
  const url = location.origin + "/predictions?" + q.toString();
  const label = state.meet && state.meet.label ? state.meet.label : "A race";
  return `<div class="hdr-row share-row">
    <button type="button" class="share-box share-btn" data-share-title="${esc(label)} predicted on Racecast"
            data-share-url="${esc(url)}" title="Share this prediction; the preview is its card">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M14 9V5l7 7-7 7v-4.1c-5 0-8.5 1.6-11 5.1 1-5 4-10 11-11z"/></svg>
      <span>Share</span>
    </button></div>`;
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
/* ★ TWO WEATHERS (owner, 2026-09-06). The headline time is the race at the
   venue's NORMAL weather for that time of year; when a forecast exists
   (16 days out at most) the same race at the forecast weather sits beside
   it with the difference. Both come from the API in one call. */
function weatherLine(d) {
  const f = d.forecast;
  const normal = d.normal_weather
    ? `<span class="wx-normal" title="the venue's usual weather at this time of year, at the race hour">normal weather: ${esc(d.normal_weather)}</span>`
    : (d.weather_basis === "none"
       ? `<span class="wx-normal">weather not known for this venue</span>` : "");
  if (!f || f.seconds === undefined) {
    return normal ? `<div class="wx-line">${normal}</div>` : "";
  }
  const sign = f.delta > 0 ? "+" : "";
  const when = f.hour_local !== undefined ? ` at ${f.hour_local}:00` : "";
  return `<div class="wx-line">${normal}
    <span class="wx-forecast" title="Open-Meteo forecast fetched ${esc(f.fetched_at || "")}">
      forecast${when}: ${esc(f.conditions || "")} \u2192 <b>${fmtTime(f.seconds)}</b>
      <em>${sign}${f.delta.toFixed(1)}s</em></span></div>`;
}

function renderAthleteSet(d) {
  /* Several athletes at one race: a table, sorted fastest first, because the
     question people ask with two names is "who wins". */
  const anyFc = (d.athletes || []).some((a) => a.forecast && a.forecast.seconds !== undefined);
  const rows = [...(d.athletes || [])]
    .filter((a) => a.seconds !== undefined)
    .sort((a, b) => a.seconds - b.seconds)
    .map((a, i) => `<tr><td class="rank">${i + 1}</td>
      <td>${esc(a.name || "")}</td>
      <td>${fmtTime(a.seconds)}</td>
      <td>${a.low !== undefined
            ? `${fmtTime(a.low)} \u2013 ${fmtTime(a.high)}` : ""}</td>${
      anyFc ? `<td>${a.forecast && a.forecast.seconds !== undefined
                ? `${fmtTime(a.forecast.seconds)} <em>${a.forecast.delta > 0 ? "+" : ""}${a.forecast.delta.toFixed(1)}s</em>`
                : ""}</td>` : ""}</tr>`)
    .join("");
  const first = (d.athletes || []).find((a) => a.forecast && a.forecast.conditions);
  const note = first
    ? `<p class="wx-line">Predicted is the race at normal weather; Forecast is the same race at the forecast
       (${esc(first.forecast.conditions)}${first.forecast.hour_local !== undefined ? ` at ${first.forecast.hour_local}:00` : ""}).</p>`
    : "";
  return `<table class="rk"><thead><tr>
      <th>#</th><th>Athlete</th><th>Predicted</th><th>Range</th>${anyFc ? "<th>Forecast</th>" : ""}
    </tr></thead><tbody>${rows}</tbody></table>${note}`;
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
    ${weatherLine(d)}
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
      <td class="rank">${t.score === null ? " - " : i + 1}</td>
      <td>${esc(schoolWithState(t.team, t.state))}${t.divs
          ? ` <span class="t-divs" title="Coalesced: one squad, drawn from `
            + `these divisions and capped at seven.">${
              esc(t.divs.join(" + "))}</span>`
          : ""}</td>
      <td>${t.score === null ? esc(t.note || "incomplete") : t.score}</td>
      ${scored ? `<td class="actual">${t.actual_score ?? " - "}</td>` : ""}
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

bindPicker($("athlete-input"), $("athlete-results"), "athlete",
           renderAthleteRows, (d) => {
  /* ! THE ID COMES FROM THE ROW, NOT FROM A LINK. It used to be scraped out
       of "/athlete/123" with a regex, which is one URL-shape change away
       from silently matching nothing. /api/predict/athletes returns the
       person_id as a field. */
  const id = d.pid;
  if (!id) { setStatus("That row carried no athlete id.", true); return; }
  // Same control both ways: clicking a chosen athlete takes them out again.
  if (state.athletes.some((a) => a.id === id)) {
    state.athletes = state.athletes.filter((a) => a.id !== id);
  } else {
    state.athletes.push({
      id: id, name: d.label,
      school: d.school || null,
      year: d.year || null,
      rating: d.rating === "" ? null : Number(d.rating),
    });
  }
  renderAthletes();
  saveState();
}, false, { rows: athleteRows });

/* ★ A WHOLE SQUAD IN ONE CLICK (owner, 2026-09-06). Pick a school and its
   current squad joins the list, strongest first, up to the twelve the
   individual API takes; anyone already listed is left where they are. */
const MAX_ATHLETES = 12;
bindPicker($("squad-input"), $("squad-results"), "school", renderSimple,
           async (d) => {
  const school = d.school || d.label;
  if (!school) return;
  const p = new URLSearchParams({ school: school, sport: state.meet.sport });
  const g = state.field && state.field.gender;
  if (g) p.set("gender", g);
  setStatus(`Adding ${school}\u2026`, false);
  try {
    const res = await fetch("/api/predict/squad?" + p.toString());
    const data = await res.json();
    const runners = (data.runners || []).filter((r) => r.person_id != null);
    if (!runners.length) {
      setStatus(data.error || `${school} has no current squad to add.`, true);
      return;
    }
    let added = 0, room = MAX_ATHLETES - state.athletes.length;
    for (const r of runners) {
      const id = String(r.person_id);
      if (state.athletes.some((a) => a.id === id)) continue;
      if (room <= 0) break;
      state.athletes.push({ id: id, name: r.name, school: r.school || school,
                            year: data.season_year || null,
                            rating: r.rating == null ? null : Number(r.rating) });
      added += 1; room -= 1;
    }
    const left = runners.length - added;
    setStatus(added
      ? `Added ${added} from ${school}${left > 0 ? ` (${left} not added: ${MAX_ATHLETES} at most)` : ""}.`
      : `${school}: everyone is already listed, or the list is full (${MAX_ATHLETES}).`,
      !added);
    renderAthletes();
    saveState();
  } catch (err) {
    setStatus("Could not reach the server: " + err.message, true);
  }
});

/* ★ THE CONDITIONS THE TARGET WOULD BE RUN IN, shown on every Predict
   whether or not the model answers: the venue's normal weather for that
   time of year at the race hour, and the forecast when the date is within
   sixteen days. Same target parameters the prediction sends. */
async function loadWeather(div) {
  const el = $("wx-target");
  if (!el) return;
  try {
    const res = await fetch("/api/predict/weather?" + buildQuery(div).toString());
    const d = await res.json();
    if (!d.available) { el.innerHTML = ""; return; }
    const when = d.hour_local != null ? ` at ${d.hour_local}:00` : "";
    const normal = d.normal_text
      ? `<span class="wx-normal">normal weather${when}: ${esc(d.normal_text)}</span>`
      : (d.has_venue ? `<span class="wx-normal">no weather history for this venue yet</span>`
                     : `<span class="wx-normal">this venue has no coordinates, so no weather</span>`);
    const fc = d.forecast_text
      ? `<span class="wx-forecast" title="Open-Meteo, fetched ${esc((d.forecast && d.forecast.fetched_at) || "")}">forecast for ${esc(d.date || "")}${when}: ${esc(d.forecast_text)}</span>`
      : (d.date ? `<span class="wx-forecast">no forecast yet for ${esc(d.date)} (forecasts reach 16 days out)</span>` : "");
    el.innerHTML = `<div class="wx-line">${normal}${fc}</div>`;
  } catch (err) {
    el.innerHTML = "";
  }
}

/*
 * ★ A TABLE, NOT CHIPS (owner, 2026-09-01: "update the specific athletes UI
 *   to be like the other tab's ui... maybe it should be like a table with
 *   entries"). A chip carries a name and nothing else, so two runners called
 *   J. Smith were indistinguishable, and there was no way to check you had
 *   picked the right one before predicting. Same columns the team side's
 *   rosters show: who, where, and how fast.
 *
 * ! A RESTORED SESSION MAY HAVE NAME-ONLY ENTRIES, from before the picker
 *   carried the rest. They render with blank cells rather than being dropped
 *   -- the athlete is still a valid pick, the page just knows less about
 *   them until they are re-picked.
 */
function renderAthletes() {
  const el = $("athlete-chosen");
  el.classList.toggle("hidden", state.athletes.length === 0);
  if (!state.athletes.length) { el.innerHTML = ""; return; }
  el.innerHTML =
    `<table class="chosen-tbl">
       <thead><tr>
         <th>Athlete</th><th>School</th><th class="num">Season</th>
         <th class="num">Rating</th><th></th>
       </tr></thead>
       <tbody>` +
    state.athletes.map((a) => `
       <tr>
         <td><a class="lnk" href="/athlete/${encodeURIComponent(a.id)}"
                >${esc(a.name)}</a></td>
         <td>${esc(a.school || "")}</td>
         <td class="num">${esc(a.year == null ? "" : a.year)}</td>
         <td class="num">${a.rating == null || isNaN(a.rating)
                           ? "" : esc(a.rating)}</td>
         <td class="num"><button class="r-x"
             data-drop-athlete="${esc(a.id)}" title="Remove">&times;</button></td>
       </tr>`).join("") +
    `</tbody></table>`;
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

/* A school name goes into an attribute SELECTOR here, not into markup, so
   esc() is the wrong tool -- CSS.escape is the right one. Guarded because it
   is missing in older browsers, where a quote-free name still works. */
function cssEscape(v) {
  return (window.CSS && CSS.escape) ? CSS.escape(v)
                                    : String(v).replace(/["\\]/g, "\\$&");
}

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

/*
 * ★ EVERYTHING THAT DESCRIBES ONE MEET, CLEARED TOGETHER (owner,
 *   2026-09-01: "the page seems to remember too much... it shouldn't
 *   remember the course entered").
 *
 * ⚠ AND THE COURSE WAS STILL BEING APPLIED, not merely displayed. Picking a
 *   new meet cleared its divisions, its groups and its edits but not
 *   state.course -- so meet B was predicted on meet A's course, with meet
 *   A's name sitting in a box nobody had touched. A stale box is untidy; a
 *   stale OVERRIDE is a wrong answer.
 *
 * ! THE BOX AND THE STATE GO TOGETHER. The "Change" path cleared the state
 *   and left the input's text; chooseMeet cleared neither. One function so
 *   there is no third way to get it half right.
 *
 * ! raceMode AND coalesce GO TOO. Both are statements about a particular
 *   meet's divisions -- "score these two as one race", "merge a school
 *   entered in both" -- and neither means anything about the next meet.
 */
function resetMeetScoped() {
  state.course = null;
  state.raceMode = "separate";
  state.coalesce = false;
  state.divs = [];
  state.groups = [];
  const box = $("t-course");
  if (box) {
    box.value = "";
    /* ! BACK TO THE TEMPLATE'S WORDING, not empty. loadRaces replaces this
         with the new meet's actual course name once it knows it; until then
         an empty placeholder reads as a box with nothing to say. */
    box.placeholder = "the meet\u2019s own course";
  }
  const hint = $("t-course-hint");
  if (hint) {
    hint.textContent = "Defaults to the course this meet was run on. "
                     + "Pick another to run this same field somewhere else.";
  }
  const co = $("coalesce");
  if (co) co.checked = false;
}

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
  const target = div === undefined ? (state.meet.div ?? null) : div;
  const e = editsFor(target);
  /* ⚠ THIS USED TO `return` WITH NOTHING SAID, which is the worst way for an
       action to fail: the row is clicked, and the page is identical. */
  if (!e.field) {
    setStatus(`${divLabel(target)} has not finished loading - try again `
              + `in a moment.`, true);
    return;
  }
  setStatus(`Loading ${school}\u2026`, false);
  try {
    const squad = await loadSquad(school, e.field.gender);
    if (!squad.runners.length) {
      /* ⚠ "HAS NOT RACED THIS SEASON" WAS A FALSE EXPLANATION OF A TRUE
           RESULT. A boys race filters the squad to boys, so an all-girls
           school correctly returns nobody -- and the message blamed the
           data. Carondelet was racing; it was just not racing here. */
      setStatus(squad.other_gender
        ? `${school} is a ${squad.gender === "M" ? "girls" : "boys"}-only `
          + `school - it cannot run in this `
          + `${squad.gender === "M" ? "boys" : "girls"} race.`
        : `No one from ${school} has raced this season.`, true);
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

    /* ★ A SUCCESSFUL ADD MUST LOOK DIFFERENT FROM A FAILED ONE, and it did
     *   not: this cleared the status, so "added" and "silently did nothing"
     *   were the same empty message. Reported as "adding doesn't work, it
     *   just doesn't do anything" -- for an add that was very likely
     *   working, landing alphabetically somewhere down a long grid with
     *   nothing pointing at it.
     *
     * ! AND IF THE ROSTERS ARE HIDDEN, SHOW THEM. Adding a team while the
     *   view is "Nothing" put a card into a grid that is display:none. The
     *   add succeeded and the screen could not have looked more like a
     *   no-op. */
    if (state.view === "none") state.view = "teams";
    renderField();
    setStatus(`Added ${school} - `
              + `${squad.runners.slice(0, 7).length} runners.`, false);
    /* ⚠ NO SCROLLING. This used to pull the new card into view, which moves
         the page away from the search box you are still typing in -- and
         adding teams is something you do several times in a row (owner,
         2026-09-01: "when you add a team it should probably not scroll away
         from the search bar"). The status line says what happened and the
         card is open; neither needs the viewport moved. */
  } catch (err) {
    setStatus(`Could not load ${school}.`, true);
  }
}


/* One fetch per school, cached: a card can be opened and closed repeatedly,
   and the squad does not change while the page is open. */
const squadCache = new Map();

/* One runner onto one team's card, with their rating (the rating is how
   you judge whether adding them was right; null stays blank on purpose).
   Shared by the add button, "+ Add whole squad" and "every team". */
function addRunner(school, pid, name, rating, div) {
  /* `div` names the race; undefined means the focused one. Writes through
     editsFor, the per-division record, so a grouped race edits the right
     block. */
  const ed = editsFor(div === undefined ? state.meet.div : div);
  pid = String(pid);
  const team = (ed.field?.teams || []).find((t) => t.school === school);
  if (team && !team.runners.some((r) => String(r.person_id) === pid)) {
    team.runners.push({ person_id: pid, name: name, rating: rating, added: true });
    team.dropped = (team.dropped || []).filter((r) => String(r.person_id) !== pid);
    team.runners.sort((a, b) => (b.rating ?? -Infinity) - (a.rating ?? -Infinity));
  }
  ed.removed.delete(pid);
  ed.added.push({ person_id: pid, name: name, school: school });
  ed.open.add(school);          // keep the card you are editing open
}

/* ★ A WHOLE SQUAD IN ONE CLICK (owner, 2026-09-06): every current runner
   of the school not yet on the card. Returns how many joined; the ids
   are remembered on the race so the checkbox can take them out again. */
async function addWholeSquad(school, div) {
  const ed = editsFor(div === undefined ? state.meet.div : div);
  const squad = await loadSquad(school, ed.field?.gender);
  const team = (ed.field?.teams || []).find((t) => t.school === school);
  const have = new Set((team?.runners || []).map((r) => String(r.person_id)));
  ed.wholeAdded = ed.wholeAdded || new Set();
  let n = 0;
  for (const r of squad.runners || []) {
    if (have.has(String(r.person_id))) continue;
    addRunner(school, r.person_id, r.name, r.rating == null ? null : Number(r.rating), div);
    ed.wholeAdded.add(String(r.person_id));
    n += 1;
  }
  return n;
}

/* Whole squads for every team of one race (div), or of every race. */
/* ! THE CARDS STAY AS THEY WERE (owner, 2026-09-06: "should not expand
     the roster view"): addRunner opens the card it edits, which is right
     for one runner and wrong for thirty teams at once, so the open set
     is put back afterwards. And every squad is fetched AT ONCE, not one
     after another: thirty sequential round trips was the slowness. */
async function wholeSquadsFor(divs) {
  let n = 0, teams = 0;
  const jobs = [];
  for (const div of divs) {
    const ed = editsFor(div);
    const wasOpen = new Set(ed.open);
    for (const t of (ed.field?.teams || [])) {
      teams += 1;
      jobs.push(loadSquad(t.school, ed.field?.gender).catch(() => null));
    }
    ed._wasOpen = wasOpen;
  }
  await Promise.all(jobs);            // the cache is warm; the adds are instant
  for (const div of divs) {
    const ed = editsFor(div);
    for (const t of (ed.field?.teams || [])) {
      try { n += await addWholeSquad(t.school, div); } catch (err) { /* one squad failing does not stop the rest */ }
    }
    ed.open = ed._wasOpen || ed.open;
    delete ed._wasOpen;
  }
  return { n, teams };
}

/* The checkbox unticked: the runners the whole-squad action put on this
   race's cards come off again; hand-added ones stay. */
function removeWholeSquads(div) {
  const ed = editsFor(div);
  const gone = ed.wholeAdded || new Set();
  if (!gone.size) return 0;
  for (const t of (ed.field?.teams || [])) {
    t.runners = t.runners.filter((r) => !gone.has(String(r.person_id)));
  }
  ed.added = ed.added.filter((a) => !gone.has(String(a.person_id)));
  const n = gone.size;
  ed.wholeAdded = new Set();
  return n;
}

/* ★ SQUADS: FIELDED | WHOLE, ONE PER RACE AND ONE OVERALL, EXACTLY LIKE
   Show (owner, 2026-09-06: "there should be a thing to flip for each
   division, and one for overall. These should be separate"). Each race
   block carries its own beside its counts and flips that race only; the
   header's writes to every race, and is lit only when every race agrees.
   Fielded is the roster as the results list it; Whole is every current
   runner of every team in the race. */
function wholeOn(d) {
  return (editsFor(d).wholeAdded || new Set()).size > 0;
}

/* Said in the words the field header uses (owner, 2026-09-06: "it
   doesn't explain very well"): a future race fields each team's top 7
   by prediction, a past one the runners who raced; the other setting
   puts every current runner of every team on its card. */
function squadButtons(on, asran) {
  const btn = (v, label, title) =>
    `<button class="vbtn${on === v ? " is-on" : ""}" data-whole="${v}" title="${title}">${label}</button>`;
  return ` <span class="viewsel">Squads:` +
    btn("fielded", asran ? "As raced" : "Top 7",
        asran ? "Each team's runners as the results list them"
              : "Each team's seven best by predicted time, the squad it would field") +
    btn("whole", "Everyone",
        "Every current runner of every team onto its card, not only the top 7") +
    `</span>`;
}

function renderSquadBoxes() {
  const el = $("squad-boxes");
  if (!el) return;
  const divs = activeBlocks();
  if (divs.length < 2) { el.innerHTML = ""; return; }   // one race: its own block has it
  const states = divs.map(wholeOn);
  const all = states.every((v) => v === states[0]) ? (states[0] ? "whole" : "fielded") : null;
  const asran = divs.every((d) => (editsFor(d).field || {}).when === "asran");
  el.innerHTML = squadButtons(all, asran);
}

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
  /* a card action addresses the card's own race, whichever block is focused */
  const card = e.target.closest("[data-div]");
  const cardDiv = card && card.classList.contains("team-card") ? (card.dataset.div || null) : undefined;
  const sqa = e.target.closest("[data-squad-all]");
  if (sqa) {
    const school = sqa.dataset.squadAll;
    sqa.disabled = true;
    addWholeSquad(school, cardDiv).then((n) => {
      setStatus(n ? `Added ${n} from ${school}.` : `${school}: everyone is already on the card.`, false);
      renderField(); renderSquadBoxes(); saveState();
    }).catch((err) => { setStatus("Could not load the squad: " + err.message, true); });
    return;
  }
  const whole = e.target.closest("[data-whole]");
  if (whole) {
    /* Which races: the header's control is the overall one and writes to
       every race; a block's writes to that block (focusBlock has already
       set state.meet.div from it). */
    const overall = !!whole.closest("#squad-boxes");
    const targets = overall ? activeBlocks() : [state.meet.div ?? null];
    if (whole.dataset.whole === "fielded") {
      let n = 0;
      for (const d of targets) n += removeWholeSquads(d);
      setStatus(n ? `Took ${n} whole-squad additions off again.` : "", false);
      renderField(); renderSquadBoxes(); saveState();
      return;
    }
    const todo = targets.filter((d) => !wholeOn(d));
    if (!todo.length) { renderField(); renderSquadBoxes(); return; }
    whole.disabled = true;
    wholeSquadsFor(todo).then(({ n, teams }) => {
      setStatus(n ? `Added ${n} across ${teams} teams.` : "Every squad is already whole.", false);
      renderField(); renderSquadBoxes(); saveState();
    });
    return;
  }
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
    const rating = add.dataset.rating;
    addRunner(add.dataset.school, add.dataset.add, add.dataset.name,
              rating === "" || rating === undefined ? null : Number(rating));
    renderField();
    return;
  }
  /* ⚠ MATCHED ON THE data- ATTRIBUTE, NOT ON THE CLASS. This read
       `.chip-x, .mc-change`, so the moment the chosen athletes became table
       rows -- whose remove button is a .r-x like every other one on the page
       -- the handler stopped matching and Remove did nothing. The attribute
       is what the handler actually acts on; the class is styling. */
  const x = e.target.closest("[data-drop-athlete], .mc-change, .chip-x");
  if (!x) return;
  if (x.dataset.dropAthlete) {
    state.athletes = state.athletes.filter((a) => a.id !== x.dataset.dropAthlete);
    renderAthletes();
    saveState();
  } else if (x.dataset.clear === "meet") {
    state.meet = null;
    resetMeetScoped();
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
    /* ★ WHICH RACES THIS APPLIES TO IS DECIDED BY WHERE IT WAS CLICKED. The
     *   header's control is the overall one and writes to every race; the
     *   one inside a block writes to that block. focusBlock has already set
     *   state.meet.div from the block, and finds nothing to set for the
     *   header -- so the two cannot be told apart that way, and the DOM
     *   answers instead. */
    const overall = !!vb.closest("#view-sel");
    const targets = overall ? activeBlocks()
                            : [state.meet.div ?? null];
    for (const d of targets) {
      const e = editsFor(d);
      e.view = vb.dataset.view;
      // Picking a view sets every card, which is what makes the three states
      // exclusive -- otherwise "Rosters" would leave cards the user had shut.
      e.open.clear();
      if (e.view === "all")
        for (const t of (e.field ? e.field.teams : [])) e.open.add(t.school);
    }
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

/* ⚠ THE HEADER, NOT JUST #field-summary, AND THAT WAS THE BUG. The overall
     Show: control was added to .field-head as a SIBLING of #field-summary,
     so its clicks reached neither this listener nor the one on #field, and
     the buttons did nothing at all ("now the show button is ineffective").
     Binding the container covers both, and any future control put beside
     them. */
document.querySelector(".field-head")
        .addEventListener("click", onSummaryClick);
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
