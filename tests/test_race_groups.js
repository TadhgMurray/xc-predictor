/*
 * Issue #89: combine SOME divisions and leave others separate.
 *
 * A group is a SCORING concept, not a layout one -- every picked division
 * keeps its own block, field and edits, exactly as the server builds a
 * combined race (division by division, then unioned). A group only says
 * which of those are scored as one race.
 *
 *   node tests/test_race_groups.js
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
  ${grab("groupsForMode")}
  ${grab("normalizeGroups")}
  ${grab("moveDiv")}
  ${grab("modeOfGroups")}
  return { groupsForMode, normalizeGroups, moveDiv, modeOfGroups };
})()`);
const { groupsForMode, normalizeGroups, moveDiv, modeOfGroups } = box;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m}: got ${JSON.stringify(a)}`);

const D = ["10", "11", "12", "13"];

/* 1. The presets are groupings, which is what let everything downstream
      stay as it was. */
{
  eq(groupsForMode(D, "separate"), [["10"], ["11"], ["12"], ["13"]],
     "separate is one group each");
  eq(groupsForMode(D, "combined"), [D], "combined is one group of all");
  eq(groupsForMode([], "combined"), [], "nothing picked is no groups");
  console.log("  the two old modes are groupings ................. OK");
}

/* 2. normalizeGroups keeps groups a PARTITION of divs. A stale id left in a
      group would be sent to the server as part of a race. */
{
  eq(normalizeGroups(["10", "11"], [["10", "99"], ["11"]]),
     [["10"], ["11"]], "an unpicked division is dropped from its group");
  eq(normalizeGroups(D, [["10", "11"]]),
     [["10", "11"], ["12"], ["13"]],
     "a newly picked division joins as a race of its own");
  eq(normalizeGroups(["10"], [["10"], []]), [["10"]], "empty groups go");
  eq(normalizeGroups(["10", "11"], [["10"], ["10", "11"]]),
     [["10"], ["11"]], "a division cannot be in two groups at once");
  eq(normalizeGroups([], [["10"]]), [], "no picks, no groups");
  console.log("  groups stay a partition of the picked divisions .. OK");
}

/* 3. Moving one division around. */
{
  const g = [["10"], ["11"], ["12"]];
  eq(moveDiv(g, "12", 0), [["10", "12"], ["11"]],
     "into an existing race, and its old group collapses");
  eq(moveDiv([["10", "11"]], "11", -1), [["10"], ["11"]],
     "out into a new race of its own");
  eq(moveDiv([["10", "11"]], "11", 0), [["10", "11"]],
     "into the race it is already in changes nothing");
  eq(moveDiv([["10"]], "10", -1), [["10"]],
     "the last division out of the last group still has a group");
  console.log("  a division moves between races ................... OK");
}

/* 4. THE MODE FOLLOWS THE GROUPING. Rebuilding one-per-division by hand IS
      separate mode, and the radios must not claim "custom" for it. */
{
  ok(modeOfGroups(D, groupsForMode(D, "separate")) === "separate",
     "one each reads as separate");
  ok(modeOfGroups(D, [D]) === "combined", "all in one reads as combined");
  ok(modeOfGroups(D, [["10", "11"], ["12"], ["13"]]) === "custom",
     "a mix reads as custom");
  ok(modeOfGroups(["10"], [["10"]]) === "separate",
     "a single division is separate, not combined -- there is nothing to "
     + "combine it with");
  console.log("  the radios describe the grouping, not vice versa . OK");
}

