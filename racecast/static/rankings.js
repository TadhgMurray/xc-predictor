/*
 * rankings.js -- behaviour for the rankings page.
 *
 * Goes in racecast/static/. Loaded by templates/rankings.html.
 *
 * Talks to /api/rankings, which serves two boards from two tables:
 *
 *   ability       athlete_season -- one row per (person, pool, sport, year).
 *                 Season averages. Averaging kills per-race noise (~3.3%,
 *                 about 4 rating points), so this is the honest ranking of
 *                 athletes and it is the default.
 *
 *   performance   ranking_results -- one row per rated race. Single races,
 *                 full noise. Right for "best races", wrong for "best
 *                 athletes".
 *
 * No framework, no build step. Vanilla fetch and template strings.
 */

"use strict";

const PAGE_SIZE = 50;

/* College is division/region/conference; high school is league/division/
   section/district/county/class. The sets do not overlap, so one group is
   shown at a time -- see the note in rankings.html. */
/* Biggest grouping first, league last. college / high school, and the two
   sets never mix -- see the note in rankings.py. */
const COLLEGE_UNITS = ["division", "region", "conference"];
const HS_UNITS = ["state_div", "section", "section_div", "area", "league"];

const UNIT_KEYS = COLLEGE_UNITS.concat(HS_UNITS);

/*
 * Page state.
 *
 * `offset` is reset by anything that CHANGES THE QUERY (applying filters,
 * switching board) and preserved only by the pager. Without that you land on
 * page 7 of a two-page result and stare at an empty table wondering what broke.
 */
const state = {
  board: "ability",
  offset: 0,
  busy: false,
  // Set by a jump; the row for this person_id is marked once and then
  // cleared, so it does not stay highlighted as the user pages away.
  highlight: null,
  // The sort is HERE, not in a <select>. Clicking a column header sets it;
  // clicking the same header again flips the direction.
  sort: "rating",
  // Set once the user picks a pool, so switching boards stops choosing for
  // them. See syncBoard.
  poolTouched: false,
  // Set once the user clicks a column header. Until then the teams board
  // picks its own sort, which depends on whether one season is on screen.
  sortTouched: false,
  dir: ""            // "" = the column's own natural direction
};

const $ = (id) => document.getElementById(id);

/* ★ TWENTY IS A TRACK NUMBER. An athlete contests several events per meet
   indoors and out, so twenty rated marks is an ordinary season. A cross
   country season is eight to twelve races -- one a week for a term -- so the
   same floor does not raise the bar there, it empties the board.

   rankings.parseFilters applies the same split server-side; this keeps the
   box agreeing with what the API would do if the box were empty. */
/* ★ 999999 IS A SENTINEL, NOT A TIME. A DNS or DNF still needs a row and
   time_seconds is numeric, so the scrapers write 999999 -- 270,166 of them in
   results alone, against ~17,000 for any real value. The pace band keeps them
   out of every rating, but the race page renders whatever the number is, and
   "277:46.6" is a worse lie than saying nothing.

   ⚠ THE ROW IS KEPT, NOT HIDDEN. Somebody looking for an athlete in a race
     they did not finish should find them, with the reason -- not an absence
     they have to interpret. */
/* ⚠ A THRESHOLD, NOT THE VALUE. The sentinel is written as 999999 -- 270,166
     rows of it against ~17,000 for the commonest real time -- but it lands in
     a `real` column, and different feeds may round or scale it. Anything past
     a day is treated as no time: no race is a day long, and a comparison that
     depends on a float surviving exactly is one that fails silently. */
const DNF_SENTINEL = 86400;

function isNoTime(sec) {
  return sec === null || sec === undefined || Number(sec) >= DNF_SENTINEL;
}

/* "5000" -> {distance: "5000"}; "110|hurdles" -> {distance: "110",
   event: "hurdles"}; "field:shot_put" -> {event: "shot_put"}. */
function parseEventValue(v) {
  if (!v) return {};
  if (v.startsWith("field:")) return { event: v.slice(6) };
  const bar = v.indexOf("|");
  if (bar >= 0) return { distance: v.slice(0, bar), event: v.slice(bar + 1) };
  return { distance: v };
}

function isFieldSelection() {
  const d = $("distance");
  return !!(d && d.value && d.value.startsWith("field:"));
}

/* Metres -> "17.05 m (55-11.25)". Marks are stored in metres by the build
   (marks.parseMark); the feet-inches beside it is what most readers of a
   US board expect to see, and the conversion is exact. */
function fmtMark(m) {
  if (m === null || m === undefined) return " - ";
  const metres = Number(m);
  if (!isFinite(metres)) return " - ";
  const totalInches = metres / 0.0254;
  const feet = Math.floor(totalInches / 12);
  const inches = totalInches - feet * 12;
  return `${metres.toFixed(2)} m (${feet}-${inches.toFixed(2).padStart(5, "0")})`;
}


/* A school cell that links through, or a plain one when the name is missing.
   /school/<path:school_name> takes the raw string, so no id lookup. */
/* ★ THE TWO RACE ROUTES ARE DIFFERENT SHAPES. XC is
   /race/xc/<meet>/<div> and TF is /race/tf/<meet>/<event>/<div> -- three
   parts, because a track meet holds many events under one meet_id and the
   div alone does not identify a race.

   ⚠ Building the TF link with two parts matched no route at all, so every
     track link from these boards answered 404. event_id now rides in
     ranking_results for exactly this. */
function raceHref(r) {
  if (r.sport === "XC") return `/race/xc/${r.meet_id}/${r.div_id}`;
  if (r.event_id === null || r.event_id === undefined) return null;
  return `/race/tf/${r.meet_id}/${r.event_id}/${r.div_id}`;
}

/* A cell that links only when there is somewhere to link to -- a dead href is
   worse than plain text, because it looks live. */
function maybeLink(href, inner, cls) {
  const c = cls ? ` class="${cls}"` : "";
  return href ? `<td${c}><a href="${href}">${inner}</a></td>`
              : `<td${c}>${inner}</td>`;
}

function schoolCell(school, state) {
  const st = state ? ` <span class="state">${esc(state)}</span>` : "";
  if (!school) return `<td> - ${st}</td>`;
  return `<td><a href="/school/${encodeURIComponent(school)}">`
       + `${esc(school)}</a>${st}</td>`;
}

/* ★ THREE, ON EVERY BOARD THAT HAS A FLOOR (owner, 2026-09-02). The old
   20 TF / 8 XC floors kept thin seasons off the public board but hid
   whole rosters from anyone looking for themselves; three is the least a
   season average can rest on, and the box is still editable. */
function defaultMinRaces() {
  return 3;
}

/* Follow the sport unless the user has typed their own. Once they have, the
   number is theirs and switching sport must not overwrite it. */
function syncMinRaces() {
  /* An untouched box stays EMPTY and shows the placeholder: a "3" in the
     box would read as a filter that is on, while the API's default floor
     lets open seasons through at any count. The value is only the
     reader's once they type it. */
  const box = $("min_races");
  if (!box.dataset.touched) box.value = "";
}


/*
 * Escape before interpolating into innerHTML.
 *
 * Athlete and school names are SCRAPED FREE TEXT and genuinely contain & and
 * <. Without this a school called "Smith & Jones" renders broken, and anything
 * that looks like a tag disappears.
 */
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}


/*
 * Rating to one decimal.
 *
 * The engine stores speed_rating as `real` (float32), so the raw JSON value is
 * 138.66000366210938. One decimal is all the precision the number actually
 * carries -- a single race is worth about +-4 points of noise.
 */
function fmtRating(value) {
  return (value === null || value === undefined) ? "" : Number(value).toFixed(1);
}

/* HS-equivalent rating view (scale-view.js owns the toggle + stored mode).
 * The API stamps hs_<key> beside each rating-ish column; in HS mode a row
 * that has the alternate value shows it, everything else keeps its own
 * number. The ORDER is the server's, and the server is told which scale is
 * showing (buildQuery sends `scale`), so a pool=all board is paginated on
 * the numbers the reader sees. A scale flip on such a board refetches. */
function hsMode() {
  return Boolean(window.rcScale && window.rcScale.mode === "hs");
}

function rval(r, key) {
  if (hsMode() && r["hs_" + key] !== null && r["hs_" + key] !== undefined) {
    return r["hs_" + key];
  }
  return r[key];
}


/*
 * The Teams tab's course mode: a course in the combo turns the board into
 * single races at that venue. The page class drives which filter fields
 * show (see the .teams-course rules in style.css), and the sport pin keeps
 * a TF selection from 400ing -- course racing is cross country.
 *
 * Re-synced from buildQuery, which runs on every load -- and every combo
 * close with changes triggers a load, so the fields react as soon as the
 * course panel is dismissed.
 */
function syncTeamsCourseMode() {
  const on = state.board === "teams"
          && Boolean(combos.course && combos.course.values().length);
  $("rankings").classList.toggle("teams-course", on);
  if (on && $("sport").value !== "XC") $("sport").value = "XC";
  return on;
}


/*
 * Build the query string from the current controls.
 *
 * ONLY SENDS FILTERS THE USER SET. An empty text input would otherwise become
 * a literal filter on '' and return nothing, which looks exactly like "no
 * results" rather than "you sent a bad filter".
 */
