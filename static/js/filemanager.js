/*
 * File Manager behaviour: the selection bar (shown while something is ticked), "select all", and filling the rename and
 * copy dialogs from the row that opened them. No file rules live here - every path is checked on the server.
 */
(function () {
  var bar = document.querySelector("[data-fm-selection]");
  var count = document.querySelector("[data-fm-count]");
  var all = document.querySelector("[data-fm-all]");

  function rows() { return Array.prototype.slice.call(document.querySelectorAll("[data-fm-row]")); }

  function refresh() {
    var ticked = rows().filter(function (box) { return box.checked; }).length;
    if (bar) { bar.hidden = ticked === 0; }
    if (count) { count.textContent = String(ticked); }
    if (all) {
      all.checked = ticked > 0 && ticked === rows().length;
      all.indeterminate = ticked > 0 && ticked < rows().length;
    }
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target.matches) { return; }
    if (target.matches("[data-fm-all]")) {
      rows().forEach(function (box) { box.checked = target.checked; });
    }
    if (target.matches("[data-fm-all], [data-fm-row]")) { refresh(); }
  });

  [["fm-rename", ""], ["fm-copy", "copy-of-"]].forEach(function (pair) {
    var dialog = document.getElementById(pair[0]);
    if (!dialog) { return; }
    var input = dialog.querySelector("[name=new_name]");
    dialog.addEventListener("show.bs.modal", function (event) {
      var opener = event.relatedTarget;
      if (!opener) { return; }
      dialog.querySelector("[name=item]").value = opener.getAttribute("data-item") || "";
      var name = opener.getAttribute("data-name") || "";
      dialog.querySelector("[data-fm-name]").textContent = name;
      input.value = pair[1] + name;
    });
    dialog.addEventListener("shown.bs.modal", function () { input.focus(); input.select(); });
  });

  // Move and permissions dialogs are filled from the row that opened them.
  [["fm-move", "data-move-start", "to_folder"], ["fm-chmod", "data-mode", "mode"]].forEach(function (trio) {
    var dialog = document.getElementById(trio[0]);
    if (!dialog) { return; }
    var field = dialog.querySelector("[name=" + trio[2] + "]");
    dialog.addEventListener("show.bs.modal", function (event) {
      var opener = event.relatedTarget;
      if (!opener) { return; }
      dialog.querySelector("[name=item]").value = opener.getAttribute("data-item") || "";
      dialog.querySelector("[data-fm-name]").textContent = opener.getAttribute("data-name") || "";
      field.value = opener.getAttribute(trio[1]) || "";
    });
    dialog.addEventListener("shown.bs.modal", function () { field.focus(); field.select(); });
  });

  refresh();
})();