/* 5. The wiring, in the source: one request per group, div_ids for a group
      of several, and every division's edits carried into it. */
{
  const bq = grab("buildQuery");
  ok(/Array\.isArray\(div\)/.test(bq), "buildQuery takes a group");
  ok(/const combining = group\.length > 1/.test(bq),
     "a group of several is the combined case");
  ok(/mergedEdits\(group\)/.test(bq),
     "and carries every division's edits -- editsFor(null) is the all-races "
     + "record, so a team removed while one division was open would "
     + "otherwise race anyway");
  ok(/q\.set\("div_ids", group\.join\(","\)\)/.test(bq),
     "div_ids for a group of several");
  ok(/if \(group\.length === 1\) q\.set\("div_id"/.test(bq),
     "and the ordinary div_id for a group of one");

  ok(/state\.groups\.map\(\(g\) => g\.slice\(\)\)/.test(SRC),
     "predict makes one request per group");

  /* Coalesce is about a school in two divisions of ONE race, so it applies
     to any multi-division group, not only to the combined preset. */
  ok(/state\.groups\.some\(\(g\) => g\.length > 1\)/.test(SRC),
     "coalesce shows whenever any group merges divisions");

  /* Blocks stay per division -- that is the whole reason this was a small
     change rather than a rewrite. */
  const ab = grab("activeBlocks");
  ok(!/groups/.test(ab),
     "activeBlocks is unchanged: a group is scoring, not layout");
  console.log("  one request per group, blocks still per division . OK");
}

/* 6. A session saved before groups existed still opens. */
{
  ok(/saved\.groups \|\| groupsForMode\(state\.divs, state\.raceMode\)/
       .test(SRC),
     "an old session's mode is exactly the grouping it meant");
}




/* ---------------------------------------------------------------- *
 * One colour per race, and the colour is never the only signal.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };

  const pal = eval(`(() => {
    const _RACE_COLOURS = ${/_RACE_COLOURS = (\[[^\]]*\])/.exec(SRC)[1]};
    ${grab("raceColour")}
    return { _RACE_COLOURS, raceColour };
  })()`);

  chk(pal._RACE_COLOURS.length >= 6, "enough colours for a real meet");
  chk(new Set(pal._RACE_COLOURS).size === pal._RACE_COLOURS.length,
     "no colour is repeated");
  /* Okabe-Ito: the standard categorical set that survives the common colour
     vision deficiencies. Its defining property here is that it contains no
     red/green pair, which is the mistake a hand-picked set makes. */
  for (const c of ["#0072B2", "#E69F00", "#009E73", "#CC79A7"])
    chk(pal._RACE_COLOURS.includes(c), `${c} is part of Okabe-Ito`);
  chk(!pal._RACE_COLOURS.some((c) => /^#(FF0000|00FF00)$/i.test(c)),
     "no raw red or green");

  chk(pal.raceColour(0) === pal._RACE_COLOURS[0], "index 0 is the first");
  chk(pal.raceColour(pal._RACE_COLOURS.length) === pal._RACE_COLOURS[0],
     "it wraps rather than running off the end");
  chk(pal.raceColour(-1) === pal._RACE_COLOURS[pal._RACE_COLOURS.length - 1],
     "and a negative index does not produce undefined");

  /* Applied everywhere a race appears, so the four surfaces agree. */
  for (const [what, re] of [
      ["the field blocks", /style="--race:\$\{raceColour\(gi\)\}"/],
      ["the race chips", /raceColour\(gi\)\)/],
      ["the grouping rows", /--race:\$\{\s*\n?\s*raceColour\(mine/],
      ["the result sections", /--race:\$\{raceColour\(targets\.indexOf\(div\)\)\}/]])
    chk(re.test(SRC), `the colour reaches ${what}`);

  /* ⚠ AND IT IS NEVER THE ONLY SIGNAL. Every coloured thing also carries the
     division's name in text, and a shared block says who it is scored with. */
  const rf = grab("renderField");
  chk(/divLabel\(d\)/.test(rf), "a block still names its division");
  chk(/scored with/.test(rf), "and says when it shares a race");

  const css = fs.readFileSync(
    path.join(__dirname, "..", "racecast", "static", "style.css"), "utf8");
  chk(/color: var\(--race, #374151\)/.test(css),
     "the heading colour is set in the LAST rule that sets color -- an "
     + "earlier one loses on order and never applies");
  chk((css.match(/var\(--race/g) || []).length >= 6,
     "blocks, chips, control and results all read it");

  if (bad) { failed += bad; }
  else console.log("  one colour per race, and never the only cue .... OK");
}




/* ---------------------------------------------------------------- *
 * Quick-select: all boys / all girls, per distance the meet runs.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };
  const qs = eval(`(() => { ${grab("quickSets")} return quickSets; })()`);

  const race = (id, g, d) => ({ div_id: id, gender: g, distance: d });

  /* One distance: the distance is noise, so it is left out. */
  {
    const out = qs([race(1, "M", 5000), race(2, "M", 5000),
                    race(3, "F", 5000), race(4, "F", 5000)]);
    eq(out.map((x) => x.label), ["All boys", "All girls"],
       "one distance needs no distance in the label");
    eq(out.find((x) => x.label === "All boys").ids, ["1", "2"],
       "and the set is the divisions that match");
  }

  /* Two distances: now it has to say which. */
  {
    const out = qs([race(1, "M", 5000), race(2, "M", 5000),
                    race(3, "M", 3200), race(4, "M", 3200),
                    race(5, "F", 5000), race(6, "F", 5000)]);
    eq(out.map((x) => x.label),
       ["All boys · 3200m", "All boys · 5000m", "All girls · 5000m"],
       "each gender/distance pair the meet actually runs");
  }

  /* A set of one duplicates the division's own chip beside it. */
  eq(qs([race(1, "M", 5000), race(2, "F", 5000)]), [],
     "a shortcut to a single race is not a shortcut");

  /* Races with no gender cannot be offered -- an empty promise is worse
     than no button. */
  eq(qs([race(1, null, 5000), race(2, null, 5000)]), [],
     "no gender, no quick-select");
  eq(qs([]), [], "no races, no buttons");

  /* Distance-less races still group by gender. */
  {
    const out = qs([race(1, "M", null), race(2, "M", null)]);
    eq(out.map((x) => x.label), ["All boys"], "a meet with no distances");
  }

  /* The click is additive, and the same button gets you back. */
  chk(/ids\.every\(\(i\) => state\.divs\.includes\(i\)\)/.test(SRC),
     "a quick-select is lit when everything it names is picked");
  chk(/state\.divs\.concat\(\s*\n?\s*ids\.filter/.test(SRC),
     "clicking it ADDS -- picking boys then girls gives you both, where a "
     + "replacing selection would make the second click undo the first");
  chk(/on \? state\.divs\.filter\(\(d\) => !ids\.includes\(d\)\)/.test(SRC),
     "and clicking it again takes exactly that set back out");
  chk(/function repaintChips\(\)/.test(SRC),
     "both click paths repaint the chips through one function");

  if (bad) { failed += bad; }
  else console.log("  quick-select per gender and distance ........... OK");
}




/* ---------------------------------------------------------------- *
 * Coalesce is explained on the CARD, before predicting -- not only in
 * the results, where the decision has already been made.
 * ---------------------------------------------------------------- */
{
  let bad = 0;
  const chk = (c, m) => { if (!c) { console.error("  FAIL " + m); bad++; } };

  const fn = eval(`(() => {
    const _edits = new Map();
    const _divLabels = new Map([["10","D2"],["11","D3"],["12","D4"]]);
    const state = { groups: [], meet: { div: null } };
    ${grab("divKey")}
    ${grab("divLabel")}
    ${grab("editsFor")}
    ${grab("groupIndex")}
    ${grab("alsoInThisRace")}
    return { state, editsFor, alsoInThisRace };
  })()`);

  const seed = (div, ...schools) => {
    const e = fn.editsFor(div);
    e.field = { teams: schools.map((s) => ({ school: s, runners: [{}],
                                             dropped: [] })) };
  };
  seed("10", "Alpha", "Beta");
  seed("11", "Alpha", "Gamma");
  seed("12", "Alpha");

  /* D2 and D3 are ONE race; D4 is its own. */
  fn.state.groups = [["10", "11"], ["12"]];

  const a = fn.alsoInThisRace("10");
  chk(a.get("Alpha") && a.get("Alpha").join() === "D3",
     "a school entered in both divisions of one race is marked");
  chk(!a.has("Beta"), "a school in only one of them is not");
  chk(!a.has("Gamma"), "and a school from the other block is not listed here");

  /* ⚠ THE SAME RACE ONLY. Alpha is also in D4, but D4 is scored separately
     -- that is simply two teams, and coalesce has nothing to say about it. */
  chk(fn.alsoInThisRace("12").size === 0,
     "a division scored on its own marks nothing, however many schools it "
     + "shares with other races");

  fn.state.groups = [["10"], ["11"], ["12"]];
  chk(fn.alsoInThisRace("10").size === 0,
     "and with every division separate there is nothing to coalesce");

  chk(/state\.coalesce \? " is-merged" : ""/.test(SRC),
     "the card says which way the checkbox is set, not just that there is a "
     + "duplicate");
  const co = SRC.slice(SRC.indexOf('$("coalesce").addEventListener'));
  chk(/renderField\(\);/.test(co.slice(0, 500)),
     "toggling coalesce redraws the cards -- the one place explaining it "
     + "must not be stale while someone toggles it to find out");

  if (bad) { failed += bad; }
  else console.log("  a doubly-entered school is marked on its card .. OK");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall race-group checks passed");
