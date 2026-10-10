/*
 * The first rankings load (the ability board) is still in flight when the
 * reader clicks Teams (owner, 2026-09-29: "all the things show as
 * undefined! Like in team rankings"). The busy guard used to swallow the
 * tab's load, and the ability rows were drawn by the teams renderer.
 *
 *   node tests/test_rankings_board_race.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "racecast", "static", "rankings.js"), "utf8");

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };

// the real load(), and the declarations it needs above it
const start = src.indexOf("let _reloadWanted");
const end = src.indexOf("\n}\n", src.indexOf("async function load()")) + 3;
ok(start > 0 && end > start, "load() found");
const loadSrc = src.slice(start, end);

const el = () => ({ innerHTML: "", disabled: false, textContent: "", value: "",
                    style: {}, classList: { add() {}, remove() {}, toggle() {} } });
const els = {};
const pending = [];
const drawn = [];
const ctx = {
  state: { busy: false, board: "ability", offset: 0 },
  $: (id) => (els[id] = els[id] || el()),
  document: { getElementById: () => null },
  buildQuery: () => new URLSearchParams({ board: ctx.state.board }),
  syncUrl: () => {},
  teamsNote: () => {},
  // the race-shape header (kicker, board heading, No. 1 line; owner, 2026-10-10)
  syncBoardHeader: () => {},
  emptyBoard: () => "empty",
  esc: (x) => String(x),
  PAGE_SIZE: 50,
  renderBoard: (rows) => { drawn.push({ board: ctx.state.board, rows: rows[0].kind }); return "t"; },
  // load() draws through paintBoard (renderBoard, then the phone column order)
  paintBoard: (rows, data) => { ctx.$("results").innerHTML = ctx.renderBoard(rows, data); },
  fetch: (url) => new Promise((resolve) => pending.push({ url, resolve })),
  _lastBoard: null,
};
vm.createContext(ctx);
vm.runInContext(loadSrc + "\nthis.load = load;", ctx);

(async () => {
  const reply = (kind) => ({ ok: true, json: async () => ({ rows: [{ kind }] }) });
  ctx.load();                                   // the page's first load: ability
  ctx.state.board = "teams";                    // the reader clicks Teams ...
  ctx.load();                                   // ... whose load finds busy
  ok(pending.length === 1, "one request in flight");
  pending[0].resolve(reply("athlete"));         // the ability rows land
  await new Promise((r) => setTimeout(r, 0));
  ok(!drawn.some((d) => d.board === "teams" && d.rows === "athlete"),
     "athlete rows are never drawn by the teams renderer");
  ok(pending.length === 2 && /board=teams/.test(pending[1].url),
     "the teams board is fetched once the first load finishes");
  pending[1].resolve(reply("team"));
  await new Promise((r) => setTimeout(r, 0));
  ok(drawn.length === 1 && drawn[0].board === "teams" && drawn[0].rows === "team",
     "and the teams rows are what is drawn");
  ok(ctx.state.busy === false, "and the page is not left busy");
  if (failed) { console.error(`${failed} failed`); process.exit(1); }
  console.log("ok");
})();
