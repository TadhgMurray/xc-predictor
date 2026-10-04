/*
 * rating-explain.js -- "How was this rated?" on any rating cell.
 *
 * ★ WHY (owner, 2026-10-04: Hammerand's WashU 10k, 29:20.48, rated 126.3 on
 *   a +7.03% heat credit for a race that ran at night, beside his 29:27 at
 *   NCAA at 117.6 -- "nobody could see why from the site"). A rating cell
 *   that carries data-rx="<xc|tf>:<result_id>" (athlete.html, race.html,
 *   race_tf.html) becomes a button; pressing it fetches
 *   /api/explain/<sport>/<id> ONCE (kept for the page's life) and draws the
 *   chain -- distance, era, track, weather, course, race day, event, track
 *   season, other -- each as a percent of time and in rating points.
 *
 * ! LAZY: nothing is fetched until a rating is pressed, so a 200-row race
 *   page costs nothing extra to load.
 * ! A POPOVER BESIDE THE CELL ON DESKTOP, A SHEET FROM THE BOTTOM ON A
 *   PHONE. On a phone every table scrolls inside its own box (layout.js),
 *   so anything anchored to a cell would be clipped by that box; the sheet
 *   is fixed to the viewport instead. Either way it hangs off <body>.
 * ! THE PAGE'S SCALE IS NOT THE STEPS' SCALE. The steps are on the pool the
 *   rating was solved in; when scale-view.js is showing the HS-equivalent,
 *   the sheet says both numbers rather than leave a reader to wonder why
 *   152.2 on the page ends at 126.3 here.
 */
"use strict";

