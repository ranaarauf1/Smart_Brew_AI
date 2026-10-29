"""Saved recipes and one-touch ordering (feature 5: FR-07–10, FR-15, NFR-24).

A recipe stores a drink and a selection dict of option IDs (see
services/customization.py), so it always uses current prices and stock.
"""

from sqlalchemy import func

from app import db
from app.models import CustomizationOption, SavedRecipe
from app.services import customization, orders

MAX_NAME_LENGTH = 40


class RecipeError(Exception):
    """Raised when a recipe cannot be saved or changed; the message is shown."""


def validate_name(user, name, exclude_recipe_id=None):
    """
    Purpose: Checks a recipe name: 1–40 characters after trimming and unique
        among the user's recipes, ignoring upper/lower case.

    Args:
        user (User): The recipe owner.
        name (str | None): Name as typed.
        exclude_recipe_id (int | None): Recipe being renamed (may keep its name).

    Returns:
        str: The trimmed name.

    Raises:
        RecipeError: If the name is empty, too long or already used.
    """
    name = (name or "").strip()
    if not name:
        raise RecipeError("Please give your recipe a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise RecipeError(f"Recipe names can be at most {MAX_NAME_LENGTH} characters.")

    query = SavedRecipe.query.filter(
        SavedRecipe.user_id == user.id, func.lower(SavedRecipe.name) == name.lower()
    )
    if exclude_recipe_id is not None:
        query = query.filter(SavedRecipe.id != exclude_recipe_id)
    if query.first() is not None:
        raise RecipeError(f"You already have a recipe called \"{name}\".")
    return name


def validate_options(drink, data):
    """
    Purpose: Checks the options of a recipe with the same rules as the
        customization screen (FR-13, FR-65).

    Args:
        drink (Drink): The recipe's drink.
        data (Mapping): Submitted choices.

    Returns:
        dict: Selection dict of option IDs to store.

    Raises:
        RecipeError: If an option is invalid or out of stock.
    """
    selection, errors = customization.parse_selection(drink, data)
    if errors:
        raise RecipeError(" ".join(errors))
    return selection.to_ids()


def get_user_recipes(user):
    """
    Purpose: Lists the user's saved recipes alphabetically (FR-08).

    Args:
        user (User): The logged-in user.

    Returns:
        list[SavedRecipe]: The recipes.
    """
    return (
        SavedRecipe.query.filter_by(user_id=user.id)
        .order_by(func.lower(SavedRecipe.name))
        .all()
    )


def get_recipe(user, recipe_id):
    """
    Purpose: Loads one of the user's own recipes.

    Args:
        user (User): The logged-in user.
        recipe_id (int): Recipe ID.

    Returns:
        SavedRecipe | None: The recipe, or None if missing or owned by
            someone else.
    """
    recipe = db.session.get(SavedRecipe, recipe_id)
    if recipe is None or recipe.user_id != user.id:
        return None
    return recipe


def save_recipe(user, drink, name, data):
    """
    Purpose: Saves the current customization as a named recipe (FR-07).

    Args:
        user (User): The logged-in user.
        drink (Drink): The drink being customized.
        name (str | None): Recipe name as typed.
        data (Mapping): Submitted choices.

    Returns:
        SavedRecipe: The new recipe.

    Raises:
        RecipeError: If the name or options are invalid.
    """
    name = validate_name(user, name)
    recipe = SavedRecipe(
        user_id=user.id, drink=drink, name=name, customizations=validate_options(drink, data)
    )
    db.session.add(recipe)
    db.session.commit()
    return recipe


def update_recipe_options(recipe, data):
    """
    Purpose: Replaces a recipe's options after editing them (FR-09).

    Args:
        recipe (SavedRecipe): The recipe.
        data (Mapping): Submitted choices.

    Returns:
        SavedRecipe: The updated recipe.

    Raises:
        RecipeError: If an option is invalid or out of stock.
    """
    recipe.customizations = validate_options(recipe.drink, data)
    db.session.commit()
    return recipe


def rename_recipe(recipe, name):
    """
    Purpose: Renames a recipe (FR-09).

    Args:
        recipe (SavedRecipe): The recipe.
        name (str | None): New name as typed.

    Returns:
        SavedRecipe: The renamed recipe.

    Raises:
        RecipeError: If the name is invalid or already used.
    """
    recipe.name = validate_name(recipe.user, name, exclude_recipe_id=recipe.id)
    db.session.commit()
    return recipe


def delete_recipe(recipe):
    """
    Purpose: Deletes a recipe (FR-10).

    Args:
        recipe (SavedRecipe): The recipe.

    Returns:
        None
    """
    db.session.delete(recipe)
    db.session.commit()


def load_saved_selection(recipe):
    """
    Purpose: Rebuilds the stored selection without stock checks, to describe
        a recipe even when it cannot be ordered right now.

    Args:
        recipe (SavedRecipe): The recipe.

    Returns:
        Selection: The stored options (missing options become None).
    """
    data = recipe.customizations or {}
    selection = customization.Selection()
    for group in customization.SINGLE_CHOICE_GROUPS + (CustomizationOption.GROUP_SYRUP,):
        option_id = data.get(group)
        if option_id is not None:
            setattr(selection, group, db.session.get(CustomizationOption, option_id))
    if selection.syrup is not None:
        selection.syrup_shots = data.get("syrup_shots") or 0
    return selection


def get_recipe_status(recipe):
    """
    Purpose: Describes a recipe for "My recipes": options summary, current
        price, and whether it can be ordered now, with the reason if not
        (FR-15, e.g. "Almond milk is out of stock.").

    Args:
        recipe (SavedRecipe): The recipe.

    Returns:
        dict: Keys recipe, summary, price_cents, available (bool) and
            reason (str | None).
    """
    selection = load_saved_selection(recipe)
    status = {
        "recipe": recipe,
        "summary": selection.summary(),
        "price_cents": customization.compute_price(recipe.drink, selection),
        "available": True,
        "reason": None,
    }
    if not recipe.drink.is_active:
        status.update(available=False, reason=f"{recipe.drink.name} is no longer on the menu.")
        return status
    try:
        # Same checks as a normal order: availability, options, stock quantities
        orders.validate_order(recipe.drink, recipe.customizations)
    except orders.OrderError as error:
        status.update(available=False, reason=str(error))
    return status


def order_recipe(user, recipe):
    """
    Purpose: One-touch ordering: creates the order straight from the recipe,
        with no further selection (FR-15, NFR-24).

    Args:
        user (User): The logged-in user.
        recipe (SavedRecipe): The recipe.

    Returns:
        Transaction: The new pending order.

    Raises:
        RecipeError: If the drink or an option is not available.
    """
    if not recipe.drink.is_active:
        raise RecipeError(f"{recipe.drink.name} is no longer on the menu.")
    try:
        return orders.create_pending_order(user, recipe.drink, recipe.customizations)
    except orders.OrderError as error:
        raise RecipeError(str(error)) from None
