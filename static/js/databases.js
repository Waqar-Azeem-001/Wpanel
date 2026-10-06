/*
 * Databases screen: show/hide and generate passwords in the browser, keep "All privileges" and the single boxes in step,
 * and fill the change-password dialog from the row that opened it. The password rules themselves are enforced by the
 * server; this only makes strong ones easy.
 */
(function () {
  function field(id) { return document.getElementById(id); }

  function generate(length) {
    var letters = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ";
    var digits = "23456789";
    var all = letters + digits + "-_.!";
    var bytes = new Uint32Array(length);
    window.crypto.getRandomValues(bytes);
    var out = [];
    for (var i = 0; i < length; i++) { out.push(all.charAt(bytes[i] % all.length)); }
    // always at least one letter and one digit, whatever the dice said
    out[0] = letters.charAt(bytes[0] % letters.length);
    out[1] = digits.charAt(bytes[1] % digits.length);
    return out.join("");
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest ? event.target.closest("[data-pw-toggle], [data-pw-generate]") : null;
    if (!target) { return; }
    var input = field(target.getAttribute("data-pw-toggle") || target.getAttribute("data-pw-generate"));
    if (!input) { return; }
    if (target.hasAttribute("data-pw-generate")) {
      input.value = generate(20);
      input.type = "text"; // so the person can see and save what was made
    } else {
      input.type = input.type === "password" ? "text" : "password";
    }
  });

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
