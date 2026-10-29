"""Customer-facing touchscreen pages: home, menu, drink details (feature 2),
customization (feature 3) and ordering/dispensing (feature 4)."""

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.services import customization, inventory, orders
from app.services import menu as menu_service

customer_bp = Blueprint("customer", __name__)

SESSION_ORDER_KEY = "order_id"


def get_drink_or_404(drink_id):
    """
    Purpose: Loads an active drink or stops the request with 404.

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        Drink: The drink.
    """
    drink = menu_service.get_drink(drink_id)
    if drink is None:
        abort(404)
    return drink


def get_own_order_or_404(order_id):
    """
    Purpose: Loads an order placed in this session or by this user, or stops
        the request with 404 so other people's orders stay hidden.

    Args:
        order_id (int): Transaction ID from the URL.

    Returns:
        Transaction: The order.
    """
    order = orders.get_order(order_id)
    if order is None or not orders.can_access_order(order, g.user, session.get(SESSION_ORDER_KEY)):
        abort(404)
    return order


def render_customize(drink, selected, error=None, status=200):
    """
    Purpose: Renders the customization screen with the given choices and the
        server-calculated preview, so it is correct even without JavaScript.

    Args:
        drink (Drink): The drink being customized.
        selected (dict): Selection dict (option IDs) to preselect.
        error (str | None): Message to show, e.g. why an order was refused.
        status (int): HTTP status code.

    Returns:
        tuple[str, int]: The rendered page and status code.
    """
    preview = customization.build_preview(drink, selected)
    return render_template(
        "customize.html",
        drink=drink,
        groups=customization.get_option_groups(drink),
        selected=selected,
        preview=preview,
        error=error,
        max_shots=customization.MAX_SYRUP_SHOTS,
    ), status


@customer_bp.route("/")
def home():
    """
    Purpose: Shows the welcome screen of the coffee machine.

    Args:
        None

    Returns:
        str: The rendered home page HTML.
    """
    return render_template("home.html")


@customer_bp.route("/menu")
def menu():
    """
    Purpose: Shows the menu with category tabs (FR-11). The tab is chosen by
        the ?category=<id> query parameter; the first category is the default.
        Drinks low on a base ingredient are greyed out (FR-14, FR-26–28).

    Args:
        None

    Returns:
        str: The rendered menu page; 404 for an unknown category.
    """
    categories = menu_service.get_categories()
    category_id = request.args.get("category", type=int)

    if category_id is None:
        selected = categories[0] if categories else None
    else:
        selected = next((c for c in categories if c.id == category_id), None)
        if selected is None:
            abort(404)

    drinks = menu_service.get_drinks_for_category(selected.id) if selected else []
    available = {drink.id: inventory.is_drink_available(drink) for drink in drinks}
    return render_template(
        "menu.html", categories=categories, selected=selected, drinks=drinks, available=available
    )


@customer_bp.route("/drink/<int:drink_id>")
def drink_detail(drink_id):
    """
    Purpose: Shows one drink with its nutritional information (FR-12),
        one touch from the menu.

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        str: The rendered detail page; 404 if the drink is not available.
    """
    drink = get_drink_or_404(drink_id)
    return render_template(
        "drink_detail.html",
        drink=drink,
        nutrition=menu_service.get_nutrition(drink),
        available=inventory.is_drink_available(drink),
    )


@customer_bp.route("/drink/<int:drink_id>/customize")
def customize(drink_id):
    """
    Purpose: Shows the live customization screen (FR-13, FR-65).

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        Response: The screen, or a redirect to the menu if the drink is
            not available (FR-14).
    """
    drink = get_drink_or_404(drink_id)
    if not inventory.is_drink_available(drink):
        flash(inventory.unavailable_message(drink), "warning")
        return redirect(url_for("customer.menu", category=drink.category_id))

    selected = customization.default_selection(drink).to_ids()
    selected["syrup"] = customization.default_syrup_id()
    return render_customize(drink, selected)


@customer_bp.route("/api/drinks/<int:drink_id>/preview", methods=["POST"])
def preview(drink_id):
    """
    Purpose: Calculates price and nutrition for the current choices; called
        by customize.js on every change (FR-13).

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        Response: JSON preview (see customization.build_preview); status 400
            for invalid JSON or choices, 404 for an unknown drink.
    """
    drink = menu_service.get_drink(drink_id)
    if drink is None:
        return jsonify(ok=False, errors=["This drink is not available."]), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, errors=["Invalid request."]), 400

    result = customization.build_preview(drink, data)
    return jsonify(result), (200 if result["ok"] else 400)


@customer_bp.route("/drink/<int:drink_id>/order", methods=["POST"])
def place_order(drink_id):
    """
    Purpose: Confirms the customized order: checks options and stock
        (FR-14, FR-65) and creates a pending order.

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        Response: Redirect to the order screen, the customization screen
            with the reason (status 400), or the menu if the drink became
            unavailable.
    """
    drink = get_drink_or_404(drink_id)
    if not inventory.is_drink_available(drink):
        flash(inventory.unavailable_message(drink), "warning")
        return redirect(url_for("customer.menu", category=drink.category_id))

    try:
        order = orders.create_pending_order(g.user, drink, request.form)
    except orders.OrderError as error:
        return render_customize(drink, request.form.to_dict(), error=str(error), status=400)

    session[SESSION_ORDER_KEY] = order.id
    return redirect(url_for("customer.view_order", order_id=order.id))


@customer_bp.route("/order/<int:order_id>")
def view_order(order_id):
    """
    Purpose: Shows an order: the summary with "Confirm & dispense" while
        pending, or the result after dispensing.

    Args:
        order_id (int): Transaction ID from the URL.

    Returns:
        str: The rendered order page; 404 for someone else's order.
    """
    return render_template("order.html", order=get_own_order_or_404(order_id))


@customer_bp.route("/order/<int:order_id>/dispense", methods=["POST"])
def dispense(order_id):
    """
    Purpose: Temporary "Confirm & dispense" step: re-checks stock and
        dispenses the drink (FR-66, FR-27).

    Args:
        order_id (int): Transaction ID from the URL.

    Returns:
        Response: Redirect to the order page showing the result.
    """
    order = get_own_order_or_404(order_id)
    # TODO(feature 7): take payment before dispensing, never charge for a
    # cancelled order, and end the session after a successful purchase
    # (FR-01–03, see auth.end_session).
    orders.dispense_order(order)
    return redirect(url_for("customer.view_order", order_id=order.id))
