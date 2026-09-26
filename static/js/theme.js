/*
 * Runs before the page paints (loaded in the head): the theme (the person's choice, else the system setting) and the
 * collapsed or expanded rail, so neither flashes the wrong way. shell.js binds the buttons that change them.
 */
(function () {
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function read(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function write(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* blocked: the choice lasts until the page closes */ } }

  function choice() { var stored = read("wp.theme"); return stored === "light" || stored === "dark" ? stored : "system"; }
  function apply() {
    var pick = choice();
    root.setAttribute("data-bs-theme", pick === "system" ? (query && query.matches ? "dark" : "light") : pick);
    root.setAttribute("data-theme-choice", pick);
  }
  apply();
  root.classList.add("js");
  if (query && query.addEventListener) { query.addEventListener("change", apply); }
  window.wpTheme = { get: choice, set: function (pick) { write("wp.theme", pick); apply(); } };

  root.setAttribute("data-rail", read("wp.rail") === "collapsed" ? "collapsed" : "expanded");
})();
