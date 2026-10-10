/* rc-jump.js -- a side card's whole row jumps, not just its link.
   ★ ONE SCRIPT FOR EVERY ROW-JUMP CARD (owner, 2026-10-10): School PRs had
   it inline (owner, 2026-10-09); About's Contents and Site status's
   Sections card use the same rows, so it lives here once. Loaded at the
   end of <body>: a tr.rc-jump forwards a click anywhere in the row to the
   row's first link, and a click on the link itself goes as it is. */
document.querySelectorAll("tr.rc-jump").forEach(function (tr) {
  tr.addEventListener("click", function (e) {
    if (e.target.closest("a")) return;
    var a = tr.querySelector("a"); if (a) a.click();
  });
});
