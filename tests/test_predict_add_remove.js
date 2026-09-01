/*
 * Issue #87: the Add/Remove box, one per race, keyed on the school's real
 * name rather than its display label.
 *
 * The functions are lifted out of predictions.js and run for real against a
 * stand-in edit store, so these cannot drift from the shipped code:
 *     node tests/test_predict_add_remove.js
 */
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name) {
  const i = SRC.indexOf("function " + name + "(");
  if (i < 0) { console.error(`${name} not found in predictions.js`); process.exit(1); }
  const open = SRC.indexOf("{", i);
  let depth = 0;
  for (let j = open; j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(i, j + 1);
  }
  console.error(`${name} is unbalanced`); process.exit(1);
}

function grabConst(name) {
  const i = SRC.indexOf("const " + name + " =");
  if (i < 0) { console.error(`${name} not found`); process.exit(1); }
  return SRC.slice(i, SRC.indexOf("\n", i) + 1);
}

const sandbox = eval(`(() => {
  const _edits = new Map();
  const _divLabels = new Map([["10", "D2"], ["12", "D3"]]);
  let _blocks = [null];
  const activeBlocks = () => _blocks.slice();
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                              .replace(/"/g, "&quot;");
  ${grab("divKey")}
  ${grab("divLabel")}
  ${grab("editsFor")}
  ${grab("teamInDiv")}
  ${grab("blocksWith")}
  ${grabConst("schoolValue")}
  ${grab("renderSchoolsFor")}
  ${grab("removeTeam")}
  return { editsFor, teamInDiv, blocksWith, schoolValue, renderSchoolsFor,
           removeTeam, setBlocks: (b) => { _blocks = b; },
           reset: () => _edits.clear() };
})()`);
const { editsFor, teamInDiv, blocksWith, schoolValue, renderSchoolsFor,
        removeTeam, setBlocks, reset } = sandbox;

/* removeTeam calls these two; neither is under test here. */
global.renderField = () => {};
global.setStatus = () => {};

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } return c; };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m}: got ${JSON.stringify(a)}`);

/* A search row as /search/api really returns one: the label carries the
   state, the value does not. */
const row = (bare, st) => ({ label: st ? `${bare} (${st})` : bare,
                             value: bare, sublabel: "412 athletes" });

function seed(div, school) {
  const e = editsFor(div);
  if (!e.field) e.field = { gender: "M", teams: [] };
  e.field.teams.push({ school, runners: [{ person_id: 1, name: "A" },
                                         { person_id: 2, name: "B" }] });
}

/* 1. THE BUG, BOTH HALVES. "DeWitt (MI)" is the label; "DeWitt" is the name
      the field and the database use. Identity is the value. */
{
  reset(); setBlocks([null]);
  eq(schoolValue(row("DeWitt", "MI")), "DeWitt", "the value, not the label");
  eq(schoolValue({ label: "DeWitt" }), "DeWitt", "an unlabelled row still works");

  seed(null, "DeWitt");
  ok(teamInDiv(null, "DeWitt"), "the seeded school is found by its real name");
  ok(!teamInDiv(null, "DeWitt (MI)"), "and the label is not its name");

  const html = renderSchoolsFor(null)([row("DeWitt", "MI")]);
  ok(html.includes(">Remove<"), "a school already racing offers Remove");
  ok(html.includes('data-school="DeWitt"'), "the click carries the bare name");
  ok(html.includes("DeWitt (MI)"), "but the row still READS as the label");
  console.log("  a labelled school matches the field it is in ...... OK");
}

/* 2. And so adding sends the bare name -- the half that failed as
      "No one from DeWitt (MI) has raced this season". */
{
  reset(); setBlocks([null]);
  editsFor(null).field = { gender: "M", teams: [] };
  const html = renderSchoolsFor(null)([row("DeWitt", "MI")]);
  ok(html.includes(">Add<"), "a school not in the field offers Add");
  ok(html.includes('data-school="DeWitt"'), "Add carries the bare name too");
  console.log("  Add sends the name the squad endpoint matches ..... OK");
}

/* 3. One box per race: the box in D2 answers for D2 only. */
{
  reset(); setBlocks(["10", "12"]);
  seed("12", "Jesuit");
  editsFor("10").field = { gender: "M", teams: [] };
  ok(!teamInDiv("10", "Jesuit"), "not in D2");
  ok(teamInDiv("12", "Jesuit"), "in D3");
  ok(renderSchoolsFor("12")([row("Jesuit")]).includes(">Remove<"),
     "D3's box offers Remove");
  const d2 = renderSchoolsFor("10")([row("Jesuit")]);
  ok(d2.includes(">Add<"), "D2's box offers Add");
  ok(d2.includes("already in D3"), "and says where it is already racing");
  console.log("  each race's box answers for that race .............. OK");
}

/* 4. Remove takes the team out and marks its runners removed, written
      through the edit record for THAT race. */
{
  reset(); setBlocks(["10", "12"]);
  seed("10", "Jesuit"); seed("12", "Jesuit");
  removeTeam("10", "Jesuit");
  eq(editsFor("10").field.teams.map((t) => t.school), [], "gone from D2");
  eq([...editsFor("10").removed], ["1", "2"], "its runners marked removed");
  eq(editsFor("12").field.teams.map((t) => t.school), ["Jesuit"],
     "and D3 is untouched");
  console.log("  Remove acts on one race, not all of them .......... OK");
}

/* 5. A pending add counts, so a school cannot be added twice before the
      re-render lands. */
{
  reset(); setBlocks([null]);
  editsFor(null).field = { gender: "M", teams: [] };
  editsFor(null).added.push({ person_id: 7, name: "C", school: "Davis" });
  ok(teamInDiv(null, "Davis"), "a pending add is in the field");
  console.log("  a pending addition counts as in the field ......... OK");
}

/* 6. A name with quotes cannot break out of the attribute it is written
      into -- it goes into data-school, data-label AND a title. */
{
  reset(); setBlocks([null]);
  editsFor(null).field = { gender: "M", teams: [] };
  const html = renderSchoolsFor(null)([
    { label: 'St. "Mary" & Co (CA)', value: 'St. "Mary" & Co' }]);
  ok(!html.includes('data-school="St. "Mary"'), "the attribute is escaped");
  ok(html.includes("&amp;"), "the ampersand is escaped");
  console.log("  a school name with quotes cannot break the row .... OK");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall add/remove checks passed");
