"""Customer-facing touchscreen pages: home, menu with recommendations
(features 2, 6), customization (feature 3), ordering/dispensing (feature 4)
and saved recipes (feature 5)."""

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

from app.routes.auth import login_required
from app.services import customization, inventory, orders, preferences, recipes, recommendations
from app.services import menu as menu_service
from app.services import weather as weather_service

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


def get_own_recipe_or_404(recipe_id):
    """
    Purpose: Loads one of the logged-in user's recipes, or stops with 404 so
        other users' recipes stay hidden.

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        SavedRecipe: The recipe.
    """
    recipe = recipes.get_recipe(g.user, recipe_id)
    if recipe is None:
        abort(404)
    return recipe


def render_customize(drink, selected, error=None, status=200, recipe=None, recipe_name=""):
    """
    Purpose: Renders the customization screen with the given choices and the
        server-calculated preview, so it is correct even without JavaScript.

    Args:
        drink (Drink): The drink being customized.
        selected (dict): Selection dict (option IDs) to preselect.
        error (str | None): Message to show, e.g. why an order was refused.
        status (int): HTTP status code.
        recipe (SavedRecipe | None): Recipe being edited, if any (feature 5).
        recipe_name (str): Name typed for a new recipe, kept after an error.

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
        recipe=recipe,
        recipe_name=recipe_name,
        max_recipe_name=recipes.MAX_NAME_LENGTH,
    ), status


def build_menu_entries(drinks, recs):
    """
    Purpose: Orders a category's drinks for the menu: available drinks by
        recommendation score (ties by name), then unavailable ones by name.

    Args:
        drinks (list[Drink]): Drinks of the category.
        recs (MenuRecommendations): Recommendations for this user.

    Returns:
        list[dict]: Entries with keys drink, available, rec and top.
    """
    by_id = {rec.drink.id: rec for rec in recs.ranked}
    available = sorted(
        (d for d in drinks if d.id in by_id),
        key=lambda d: (-by_id[d.id].score, d.name),
    )
    unavailable = sorted((d for d in drinks if d.id not in by_id), key=lambda d: d.name)
    return [
        {"drink": d, "available": True, "rec": by_id[d.id], "top": d.id in recs.top_ids}
        for d in available
    ] + [{"drink": d, "available": False, "rec": None, "top": False} for d in unavailable]


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
    Purpose: Shows the menu (FR-11). The default "For you" tab lists all
        available drinks sorted by recommendation score with the top 3
        highlighted (FR-22–25); ?category=<id> shows one category, also
        sorted by score, with unavailable drinks greyed out at the end
        (FR-14, FR-26–28).

    Args:
        None

    Returns:
        str: The rendered menu page; 404 for an unknown category.
    """
    categories = menu_service.get_categories()
    recs = recommendations.get_menu_recommendations(g.user)

    selected = None
    if request.args.get("category") is not None:
        category_id = request.args.get("category", type=int)
        selected = next((c for c in categories if c.id == category_id), None)
        if selected is None:
            abort(404)

    if selected is None:
        entries = [
            {"drink": rec.drink, "available": True, "rec": rec, "top": rec.drink.id in recs.top_ids}
            for rec in recs.ranked
        ]
    else:
        entries = build_menu_entries(menu_service.get_drinks_for_category(selected.id), recs)

    return render_template(
        "menu.html",
        categories=categories,
        selected=selected,
        entries=entries,
        weather_text=weather_service.describe(recs.weather),
        weather_note=recs.note,
        period=recs.period,
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
    Purpose: Shows the live customization screen (FR-13, FR-65), with the
        user's preferred options preselected where possible (feature 6).

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

    selection = customization.default_selection(drink)
    preferences.apply_preferences(selection, g.user, drink)
    selected = selection.to_ids()
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


# ---------------------------------------------------------------------------
# Saved recipes (feature 5)
# ---------------------------------------------------------------------------


@customer_bp.route("/recipes")
@login_required
def recipe_list():
    """
    Purpose: Shows "My recipes" with each recipe's options, current price and
        whether it can be ordered now (FR-08, FR-15).

    Args:
        None

    Returns:
        str: The rendered recipe list.
    """
    statuses = [recipes.get_recipe_status(r) for r in recipes.get_user_recipes(g.user)]
    return render_template(
        "recipes.html", statuses=statuses, max_recipe_name=recipes.MAX_NAME_LENGTH
    )


@customer_bp.route("/drink/<int:drink_id>/recipes", methods=["POST"])
@login_required
def save_recipe(drink_id):
    """
    Purpose: Saves the choices on the customization screen as a named
        recipe (FR-07).

    Args:
        drink_id (int): ID of the drink from the URL.

    Returns:
        Response: Redirect to "My recipes", or the customization screen with
            the reason (status 400).
    """
    drink = get_drink_or_404(drink_id)
    name = request.form.get("recipe_name", "")
    try:
        recipe = recipes.save_recipe(g.user, drink, name, request.form)
    except recipes.RecipeError as error:
        return render_customize(
            drink, request.form.to_dict(), error=str(error), status=400, recipe_name=name
        )
    flash(f"Saved \"{recipe.name}\" to My recipes.", "success")
    return redirect(url_for("customer.recipe_list"))


@customer_bp.route("/recipes/<int:recipe_id>/edit")
@login_required
def edit_recipe(recipe_id):
    """
    Purpose: Opens the customization screen prefilled with a recipe's
        options so they can be changed (FR-09).

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        Response: The customization screen in edit mode, or a redirect if
            the drink is no longer on the menu.
    """
    recipe = get_own_recipe_or_404(recipe_id)
    if not recipe.drink.is_active:
        flash(f"{recipe.drink.name} is no longer on the menu.", "warning")
        return redirect(url_for("customer.recipe_list"))

    selected = dict(recipe.customizations or {})
    if selected.get("syrup") is None:
        selected["syrup"] = customization.default_syrup_id()  # preselect a type for the stepper
    return render_customize(recipe.drink, selected, recipe=recipe)


@customer_bp.route("/recipes/<int:recipe_id>/update", methods=["POST"])
@login_required
def update_recipe(recipe_id):
    """
    Purpose: Saves the edited options of a recipe (FR-09).

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        Response: Redirect to "My recipes", or the edit screen with the
            reason (status 400).
    """
    recipe = get_own_recipe_or_404(recipe_id)
    try:
        recipes.update_recipe_options(recipe, request.form)
    except recipes.RecipeError as error:
        return render_customize(
            recipe.drink, request.form.to_dict(), error=str(error), status=400, recipe=recipe
        )
    flash(f"Updated \"{recipe.name}\".", "success")
    return redirect(url_for("customer.recipe_list"))


@customer_bp.route("/recipes/<int:recipe_id>/rename", methods=["POST"])
@login_required
def rename_recipe(recipe_id):
    """
    Purpose: Renames a recipe (FR-09).

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        Response: Redirect to "My recipes" with a message.
    """
    recipe = get_own_recipe_or_404(recipe_id)
    try:
        recipes.rename_recipe(recipe, request.form.get("name"))
    except recipes.RecipeError as error:
        flash(str(error), "error")
    else:
        flash(f"Renamed to \"{recipe.name}\".", "success")
    return redirect(url_for("customer.recipe_list"))


@customer_bp.route("/recipes/<int:recipe_id>/delete", methods=["POST"])
@login_required
def delete_recipe(recipe_id):
    """
    Purpose: Deletes a recipe after the user confirmed it (FR-10). The
        "Yes, delete" button sends confirm=yes; without it nothing happens.

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        Response: Redirect to "My recipes" with a message.
    """
    recipe = get_own_recipe_or_404(recipe_id)
    if request.form.get("confirm") != "yes":
        flash("Please confirm that you want to delete this recipe.", "warning")
        return redirect(url_for("customer.recipe_list"))
    name = recipe.name
    recipes.delete_recipe(recipe)
    flash(f"Deleted \"{name}\".", "success")
    return redirect(url_for("customer.recipe_list"))


@customer_bp.route("/recipes/<int:recipe_id>/order", methods=["POST"])
@login_required
def order_recipe(recipe_id):
    """
    Purpose: One-touch ordering from a saved recipe: goes straight to the
        confirm/dispense step (FR-15, NFR-24).

    Args:
        recipe_id (int): Recipe ID from the URL.

    Returns:
        Response: Redirect to the order screen, or back to "My recipes" with
            the reason if it cannot be ordered.
    """
    recipe = get_own_recipe_or_404(recipe_id)
    try:
        order = recipes.order_recipe(g.user, recipe)
    except recipes.RecipeError as error:
        flash(str(error), "error")
        return redirect(url_for("customer.recipe_list"))
    session[SESSION_ORDER_KEY] = order.id
    return redirect(url_for("customer.view_order", order_id=order.id))
