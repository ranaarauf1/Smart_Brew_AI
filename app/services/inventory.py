"""Inventory: stock thresholds, drink availability, stock checks and deduction
(feature 4: FR-14, FR-26–28, FR-66, FR-67)."""

from sqlalchemy import update

from app import db
from app.models import CustomizationOption, ErrorEvent, Ingredient, utcnow


class InsufficientStockError(Exception):
    """Raised when stock no longer covers a deduction; nothing is deducted."""


class StockLevelError(Exception):
    """Raised when a stock level entered on the Simulation panel is invalid."""


def is_below_threshold(ingredient):
    """
    Purpose: Checks whether an ingredient is below its minimum threshold.
        A level exactly at the threshold still counts as available.

    Args:
        ingredient (Ingredient): The ingredient.

    Returns:
        bool: True if stock_level < min_threshold.
    """
    return ingredient.stock_level < ingredient.min_threshold


def get_milk_options():
    """
    Purpose: Lists the milk options that use a real milk ingredient
        (i.e. everything except "None").

    Args:
        None

    Returns:
        list[CustomizationOption]: Milk options with an ingredient.
    """
    return (
        CustomizationOption.query.filter(
            CustomizationOption.group == CustomizationOption.GROUP_MILK,
            CustomizationOption.ingredient_id.isnot(None),
        )
        .order_by(CustomizationOption.sort_order)
        .all()
    )


def get_blocking_ingredients(drink):
    """
    Purpose: Finds the base ingredients that make a drink unavailable
        (FR-14, FR-26–28). Milk counts as interchangeable: a milk drink is
        only blocked when every milk is below its threshold.

    Args:
        drink (Drink): The drink to check.

    Returns:
        list[str]: Names of blocking ingredients ("Milk" if all milks are
            low); empty if the drink is available.
    """
    blocking = []
    has_milk = False
    for item in drink.recipe_items:
        if item.ingredient.kind == Ingredient.KIND_MILK:
            has_milk = True
        elif is_below_threshold(item.ingredient):
            blocking.append(item.ingredient.name)

    if has_milk and all(is_below_threshold(o.ingredient) for o in get_milk_options()):
        blocking.append("Milk")
    return blocking


def is_drink_available(drink):
    """
    Purpose: Checks whether a drink can be ordered with its base ingredients.

    Args:
        drink (Drink): The drink to check.

    Returns:
        bool: True if no base ingredient is below its threshold.
    """
    return not get_blocking_ingredients(drink)


def unavailable_message(drink):
    """
    Purpose: Builds the message shown when a drink cannot be ordered.

    Args:
        drink (Drink): The unavailable drink.

    Returns:
        str: User-facing explanation.
    """
    names = ", ".join(get_blocking_ingredients(drink))
    return f"{drink.name} is not available right now (low on: {names})."


def check_stock(needs):
    """
    Purpose: Checks that current stock covers every quantity an order needs
        (FR-14, FR-66), e.g. the extra beans for an extra shot.

    Args:
        needs (dict[Ingredient, float]): Quantity needed per ingredient.

    Returns:
        list[dict]: One entry per shortage with keys ingredient, needed and
            available; empty if stock covers everything.
    """
    shortages = []
    for ingredient, quantity in needs.items():
        if ingredient.stock_level < quantity:
            shortages.append({
                "ingredient": ingredient,
                "needed": quantity,
                "available": ingredient.stock_level,
            })
    return shortages


def shortage_message(shortages):
    """
    Purpose: Builds a clear message for stock shortages.

    Args:
        shortages (list[dict]): Result of check_stock().

    Returns:
        str: E.g. "Not enough Coffee beans for this order (needs 27 g, 20 g left)."
    """
    parts = [
        f"{s['ingredient'].name} (needs {s['needed']:g} {s['ingredient'].unit}, "
        f"{max(s['available'], 0):g} {s['ingredient'].unit} left)"
        for s in shortages
    ]
    return "Not enough " + "; ".join(parts) + " for this order."