function buildQuery() {
  syncTeamsCourseMode();
  const q = new URLSearchParams({
    board:  state.board,
    pool:   $("pool").value,
    sport:  $("sport").value,
    /* ★ SCOPE IS ALWAYS SENT, INCLUDING THE DEFAULT. The API defaults to
       "usa" too, but a URL that omits it is a URL whose meaning changes if
       that default ever moves -- and these URLs get shared. */
    scope:  $("scope").value,
    /* ★ THE SCALE THE READER IS ON, so the server orders by what it shows.
       Without it a pool=all board in HS-equivalent view reads unsorted
       wherever two pools' factors differ, which is every board that crosses
       gender. */
    scale:  hsMode() ? "hs" : "pool",
    limit:  PAGE_SIZE,
    offset: state.offset
  });

  /* ! SENT ONLY WHEN NON-EMPTY, so an untouched box adds no clause and the
     URL stays short enough to share. _multiValue on the server splits on
     commas, so "DI, DII" is two values and the spaces do not survive. */
  for (const k of UNIT_KEYS) {
    const host = document.querySelector(`.combo[data-field="${k}"]`);
    /* ! ONLY FROM THE VISIBLE GROUP. A college division left selected while
       the pool says hs would filter a high school board by an NCAA division
       and return nothing, with no clue why. */
    if (!host || host.closest(".unit-row").classList.contains("hidden")) {
      continue;
    }
    const vals = combos[k] ? combos[k].values() : [];
    if (vals.length) q.set(k, vals.join(","));   // q, not `query`: every unit filter threw "query is not defined" since 2026-09-02
  }

  /* The multi-value filters. Several chips become ONE comma-separated
     parameter -- the API splits it and binds the list as a Postgres array, so
     "CA,TX" is one bind, not two clauses. An empty combo sends NOTHING, which
     is what keeps the index usable: an always-true predicate stops Postgres
     choosing one. */
  for (const field of ["state", "grade", "year", "school"]) {
    const vals = combos[field] ? combos[field].values() : [];
    if (vals.length) q.set(field, vals.join(","));
  }

  /* Sort. `dir` is only sent when the user overrode it, so the API can apply
     each column's natural direction -- highest rating, latest date, FASTEST
     time. Sending "desc" blindly would make the time sort list slowest first. */
  if (state.sort) q.set("sort", state.sort);
  /* dir only when the user flipped a header. Left out, the API applies the
     column's natural direction -- highest rating, latest date, FASTEST time.
     Sending "desc" blindly would list the slowest times first. */
  if (state.dir) q.set("dir", state.dir);

  // Board-specific filters. Sending date_from to the ability board is a 400 by
  // design -- the API refuses rather than silently ignoring it -- so the two
  // branches must stay aligned with the perf-only/ability-only CSS.
  /* ! THE TEAMS BOARD TAKES A DIFFERENT SET, and sends none of the
     single-race filters: a date range or a distance belongs to one race,
     and min_races is a fact about one athlete. */
  /* ★ THE COURSE BOARD TAKES ALMOST NOTHING THE OTHERS DO. No pool, no
     sport, no scope, no season, no school -- a course has none of those. It
     keeps `state`, because a course is somewhere, and adds its own three. */
  if (state.board === "courses") {
    const q2 = new URLSearchParams({ limit: PAGE_SIZE, offset: state.offset });
    const st = combos.state ? combos.state.values() : [];
    if (st.length) q2.set("state", st.join(","));
    const dist = $("course_distance").value;
    if (dist) q2.set("distance", dist);
    const name = $("course_name").value.trim();
    if (name) q2.set("name", name);
    const minr = $("course_min_results").value;
    if (minr && Number(minr) > 0) q2.set("min_results", minr);
    if (state.sort) q2.set("sort", state.sort);
    if (state.dir) q2.set("dir", state.dir);
    return q2;
  }

  if (state.board === "teams") {
    /* ★ A COURSE SWITCHES WHAT THE BOARD IS -- single races at one venue --
       and the season machinery's filters would 400 by design, so they are
       not sent: no year, no state, no min_athletes, no sort (the server
       serves the one honest order). Distance rides along like on
       Performances: empty means every distance there. */
    const cv = combos.course ? combos.course.values() : [];
    if (cv.length) {
      q.set("course", cv.join(","));
      q.delete("year");
      q.delete("state");
      q.delete("sort");
      q.delete("dir");
      if ($("distance").value) q.set("distance", $("distance").value);
      return q;
    }
    /* ★ THE SORT IS THE SERVER'S CHOICE UNTIL SOMEBODY CLICKS A HEADER, and
       until then this sends none. The right default depends on whether the
       filtered field is small enough to be raced as one meet -- which takes
       a row count to know -- so the rule lives in teams.serveBoard, on the
       side that can answer it. load() adopts what comes back, so the header
       arrow lands on the column the board is actually sorted by.

       ! AND A URL WITHOUT sort= IS THE BETTER URL TO SHARE: it means "the
         right order for these filters" rather than freezing today's rule
         into a link somebody opens in a year. */
    if (!state.sortTouched) { q.delete("sort"); q.delete("dir"); }
    q.set("min_athletes", $("min_athletes").value || 5);
    return q;
  }

  if (state.board === "pr") {
    /* ★ THE SELECT'S VALUE ENCODES THE KIND (rankings.html): "5000" is a
       flat race, "110|hurdles" a timed non-flat race at that distance,
       "field:shot_put" a field event that ranks the mark and takes no
       distance. The API wants distance= and event= apart. */
    const sel = parseEventValue($("distance").value);
    if (sel.distance) q.set("distance", sel.distance);
    if (sel.event)    q.set("event", sel.event);
    if ($("date_from").value) q.set("date_from", $("date_from").value);
    if ($("date_to").value)   q.set("date_to",   $("date_to").value);
  } else if (state.board === "ability") {

    /* ★ SENT ONLY WHEN TYPED. Absent, the API applies its own floor of
       three and exempts open seasons from it (none from 2026 XC on); a
       number the reader typed applies to every row. Sending the default
       here would turn the exemption off for everyone. */
    const mrBox = $("min_races");
    if (mrBox.dataset.touched && mrBox.value) q.set("min_races", mrBox.value);
  } else {
    /* Performances: distance is OPTIONAL scope here (the board is already
       distance-normalised), so an empty "Any" sends nothing. */
    if ($("distance").value) q.set("distance", $("distance").value);
    if ($("date_from").value) q.set("date_from", $("date_from").value);
    if ($("date_to").value)   q.set("date_to",   $("date_to").value);
  }

  /* The course specifier rides only the two boards that accept it -- the
     API refuses it elsewhere by design, same posture as date_from. */
  if (state.board === "pr" || state.board === "performance") {
    const cv = combos.course ? combos.course.values() : [];
    if (cv.length) q.set("course", cv.join(","));
  }

  return q;
}


/* ------------------------------------------------------------------ *
 *  FILTER OPTIONS
 * ------------------------------------------------------------------ */

/*
 * ★ USPS CODES ONLY, NO ALIAS TABLE, AND THAT IS A MEASURED DECISION.
 *
 *   athlete_season.state holds four encodings of the same thing: USPS upper
 *   (14,999,157 rows), FIPS numerics like '06' (22,033), full names like
 *   'Texas' (18,938), and mixed case like 'Ca' (15,582). California really is
 *   split across CA / 06 / Ca.
 *
 *   But USPS upper is 99.64% of rows, and 06 is 480 against CA's 1,673,484 --
 *   0.03%. Mapping every state to its aliases would mean a hand-written table
 *   of 50+ entries that has to stay right forever, to recover an under-count
 *   far smaller than the +-3 points of noise on a single rating. The API
 *   upper-cases, which catches Ca/Il/Mi/Ne/Wi for free. The rest is left.
 *
 * ⚠ AND A PATTERN TEST WOULD BE WRONG. 77 distinct two-letter uppercase
 *   values exist for 50 states -- the extras are Canadian provinces (AB, BC,
 *   ON, QC), Australian (NSW, QLD, ACT) and similar. "Two capitals means
 *   American" lets Ontario through. Hence an explicit list.
 */
const US_STATES = [
  ["AL","Alabama"],["AK","Alaska"],["AZ","Arizona"],["AR","Arkansas"],
  ["CA","California"],["CO","Colorado"],["CT","Connecticut"],["DE","Delaware"],
  /* Abbreviated so it fits a grid column. Every other state name is short
     enough; this one alone would wrap and make its row twice as tall. */
  ["DC","Washington D.C."],["FL","Florida"],["GA","Georgia"],
  ["HI","Hawaii"],["ID","Idaho"],["IL","Illinois"],["IN","Indiana"],
  ["IA","Iowa"],["KS","Kansas"],["KY","Kentucky"],["LA","Louisiana"],
  ["ME","Maine"],["MD","Maryland"],["MA","Massachusetts"],["MI","Michigan"],
  ["MN","Minnesota"],["MS","Mississippi"],["MO","Missouri"],["MT","Montana"],
  ["NE","Nebraska"],["NV","Nevada"],["NH","New Hampshire"],["NJ","New Jersey"],
  ["NM","New Mexico"],["NY","New York"],["NC","North Carolina"],
  ["ND","North Dakota"],["OH","Ohio"],["OK","Oklahoma"],["OR","Oregon"],
  ["PA","Pennsylvania"],["RI","Rhode Island"],["SC","South Carolina"],
  ["SD","South Dakota"],["TN","Tennessee"],["TX","Texas"],["UT","Utah"],
  ["VT","Vermont"],["VA","Virginia"],["WA","Washington"],["WV","West Virginia"],
  ["WI","Wisconsin"],["WY","Wyoming"],
  /* Short enough for a grid column. A label that wraps makes its whole grid
     ROW taller, not just its own cell, so one long name adds a line across
     four columns -- which is what pushed the panel past its height and put a
     scrollbar back. */
  ["PR","Puerto Rico"],["GU","Guam"],["VI","Virgin Islands"],
  ["AE","Armed Forces EU"],["AP","Armed Forces PAC"]
];

/* Numeric school grades plus the collegiate class words tfrrs stores. Both
   appear in the grade column, so both are offered.
 *
 * ★ THE GREY COLUMN IS THE LEVEL, NOT THE CODE. For a state, "CA" next to
 *   "California" is useful -- it is what the data stores and what you might
 *   type. For a grade, repeating "12" next to "12th" says nothing. The level
 *   answers the question someone actually has: which board does this grade
 *   belong to, and will it return anything against the pool I picked.
 *
 * ⚠ THE MAPPING IS normalize_distance.GRADE_TO_LEVEL, not a fresh guess:
 *   1-5 elem, 6-8 ms, 9-12 hs, and the class words college. Grade 5 is listed
 *   even though no pool in the dropdown is elem-based -- it exists in the
 *   data, and silently omitting a value that is really there is worse than
 *   showing one that pairs badly with some pools.
 *
 * Ascending, because a grade list that starts at 12 reads backwards. */
const GRADES = [
  ["5","5th","elem"],
  ["6","6th","ms"],["7","7th","ms"],["8","8th","ms"],
  ["9","9th","hs"],["10","10th","hs"],["11","11th","hs"],["12","12th","hs"],
  ["Fr","Freshman","college"],["So","Sophomore","college"],
  ["Jr","Junior","college"],["Sr","Senior","college"]
];

/* Newest first: the reason to open a year filter is almost always this season
   or last, and nobody scrolls to 1990 by choice. */
const YEARS = (() => {
  const now = new Date().getFullYear() + 1;   // a TF season runs ahead
  const out = [];
  for (let y = now; y >= 1990; y--) out.push([String(y), String(y)]);
  return out;
})();

const FIELD_OPTIONS = {
  state:  US_STATES,
  grade:  GRADES,
  year:   YEARS,
  school: []          // searched, not listed -- see SEARCHED below
};

/*
 * ★ THE PANEL SHAPE FOLLOWS WHETHER THE OPTION SET IS BOUNDED.
 *
 *   Three of these four have a fixed, knowable list, so the panel shows ALL of
 *   it at once in a grid -- no scrolling. That matters more than it sounds:
 *   once you have used the state filter twice you know Texas is bottom-right
 *   and you go straight there. A scroll bar destroys that, because where a row
 *   sits depends on where you last left the scroll position.
 *
 *   School cannot work that way -- 145,247 of them -- so it is a search box
 *   against the site's existing index instead.
 *
 *   cols is the grid width; width is the panel's. Both are per field because
 *   56 states and 12 grades do not want the same box.
 *
 * ★ EVERY LIST FILLS DOWNWARD. Grid's default is row-major -- Alabama,
 *   Alaska, Arizona, Arkansas ACROSS the top, then Delaware under Alabama.
 *   That means reading an ordered list requires zig-zagging, and the eye has
 *   to track four columns at once to stay in sequence. Column-major puts
 *   A-M down the first column and N-Z down the last, which is how a printed
 *   index or a phone book reads, and it is the only layout where "scan down
 *   until you find it" works.
 *
 *   It is not free: the row count depends on how many options survive the
 *   filter, so it has to be recomputed on every render rather than set once.
 */
