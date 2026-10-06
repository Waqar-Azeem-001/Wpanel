/*
 * Cron jobs screen: the schedule box shows only for "Custom schedule", and the edit dialog is filled from the row that opened it.
 * The server checks every schedule and command; nothing here decides what is allowed.
 */
(function () {
  var preset = document.querySelector("[data-cron-preset]");
  var custom = document.querySelector("[data-cron-custom]");
  if (preset && custom) {
    var sync = function () {
      custom.hidden = preset.value !== "custom";
    };
    preset.addEventListener("change", sync);
    sync();
  }

  var dialog = document.getElementById("cron-edit");
  if (dialog) {
    dialog.addEventListener("show.bs.modal", function (event) {
      var button = event.relatedTarget;
      if (!button) { return; }
      dialog.querySelector("[name=job]").value = button.getAttribute("data-job") || "";
      dialog.querySelector("[name=schedule]").value = button.getAttribute("data-schedule") || "";
      dialog.querySelector("[name=command]").value = button.getAttribute("data-command") || "";
    });
  }
})();
