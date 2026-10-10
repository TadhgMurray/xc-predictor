/*
 * usage.js (2026-10-10): Do Not Track and Global Privacy Control mean
 * NOTHING is sent; otherwise one view beacon after load carrying the route
 * template (never the path), the referring host and the screen class;
 * events batched; errors only from our own scripts, at most three.
 *
 *   node tests/test_usage_beacon.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "racecast", "static", "usage.js"), "utf8");

function run(nav, opts) {
  opts = opts || {};
  const sent = [], listeners = {}, timers = [];
  const on = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
  const navigator = Object.assign({
    sendBeacon: (url, blob) => { sent.push({ url, blob }); return true; },
  }, nav);
  const document = {
    currentScript: { getAttribute: () => "/athlete/<int:person_id>" },
    readyState: "loading",
    referrer: opts.referrer || "",
    visibilityState: "visible",
    addEventListener: on,
  };
  const window = {
    matchMedia: () => ({ matches: !!opts.phone }),
    addEventListener: on,
  };
  const ctx = {
    window, navigator, document, URL,
    location: { href: "https://racecast.co/athlete/123", hostname: "racecast.co",
                origin: "https://racecast.co", pathname: "/athlete/123" },
    Blob: class { constructor(parts) { this.text = parts.join(""); } },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  const fire = (name, ev) => (listeners[name] || []).forEach((f) => f(ev || {}));
  const flushTimers = () => { while (timers.length) timers.shift()(); };
  const bodies = () => sent.map((s) => JSON.parse(s.blob.text));
  return { window, document, fire, flushTimers, bodies, sent };
}

let failed = 0;
const ok = (c, m) => { if (!c) { console.error("  FAIL " + m); failed++; } };

/* 1. Do Not Track, in each spelling browsers use, and GPC: nothing at all. */
for (const nav of [{ doNotTrack: "1" }, { doNotTrack: "yes" }, { msDoNotTrack: "1" },
                   { globalPrivacyControl: true }, { webdriver: true }]) {
  const t = run(nav);
  t.fire("load"); t.flushTimers();
  t.window.rcUse("predict:run"); t.flushTimers(); t.fire("pagehide");
  t.fire("error", { message: "boom", filename: "https://racecast.co/static/a.js", lineno: 3 });
  ok(t.sent.length === 0, `opted out ${JSON.stringify(nav)}: sent ${t.sent.length}`);
  ok(typeof t.window.rcUse === "function", "rcUse still callable when opted out");
}

/* 2. A normal reader: one view, after load, with the template not the path. */
{
  const t = run({}, { referrer: "https://www.google.com/search?q=jane", phone: true });
  ok(t.sent.length === 0, "nothing before load");
  t.fire("load"); t.flushTimers();
  const b = t.bodies();
  ok(b.length === 1 && b[0].k === "v", "one view beacon");
  ok(b[0].t === "/athlete/<int:person_id>", "template, not the address");
  ok(!JSON.stringify(b[0]).includes("123"), "the athlete id never leaves the page");
  ok(b[0].r === "www.google.com" && !JSON.stringify(b[0]).includes("jane"), "referrer host only");
  ok(b[0].s === "p", "phone screen class");
  ok(t.sent[0].url === "/api/b", "same-origin endpoint");
}

/* 3. Events are batched into one beacon. */
{
  const t = run({});
  t.fire("load"); t.flushTimers();
  t.window.rcUse("predict:run"); t.window.rcUse("follow:click");
  t.fire("pagehide");
  const ev = t.bodies().filter((b) => b.k === "e");
  ok(ev.length === 1 && ev[0].e.join() === "predict:run,follow:click", "batched events");
}

/* 4. Errors: our scripts only, three at most, an internal referrer is not a ref. */
{
  const t = run({}, { referrer: "https://racecast.co/rankings" });
  t.fire("load"); t.flushTimers();
  ok(t.bodies()[0].r === "", "own-site referrer is not counted");
  t.fire("error", { message: "x", filename: "chrome-extension://abc/x.js", lineno: 1 });
  t.fire("error", { message: "x", filename: "https://cdn.example/x.js", lineno: 1 });
  ok(t.bodies().length === 1, "foreign scripts are not reported");
  for (let i = 0; i < 5; i++) {
    t.fire("error", { message: "boom " + i, filename: "https://racecast.co/static/rankings.js?v=9", lineno: 7 });
  }
  const errs = t.bodies().filter((b) => b.k === "x");
  ok(errs.length === 3, `capped at three, got ${errs.length}`);
  ok(errs[0].x.f === "/static/rankings.js" && errs[0].x.l === 7, "path of the script, no query");
}

if (failed) { console.error(`${failed} failed`); process.exit(1); }
console.log("usage beacon: ok");
