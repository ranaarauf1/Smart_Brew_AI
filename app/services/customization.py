"""Live drink customization: options, validation, ingredients, price and
nutrition (feature 3: FR-13, FR-65).

A *selection* is what the customer chose. In requests and in the database it
is a dict of option IDs::

    {"milk": 2, "strength": 9, "temperature": 11, "syrup": 13, "syrup_shots": 2}

Groups that do not apply to a drink are None, and "syrup" is None when
syrup_shots is 0.
"""

from dataclasses import dataclass

from app import format_euro
from app.models import CustomizationOption, Drink, Ingredient
from app.services import inventory
from app.services.menu import base_nutrition, format_nutrition

MIN_SYRUP_SHOTS = 0
MAX_SYRUP_SHOTS = 3

GROUP_LABELS = {
    CustomizationOption.GROUP_MILK: "Milk",
    CustomizationOption.GROUP_STRENGTH: "Strength",
    CustomizationOption.GROUP_TEMPERATURE: "Temperature",
    CustomizationOption.GROUP_SYRUP: "Syrup",
}

# Groups where exactly one option is always chosen (syrup is optional)
SINGLE_CHOICE_GROUPS = (
    CustomizationOption.GROUP_MILK,
    CustomizationOption.GROUP_STRENGTH,
    CustomizationOption.GROUP_TEMPERATURE,
)


@dataclass
class Selection:
    """The validated options for one drink; None where a group does not apply."""

    milk: CustomizationOption | None = None
    strength: CustomizationOption | None = None
    temperature: CustomizationOption | None = None
    syrup: CustomizationOption | None = None
    syrup_shots: int = 0

    def to_ids(self):
        """
        Purpose: Converts the selection to the storable dict of option IDs.

        Args:
            None

        Returns:
            dict: Selection dict as described in the module docstring.
        """
        return {
            "milk": self.milk.id if self.milk else None,
            "strength": self.strength.id if self.strength else None,
            "temperature": self.temperature.id if self.temperature else None,
            "syrup": self.syrup.id if self.syrup else None,
            "syrup_shots": self.syrup_shots,
        }

    def summary(self):
        """
        Purpose: Describes the chosen options in one short line for the
            order screen and history, e.g. "Oat milk · Extra shot · Hot · 2× Vanilla".

        Args:
            None

        Returns:
            str: The summary, or "Standard" if no option applies.
        """
        parts = []
        if self.milk:
            parts.append("No milk" if self.milk.ingredient is None else self.milk.name)
        if self.strength:
            parts.append(self.strength.name)
        if self.temperature:
            parts.append(self.temperature.name)
        if self.syrup:
            parts.append(f"{self.syrup_shots}× {self.syrup.name}")
        return " · ".join(parts) or "Standard"


# ---------------------------------------------------------------------------
# Which options a drink has
# ---------------------------------------------------------------------------


def get_recipe_milk_item(drink):
    """
    Purpose: Finds the milk line of a drink's recipe.

    Args:
        drink (Drink): The drink.

    Returns:
        DrinkIngredient | None: The recipe line using milk, or None.
    """
    return next(
        (item for item in drink.recipe_items if item.ingredient.kind == Ingredient.KIND_MILK),
        None,
    )


def get_applicable_groups(drink):
    """
    Purpose: Decides which option groups a drink offers: milk only with milk
        in the recipe, strength only for coffee, temperature only for hot
        drinks, syrup for all.

    Args:
        drink (Drink): The drink.

    Returns:
        list[str]: Group keys in display order.
    """
    groups = []
    if get_recipe_milk_item(drink) is not None:
        groups.append(CustomizationOption.GROUP_MILK)
    if any(item.ingredient.kind == Ingredient.KIND_COFFEE for item in drink.recipe_items):
        groups.append(CustomizationOption.GROUP_STRENGTH)
    if drink.serving == Drink.SERVING_HOT:
        groups.append(CustomizationOption.GROUP_TEMPERATURE)
    groups.append(CustomizationOption.GROUP_SYRUP)
    return groups


def get_options(group):
    """
    Purpose: Lists all options of one group in display order.

    Args:
        group (str): Group key, e.g. "milk".

    Returns:
        list[CustomizationOption]: The group's options.
    """
    return (
        CustomizationOption.query.filter_by(group=group)
        .order_by(CustomizationOption.sort_order)
        .all()
    )


def is_option_in_stock(option):
    """
    Purpose: Checks whether an option can be chosen (FR-65): an option whose
        ingredient is below its minimum threshold is out of stock.

    Args:
        option (CustomizationOption): The option.

    Returns:
        bool: True if the option has no ingredient or its ingredient is
            at or above the threshold.
    """
    return option.ingredient is None or not inventory.is_below_threshold(option.ingredient)


