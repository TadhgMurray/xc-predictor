/*
 * staged.js -- the staged table reveal.
 *
 * A table.staged renders every row, hides the ones past the fold with
 * .row-hidden, and ends with a .more-row footer. First press reveals
 * data-step more (a sample); the second reveals everything -- after one press
 * the reader has said they want the list.
 *
 * ★ AND IT GOES BACK NOW (issue #57, point 3). Every "more" on the site was
 *   one-way: a reader who expanded a 300-row table to see one name had no way
 *   to fold it again and had to reload the page. The rankings pager could go
 *   backwards and nothing else could. Expanding and collapsing are the same
 *   control, so they are the same button.
 *
 * ! THE FOLD IS REMEMBERED AS A COUNT, NOT AS ROWS. The template owns where
 *   the fold is (it writes .row-hidden); this reads how many rows it left
 *   visible and collapses back to that many BY POSITION. It used to re-hide
 *   the exact row elements it had revealed -- but scale-view.js re-orders a
 *   table's rows when the rating scale flips, so a row revealed at #20 can
 *   be #3 by the time "Show less" is pressed, and hiding it would punch a
 *   hole in the top of the table.
 */

"use strict";

document.querySelectorAll("table.staged").forEach(function (table) {
    var btn = table.querySelector(".more-btn");
    if (!btn) return;

    var step = parseInt(table.dataset.step, 10) || 20;
    var pressed = 0;
    // The fold, as the template drew it: how many rows start visible.
    var fold = table.querySelectorAll("tbody tr:not(.row-hidden):not(.more-row)").length;

    function bodyRows() {
        return Array.prototype.filter.call(
            table.querySelectorAll("tbody tr"),
            function (tr) { return !tr.classList.contains("more-row"); });
    }

    // The label is rebuilt from scratch every time rather than patched in
    // place. The original version wrote through btn.childNodes[0].nodeValue,
    // which is the bare text node beside the <span> -- it works only while
    // the button's markup is exactly "text + span", and says nothing about
    // that requirement.
    function setLabel(text, left) {
        btn.textContent = text;
        if (left > 0) {
            var span = document.createElement("span");
            span.className = "more-left";
            span.textContent = "(" + left + " left)";
            btn.appendChild(document.createTextNode(" "));
            btn.appendChild(span);
        }
    }

    function collapse() {
        bodyRows().forEach(function (tr, i) {
            tr.classList.toggle("row-hidden", i >= fold);
        });
        pressed = 0;
        setLabel("Show " + step + " more",
                 table.querySelectorAll("tr.row-hidden").length);
    }

    btn.addEventListener("click", function () {
        if (btn.dataset.act === "collapse") { collapse(); return; }

        pressed += 1;
        var hidden = table.querySelectorAll("tr.row-hidden");
        var take = pressed === 1 ? Math.min(step, hidden.length) : hidden.length;
        for (var i = 0; i < take; i++) {
            hidden[i].classList.remove("row-hidden");
        }

        var left = table.querySelectorAll("tr.row-hidden").length;
        if (!left) {
            /* ⚠ THE FOOTER USED TO BE REMOVED HERE, which is what made this
               one-way: with the row gone there was nothing left to press.
               It becomes the collapse control instead. */
            btn.dataset.act = "collapse";
            setLabel("Show less", 0);
        } else {
            setLabel("Show the remaining " + left, left);
        }
    });
});
