/*
 * athlete-charts.js -- line charts for the athlete page.
 *
 * Goes in racecast/static/. Loaded by templates/athlete.html.
 *
 * NO CHARTING LIBRARY. Hand-drawn SVG. The trade: no 200KB CDN dependency, no
 * page that breaks when a CDN is unreachable, and charts that inherit the
 * site's own colours.
 *
 * ★ ONE VIEWBOX UNIT = ONE SCREEN PIXEL.
 *
 *   The first version used a fixed 640-wide viewBox with
 *   preserveAspectRatio="none", which stretches x and y by DIFFERENT amounts
 *   to fill the container. That distorted everything: axis text came out
 *   horizontally squashed, dots rendered as ellipses, and any padding measured
 *   in viewBox units shrank or grew with the window -- so the left inset that
 *   kept points off the y-axis was only reliable at one specific width.
 *
 *   Measuring the container and drawing in real pixels removes the whole class
 *   of problem. The cost is a redraw on resize, which is what the
 *   ResizeObserver at the bottom is for.
 *
 * ★ THE X-AXIS IS ORDINAL, NOT TIME-PROPORTIONAL.
 *
 *   Races sit at even intervals regardless of the days between them. A true
 *   time axis crushes a season into a few pixels and leaves a huge gap over
 *   the off-season. The gap between two dots therefore carries no meaning --
 *   the season dividers show where the calendar actually breaks.
 */

"use strict";

/* ------------------------------------------------------------------ *
 *  GEOMETRY (all values are CSS pixels)
 * ------------------------------------------------------------------ */

/* Room for the axis labels. Left is widest because y labels are the longest
 * text on the chart ("5:42/mi"). */
const PAD = { top: 16, right: 18, bottom: 30, left: 84 };

/* Where the y-axis numbers START. They are LEFT-aligned, so every label
 * begins at the same x and grows rightward -- the digits line up on their
 * left edge rather than their right. PAD.left must stay comfortably wider
 * than the longest label, or a long one ('5:42/mi') runs under the
 * gridlines. */
const Y_LABEL_X = 8;

/* Every dot, every chart, one colour. Borrowed from .flag--cpr in style.css so
   the charts use a blue the site already contains rather than a new one. */
const DOT_COLOUR = "#2e53e6";

/* Horizontal breathing room INSIDE the plot area, so the first and last dots
 * do not sit on the y-axis or the right edge. In real pixels now, so it stays
 * 16px at every container width. */
const X_INSET = 24;

/* Fallback width for the first draw, if the container has not been laid out
 * yet (display:none, or a hidden tab). The ResizeObserver corrects it as soon
 * as it gets a real box. */
const FALLBACK_W = 640;

/* Below this many points a line is misleading -- two dots joined by a straight
 * segment implies a trend that two races cannot support. */
const MIN_POINTS = 2;


/* ------------------------------------------------------------------ *
 *  FORMATTING
 * ------------------------------------------------------------------ */

/*
 * Seconds to m:ss.
 *
 * Math.round BEFORE splitting, or 119.7s renders as "1:59" -- the seconds part
 * rounds to 60 and the minutes part never hears about it.
 */
