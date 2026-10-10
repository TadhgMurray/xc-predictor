/*
 * The rankings filter bar, split (owner, 2026-10-10): pool and sport as
 * chips that APPLY ON CHANGE (no Apply button), "Include international
 * athletes" in place of the Scope select, and every old URL parameter
 * still opening the same board.
 *
 *   node tests/test_rankings_inline_filters.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const ROOT = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(ROOT, "racecast", "static", "rankings.js"), "utf8");
const tpl = fs.readFileSync(path.join(ROOT, "racecast", "templates", "rankings.html"), "utf8");

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };

/* one top-level function's source, by name */
function grab(name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error("no function " + name);
  const end = src.indexOf("\n}\n", i) + 3;
  return src.slice(i, end);
}

/* ---- a fake page: two selects and their chip boxes --------------------- */
function makeSelect(id, options, value) {
  const listeners = {};
  const sel = {
    id, value,
    options: options.map(([v, t, hidden]) => ({ value: v, text: t, hidden: Boolean(hidden) })),
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    dispatchEvent: (ev) => { (listeners[ev.type] || []).forEach((fn) => fn(ev)); return true; },
  };
  return sel;
}

function makeChipBox(forId) {
  const box = { dataset: { for: forId }, children: [] };
  Object.defineProperty(box, "innerHTML", {
    set(html) {
      box.children = [...html.matchAll(/data-v="([^"]*)">([^<]*)</g)].map((m) => {
        const attrs = {};
        const cls = new Set(["rk-chip"]);
        const chip = {
          dataset: { v: m[1] }, textContent: m[2], hidden: false,
          classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)),
                       contains: (c) => cls.has(c) },
          setAttribute: (k, v) => { attrs[k] = v; }, attrs,
          closest: (s) => (s === ".rk-chip" ? chip : s === ".rk-chips" ? box : null),
        };
        return chip;
      });
    },
  });
  return box;
}

const els = {
  pool: makeSelect("pool", [["all", "All pools", true], ["hs_m", "HS boys"], ["hs_f", "HS girls"],
                            ["college_m", "College men"]], "hs_m"),
  sport: makeSelect("sport", [["both", "Both"], ["XC", "Cross country"], ["TF", "Track & field"]], "XC"),
  intl: { checked: false, addEventListener() {} },
};
const boxes = [makeChipBox("pool"), makeChipBox("sport")];

const applied = [];
const ctx = {
  $: (id) => els[id],
  document: { querySelectorAll: (s) => (s === ".rk-chips[data-for]" ? boxes : []) },
  esc: (x) => String(x),
  Event: class { constructor(type) { this.type = type; } },
};
vm.createContext(ctx);
vm.runInContext([grab("scopeValue"), grab("syncChips"), grab("pickChip")].join("\n")
  + "\nthis.scopeValue = scopeValue; this.syncChips = syncChips; this.pickChip = pickChip;", ctx);

/* the page's own wiring, as rankings.js does it: a select change applies */
els.pool.addEventListener("change", () => { applied.push("pool=" + els.pool.value); });
els.pool.addEventListener("change", ctx.syncChips);
els.sport.addEventListener("change", () => { applied.push("sport=" + els.sport.value); });
els.sport.addEventListener("change", ctx.syncChips);

/* ---- 1. a chip per option, mirroring the select ------------------------ */
ctx.syncChips();
const [poolBox, sportBox] = boxes;
ok(poolBox.children.length === 4, "one pool chip per option");
ok(poolBox.children[0].hidden, "an option hidden on this board is a hidden chip (All pools)");
ok(poolBox.children[1].classList.contains("is-on"), "the selected pool's chip is lit");
ok(poolBox.children[1].attrs["aria-pressed"] === "true", "and says so to a screen reader");
ok(sportBox.children[1].classList.contains("is-on"), "the sport in view is lit");

/* ---- 2. APPLY ON CHANGE: a chip click is a select change -------------- */
ctx.pickChip({ target: poolBox.children[2] });
ok(els.pool.value === "hs_f", "a chip sets the select");
ok(applied.length === 1 && applied[0] === "pool=hs_f",
   "and fires the select's change, which is what applies (no Apply button)");
ok(poolBox.children[2].classList.contains("is-on")
   && !poolBox.children[1].classList.contains("is-on"), "the lit chip follows");
ctx.pickChip({ target: poolBox.children[2] });
ok(applied.length === 1, "pressing the lit chip again does not reload the board");
ctx.pickChip({ target: sportBox.children[0] });
ok(els.sport.value === "both" && applied[1] === "sport=both", "Both is still a sport chip");

