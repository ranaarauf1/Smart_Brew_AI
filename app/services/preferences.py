"""Preference profile learned from order history (feature 6): preferred milk,
strength and temperature, preselected on the customization screen."""

from collections import Counter

from app import db
from app.models import CustomizationOption, Transaction, UserPreference
from app.services import customization

HISTORY_ORDER_LIMIT = 30  # last N completed orders; also used by recommendations.py

# Selection key -> UserPreference column
PREFERENCE_FIELDS = {
    CustomizationOption.GROUP_MILK: "preferred_milk_option_id",
    CustomizationOption.GROUP_STRENGTH: "preferred_strength_option_id",
    CustomizationOption.GROUP_TEMPERATURE: "preferred_temperature_option_id",
}


def get_recent_completed_orders(user, limit=HISTORY_ORDER_LIMIT):
    """
    Purpose: Loads the user's most recent completed orders, newest first.

    Args:
        user (User): The user.
        limit (int): Maximum number of orders.

    Returns:
        list[Transaction]: Completed orders, newest first.
    """
    return (
        Transaction.query.filter_by(user_id=user.id, status=Transaction.STATUS_COMPLETED)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .limit(limit)
        .all()
    )


def most_frequent(values_newest_first):
    """
    Purpose: Finds the most frequent value; a tie goes to the value chosen
        most recently.

    Args:
        values_newest_first (list): Values ordered newest first; None values
            are ignored.

    Returns:
        object | None: The most frequent value, or None if there is none.
    """
    values = [v for v in values_newest_first if v is not None]
    if not values:
        return None
    counts = Counter(values)
    best = max(counts.values())
    # First value in newest-first order with the highest count wins the tie
    return next(v for v in values if counts[v] == best)


def update_preferences(user):
    """
    Purpose: Recalculates the user's preference profile from the most
        frequent milk, strength and temperature in their last 30 completed
        orders. Groups never chosen stay empty.

    Args:
        user (User): The user whose profile is updated.

    Returns:
        UserPreference: The updated profile (committed).
    """
    if user.preference is None:
        user.preference = UserPreference()
    orders = get_recent_completed_orders(user)

    for group, field in PREFERENCE_FIELDS.items():
        values = [order.customizations.get("selection", {}).get(group) for order in orders]
        setattr(user.preference, field, most_frequent(values))

    db.session.commit()
    return user.preference


def apply_preferences(selection, user, drink):
    """
    Purpose: Preselects the user's preferred options on top of the drink's
        defaults, where the drink offers that group and the option is in
        stock. Guests and users without a profile keep the defaults.

    Args:
        selection (Selection): Default selection for the drink (changed in place).
        user (User | None): The logged-in user, or None.
        drink (Drink): The drink being customized.

    Returns:
        Selection: The same selection, with preferences applied.
    """
    if user is None or user.preference is None:
        return selection

    groups = customization.get_applicable_groups(drink)
    for group, field in PREFERENCE_FIELDS.items():
        option_id = getattr(user.preference, field)
        if group not in groups or option_id is None:
            continue
        option = db.session.get(CustomizationOption, option_id)
        if option is not None and option.group == group and customization.is_option_in_stock(option):
            setattr(selection, group, option)
    return selection
