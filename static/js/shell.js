/*
 * Behaviour of the application shell and the sign-in screens. No business rules live here (roadmap Rule 3): it opens and
 * closes the menus, filters the "jump to" list and shows or hides a password.
 */
(function () {
  var doc = document;
  var root = doc.documentElement;
  var body = doc.body;

  function store(key, value) {
    try { if (value === undefined) { return window.localStorage.getItem(key); } window.localStorage.setItem(key, value); } catch (e) { /* private window: the choice is simply not remembered */ }
    return null;
  }

  // --- The menus: a section opens a panel; on a phone the same markup is a sheet ---------------------------------------
  var items = Array.prototype.slice.call(doc.querySelectorAll(".topnav-item"));
  var wide = window.matchMedia ? window.matchMedia("(min-width: 992px)") : { matches: true };
  var hoverTimer = null;
  function setOpen(item, open) {
    var button = item.querySelector("[data-mega]");
    if (!button) { return; }
    item.classList.toggle("is-open", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
  function closeMenus(except) { items.forEach(function (item) { if (item !== except) { setOpen(item, false); } }); }
  items.forEach(function (item) {
    var button = item.querySelector("[data-mega]");
    if (!button) { return; }
    button.addEventListener("click", function () {
      if (!wide.matches) { return; }
      if (Date.now() - (item.hoverOpenedAt || 0) < 500) { return; } // the pointer arrived and the menu opened on hover: this click is the same intent
      var open = !item.classList.contains("is-open");
      closeMenus(item);
      setOpen(item, open);
    });
    item.addEventListener("mouseenter", function () {
      if (!wide.matches) { return; }
      window.clearTimeout(hoverTimer);
      if (doc.querySelector(".topnav-item.is-open")) { closeMenus(item); setOpen(item, true); item.hoverOpenedAt = Date.now(); }
    });
    item.addEventListener("mouseleave", function () {
      if (!wide.matches) { return; }
      hoverTimer = window.setTimeout(function () { setOpen(item, false); }, 220);
    });
  });
  doc.addEventListener("click", function (event) { if (!event.target.closest(".topnav-item")) { closeMenus(null); } });

  var opener = doc.querySelector("[data-menu-open]");
  function openSheet() { body.classList.add("menu-open"); if (opener) { opener.setAttribute("aria-expanded", "true"); } var first = doc.querySelector("#topnav a"); if (first) { first.focus(); } }
  function closeSheet() { if (!body.classList.contains("menu-open")) { return; } body.classList.remove("menu-open"); if (opener) { opener.setAttribute("aria-expanded", "false"); opener.focus(); } }
  if (opener) { opener.addEventListener("click", openSheet); }
  doc.querySelectorAll("[data-menu-close]").forEach(function (el) { el.addEventListener("click", closeSheet); });
  doc.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") { return; }
    var open = doc.querySelector(".topnav-item.is-open");
    closeMenus(null);
    if (open) { var trigger = open.querySelector("[data-mega]"); if (trigger) { trigger.focus(); } }
    closeSheet();
  });
  var topnav = doc.getElementById("topnav");
  if (topnav) { topnav.addEventListener("click", function (event) { if (event.target.closest("a") && body.classList.contains("menu-open")) { body.classList.remove("menu-open"); } }); }

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
      try { pages = JSON.parse(doc.getElementById("quick-pages").textContent); } catch (e) { pages = []; }
      pages = pages.map(function (p) { return { label: p.label, parent: p.group, href: p.url }; });
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

  // --- Light, dark or follow the system ---------------------------------------------------------------------------------
  function syncTheme() {
    var current = window.wpTheme ? window.wpTheme.get() : "system";
    doc.querySelectorAll("[data-theme-choice]").forEach(function (button) {
      button.setAttribute("aria-pressed", button.getAttribute("data-theme-choice") === current ? "true" : "false");
    });
  }
  doc.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-choice]");
    if (!button || !window.wpTheme) { return; }
    window.wpTheme.set(button.getAttribute("data-theme-choice"));
    syncTheme();
  });
  syncTheme();

  // --- A dashboard panel that could not load says so and can try again ---------------------------------------------------
  function widgetFailed(event) {
    var target = event.detail && event.detail.target ? event.detail.target : event.target;
    if (!target || !target.hasAttribute || !target.hasAttribute("data-widget")) { return; }
    var body = target.querySelector("[data-widget-body]");
    if (!body) { return; }
    body.innerHTML = "";
    var block = doc.createElement("div"); block.className = "state-block is-error"; block.setAttribute("role", "alert");
    var icon = doc.createElement("i"); icon.className = "bi bi-exclamation-triangle"; icon.setAttribute("aria-hidden", "true");
    var title = doc.createElement("strong"); title.textContent = "This panel could not be loaded";
    var text = doc.createElement("p"); text.textContent = "Nothing was changed. Check your connection and try again.";
    var retry = doc.createElement("button"); retry.type = "button"; retry.className = "btn btn-sm btn-outline-secondary"; retry.textContent = "Try again";
    retry.addEventListener("click", function () {
      body.innerHTML = "<span class='skeleton'></span><span class='skeleton'></span><span class='skeleton'></span>";
      if (window.htmx) { window.htmx.ajax("GET", target.getAttribute("hx-get"), { target: target, swap: "outerHTML" }); }
    });
    block.appendChild(icon); block.appendChild(title); block.appendChild(text); block.appendChild(retry); body.appendChild(block);
  }
  doc.body.addEventListener("htmx:responseError", widgetFailed);
  doc.body.addEventListener("htmx:sendError", widgetFailed);

  // --- Forms: the buttons at the end of a form become one footer bar (Cancel on the left, the main action on the right) ------
  function decorateForms(scope) {
    (scope || doc).querySelectorAll("main form").forEach(function (form) {
      if (form.dataset.actions || form.matches(".filters, .row-form, .inline-form, .bulk-bar, .row, [data-plain]") || (form.getAttribute("method") || "get").toLowerCase() === "get" || form.closest("table, td, th, li, .dropdown-menu, .modal")) { return; }
      // only a real form (something to fill in) gets a footer bar; a lone "Suspend" or "Delete" button is left as it is
      if (form.querySelector("table") || !form.querySelector(".field, .form-control, .form-select, textarea, input[type=file]")) { return; }
      var kids = Array.prototype.slice.call(form.children);
      var tail = [];
      for (var i = kids.length - 1; i >= 0; i--) {
        var kid = kids[i];
        var control = kid.matches("button, .btn, a") || (kid.matches("div") && kid.querySelector("button, [type=submit]") && !kid.querySelector("input:not([type=hidden]), select, textarea"));
        if (!control) { break; }
        tail.unshift(kid);
      }
      var hasSubmit = tail.some(function (el) { return el.matches("button[type=submit], button:not([type]), input[type=submit]") || !!el.querySelector("button[type=submit], button:not([type])"); });
      if (!tail.length || !hasSubmit) { return; }
      var bar = doc.createElement("div");
      bar.className = "form-actions";
      form.insertBefore(bar, tail[0]);
      tail.forEach(function (el) { bar.appendChild(el); });
      form.dataset.actions = "1";
    });
  }
  decorateForms();
  doc.body.addEventListener("htmx:afterSwap", function (event) { decorateForms(event.target); });

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
