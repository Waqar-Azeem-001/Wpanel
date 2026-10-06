/*
 * Email screen: fills the password, size and automatic-reply dialogs from the row that opened them. The server checks
 * every address, size and password; nothing here decides what is allowed. (passwords.js adds Generate and show/hide.)
 */
(function () {
  function opener(event) { return event.relatedTarget; }

  function wire(id, onShow) {
    var dialog = document.getElementById(id);
    if (!dialog) { return; }
    dialog.addEventListener("show.bs.modal", function (event) {
      var button = opener(event);
      var address = button ? button.getAttribute("data-address") : "";
      dialog.querySelector("[name=address]").value = address;
      dialog.querySelector("[data-em-address]").textContent = address;
      if (onShow) { onShow(dialog, button); }
    });
  }

  wire("em-password", function (dialog) {
    var input = dialog.querySelector("[name=password]");
    input.value = "";
    input.type = "password";
  });

  wire("em-quota", function (dialog, button) {
    var select = dialog.querySelector("[name=quota]");
    var current = button.getAttribute("data-quota");
    Array.prototype.forEach.call(select.options, function (option) { option.selected = option.value === current; });
  });

  wire("em-auto", function (dialog, button) {
    dialog.querySelector("[name=subject]").value = button.getAttribute("data-subject") || "";
    dialog.querySelector("[name=body]").value = button.getAttribute("data-body") || "";
    var has = button.getAttribute("data-has") === "1";
    dialog.querySelector("[name=enabled]").checked = has ? button.getAttribute("data-enabled") === "1" : true;
    dialog.querySelector("[data-em-remove]").hidden = !has; // nothing to remove until a reply exists
  });
})();
