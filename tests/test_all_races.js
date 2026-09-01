/*
 * "All races" is a SELECTION of every division, not the absence of one, and
 * the race mode decides scoring rather than layout.
 *
 *   node tests/test_all_races.js
 */
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name) {
  const i = SRC.indexOf("function " + name + "(");
  if (i < 0) { console.error(`${name} not found`); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  console.error(`${name} is unbalanced`); process.exit(1);
}

const box = eval(`(() => {
  const state = { divs: [], raceMode: "separate", meet: { div: null } };
  ${grab("toggleDiv")}
  ${grab("activeBlocks")}
  return { state, toggleDiv, activeBlocks };
})()`);
const { state, toggleDiv, activeBlocks } = box;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m}: got ${JSON.stringify(a)}`);

const ALL = ["10", "11", "12"];

/* 1. All races picks everything, and picking it again clears. */
{
  eq(toggleDiv([], null, ALL), ALL, "All races selects every division");
  eq(toggleDiv(ALL, null, ALL), [], "pressing it again clears -- still a toggle");
  eq(toggleDiv(["10"], null, ALL), ALL, "from a partial selection it fills in");
  eq(toggleDiv([], null, []), [],
     "a meet with no divisions has nothing to select");
  console.log("  All races is a selection, not an absence ......... OK");
}

/* 2. Named divisions still behave as before -- All races is not special
      once it has been expanded. */
{
  eq(toggleDiv([], "10", ALL), ["10"], "one division");
  eq(toggleDiv(["10"], "11", ALL), ["10", "11"], "two");
  eq(toggleDiv(ALL, "11", ALL), ["10", "12"],
     "unpicking one out of All races leaves the other two");
  console.log("  a named division is unchanged .................... OK");
}

/* 3. THE LAYOUT FOLLOWS THE SELECTION, NOT THE MODE. This is the whole
      point: combined scores as one race but still SHOWS each division,
      because that is how the server builds it. */
{
  state.divs = ALL.slice();
  state.raceMode = "separate";
  eq(activeBlocks(), ALL, "separate: one block per division");
  state.raceMode = "combined";
  eq(activeBlocks(), ALL, "combined: still one block per division");

  state.divs = [];
  state.meet.div = null;
  eq(activeBlocks(), [null], "nothing picked is the whole meet, one block");
  state.meet.div = "10";
  eq(activeBlocks(), ["10"], "a single race with no chips is its own block");
  console.log("  the mode decides scoring, not layout ............. OK");
}

/* 4. The wiring the above depends on, checked in the source: the coalesce
      option and the mode hint key off the division COUNT, which is what
      "All races" used to zero out. */
{
  /* ⚠ THE RULE MOVED WITH #89. Coalesce is about a school entered in two
       divisions of ONE race, so it follows the GROUPING -- any group that
       merges divisions -- rather than the combined preset. "All races" still
       has to be a real selection for a group to have more than one member,
       which is what this originally guarded. */
  ok(/state\.groups\.some\(\(g\) => g\.length > 1\)/.test(SRC),
     "coalesce appears whenever any group merges divisions");
  ok(/const n = state\.divs\.length/.test(SRC),
     "the hint still counts the divisions picked");

  const rf = grab("renderField");
  ok(/const separate = blocks\.length > 1/.test(rf),
     "renderField heads its blocks by how many there are, not by the mode");
  ok(!/state\.raceMode === "separate"/.test(rf),
     "renderField no longer branches on the mode at all");

  const ab = grab("activeBlocks");
  ok(!/raceMode/.test(ab), "neither does activeBlocks");
  console.log("  coalesce and headings follow the count ........... OK");
}




/* ---------------------------------------------------------------- *
 * The Show: control is ONE control for the whole field, and an add
 * can never be silent.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };

  const tpl = fs.readFileSync(
    path.join(__dirname, "..", "racecast", "templates", "predictions.html"),
    "utf8");
  chk(/id="view-sel"/.test(tpl), "the header carries the Show control");

  const blk = grab("renderFieldBlock");
  chk(!/data-view=/.test(blk),
     "the per-block summary no longer renders its own copy -- state.view is "
     + "global, so N copies were N controls for one setting");
  chk(/undo-team/.test(blk),
     "but Undo stays per block: droppedTeams is per division, so 'undo "
     + "removing X' has to name a race to be true");
  chk(/function renderViewSel/.test(SRC), "there is one renderer for it");
  chk(/renderViewSel\(\);/.test(SRC), "and renderField calls it");

  /* Clicking it must open or shut every race, not just the focused one --
     the control is in the header, where focusBlock finds no block. */
  const osc = grab("onSummaryClick");
  chk(/const targets = overall \? activeBlocks\(\)/.test(osc),
     "the overall control applies to every active block");
  chk(!/state\.open\.clear\(\)/.test(osc),
     "and never through state.open, which answers for the focused division "
     + "only -- the header has no block to focus");

  /* An add reports what it did. A successful add used to end in
     setStatus("") -- identical to doing nothing at all. */
  const at = grab("addTeam");
  chk(/setStatus\(`Added \$\{school\}/.test(at),
     "a successful add says so");
  chk(!/setStatus\(""/.test(at),
     "and no longer clears the status, which made success and silence "
     + "look the same");
  chk(/if \(state\.view === "none"\) state\.view = "teams"/.test(at),
     "adding while the rosters are hidden un-hides them, or the card lands "
     + "in a grid with display:none");
  chk(/has not finished loading/.test(at),
     "and the early return says why instead of returning silently");

  if (bad) { failed += bad; }
  else console.log("  one Show control, and an add that speaks .......... OK");
}




