/*
 * staged.js -- the two-stage table reveal.
 *
 * A table.staged renders every row, hides the ones past the fold with
 * .row-hidden, and ends with a .more-row button. First press reveals
 * data-step more rows (a sample); second press reveals everything -- after
 * one press the reader has said they want the list. Lived inline in
 * school.html until the home page needed it too.
 */

"use strict";

document.querySelectorAll("table.staged").forEach(function (table) {
    var btn = table.querySelector(".more-btn");
    if (!btn) return;
    var step = parseInt(table.dataset.step, 10) || 20;
    var pressed = 0;
    btn.addEventListener("click", function () {
        pressed += 1;
        var hidden = table.querySelectorAll("tr.row-hidden");
        var take = pressed === 1 ? Math.min(step, hidden.length) : hidden.length;
        for (var i = 0; i < take; i++) hidden[i].classList.remove("row-hidden");
        var left = table.querySelectorAll("tr.row-hidden").length;
        if (!left) {
            table.querySelector(".more-row").remove();
        } else {
            btn.querySelector(".more-left").textContent = "(" + left + " left)";
            btn.childNodes[0].nodeValue = "Show the remaining " + left + " ";
        }
    });
});
