/*
 * ★ THE PREDICTIONS OPENING KEEPS EVERY WAY IN (owner, 2026-10-10: the
 * approved predictions design). The page now opens on this week's meets and
 * "Who wins state?", and the date, the course and the team size fold under
 * "Change date, course or team size". None of that may cost a deep link:
 *   - a this-week "Predict" link (?meet_id=&sport=&src=) opens that meet,
 *     its feed pinned, with the options still folded;
 *   - a shared link that set a course, a team size or a date other than
 *     the one the page proposes opens the fold, values in place;
 *   - mode=rerun_exact still lands on "Test the model on a past race";
 *   - a restored session reopens the fold when it had set any of them, and
 *     a ?meet_id= link naming another meet still beats the saved session.
 * restoreFromQuery, restoreState and openOpts run for real, lifted out of
 * predictions.js with the page around them stubbed:
 *     node tests/test_predict_opts_fold.js
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

let fails = 0;
const ok = (cond, msg) => {
  console.log((cond ? "  ok  " : "  FAIL") + " " + msg);
  if (!cond) fails++;
};

const PROPOSED = "2026-10-17";       // what loadRaces would put in the date box

function page(opts) {
  opts = opts || {};
  const els = {};
  const el = (id) => (els[id] = els[id] || {
    id, value: "", checked: false, hidden: false, open: false, placeholder: "",
    classList: { add() {}, remove() {}, toggle() {} },
  });
  const log = { chosen: null, predicted: 0, when: null, who: null, restored: null };
  const store = { "rc-predict-v1": opts.saved ? JSON.stringify(opts.saved) : null };
  const sb = eval(`(() => {
    const $ = (id) => ${opts.noFold ? '(id === "pred-opts" ? null : el(id))' : "el(id)"};
    const location = { search: ${JSON.stringify(opts.search || "")} };
    const SESSION_KEY = "rc-predict-v1";
    const sessionStorage = { getItem: (k) => store[k] || null, setItem() {} };
    const state = { meet: null, when: "thisyear", who: "team", divs: [],
                    raceMode: "separate", groups: [], coalesce: false,
                    course: null, perTeam: null, athletes: [] };
    const _edits = new Map();
    const _divLabels = new Map();
    const fetch = async (url) => ({ ok: true, json: async () =>
      url.indexOf("/api/predict/share/") === 0
        ? { query: ${JSON.stringify(opts.shareQuery || "")}, extra: {} }
        : { meet_name: "2025 Clovis Invitational", date: "2025-10-11" } });
    const showWhen = (w) => { log.when = w; state.when = w; };
    const showWho = (w) => { log.who = w; state.who = w; };
    const raceLink = (s, id, d) => "/race/" + s + "/" + id + "/" + d;
    const chooseMeet = async (d) => { log.chosen = d; state.meet = { id: "x" }; };
    const normalizeGroups = (d, g) => g;
    const groupsForMode = () => [];
    const syncPerTeam = () => {};
    const loadRaces = async () => { el("t-date").value = "${PROPOSED}"; };
    const loadField = async () => {};
    const applySharedField = () => {};
    const renderField = () => {};
    const renderAthletes = () => {};
    const renderChosenMeet = () => {};
    const showStep = () => {};
    const resetEdits = () => {};
    const editsFor = () => ({});
    const saveState = () => {};
    const setStatus = () => {};
    const predict = () => { log.predicted++; };
    ${grab("parsePerTeam")}
    ${grab("openOpts")}
    ${grab("restoreFromQuery", "async function ")}
    const _rfq = restoreFromQuery;
    ${grab("restoreState").replace(/restoreFromQuery\(/g, "rfq(")}
    function rfq(p, n) { log.restored = p.toString(); return _rfq(p, n); }
    return { state, restoreFromQuery, restoreState };
  })()`);
  return { sb, els, el, log };
}

(async () => {
  // ---- a this-week Predict link: the meet, its feed, the fold closed
  let t = page();
  await t.sb.restoreFromQuery(new URLSearchParams("meet_id=270001&sport=XC&src=tfrrs"), {});
  ok(t.log.chosen && t.log.chosen.link === "/meet/xc/270001?src=tfrrs",
     "a Predict link from this week opens that meet with its feed pinned");
  ok(t.log.when === "thisyear", "...in Run it this year");
  ok(t.el("pred-opts").open === false, "...with the options still folded");
  ok(t.el("t-date").value === PROPOSED, "...and the proposed date left alone");
  ok(t.log.predicted === 1, "...and the prediction runs");

  // ---- a shared link that set a course
  t = page();
  await t.sb.restoreFromQuery(new URLSearchParams(
    `meet_id=91&sport=XC&div_id=3&date=${PROPOSED}&course=Mt.%20SAC`), {});
  ok(t.el("pred-opts").open === true, "a shared link with a course opens the fold");
  ok(t.el("t-course").value === "Mt. SAC" && t.sb.state.course === "Mt. SAC",
     "...the course in its box and in the request");
  ok(t.log.chosen.link === "/race/xc/91/3", "...on the race the link named");

  // ---- a shared link that set the team size
  t = page();
  await t.sb.restoreFromQuery(new URLSearchParams(`meet_id=91&sport=XC&date=${PROPOSED}&per_team=10`), {});
  ok(t.el("pred-opts").open === true && t.sb.state.perTeam === 10,
     "a shared link with ten runners a team opens the fold");

  // ---- a this-year link always carries its date: only a moved one opens
  t = page();
  await t.sb.restoreFromQuery(new URLSearchParams(`meet_id=91&sport=XC&date=${PROPOSED}`), {});
  ok(t.el("pred-opts").open === false, "the proposed date alone does not open the fold");
  t = page();
  await t.sb.restoreFromQuery(new URLSearchParams("meet_id=91&sport=XC&date=2026-10-24"), {});
  ok(t.el("pred-opts").open === true && t.el("t-date").value === "2026-10-24",
     "a moved date opens it, the date in the box");

  // ---- the backtest mode still restores
  t = page();
  await t.sb.restoreFromQuery(new URLSearchParams("meet_id=91&sport=XC&mode=rerun_exact"), {});
  ok(t.log.when === "asran", "mode=rerun_exact lands on Test the model on a past race");

  // ---- a page without the fold (an old cached page) still restores
  t = page({ noFold: true });
  await t.sb.restoreFromQuery(new URLSearchParams("meet_id=91&sport=XC&course=Mt.%20SAC"), {});
  ok(t.log.predicted === 1, "no fold on the page is not an error");

  // ---- the session: a saved course reopens the fold
  const saved = { meet: { id: "91", sport: "XC", label: "2025 Clovis Invitational" },
                  when: "thisyear", who: "team", divs: [], course: "Mt. SAC", perTeam: null };
  t = page({ saved, search: "" });
  t.sb.restoreState();
  ok(t.el("pred-opts").open === true, "a restored session with a course reopens the fold");
  t = page({ saved: { ...saved, course: null }, search: "" });
  t.sb.restoreState();
  ok(t.el("pred-opts").open === false, "...and one without stays folded");

  // ---- ?meet_id= naming another meet still beats the session
  t = page({ saved, search: "?meet_id=270001&sport=XC" });
  t.sb.restoreState();
  await new Promise((r) => setTimeout(r, 0));
  ok(t.log.restored === "meet_id=270001&sport=XC", "a link to another meet wins over the saved session");

  // ---- ?s= is read back and restored like a long link
  t = page({ search: "?s=abc123", shareQuery: "meet_id=91&sport=XC&per_team=9" });
  t.sb.restoreState();
  await new Promise((r) => setTimeout(r, 20));
  ok(t.log.restored === "meet_id=91&sport=XC&per_team=9", "a saved share link is restored");
  ok(t.el("pred-opts").open === true, "...and its team size opens the fold");

  if (fails) { console.error(`${fails} check(s) failed`); process.exit(1); }
  console.log("all checks passed");
})();
