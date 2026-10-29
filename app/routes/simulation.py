"""Simulation panel: stands in for the machine's hardware and outside
services (NFC reader, ingredient sensors, weather API and clock)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app import db
from app.models import Ingredient, NfcCard, SystemState
from app.services import auth, clock, inventory
from app.services import weather as weather_service

simulation_bp = Blueprint("simulation", __name__, url_prefix="/simulation")


@simulation_bp.app_context_processor
def inject_simulated_clock():
    """
    Purpose: Gives every template the simulated time, so the status bar can
        show it instead of the real time.

    Args:
        None

    Returns:
        dict: Template variable sim_time ("HH:MM" or None).
    """
    return {"sim_time": clock.get_time_override()}


@simulation_bp.route("/")
def panel():
    """
    Purpose: Shows the simulation panel: NFC cards, ingredient stock,
        weather and time of day.

    Args:
        None

    Returns:
        str: The rendered simulation panel.
    """
    now = clock.now()
    try:
        current_weather = weather_service.get_weather(now)
    except weather_service.WeatherUnavailableError:
        current_weather = None

    return render_template(
        "simulation.html",
        cards=NfcCard.query.order_by(NfcCard.is_master, NfcCard.uid).all(),
        ingredients=inventory.get_all_ingredients(),
        is_low=inventory.is_below_threshold,
        state=SystemState.get(),
        conditions=weather_service.CONDITIONS,
        weather_text=weather_service.describe(current_weather),
        now_text=now.strftime("%H:%M"),
        min_temp=weather_service.MIN_TEMP_C,
        max_temp=weather_service.MAX_TEMP_C,
    )


@simulation_bp.route("/nfc-tap", methods=["POST"])
def nfc_tap():
    """
    Purpose: Simulates tapping an NFC card on the reader to log in (FR-02).

    Args:
        None

    Returns:
        Response: Redirect to the menu when logged in, or to the password
            login page with the reason when the card is refused.
    """
    uid = request.form.get("uid", "")
    try:
        user = auth.login_with_nfc(uid)
    except auth.NfcLoginError as error:
        flash(str(error), "error")
        return redirect(url_for("auth.login"))

    auth.start_session(user)
    flash(f"Card accepted. Welcome, {user.name}!", "success")
    return redirect(url_for("customer.menu"))


@simulation_bp.route("/stock", methods=["POST"])
def set_stock():
    """
    Purpose: Simulates the ingredient sensors: sets one ingredient's stock
        level so blocking can be tested (FR-14, FR-26–28). The Empty and
        Full buttons send preset=empty or preset=full instead of a number.

    Args:
        None

    Returns:
        Response: Redirect back to the stock section of the panel.
    """
    ingredient_id = request.form.get("ingredient_id", type=int)
    level = request.form.get("level")
    preset = request.form.get("preset")
    if preset in ("empty", "full"):
        ingredient = db.session.get(Ingredient, ingredient_id) if ingredient_id else None
        if ingredient is not None:
            level = 0 if preset == "empty" else ingredient.capacity

    try:
        ingredient = inventory.set_stock_level(ingredient_id, level)
    except inventory.StockLevelError as error:
        flash(str(error), "error")
    else:
        state = "below minimum" if inventory.is_below_threshold(ingredient) else "OK"
        flash(
            f"{ingredient.name} set to {ingredient.stock_level:g} {ingredient.unit} ({state}).",
            "success",
        )
    return redirect(url_for("simulation.panel", _anchor="stock"))


@simulation_bp.route("/weather", methods=["POST"])
def set_weather():
    """
    Purpose: Simulates the weather API: auto/manual mode, condition,
        temperature and the "Weather API down" switch (FR-17, FR-56).

    Args:
        None

    Returns:
        Response: Redirect back to the weather section of the panel.
    """
    try:
        weather_service.update_settings(
            request.form.get("mode"),
            request.form.get("condition"),
            request.form.get("temperature"),
            online=request.form.get("api_down") != "on",
        )
    except weather_service.WeatherSettingsError as error:
        flash(str(error), "error")
    else:
        flash("Weather settings saved.", "success")
    return redirect(url_for("simulation.panel", _anchor="weather"))


@simulation_bp.route("/time", methods=["POST"])
def set_time():
    """
    Purpose: Sets a simulated time of day for recommendations (FR-16), or
        goes back to the real time when action=real is sent.

    Args:
        None

    Returns:
        Response: Redirect back to the time section of the panel.
    """
    use_real_time = request.form.get("action") == "real"
    text = request.form.get("time")
    try:
        if not use_real_time:
            clock.parse_time(text)  # an empty or invalid time is an error, not "real time"
        override = clock.set_time_override(None if use_real_time else text)
    except clock.ClockSettingsError as error:
        flash(str(error), "error")
    else:
        flash(f"Time set to {override}." if override else "Using the real time again.", "success")
    return redirect(url_for("simulation.panel", _anchor="time"))