function fmtTime(seconds) {
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtPace(secondsPerMile) {
  return fmtTime(secondsPerMile) + "/mi";
}

function fmtRating(value) {
  return Number(value).toFixed(1);
}

/* "2025-08-30" -> "Aug 30, 2025", for tooltips only. Built from the string
 * rather than a Date, so a UTC-vs-local shift cannot move a race a day. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${MONTHS[Number(m) - 1]} ${Number(d)}, ${y}`;
}

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}


/* ------------------------------------------------------------------ *
 *  SCALES
 * ------------------------------------------------------------------ */

/*
 * Ordinal x: point i sits at slot i of n, inset from both edges.
 *
 * The n <= 1 case returns the centre. Dividing by (n-1) would be division by
 * zero and every point would render at NaN, which draws nothing at all and
 * looks like a data problem rather than a maths one.
 */
function makeXScale(n, width) {
  const lo = PAD.left + X_INSET;
  const hi = width - PAD.right - X_INSET;
  if (n <= 1) return () => (lo + hi) / 2;
  return (i) => lo + (i / (n - 1)) * (hi - lo);
}

/*
 * Linear y scale, INVERTED: SVG's origin is top-left, so a bigger value must
 * map to a smaller pixel coordinate or every chart comes out upside down.
 *
 * The span === 0 guard matters -- every athlete has some chart where all points
 * share one value (one race, or two identical times).
 */
function makeYScale(min, max, height) {
  const top = PAD.top;
  const bottom = height - PAD.bottom;
  const span = max - min;
  if (span === 0) return () => (top + bottom) / 2;
  return (v) => bottom - ((v - min) / span) * (bottom - top);
}

/*
 * Round a raw range outward to readable tick values (130, 135, 140) instead of
 * whatever the data spanned (131.4, 139.87).
 *
 * The 8% padding stops the top and bottom points sitting exactly on the frame,
 * where their dots get clipped in half.
 */
function niceRange(min, max) {
  if (min === max) return [min - 1, max + 1, 1];

  const pad = (max - min) * 0.08;
  let lo = min - pad;
  let hi = max + pad;

  const rawStep = (hi - lo) / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * mag >= rawStep) * mag;

  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  return [lo, hi, step];
}


/* ------------------------------------------------------------------ *
 *  SEASONS
 * ------------------------------------------------------------------ */

/*
 * Split point indices into runs sharing a SEASON -- one year of one sport.
 *
 * Keying on year alone merged a spring TF season and the following autumn XC
 * season into one block, which is wrong twice over: they are different
 * seasons, and on the career chart it hid the sport change at exactly the
 * point the reader most needs to see it.
 *
 * `end` is INCLUSIVE, so a one-race season is start === end rather than a
 * zero-width run.
 *
 * One pass works because the series is sorted by date, so a season cannot
 * reappear after a later one has started.
 */
function seasonRuns(points) {
  const runs = [];
  points.forEach((p, i) => {
    const key = `${p.y}|${p.sp || ""}`;
    const last = runs[runs.length - 1];
    if (last && last.key === key) {
      last.end = i;
    } else {
      runs.push({ key, year: p.y, sport: p.sp, start: i, end: i });
    }
  });
  return runs;
}


/* ------------------------------------------------------------------ *
 *  DRAWING
 * ------------------------------------------------------------------ */

/*
 * Draw one chart into `host`.
 *
 * points  [{ d, v, meet, result, rid, y, sp }]  already sorted by date
 * opts    { title, fmt, bySport }
 *         fmt      formats a y value for the axis and tooltip
 *         bySport  colour dots by XC/TF -- only for the combined chart
 *
 * Height comes from the CSS custom property --chart-h on the host, so the
 * sidebar can run short charts and the main column tall ones without this
 * file knowing about either.
 */
