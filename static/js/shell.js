/*
 * Behaviour of the application shell and the sign-in screens. No business rules live here (roadmap Rule 3): it opens and
 * closes things, remembers whether the rail is collapsed, filters the "jump to" list and shows or hides a password.
 */
(function () {
  var doc = document;
  var root = doc.documentElement;
  var body = doc.body;

  function store(key, value) {
    try { if (value === undefined) { return window.localStorage.getItem(key); } window.localStorage.setItem(key, value); } catch (e) { /* private window: the choice is simply not remembered */ }
    return null;
  }

  // --- The rail: collapse (wide screens), drawer (phones), groups -------------------------------------------------
  var rail = doc.getElementById("rail");
  if (rail) {
    var collapse = rail.querySelector("[data-rail-collapse]");
    function syncCollapse() {
      var collapsed = root.getAttribute("data-rail") === "collapsed";
      if (collapse) { collapse.setAttribute("aria-pressed", collapsed ? "true" : "false"); }
    }
    syncCollapse();
    if (collapse) {
      collapse.addEventListener("click", function () {
        var next = root.getAttribute("data-rail") === "collapsed" ? "expanded" : "collapsed";
        root.setAttribute("data-rail", next);
        store("wp.rail", next);
        syncCollapse();
      });
    }

    rail.querySelectorAll(".rail-toggle").forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.closest(".rail-group");
        var open = group.classList.toggle("is-open");
        button.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });

    var opener = doc.querySelector("[data-rail-open]");
    function openDrawer() { body.classList.add("rail-open"); if (opener) { opener.setAttribute("aria-expanded", "true"); } var first = rail.querySelector("a"); if (first) { first.focus(); } }
    function closeDrawer() { if (!body.classList.contains("rail-open")) { return; } body.classList.remove("rail-open"); if (opener) { opener.setAttribute("aria-expanded", "false"); opener.focus(); } }
    if (opener) { opener.addEventListener("click", openDrawer); }
    doc.querySelectorAll("[data-rail-close]").forEach(function (el) { el.addEventListener("click", closeDrawer); });
    doc.addEventListener("keydown", function (event) { if (event.key === "Escape") { closeDrawer(); } });
    rail.addEventListener("click", function (event) { if (event.target.closest("a") && body.classList.contains("rail-open")) { body.classList.remove("rail-open"); } });
  }

  // --- Search or jump to ---------------------------------------------------------------------------------------------
  var dialog = doc.getElementById("quick-nav");
  if (dialog && window.bootstrap) {
    var modal = new window.bootstrap.Modal(dialog);
    var input = doc.getElementById("quick-q");
    var list = doc.getElementById("quick-list");
    var searchUrl = dialog.getAttribute("data-search-url");
    var pages = [];
    var shown = [];
    var selected = 0;

    function collectPages() {
      pages = [];
      var seen = {};
      doc.querySelectorAll("#rail .rail-link, #rail .rail-sublink").forEach(function (link) {
        var href = link.getAttribute("href");
        var group = link.closest(".rail-group");
        var label = link.textContent.trim();
        var parent = group && !link.classList.contains("rail-link") ? group.querySelector(".rail-link").textContent.trim() : "";
        if (!href || seen[href + label]) { return; }
        seen[href + label] = true;
        if (link.classList.contains("rail-link") && group) { return; } // a group's own link is its first page, listed below it
        pages.push({ label: label, parent: parent, href: href });
      });
    }

    function render() {
      var term = input.value.trim().toLowerCase();
      shown = pages.filter(function (p) { return !term || (p.parent + " " + p.label).toLowerCase().indexOf(term) !== -1; }).slice(0, 8);
      list.innerHTML = "";
      shown.forEach(function (p, i) {
        var li = doc.createElement("li");
        var a = doc.createElement("a");
        a.href = p.href; a.setAttribute("role", "option");
        var name = doc.createElement("span"); name.textContent = p.label; a.appendChild(name);
        if (p.parent) { var small = doc.createElement("small"); small.textContent = p.parent; a.appendChild(small); }
        if (i === selected) { a.classList.add("is-selected"); a.setAttribute("aria-selected", "true"); }
        li.appendChild(a); list.appendChild(li);
      });
      if (searchUrl && term.length >= 2) {
        var row = doc.createElement("li"); var link = doc.createElement("a");
        link.href = searchUrl + "?q=" + encodeURIComponent(input.value.trim()); link.setAttribute("role", "option");
        link.textContent = "Search everything for “" + input.value.trim() + "”";
        row.appendChild(link); list.appendChild(row);
        shown.push({ href: link.href });
      }
      if (!list.children.length) { var empty = doc.createElement("li"); empty.className = "quick-empty"; empty.textContent = "No page matches."; list.appendChild(empty); }
    }

    function open() { collectPages(); selected = 0; input.value = ""; render(); modal.show(); }
    doc.querySelectorAll("[data-quick-open]").forEach(function (button) { button.addEventListener("click", open); });
    dialog.addEventListener("shown.bs.modal", function () { input.focus(); });
    input.addEventListener("input", function () { selected = 0; render(); });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        var count = list.querySelectorAll("a").length;
        if (!count) { return; }
        selected = (selected + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
        list.querySelectorAll("a").forEach(function (a, i) { a.classList.toggle("is-selected", i === selected); });
      } else if (event.key === "Enter") {
        var target = list.querySelectorAll("a")[selected];
        if (target) { event.preventDefault(); window.location.href = target.href; }
      }
    });
    doc.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); open(); }
    });
  }

  // --- Show or hide a password ---------------------------------------------------------------------------------------
  doc.addEventListener("click", function (event) {
    var button = event.target.closest("[data-toggle-password]");
    if (!button) { return; }
    var field = button.closest(".input-wrap").querySelector("input");
    var show = field.type === "password";
    field.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", show ? "true" : "false");
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    var icon = button.querySelector(".bi");
    if (icon) { icon.className = "bi " + (show ? "bi-eye-slash" : "bi-eye"); }
  });
})();
