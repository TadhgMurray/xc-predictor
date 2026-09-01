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
  ok(/state\.raceMode === "combined" && n > 1/.test(SRC),
     "coalesce appears for a combined race of more than one division");
  ok(/const n = state\.divs\.length/.test(SRC),
     "the count is the number of divisions picked, which is why All races "
     + "had to become a real selection before coalesce could appear");

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
  chk(/for \(const d of activeBlocks\(\)\)/.test(osc),
     "the view is applied across every active block");
  chk(!/state\.open\.clear\(\)/.test(osc),
     "not through state.open, which answers for the focused division only");

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

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall all-races checks passed");