function drawChart(host, points, opts) {
  const width = Math.max(240, host.clientWidth || FALLBACK_W);
  const height = parseInt(
    getComputedStyle(host).getPropertyValue("--chart-h"), 10) || 220;

  host.innerHTML = "";

  /* Zero points is nothing to draw. ONE point is a real race and gets a dot --
     the earlier version refused it, which meant an athlete's first season
     showed "only one race" instead of the race. There is no line, because a
     line through one point is not a thing, but the dot is hoverable and
     clickable like any other. */
  if (!points || points.length === 0) {
    host.classList.add("chart-empty");
    host.innerHTML =
      `<div class="chart-title">${esc(opts.title)}</div>` +
      `<div class="chart-none">no data</div>`;
    return;
  }

  const data = points;
  const n = data.length;

  const ys = data.map((p) => p.v);
  const [yLo, yHi, yStep] = niceRange(Math.min(...ys), Math.max(...ys));

  const xScale = makeXScale(n, width);
  const yScale = makeYScale(yLo, yHi, height);

  const parts = [];

  /* --- horizontal gridlines + y labels ---
     Gridlines start at PAD.left; the first dot is X_INSET further right, so
     nothing ever sits on the axis. */
  for (let v = yLo; v <= yHi + 1e-9; v += yStep) {
    const y = yScale(v).toFixed(1);
    parts.push(
      `<line class="grid" x1="${PAD.left}" y1="${y}" ` +
      `x2="${width - PAD.right}" y2="${y}"/>`,
      `<text class="axis-y" x="${Y_LABEL_X}" y="${y}" ` +
      `dominant-baseline="middle">${esc(opts.fmt(v))}</text>`
    );
  }

  /* --- season dividers + year labels ---
     A divider goes BETWEEN the last race of one year and the first of the
     next, at the midpoint of that gap -- not on top of a race, which would
     make it look like the divider belonged to that point.

     The year label is centred over its own run, so a twelve-race season gets a
     label in the middle of twelve slots rather than at one edge. */
  const runs = seasonRuns(data);

  /* --- season dividers + labels ---
     A divider goes BETWEEN the last race of one season and the first of the
     next, at the midpoint of that gap. Not on top of a race: a divider sitting
     on a dot reads as belonging to it.

     seasonRuns keys on year AND sport, so a spring TF season and the following
     autumn XC season are two runs and get a rule between them -- on the career
     chart that boundary is exactly where the reader needs a break. */
  runs.forEach((run, i) => {
    if (i > 0) {
      const divX = (xScale(run.start) + xScale(run.start - 1)) / 2;
      parts.push(
        `<line class="season-div" x1="${divX.toFixed(1)}" y1="${PAD.top}" ` +
        `x2="${divX.toFixed(1)}" y2="${height - PAD.bottom}"/>`
      );
    }

    /* Centred over the run it names, so a twelve-race season gets its label in
       the middle of twelve slots rather than at one edge.

       "XC 2025" rather than "2025": with a divider per season the year alone
       would appear twice in a row on the career chart with nothing to tell the
       two apart. */
    const mid = ((xScale(run.start) + xScale(run.end)) / 2).toFixed(1);
    /* Roughly 6.2px per character at the 11px axis font. Measuring properly
       would need getComputedTextLength, which requires the element to be in
       the DOM -- and this string is being built before it exists. An estimate
       is fine because the consequence of getting it slightly wrong is one
       label shown or hidden, not a broken layout. */
    const label = run.sport ? `${run.sport} ${run.year}` : String(run.year);
    const runWidth = xScale(run.end) - xScale(run.start);
    const needed = label.length * 6.2;

    /* A one-race season has zero width, so allow the label anyway if there is
       clear space to the next divider. Otherwise short seasons at the ends of
       a career -- often the interesting ones -- would never be labelled. */
    const next = runs[i + 1];
    const slotWidth = next
      ? xScale(next.start) - xScale(run.start)
      : (width - PAD.right) - xScale(run.start);

    if (run.year && Math.max(runWidth, slotWidth) >= needed) {
      parts.push(
        `<text class="axis-x" x="${mid}" y="${height - 10}" ` +
        `text-anchor="middle">${esc(label)}</text>`
      );
    }
  });

  /* --- the line ---
     ONE continuous path in one colour. The sport is carried by the dot colours
     and the season labels; splitting the line by sport as well was noise. */
  const path = data
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},` +
                   `${yScale(p.v).toFixed(1)}`)
    .join(" ");
  parts.push(`<path class="line" d="${path}"/>`);

  /* Seasons of exactly one race have no segment to draw, so their dot must
     survive even when crowding would otherwise suppress it -- otherwise that
     race vanishes from the chart entirely. */
  const isolated = new Set(
    runs.filter((r) => r.start === r.end).map((r) => r.start));

  /* --- the dots ---
     Sizing still scales with crowding: a career chart can hold 150 races, and
     at a fixed 3.5px radius they merge into a caterpillar.

     ★ BUT THEY NEVER DISAPPEAR. This used to drop to radius 0 past ~60 points,
       leaving a bare line. A dot is the only thing that says "a race happened
       here" -- without it a season of 70 races and a season of 7 look like the
       same continuous curve, and there is nothing to aim a cursor at. Past 60
       the radius shrinks to 1.6px instead, which reads as texture on the line
       rather than as a caterpillar, and every race stays visible.

     The invisible hit circles are unchanged and still carry the hovering. */
  const spacing = n > 1 ? xScale(1) - xScale(0) : width;
  const dotR = n > 60 ? 1.6 : (n > 25 ? 2.5 : 3.5);
  const hitR = Math.max(4, Math.min(12, spacing * 0.6));

  data.forEach((p, i) => {
    const cx = xScale(i).toFixed(1);
    const cy = yScale(p.v).toFixed(1);
    /* ★ NO PER-SPORT COLOURING. The dots were split XC/TF on the combined
       chart, which made one athlete's career read as two series -- and the
       whole point of that chart is that speed_rating is 5K-equivalent and
       difficulty-adjusted, so an XC 5k and a TF 1600m ARE comparable. One
       colour says that; two said the opposite. The sport is still in the
       tooltip for anyone who wants it. */

    const label = `${fmtDate(p.d)} \u2014 ${opts.fmt(p.v)}` +
                  (opts.bySport && p.sp ? ` \u2014 ${p.sp}` : "") +
                  (p.meet ? ` \u2014 ${p.meet}` : "") +
                  (p.result ? ` (${p.result})` : "");

    /* Fill set here rather than left to a class: athlete-charts.css still
       carries the dot--xc / dot--tf rules, and setting it inline guarantees
       one colour whatever remains in that file. */
    parts.push(`<circle class="dot" cx="${cx}" cy="${cy}" ` +
               `r="${dotR}" fill="${DOT_COLOUR}"/>`);
    parts.push(
      `<circle class="hit" cx="${cx}" cy="${cy}" r="${hitR.toFixed(1)}" ` +
      `data-label="${esc(label)}" data-rid="${p.rid ?? ""}"/>`
    );
  });

  host.classList.remove("chart-empty");
  host.innerHTML =
    `<div class="chart-title">${esc(opts.title)}` +
    `</div>` +
    /* No preserveAspectRatio override: the default keeps x and y in step, and
       because the viewBox matches the measured pixel box there is nothing to
       scale anyway. */
    `<svg viewBox="0 0 ${width} ${height}" width="${width}" ` +
    `height="${height}" role="img" aria-label="${esc(opts.title)}">` +
    `${parts.join("")}</svg>` +
    `<div class="chart-tip" hidden></div>`;

  attachHover(host);
}


/*
 * Tooltip and click wiring.
 *
 * ONE listener on the SVG, not one per dot. With ~150 races across several
 * charts that is a handful of listeners instead of hundreds, and dots redrawn
 * on resize are picked up automatically because the listener never referenced
 * them directly.
 */
function attachHover(host) {
  const tip = host.querySelector(".chart-tip");
  const svg = host.querySelector("svg");

  svg.addEventListener("mouseover", (e) => {
    const hit = e.target.closest(".hit");
    if (!hit) return;

    tip.textContent = hit.dataset.label;
    /* Unhide BEFORE measuring. A hidden element has offsetWidth 0, so
       clamping against it would place every tooltip at the same wrong spot. */
    tip.hidden = false;

    /* Positioned against the HOST, not the page: the tooltip lives in a
       relatively-positioned container, so it follows the chart on scroll with
       no scroll handler. */
    const hostBox = host.getBoundingClientRect();
    const dotBox = hit.getBoundingClientRect();

    const tipW = tip.offsetWidth;
    const tipH = tip.offsetHeight;
    const margin = 4;

    /* --- horizontal clamp ---
       translate(-50%) means the tooltip's left edge sits at (left - tipW/2),
       so keeping it inside the box means keeping `left` between tipW/2 and
       hostW - tipW/2. Without this the first and last dots put half the
       tooltip outside the chart, where it gets clipped.

       Math.max after Math.min so that a tooltip WIDER than the host still
       lands at the left edge rather than being pushed off the right. */
    const centre = dotBox.left - hostBox.left + dotBox.width / 2;
    const half = tipW / 2;
    const left = Math.max(
      half + margin,
      Math.min(centre, hostBox.width - half - margin)
    );

    /* --- vertical flip ---
       The tooltip normally sits above the dot. For points near the top of the
       plot that would put it above the chart entirely, so it flips below and
       drops the -100% translate. */
    const above = dotBox.top - hostBox.top - 8;
    const flip = above - tipH < 0;

    tip.style.left = `${left}px`;
    tip.style.top = flip
      ? `${dotBox.bottom - hostBox.top + 8}px`
      : `${above}px`;
    tip.style.transform = flip
      ? "translate(-50%, 0)"
      : "translate(-50%, -100%)";
  });

  svg.addEventListener("mouseout", (e) => {
    if (e.target.closest(".hit")) tip.hidden = true;
  });

  /* Clicking a dot jumps to that race's row, already anchored in the season
     tables as id="race-<result_id>". */
  svg.addEventListener("click", (e) => {
    const hit = e.target.closest(".hit");
    if (!hit || !hit.dataset.rid) return;

    const row = document.getElementById(`race-${hit.dataset.rid}`);
    if (!row) return;

    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("race-flash");
    setTimeout(() => row.classList.remove("race-flash"), 1200);
  });
}


/* ------------------------------------------------------------------ *
 *  BOOT
 * ------------------------------------------------------------------ */

/*
 * Read the JSON the route embedded, then draw every slot and keep it in sync
 * with its container width.
 *
 * Each slot carries data-chart="<key>"; a slot whose key is missing or empty
 * renders the "no data" state rather than keeping placeholder text. Failing
 * visibly beats a div that still reads like a caption and never becomes a
 * chart.
 */
function initCharts() {
  const node = document.getElementById("chart-data");
  if (!node) return;

  let data;
  try {
    data = JSON.parse(node.textContent);
  } catch (err) {
    console.error("chart data is not valid JSON", err);
    return;
  }

  const formats = { rating: fmtRating, pace: fmtPace, time: fmtTime };
  const drawn = [];

  document.querySelectorAll("[data-chart]").forEach((host) => {
    const key = host.dataset.chart;

    /* Per-distance TF charts live in a nested object, addressed as
       "tf_dist.1600m". One dotted key keeps the markup declarative instead of
       needing a special case per distance. */
    const points = key.includes(".")
      ? (data[key.split(".")[0]] || {})[key.split(".").slice(1).join(".")]
      : data[key];

    const opts = {
      title: host.dataset.title || key,
      fmt: formats[host.dataset.kind || "rating"] || fmtRating,
      bySport: host.dataset.bysport === "1"
    };

    drawChart(host, points, opts);
    drawn.push({ host, points, opts });
  });

  /*
   * REDRAW ON RESIZE. Because the chart is drawn in pixels rather than scaled,
   * a wider container needs new coordinates -- an SVG that simply stretched
   * would reintroduce the distortion this whole approach exists to avoid.
   *
   * requestAnimationFrame coalesces the burst of callbacks a drag produces into
   * one redraw per frame. Without it a slow drag redraws every chart dozens of
   * times a second.
   */
  if (typeof ResizeObserver === "undefined") return;

  let pending = false;
  const observer = new ResizeObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      drawn.forEach(({ host, points, opts }) => drawChart(host, points, opts));
    });
  });

  drawn.forEach(({ host }) => observer.observe(host));
}

document.addEventListener("DOMContentLoaded", initCharts);