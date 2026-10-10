document.addEventListener('click', function (e) {
    // the name in each row is a real link; a click on it (and a ctrl- or
    // middle-click anywhere, which means "new tab") is the browser's own
    if (e.target.closest('a') || e.ctrlKey || e.metaKey || e.shiftKey ||
        e.button !== 0) return;
    var tr = e.target.closest('.result-row');
    if (tr && tr.dataset.href) window.location = tr.dataset.href;
});

document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('load-more');
    if (!btn) return;

    /* ★ ONE PAGE AT A TIME, AND SAY WHEN IT FAILS (sweep 2026-10-10, B19).
         The scroll observer and a click could both fire before the first
         answer landed, and both asked for the same offset -- the page was
         appended twice. A failure was silent: the button just stayed. Now a
         request in flight blocks another, a row already on the page is not
         added again, and a failure says so and offers the same page again. */
    var busy = false;
    var status = document.getElementById('load-more-status');
    function say(msg) { if (status) status.textContent = msg; }

    btn.addEventListener('click', function () {
        if (busy) return;
        busy = true;
        btn.disabled = true;
        btn.textContent = 'Loading\u2026';
        say('');
        var q = btn.dataset.q, kind = btn.dataset.kind,
            year = btn.dataset.year, offset = btn.dataset.offset;
        var url = '/search?format=json&q=' + encodeURIComponent(q) +
                  '&kind=' + encodeURIComponent(kind) + '&offset=' + offset +
                  (year ? '&year=' + encodeURIComponent(year) : '');

        fetch(url).then(function (r) {
            var type = r.headers.get('content-type') || '';
            if (!r.ok || type.indexOf('json') < 0) throw new Error(r.status);
            return r.json();
        }).then(function (rows) {
            var body = document.getElementById('results-body');
            var seen = {};
            body.querySelectorAll('.result-row').forEach(function (tr) { seen[tr.dataset.href] = 1; });
            rows.forEach(function (r) {
                if (r.link && seen[r.link]) return;
                seen[r.link] = 1;
                body.appendChild(buildRow(r, kind));
            });
            btn.dataset.offset = parseInt(btn.dataset.offset, 10) + rows.length;
            btn.textContent = 'Load more';
            if (rows.length < 30) btn.style.display = 'none';
        }).catch(function () {
            btn.textContent = 'Retry';
            say('More results could not load. Try again in a moment.');
        }).then(function () {
            busy = false;
            btn.disabled = false;
        });
    });

    function buildRow(r, kind) {
        var tr = document.createElement('tr');
        tr.className = 'result-row';
        tr.dataset.href = r.link;
        var cells;
        if (kind === 'athlete')      cells = [r.label, r.sublabel, r.sort_year, r.sort_count];
        else if (kind === 'meet')    cells = [r.label, r.sort_year, r.sort_count];
        else if (kind === 'course')  cells = [r.label, r.sort_count];
        else if (kind === 'venue')   cells = [r.label, r.sublabel, r.sort_count];
        else if (kind === 'school')  cells = [r.label, r.sort_count];
        else                         cells = [r.label, r.kind, r.sublabel];
        tr.innerHTML = cells.map(function (c, i) {
            var text = esc(c == null || c === 0 ? ' - ' : c);
            // the first cell is the name: a real link, as in search.html
            return '<td>' + (i === 0 && r.link
                ? '<a class="sr-link" href="' + esc(r.link) + '">' + text + '</a>'
                : text) + '</td>';
        }).join('');
        // the school crest (305), into the first cell only, and only when
        // the server said there is one -- the page's own rows are built the
        // same way in search.html's result_row macro
        if (r.crest && tr.cells.length) {
            var img = document.createElement('img');
            img.className = 'school-mark';
            img.src = r.crest;
            img.alt = '';
            img.width = img.height = 18;
            img.loading = 'lazy';
            tr.cells[0].insertBefore(img, tr.cells[0].firstChild);
        }
        return tr;
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // Infinite scroll: auto-click Load More when it scrolls into view.
    // IntersectionObserver fires once when the button enters the viewport,
    // instead of on every scroll pixel (which would need throttling).
    if (btn && 'IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function (entries) {
            // ! not after a failure: a Retry is the reader's to press, or a
            //   button left in view would ask again on every scroll
            if (entries[0].isIntersecting && btn.style.display !== 'none' &&
                !busy && btn.textContent !== 'Retry') {
                btn.click();          // reuse the existing fetch-and-append logic
            }
        }, { rootMargin: '200px' });  // fire 200px BEFORE it's visible, feels seamless

        observer.observe(btn);
    }
});