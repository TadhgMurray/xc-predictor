// conversions.js -- drives the conversions tool.
// One source in -> /api/convert -> fills the Time+Rating cells of both tables.

(function () {
    var typeSel = document.getElementById('source-type');
    var poolSel = document.getElementById('source-pool');
    var sportSel = document.getElementById('source-sport');
    var btn = document.getElementById('convert-btn');
    if (!typeSel || !btn) return;

    // ---- show only the input fields relevant to the chosen source -------
    function paintFields() {
        var t = typeSel.value;
        document.querySelectorAll('.src-field').forEach(function (f) {
            var forTypes = (f.getAttribute('data-for') || '').split(' ');
            f.style.display = forTypes.indexOf(t) >= 0 ? '' : 'none';
        });

        // ★ THE ATHLETE SURVIVES A SOURCE-TYPE CHANGE.
        //   The athlete field is data-for="athlete result", so it stays on
        //   screen and chosenAthlete keeps its person_id -- but loadRaces only
        //   ran at PICK time, and only when the type was ALREADY 'result'.
        //   Picking an athlete under "An athlete" and then switching to "A
        //   specific result" therefore left the select reading "pick athlete
        //   first" while the athlete sat selected right above it.
        //
        //   Loading here instead of at pick time means the races follow
        //   whichever way the user arrives at 'result'.
        //
        //   `chosenAthlete &&` because paintFields() also runs once at startup,
        //   BEFORE `var chosenAthlete = {...}` executes. Hoisting makes the
        //   name exist but leaves it undefined, so the guard is load-bearing
        //   rather than defensive.
        if (t === 'result' && typeof chosenAthlete !== 'undefined' &&
                chosenAthlete && chosenAthlete.person_id) {
            loadRaces(chosenAthlete.person_id);
        }
    }
    typeSel.addEventListener('change', paintFields);
    paintFields();

    // ---- CLOSE ANY OPEN DROPDOWN ON AN OUTSIDE CLICK --------------------
    // ONE delegated listener for every menu on the page, rather than a blur
    // handler per input. Blur alone is not enough: the fetch-backed typeahead
    // has none, and a blur handler races the click that picks an item (which
    // is why the pickers use mousedown + preventDefault).
    //
    // The test is "did the click land inside the anchor that owns this menu".
    // Clicking the input keeps its own menu open; clicking anywhere else --
    // another field, the page background, a table -- closes everything.
    document.addEventListener('mousedown', function (e) {
        var anchor = e.target.closest ? e.target.closest('.drop-anchor') : null;
        document.querySelectorAll('.conv-drop').forEach(function (d) {
            if (!anchor || !anchor.contains(d)) d.innerHTML = '';
        });
    });

    // Escape closes them too, for anyone who reaches for it.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.conv-drop').forEach(function (d) {
            d.innerHTML = '';
        });
    });

    // ---- TIME MASK ------------------------------------------------------
    // The field starts at 00:00:00.00 and digits fill from the RIGHT, so the
    // first thing you type is hundredths and it walks up through seconds,
    // minutes, hours. Typing 90110 gives 00:09:01.10 -- no colons to type and
    // no ambiguity about which field a bare number lands in.
    //
    var TIME_ZERO = '00:00:00';

    function maskTime(raw) {
        // Keep only digits, keep the LAST 6 (older ones scroll off the left
        // exactly as they do on a stopwatch), zero-pad, then punctuate.
        var d = String(raw).replace(/\D/g, '').slice(-6);
        while (d.length < 6) d = '0' + d;
        return d.slice(0, 2) + ':' + d.slice(2, 4) + ':' + d.slice(4, 6);
    }

    var timeEl = document.getElementById('in-time');
    if (timeEl) {
        timeEl.addEventListener('input', function () {
            timeEl.value = maskTime(timeEl.value);
            // Caret to the end: digits enter at the right, so anywhere else
            // would make the next keystroke land in the wrong place.
            timeEl.setSelectionRange(timeEl.value.length, timeEl.value.length);
        });
        timeEl.addEventListener('focus', function () {
            timeEl.setSelectionRange(timeEl.value.length, timeEl.value.length);
        });
    }

    // ---- DISTANCE DROPDOWN ----------------------------------------------
    // Same custom dropdown as the course field, NOT a <datalist>: a datalist
    // is drawn by the browser and cannot be styled or sized, which is why it
    // never matched the input's width. Still fully editable -- type any
    // number and it is used as-is.
    var COMMON_DISTANCES = [
        {label: '400m', m: 400}, {label: '800m', m: 800},
        {label: '1500m', m: 1500}, {label: '1600m', m: 1600},
        {label: 'Mile', m: 1609.34}, {label: '3000m', m: 3000},
        {label: '3200m', m: 3200}, {label: '2 Mile', m: 3218.69},
        {label: '4000m (XC)', m: 4000}, {label: '3 Mile (XC)', m: 4828},
        {label: '5000m', m: 5000}, {label: '6000m (XC)', m: 6000},
        {label: '8000m (XC)', m: 8000}, {label: '10,000m', m: 10000}
    ];

    // localTypeahead: the same shape as typeahead() below but filtering a
    // client-side list instead of fetching. Written once here and used for
    // both distance fields rather than duplicated per input.
    function localTypeahead(input, drop, list, onPick) {
        if (!input || !drop) return;
        function paint(q) {
            var hits = list.filter(function (e) {
                return !q || e.label.toLowerCase().indexOf(q) >= 0 ||
                       String(e.m).indexOf(q) >= 0;
            });
            drop.innerHTML = hits.map(function (e) {
                return '<div class="drop-item" data-m="' + e.m +
                       '" data-label="' + esc(e.label) + '">' + esc(e.label) +
                       ' <span class="drop-meta">' + e.m + ' m</span></div>';
            }).join('');
        }
        input.addEventListener('input', function () {
            paint(input.value.trim().toLowerCase());
        });
        // Focus with an empty box shows the whole list, so the field reads as
        // a dropdown rather than a search you have to guess at.
        input.addEventListener('focus', function () {
            if (!input.value.trim()) paint('');
        });
        input.addEventListener('blur', function () {
            setTimeout(function () { drop.innerHTML = ''; }, 150);
        });
        drop.addEventListener('mousedown', function (e) {
            var item = e.target.closest('.drop-item');
            if (!item) return;
            e.preventDefault();                  // keep focus off the blur race
            onPick(item);
            drop.innerHTML = '';
        });
    }

    localTypeahead(document.getElementById('in-distance'),
                   document.getElementById('in-distance-drop'),
                   COMMON_DISTANCES,
                   function (item) {
                       document.getElementById('in-distance').value =
                           item.getAttribute('data-m');
                   });

    // ---- TIME PARSING ---------------------------------------------------
    // Accepts ss.ss, m:ss.ss, or h:mm:ss.ss. Splitting on ":" and folding
    // right-to-left with a x60 multiplier means one loop covers all three
    // shapes -- no branching on how many colons were typed.
    function parseTime(str) {
        if (!str) return NaN;
        var parts = String(str).trim().split(':');
        var mult = 1, total = 0;
        for (var i = parts.length - 1; i >= 0; i--) {
            var v = parseFloat(parts[i]);
            if (isNaN(v)) return NaN;
            total += v * mult;
            mult *= 60;
        }
        return total;
    }

    // ---- UNIT TOGGLES ---------------------------------------------------
    // Each unit-bearing input carries data-unit = the unit CURRENTLY SHOWN.
    // Clicking a button converts the value in place and rewrites that
    // attribute. Nothing else in the file reads the input raw: readWeather
    // always converts back to canonical (degC, m/s) first, so the wire format
    // is unchanged and only the display moves.
    var UNITS = {
        C:   { toCanon: function (v) { return v; },
               fromCanon: function (v) { return v; } },
        F:   { toCanon: function (v) { return (v - 32) * 5 / 9; },
               fromCanon: function (v) { return v * 9 / 5 + 32; } },
        ms:  { toCanon: function (v) { return v; },
               fromCanon: function (v) { return v; } },
        mph: { toCanon: function (v) { return v * 0.44704; },
               fromCanon: function (v) { return v / 0.44704; } }
    };

    // The canonical value of an input, whatever unit it is displaying.
    function canonValue(el) {
        if (!el || el.value === '') return null;
        var v = parseFloat(el.value);
        if (isNaN(v)) return null;
        var u = UNITS[el.getAttribute('data-unit') || 'C'];
        return u ? u.toCanon(v) : v;
    }

    // Write a canonical value into an input, displayed in ITS current unit.
    function setCanon(el, canon) {
        if (!el || canon == null || isNaN(canon)) return;
        var u = UNITS[el.getAttribute('data-unit') || 'C'];
        el.value = (u ? u.fromCanon(canon) : canon).toFixed(1);
    }

    // One delegated listener rather than one per button, so buttons added to
    // the page later keep working without rewiring.
    document.addEventListener('click', function (e) {
        var b = e.target.closest ? e.target.closest('.unit-btn') : null;
        if (!b) return;
        e.preventDefault();
        var el = document.getElementById(b.getAttribute('data-target'));
        var to = b.getAttribute('data-unit');
        if (!el || !UNITS[to]) return;
        var from = el.getAttribute('data-unit') || 'C';
        if (from !== to && el.value !== '') {
            // via canonical, so C->F->C round-trips instead of drifting
            el.value = UNITS[to].fromCanon(UNITS[from].toCanon(
                parseFloat(el.value))).toFixed(1);
        }
        el.setAttribute('data-unit', to);
        // light up the sibling buttons pointing at the same input
        document.querySelectorAll('.unit-btn[data-target="' +
                b.getAttribute('data-target') + '"]').forEach(function (o) {
            o.classList.toggle('is-on', o === b);
        });
    });

    // ---- APPARENT TEMP FROM ITS PARTS -----------------------------------
    // Australian apparent temperature -- the definition Open-Meteo reports,
    // which is where the corpus's apparent_temp came from.
    //
    //     e  = rh/100 * 6.105 * exp(17.27*Ta / (237.7+Ta))    vapour pressure
    //     AT = Ta + 0.33*e - 0.70*ws - 4.00
    //
    // ⚠ This is a SECOND IMPLEMENTATION of a definition this codebase does not
    //   own. It fills the apparent-temp field as a convenience; the field
    //   itself stays editable so a real looked-up value always wins.
    function deriveApparentTemp() {
        var ta = canonValue(document.getElementById('in-wx-airtemp'));
        var rhEl = document.getElementById('in-wx-humidity');
        var rh = rhEl && rhEl.value !== '' ? parseFloat(rhEl.value) : null;
        if (ta == null || rh == null || isNaN(rh)) return;
        var ws = canonValue(document.getElementById('in-wx-wind')) || 0;
        var e = (rh / 100) * 6.105 * Math.exp(17.27 * ta / (237.7 + ta));
        setCanon(document.getElementById('in-wx-temp'),
                 ta + 0.33 * e - 0.70 * ws - 4.00);
        var note = document.getElementById('wx-derived');
        if (note) note.textContent =
            'Apparent temp filled in above (approximate). Edit it directly to override.';
    }
    ['in-wx-airtemp', 'in-wx-humidity', 'in-wx-wind'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', deriveApparentTemp);
    });

    // ---- collect input weather (only fields the user filled) ------------
    function readWeather(prefix) {
        var w = {};
        var map = { temp: 'apparent_temp', wind: 'wind',
                    precip: 'precip', soil: 'soil' };
        Object.keys(map).forEach(function (k) {
            var el = document.getElementById(prefix + k);
            if (!el || el.value === '') return;
            // canonValue, not parseFloat: the field may be showing degF or mph.
            var v = canonValue(el);
            if (v === null) v = parseFloat(el.value);   // unitless fields
            if (!isNaN(v)) w[map[k]] = v;
        });
        return Object.keys(w).length ? w : null;
    }

    // ---- build the source spec from the visible fields ------------------
    var chosenInCourse = { course: null, difficulty: 0 };

    typeahead(
        document.getElementById('in-course'),
        document.getElementById('in-course-drop'),
        '/api/course_search?q=',
        function (c) {
            // input course: one row per COURSE (not per distance) -- we only
            // need its difficulty; the distance comes from the time field.
            return '<div class="drop-item" data-name="' + esc(c.name) +
                   '" data-diff="' + c.difficulty + '">' +
                   esc(c.name) + ' <span class="drop-meta">diff ' +
                   (c.difficulty != null ? c.difficulty.toFixed(3) : '?') +
                   '</span></div>';
        },
        function (item) {
            chosenInCourse.course = item.getAttribute('data-name');
            chosenInCourse.difficulty = parseFloat(item.getAttribute('data-diff')) || 0;
            document.getElementById('in-course').value = chosenInCourse.course;
            document.getElementById('in-course-chosen').textContent =
                'On: ' + chosenInCourse.course;
        }
    );

    function hsMode() {
        return Boolean(window.rcScale && window.rcScale.mode === 'hs');
    }

    // ★ THE POOL FOLLOWS THE ATHLETE (issue 49). The select defaulted to
    //   hs_m, so a college runner arriving from their page was converted as
    //   a high schooler. /api/athlete_pool says which pool their latest
    //   season is in; the select follows, and the reader can still change it.
    function adoptAthletePool(pid, then) {
        fetch('/api/athlete_pool?person_id=' + encodeURIComponent(pid))
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (d) {
                if (d && d.pool && poolSel) {
                    var has = Array.prototype.some.call(poolSel.options,
                        function (o) { return o.value === d.pool; });
                    if (has) poolSel.value = d.pool;
                }
                if (then) then();
            });
    }

    function paintScaleHint() {
        var el = document.getElementById('rating-scale-hint');
        if (!el) return;
        el.textContent = hsMode() ? '(HS-equivalent scale)'
                                  : '(own-pool scale: ' + (poolSel ? poolSel.value : '') + ')';
    }
    paintScaleHint();
    if (poolSel) poolSel.addEventListener('change', paintScaleHint);
    document.addEventListener('rc-scale-change', function () {
        paintScaleHint();
        paintBaseRating();
        // a typed rating means something else on the other scale: convert again
        if (typeSel && typeSel.value === 'rating') convert();
    });

    var lastBase = null;
    function paintBaseRating() {
        if (!lastBase) return;
        var v = hsMode() && lastBase.hs != null ? lastBase.hs : lastBase.pool;
        showNorm('Speed rating: ' + (v != null ? v : ' - ') +
                 (hsMode() ? ' (HS-equivalent)' : ' (own pool)') + lastBase.tail);
    }

    function buildSource() {
        var t = typeSel.value;
        var src = { type: t, pool: poolSel.value, sport: sportSel.value };
        if (t === 'time') {
            src.time = parseTime(document.getElementById('in-time').value);
            src.distance = parseFloat(document.getElementById('in-distance').value);
            src.weather = readWeather('in-wx-');
            // ONLY send a difficulty when a course was actually chosen.
            // Sending 0 for a blank course asserts "this venue is exactly
            // average", which the API reads as a deliberate choice -- so the
            // source resolved neutral while the targets resolved the sport
            // default, and 541s/3200m came back as 8:51.87 instead of 541s.
            if (chosenInCourse.course) {
                src.course = chosenInCourse.course;
                src.difficulty = chosenInCourse.difficulty;
            }
        } else if (t === 'rating') {
            src.rating = parseFloat(document.getElementById('in-rating').value);
            // the scale the number was typed on (issue 49); the API converts
            // an HS-equivalent to the pool's own scale before it converts
            // anything else
            src.scale = hsMode() ? 'hs' : 'pool';
        } else if (t === 'athlete') {
            src.person_id = parseInt(chosenAthlete.person_id);
        } else if (t === 'result') {
            src.result_id = parseInt(document.getElementById('in-result-select').value);
        }
        return src;
    }

    // ---- gather the current table targets from the DOM ------------------
    function xcCourses() {
        return Array.prototype.map.call(
            document.querySelectorAll('#xc-body tr'),
            function (tr) {
                return {
                    label: tr.cells[0].textContent,
                    course: tr.getAttribute('data-course'),
                    /* ★ THE ID IS THE AUTHORITATIVE PART. The server
                       re-resolves the difficulty from it against the table
                       the pipeline rebuilds; data-difficulty below is only
                       a fallback for a course with no fitted cell. */
                    canonical_id: parseInt(tr.getAttribute('data-canonical'), 10) || null,
                    // null, not 0, when the course has no fitted cell: the
                    // API defaults an ABSENT difficulty to the sport's typical
                    // venue, but honours an explicit 0 as "exactly average".
                    difficulty: isNaN(parseFloat(tr.getAttribute('data-difficulty')))
                        ? null : parseFloat(tr.getAttribute('data-difficulty')),
                    distance: parseFloat(tr.getAttribute('data-distance')) || 5000
                };
            });
    }

    function tfDistances() {
        return Array.prototype.map.call(
            document.querySelectorAll('#tf-body tr'),
            function (tr) {
                return {
                    label: tr.cells[0].textContent,
                    distance: parseFloat(tr.getAttribute('data-distance'))
                };
            });
    }

    // ---- call the API and fill the cells --------------------------------
    function convert() {
        var payload = {
            source: buildSource(),
            xc_courses: xcCourses(),
            tf_distances: tfDistances()
        };

        fetch('/api/convert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) { render(data); })
        .catch(function () { showNorm('Conversion failed.'); });
    }

    function showNorm(msg) {
        document.getElementById('norm-display').textContent = msg;
    }

    // ★ ARRIVING FROM AN ATHLETE PAGE. The link carries a person_id; select
    //   the athlete source, name them, and convert -- so the reader lands on
    //   the answer rather than on an empty form.
    (function prefillFromLink() {
        var host = document.querySelector('.conv[data-prefill-athlete]');
        var pid = host && host.getAttribute('data-prefill-athlete');
        if (!pid) return;
        fetch('/search/api?q=&person_id=' + encodeURIComponent(pid))
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (rows) {
                var name = (rows && rows[0] && (rows[0].name || rows[0].label))
                           || ('athlete ' + pid);
                if (typeSel) { typeSel.value = 'athlete'; paintFields(); }
                chosenAthlete = { person_id: Number(pid), name: name };
                var box = document.getElementById('athlete-chosen');
                if (box) box.textContent = name;
                var inp = document.getElementById('in-athlete');
                if (inp) inp.value = name;
                var pre = host.getAttribute('data-prefill-pool');
                if (pre && poolSel && Array.prototype.some.call(poolSel.options,
                        function (o) { return o.value === pre; })) {
                    poolSel.value = pre;
                    paintScaleHint();
                    convert();
                } else {
                    adoptAthletePool(pid, convert);
                }
            });
    })();

    function render(data) {
        if (data.error || data.normalized_time == null) {
            showNorm(data.error ? ('Error: ' + data.error) : 'Could not resolve source.');
            return;
        }
        lastBase = { pool: data.base_rating, hs: data.base_rating_hs,
                     tail: '  ·  normalized 5K: ' + fmt(data.normalized_time) };
        paintBaseRating();
        fill('#xc-body', data.xc);
        fill('#tf-body', data.tf);
        paintPaces(data);
    }

    // ★ EVERY ROW SAYS WHERE ITS NUMBER CAME FROM. One is the athlete's own
    //   two races with no constant in it and carries a badge; the rest name
    //   the single published relationship applied to it. Five paces of
    //   visibly different confidence drawn as five identical rows would be
    //   claiming something the work does not support.
    function paintPaces(data) {
        var panel = document.getElementById('paces-panel');
        var wrap = document.getElementById('paces-tbl-wrap');
        var body = document.getElementById('paces-body');
        var src = document.getElementById('paces-src');
        var foot = document.getElementById('paces-foot');
        var vd = document.getElementById('paces-vdot');
        if (!panel) return;
        var rows = data.paces || [];
        var from = data.paces_from;
        // VO2max needs one time, so it survives a source that cannot give
        // paces; the athlete's own is preferred because it is read off that
        // athlete's races rather than off this one typed result.
        var v = (from && from.vdot) || data.vdot;
        if (!rows.length && !(v && v.value) && !data.paces_note) {
            panel.hidden = true;
            return;
        }
        panel.hidden = false;

        // ★ NO ROWS MEANS NO TABLE, not an empty one. A typed time cannot
        //   produce critical speed, and a header row over nothing reads as a
        //   failure rather than as a precondition.
        if (wrap) wrap.hidden = !rows.length;
        body.innerHTML = rows.map(function (p) {
            var cls = p.source === 'derived' ? ' class="derived"' : '';
            return '<tr' + cls + '><td class="zone">' + esc(p.label) +
                   '</td><td class="pace">' + esc(p.per_mile) +
                   '</td><td class="pace km">' + esc(p.per_km) +
                   '</td><td class="basis">' + esc(p.basis) + '</td></tr>';
        }).join('');

        src.textContent = from && from.n_races
            ? from.year + ' season \u00b7 ' + from.n_races + ' races' : '';

        // ★ VO2MAX ON ITS OWN LINE. It is a headline number the page was asked
        //   for, not a caveat; buried at the end of the footnote it read as
        //   "Estimated ... estimated ...", three hedges in one sentence.
        if (vd) {
            vd.hidden = !(v && v.value);
            if (v && v.value)
                vd.innerHTML = 'VO<sub>2</sub>max <strong>' +
                    esc(v.value) + '</strong> <span class="paces-vdot-note">' +
                    esc(v.note) + '</span>';
        }

        var bits = [];
        // ! KEYED ON A FITTED ROW, NOT ON HAVING ANY ROWS. The projected
        //   ladder has rows and no critical speed in it, so claiming one was
        //   fitted would be describing a row that is not on the page.
        var fitted = rows.some(function (p) { return p.source === 'derived'; });
        if (fitted) {
            bits.push('Critical speed is fitted from this athlete\'s own ' +
                      'races. Every other row applies a published ' +
                      'relationship to it.');
            if (from && from.dprime)
                bits.push('D\u2032 ' + from.dprime + ' m - the distance ' +
                          'this athlete can cover above critical speed before ' +
                          'slowing.');
        } else if (from && from.reason) {
            bits.push('Not available for ' + from.year + ': ' + from.reason + '.');
        }
        if (data.paces_note) bits.push(data.paces_note);
        foot.textContent = bits.join(' ');
    }

    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
        });
    }

    // match returned cells to rows by order (API preserves target order)
    function fill(bodySel, cells) {
        var rows = document.querySelectorAll(bodySel + ' tr');
        cells.forEach(function (c, i) {
            if (rows[i]) rows[i].querySelector('.cell-time').textContent =
                c.time != null ? fmt(c.time) : ' - ';
        });
    }

    // seconds -> m:ss.xx, or h:mm:ss.xx past an hour. Mirrors what parseTime
    // accepts, so a converted time can be pasted straight back in as a source.
    function fmt(sec) {
        var h = Math.floor(sec / 3600);
        var m = Math.floor((sec - h * 3600) / 60);
        var s = (sec - h * 3600 - m * 60).toFixed(2);
        if (parseFloat(s) < 10) s = '0' + s;
        if (h > 0) return h + ':' + (m < 10 ? '0' + m : m) + ':' + s;
        return m + ':' + s;
    }

    typeahead(
        document.getElementById('add-course'),
        document.getElementById('course-drop'),
        '/api/course_search?q=',
        function (c) {
            return c.distances.slice(0, 4).map(function (d) {
                /* per-distance difficulty: course_difficulties is keyed on
                   (canonical_id, distance_m), so a course fitted at 4.0k and
                   5.0k has two, and showing one for both was wrong even
                   before the stale-table bug. */
                var dd = (d.difficulty != null ? d.difficulty : c.difficulty);
                return '<div class="drop-item" data-name="' + esc(c.name) +
                       '" data-diff="' + dd +
                       '" data-cid="' + (c.canonical_id == null ? '' : c.canonical_id) +
                       '" data-dist="' + d.distance + '">' +
                       esc(c.name) +
                       ' <span class="drop-meta">' + (d.distance/1000).toFixed(2) +
                       'k · ' + d.n + ' races · diff ' +
                       (dd != null ? dd.toFixed(3) : '?') +
                       '</span></div>';
            }).join('');
        },
        function (item) {
            addCourseRow(item.getAttribute('data-name'),
                         parseFloat(item.getAttribute('data-diff')) || 0,
                         parseFloat(item.getAttribute('data-dist')) || 5000,
                         item.getAttribute('data-cid'));
            document.getElementById('add-course').value = '';
        }
    );

    function addCourseRow(name, diff, dist, cid) {
        var tr = document.createElement('tr');
        tr.setAttribute('data-course', name);
        tr.setAttribute('data-canonical', cid == null ? '' : cid);
        tr.setAttribute('data-difficulty', diff);
        tr.setAttribute('data-distance', dist);
        tr.innerHTML =
            '<td>' + esc(name) + '</td>' +
            '<td class="cell-dist">' + (dist/1000).toFixed(2) + 'k</td>' +
            '<td class="cell-time"> - </td>';
        document.getElementById('xc-body').appendChild(tr);
        convert();
    }


    // reusable typeahead: wires an input + dropdown to a fetch, calls onPick(item)
    function typeahead(input, drop, url, renderItem, onPick) {
        var timer = null;
        var seq = 0;                    // sequence guard for late responses

        /* ⚠ AND A `closed` FLAG BESIDE THE SEQUENCE GUARD. seq drops a
             response a newer keystroke superseded; it says nothing about a
             reader who has LEFT the box, and this picker had no blur handler
             at all -- so a slow response painted a menu over the page
             seconds after they moved on. */
        var closed = false;
        input.addEventListener('focus', function () { closed = false; });
        input.addEventListener('blur', function () {
            closed = true;
            clearTimeout(timer);
            setTimeout(function () { drop.innerHTML = ''; }, 150);
        });
        input.addEventListener('input', function () {
            var q = input.value.trim();
            closed = false;
            clearTimeout(timer);
            if (q.length < 2) { drop.innerHTML = ''; return; }
            var mySeq = ++seq;          // this request's id
            timer = setTimeout(function () {
                fetch(url + encodeURIComponent(q))
                    .then(function (r) { return r.json(); })
                    .then(function (rows) {
                        // newer input, or the reader has left -> drop
                        if (mySeq !== seq || closed) return;
                        drop.innerHTML = rows.map(renderItem).join('');
                    });
            }, 150);
        });

        drop.addEventListener('click', function (e) {
            var item = e.target.closest('.drop-item');
            if (!item) return;
            seq++;                       // invalidate any in-flight fetch
            closed = true;
            clearTimeout(timer);
            onPick(item);
            drop.innerHTML = '';
        });
    }

    var chosenAthlete = { person_id: null };

    typeahead(
        document.getElementById('in-athlete'),
        document.getElementById('athlete-drop'),
        '/search/api?kind=athlete&q=',
        function (a) {
            // /search/api returns {label, sublabel, link, ...}; person_id is in link
            var pid = (a.link || '').split('/').pop();
            return '<div class="drop-item" data-pid="' + pid + '" data-name="' +
                   esc(a.label) + '">' + esc(a.label) +
                   ' <span class="drop-meta">' + esc(a.sublabel || '') + '</span></div>';
        },
        function (item) {
            chosenAthlete.person_id = item.getAttribute('data-pid');
            document.getElementById('in-athlete').value = item.getAttribute('data-name');
            document.getElementById('athlete-chosen').textContent =
                'Selected: ' + item.getAttribute('data-name');
            adoptAthletePool(chosenAthlete.person_id, paintScaleHint);
            // if result source, load their races into the select
            if (typeSel.value === 'result') loadRaces(chosenAthlete.person_id);
        }
    );

    function loadRaces(pid) {
        var sel = document.getElementById('in-result-select');
        fetch('/api/athlete_results?person_id=' + pid + '&sport=' + sportSel.value)
            .then(function (r) { return r.json(); })
            .then(function (races) {
                sel.innerHTML = races.map(function (r) {
                    var t = r.time_seconds ? fmt(r.time_seconds) : '?';
                    return '<option value="' + r.result_id + '">' +
                           r.date + ' · ' + esc(r.meet_name || r.event_short || '') +
                           (r.event_short ? ' · ' + r.event_short : '') +
                           ' · ' + t + '</option>';
                }).join('');
            });
    }

    // ---- DISTANCE typeahead: standard events, client-side (instant) --------
    var TF_EVENTS = [
        {label:'400m',m:400},{label:'600m',m:600},{label:'800m',m:800},
        {label:'1000m',m:1000},{label:'1200m',m:1200},{label:'1500m',m:1500},
        {label:'Mile',m:1609.34},{label:'1600m',m:1600},{label:'3000m',m:3000},
        {label:'3200m',m:3200},{label:'2 Mile',m:3218.69},{label:'5000m',m:5000},
        {label:'10,000m',m:10000}
    ];
    var distInput = document.getElementById('add-distance');
    var distDrop  = document.getElementById('distance-drop');

    distInput.addEventListener('input', function () {
        var q = distInput.value.trim().toLowerCase();
        if (!q) { distDrop.innerHTML = ''; return; }
        var hits = TF_EVENTS.filter(function (e) {
            return e.label.toLowerCase().indexOf(q) >= 0 || String(e.m).indexOf(q) >= 0;
        });
        distDrop.innerHTML = hits.map(function (e) {
            return '<div class="drop-item" data-m="' + e.m + '" data-label="' +
                   e.label + '">' + e.label + '</div>';
        }).join('');
    });

    distDrop.addEventListener('click', function (e) {
        var item = e.target.closest('.drop-item');
        if (!item) return;
        var tr = document.createElement('tr');
        tr.setAttribute('data-distance', item.getAttribute('data-m'));
        tr.innerHTML = '<td>' + item.getAttribute('data-label') +
                       '</td><td class="cell-time"> - </td>';
        document.getElementById('tf-body').appendChild(tr);
        distDrop.innerHTML = '';
        distInput.value = '';
        convert();
    });

    sportSel.addEventListener('change', function () {
        if (typeSel.value === 'result' && chosenAthlete.person_id)
            loadRaces(chosenAthlete.person_id);
    });

    function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

    btn.addEventListener('click', convert);
})();