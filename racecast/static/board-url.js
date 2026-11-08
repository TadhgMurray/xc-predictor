/*
 * board-url.js -- ONE spelling for every board API URL (2026-11-08).
 *
 * ★ WHY. Cloudflare keys its cache on the URL byte for byte, so
 *   "?pool=hs_m&board=ability" and "?board=ability&pool=hs_m", or a page-one
 *   request with and without "offset=0", are two cold copies of one board.
 *   Every caller that builds a board URL -- rankings.js's load, its Last
 *   button and athlete finder, anything that prefetches a board -- goes
 *   through rcBoardUrl, so the same view is always the same URL and one
 *   reader's request warms the edge for the next.
 *
 * THE RULE (racecast/edge_cache.py canonicalPairs is the spec; the edge
 * Worker in deploy/cloudflare/worker.js carries the same three lines, and
 * tests/test_edge_cache.py runs all three on the same inputs):
 *   - tracking parameters (utm_*, fbclid, gclid ...) dropped;
 *   - offset=0 dropped (page one under any default);
 *   - the rest sorted by name, stably (repeated names keep their order).
 * ! NOTHING ELSE IS DROPPED: scope=usa, scale, sort ... have defaults only
 *   the server knows, and a URL that leans on one changes meaning when it
 *   moves (see buildQuery's note on scope).
 *
 * ! THE ADDRESS BAR IS NOT THIS. syncUrl keeps the reader's own order for
 *   the URL people copy and share; only what goes over the wire is canonical.
 */
"use strict";

(function (root) {
  var TRACKING = ["fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "msclkid", "yclid", "twclid", "ttclid", "li_fat_id", "igshid", "igsh",
    "mc_cid", "mc_eid", "_ga", "_gl", "srsltid", "mkt_tok", "_hsenc", "_hsmi",
    "rdt_cid", "epik", "s_kwcid"];

  function isTracking(name) {
    var n = String(name || "").toLowerCase();
    return n.indexOf("utm_") === 0 || TRACKING.indexOf(n) !== -1;
  }

  /* URLSearchParams (or anything it accepts) -> canonical query string */
  function canonicalQs(params) {
    var src = new URLSearchParams(params);
    var out = new URLSearchParams();
    src.forEach(function (v, k) {
      if (isTracking(k) || (k === "offset" && v === "0")) return;
      out.append(k, v);
    });
    out.sort();                     // stable, by name: the spec's order
    return out.toString();
  }

  /* "/api/rankings", URLSearchParams -> "/api/rankings?..." */
  function boardUrl(path, params) {
    var q = canonicalQs(params);
    return q ? path + "?" + q : path;
  }

  root.rcCanonicalQs = canonicalQs;
  root.rcBoardUrl = boardUrl;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { canonicalQs: canonicalQs, boardUrl: boardUrl, isTracking: isTracking };
  }
})(typeof window !== "undefined" ? window : globalThis);
