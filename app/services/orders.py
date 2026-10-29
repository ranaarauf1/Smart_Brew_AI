"""Order flow: confirm a customized drink, then dispense it once paid
(feature 4: FR-14, FR-27, FR-66, FR-67; history FR-05, FR-06).

Status flow (payment steps in services/payments.py)::

    pending --pay--> paid --dispense--> dispensing --> completed
                                                  \\-> cancelled (stock re-check failed)
"""

import uuid

from sqlalchemy import update

from app import db
from app.models import Transaction, utcnow
from app.services import customization, inventory, preferences


class OrderError(Exception):
    """Raised when an order cannot be placed; the message is shown to the user."""


def validate_order(drink, data):
    """
    Purpose: Runs every check an order must pass: drink available, valid
        options in stock, and stock covering the customized quantities
        (FR-14, FR-65), e.g. the extra beans of an extra shot.

    Args:
        drink (Drink): The drink being ordered.
        data (Mapping): Submitted choices (selection dict or form).

    Returns:
        tuple[Selection, dict[Ingredient, float]]: The selection and the
            quantity needed per ingredient.

    Raises:
        OrderError: With a clear message if any check fails.
    """
    if not inventory.is_drink_available(drink):
        raise OrderError(inventory.unavailable_message(drink))

    selection, errors = customization.parse_selection(drink, data)
    if errors:
        raise OrderError(" ".join(errors))

    needs = customization.compute_ingredients(drink, selection)
    shortages = inventory.check_stock(needs)
    if shortages:
        raise OrderError(inventory.shortage_message(shortages))
    return selection, needs


def build_order_details(drink, selection, needs):
    """
    Purpose: Builds the customization details stored with the transaction,
        including names, so the history stays readable even if options
        change later (FR-05, FR-06).

    Args:
        drink (Drink): The drink.
        selection (Selection): Validated choices.
        needs (dict[Ingredient, float]): Quantity per ingredient.

    Returns:
        dict: JSON-ready details with keys selection, summary, options,
            ingredients and nutrition.
    """
    nutrition = customization.compute_nutrition(drink, selection)
    return {
        "selection": selection.to_ids(),
        "summary": selection.summary(),
        "options": {
            "milk": selection.milk.name if selection.milk else None,
            "strength": selection.strength.name if selection.strength else None,
            "temperature": selection.temperature.name if selection.temperature else None,
            "syrup": selection.syrup.name if selection.syrup else None,
            "syrup_shots": selection.syrup_shots,
        },
        "ingredients": customization.ingredients_snapshot(needs),
        "nutrition": {key: round(value, 1) for key, value in nutrition.items()},
    }


def create_pending_order(user, drink, data):
    """
    Purpose: Checks a customized order and saves it as a pending transaction.
        Nothing is deducted yet.

    Args:
        user (User | None): The logged-in user, or None for a guest.
        drink (Drink): The drink being ordered.
        data (Mapping): Submitted choices.

    Returns:
        Transaction: The new pending transaction.

    Raises:
        OrderError: If the order fails a check (see validate_order).
    """
    selection, needs = validate_order(drink, data)
    order = Transaction(
        user_id=user.id if user else None,
        drink=drink,
        customizations=build_order_details(drink, selection, needs),
        amount_cents=customization.compute_price(drink, selection),
        status=Transaction.STATUS_PENDING,
        idempotency_key=uuid.uuid4().hex,
    )
    db.session.add(order)
    db.session.commit()
    return order


def get_order(order_id):
    """
    Purpose: Loads an order by ID.

    Args:
        order_id (int): Transaction ID.

    Returns:
        Transaction | None: The order, or None if not found.
    """
    return db.session.get(Transaction, order_id)


def can_access_order(order, user, session_order_id):
    """
    Purpose: Checks that the current person placed this order, so nobody can
        view or dispense someone else's drink.

    Args:
        order (Transaction): The order.
        user (User | None): The logged-in user, or None for a guest.
        session_order_id (int | None): Order ID remembered in this session.

    Returns:
        bool: True if the order belongs to this session or user.
    """
    if session_order_id is not None and order.id == session_order_id:
        return True
    return user is not None and order.user_id == user.id


def record_successful_payment(order, payment):
    """
    Purpose: Marks an order as paid and copies the payment summary onto it
        for the history (NFR-08: brand and last 4 digits only). The caller
        commits.

    Args:
        order (Transaction): The order.
        payment (Payment): Its approved or offline-accepted payment.

    Returns:
        None
    """
    order.status = Transaction.STATUS_PAID
    order.payment_method = payment.method
    order.card_brand = payment.card_brand
    order.card_last4 = payment.card_last4
    order.prepaid_card_id = payment.prepaid_card_id


def dispense_order(order):
    """
    Purpose: Dispenses a paid order: re-checks stock right before
        dispensing (FR-66), deducts the ingredients (FR-27), records
        low-threshold events (FR-67) and completes the transaction.
        If the re-check fails the order is cancelled and nothing is
        deducted; the caller refunds the payment (services/payments.py).

    Args:
        order (Transaction): A paid order.

    Returns:
        Transaction: The order with status completed or cancelled. An order
            that is not paid (e.g. a double tap) is returned unchanged, so
            nothing is dispensed twice or without payment (FR-62).
    """
    # Claim the order: only one request can move it from paid to dispensing
    claimed = db.session.execute(
        update(Transaction)
        .where(Transaction.id == order.id, Transaction.status == Transaction.STATUS_PAID)
        .values(status=Transaction.STATUS_DISPENSING)
    )
    db.session.commit()
    db.session.refresh(order)
    if claimed.rowcount == 0:
        return order

    try:
        # FR-66: same checks as at confirmation, against the current stock
        _, needs = validate_order(order.drink, order.customizations["selection"])
        levels_before = inventory.deduct_stock(needs)
    except (OrderError, inventory.InsufficientStockError) as error:
        order.status = Transaction.STATUS_CANCELLED
        order.failure_reason = str(error)[:255]
        db.session.commit()
        return order

    inventory.record_threshold_crossings(levels_before, needs.keys())
    order.status = Transaction.STATUS_COMPLETED
    order.completed_at = utcnow()
    db.session.commit()

    if order.user is not None:
        # Feature 6: learn preferred milk/strength/temperature from history
        preferences.update_preferences(order.user)
    return order