/* ---- 3. Include international athletes is the old scope --------------- */
ok(ctx.scopeValue() === "usa", "unticked is scope=usa, the old default");
els.intl.checked = true;
ok(ctx.scopeValue() === "all", "ticked is scope=all, the old Everyone");

/* ---- 4. URL back-compat: applyUrlFilters on an old shared link --------- */
{
  const set = {};
  const url = {
    pool: makeSelect("pool", [["all", "All pools", true], ["hs_m", "HS boys"], ["college_f", "College women"]], "hs_m"),
    sport: makeSelect("sport", [["both", "Both"], ["XC", "XC"], ["TF", "TF"]], "XC"),
    intl: { checked: false },
    distance: makeSelect("distance", [["", "Any"], ["5000", "5000 m"]], "5000"),
    min_races: { value: "", dataset: {} },
    projected: { checked: false },
    date_from: { value: "" }, date_to: { value: "" },
  };
  const combos = { grade: { set: (v) => { set.grade = v; } } };
  const c2 = {
    $: (id) => url[id] || { value: "", dataset: {} },
    state: { board: "performance", sort: "rating" },
    combos, UNIT_KEYS: [],
    COLUMNS: { performance: [{ key: "rating" }] },
    syncUnitRows: () => {}, syncMinRaces: () => {},
    document: { createElement: () => ({}) },
  };
  vm.createContext(c2);
  vm.runInContext([grab("setSelectFromUrl"), grab("setInputFromUrl"), grab("applyUrlFilters")].join("\n")
    + "\nthis.applyUrlFilters = applyUrlFilters;", c2);
  c2.applyUrlFilters(new URLSearchParams("board=performance&pool=college_f&sport=both&scope=all&grade=9"));
  ok(url.pool.value === "college_f", "an old link's pool is restored");
  ok(url.sport.value === "both", "sport=both on a link still opens on Both");
  ok(url.intl.checked === true, "scope=all ticks Include international athletes");
  ok(JSON.stringify(set.grade) === '["9"]', "a More-filters value (grade) is restored");

  url.intl.checked = true;
  c2.applyUrlFilters(new URLSearchParams("scope=usa"));
  ok(url.intl.checked === false, "scope=usa leaves it unticked");
  url.sport.value = "XC";
  c2.applyUrlFilters(new URLSearchParams("pool=hs_m"));
  ok(url.sport.value === "XC", "a link with no sport keeps the page's default (the season in season)");
}

/* ---- 5. the markup and the script agree ------------------------------- */
ok(!/id="apply"/.test(tpl), "no Apply button on the page");
ok(!/\$\("apply"\)/.test(src), "and nothing in the script reaches for one");
ok(!/id="scope"/.test(tpl) && !/\$\("scope"\)/.test(src), "the Scope select is gone everywhere");
for (const word of ["Athletes (season)", "Best races", "Fastest times", "Teams", "Courses"]) {
  ok(tpl.includes(`>${word}</button>`), `the tab says ${word}`);
}
ok(!/class="tab tab-link"/.test(tpl), "Breakouts and By state are links, not tabs");
ok(/<nav class="rk-tablinks"[\s\S]*href="\/breakouts"[\s\S]*id="by-state-tab"/.test(tpl),
   "they sit at the end of the tab bar");
ok(/<details class="rk-more[^"]*" id="rk-more">/.test(tpl) && !/id="rk-more" open/.test(tpl),
   "More filters is a disclosure, shut by default");
const more = tpl.slice(tpl.indexOf('id="rk-more"'), tpl.indexOf("</details>\n    </div>\n  </details>"));
for (const id of ['id="intl"', 'data-field="grade"', 'data-field="year"', 'data-field="school"',
                  'id="min_races"', 'id="events"', 'id="units"']) {
  ok(more.includes(id), `${id} is under More filters`);
}
const quick = tpl.slice(tpl.indexOf('id="rk-quick"'), tpl.indexOf('id="rk-more"'));
for (const id of ['id="pool"', 'id="sport"', 'data-field="state"']) {
  ok(quick.includes(id), `${id} is inline`);
}
ok(/default_sport == "XC" %\} selected/.test(tpl), "the sport select opens on the season in season");
ok(/const BOARD_WORD = \{ ability: "Athletes \(season\)", performance: "Best races",\s*pr: "Fastest times"/.test(src),
   "the board heading (syncBoardHeader) says the tab's words");

if (failed) { console.error(`${failed} failed`); process.exit(1); }
console.log("ok");
