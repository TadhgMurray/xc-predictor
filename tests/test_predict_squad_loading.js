/*
 * Squads load in a few batched requests, once each, for the race they are
 * for (owner, 2026-10-05: "speed up the predictions and the loading of the
 * squads. They're pretty slow", and "adding the entire roster of Amherst
 * should not add this guy" -- a grade-5 Amherst (WI) runner on a NESCAC
 * card, because the level came off the focused race, not the target one).
 *
 * The functions are lifted out of predictions.js and run for real against a
 * stubbed network:   node tests/test_predict_squad_loading.js
 */
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "predictions.js"), "utf8");

function grab(name, kw) {
  const i = SRC.indexOf((kw || "function ") + name + "(");
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

/* the network: every request is recorded, answered after a tick, and the
   number in flight at once is tracked */
const log = [];
let inFlight = 0, maxInFlight = 0, failNext = 0;
function answer(url, params) {
  inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
  log.push({ url, params });
  return new Promise((resolve) => setTimeout(() => {
    inFlight--;
    if (failNext > 0) {
      failNext--;
      resolve({ ok: false, status: 500, statusText: "boom",
                text: async () => JSON.stringify({ error: "boom" }) });
      return;
    }
    let body;
    if (url.startsWith("/api/predict/squads")) {
      const teams = JSON.parse(params.get("teams"));
      body = { squads: teams.map(([s, st]) => ({ school: s, state: st,
               runners: [{ person_id: s.length, name: s }],
               levels: params.get("levels") })) };
    } else {
      body = { school: params.get("school"), state: params.get("state"),
               runners: [{ person_id: 1, name: "x" }],
               levels: params.get("levels") };
    }
    resolve({ ok: true, status: 200, text: async () => JSON.stringify(body) });
  }, 5));
}

const sandbox = eval(`(() => {
  const _edits = new Map();
  const state = { meet: { id: "270850", sport: "XC", div: "2" } };
  const fetch = (url) => {
    const q = new URLSearchParams(url.split("?")[1] || "");
    return answer(url, q);
  };
  const sendQuery = (p, q) => answer(p, q);
  ${grab("readJson", "async function ")}
  ${grab("divKey")}
  ${grab("editsFor")}
  ${grabConst("squadCache")}
  ${grabConst("SQUAD_CHUNK")}
  ${grabConst("SQUAD_PARALLEL")}
  ${grab("squadCtx")}
  ${grab("squadStateOf")}
  ${grab("squadKey")}
  ${grab("squadParams")}
  ${grab("loadSquad", "async function ")}
  ${grab("loadSquads", "async function ")}
  ${grab("mapLimit", "async function ")}
  ${grab("pickState")}
  return { state, editsFor, squadCache, loadSquad, loadSquads, mapLimit,
           pickState };
})()`);
const { state, editsFor, squadCache, loadSquad, loadSquads, mapLimit,
        pickState } = sandbox;

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } return c; };

/* Race "1" is a college race; race "2" (the focused one) a mixed race. */
const schools = Array.from({ length: 90 }, (_, i) => `School ${i}`);
editsFor("1").field = { gender: "M", levels: ["college"],
  teams: [{ school: "Amherst", state: "MA" },
          ...schools.map((s) => ({ school: s, state: null }))] };
editsFor("2").field = { gender: "F", levels: [], teams: [] };

