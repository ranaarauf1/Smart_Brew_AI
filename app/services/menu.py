"""Menu browsing: categories, drinks and nutrition (feature 2, FR-11, FR-12)."""

from app import db
from app.models import Category, Drink


def get_categories():
    """
    Purpose: Lists the menu categories in display order (FR-11).

    Args:
        None

    Returns:
        list[Category]: All categories sorted by sort_order.
    """
    return Category.query.order_by(Category.sort_order, Category.name).all()


def get_drinks_for_category(category_id):
    """
    Purpose: Lists the active drinks of one category (FR-11).

    Args:
        category_id (int): ID of the category.

    Returns:
        list[Drink]: Active drinks in the category, sorted by name.
    """
    return (
        Drink.query.filter_by(category_id=category_id, is_active=True)
        .order_by(Drink.name)
        .all()
    )


def get_drink(drink_id):
    """
    Purpose: Loads a drink for its detail page (FR-12).

    Args:
        drink_id (int): ID of the drink.

    Returns:
        Drink | None: The drink, or None if it does not exist or is inactive.
    """
    drink = db.session.get(Drink, drink_id)
    if drink is None or not drink.is_active:
        return None
    return drink


def base_nutrition(drink):
    """
    Purpose: Returns the nutrition values of a drink as served by default.

    Args:
        drink (Drink): The drink.

    Returns:
        dict: Keys calories, sugar_g, fat_g, protein_g, caffeine_mg.
    """
    return {
        "calories": drink.calories,
        "sugar_g": drink.sugar_g,
        "fat_g": drink.fat_g,
        "protein_g": drink.protein_g,
        "caffeine_mg": drink.caffeine_mg,
    }


def format_nutrition(values):
    """
    Purpose: Turns nutrition values into labelled display rows (FR-12).

    Args:
        values (dict): Keys calories, sugar_g, fat_g, protein_g, caffeine_mg.

    Returns:
        list[tuple[str, str]]: (label, formatted value) pairs in display order.
    """
    return [
        ("Calories", f"{round(values['calories'])} kcal"),
        ("Sugar", f"{round(values['sugar_g'], 1):g} g"),
        ("Fat", f"{round(values['fat_g'], 1):g} g"),
        ("Protein", f"{round(values['protein_g'], 1):g} g"),
        ("Caffeine", f"{round(values['caffeine_mg'])} mg"),
    ]


def get_nutrition(drink):
    """
    Purpose: Collects the nutrition values shown on the detail page (FR-12).

    Args:
        drink (Drink): The drink.

    Returns:
        list[tuple[str, str]]: (label, formatted value) pairs in display order.
    """
    return format_nutrition(base_nutrition(drink))
