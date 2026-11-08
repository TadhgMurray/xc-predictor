/*
 * deploy/cloudflare/worker.js -- the board APIs answered at the edge
 * (prototype, 2026-11-08). See deploy/cloudflare/README.md.
 *
 * ★ WHAT IT DOES, per request on its routes (the board and search APIs only):
 *   1. Anything but GET/HEAD, or a request the snapshot publisher marks
 *      (X-Racecast-Snapshot: bypass), goes to the origin untouched.
 *   2. The URL is put in its canonical spelling (CANONICAL below: the same
 *      rule as racecast/edge_cache.py and static/board-url.js).
 *   3. If the snapshot published after the last go-live holds that URL, the
 *      stored bytes are the answer -- the live app's own response, fetched
 *      by scripts/edge_snapshot.py, byte for byte. No origin, no edge cache.
 *   4. Otherwise the origin is asked for the CANONICAL URL, so Cloudflare's
 *      cache keys every spelling of one board on one entry.
 *
 * ★ FAILS OPEN. Any error reading KV, a missing binding, or a snapshot
 *   switched off (KV "current" = "off") falls through to step 4. Set the
 *   route's failure mode to "Fail open" too, so a Worker over its daily
 *   limit hands requests to the origin instead of an error page.
 *
 * ! THE SNAPSHOT IS VERSIONED. The publisher writes every key under a new
 *   generation ("g<id>:<path>?<query>") and flips "current" to <id> LAST, so
 *   a reader never sees half of one generation and half of another. Old
 *   generations expire on their own (expiration_ttl).
 */

// ---- the canonical spelling (keep equal to edge_cache.canonicalPairs) ----
const TRACKING = new Set(["fbclid", "gclid", "gclsrc", "dclid", "gbraid",
  "wbraid", "msclkid", "yclid", "twclid", "ttclid", "li_fat_id", "igshid",
  "igsh", "mc_cid", "mc_eid", "_ga", "_gl", "srsltid", "mkt_tok", "_hsenc",
  "_hsmi", "rdt_cid", "epik", "s_kwcid"]);

export function isTracking(name) {
  const n = String(name || "").toLowerCase();
  return n.startsWith("utm_") || TRACKING.has(n);
}

export function canonicalQs(search) {
  const src = new URLSearchParams(search);
  const out = new URLSearchParams();
  for (const [k, v] of src) {
    if (isTracking(k) || (k === "offset" && v === "0")) continue;
    out.append(k, v);
  }
  out.sort();
  return out.toString();
}
// ------------------------------------------------------------------------

// how long one isolate trusts its copy of the "current" pointer, so a
// request costs one KV read (the body), not two
const POINTER_MS = 60 * 1000;
let pointer = { gen: null, at: 0 };

async function currentGen(env) {
  const now = Date.now();
  if (now - pointer.at < POINTER_MS) return pointer.gen;
  let gen = null;
  try {
    gen = await env.SNAP.get("current", { cacheTtl: 60 });
  } catch (_e) {
    gen = null;
  }
  pointer = { gen: gen && gen !== "off" ? gen : null, at: now };
  return pointer.gen;
}

function fromSnapshot(body, meta, head) {
  const h = new Headers();
  h.set("Content-Type", (meta && meta.ct) || "application/json");
  // the origin's own header, as the publisher read it: browsers keep it as
  // long as they would keep the origin's answer
  if (meta && meta.cc) h.set("Cache-Control", meta.cc);
  h.set("Vary", "Accept-Encoding");
  h.set("X-Racecast-Edge", "snapshot " + ((meta && meta.gen) || ""));
  return new Response(head ? null : body, { status: 200, headers: h });
}

export default {
  async fetch(request, env, ctx) {
    const method = request.method;
    if ((method !== "GET" && method !== "HEAD")
        || request.headers.get("X-Racecast-Snapshot") === "bypass") {
      return fetch(request);
    }
    const url = new URL(request.url);
    const qs = canonicalQs(url.search);
    const key = url.pathname + (qs ? "?" + qs : "");

    if (env.SNAP) {
      try {
        const gen = await currentGen(env);
        if (gen) {
          const hit = await env.SNAP.getWithMetadata("g" + gen + ":" + key,
                                                     { cacheTtl: 300 });
          if (hit && hit.value !== null) {
            return fromSnapshot(hit.value, hit.metadata, method === "HEAD");
          }
        }
      } catch (_e) {
        // fall through to the origin: the snapshot is an optimisation only
      }
    }

    // the canonical URL, so every spelling of one board shares one cache
    // entry; the request's own headers ride along
    if (url.search.slice(1) === qs) return fetch(request);
    url.search = qs;
    return fetch(new Request(url.toString(), request));
  },
};
