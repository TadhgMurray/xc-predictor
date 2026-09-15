// topbar-search.js -- the live search dropdown. Loaded on every page.
(function () {
    function init() {
        var input = document.getElementById('search-input');
        var box   = document.getElementById('search-results');
        if (!input || !box) return;

        var timer = null, seq = 0, closed = false;
        /* Index of the keyboard-highlighted row, -1 for none. Reset whenever
           the list re-renders: the old index would point at a row that may
           no longer exist, or worse, a different one. */
        var active = -1;

        /* Every keyboard-reachable row, in visual order. The "See all
           results" link is included: it is the last stop of an arrow-down
           walk, same as it is the last thing the eye reaches. */
        function options() {
            return box.querySelectorAll('.sr-item, .sr-more');
        }

        function setActive(i) {
            var opts = options();
            if (!opts.length) { active = -1; return; }
            /* Wrap at both ends -- arrow-down from the last row returns to
               the first, which beats a dead stop nobody can see the reason
               for. */
            active = ((i % opts.length) + opts.length) % opts.length;
            for (var k = 0; k < opts.length; k++) {
                opts[k].classList.toggle('is-active', k === active);
            }
            /* The list scrolls (.sr-scroll); the highlight must not walk out
               of view. 'nearest' only scrolls when needed, so mouse users
               see no jump. */
            opts[active].scrollIntoView({ block: 'nearest' });
        }

        input.addEventListener('input', function () {
            var q = input.value.trim();
            clearTimeout(timer);
            if (q.length < 2) { box.innerHTML = ''; active = -1; return; }
            closed = false;             // typing re-opens what a click shut
            var mySeq = ++seq;
            timer = setTimeout(function () {
                fetch('/search/api?q=' + encodeURIComponent(q))
                    .then(function (r) { return r.json(); })
                    .then(function (rows) {
                        if (mySeq !== seq || closed) return;
                        render(rows, q);
                    });
            }, 150);
        });

        function render(rows, q) {
            active = -1;
            var more = '<a class="sr-more" href="/search?q=' +
                    encodeURIComponent(q) + '">See all results →</a>';
            if (!rows.length) { box.innerHTML = more; return; }
            var items = rows.map(function (r) {
                var sub = r.sublabel ? '<span class="sr-sub">' + esc(r.sublabel) + '</span>' : '';
                return '<a class="sr-item" href="' + r.link + '">' +
                    '<span class="sr-kind">' + r.kind + '</span>' +
                    '<span class="sr-label">' + esc(r.label) + '</span>' + sub + '</a>';
            }).join('');
            box.innerHTML = '<div class="sr-scroll">' + items + '</div>' + more;
        }

        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                if (!options().length) return;
                /* Without this the caret jumps to the end/start of the input
                   on every press, which reads as the page fighting you. */
                e.preventDefault();
                setActive(active + (e.key === 'ArrowDown' ? 1 : -1));
            } else if (e.key === 'Enter') {
                var opts = options();
                if (active >= 0 && opts[active]) {
                    /* A highlighted row wins: rows are <a>, so following the
                       highlight is just following its href. */
                    e.preventDefault();
                    window.location = opts[active].href;
                } else {
                    /* No highlight -> the full results page, as before. */
                    var q = input.value.trim();
                    if (q) window.location = '/search?q=' + encodeURIComponent(q);
                }
            } else if (e.key === 'Escape') {
                closed = true;              // and stay shut
                box.innerHTML = '';
                active = -1;
            }
        });

        /* ⚠ CLOSING IS NOT ENOUGH ON ITS OWN. Clicking away emptied the box,
             and then a response still in flight painted it again seconds
             later -- over the page, with nothing left to click away from.
             seq answered "is this superseded"; nothing answered "does anyone
             still want this". */
        document.addEventListener('click', function (e) {
            if (!input.contains(e.target) && !box.contains(e.target)) {
                closed = true;
                box.innerHTML = '';
            }
        });

        function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

/* ---- the account (283) ------------------------------------------------
   Every page is served signed-out and cached; this asks /api/me (never
   cached) and swaps the topbar link, marks an athlete page that is yours,
   and hands the answer to any page script listening (xcp:me). */
(function () {
  /* ! AFTER THE DOM, LIKE init() ABOVE: this file is loaded before <body>,
       so the slot does not exist yet when the script runs. */
  function whoami() {
    var slot = document.getElementById('topbar-account');
    if (!slot) return;
    function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
    fetch('/api/me', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (me) {
            window.xcpMe = me || { signed_in: false };
            if (me && me.signed_in) {
                /* the picture, or the first letter of the name, as a round
                   button: it reads as "you", not as one more menu item */
                var initial = (me.label || '?').trim().charAt(0).toUpperCase();
                slot.innerHTML = '<a href="/account" class="tb-me' + (me.photo ? ' has-photo' : '') + '" title="' +
                    esc(me.label) + ' · settings">' +
                    (me.photo ? '<img src="' + esc(me.photo) + '" alt="" width="34" height="34">' : esc(initial)) + '</a>';
                var mine = document.querySelector('[data-person-id]');
                if (mine && (me.athletes || []).some(function (a) { return String(a.person_id) === mine.dataset.personId; })) {
                    mine.insertAdjacentHTML('beforeend', ' · <a href="/account" class="is-account">Your page</a>');
                    var av = document.getElementById('ath-avatar');
                    if (av && av.classList.contains('ath-avatar-empty') && !me.photo) {
                        av.innerHTML = '<a href="/account#photo" class="ath-avatar-add" title="Add your picture">+<span>photo</span></a>';
                        av.hidden = false;
                    }
                }
            }
            document.dispatchEvent(new CustomEvent('xcp:me', { detail: window.xcpMe }));
        })
        .catch(function () { window.xcpMe = { signed_in: false }; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', whoami);
  else whoami();
})();
