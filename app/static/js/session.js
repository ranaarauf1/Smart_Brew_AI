// Inactivity countdown, used twice: the 60 s customer session (NFR-14) and
// the 2-minute admin session (feature 10). The <script> tag says which
// elements to use (data-countdown-id, data-form-id) and where to ping.
// Any tap or key press restarts the countdown. Taps also ping the server
// (at most every 5 s) so its inactivity timer stays in sync, even on pages
// that do not reload. At 0 the form is submitted with reason=timeout.
(function () {
  var script = document.currentScript;
  var display = document.getElementById(script.dataset.countdownId || "countdown");
  var form = document.getElementById(script.dataset.formId || "logout-form");
  if (!display || !form) {
    return;
  }

  var keepaliveUrl = script.dataset.keepaliveUrl;
  var homeUrl = script.dataset.homeUrl;
  var timeout = parseInt(display.dataset.timeout, 10) || 60;
  var PING_INTERVAL_MS = 5000;

  var deadline = Date.now() + timeout * 1000;
  var lastPing = Date.now(); // the page load itself counted as activity
  var loggingOut = false;

  /**
   * Purpose: Calculates how many whole seconds remain before logout.
   *
   * Args:
   *   None
   *
   * Returns:
   *   number: Seconds left, never below 0.
   */
  function secondsLeft() {
    return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  }

  /**
   * Purpose: Submits the logout form marked as an inactivity timeout.
   *
   * Args:
   *   None
   *
   * Returns:
   *   undefined
   */
  function logOutForTimeout() {
    loggingOut = true;
    form.querySelector("input[name=reason]").value = "timeout";
    form.submit();
  }

  /**
   * Purpose: Updates the countdown display and logs out when it reaches 0.
   *
   * Args:
   *   None
   *
   * Returns:
   *   undefined
   */
  function render() {
    var left = secondsLeft();
    display.textContent = left;
    display.parentElement.classList.toggle("countdown-warning", left <= 10);
    if (left === 0 && !loggingOut) {
      logOutForTimeout();
    }
  }

  /**
   * Purpose: Tells the server the user is still active; returns to the
   *   start screen if the server has already ended the session.
   *
   * Args:
   *   None
   *
   * Returns:
   *   undefined
   */
  function ping() {
    lastPing = Date.now();
    fetch(keepaliveUrl, { method: "POST", credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 401) {
          window.location.href = homeUrl;
        }
      })
      .catch(function () { /* offline: the countdown still protects the session */ });
  }

  /**
   * Purpose: Restarts the countdown on user activity and pings the server
   *   if the last ping was more than 5 s ago.
   *
   * Args:
   *   None
   *
   * Returns:
   *   undefined
   */
  function onActivity() {
    if (loggingOut) {
      return;
    }
    deadline = Date.now() + timeout * 1000;
    render();
    if (Date.now() - lastPing >= PING_INTERVAL_MS) {
      ping();
    }
  }

  ["pointerdown", "keydown"].forEach(function (eventName) {
    document.addEventListener(eventName, onActivity, { passive: true });
  });

  render();
  setInterval(render, 250);
})();