const PANEL = {
  /* 56 states / 4 columns = 14 rows, which fits without a scrollbar. Three
     columns needed 19 rows and did not. */
  state:  { cols: 4, width: 560, flow: "column" },
  year:   { cols: 4, width: 400, flow: "column" },
  grade:  { cols: 2, width: 300, flow: "column" },
  /* Searched, so the option count is unbounded and unknown until it returns.
     Column-major over a variable-length result list would move every entry
     each time a letter is typed. One column, top to bottom, is already the
     reading order. */
  /* ★ SEARCHED, LIKE School, BUT AGAINST /api/units. Units have no
     search_index rows -- that table holds things with a page, and a league
     has none -- so the combo needs a per-field endpoint rather than the
     hardcoded /search/api. Issue #55. */
  ...Object.fromEntries(
    ["division", "region", "conference",
     "state_div", "section", "section_div", "area", "league"].map((k) => [k, {

       cols: 1, width: 320, searched: true, kind: k,
       endpoint: "/api/units", hint: "Search\u2026",
     }])),
  /* ★ LISTED WHEN A STATE IS PICKED (owner, 2026-09-06: "a dropdown so
     you can press for sure"). Opening the panel lists that state's schools
     from /api/schools, biggest programme first, and typing narrows the
     list; with no state it is search-by-typing as before. */
  school: { cols: 1, width: 340, searched: true, kind: "school",
            listed: "/api/schools", hint: "Search schools\u2026" },
  /* The course specifier (Performances + Best times). Same searched shape
     as school, against the site index's course kind. */
  course: { cols: 1, width: 340, searched: true, kind: "course",
            hint: "Search courses\u2026" }
};


/* ------------------------------------------------------------------ *
 *  COMBOBOX
 * ------------------------------------------------------------------ */

/*
 * One implementation, four fields.
 *
 * A fixed-size trigger button plus a big scrollable checkbox panel. The button
 * shows "Any", one name, or "N selected" -- never the list, because listing
 * the selections is what made the previous version grow and shove the whole
 * toolbar around as values came and went.
 *
 * ★ THE VALUE AND THE LABEL ARE DIFFERENT THINGS. The row reads "California"
 *   and the query carries "CA". The filter box matches either, which is the
 *   point of a menu you can also type into.
 *
 * ★ FREE TEXT WHERE THERE IS NO LIST. School has ~300k values, so the typed
 *   string becomes the option -- "Add "Niwot"" -- and already-chosen values
 *   stay listed so they can be unticked.
 */
const combos = {};

function makeCombo(host) {
  const field = host.dataset.field;
  const options = FIELD_OPTIONS[field] || [];
  const cfg = PANEL[field] || { cols: 1, width: 300 };
  const searched = !!cfg.searched;
  const chosen = new Set();
  let dirty = false;
  let searchTimer = null;
  let found = [];              // last search results, for searched fields
  let listRows = null;         // the state's schools when listed (cfg.listed)
  let listNote = "";           // why there is no list, when there is none

  host.innerHTML =
    `<button type="button" class="combo-btn" aria-expanded="false">` +
      `<span class="combo-btn-label">Any</span>` +
      `<span class="combo-caret" aria-hidden="true">\u25be</span>` +
    `</button>` +
    `<div class="combo-panel hidden" style="width:${cfg.width}px">` +
      `<div class="combo-search">` +
        /* ! type="search", NOT "text". A bare text input with no name is
             the shape Chrome's autofill heuristics guess at, and a wrong
             guess offers Google Pay over the box. */
        `<input id="${field}-input" class="combo-input" type="search" ` +
        `autocomplete="off" placeholder="${searched ? (cfg.hint || "Search\u2026") : "Filter\u2026"}">` +
      `</div>` +
      `<div class="combo-opts${cfg.flow === "column" ? " flow-col" : ""}" ` +
      `style="--cols:${cfg.cols}"></div>` +
      `<div class="combo-foot">` +
        `<button type="button" class="combo-clear">Clear</button>` +
        `<button type="button" class="combo-done">Done</button>` +
      `</div>` +
    `</div>`;

  const btn = host.querySelector(".combo-btn");
  const btnLabel = host.querySelector(".combo-btn-label");
  const panel = host.querySelector(".combo-panel");
  const input = host.querySelector(".combo-input");
  const opts = host.querySelector(".combo-opts");

  /* ★ A SEARCHED FIELD'S LABEL IS NOT ITS VALUE. Schools are indexed as
     "Tufts (MA)" -- the site-wide display convention -- while
     ranking_results.school stores "Tufts". So the panel and the trigger show
     the label, and everything that filters sends the value.

     `seen` remembers labels for values already chosen, because the search
     box is cleared afterwards: without it, picking Tufts and then typing
     something else turns the trigger back into a bare "Tufts". */
  const seen = new Map();

  function labelFor(value) {
    const hit = options.find((o) => o[0] === value);
    if (hit) return hit[1];
    return seen.get(value) || value;
  }

  /* ★ THE TRIGGER NEVER CHANGES SIZE. Listing the selections on the button is
     what made an earlier version grow and shove the toolbar around. One name
     when there is one, a count when there are more. */
  function renderButton() {
    const n = chosen.size;
    btnLabel.textContent =
      n === 0 ? "Any" : n === 1 ? labelFor([...chosen][0]) : `${n} selected`;
    host.classList.toggle("has-values", n > 0);
  }

  function row(value, label, code) {
    const on = chosen.has(value);
    return `<label class="combo-opt${on ? " is-on" : ""}">` +
      `<input type="checkbox" data-v="${esc(value)}"${on ? " checked" : ""}>` +
      `<span>${esc(label)}</span>` +
      (code && code !== label ? `<span class="combo-code">${esc(code)}</span>` : "") +
      `</label>`;
  }

  function renderOptions() {
    if (searched) {
      /* Chosen first so they can always be unticked, then whatever the last
         search returned. Without the chosen rows, a school you added would
         vanish from the panel the moment you cleared the box. */
      const picked = [...chosen].map((v) => row(v, labelFor(v), null));
      /* The listed rows first, narrowed by whatever is typed, then the
         search hits the list did not already carry. */
      const q = input.value.trim().toLowerCase();
      const seenV = new Set(chosen);
      const hits = [];
      for (const f of (listRows || [])) {
        const l = f.label.toLowerCase();
        if (q && !(l.startsWith(q) || l.includes(" " + q))) continue;
        if (seenV.has(f.v)) continue;
        seenV.add(f.v); hits.push(row(f.v, f.label, null));
      }
      for (const f of found) {
        if (seenV.has(f.v)) continue;
        seenV.add(f.v); hits.push(row(f.v, f.label, null));
      }
      const why = hits.length ? "" : q.length >= 2 ? "No matches"
        : listRows ? (q ? "No matches" : "No schools listed")
        : (listNote || "Type at least two letters");
      opts.innerHTML = picked.concat(hits).join("") ||
        `<div class="combo-none">${esc(why)}</div>`;
      return;
    }

    const q = input.value.trim().toLowerCase();
    const pool = options.filter((o) => !q || o[0].toLowerCase().startsWith(q)
                                          || o[1].toLowerCase().includes(q));

    /* Column-major needs the row count, and it changes with the filter -- so
       it is set per render, not once at build time. */
    if (cfg.flow === "column") {
      opts.style.setProperty("--rows", Math.ceil(pool.length / cfg.cols) || 1);
    }
    /* NOT reordered to put chosen first. With every option visible at once,
       moving them would shuffle the grid under the cursor and destroy the
       spatial memory the no-scroll layout exists to give. Ticked rows are
       highlighted in place instead. */
    /* o[2] is the grey column where a list defines one (grade -> level);
       otherwise it falls back to the value itself (state -> "CA"). */
    opts.innerHTML = pool.map((o) => row(o[0], o[1], o[2] || o[0])).join("") ||
      `<div class="combo-none">No matches</div>`;
  }

  /* Debounced search against the site's own index, one kind per field so
     athletes and meets do not crowd out the schools (or the courses). */
  async function runSearch() {
    const kind = cfg.kind || "school";
    const q = input.value.trim();
    if (q.length < 2) { found = []; renderOptions(); return; }
    /* A listed field narrows its own list on every keystroke (renderOptions
       does that); the site search is only asked once two letters are in. */
    try {
      const res = await fetch((cfg.endpoint || "/search/api")
        + "?kind=" + kind + "&q=" + encodeURIComponent(q));
      const rows = await res.json();
      /* ! DEDUPED ON THE VALUE, NOT THE LABEL. A school split across two
         real state clusters is indexed twice -- "Tufts (MA)" and
         "Tufts (CT)" -- and both filter to the same bare "Tufts", so
         deduping on the label would offer one option that does nothing
         different from the other. */
      const byValue = new Map();
      for (const r of (rows || [])) {
        if (r.kind !== kind) continue;
        const v = r.value || r.label;
        if (!v || byValue.has(v)) continue;
        byValue.set(v, r.label || v);
        seen.set(v, r.label || v);
      }
      found = [...byValue.entries()].slice(0, 40).map(([v, label]) => ({ v, label }));
    } catch (err) {
      found = [];
    }
    renderOptions();
  }

  /* The state's schools, fetched on every open because the State filter
     may have changed since. {"typed": true} means the server cannot list
     (no state, or its index is not built yet) and typing is the way. */
  async function runList() {
    if (!cfg.listed) return;
    const st = combos.state ? combos.state.values() : [];
    if (!st.length) {
      listRows = null;
      listNote = "Pick a state to list its schools, or type a name";
      renderOptions(); return;
    }
    try {
      const res = await fetch(cfg.listed + "?state=" + encodeURIComponent(st.join(",")));
      const d = await res.json();
      if (d.typed) { listRows = null; listNote = "Type at least two letters"; }
      else {
        listRows = (d.rows || []).map((r) => {
          seen.set(r.value, r.label);
          return { v: r.value, label: r.label };
        });
        listNote = "";
      }
    } catch (err) {
      listRows = null; listNote = "Type at least two letters";
    }
    renderOptions();
  }

  function open() {
    /* One panel at a time -- two overlapping menus is a layout bug waiting to
       happen and there is never a reason to have both. */
    document.querySelectorAll(".combo-panel").forEach((p) => p.classList.add("hidden"));
    document.querySelectorAll(".combo-btn").forEach((b) => b.setAttribute("aria-expanded", "false"));
    panel.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    input.value = "";
    found = [];
    renderOptions();
    runList();

    /* ⚠ A 520px PANEL UNDER A RIGHT-HAND FILTER RUNS OFF THE PAGE. Measured
       on open rather than guessed from column order, because the filter row
       wraps at narrow widths and which control is rightmost changes. */
    panel.classList.remove("flip");
    if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
      panel.classList.add("flip");
    }
    input.focus();
  }

  /* ★ THE QUERY RUNS ON CLOSE, NOT PER TICK. Ticking eight states would
     otherwise fire eight requests, seven already stale before they land. */
  function close() {
    panel.classList.add("hidden");
    btn.setAttribute("aria-expanded", "false");
    if (dirty) {
      dirty = false;
      state.offset = 0;
      load();
    }
  }

  btn.addEventListener("click", () => {
    panel.classList.contains("hidden") ? open() : close();
  });

  input.addEventListener("input", () => {
    if (searched) {
      if (listRows) renderOptions();        // the list narrows at once
      clearTimeout(searchTimer);
      searchTimer = setTimeout(runSearch, 200);
    } else {
      renderOptions();
    }
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); btn.focus(); }
    if (e.key === "Enter") {
      e.preventDefault();
      const first = opts.querySelector("input[type=checkbox]");
      if (first) {
        toggle(first.dataset.v, true);
        input.value = "";
        found = [];
        renderOptions();
      }
    }
  });

  function toggle(value, on) {
    if (!value) return;
    if (on) chosen.add(value); else chosen.delete(value);
    dirty = true;
    renderButton();
    /* ! ANNOUNCED, SO ONE FILTER CAN DEPEND ON ANOTHER. State division only
       means something once a State is chosen -- every state has a D2 -- and
       the page cannot know a combo changed without being told. */
    host.dispatchEvent(new CustomEvent("combochange",
                                       { bubbles: true, detail: { field } }));
  }

  opts.addEventListener("change", (e) => {
    const cb = e.target.closest("input[type=checkbox]");
    if (!cb) return;
    toggle(cb.dataset.v, cb.checked);
    cb.closest(".combo-opt").classList.toggle("is-on", cb.checked);
  });

  host.querySelector(".combo-clear").addEventListener("click", () => {
    if (!chosen.size) return;
    chosen.clear();
    dirty = true;
    renderButton();
    renderOptions();
  });
  host.querySelector(".combo-done").addEventListener("click", close);

  /* Clicking away closes AND applies -- a menu dismissed by clicking elsewhere
     should not quietly discard the ticks. */
  document.addEventListener("mousedown", (e) => {
    if (!panel.classList.contains("hidden") && !host.contains(e.target)) close();
  });

  renderButton();

  const api = {
    values: () => [...chosen],
    set: (vals) => { chosen.clear(); (vals || []).forEach((v) => chosen.add(v));
                     renderButton(); }
  };
  combos[field] = api;
  return api;
}