(function () {
  var cache = {};                      /* "tf:123" -> body (or a pending Promise) */
  var pop = null, backdrop = null, opener = null, anchorCell = null;

  function isPhone() {
    return window.matchMedia && window.matchMedia("(max-width: 700px)").matches;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function signed(v, places) {
    if (v == null || isNaN(v)) return "";
    var a = Math.abs(v) < Math.pow(10, -places) / 2 ? 0 : v;
    return (a > 0 ? "+" : a < 0 ? "−" : "") + Math.abs(a).toFixed(places);
  }

  function mdy(iso) {
    if (!iso) return "";
    var p = iso.split("-");
    var mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
               "Oct", "Nov", "Dec"][parseInt(p[1], 10) - 1];
    return mon ? mon + " " + parseInt(p[2], 10) + ", " + p[0] : iso;
  }

  /* --- wiring: every rated cell becomes a button --------------------- */
  function arm(root) {
    var cells = (root || document).querySelectorAll("td[data-rx]");
    for (var i = 0; i < cells.length; i++) {
      var rv = cells[i].querySelector(".rv");
      if (!rv || rv.dataset.rxArmed) continue;
      rv.dataset.rxArmed = "1";
      rv.classList.add("rx-hot");
      rv.setAttribute("role", "button");
      rv.setAttribute("tabindex", "0");
      rv.setAttribute("aria-haspopup", "dialog");
      rv.setAttribute("title", "How was this rated?");
    }
  }

  document.addEventListener("click", function (e) {
    var rv = e.target.closest && e.target.closest("td[data-rx] .rv");
    if (rv) {
      e.preventDefault();
      open(rv);
      return;
    }
    if (pop && !pop.hidden && !pop.contains(e.target)) close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && pop && !pop.hidden) { close(); return; }
    var rv = e.target.closest && e.target.closest("td[data-rx] .rv");
    if (rv && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      open(rv);
    }
  });

  /* --- the panel ----------------------------------------------------- */
  function ensurePanel() {
    if (pop) return;
    backdrop = document.createElement("div");
    backdrop.className = "rx-backdrop";
    backdrop.hidden = true;
    backdrop.addEventListener("click", close);
    pop = document.createElement("div");
    pop.className = "rx-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "How this result was rated");
    pop.hidden = true;
    document.body.appendChild(backdrop);
    document.body.appendChild(pop);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, { passive: true });
    document.addEventListener("scroll", place, { passive: true, capture: true });
  }

  function open(rv) {
    ensurePanel();
    var td = rv.closest("td[data-rx]");
    var key = (td.getAttribute("data-rx") || "").toLowerCase();
    var m = /^(xc|tf):(-?\d+)$/.exec(key);
    if (!m) return;
    opener = rv;
    anchorCell = td;
    pop.classList.toggle("rx-sheet", isPhone());
    backdrop.hidden = !isPhone();
    pop.hidden = false;
    pop.innerHTML = head(null) +
      '<div class="rx-body"><p class="rx-loading">Working it out…</p></div>';
    wireClose();
    place();
    load(m[1], m[2]).then(function (body) {
      if (opener !== rv || pop.hidden) return;   /* another one was opened */
      render(body, rv);
    }, function () {
      if (opener !== rv || pop.hidden) return;
      pop.querySelector(".rx-body").innerHTML =
        '<p class="rx-na">The breakdown is not available right now.</p>';
    });
  }

  function close() {
    if (!pop || pop.hidden) return;
    pop.hidden = true;
    backdrop.hidden = true;
    var o = opener;
    opener = anchorCell = null;
    if (o && document.activeElement && pop.contains(document.activeElement)) o.focus();
  }

  function wireClose() {
    var b = pop.querySelector(".rx-close");
    if (b) {
      b.addEventListener("click", function (e) { e.stopPropagation(); close(); });
      try { b.focus({ preventScroll: true }); } catch (err) { b.focus(); }
    }
  }

  function load(sport, rid) {
    var key = sport + ":" + rid;
    if (cache[key]) return Promise.resolve(cache[key]);
    return fetch("/api/explain/" + sport + "/" + rid, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body || body.ok === false) throw new Error((body && body.error) || "no body");
        cache[key] = body;
        return body;
      });
  }

  /* desktop: beside the cell, below it when there is room, kept on screen;
     phone: the sheet is pinned by CSS and needs nothing here */
  function place() {
    if (!pop || pop.hidden || !anchorCell) return;
    if (pop.classList.contains("rx-sheet")) return;
    var r = anchorCell.getBoundingClientRect();
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var w = Math.min(420, vw - 24);
    pop.style.width = w + "px";
    var left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, vw - w - 12));
    /* ! NEVER OVER THE ROW IT EXPLAINS: below the cell when it fits, else
       above, else on the roomier side with its height cut to that room
       (it scrolls inside); only a cell with no room either side is
       covered. */
    pop.style.maxHeight = "";
    var h = pop.offsetHeight;
    var below = vh - r.bottom - 20, above = r.top - 20, top;
    if (h <= below) {
      top = r.bottom + 8;
    } else if (h <= above) {
      top = r.top - 8 - h;
    } else if (Math.max(below, above) >= 260) {
      if (below >= above) {
        pop.style.maxHeight = below + "px";
        top = r.bottom + 8;
      } else {
        pop.style.maxHeight = above + "px";
        top = r.top - 8 - pop.offsetHeight;
      }
    } else {
      top = vh - h - 12;
    }
    top = Math.max(12, Math.min(top, vh - pop.offsetHeight - 12));
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  function head(body) {
    var race = (body && body.race) || {};
    var bits = [race.time, race.event, race.meet, mdy(race.date)]
      .filter(function (x) { return x; }).map(esc);
    return '<div class="rx-head">' +
      '<div><div class="rx-title">How this was rated</div>' +
      (bits.length ? '<div class="rx-sub">' + bits.join(" · ") + "</div>" : "") +
      '</div><button type="button" class="rx-close" aria-label="Close">×</button></div>';
  }

  function render(body, rv) {
    var html = head(body) + '<div class="rx-body">';
    var steps = body.steps || [];
    var dist = steps.filter(function (s) { return s.key === "distance"; })[0];
    if (dist) {
      html += '<div class="rx-start">' +
        (body.start_rating != null
          ? '<span class="rx-chip">' + body.start_rating.toFixed(1) + "</span>" : "") +
        '<p class="rx-text">' + esc(dist.text) + "</p></div>";
    }
    html += '<ol class="rx-steps">';
    steps.forEach(function (s) {
      if (s.key === "distance") return;
      var cls = "rx-step rx-" + s.key + (s.available === false ? " is-na" : "") +
        (s.pct != null && Math.abs(s.pct) >= 0.05 ? (s.pct > 0 ? " is-up" : " is-down") : " is-flat");
      html += '<li class="' + cls + '"><div class="rx-row">' +
        '<span class="rx-label">' + esc(s.label) +
        (s.key === "day" && s.applied === false && s.available !== false
          ? ' <span class="rx-tag">not applied</span>' : "") + "</span>";
      if (s.available === false) {
        html += '<span class="rx-pct rx-muted">n/a</span><span class="rx-pts"></span>';
      } else {
        html += '<span class="rx-pct">' + signed(s.pct, s.pct != null && Math.abs(s.pct) < 1 ? 2 : 1) +
          (s.pct != null ? "%" : "") + "</span>" +
          '<span class="rx-pts">' + (s.pts != null ? signed(s.pts, 1) : "") + "</span>";
      }
      html += "</div>";
      html += '<p class="rx-text">' + esc(s.text) + "</p>";
      if (s.key === "weather" && s.detail && s.detail.hourly && s.detail.hourly.length) {
        html += tempStrip(s.detail);
      }
      html += "</li>";
    });
    html += "</ol>";
    if (body.rating != null) {
      html += '<div class="rx-total"><span>Rating</span><span class="rx-final">' +
        body.rating.toFixed(1) + "</span></div>";
      var shown = parseFloat(rv.textContent);
      if (!isNaN(shown) && Math.abs(shown - body.rating) >= 0.05) {
        html += '<p class="rx-note">This page shows ' + shown.toFixed(1) +
          " on the HS-equivalent scale; the steps are on the " +
          esc(body.race && body.race.pool_label || "athlete's own") +
          " scale, where it is " + body.rating.toFixed(1) + ".</p>";
      }
    }
    (body.notes || []).forEach(function (n) {
      html += '<p class="rx-note">' + esc(n) + "</p>";
    });
    html += '<p class="rx-foot">Percent of time: + means the conditions were ' +
      "judged slow and the time is credited. Points are this rating's.</p>";
    html += "</div>";
    pop.innerHTML = html;
    wireClose();
    place();
  }

  /* the day's feels-like temperature, hour by hour: the race window
     shaded, the hour the model read (the peak, for track) marked */
  function tempStrip(d) {
    var hrs = d.hourly.filter(function (h) { return h[1] != null; });
    if (!hrs.length) return "";
    var lo = d.hours ? d.hours[0] : -1, hi = d.hours ? d.hours[1] : -1;
    var vals = hrs.map(function (h) { return h[1]; });
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    var span = Math.max(mx - mn, 4);
    var W = 240, H = 44, bw = W / 24;
    var svg = '<svg class="rx-strip" viewBox="0 0 ' + W + " " + (H + 12) +
      '" role="img" aria-label="Feels-like temperature by local hour">';
    if (lo >= 0) {
      svg += '<rect class="rx-win" x="' + (lo * bw) + '" y="0" width="' +
        ((hi - lo + 1) * bw) + '" height="' + H + '"></rect>';
    }
    hrs.forEach(function (h) {
      var bh = 4 + (H - 6) * (h[1] - mn) / span;
      var inWin = h[0] >= lo && h[0] <= hi;
      var peak = d.temp_agg === "max" && h[0] === d.peak_hour;
      svg += '<rect class="rx-bar' + (inWin ? " in" : "") + (peak ? " peak" : "") +
        '" x="' + (h[0] * bw + 1) + '" y="' + (H - bh) + '" width="' + (bw - 2) +
        '" height="' + bh + '"><title>' + hour(h[0]) + ": " + h[1].toFixed(0) +
        "°C</title></rect>";
    });
    [0, 6, 12, 18].forEach(function (x) {
      svg += '<text x="' + (x * bw + 1) + '" y="' + (H + 10) + '">' + hour(x) + "</text>";
    });
    svg += "</svg>";
    return '<div class="rx-stripwrap">' + svg + '<span class="rx-legend">' +
      "feels-like °C by local hour; shaded: the hours the model reads" +
      "</span></div>";
  }

  function hour(h) {
    return h === 0 ? "12am" : h === 12 ? "12pm" : h < 12 ? h + "am" : (h - 12) + "pm";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { arm(); });
  } else {
    arm();
  }
  window.rcRatingExplain = { arm: arm };
})();
