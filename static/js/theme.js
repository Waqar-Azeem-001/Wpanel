/* Runs before the page paints: follow the visitor's light/dark preference and the collapsed/expanded rail they chose (no flash of the wrong one). */
(function () {
  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function apply() { document.documentElement.setAttribute("data-bs-theme", query && query.matches ? "dark" : "light"); }
  apply();
  document.documentElement.classList.add("js");
  if (query && query.addEventListener) { query.addEventListener("change", apply); }
  try { document.documentElement.setAttribute("data-rail", window.localStorage.getItem("wp.rail") === "collapsed" ? "collapsed" : "expanded"); } catch (e) { /* storage blocked: expanded */ }
})();