/* ★ YEAR IS CHECKBOXES IN THE OPEN, NOT A MENU (owner, 2026-09-06:
   "Season should be a checkbox"). The five newest seasons sit in the
   filter bar as tick chips; "earlier" unfolds the rest in place. Same
   api as a combo (values / set, combochange on change) so buildQuery and
   the URL restore do not know the difference. Applies on tick: with the
   chips in the open there is no panel to close. */
const YEAR_INLINE = 5;

function makeYearChips(host) {
  const chosen = new Set();
  let expanded = false;
  host.classList.add("year-chips");

  function render() {
    const rows = expanded ? YEARS : YEARS.slice(0, YEAR_INLINE);
    host.innerHTML = rows.map(([v]) =>
      `<label class="ychip${chosen.has(v) ? " is-on" : ""}">` +
      `<input type="checkbox" data-v="${v}"${chosen.has(v) ? " checked" : ""}>` +
      `<span>${v}</span></label>`).join("") +
      `<button type="button" class="ymore">${expanded ? "fewer" : "earlier"}</button>`;
  }

  host.addEventListener("change", (e) => {
    const cb = e.target.closest("input[type=checkbox]");
    if (!cb) return;
    if (cb.checked) chosen.add(cb.dataset.v); else chosen.delete(cb.dataset.v);
    render();
    host.dispatchEvent(new CustomEvent("combochange",
                                       { bubbles: true, detail: { field: "year" } }));
    state.offset = 0;
    load();
  });
  host.addEventListener("click", (e) => {
    if (!e.target.closest(".ymore")) return;
    expanded = !expanded;
    render();
  });

  render();
  const api = {
    values: () => [...chosen],
    set: (vals) => {
      chosen.clear(); (vals || []).forEach((v) => chosen.add(v));
      const inline = new Set(YEARS.slice(0, YEAR_INLINE).map((y) => y[0]));
      if ([...chosen].some((v) => !inline.has(v))) expanded = true;
      render();
    }
  };
  combos.year = api;
  return api;
}

/* ! THE CHIPS ARE NOT USED (owner, 2026-09-06 evening: "it needs to go
     back to being a click thing that has a dropdown, as previous"). Year
     is the combo again, everywhere; its panel already ticks. makeYearChips
     stays for the record. */
document.querySelectorAll(".combo").forEach(makeCombo);


/* ------------------------------------------------------------------ *
 *  SORT OPTIONS
 * ------------------------------------------------------------------ */

/*
 * ⚠ MUST MATCH _SORTS_ABILITY / _SORTS_PERFORMANCE IN rankings.py. The API
 *   validates against its own whitelist and 400s on anything else, so an
 *   option here that is not there is a broken menu entry, not a silent no-op.
 *   The lists differ on purpose: a date sort is meaningless on a season
 *   average, and a races count does not exist on a single performance.
 */
/*
 * The columns, in order, per board.
 *
 * `key` is the API's sort key, or null for a column that cannot be sorted --
 * the row number is a property of the page, not of the athlete, so sorting by
 * it is meaningless.
 *
 * ⚠ EVERY key MUST EXIST IN _SORTS_ABILITY / _SORTS_PERFORMANCE in
 *   rankings.py. The API validates against its own whitelist and 400s on
 *   anything else, so a key here that is not there is a header that errors
 *   when clicked. The lists differ per board on purpose: a date sort is
 *   meaningless on a season average, and a race count does not exist on a
 *   single performance.
 */
const COLUMNS = {
  ability: [
    { key: null,     label: "#" },
    { key: "name",   label: "Athlete" },
    { key: "school", label: "School" },
    { key: "grade",  label: "Grade" },
    { key: null,     label: "Sport" },
    { key: "year",   label: "Year" },
    { key: "rating", label: "Rating" },
    { key: "best",   label: "Best" },
    { key: "races",  label: "Races" }
  ],
  performance: [
    { key: null,     label: "#" },
    { key: "name",   label: "Athlete" },
    { key: "school", label: "School" },
    { key: "grade",  label: "Grade" },
    { key: null,     label: "Sport" },
    { key: "date",   label: "Date" },
    { key: "rating", label: "Rating" }
  ],
  /* ★ THE ONLY BOARD THAT RANKS A GROUP. The other three rank a number
     each row already carries; this one ranks the finish order of a
     hypothetical meet -- every team's top seven, sorted by season rating,
     scored with the ordinary rules.
     ! # IS THE BOARD'S OWN RANK, NOT THE ROW NUMBER. Filter the national
       board to three states and it reads 1, 4, 11, which is the truth about
       where those teams stand; renumbering would invent a championship. */
  /* ★ A BOARD OF GROUND, NOT OF PEOPLE. Difficulty leads because it is what
     the board ranks; results and athletes follow it because a difficulty is
     only as good as the racing behind it, and putting them out of sight
     would be publishing three decimal places with nothing under them. */
  courses: [
    /* ! # IS THE PLACE ON THE BOARD AS FILTERED, and unlike on the teams
         board that is a true sentence. A course's difficulty is measured
         against the whole corpus either way, so filtering to Oregon and
         reading "3" means third hardest in Oregon -- where renumbering a
         filtered team board would invent a championship. */
    { key: null,         label: "#" },
    { key: "difficulty", label: "Difficulty" },
    { key: "name",       label: "Course" },
    { key: "state",      label: "State" },
    { key: "distance",   label: "Distance" },
    { key: "results",    label: "Results" },
    { key: "athletes",   label: "Athletes" },
    { key: "meets",      label: "Meets" }
  ],
  teams: [
    { key: "rank",     label: "#" },
    { key: "school",   label: "Team" },
    { key: "state",    label: "State" },
    { key: "year",     label: "Year" },
    { key: "points",   label: "Points" },
    { key: "rating",   label: "Top 5 avg" },
    { key: "fifth",    label: "5th runner" },
    { key: "athletes", label: "Runners" }
  ],
  /* The Teams tab with a course picked: single races at one venue, served
     pre-sorted by top-5 average. No sortable keys -- there is one honest
     order and the server applies it. NOT a board of its own in state.board;
     renderBoard picks this head off data.course_mode. */
  teamscourse: [
    { key: null, label: "#" },
    { key: null, label: "Team" },
    { key: null, label: "Top 5 avg" },
    { key: null, label: "Distance" },
    { key: null, label: "Meet" },
    { key: null, label: "Date" }
  ],
  /* Time first, because it is what this board ranks. Rating is still shown --
     the gap between a fast time and a modest rating IS the course, and seeing
     both is how somebody learns that. Pool is shown because "all pools" is an
     option here and a 5k time alone does not say who ran it. */
  pr: [
    { key: null,     label: "#" },
    { key: "name",   label: "Athlete" },
    { key: "school", label: "School" },
    { key: "grade",  label: "Grade" },
    { key: null,     label: "Pool" },
    { key: "date",   label: "Date" },
    /* One column for both: a time on a running board, a mark on a field
       board. The sort key stays "time"; the API maps it onto the mark. */
    { key: "time",   label: "Time / Mark" },
    { key: "rating", label: "Rating" }
  ]
};


/* Seconds -> m:ss.d, or h:mm:ss for anything past an hour. A leaderboard of
   "1183.4" is a leaderboard nobody can read. */
function fmtTime(sec) {
  /* ! THE SENTINEL PRINTS AS DNF, NOT AS 277:46.6. See DNF_SENTINEL. */
  if (isNoTime(sec)) return "DNF";
  const s = Number(sec);
  if (!isFinite(s)) return " - ";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s - h * 3600 - m * 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (h) return `${h}:${pad(m)}:${pad(Math.round(rest))}`;
  return `${m}:${rest < 10 ? "0" : ""}${rest.toFixed(1)}`;
}

const POOL_LABEL = {
  hs_m: "HS Boys", hs_f: "HS Girls",
  ms_m: "MS Boys", ms_f: "MS Girls",
  college_m: "College Men", college_f: "College Women",
  elem_m: "Elem Boys", elem_f: "Elem Girls"
};

/*
 * The header row, with the current sort marked.
 *
 * aria-sort, not just an arrow: a screen reader announces the sort state from
 * it, and the arrow alone would be invisible to one.
 */
function renderHead(board) {
  return "<thead><tr>" + COLUMNS[board].map((c) => {
    if (!c.key) return `<th>${c.label}</th>`;
    const active = state.sort === c.key;
    const dir = active ? (effectiveDir(board, c.key) === "asc" ? "asc" : "desc") : "";
    const aria = active ? ` aria-sort="${dir === "asc" ? "ascending" : "descending"}"` : "";
    const arrow = active ? (dir === "asc" ? " \u2191" : " \u2193") : "";
    return `<th class="sortable${active ? " is-sorted" : ""}"` +
           ` data-key="${c.key}"${aria}>${c.label}${arrow}</th>`;
  }).join("") + "</tr></thead>";
}

/*
 * Which way a column is actually sorted right now.
 *
 * When the user has not overridden it, the direction is the column's natural
 * one -- highest rating, latest date, but ASCENDING for names and schools,
 * because alphabetical means A first. Mirrors the per-column defaults in
 * rankings.py so the arrow never contradicts the data.
 */
/* One spelling for a grade (racecast/grade_label.py, mirrored): a school
   pool reads the number 5-12, a college pool the eligibility spelling
   FR-1..SR-4. The database keeps what the feed said. */
const _WORD_HS = {fr: "9", so: "10", jr: "11", sr: "12"};
const _WORD_COL = {fr: "FR-1", so: "SO-2", jr: "JR-3", sr: "SR-4"};
const _NUM_COL = {"13": "FR-1", "14": "SO-2", "15": "JR-3", "16": "SR-4"};
function poolNow() {
  const el = document.getElementById("pool");
  return el ? el.value : "";
}
function gradeLabel(grade, pool) {
  if (grade === null || grade === undefined) return "";
  const g = String(grade).trim();
  if (!g) return g;
  const level = String(pool || "").split("|")[0].split("_")[0].toLowerCase();
  const college = level === "college" || level === "pro";
  const m = /^(FR|SO|JR|SR)-?([1-6])$/i.exec(g);
  if (m) return (college || level === "") ? `${m[1].toUpperCase()}-${m[2]}` : (_WORD_HS[m[1].toLowerCase()] || g);
  const low = g.toLowerCase().replace(/\.$/, "");
  if (college) {
    if (_WORD_COL[low]) return _WORD_COL[low];
    if (/^\d+$/.test(g)) return _NUM_COL[g] || g;
    return g;
  }
  if (_WORD_HS[low]) return _WORD_HS[low];
  if (/^\d+$/.test(g)) return String(parseInt(g, 10));
  return g;
}

