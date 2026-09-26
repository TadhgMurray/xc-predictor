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

    btn.addEventListener('click', function () {
        var q = btn.dataset.q, kind = btn.dataset.kind,
            year = btn.dataset.year, offset = btn.dataset.offset;
        var url = '/search?format=json&q=' + encodeURIComponent(q) +
                  '&kind=' + encodeURIComponent(kind) + '&offset=' + offset +
                  (year ? '&year=' + encodeURIComponent(year) : '');

        fetch(url).then(function (r) { return r.json(); }).then(function (rows) {
            var body = document.getElementById('results-body');
            rows.forEach(function (r) { body.appendChild(buildRow(r, kind)); });
            btn.dataset.offset = parseInt(btn.dataset.offset) + rows.length;
            if (rows.length < 30) btn.style.display = 'none';
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
    function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

    // Infinite scroll: auto-click Load More when it scrolls into view.
    // IntersectionObserver fires once when the button enters the viewport,
    // instead of on every scroll pixel (which would need throttling).
    if (btn && 'IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function (entries) {
            if (entries[0].isIntersecting && btn.style.display !== 'none') {
                btn.click();          // reuse the existing fetch-and-append logic
            }
        }, { rootMargin: '200px' });  // fire 200px BEFORE it's visible, feels seamless

        observer.observe(btn);
    }
});