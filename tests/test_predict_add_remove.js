/*
 * Issue #87: the school search box acts on EVERY race on screen.
 *
 * blocksWith and renderSchools are lifted out of predictions.js and run for
 * real against a fake edit store, so these cannot drift from the shipped
 * code:   node tests/test_predict_add_remove.js
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

/* The real functions, over a stand-in for the module scope they close on. */
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
  ${grab("blocksWith")}
  ${grab("renderSchools")}
  return { editsFor, blocksWith, renderSchools,
           setBlocks: (b) => { _blocks = b; }, reset: () => _edits.clear() };
})()`);
const { editsFor, blocksWith, renderSchools, setBlocks, reset } = sandbox;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } return c; };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m}: got ${JSON.stringify(a)}`);

/* Put a school into one division's field. */
function seed(div, school) {
  const e = editsFor(div);
  if (!e.field) e.field = { gender: "M", teams: [] };
  e.field.teams.push({ school, runners: [{ person_id: 1, name: "A" }] });
}

/* 1. THE BUG. Two divisions raced separately; the school is in the second.
      The old teamIsIn read state.field -- one block -- and missed it. */
{
  reset(); setBlocks(["10", "12"]);
  seed("12", "Jesuit");
  eq(blocksWith("Jesuit"), ["12"], "found in the block that has it");
  eq(blocksWith("Davis"), [], "a school in no block");
  const html = renderSchools([{ label: "Jesuit" }]);
  ok(html.includes("Remove from D3"), "an in-field school offers Remove");
  ok(!html.includes(">Add"), "and does not also offer Add");
  console.log("  a school in ANY active race reads as in the field .. OK");
}

/* 2. A school in several races reports all of them, and one Remove covers
      the lot -- the row names every race it will act on. */
{
  reset(); setBlocks(["10", "12"]);
  seed("10", "Jesuit"); seed("12", "Jesuit");
  eq(blocksWith("Jesuit"), ["10", "12"], "in both");
  ok(renderSchools([{ label: "Jesuit" }]).includes("Remove from D2, D3"),
     "Remove names both races");
  console.log("  Remove spans every race that has the school ....... OK");
}

/* 3. A pending add counts, so a school added but not yet re-rendered cannot
      be added a second time. */
{
  reset(); setBlocks(["10", "12"]);
  editsFor("10").field = { gender: "M", teams: [] };
  editsFor("10").added.push({ person_id: 7, name: "B", school: "Davis" });
  eq(blocksWith("Davis"), ["10"], "a pending add is in the field");
  console.log("  a pending addition counts as in the field .......... OK");
}

/* 4. Adding is the ambiguous case: with several races on screen, the row
      fans out so the click says WHICH race, and carries it in data-div. */
{
  reset(); setBlocks(["10", "12"]);
  const html = renderSchools([{ label: "Davis" }]);
  ok(html.includes("Add to D2") && html.includes("Add to D3"),
     "one Add row per race");
  ok(html.includes('data-div="10"') && html.includes('data-div="12"'),
     "each Add carries its own division");
  console.log("  Add fans out over the races, one row each ......... OK");
}

/* 5. One race is not ambiguous, so it stays a plain Add / Remove and the
      division is the empty key -- the whole meet. */
{
  reset(); setBlocks([null]);
  let html = renderSchools([{ label: "Davis" }]);
  ok(html.includes(">Add<"), "a single race says just Add");
  ok(!html.includes("Add to"), "with no division suffix");
  ok(html.includes('data-div=""'), "the whole meet is the empty key");
  seed(null, "Davis");
  html = renderSchools([{ label: "Davis" }]);
  ok(html.includes(">Remove<") && !html.includes("Remove from"),
     "and just Remove once it is in");
  console.log("  a single race keeps the plain Add / Remove ........ OK");
}

/* 6. The school name is escaped -- it goes into an attribute AND a span. */
{
  reset(); setBlocks([null]);
  const html = renderSchools([{ label: 'St. "Mary" & Co' }]);
  ok(!html.includes('data-label="St. "Mary"'), "the attribute is escaped");
  ok(html.includes("&amp;"), "the ampersand is escaped");
  console.log("  a school name with quotes cannot break the row .... OK");
}

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nall add/remove checks passed");