def deduct_stock(needs):
    """
    Purpose: Deducts an order's ingredients from stock in one database
        transaction (FR-27). Each UPDATE only succeeds if stock still covers
        the quantity, so stock can never go negative even if it changed
        since the last check. The caller commits.

    Args:
        needs (dict[Ingredient, float]): Quantity to deduct per ingredient.

    Returns:
        dict[int, float]: Stock level per ingredient ID before the deduction.

    Raises:
        InsufficientStockError: If any ingredient no longer covers its
            quantity; all deductions are rolled back.
    """
    levels_before = {}
    for ingredient, quantity in needs.items():
        db.session.refresh(ingredient)
        levels_before[ingredient.id] = ingredient.stock_level
        result = db.session.execute(
            update(Ingredient)
            .where(Ingredient.id == ingredient.id, Ingredient.stock_level >= quantity)
            .values(stock_level=Ingredient.stock_level - quantity)
        )
        if result.rowcount == 0:
            db.session.rollback()
            raise InsufficientStockError(f"Not enough {ingredient.name} to dispense.")

    for ingredient in needs:
        db.session.refresh(ingredient)  # load the new levels into the objects
    return levels_before


def get_open_low_stock_event(ingredient):
    """
    Purpose: Finds the unresolved low-threshold event for an ingredient.

    Args:
        ingredient (Ingredient): The ingredient.

    Returns:
        ErrorEvent | None: The open event, or None.
    """
    return ErrorEvent.query.filter_by(
        code=ErrorEvent.CODE_LOW_INGREDIENT, ingredient_id=ingredient.id, resolved_at=None
    ).first()


def record_threshold_crossings(levels_before, ingredients):
    """
    Purpose: Creates a "Low Ingredient Threshold" error event for every
        ingredient that just dropped below its minimum (FR-67). Ingredients
        with an open event get no duplicate. The caller commits.

    Args:
        levels_before (dict[int, float]): Stock per ingredient ID before the change.
        ingredients (Iterable[Ingredient]): The ingredients after the change.

    Returns:
        list[ErrorEvent]: The events created.
    """
    created = []
    for ingredient in ingredients:
        before = levels_before.get(ingredient.id, ingredient.stock_level)
        crossed = before >= ingredient.min_threshold and is_below_threshold(ingredient)
        if crossed and get_open_low_stock_event(ingredient) is None:
            event = ErrorEvent(
                code=ErrorEvent.CODE_LOW_INGREDIENT,
                component="Inventory",
                message=(
                    f"Low Ingredient Threshold: {ingredient.name} at "
                    f"{ingredient.stock_level:g} {ingredient.unit} "
                    f"(minimum {ingredient.min_threshold:g} {ingredient.unit})."
                ),
                severity=ErrorEvent.SEVERITY_WARNING,
                ingredient_id=ingredient.id,
            )
            db.session.add(event)
            created.append(event)
    return created


def get_all_ingredients():
    """
    Purpose: Lists all ingredients for the Simulation panel.

    Args:
        None

    Returns:
        list[Ingredient]: Ingredients sorted by name.
    """
    return Ingredient.query.order_by(Ingredient.name).all()


def parse_stock_level(raw_value, ingredient):
    """
    Purpose: Validates a stock level typed on the Simulation panel.

    Args:
        raw_value (str | None): The value from the form.
        ingredient (Ingredient): The ingredient being set.

    Returns:
        float: The level, between 0 and the ingredient's capacity.

    Raises:
        StockLevelError: If the value is missing, not a number or out of range.
    """
    try:
        level = float(raw_value)
    except (TypeError, ValueError):
        raise StockLevelError("Please enter a number.") from None
    if level != level or level < 0 or level > ingredient.capacity:  # level != level: NaN
        raise StockLevelError(
            f"{ingredient.name} must be between 0 and {ingredient.capacity:g} {ingredient.unit}."
        )
    return level


def set_stock_level(ingredient_id, raw_value):
    """
    Purpose: Sets an ingredient's stock from the Simulation panel. Dropping
        below the minimum creates a low-threshold event (FR-67); going back
        to or above the minimum resolves the open event.

    Args:
        ingredient_id (int | None): ID of the ingredient.
        raw_value (str | float | None): New level as entered.

    Returns:
        Ingredient: The updated ingredient.

    Raises:
        StockLevelError: If the ingredient is unknown or the level is invalid.
    """
    ingredient = db.session.get(Ingredient, ingredient_id) if ingredient_id else None
    if ingredient is None:
        raise StockLevelError("Unknown ingredient.")
    level = parse_stock_level(raw_value, ingredient)

    levels_before = {ingredient.id: ingredient.stock_level}
    ingredient.stock_level = level
    record_threshold_crossings(levels_before, [ingredient])

    open_event = get_open_low_stock_event(ingredient)
    if open_event is not None and not is_below_threshold(ingredient):
        open_event.resolved_at = utcnow()

    db.session.commit()
    return ingredient
