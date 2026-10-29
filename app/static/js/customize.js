// FR-13 / FR-65: live customization preview.
// On every change the current choices are sent to the Python preview
// endpoint; this script only displays the price, nutrition, messages and
// out-of-stock options it returns. No prices or nutrition are calculated here.
(function () {
  var form = document.getElementById("customize-form");
  if (!form) {
    return;
  }

  var previewUrl = form.dataset.previewUrl;
  var shotsInput = document.getElementById("syrup-shots");
  var priceEl = document.getElementById("preview-price");
  var summaryEl = document.getElementById("preview-summary");
  var nutritionEl = document.getElementById("preview-nutrition");
  var messagesEl = document.getElementById("preview-messages");
  var confirmButton = document.getElementById("confirm-button");
  var requestNumber = 0; // only the newest response is displayed

  /**
   * Purpose: Reads the value of the checked radio button of one group.
   *
   * Args:
   *   name (string): The group's input name, e.g. "milk".
   *
   * Returns:
   *   string|null: The chosen option ID, or null if none is checked.
   */
  function checkedValue(name) {
    var input = form.querySelector("input[name='" + name + "']:checked");
    return input ? input.value : null;
  }

  /**
   * Purpose: Collects the current choices in the format the server expects.
   *
   * Args:
   *   None
   *
   * Returns:
   *   Object: Selection with milk, strength, temperature, syrup, syrup_shots.
   */
  function collectSelection() {
    return {
      milk: checkedValue("milk"),
      strength: checkedValue("strength"),
      temperature: checkedValue("temperature"),
      syrup: checkedValue("syrup"),
      syrup_shots: shotsInput ? shotsInput.value : 0
    };
  }

  /**
   * Purpose: Replaces the message area with the given messages.
   *
   * Args:
   *   messages (string[]): Messages to show; empty clears the area.
   *
   * Returns:
   *   undefined
   */
  function showMessages(messages) {
    messagesEl.replaceChildren();
    messages.forEach(function (text) {
      var p = document.createElement("p");
      p.className = "field-error";
      p.textContent = text;
      messagesEl.appendChild(p);
    });
  }

  /**
   * Purpose: Rebuilds the nutrition list from the server's rows.
   *
   * Args:
   *   rows (Array<[string, string]>): (label, value) pairs.
   *
   * Returns:
   *   undefined
   */
  function showNutrition(rows) {
    nutritionEl.replaceChildren();
    rows.forEach(function (row) {
      var wrapper = document.createElement("div");
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      wrapper.className = "nutrition-row";
      dt.textContent = row[0];
      dd.textContent = row[1];
      wrapper.appendChild(dt);
      wrapper.appendChild(dd);
      nutritionEl.appendChild(wrapper);
    });
  }

  /**
   * Purpose: Disables and labels options the server reports as out of stock
   *   (FR-65), and re-enables options that are back in stock.
   *
   * Args:
   *   outOfStockIds (number[]): Option IDs that cannot be chosen.
   *
   * Returns:
   *   undefined
   */
  function showStock(outOfStockIds) {
    form.querySelectorAll("input[data-option-id]").forEach(function (input) {
      var out = outOfStockIds.indexOf(parseInt(input.dataset.optionId, 10)) !== -1;
      var label = input.closest(".option");
      input.disabled = out;
      if (out) {
        input.checked = false;
      }
      label.classList.toggle("option-disabled", out);
      label.querySelector(".out-of-stock-label").hidden = !out;
    });
  }

  /**
   * Purpose: Displays a preview response from the server.
   *
   * Args:
   *   data (Object): JSON from the preview endpoint.
   *
   * Returns:
   *   undefined
   */
  function showPreview(data) {
    showStock(data.out_of_stock_option_ids || []);
    if (data.ok) {
      priceEl.textContent = data.price;
      summaryEl.textContent = data.summary;
      showNutrition(data.nutrition);
      showMessages(data.stock_problems);
      confirmButton.disabled = !data.can_order;
    } else {
      showMessages(data.errors || ["This choice is not possible."]);
      confirmButton.disabled = true;
    }
  }

  /**
   * Purpose: Sends the current choices to the server and shows the result.
   *
   * Args:
   *   None
   *
   * Returns:
   *   undefined
   */
  function refreshPreview() {
    var thisRequest = ++requestNumber;
    form.classList.toggle("syrup-off", shotsInput && parseInt(shotsInput.value, 10) === 0);
    fetch(previewUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectSelection())
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (thisRequest === requestNumber) {
          showPreview(data);
        }
      })
      .catch(function () {
        if (thisRequest === requestNumber) {
          showMessages(["Live preview unavailable. Your choices are checked again when you confirm."]);
        }
      });
  }

  /**
   * Purpose: Changes the number of syrup shots by one step, kept within 0–max.
   *
   * Args:
   *   step (number): +1 or -1.
   *
   * Returns:
   *   undefined
   */
  function changeShots(step) {
    var max = parseInt(shotsInput.max, 10);
    var value = (parseInt(shotsInput.value, 10) || 0) + step;
    shotsInput.value = Math.min(max, Math.max(0, value));
    refreshPreview();
  }

  form.addEventListener("change", refreshPreview);
  form.querySelectorAll(".stepper").forEach(function (button) {
    button.addEventListener("click", function () {
      changeShots(parseInt(button.dataset.step, 10));
    });
  });

  form.classList.toggle("syrup-off", shotsInput && parseInt(shotsInput.value, 10) === 0);
})();
