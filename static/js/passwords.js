/*
 * Shared by the hosting screens that set a password (databases, email): a Generate button and a show/hide button next to a
 * password field. Buttons say which field they work on:  data-pw-generate="field-id"  /  data-pw-toggle="field-id".
 * The password rules themselves are enforced by the server; this only makes strong ones easy.
 */
(function () {
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
    var input = document.getElementById(target.getAttribute("data-pw-toggle") || target.getAttribute("data-pw-generate"));
    if (!input) { return; }
    if (target.hasAttribute("data-pw-generate")) {
      input.value = generate(20);
      input.type = "text"; // so the person can see and save what was made
    } else {
      input.type = input.type === "password" ? "text" : "password";
    }
  });
})();
