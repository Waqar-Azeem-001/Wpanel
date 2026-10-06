/*
 * Domains screen: the add form changes with what is being added (a subdomain is typed as its first part and put under one of
 * your domains; the suggested website folder follows the name), and the redirect dialog is filled from the row that opened it.
 * The server checks every name, folder and address; nothing here decides what is allowed.
 */
(function () {
  var form = document.querySelector("[data-dm-add]");
  if (form) {
    var name = form.querySelector("[data-dm-name]");
    var parent = form.querySelector("[data-dm-parent]");
    var root = form.querySelector("[data-dm-root]");
    var hint = form.querySelector("[data-dm-hint]");
    var kinds = form.querySelectorAll("[name=kind]");

    function kind() {
      var chosen = form.querySelector("[name=kind]:checked");
      return chosen ? chosen.value : "addon";
    }

    function fullName() {
      var typed = name.value.trim().toLowerCase();
      return kind() === "subdomain" && parent.value ? (typed || "blog") + "." + parent.value : typed;
    }

    function refresh() {
      var which = kind();
      var isSub = which === "subdomain";
      parent.hidden = !isSub;
      name.placeholder = isSub ? "blog" : (which === "alias" ? "myalias.com" : "myshop.com");
      root.disabled = which === "alias";
      root.placeholder = which === "alias" ? "shows your main website" : "public_html/" + (fullName() || "myshop.com");
      if (hint) {
        hint.textContent = which === "alias"
          ? "An alias shows your main website, so it needs no folder of its own."
          : "Leave the folder empty to use the suggested one. It is created for you in the file manager.";
      }
    }

    kinds.forEach(function (radio) { radio.addEventListener("change", refresh); });
    name.addEventListener("input", refresh);
    parent.addEventListener("change", refresh);
    refresh();
  }

  var dialog = document.getElementById("dm-redirect");
  if (dialog) {
    dialog.addEventListener("show.bs.modal", function (event) {
      var button = event.relatedTarget;
      var domain = button ? button.getAttribute("data-name") : "";
      var url = button ? button.getAttribute("data-url") : "";
      dialog.querySelector("[name=name]").value = domain;
      dialog.querySelector("[data-dm-name]").textContent = domain;
      dialog.querySelector("[name=redirect_to]").value = url || "";
      dialog.querySelector("[name=kind]").value = button && button.getAttribute("data-permanent") === "1" ? "301" : (url ? "302" : "301");
      dialog.querySelector("[data-dm-remove]").hidden = !url; // nothing to remove until a redirect exists
    });
  }
})();