def get_option_groups(drink):
    """
    Purpose: Builds the option groups shown on the customization screen,
        marking out-of-stock options (FR-13, FR-65).

    Args:
        drink (Drink): The drink being customized.

    Returns:
        list[dict]: One dict per group with keys key, label and options,
            where options is a list of {"option", "in_stock"} dicts.
    """
    return [
        {
            "key": group,
            "label": GROUP_LABELS[group],
            "options": [
                {"option": option, "in_stock": is_option_in_stock(option)}
                for option in get_options(group)
            ],
        }
        for group in get_applicable_groups(drink)
    ]


def get_out_of_stock_option_ids(drink):
    """
    Purpose: Lists the IDs of the drink's options that are out of stock, so
        the screen can disable them after a stock change.

    Args:
        drink (Drink): The drink being customized.

    Returns:
        list[int]: Option IDs that cannot be chosen right now.
    """
    return [
        entry["option"].id
        for group in get_option_groups(drink)
        for entry in group["options"]
        if not entry["in_stock"]
    ]


def default_option(group):
    """
    Purpose: Picks the preselected option of a group: the default if it is
        in stock, otherwise the first option in stock. For milk, "None" is
        only picked if no real milk is in stock.

    Args:
        group (str): Group key.

    Returns:
        CustomizationOption | None: The option, or None if none is in stock.
    """
    options = [o for o in get_options(group) if is_option_in_stock(o)]
    if group == CustomizationOption.GROUP_MILK:
        real_milks = [o for o in options if o.ingredient is not None]
        options = real_milks or options
    preferred = next((o for o in options if o.is_default), None)
    return preferred or (options[0] if options else None)


def default_selection(drink):
    """
    Purpose: Builds the selection preselected when the screen opens.

    Args:
        drink (Drink): The drink being customized.

    Returns:
        Selection: Defaults for every applicable group, no syrup.
    """
    groups = get_applicable_groups(drink)
    selection = Selection()
    for group in SINGLE_CHOICE_GROUPS:
        if group in groups:
            setattr(selection, group, default_option(group))
    return selection


def default_syrup_id():
    """
    Purpose: Picks the syrup type preselected on the screen (first in stock).

    Args:
        None

    Returns:
        int | None: Option ID, or None if no syrup is in stock.
    """
    option = default_option(CustomizationOption.GROUP_SYRUP)
    return option.id if option else None


# ---------------------------------------------------------------------------
# Validating a selection
# ---------------------------------------------------------------------------


def _parse_int(value):
    """
    Purpose: Converts a request value to int, accepting ints and digit strings.

    Args:
        value (object): Value from JSON or a form.

    Returns:
        int | None: The integer, or None if empty or not a whole number.
    """
    if isinstance(value, bool):
        return None  # True/False are ints in Python but not valid IDs
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _resolve_option(group, raw_value, errors):
    """
    Purpose: Turns a submitted option ID into an option of the given group,
        recording an error if it is invalid or out of stock.

    Args:
        group (str): Group key.
        raw_value (object): Submitted option ID.
        errors (list[str]): Error list to append to.

    Returns:
        CustomizationOption | None: The option, or None on error.
    """
    option_id = _parse_int(raw_value)
    option = next((o for o in get_options(group) if o.id == option_id), None)
    if option is None:
        errors.append(f"Please choose a valid {GROUP_LABELS[group].lower()} option.")
        return None
    if not is_option_in_stock(option):
        # FR-65: depleted options cannot be chosen
        errors.append(f"{option.name} is out of stock.")
        return None
    return option


def parse_selection(drink, data):
    """
    Purpose: Validates submitted choices for a drink (FR-13, FR-65). Missing
        groups get their default; options for groups the drink does not
        offer are rejected.

    Args:
        drink (Drink): The drink being customized.
        data (Mapping): Submitted values (JSON body or form), keys as in the
            module docstring.

    Returns:
        tuple[Selection | None, list[str]]: The selection and no errors, or
            None and the error messages.
    """
    errors = []
    groups = get_applicable_groups(drink)
    selection = Selection()

    for group in SINGLE_CHOICE_GROUPS:
        raw = data.get(group)
        missing = raw is None or raw == ""
        if group not in groups:
            if not missing:
                errors.append(f"{drink.name} has no {GROUP_LABELS[group].lower()} option.")
            continue
        if missing:
            option = default_option(group)
            if option is None:
                errors.append(f"No {GROUP_LABELS[group].lower()} option is in stock.")
        else:
            option = _resolve_option(group, raw, errors)
        setattr(selection, group, option)

    raw_shots = data.get("syrup_shots")
    shots = 0 if raw_shots is None or raw_shots == "" else _parse_int(raw_shots)
    if shots is None or not MIN_SYRUP_SHOTS <= shots <= MAX_SYRUP_SHOTS:
        errors.append(f"Syrup shots must be between {MIN_SYRUP_SHOTS} and {MAX_SYRUP_SHOTS}.")
    elif shots > 0:
        raw_syrup = data.get("syrup")
        if raw_syrup is None or raw_syrup == "":
            errors.append("Please choose a syrup for the syrup shots.")
        else:
            selection.syrup = _resolve_option(CustomizationOption.GROUP_SYRUP, raw_syrup, errors)
            selection.syrup_shots = shots
    # shots == 0: any syrup type sent is ignored

    if errors:
        return None, errors
    return selection, []


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------


