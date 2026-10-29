// On-screen PIN keypad for admin mode (feature 10). The PIN is still checked
// on the server; this only types digits into the field.
(function () {
  var field = document.getElementById("pin");
  if (!field) {
    return;
  }

  /**
   * Purpose: Adds a digit to the PIN field, or removes the last one for "⌫".
   *
   * Args:
   *   key (string): The pressed key ("0"–"9" or "⌫").
   *
   * Returns:
   *   undefined
   */
  function press(key) {
    if (key === "⌫") {
      field.value = field.value.slice(0, -1);
    } else if (field.value.length < field.maxLength) {
      field.value += key;
    }
  }

  document.querySelectorAll(".key[data-key]").forEach(function (button) {
    button.addEventListener("click", function () {
      press(button.dataset.key);
    });
  });
})();