const NATURAL_ASC = new Set(["name", "school", "state", "grade", "time",
                             "first", "rank", "points"]);

function effectiveDir(board, key) {
  if (state.sort === key && state.dir) return state.dir;
  return NATURAL_ASC.has(key) ? "asc" : "desc";
}

/*
 * Click a header: sort by it, or flip it if it is already the sort.
 */
function onHeaderClick(key) {
  state.sortTouched = true;
  if (state.sort === key) {
    state.dir = effectiveDir(state.board, key) === "asc" ? "desc" : "asc";
  } else {
    state.sort = key;
    state.dir = "";          // fall back to that column's natural direction
  }
  state.offset = 0;
  load();
}


/* ------------------------------------------------------------------ *
 *  RENDERING
 * ------------------------------------------------------------------ */

/*
 * The ability board.
 *
 * `rating` is the season mean; `best` is the single best race that season.
 * Showing both is deliberate: it makes the difference between the two boards
 * visible in one row, and a big gap between them says "inconsistent season"
 * rather than "bad athlete".
 *
 * The row number is offset + i + 1 because the API pages server-side and does
 * not send a rank -- rank is a property of the page, not of the athlete.
 */
function renderAbility(rows) {
  const body = rows.map((r, i) => `
    <tr${String(r.person_id) === state.highlight ? ' class="is-found"' : ""}>
      <td class="rank${state.offset + i < 3 ? " top3" : ""}">${state.offset + i + 1}</td>
      <td><a href="/athlete/${r.person_id}">${esc(r.name)}</a></td>
      ${schoolCell(r.school, r.school_state || r.state)}
      <td>${esc(gradeLabel(r.grade, r.pool || poolNow()))}</td>
      <td>${esc(r.sport)}</td>
      <td>${r.year}</td>
      <td class="rating"><a href="/athlete/${r.person_id}">${fmtRating(rval(r, "rating"))}</a></td>
      <td>${fmtRating(rval(r, "best_rating"))}</td>
      <td>${r.n_races === null || r.n_races === undefined ? "" : r.n_races}</td>
    </tr>`).join("");

  return `<table class="rk">${renderHead("ability")}
    <tbody>${body}</tbody>
  </table>`;
}


/*
 * The performance board.
 *
 * XC and TF race pages are DIFFERENT ROUTES (/race/xc/... and /race/tf/...),
 * so the link has to branch on the sport. With sport=both a single board holds
 * rows of each kind, which is exactly why the sport column is shown.
 *
 * ★ THE RATING CARRIES THE LINK, NOT THE DATE. The rating is what the row is
 *   about and where the eye already is; a date is a fact about the race rather
 *   than a way into it. The times board does the same with its time.
 */
function renderPerformance(rows) {
  const body = rows.map((r, i) => {
    const href = raceHref(r);

    return `
    <tr>
      <td class="rank${state.offset + i < 3 ? " top3" : ""}">${state.offset + i + 1}</td>
      <td><a href="/athlete/${r.person_id}">${esc(r.name)}</a></td>
      ${schoolCell(r.school, r.school_state || r.state)}
      <td>${esc(gradeLabel(r.grade, r.pool || poolNow()))}</td>
      <td>${esc(r.sport)}</td>
      ${maybeLink(href, esc(r.race_date))}
      ${maybeLink(href, fmtRating(rval(r, "rating")), "rating")}
    </tr>`;
  }).join("");

  return `<table class="rk">${renderHead("performance")}
    <tbody>${body}</tbody>
  </table>`;
}


/*
 * The best-times board.
 *
 * One row per athlete -- their fastest at this distance -- so the rank column
 * is a rank of PEOPLE, not of races. See getPrRankings for why that differs
 * from the performance board's per-athlete cap.
 */
function renderPr(rows) {
  const body = rows.map((r, i) => {
    const href = raceHref(r);

    /* ! A DNF STILL LINKS TO THE RACE. The row is kept deliberately --
       somebody looking for an athlete in a race they did not finish should
       find them, with the reason -- and the race page is where the rest of
       that story is.

       The rating belongs to a race too, so it points at the same place the
       time does. maybeLink because a TF row without an event_id has no
       route to offer.

       ⚠ COMMENTS STAY OUT HERE, ABOVE THE LITERAL. A {slash-star} comment
       INSIDE the template string is not a comment, it is output -- and raw
       text is illegal between <tr> cells, so browsers hoist it out of the
       table and it rendered as junk text above the whole board. */
    return `
    <tr>
      <td class="rank${state.offset + i < 3 ? " top3" : ""}">${state.offset + i + 1}</td>
      <td><a href="/athlete/${r.person_id}">${esc(r.name)}</a></td>
      ${schoolCell(r.school, r.school_state || r.state)}
      <td>${esc(gradeLabel(r.grade, r.pool || poolNow()))}</td>
      <td>${esc(POOL_LABEL[r.pool] || r.pool)}</td>
      <td>${esc(r.race_date)}</td>
      ${r.mark !== null && r.mark !== undefined
          ? maybeLink(href, fmtMark(r.mark), "time mark")
          : maybeLink(href, fmtTime(r.time_seconds),
                      "time" + (isNoTime(r.time_seconds) ? " dnf" : ""))}
      ${maybeLink(href, fmtRating(rval(r, "rating")), "rating")}
    </tr>`;
  }).join("");

  return `<table class="rk">${renderHead("pr")}
    <tbody>${body}</tbody>
  </table>`;
}



/* 1 -> "1st". Used in the tooltip that keeps a team's own season visible
   when the board has been re-raced across several of them. */
function ordinal(n) {
  const t = n % 100;
  const suffix = (t >= 11 && t <= 13) ? "th"
               : ["th", "st", "nd", "rd"][n % 10] || "th";
  return `${n}${suffix}`;
}


/*
 * What the rank column means, which is not the same sentence every time.
 *
 * ★ WRITTEN FROM THE RESPONSE, NOT FROM THE FILTERS. Whether the field was
 *   raced depends on how many teams matched, which only the server knows --
 *   and a note that says "raced" over a board that fell back is worse than
 *   no note at all.
 */
function teamsNote(data) {
  const note = $("teams-note");
  if (!note) return;

  /* ★ COURSE MODE FIRST: none of the season sentences below is true of it.
     The board is single races at one venue, already in its one honest
     order. */
  if (data.course_mode) {
    const c = (data.filters && data.filters.course || []).join(", ");
    note.innerHTML =
      `<strong>Single races at ${esc(c)}:</strong> every race there where `
      + "a school put five rated finishers across the line, ranked by "
      + "top-5 average rating. The same squad appears once per race.";
    return;
  }

  const n = (data.field_size || 0).toLocaleString();
  const subset = Number.isInteger(data.shown_of_field)
              && data.shown_of_field !== data.field_size;

  if (data.reason === "unbuilt") {
    /* ⚠ NOT "NARROW YOUR FILTER". No filter fixes a table built without the
       ratings column, and sending somebody to fiddle with the year chips
       over a rebuild is how a five-minute fix becomes an afternoon. */
    note.innerHTML =
      "<strong>Showing each squad's place in its own season.</strong> "
      + "Racing seasons against each other needs the stored squad ratings, "
      + "which this table was built without - rebuild with "
      + "<code>racecast/build_team_season.py</code> to turn it on.";

  } else if (data.raced && subset) {
    /* ★ A SCHOOL SEARCH IS A LOOKUP, NOT A SMALLER MEET. The searched teams
       raced the whole field and are being picked out of it, so # is their
       place among all of them -- the number somebody searching a team
       wants. Said out loud because rows numbered 1, 2 and 4 otherwise look
       like the board lost some. */
    note.innerHTML =
      `<strong>Showing ${data.shown_of_field.toLocaleString()} of ${n} teams</strong>`
      + " that raced each other in one meet. The # is their place in that "
      + "full field, not among the rows shown - clear the filters to "
      + "see everyone. Hover a rank for that squad's own season.";

  } else if (data.raced) {
    note.innerHTML =
      `<strong>${n} teams raced against each other</strong> in one meet - `
      + "every squad's top seven entered, sorted by season rating and scored "
      + "the ordinary way. One first place, and the points are this field's. "
      + "Squads from different years are separate entries; hover a rank to "
      + "see where that squad finished in its own season.";

  } else if (data.span === "alltime") {
    /* The board the page opens on. Ranks come from a meet run at build time
       over every season at once, so they are a single ranking -- and a
       filtered view of one has gaps, which is the honest answer rather than
       a renumbering that would invent a championship. */
    note.innerHTML =
      `<strong>Every squad of every season in one field</strong> - `
      + `all ${n} of them, raced when the board was built and scored the `
      + "ordinary way. One first place. A squad's year is its own; filter "
      + "to a single Year to rank that season on its own instead.";

  } else {
    note.innerHTML =
      "<strong>One season's own meet</strong> - every squad that raced "
      + `that year, all ${n} of them, scored against each other. Clear the `
      + "Year filter to put every season in one field instead.";
  }
}


/*
 * The courses board.
 *
 * ⚠ THE SIGN IS SPELLED OUT ON EVERY ROW, not left to a + to carry. "+4.1%
 *   harder" and "-5.0% easier" cost four characters and remove the one
 *   misreading this board invites: that a big number is a fast course.
 *
 * ★ A PERCENT, THE SAME ONE THE COURSE PAGES PRINT. The server sends
 *   difficulty_pct -- percent slower than a typical course, against the
 *   corpus-mean zero difficulty_view keeps -- beside the raw multiplier,
 *   which only the title keeps. Printing the raw +0.041 here while the
 *   course page said +4.1% was the last decimal on the site (2026-09-06).
 */
