/*
 * Databases screen: keep "All privileges" and the single boxes in step,
 * and fill the change-password dialog from the row that opened it. The password rules themselves are enforced by the
 * server; this only makes strong ones easy.
 */
(function () {
  var all = document.querySelector("[data-priv-all]");
  var singles = Array.prototype.slice.call(document.querySelectorAll("[data-priv]"));
  if (all) {
    all.addEventListener("change", function () { if (all.checked) { singles.forEach(function (b) { b.checked = false; }); } });
    singles.forEach(function (box) {
      box.addEventListener("change", function () { if (box.checked) { all.checked = false; } });
    });
  }

  var dialog = document.getElementById("db-password");
  if (dialog) {
    var input = dialog.querySelector("[name=password]");
    dialog.addEventListener("show.bs.modal", function (event) {
      var opener = event.relatedTarget;
      var user = opener ? opener.getAttribute("data-user") : "";
      dialog.querySelector("[name=user]").value = user || "";
      dialog.querySelector("[data-db-user]").textContent = user || "";
      input.value = "";
      input.type = "password";
    });
    dialog.addEventListener("shown.bs.modal", function () { input.focus(); });
  }
})();
