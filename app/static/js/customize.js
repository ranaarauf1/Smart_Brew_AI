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
  // Cup layers, bottom to top (same order as services/cup.py)
  var LAYER_ORDER = ["syrup", "chocolate", "espresso", "tea", "milk", "foam"];

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
   * Purpose: Sets a cup layer's position and height. The attributes keep it
   *   correct everywhere; the style properties let browsers that support
   *   it animate the change (CSS transition, off with reduced motion).
   *
   * Args:
   *   el (SVGElement): The layer rectangle.
   *   y (number): Top edge in SVG units.
   *   height (number): Height in SVG units.
   *
   * Returns:
   *   undefined
   */
  function placeLayer(el, y, height) {
    el.setAttribute("y", y.toFixed(1));
    el.setAttribute("height", height.toFixed(1));
    el.style.y = y.toFixed(1) + "px";
    el.style.height = height.toFixed(1) + "px";
  }

  /**
   * Purpose: Redraws the cup from the layers the server sent (FR-13): each
   *   layer's millilitres become a height on the vessel's scale. Also
   *   updates steam, ice, the legend and the screen-reader description.
   *
   * Args:
   *   cup (Object): The "cup" part of the preview (see services/cup.py).
   *
   * Returns:
   *   undefined
   */
  function showCup(cup) {
    var svg = document.getElementById("cup-picture");
    var legend = document.getElementById("cup-legend");
    if (!svg || !cup) {
      return;
    }
    var top = parseFloat(svg.dataset.top);
    var bottom = parseFloat(svg.dataset.bottom);
    var capacity = cup.capacity_ml;
    var amounts = {};
    cup.layers.forEach(function (layer) { amounts[layer.kind] = layer.ml; });

    var y = bottom;
    var total = 0;
    var espressoTop = null;
    LAYER_ORDER.forEach(function (kind) {
      var ml = amounts[kind] || 0;
      var height = Math.min(ml, Math.max(capacity - total, 0)) / capacity * (bottom - top);
      y -= height;
      total += ml;
      placeLayer(document.getElementById("cup-layer-" + kind), y, height);
      if (kind === "espresso" && height > 0) {
        espressoTop = y;
      }
    });

    var crema = document.getElementById("cup-crema");
    crema.classList.toggle("is-off", espressoTop === null);
    placeLayer(crema, espressoTop === null ? bottom : espressoTop, 2.5);

    var ice = document.getElementById("cup-ice");
    if (ice) {
      ice.setAttribute("transform", "translate(0 " + (y - 30).toFixed(1) + ")");
    }
    [1, 2, 3].forEach(function (n) {
      document.getElementById("cup-steam-" + n).classList.toggle("is-off", n > cup.steam);
    });
    svg.setAttribute("aria-label", cup.description);

    legend.replaceChildren();
    cup.layers.slice().reverse().forEach(function (layer) {
      var item = document.createElement("li");
      var swatch = document.createElement("span");
      var amount = document.createElement("span");
      swatch.className = "swatch swatch-" + layer.kind;
      swatch.setAttribute("aria-hidden", "true");
      amount.className = "num";
      amount.textContent = layer.ml + " ml";
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(layer.label));
      item.appendChild(amount);
      legend.appendChild(item);
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
      showCup(data.cup);
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
