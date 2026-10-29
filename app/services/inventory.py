"""Inventory: stock thresholds, drink availability, stock checks and deduction
(feature 4: FR-14, FR-26–28, FR-66, FR-67)."""

from sqlalchemy import update

from app import db
from app.models import CustomizationOption, ErrorEvent, Ingredient, utcnow
from app.services import maintenance


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
    Purpose: Checks whether a drink can be ordered: no base ingredient below
        its threshold and no hardware fault that blocks it (feature 10).

    Args:
        drink (Drink): The drink to check.

    Returns:
        bool: True if the drink can be made.
    """
    return not get_blocking_ingredients(drink) and not maintenance.get_fault_blockers(drink)


def unavailable_message(drink):
    """
    Purpose: Builds the message shown when a drink cannot be ordered.

    Args:
        drink (Drink): The unavailable drink.

    Returns:
        str: User-facing explanation, e.g. "Espresso is not available right
            now (low on: Cups; grinder jammed)."
    """
    reasons = []
    names = get_blocking_ingredients(drink)
    if names:
        reasons.append("low on: " + ", ".join(names))
    reasons.extend(maintenance.get_fault_blockers(drink))
    return f"{drink.name} is not available right now ({'; '.join(reasons)})."


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

    was_below = is_below_threshold(ingredient)
    ingredient.stock_level = level
    sync_low_stock_event(ingredient, was_below)
    db.session.commit()
    return ingredient


def sync_low_stock_event(ingredient, was_below):
    """
    Purpose: Keeps an ingredient's low-threshold event in line after a
        manual change (Simulation panel or admin mode): opens one when it
        newly drops below the minimum (FR-67), resolves it when it is at or
        above again. The caller commits.

    Args:
        ingredient (Ingredient): The ingredient after the change.
        was_below (bool): Whether it was below its minimum before.

    Returns:
        None
    """
    open_event = get_open_low_stock_event(ingredient)
    if is_below_threshold(ingredient):
        if not was_below and open_event is None:
            # Treat the old level as "at the minimum" so the crossing is detected
            record_threshold_crossings({ingredient.id: ingredient.min_threshold}, [ingredient])
    elif open_event is not None:
        open_event.resolved_at = utcnow()


def get_ingredient(ingredient_id):
    """
    Purpose: Loads an ingredient for an admin action.

    Args:
        ingredient_id (int | None): ID of the ingredient.

    Returns:
        Ingredient: The ingredient.

    Raises:
        StockLevelError: If it does not exist.
    """
    ingredient = db.session.get(Ingredient, ingredient_id) if ingredient_id else None
    if ingredient is None:
        raise StockLevelError("Unknown ingredient.")
    return ingredient


def parse_number(raw_value, label):
    """
    Purpose: Parses a number typed in admin mode.

    Args:
        raw_value (str | float | None): The value from the form.
        label (str): Field name for the error message.

    Returns:
        float: The number.

    Raises:
        StockLevelError: If it is missing, not a number or NaN.
    """
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise StockLevelError(f"Please enter the {label} as a number.") from None
    if value != value:  # NaN
        raise StockLevelError(f"Please enter the {label} as a number.")
    return value


def refill_ingredient(ingredient_id, raw_quantity):
    """
    Purpose: Refills an ingredient by a quantity, capped at its capacity
        (FR-43–52). The caller commits.

    Args:
        ingredient_id (int | None): ID of the ingredient.
        raw_quantity (str | float | None): Quantity to add.

    Returns:
        tuple[Ingredient, float, float]: The ingredient, the level before
            and the quantity actually added.

    Raises:
        StockLevelError: If the ingredient is unknown or the quantity is not
            a positive number.
    """
    ingredient = get_ingredient(ingredient_id)
    quantity = parse_number(raw_quantity, "quantity")
    if quantity <= 0:
        raise StockLevelError("The refill quantity must be more than 0.")
    before = ingredient.stock_level
    was_below = is_below_threshold(ingredient)
    ingredient.stock_level = min(ingredient.capacity, before + quantity)
    sync_low_stock_event(ingredient, was_below)
    return ingredient, before, ingredient.stock_level - before


def reset_ingredient(ingredient_id):
    """
    Purpose: Resets an ingredient counter to full capacity. The caller commits.

    Args:
        ingredient_id (int | None): ID of the ingredient.

    Returns:
        tuple[Ingredient, float]: The ingredient and its level before.

    Raises:
        StockLevelError: If the ingredient is unknown.
    """
    ingredient = get_ingredient(ingredient_id)
    before = ingredient.stock_level
    was_below = is_below_threshold(ingredient)
    ingredient.stock_level = ingredient.capacity
    sync_low_stock_event(ingredient, was_below)
    return ingredient, before


def set_limits(ingredient_id, raw_minimum, raw_capacity):
    """
    Purpose: Changes an ingredient's minimum threshold and capacity. The
        minimum must be between 0 and the capacity, and the capacity cannot
        be below the current stock. The caller commits.

    Args:
        ingredient_id (int | None): ID of the ingredient.
        raw_minimum (str | float | None): New minimum.
        raw_capacity (str | float | None): New capacity.

    Returns:
        tuple[Ingredient, float, float]: The ingredient and the old minimum
            and capacity.

    Raises:
        StockLevelError: If a value is invalid.
    """
    ingredient = get_ingredient(ingredient_id)
    minimum = parse_number(raw_minimum, "minimum")
    capacity = parse_number(raw_capacity, "capacity")
    if capacity <= 0:
        raise StockLevelError("The capacity must be more than 0.")
    if not 0 <= minimum <= capacity:
        raise StockLevelError("The minimum must be between 0 and the capacity.")
    if capacity < ingredient.stock_level:
        raise StockLevelError(
            f"The capacity cannot be below the current stock "
            f"({ingredient.stock_level:g} {ingredient.unit})."
        )
    old = (ingredient.min_threshold, ingredient.capacity)
    was_below = is_below_threshold(ingredient)
    ingredient.min_threshold, ingredient.capacity = minimum, capacity
    sync_low_stock_event(ingredient, was_below)
    return ingredient, *old