def compute_ingredients(drink, selection):
    """
    Purpose: Calculates how much of each ingredient the customized drink
        needs: the recipe with the milk swapped and beans/syrup added.

    Args:
        drink (Drink): The drink.
        selection (Selection): Validated choices.

    Returns:
        dict[Ingredient, float]: Quantity per ingredient (only quantities > 0).
    """
    needs = {item.ingredient: item.quantity for item in drink.recipe_items}

    milk_item = get_recipe_milk_item(drink)
    if milk_item is not None and selection.milk is not None:
        needs.pop(milk_item.ingredient)
        if selection.milk.ingredient is not None:  # "None" leaves the milk out
            needs[selection.milk.ingredient] = (
                needs.get(selection.milk.ingredient, 0) + milk_item.quantity
            )

    if selection.strength is not None and selection.strength.ingredient is not None:
        ingredient = selection.strength.ingredient
        needs[ingredient] = needs.get(ingredient, 0) + selection.strength.ingredient_qty

    if selection.syrup is not None and selection.syrup_shots > 0:
        ingredient = selection.syrup.ingredient
        needs[ingredient] = (
            needs.get(ingredient, 0) + selection.syrup.ingredient_qty * selection.syrup_shots
        )

    return {ingredient: qty for ingredient, qty in needs.items() if qty > 0}


def compute_price(drink, selection):
    """
    Purpose: Calculates the price of the customized drink (FR-13).

    Args:
        drink (Drink): The drink.
        selection (Selection): Validated choices.

    Returns:
        int: Price in euro cents.
    """
    price = drink.base_price_cents
    for option in (selection.milk, selection.strength, selection.temperature):
        if option is not None:
            price += option.price_delta_cents
    if selection.syrup is not None:
        price += selection.syrup.price_delta_cents * selection.syrup_shots
    return price


def compute_nutrition(drink, selection):
    """
    Purpose: Calculates nutrition of the customized drink (FR-13): the
        drink's base values plus the effect of each changed ingredient
        quantity, using the ingredients' values per 100 g/ml.

    Args:
        drink (Drink): The drink.
        selection (Selection): Validated choices.

    Returns:
        dict: Keys calories, sugar_g, fat_g, protein_g, caffeine_mg (never negative).
    """
    values = base_nutrition(drink)
    recipe = {item.ingredient: item.quantity for item in drink.recipe_items}
    needs = compute_ingredients(drink, selection)

    for ingredient in set(recipe) | set(needs):
        change = needs.get(ingredient, 0) - recipe.get(ingredient, 0)
        if change:
            factor = change / 100
            values["calories"] += ingredient.kcal_per_100 * factor
            values["sugar_g"] += ingredient.sugar_per_100 * factor
            values["fat_g"] += ingredient.fat_per_100 * factor
            values["protein_g"] += ingredient.protein_per_100 * factor
            values["caffeine_mg"] += ingredient.caffeine_per_100 * factor

    return {key: max(value, 0) for key, value in values.items()}


def ingredients_snapshot(needs):
    """
    Purpose: Converts ingredient quantities to a JSON-friendly list for the
        transaction record (FR-05, FR-06).

    Args:
        needs (dict[Ingredient, float]): Quantity per ingredient.

    Returns:
        list[dict]: Entries with id, name, quantity and unit, sorted by name.
    """
    return sorted(
        (
            {"id": i.id, "name": i.name, "quantity": round(q, 2), "unit": i.unit}
            for i, q in needs.items()
        ),
        key=lambda entry: entry["name"],
    )


def build_preview(drink, data):
    """
    Purpose: Calculates everything the customization screen displays for the
        current choices (FR-13); the JavaScript only shows this result.

    Args:
        drink (Drink): The drink being customized.
        data (Mapping): Submitted choices.

    Returns:
        dict: JSON-ready preview with keys ok, errors, out_of_stock_option_ids
            and, when ok, price_cents, price, nutrition, summary, can_order
            and stock_problems.
    """
    preview = {
        "ok": False,
        "errors": [],
        "out_of_stock_option_ids": get_out_of_stock_option_ids(drink),
    }
    if not inventory.is_drink_available(drink):
        preview["errors"] = [inventory.unavailable_message(drink)]
        return preview

    selection, errors = parse_selection(drink, data)
    if errors:
        preview["errors"] = errors
        return preview

    price = compute_price(drink, selection)
    shortages = inventory.check_stock(compute_ingredients(drink, selection))
    preview.update({
        "ok": True,
        "price_cents": price,
        "price": format_euro(price),
        "nutrition": format_nutrition(compute_nutrition(drink, selection)),
        "summary": selection.summary(),
        "can_order": not shortages,
        "stock_problems": [inventory.shortage_message(shortages)] if shortages else [],
    })
    return preview
