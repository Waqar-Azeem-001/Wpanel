/*
 * Small behaviours shared by every page. No business rules live here (roadmap Rule 3).
 *
 * Confirmation modal: any form or button with data-confirm="Are you sure?" asks first, in an accessible Bootstrap modal
 * (used for sensitive actions; roadmap Phase 16 builds on it).
 */
(function () {
  var modalElement = document.getElementById("confirm-modal");
  if (!modalElement || !window.bootstrap) { return; }
  var modal = new window.bootstrap.Modal(modalElement);
  var message = modalElement.querySelector("[data-confirm-message]");
  var accept = modalElement.querySelector("[data-confirm-accept]");
  var pending = null;

  function ask(text, action) {
    message.textContent = text;
    pending = action;
    modal.show();
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var trigger = event.submitter && event.submitter.getAttribute("data-confirm") ? event.submitter : form;
    var text = trigger.getAttribute("data-confirm");
    if (!text || form.dataset.confirmed === "1") { return; }
    event.preventDefault();
    ask(text, function () { form.dataset.confirmed = "1"; if (form.requestSubmit) { form.requestSubmit(event.submitter); } else { form.submit(); } });
  });

  accept.addEventListener("click", function () {
    var action = pending;
    pending = null;
    modal.hide();
    if (action) { action(); }
  });
  modalElement.addEventListener("hidden.bs.modal", function () { pending = null; });
})();