function renderCourses(rows) {
  const body = rows.map((r) => {
    const raw = Number(r.difficulty);
    const pct = r.difficulty_pct == null ? null : Number(r.difficulty_pct);
    const d = pct == null ? raw : pct;
    const sense = d > 0.05 ? "harder" : d < -0.05 ? "easier" : "neutral";
    const shown = pct == null
      ? `${raw >= 0 ? "+" : ""}${raw.toFixed(3)}`
      : Math.abs(pct) < 0.05 ? "0.0%" : `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
    const words = r.difficulty_words
      ? `${esc(r.difficulty_words)} (raw ${raw >= 0 ? "+" : ""}${raw.toFixed(3)})`
      : `${sense} than an average course`;
    return `
    <tr>
      <td class="rank${r.rank <= 3 ? " top3" : ""}">${r.rank}</td>
      <td class="rating ${d > 0 ? "hard" : "easy"}"
          title="${words}">${shown}
        <span class="sense">${sense}</span></td>
      <td><a href="/course/${encodeURIComponent(r.course_name)}">${esc(r.course_name)}</a>${
        r.n_same_name > 1
          ? `<span class="sense" title="${r.n_same_name} different venues are `
            + `called this. The engine keeps them apart by location; the state `
            + `and meet count are left blank because they cannot be told apart `
            + `by name.">1 of ${r.n_same_name} by this name</span>`
          : ""}</td>
      <td><span class="state">${esc(r.state || " - ")}</span></td>
      <td>${r.distance_m ? r.distance_m.toLocaleString() + "m" : " - "}</td>
      <td>${(r.n_results || 0).toLocaleString()}</td>
      <td>${(r.n_athletes || 0).toLocaleString()}</td>
      <td>${(r.n_meets || 0).toLocaleString()}</td>
    </tr>`;
  }).join("");

  return `<table class="rk">${renderHead("courses")}
    <tbody>${body}</tbody>
  </table>`;
}


/*
 * The teams board.
 *
 * The rank is served, not computed from the page position -- see COLUMNS.
 * The school links to its own page, which is already the team's page.
 */
function renderTeams(rows, span) {
  /* ⚠ THE LABEL DEPENDS ON WHICH BOARD THE ROW CAME FROM. board_rank is the
     rank it carried BEFORE this race -- that is a season finish when one Year
     is selected and an all-time placing otherwise, and calling the second one
     a season finish would be a confident lie in a tooltip. */
  const where = (r) => span === "season"
    ? `in the ${r.year} season` : "all-time";
  const body = rows.map((r) => `
    <tr>
      <td class="rank${r.rank <= 3 ? " top3" : ""}"${r.board_rank
        ? ` title="${ordinal(r.board_rank)} ${where(r)}, ` +
          `on ${r.board_points} points"` : ""}>${r.rank}</td>
      <td><a href="/school/${encodeURIComponent(r.school)}">${esc(r.school)}</a></td>
      <td><span class="state">${esc(r.state)}</span></td>
      <td>${r.year}</td>
      <td class="rating">${r.points}</td>
      <td>${fmtRating(rval(r, "top5_mean"))}</td>
      <td>${fmtRating(rval(r, "fifth_rating"))}</td>
      <td>${r.n_athletes}</td>
    </tr>`).join("");

  return `<table class="rk">${renderHead("teams")}
    <tbody>${body}</tbody>
  </table>`;
}


/*
 * The teams board in course mode: single races, served best-first, so the
 * # is the row's place in this list -- the server ranked it and OFFSET
 * paging keeps the numbering continuous across pages.
 */
function renderTeamsCourse(rows) {
  const body = rows.map((r, i) => `
    <tr>
      <td class="rank${state.offset + i < 3 ? " top3" : ""}">${state.offset + i + 1}</td>
      <td><a href="/school/${encodeURIComponent(r.school)}">${esc(r.school)}</a></td>
      <td class="rating">${fmtRating(rval(r, "top5_mean"))}</td>
      <td>${r.distance}m</td>
      <td><a href="/race/xc/${r.meet_id}/${r.div_id}">${esc(r.meet_name || ("Meet " + r.meet_id))}</a></td>
      <td>${r.date}</td>
    </tr>`).join("");

  return `<table class="rk">${renderHead("teamscourse")}
    <tbody>${body}</tbody>
  </table>`;
}


/* ------------------------------------------------------------------ *
 *  LOADING
 * ------------------------------------------------------------------ */

/*
 * Mirror the current filters into the address bar.
 *
 * The page READS the query string on load, so without this the URL goes stale
 * the moment anyone touches a filter -- and a copied address bar would then
 * describe the board the user arrived at rather than the one they are looking
 * at. Reading and not writing is the worse half of the feature.
 *
 * replaceState, NOT pushState: Apply is cheap and often repeated, so pushState
 * would make Back walk the user through their own filter edits one at a time
 * instead of returning them to the page they came from -- usually the home
 * page they deep-linked from.
 *
 * limit and offset are stripped. limit is a constant. offset is dropped
 * because initFromUrl deliberately does NOT read it, and publishing a
 * parameter the page ignores on load would make a copied URL lie about which
 * page it lands on.
 */
function syncUrl(query) {
  const shown = new URLSearchParams(query);
  shown.delete("limit");
  shown.delete("offset");
  try {
    history.replaceState(null, "", location.pathname + "?" + shown.toString());
  } catch (err) {
    /* ⚠ NEVER LET THIS BREAK THE PAGE. replaceState throws on a non-http
       origin, in some sandboxed frames, and if a browser rate-limits it. The
       address bar is a convenience; the table is the product. Before this
       guard the throw escaped load(), state.busy stayed true forever, and
       Apply was permanently disabled with nothing on screen to explain it. */
  }
}

/*
 * Fetch and render the current page.
 *
 * The `busy` guard stops a double-click firing two overlapping requests whose
 * responses could arrive out of order and render the wrong page.
 */
/* The last successfully rendered board, so a rating-scale flip can redraw
   without refetching -- same rows, different displayed numbers. */
let _lastBoard = null;

function renderBoard(rows, data) {
  return state.board === "ability"    ? renderAbility(rows)
       : state.board === "pr"         ? renderPr(rows)
       : state.board === "courses"    ? renderCourses(rows)
       : state.board === "teams"      ? (data.course_mode
                                          ? renderTeamsCourse(rows)
                                          : renderTeams(rows, data.span))
       :                                renderPerformance(rows);
}

document.addEventListener("rc-scale-change", () => {
  if (!_lastBoard) return;
  /* A board where the scale moves some rows is ORDERED on the scale, so a
     flip changes the order and must refetch. A board where nothing moves is
     the same rows either way, and a redraw is enough. */
  if (_lastBoard.data && _lastBoard.data.hs_movable) {
    state.offset = 0;
    load();
    return;
  }
  $("results").innerHTML = renderBoard(_lastBoard.rows, _lastBoard.data);
});

async function load() {
  if (state.busy) return;
  state.busy = true;
  $("apply").disabled = true;
  /* Loud, not a grey word. A rankings query can take a second or two, and a
     faint "Loading..." where a table used to be reads as an empty result --
     people re-click Apply, which the busy guard then swallows, and the page
     looks broken. Spinner plus a full-height block. */
  $("results").innerHTML =
    '<div class="status loading"><span class="spinner"></span>' +
    '<span>Loading rankings\u2026</span></div>';

  // Built ONCE and used for both the request and the address bar, so the two
  // cannot disagree about what is being shown.
  /* ! INSIDE THE GUARD. buildQuery() ran before the try, so a throw in
     it left state.busy true and Apply grey for the rest of the page's
     life -- the owner saw exactly that after changing the area filter
     (2026-09-05). Everything after the disable runs under the finally. */
  let query;

  try {
    query = buildQuery();
    /* Inside the try, so anything it throws still reaches the finally that
       clears `busy` -- see the guard in syncUrl for why that matters. */
    syncUrl(query);

    /* The teams board is served by its own route: it reads a
       precomputed table with a different shape, and folding it into
       /api/rankings would mean one route answering two questions. */
    const endpoint = state.board === "teams" ? "/api/teams"
                   : state.board === "courses" ? "/api/courses"
                   : "/api/rankings";
    const res = await fetch(endpoint + "?" + query.toString());
    const data = await res.json();

    // A 400 carries {"error": "..."}. SHOW IT. An empty table on a bad filter
    // is indistinguishable from an empty table on a valid one.
    if (!res.ok) {
      $("results").innerHTML =
        `<div class="status error">${esc(data.error || res.statusText)}</div>`;
      $("pager").classList.add("hidden");
      return;
    }

    const rows = data.rows || [];

    /* TWO NOTICES, TWO CONDITIONS, and neither is always on.

       bias-notice  retired. The API still sets national_bias when no state
                    filter is applied, but the offset it warned about was
                    measured at under a point (issue 191), so the banner
                    said nothing worth a banner.

       notice       the international caveat, which only applies when the
                    scope is open. On a USA board there is nothing on the far
                    side of the linkage graph to warn about, and a warning
                    that is permanently up is one nobody reads. */
    // bias-notice: retired 2026-09-06 (issue 191 measured the cross-state
    // offset at under a point); the element stays so nothing here throws.
    $("notice").classList.toggle("show", $("scope").value === "all");

    /* ★ ADOPT THE SORT THAT WAS SERVED, before anything renders. The teams
       board lets the server choose (see buildQuery), and renderHead draws
       the arrow from `state` -- so without this the arrow marks whatever was
       last on screen while the rows are ordered by something else. */
    if (state.board === "teams" && data.filters) {
      if (!data.course_mode) {
        state.sort = data.filters.sort;
        state.dir = data.filters.sort_explicit
          ? (data.filters.dir || "").toLowerCase() : "";
      }
      /* Course mode retitles the board: the subtitle is the one place that
         says what changed, and syncBoard's static teams line would be a
         wrong sentence over these rows. Restored the same way when the
         course is cleared. */
      $("subtitle").textContent = data.course_mode
        ? "Team performances at " + (data.filters.course || []).join(", ")
          + " · top-5 average rating, one race."
        : "Teams - every squad's top seven raced against each other, "
          + "scored the ordinary way.";
      teamsNote(data);
    }

    /* The scale toggle earns its place only when this board's rows actually
       move under the HS view (the API says so) -- a switch that does
       nothing reads as broken. */
    const scaleBox = document.getElementById("scale-toggle");
    if (scaleBox) scaleBox.style.display = data.hs_movable ? "" : "none";

    if (rows.length === 0) {
      _lastBoard = null;
      $("results").innerHTML =
        '<div class="status">No results for these filters.</div>';
      // Keep the pager visible past page 1 so there is a way back.
      $("pager").classList.toggle("hidden", state.offset === 0);
    } else {
      _lastBoard = { rows, data };
      $("results").innerHTML = renderBoard(rows, data);
      $("pager").classList.remove("hidden");
    }

    /* Both back-controls share one condition: there is nothing behind
       page one. Disabling them together stops "First" looking live on a
       board that is already at its top. */
    $("first").disabled = state.offset === 0;
    $("prev").disabled = state.offset === 0;
    // The API sends no total count, so "is there a next page" is INFERRED: a
    // full page probably has more behind it, a short page is the end. The only
    // wrong case is a result that is an exact multiple of PAGE_SIZE, which
    // shows one empty page. Cheaper than a COUNT(*) over 61M rows per request.
    /* A raced board was scored in memory, so its size is known exactly and
       the guess below is not needed. Everything else still infers. */
    $("next").disabled = Number.isInteger(data.total)
      ? state.offset + rows.length >= data.total
      : rows.length < PAGE_SIZE;
    /* Last is pointless when Next is: a short page is the end of the board.
       Only the three rating boards can be counted (see api_rankings). */
    $("last").disabled = $("next").disabled
      || !["ability", "performance", "pr"].includes(state.board);
    $("pageLabel").textContent =
      `${state.offset + 1}\u2013${state.offset + rows.length}`;

  } catch (err) {
    $("results").innerHTML =
      `<div class="status error">Request failed: ${esc(err.message)}</div>`;
    $("pager").classList.add("hidden");
  } finally {
    state.busy = false;
    $("apply").disabled = false;
  }
}


/*
 * Switch board.
 *
 * Sets body[data-board], which is what the CSS reads to show or hide the
 * date and min-races fields. The JS never touches individual field visibility
 * -- one attribute, and the stylesheet does the rest.
 */
function syncBoard(board) {
  const previous = state.board;
  state.board = board;
  state.offset = 0;
  $("rankings").dataset.board = board;

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.board === board));
  });

  /* A sort the new board does not offer would 400. Fall back rather than
     sending a key the API will reject.

     ⚠ AND ARRIVING AT TEAMS ALWAYS RESETS, even though the key survives the
       test. "rating" exists on both boards and means two different things --
       an athlete's season average there, a squad's top-five average here --
       so a sort carried over from the athlete board opens the team board
       sorted by something other than the finish it exists to show. */
  if (!COLUMNS[board].some((c) => c.key === state.sort)
      || (board === "teams" && previous !== "teams")
      || (board === "courses" && previous !== "courses")
      /* ! pr TOO: "rating" is a legal sort there, so a sort carried over
           from the ability board survived the test above and the times
           board opened sorted by rating -- the one thing it exists NOT to
           lead with. Arriving always resets it to the clock. */
      || (board === "pr" && previous !== "pr")) {
    /* ! THE FALLBACK IS PER BOARD. Dropping onto "rating" here would open the
         times board sorted by something other than time, which is the one
         thing it exists to sort by. */
    state.sort = board === "pr" ? "time"
               : board === "teams" ? "rating"
               : board === "courses" ? "difficulty"
               : "rating";
    state.dir = "";
  }
  // Arriving at a board hands the sort back to it; buildQuery then picks
  // the teams default from the year filter on every load.
  if (board !== previous) state.sortTouched = false;

  /* ⚠ NO sport='both' ON A BOARD OF MEETS. One hypothetical race cannot hold
     cross country and track teams at once, so the API refuses it -- and a
     select left on "Both" would 400 on arrival with no obvious cause, the
     same trap the hidden pool=all option sprang. */
  const sportSel = $("sport");
  sportSel.querySelector('option[value="both"]').hidden = board === "teams";
  if (board === "teams" && sportSel.value === "both") sportSel.value = "XC";

  /* ⚠ "Any distance" IS PERFORMANCE-ONLY. The times board RANKS the clock
     at one distance; an empty distance there is a 400. Same hidden-option
     pattern as sport=both above. */
  const distSel = $("distance");
  if (distSel) {
    const anyOpt = distSel.querySelector(".perf-any-opt");
    if (anyOpt) anyOpt.hidden = board === "pr";
    /* ★ HURDLES, STEEPLE AND FIELD EVENTS EXIST ONLY ON THE TIMES/MARKS
       BOARD: they carry no rating, so no other board can rank them. Same
       hidden-option pattern as pool=all, and the same reset -- a hidden
       option that stays selected would send event= to a board that
       rejects it. */
    distSel.querySelectorAll(".pr-only-opt").forEach((opt) => {
      opt.hidden = board !== "pr";
    });
    distSel.querySelectorAll(".pr-only-grp").forEach((grp) => {
      grp.hidden = board !== "pr";
    });
    if (board !== "pr" && (distSel.value.includes("|")
                           || distSel.value.startsWith("field:"))) {
      distSel.value = "";
    }
    if (board === "pr" && !distSel.value) distSel.value = "5000";
  }


  /* ! 'all' ONLY EXISTS ON THE PR BOARD, so leaving it selected while
       switching away would send a pool the API rejects with a 400. */
  const poolSel = $("pool");
  poolSel.querySelectorAll(".pr-only-opt").forEach((opt) => {
    opt.hidden = board !== "pr";
  });
  /* ⚠ RESET BEFORE ANYTHING ELSE READS IT. Hiding an <option> does not
       deselect it -- a hidden option that is still current keeps its value and
       buildQuery sends "all" to a board whose API rejects it, which is a 400
       on arrival with no obvious cause. */
  if (board !== "pr" && poolSel.value === "all") {
    poolSel.value = "hs_m";
  } else if (board === "pr" && !state.poolTouched) {
    /* ! ARRIVING AT THE TIMES BOARD DEFAULTS TO ALL POOLS. A time is one
         scale regardless of who ran it, so "the fastest 5000s" is the
         question this board is for -- and "all" is the option that exists
         only here, so landing on hs_m hides the thing that makes it
         different. Once the user picks a pool, that choice is theirs. */
    poolSel.value = "all";
  }

  $("subtitle").textContent =
    board === "ability"
      ? "Season ability - averaged across a season, so one lucky race cannot carry an athlete."
      : board === "pr"
      ? "Best times and marks - each athlete's fastest time at one distance, or their longest or highest mark in a field event. Raw clock and tape, no course correction."
      : board === "teams"
      ? "Teams - every squad's top seven raced against each other, scored the ordinary way."
      : board === "courses"
      ? "Courses - how much harder than average the ground is, measured from everyone who raced it."
      : "Single performances - the best individual races, noise and all.";
}


/*
 * Switch board in response to a click: sync the UI, then fetch.
 *
 * The fetch is split out of syncBoard because the URL bootstrap needs the sync
 * WITHOUT the fetch -- it sets several controls and then makes ONE request.
 * Doing sync-and-fetch there would fire a second overlapping request, and
 * which of the two renders last is not something to leave to chance.
 */
function setBoard(board) {
  syncBoard(board);
  load();
}


/* ------------------------------------------------------------------ *
 *  WIRING
 * ------------------------------------------------------------------ */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => setBoard(tab.dataset.board));
});

/* ONE listener on the container, not one per header -- the table is replaced
   wholesale on every load, so per-header listeners would have to be rebound
   each time and any missed rebind is a header that silently stops working. */
$("results").addEventListener("click", (e) => {
  const th = e.target.closest("th.sortable");
  if (th) onHeaderClick(th.dataset.key);
});

// Applying filters returns to page 1; paging keeps the filters.
/* ★ EVERY SINGLE-CLICK CONTROL APPLIES IMMEDIATELY. One gesture, intention
   complete: a select that waits for Apply reads as broken next to a scope
   select that does not -- that was three different rules for one toolbar.
   The dropdown panels still apply on close (ticking eight states is one
   query, not eight), and the typed field (min races) applies on a FINISHED
   edit -- change fires on blur, Enter or a spinner click, never per
   keystroke, so nobody queries for "1" while typing "15". offset resets
   because the new board is a different length. */
function applyNow() { state.offset = 0; load(); }

$("scope").addEventListener("change", applyNow);

/* ★ THE FIELD FOLLOWS THE POOL. A Gender control beside a pool that already
   names one is a control that can only be wrong, so it appears exactly when
   the pool stops deciding. */
/* ⚠ THREE STATES, NOT TWO. The first version asked "is this college?" and
   gave everything else the high-school group -- so a middle school board
   offered League, Section and Division, none of which a middle school is
   placed into. Middle school and elementary get NO unit filters until
   somebody decides what a middle school's units even are (issue #56). */
/* ★ A DIVISION IS MEANINGLESS WITHOUT ITS PARENT. Every state has a "D2"
   and every section has one too, so "state_div=D2" alone matches schools in
   forty states that have nothing to do with each other. The dependent box is
   therefore disabled, and cleared, until its parent has a value -- rather
   than accepted and then quietly returning a nonsense board.

   ! CLEARED, NOT JUST DISABLED. A value left behind a disabled control is a
     filter nobody can see and nobody can remove. */
const UNIT_PARENT = { state_div: "state", section_div: "section",
                      area: "section" };


function syncUnitDeps() {
  for (const [child, parent] of Object.entries(UNIT_PARENT)) {
    const host = document.querySelector(`.combo[data-field="${child}"]`);
    if (!host) continue;
    const field = host.closest(".field");
    const ready = Boolean(combos[parent] && combos[parent].values().length);
    const parentLabel = parent === "state" ? "State" : "Section";
    field.classList.toggle("is-locked", !ready);
    field.title = ready ? "" :
      `Choose a ${parentLabel} first - every ${parentLabel.toLowerCase()} `
      + `has its own divisions, so this filter needs one to mean anything.`;
    /* ★ SAID ON THE LABEL, NOT ONLY IN A TOOLTIP. A greyed control with no
       visible reason reads as broken; a tooltip is only found by someone who
       already suspects there is one. */
    const hint = field.querySelector(`.dep-hint[data-dep="${child}"]`);
    if (hint) hint.textContent = ready ? "" : `(select ${parentLabel} first)`;
    if (!ready && combos[child] && combos[child].values().length) {
      combos[child].set([]);
    }
  }
}

document.addEventListener("combochange", syncUnitDeps);

function syncUnitRows() {
  const pool = $("pool").value;
  const level = pool === "all" ? "" : pool.split("_")[0];
  $("college-units").classList.toggle("hidden", level !== "college");
  $("hs-units").classList.toggle("hidden", level !== "hs");
  /* The whole disclosure goes with them -- an empty "League & division
     filters" that opens onto nothing is worse than no control. */
  $("units").classList.toggle("hidden",
                              level !== "college" && level !== "hs");
  syncUnitDeps();
}

$("pool").addEventListener("change", syncUnitRows);

syncUnitRows();
$("distance").addEventListener("change", applyNow);
/* Date inputs fire change on a completed pick, not per keystroke. */
$("date_from").addEventListener("change", applyNow);
$("date_to").addEventListener("change", applyNow);

$("min_races").addEventListener("input", (e) => {
  e.target.dataset.touched = "1";
});
$("min_races").addEventListener("change", applyNow);
$("sport").addEventListener("change", () => { syncMinRaces(); applyNow(); });

/* Once touched, the pool is the user's and syncBoard stops overriding it. */
$("pool").addEventListener("change", () => {
  state.poolTouched = true;
  applyNow();
});

/* Kept as an explicit refresh -- it costs nothing and it is where the eye
   goes when someone wants to be sure the board matches the controls. */
$("apply").addEventListener("click", applyNow);

/* ★ FIRST IS NOT "PREV, REPEATEDLY". Fifty rows a click is not a way back
   from page twelve, and `next` will happily take you there. Same reset the
   filter controls use -- offset to zero and reload -- so the two paths back
   to the top of a board cannot drift apart. */
$("first").addEventListener("click", () => {
  if (state.offset === 0) return;
  state.offset = 0;
  load();
});

$("prev").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  load();
});

$("next").addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  load();
});

let _noteTimer = null;
function pagerNote(text) {
  const el = $("pageNote");
  if (!el) return;
  el.textContent = text;
  clearTimeout(_noteTimer);
  _noteTimer = setTimeout(() => { el.textContent = ""; }, 6000);
}

/* ★ LAST asks the server how long the board is, then lands on its final
   page. The count is a separate request on purpose: a board load must not
   pay for a count(*) it will not use, and pressing Last is the one time
   the number is wanted. Nothing to do on a board of one page. */
$("last").addEventListener("click", async () => {
  const btn = $("last");
  btn.disabled = true;
  pagerNote("Counting\u2026");
  try {
    const q = buildQuery();
    q.set("count", "1");
    const res = await fetch("/api/rankings?" + q.toString());
    const data = await res.json();
    if (!res.ok || !Number.isInteger(data.total)) {
      /* the server gave up inside its time limit: say so where the eye
         is, and leave the board alone */
      pagerNote(data.reason || data.error || "Could not count this board.");
      return;
    }
    const lastOffset = Math.max(0, Math.floor((data.total - 1) / PAGE_SIZE) * PAGE_SIZE);
    pagerNote("");
    if (lastOffset === state.offset) return;
    state.offset = lastOffset;
    load();
  } catch (err) {
    /* the button just re-enables; the board on screen is unchanged */
  } finally {
    btn.disabled = false;
  }
});

// Enter anywhere in the filter bar applies, instead of doing nothing.
document.querySelectorAll(".filters input").forEach((el) => {
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { state.offset = 0; load(); }
  });
});


/* ------------------------------------------------------------------ *
 *  URL BOOTSTRAP
 *
 *  The home page deep-links here with board/sport/pool/year (built by the
 *  rankings_url macro in home.html). Until this existed those parameters were
 *  DECORATION -- the page booted from the markup defaults and never read the
 *  query string, so every link landed on HS Boys / Both / ability regardless
 *  of what it said.
 * ------------------------------------------------------------------ */

/*
 * Set a <select>, but only to a value it actually offers.
 *
 * Assigning an unknown value to a <select> silently leaves it as "", and
 * buildQuery would then send an EMPTY pool -- which parseFilters rejects with
 * a 400. Ignoring junk and keeping the default is the friendlier failure, and
 * it means a stale bookmark degrades instead of erroring.
 */
function setSelectFromUrl(id, value) {
  if (!value) return;
  const el = $(id);
  if (Array.from(el.options).some((opt) => opt.value === value)) {
    el.value = value;
  }
}


/*
 * Set a plain input from a URL parameter.
 *
 * Absent or empty leaves the field untouched, so the markup's own default
 * survives -- min_races stays 4 on a link that does not mention it, rather
 * than being blanked to "".
 */
function setInputFromUrl(id, value) {
  if (value) $(id).value = value;
}


/*
 * Hydrate the filter controls from the query string.
 *
 * Flat rather than a loop because the element ids happen to match
 * parseFilters' parameter names one-for-one -- but that is a coincidence worth
 * stating explicitly rather than encoding in a clever mapping. The two selects
 * need validation; the free-text inputs do not, because the API validates them
 * and reports its own 400.
 *
 * limit and offset are deliberately NOT read: offset only makes sense in
 * multiples of PAGE_SIZE, and an arbitrary one from a URL would desync the
 * pager's arithmetic from the rows on screen.
 */
function applyUrlFilters(params) {
  /* ! 'all' ONLY ON THE PR BOARD, even from a URL. setSelectFromUrl writes
       whatever it is given, and a stale link carrying pool=all to another
       board would 400 before the user touched anything. */
  const askedPool = params.get("pool");
  setSelectFromUrl("pool",
    askedPool === "all" && state.board !== "pr" ? null : askedPool);
  setSelectFromUrl("sport", params.get("sport"));
  /* ! RESTORED, OR A SHARED "EVERYONE" LINK QUIETLY OPENS AS USA. The select
       defaults to usa in the markup, so without this the one scope worth
       sharing is the one that does not survive being shared. */
  setSelectFromUrl("scope", params.get("scope"));
  syncUnitRows();
  for (const k of UNIT_KEYS) {
    if (combos[k] && params.get(k)) combos[k].set(params.get(k).split(","));
  }
  /* (A call to syncGenderField() stood here. The function never existed --
     the page has no gender control; gender=m|f is a URL-only narrowing of
     pool=all on the times board -- and the ReferenceError aborted the URL
     bootstrap before its load(), so the first paint showed no rows and the
     filters after this line were lost. Issue 130.) */
  /* ! ON TEAMS THE DISTANCE CAN BE OFF THE MENU. Course pages link the
       teams board with the course's own distance -- 2900m is real there --
       and setting a <select> to a value it has no option for silently
       clears it, so the filter in the address bar would vanish from the
       request. Grow the menu to fit before setting. */
  const askedDist = params.get("distance");
  if (askedDist && state.board === "teams") {
    const distSel = $("distance");
    if (distSel && ![...distSel.options].some((o) => o.value === askedDist)) {
      const opt = document.createElement("option");
      opt.value = askedDist;
      opt.textContent = askedDist + "m";
      distSel.appendChild(opt);
    }
  }
  setSelectFromUrl("distance", askedDist);

  /* ⚠ THE COURSE BOARD'S THREE, WHICH LIVE IN THEIR OWN CONTROLS. `distance`
       is spelled the same in the URL on every board but is a different select
       here, and `name` and `min_results` exist nowhere else -- so without this
       a shared course link opens with the filters in the address bar and an
       unfiltered board under them, which is the one thing syncUrl's own note
       says a URL must never do. */
  if (state.board === "courses") {
    setSelectFromUrl("course_distance", params.get("distance"));
    setInputFromUrl("course_name", params.get("name"));
    setInputFromUrl("course_min_results", params.get("min_results"));
  }

  /* Comma-separated back into chips, so a shared URL restores the exact
     filter set rather than one blob of text. */
  for (const field of ["state", "grade", "year", "school", "course"]) {
    const raw = params.get(field);
    if (raw && combos[field]) {
      combos[field].set(raw.split(",").map((s) => s.trim()).filter(Boolean));
    }
  }

  const sort = params.get("sort");
  /* ! A SORT IN THE URL COUNTS AS CHOSEN. Without this the teams board would
       drop it on the first request and re-sort itself, so a shared link to
       "these teams by fifth runner" would open on a different order than the
       one that was shared. */
  if (sort && COLUMNS[state.board].some((c) => c.key === sort)) {
    state.sort = sort;
    state.sortTouched = true;
  }
  const dir = (params.get("dir") || "").toLowerCase();
  if (dir === "asc" || dir === "desc") state.dir = dir;
  /* ! A min_races IN THE URL IS THE USER'S, so mark it touched or the next
       sport change would silently discard what they shared. */
  const askedMin = params.get("min_races");
  setInputFromUrl("min_races", askedMin);
  if (askedMin) $("min_races").dataset.touched = "1";
  syncMinRaces();
  setInputFromUrl("date_from", params.get("date_from"));
  setInputFromUrl("date_to",   params.get("date_to"));
}


/*
 * Boot from the URL, then make the single initial request.
 *
 * board is checked against the two real values rather than passed through.
 * The home page speaks 'ability' and 'performance' correctly, but a
 * hand-edited URL saying anything else falls back to the default instead of
 * sending the API a value it will 400 on -- and note that the home page's own
 * internal name for this board is 'athlete', so a wrong value here is a
 * plausible mistake, not an exotic one.
 */
function initFromUrl() {
  const params = new URLSearchParams(window.location.search);

  /* ! THE WHITELIST HAS TO LIST EVERY BOARD. A ternary on one name sent
       board=pr to the ability board -- which then restored pool=all from the
       same URL, a pool only the pr board accepts, and the first request 400ed
       with a message about pools that had nothing to do with the cause. */
  const asked = params.get("board");
  syncBoard(["performance", "pr", "ability", "teams", "courses"]
              .includes(asked) ? asked : "ability");
  applyUrlFilters(params);

  load();
}


/* ------------------------------------------------------------------ *
 *  FIND AN ATHLETE ON THIS BOARD
 * ------------------------------------------------------------------ */

/*
 * Type a name, pick a suggestion, and the board pages to where that athlete
 * sits under the CURRENT filters.
 *
 * ★ REUSES /search/api RATHER THAN ADDING A LOOKUP. That endpoint already
 *   returns {kind, label, sublabel, link}, and link is /athlete/<person_id>,
 *   so the id falls out of a route the site already serves and already has an
 *   index for.
 *
 * ★ THE RANK IS COMPUTED SERVER-SIDE, ONCE. /api/rankings/rank takes the same
 *   filters and returns the offset that lands on the athlete's page, so this
 *   is one round trip -- not a walk through pages looking for a name.
 *
 * ⚠ ABILITY BOARD ONLY, and the control is hidden on the other one by the
 *   same ability-only CSS the min-races field uses. The performance board
 *   bounds its candidate set with LIMIT before deduping, so a rank against it
 *   would be a rank within that window, not within the corpus.
 */
let findTimer = null;
/* Index of the keyboard-highlighted suggestion, -1 for none. Reset whenever
   the list re-renders -- a stale index would point at a row that moved. */
let findActive = -1;

function findOpts() {
  return $("find-results").querySelectorAll(".find-opt");
}

function findSetActive(i) {
  const opts = findOpts();
  if (!opts.length) { findActive = -1; return; }
  // Wrap at both ends: arrow-down off the last row returns to the first.
  findActive = ((i % opts.length) + opts.length) % opts.length;
  opts.forEach((el, k) => el.classList.toggle("is-active", k === findActive));
  opts[findActive].scrollIntoView({ block: "nearest" });
}

function findStatus(msg, isError) {
  const el = $("find-status");
  el.textContent = msg || "";
  el.className = "find-status" + (msg ? " show" : "") + (isError ? " error" : "");
}

async function findSuggest() {
  const q = $("find-input").value.trim();
  const box = $("find-results");
  findActive = -1;
  if (q.length < 2) { box.innerHTML = ""; box.classList.add("hidden"); return; }

  try {
    const res = await fetch("/search/api?q=" + encodeURIComponent(q));
    const rows = await res.json();
    // Athletes only: a meet or a course has no position on a rankings board.
    const people = (rows || []).filter((r) => /^\/athlete\/\d+$/.test(r.link || ""));
    box.innerHTML = people.slice(0, 8).map((r) =>
      `<button class="find-opt" data-pid="${r.link.split("/").pop()}" ` +
      `data-name="${esc(r.label)}">` +
      `${esc(r.label)}<span class="find-sub">${esc(r.sublabel || "")}</span></button>`
    ).join("");
    box.classList.toggle("hidden", people.length === 0);
  } catch (err) {
    box.classList.add("hidden");
  }
}

async function jumpTo(personId, label) {
  $("find-results").classList.add("hidden");
  findStatus("Looking\u2026", false);

  const q = buildQuery();
  q.set("person_id", personId);
  q.delete("offset");

  try {
    const res = await fetch("/api/rankings/rank?" + q.toString());
    const data = await res.json();

    if (!res.ok) { findStatus(data.error || res.statusText, true); return; }
    if (!data.found) { findStatus(data.reason, true); return; }

    state.offset = data.offset;
    state.highlight = String(personId);
    findStatus(`${label} is #${data.rank.toLocaleString()} on this board.`, false);
    load();
  } catch (err) {
    findStatus("Could not reach the server: " + err.message, true);
  }
}

$("find-input").addEventListener("input", () => {
  clearTimeout(findTimer);
  // Debounced: a keystroke per request would fire a dozen for one name.
  findTimer = setTimeout(findSuggest, 180);
});

/* Keyboard: arrows walk the suggestions, Enter picks the highlighted one --
   or the FIRST one when nothing is highlighted, because someone who typed a
   full name and hit Enter meant the obvious match, not nothing. Escape
   closes without picking. */
$("find-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!findOpts().length) return;
    // Stop the caret jumping to the ends of the input on every press.
    e.preventDefault();
    findSetActive(findActive + (e.key === "ArrowDown" ? 1 : -1));
  } else if (e.key === "Enter") {
    const opts = findOpts();
    const pick = findActive >= 0 ? opts[findActive] : opts[0];
    if (!pick) return;
    e.preventDefault();
    $("find-input").value = pick.dataset.name;
    jumpTo(pick.dataset.pid, pick.dataset.name);
  } else if (e.key === "Escape") {
    $("find-results").classList.add("hidden");
    findActive = -1;
  }
});

$("find-results").addEventListener("mousedown", (e) => {
  const opt = e.target.closest(".find-opt");
  if (!opt) return;
  e.preventDefault();
  /* data-name, not textContent: the button also contains the sublabel, and
     textContent would glue them together as "Anders EricksonCarlton-Wrenshall". */
  $("find-input").value = opt.dataset.name;
  jumpTo(opt.dataset.pid, opt.dataset.name);
});

$("find-input").addEventListener("blur", () => {
  setTimeout(() => $("find-results").classList.add("hidden"), 150);
});


initFromUrl();