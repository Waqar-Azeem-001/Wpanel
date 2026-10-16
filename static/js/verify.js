(function () {
  var input = document.getElementById("code");
  var go = document.getElementById("verify-go");
  if (input && go) {
    input.addEventListener("input", function () {
      input.value = input.value.replace(/[^0-9]/g, "").slice(0, 6);
      go.disabled = input.value.length !== 6;
    });
  }
  var timer = document.getElementById("verify-timer");
  function tick() {
    if (timer) {
      var left = Math.max(0, Math.round((parseInt(timer.dataset.expires, 10) - Date.now()) / 1000));
      timer.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
    }
    var resend = document.getElementById("verify-resend");
    if (resend) {
      var wait = Math.max(0, Math.round((parseInt(resend.dataset.ready, 10) - Date.now()) / 1000));
      resend.disabled = wait > 0;
      resend.textContent = wait > 0 ? "Resend code in " + wait + "s" : "Resend code";
    }
  }
  tick();
  window.setInterval(tick, 1000);
})();
