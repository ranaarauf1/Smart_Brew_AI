"""Simulation panel: stands in for the machine's hardware (NFC reader and
ingredient sensors for now)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app import db
from app.models import Ingredient, NfcCard
from app.services import auth, inventory

simulation_bp = Blueprint("simulation", __name__, url_prefix="/simulation")


@simulation_bp.route("/")
def panel():
    """
    Purpose: Shows the simulation panel with the demo NFC cards to tap and
        the ingredient stock levels to change.

    Args:
        None

    Returns:
        str: The rendered simulation panel.
    """
    cards = NfcCard.query.order_by(NfcCard.is_master, NfcCard.uid).all()
    return render_template(
        "simulation.html",
        cards=cards,
        ingredients=inventory.get_all_ingredients(),
        is_low=inventory.is_below_threshold,
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