/* ---------------------------------------------------------------- *
 * Show is per race AND overall; the chosen athletes are a table.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };

  /* THE REGRESSION: #view-sel is a sibling of #field-summary inside
     .field-head, so a listener on #field-summary or #field never saw it. */
  chk(/querySelector\("\.field-head"\)\s*\n?\s*\.addEventListener\("click", onSummaryClick\)/
        .test(SRC),
     "the header CONTAINER is bound, or the overall Show buttons are dead");

  /* Per race and overall both exist, from one renderer. */
  chk(/function viewButtons\(on\)/.test(SRC), "one renderer for the buttons");
  chk(/viewButtons\(state\.view\)/.test(grab("renderFieldBlock")),
     "each race block carries its own Show control");
  chk(/viewButtons\(all\)/.test(grab("renderViewSel")),
     "and the header carries the overall one");
  chk(/views\.every\(\(v\) => v === views\[0\]\) \? views\[0\] : null/.test(SRC),
     "the overall control lights nothing when the races disagree, rather "
     + "than claiming a state that is not true of all of them");

  /* view is per race now, so it must NOT also be a plain key on state --
     that would shadow the accessor and silently make it global again. */
  chk(/view:\s*\{ get: \(\) => editsFor/.test(SRC),
     "view reaches state through the per-division accessor");
  chk(!/^\s{2}view: "teams",$/m.test(SRC),
     "and there is no bare state.view key shadowing it");
  chk(/view: e\.view/.test(SRC) && /rec\.view = e\.view/.test(SRC),
     "it is saved and restored per division");

  const osc = grab("onSummaryClick");
  chk(/vb\.closest\("#view-sel"\)/.test(osc),
     "where the click landed decides whether it applies to one race or all");

  /* The athlete tab is a table fed by the richer endpoint. */
  chk(/api\/predict\/athletes/.test(grab("athleteRows")),
     "the athlete picker reads the endpoint that carries school and rating, "
     + "not /search/api which carries neither");
  const ra = grab("renderAthletes");
  chk(/chosen-tbl/.test(ra), "the chosen athletes render as a table");
  for (const col of ["School", "Season", "Rating"])
    chk(ra.includes(col), `the table has a ${col} column`);
  chk(/a\.rating == null \|\| isNaN\(a\.rating\)/.test(ra),
     "a restored name-only entry renders blank cells rather than NaN");

  chk(/closest\("\[data-drop-athlete\]/.test(SRC),
     "remove matches the data attribute, not .chip-x -- the button is a "
     + ".r-x in a table row now");

  if (bad) { failed += bad; }
  else console.log("  Show per race + overall; athletes as a table ..... OK");
}




/* ---------------------------------------------------------------- *
 * A new meet is a new world: nothing that describes the old one may
 * survive the switch.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };

  const rms = grab("resetMeetScoped");
  /* ⚠ THE COURSE WAS STILL BEING APPLIED, not merely displayed: chooseMeet
       cleared divisions, groups and edits but not state.course, so meet B
       was predicted on meet A's course. */
  chk(/state\.course = null/.test(rms), "the course override is cleared");
  chk(/box\.value = ""/.test(rms), "and so is the box showing it");
  chk(/state\.raceMode = "separate"/.test(rms) &&
      /state\.coalesce = false/.test(rms),
     "the grouping decisions go too -- they describe one meet's divisions "
     + "and mean nothing about the next");
  chk(/state\.divs = \[\]/.test(rms) && /state\.groups = \[\]/.test(rms),
     "with the divisions and their grouping");
  chk(/co\.checked = false/.test(rms),
     "and the checkbox itself, not just the flag behind it");

  /* Both paths go through it, so there is no third way to get it half
     right -- the Change button cleared the state and left the box, and
     chooseMeet cleared neither. */
  chk((SRC.match(/resetMeetScoped\(\);/g) || []).length === 2,
     "both the picker and the Change button call it");
  const cm = SRC.slice(SRC.indexOf("async function chooseMeet"));
  chk(cm.indexOf("resetMeetScoped();") < cm.indexOf("state.meet = {"),
     "and chooseMeet resets BEFORE adopting the new meet, so loadRaces can "
     + "then fill in that meet's own course");

  if (bad) { failed += bad; }
  else console.log("  a new meet keeps nothing from the old one ....... OK");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall all-races checks passed");