(async () => {
  /* 1. Everyone on a 91-team race: three batched requests, not 91 */
  {
    await loadSquads(["Amherst", ...schools], "1");
    const batch = log.filter((r) => r.url === "/api/predict/squads");
    ok(batch.length === 3, `91 teams in 3 requests, got ${batch.length}`);
    ok(log.length === 3, `and nothing else, got ${log.length}`);
    ok(maxInFlight <= 4, `at most 4 at once, got ${maxInFlight}`);
    /* ★ THE TARGET RACE'S LEVEL, not the focused race's (which is "2") */
    ok(batch.every((r) => r.params.get("levels") === "college"
                     && r.params.get("gender") === "M"
                     && r.params.get("meet_id") === "270850"
                     && r.params.get("div_id") === "1"),
       "every batch carries race 1's gender, level and the meet");
    const first = JSON.parse(batch[0].params.get("teams"));
    ok(JSON.stringify(first[0]) === JSON.stringify(["Amherst", "MA"]),
       "the card's state rides with its school");
    console.log("  Everyone is a few batched requests ................ OK");
  }

  /* 2. and they are held: a card's squad afterwards costs nothing */
  {
    const before = log.length;
    const sq = await loadSquad("Amherst", "1");
    ok(log.length === before, "a cached squad is not fetched again");
    ok(sq.school === "Amherst" && sq.state === "MA", "the batched answer");
    console.log("  a squad fetched in a batch is held ................ OK");
  }

  /* 3. the same squad asked twice at once is one request */
  {
    const before = log.length;
    const [a, b] = await Promise.all([loadSquad("Bates", "2"),
                                      loadSquad("Bates", "2")]);
    ok(log.length === before + 1, "two asks in flight share one fetch");
    ok(a === b, "and the same answer");
    const q = log[log.length - 1].params;
    ok(q.get("gender") === "F" && q.get("levels") === "",
       "race 2's own gender, and its mixed-race empty level SENT (not omitted)");
    console.log("  in-flight asks share one request .................. OK");
  }

  /* 4. a failure is not cached */
  {
    failNext = 1;
    let threw = false;
    try { await loadSquad("Colby", "1"); } catch (e) { threw = true; }
    ok(threw, "a failed squad rejects");
    const before = log.length;
    await loadSquad("Colby", "1");
    ok(log.length === before + 1, "and the next ask tries again");
    console.log("  a failure is retried, not remembered .............. OK");
  }

  /* 5. one race's squad is never served to another race */
  {
    const before = log.length;
    await loadSquad("Bates", "1");
    ok(log.length === before + 1, "Bates for race 1 is not Bates for race 2");
    console.log("  the cache is keyed by the race's shape ............ OK");
  }

  /* 6. the namesake a pick meant */
  {
    ok(pickState({ link: "/school/Amherst?state=WI", label: "Amherst (WI)" }) === "WI",
       "state from the link");
    ok(pickState({ label: "Amherst (MA)" }) === "MA", "state from the label");
    ok(pickState({ label: "Amherst" }) === "", "no state, none invented");
    const before = log.length;
    await loadSquad("Amherst", "2", "WI");
    ok(log[before].params.get("state") === "WI", "a picked state is sent");
    console.log("  a picked school keeps its state ................... OK");
  }

  /* 7. mapLimit keeps order and its cap */
  {
    let live = 0, peak = 0;
    const got = await mapLimit([5, 1, 4, 2, 3], 2, async (x) => {
      live++; peak = Math.max(peak, live);
      await new Promise((r) => setTimeout(r, x));
      live--;
      return x * 10;
    });
    ok(JSON.stringify(got) === "[50,10,40,20,30]", "results in input order");
    ok(peak === 2, `never more than the limit, got ${peak}`);
    console.log("  mapLimit: order kept, cap held .................... OK");
  }

  /* 8. predict() asks its races together, through sendQuery */
  {
    const body = SRC.slice(SRC.indexOf("async function predict()"));
    const fn = body.slice(0, body.indexOf("\n}\n"));
    ok(/mapLimit\(targets, PREDICT_PARALLEL/.test(fn),
       "the races go out together, capped");
    ok(fn.includes("await sendQuery(path, buildQuery(div))"),
       "each through sendQuery, so a big field still POSTs");
    ok(!/for \(const \[idx, div\] of targets\.entries\(\)\) \{\s*const res = await/.test(fn),
       "and no longer one awaited after another");
    console.log("  predict() asks its races together ................. OK");
  }

  if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
  console.log("\nall squad-loading checks passed");
})();
