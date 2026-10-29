"""Tests for feature 7: payments by card, wallet and prepaid card, declines,
never charging twice, refunds after a failed dispense and ending the
session after a purchase (FR-31–35, FR-62, NFR-08, NFR-14, NFR-27)."""

import pytest
from sqlalchemy import select

from app import db
from app.models import (
    Drink,
    Ingredient,
    OfflineQueueItem,
    Payment,
    PrepaidCard,
    ProcessorCharge,
    Transaction,
)
from app.services import auth, card_reader, payments
from tests.conftest import pay

LATTE_PRICE = 350  # guest Latte with default options


def new_order(client, app, drink="Latte"):
    """
    Purpose: Places a pending order with default options and returns its ID.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        drink (str): Drink name.

    Returns:
        int: The new order's ID.
    """
    with app.app_context():
        drink_id = Drink.query.filter_by(name=drink).one().id
    response = client.post(f"/drink/{drink_id}/order", data={})
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def get_order(app, order_id):
    """
    Purpose: Loads an order with its payments for assertions.

    Args:
        app (Flask): The test application.
        order_id (int): Transaction ID.

    Returns:
        tuple[Transaction, list[Payment]]: The order and its payments (detached).
    """
    with app.app_context():
        order = db.session.get(Transaction, order_id)
        attempts = list(order.payments)
        for obj in [order, *attempts]:
            db.session.expunge(obj)
        return order, attempts


def charges(app):
    """
    Purpose: Lists the simulated bank's charge records.

    Args:
        app (Flask): The test application.

    Returns:
        list[tuple[int, str]]: (amount, status) per bank charge.
    """
    with app.app_context():
        return [(c.amount_cents, c.status) for c in ProcessorCharge.query.order_by(ProcessorCharge.id)]


def stock(app, name):
    """
    Purpose: Reads an ingredient's stock level.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.

    Returns:
        float: Stock level.
    """
    with app.app_context():
        return Ingredient.query.filter_by(name=name).one().stock_level


def set_stock(app, name, level, minimum=None):
    """
    Purpose: Sets an ingredient's stock (and optionally minimum) directly.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.
        level (float): New level.
        minimum (float | None): New minimum, or None to keep it.

    Returns:
        None
    """
    with app.app_context():
        ingredient = Ingredient.query.filter_by(name=name).one()
        ingredient.stock_level = level
        if minimum is not None:
            ingredient.min_threshold = minimum
        db.session.commit()


def balance(app, card_number):
    """
    Purpose: Reads a prepaid card's balance.

    Args:
        app (Flask): The test application.
        card_number (str): Prepaid card number.

    Returns:
        int: Balance in cents.
    """
    with app.app_context():
        return PrepaidCard.query.filter_by(card_number=card_number).one().balance_cents


def logged_in_user_id(client):
    """
    Purpose: Reads the logged-in user's ID from the client's session.

    Args:
        client (FlaskClient): The test client.

    Returns:
        int | None: User ID, or None for a guest.
    """
    with client.session_transaction() as sess:
        return sess.get(auth.SESSION_USER_KEY)


# ---------------------------------------------------------------------------
# Payment screen
# ---------------------------------------------------------------------------


def test_order_page_offers_all_payment_methods(seeded_app, seeded_client):
    """
    Purpose: The pending order shows the five payment methods instead of
        the old "Confirm & dispense" button.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    html = seeded_client.get(f"/order/{order_id}").get_data(as_text=True)

    for label in ("Insert card", "Tap card", "Apple Pay", "Google Pay", "Prepaid card"):
        assert label in html
    assert "Confirm &amp; dispense" not in html
    assert f"/order/{order_id}/pay/card_chip" in html


def test_reader_screen_lists_masked_demo_cards(seeded_app, seeded_client):
    """
    Purpose: The simulated reader lists demo cards masked, never the full
        number (NFR-08).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    html = seeded_client.get(f"/order/{order_id}/pay/card_contactless").get_data(as_text=True)

    assert "Visa •••• 4242" in html and "Mastercard •••• 4444" in html
    assert "Declined: insufficient funds" in html
    for card in card_reader.DEMO_CARDS.values():
        assert card["number"] not in html


def test_unknown_method_screen_returns_404(seeded_app, seeded_client):
    """
    Purpose: A made-up method in the URL gives 404.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    assert seeded_client.get(f"/order/{order_id}/pay/bitcoin").status_code == 404


# ---------------------------------------------------------------------------
# Successful payments (FR-31–35)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method, instrument, brand, last4", [
    ("card_chip", "visa-4242", "Visa", "4242"),
    ("card_contactless", "mastercard-4444", "Mastercard", "4444"),
    ("apple_pay", "apple-alice", "Visa", "4242"),
    ("google_pay", "google-bob", "Mastercard", "4444"),
])
def test_card_and_wallet_payments_are_approved_and_dispensed(seeded_app, seeded_client,
                                                             method, instrument, brand, last4):
    """
    Purpose: Approved cards and wallets are charged once, store only brand
        and last 4 digits, and the drink is dispensed straight away.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        method (str): Payment method.
        instrument (str): Demo card or phone.
        brand (str): Expected brand.
        last4 (str): Expected last 4 digits.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    response = pay(seeded_client, order_id, method, instrument, follow_redirects=True)

    order, attempts = get_order(seeded_app, order_id)
    assert order.status == Transaction.STATUS_COMPLETED
    assert (order.payment_method, order.card_brand, order.card_last4) == (method, brand, last4)
    assert [(p.status, p.card_last4) for p in attempts] == [(Payment.STATUS_APPROVED, last4)]
    assert charges(seeded_app) == [(LATTE_PRICE, ProcessorCharge.STATUS_CHARGED)]
    assert stock(seeded_app, "Cups") == 179
    assert f"Paid with {brand} •••• {last4}" in response.get_data(as_text=True)
    assert b"Payment approved" in response.data


def test_prepaid_payment_deducts_balance_and_shows_remaining(seeded_app, seeded_client):
    """
    Purpose: The prepaid card is charged from its local balance and the
        remaining balance is shown (FR-33). No bank is involved.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    response = pay(seeded_client, order_id, "prepaid", "PP-1000-0001", follow_redirects=True)

    assert balance(seeded_app, "PP-1000-0001") == 2500 - LATTE_PRICE
    assert "Remaining balance €21.50" in response.get_data(as_text=True)
    assert charges(seeded_app) == []
    assert get_order(seeded_app, order_id)[0].status == Transaction.STATUS_COMPLETED


# ---------------------------------------------------------------------------
# Declines and errors (NFR-27)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method, instrument, code, text", [
    ("card_chip", "visa-9995", "PAY-101", "declined (insufficient funds)"),
    ("apple_pay", "apple-blocked", "PAY-102", "wallet declined"),
    ("card_contactless", "visa-0119", "PAY-201", "you have not been charged"),
    ("card_chip", "visa-1111", "PAY-303", "could not be read"),
    ("apple_pay", "google-bob", "PAY-303", "could not be read"),  # wrong wallet type
    ("prepaid", "PP-9999-9999", "PAY-302", "not recognised"),
])
def test_refused_payments_show_code_and_keep_order_payable(seeded_app, seeded_client,
                                                           method, instrument, code, text):
    """
    Purpose: Declines and errors show a plain message with an error code,
        charge nothing, dispense nothing, and the order can still be paid
        another way.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        method (str): Payment method.
        instrument (str): Demo card, phone or prepaid card.
        code (str): Expected error code.
        text (str): Expected words in the message.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    refused = pay(seeded_client, order_id, method, instrument, follow_redirects=True)
    html = refused.get_data(as_text=True)

    assert f"(Code {code})" in html
    assert text in html
    assert get_order(seeded_app, order_id)[0].status == Transaction.STATUS_PENDING
    assert charges(seeded_app) == []
    assert stock(seeded_app, "Cups") == 180

    pay(seeded_client, order_id)  # then pays with an approved card
    assert get_order(seeded_app, order_id)[0].status == Transaction.STATUS_COMPLETED
    assert len(charges(seeded_app)) == 1


def test_prepaid_balance_too_low(seeded_app, seeded_client):
    """
    Purpose: Bob's €1.50 card is refused with both amounts in the message
        and its balance stays the same.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    response = pay(seeded_client, order_id, "prepaid", "PP-1000-0002", follow_redirects=True)

    assert b"Your prepaid card balance (\xe2\x82\xac1.50) is too low for this order (\xe2\x82\xac3.50)" in response.data
    assert b"(Code PAY-301)" in response.data
    assert balance(seeded_app, "PP-1000-0002") == 150
    _, attempts = get_order(seeded_app, order_id)
    assert [(p.status, p.error_code) for p in attempts] == [(Payment.STATUS_DECLINED, "PAY-301")]


def test_inactive_prepaid_card_is_refused(seeded_app, seeded_client):
    """
    Purpose: A deactivated prepaid card cannot pay.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    with seeded_app.app_context():
        PrepaidCard.query.filter_by(card_number="PP-1000-0001").one().is_active = False
        db.session.commit()
    order_id = new_order(seeded_client, seeded_app)

    response = pay(seeded_client, order_id, "prepaid", "PP-1000-0001", follow_redirects=True)

    assert b"(Code PAY-302)" in response.data
    assert balance(seeded_app, "PP-1000-0001") == 2500


def test_missing_method_is_refused(seeded_app, seeded_client):
    """
    Purpose: A POST without a valid method changes nothing.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    response = seeded_client.post(f"/order/{order_id}/pay", data={}, follow_redirects=True)

    assert b"Please choose a payment method." in response.data
    assert get_order(seeded_app, order_id)[0].status == Transaction.STATUS_PENDING


def test_every_error_message_has_its_code():
    """
    Purpose: All payment messages follow "plain text (Code XXX-nnn)" (NFR-27).

    Args:
        None

    Returns:
        None
    """
    values = {"balance": "€1", "amount": "€2", "reason": "offline", "limit": "€10"}
    for code in payments.ERRORS:
        message = payments.error_message(code, **values)
        assert message.endswith(f"(Code {code})")
        assert "{" not in message


# ---------------------------------------------------------------------------
# Never charge twice (FR-62)
# ---------------------------------------------------------------------------


def test_pressing_pay_twice_charges_once(seeded_app, seeded_client):
    """
    Purpose: A second press after the order is paid charges nothing and
        dispenses nothing more.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    pay(seeded_client, order_id)
    second = pay(seeded_client, order_id, "card_contactless", "mastercard-4444", follow_redirects=True)

    _, attempts = get_order(seeded_app, order_id)
    assert len(charges(seeded_app)) == 1
    assert [p.status for p in attempts] == [Payment.STATUS_APPROVED]
    assert stock(seeded_app, "Cups") == 179
    assert b"Enjoy your Latte!" in second.data


def test_pay_while_another_request_is_paying_charges_nothing(seeded_app, seeded_client):
    """
    Purpose: If another request already claimed the order (status
        "paying"), this one charges nothing (simulated race).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    with seeded_app.app_context():
        order = db.session.get(Transaction, order_id)
        order.status = Transaction.STATUS_PAYING
        db.session.commit()

        outcome = payments.pay(order, "card_chip", "visa-4242")

        assert outcome.ok is False
        assert Payment.query.count() == 0
    assert charges(seeded_app) == []


def test_processor_never_charges_the_same_key_twice(seeded_app):
    """
    Purpose: Resending a charge with the same idempotency key returns the
        original charge (the bank side of FR-62).

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    from app.services import processor

    with seeded_app.app_context():
        first = processor.charge("card", "key-1", "tok_visa_4242", 350)
        db.session.commit()
        again = processor.charge("card", "key-1", "tok_visa_4242", 350)
        db.session.commit()

    assert first.reference == again.reference
    assert len(charges(seeded_app)) == 1


# ---------------------------------------------------------------------------
# Refund when dispensing fails after payment (FR-66)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method, instrument", [
    ("card_chip", "visa-4242"),
    ("google_pay", "google-bob"),
])
def test_card_and_wallet_refunded_when_dispense_fails(seeded_app, seeded_client, method, instrument):
    """
    Purpose: If the stock re-check fails after payment, the charge is
        refunded and the customer is told so (DSP-101).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        method (str): Payment method.
        instrument (str): Demo card or phone.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    set_stock(seeded_app, "Cups", 0, minimum=0)  # e.g. ran out after confirming

    response = pay(seeded_client, order_id, method, instrument, follow_redirects=True)

    order, attempts = get_order(seeded_app, order_id)
    assert order.status == Transaction.STATUS_CANCELLED
    assert [p.status for p in attempts] == [Payment.STATUS_REFUNDED]
    assert charges(seeded_app) == [(LATTE_PRICE, ProcessorCharge.STATUS_REFUNDED)]
    html = response.get_data(as_text=True)
    assert "Your payment of €3.50 has been refunded. (Code DSP-101)" in html
    assert html.count("has been refunded") == 1  # shown once, not also as a flash
    assert stock(seeded_app, "Coffee beans") == 1800


def test_prepaid_balance_restored_when_dispense_fails(seeded_app, seeded_client):
    """
    Purpose: A failed dispense puts the prepaid money back.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    set_stock(seeded_app, "Coffee beans", 5, minimum=0)

    pay(seeded_client, order_id, "prepaid", "PP-1000-0001")

    order, attempts = get_order(seeded_app, order_id)
    assert order.status == Transaction.STATUS_CANCELLED
    assert balance(seeded_app, "PP-1000-0001") == 2500
    assert [p.status for p in attempts] == [Payment.STATUS_REFUNDED]


def test_offline_payment_voided_when_dispense_fails(seeded_app, seeded_client):
    """
    Purpose: An offline-accepted card payment is voided (never sent) when
        the drink cannot be dispensed.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/connectivity", data={"network_down": "on", "drop_mode": "off"})
    order_id = new_order(seeded_client, seeded_app)
    set_stock(seeded_app, "Cups", 0, minimum=0)

    pay(seeded_client, order_id)
    seeded_client.post("/simulation/connectivity", data={"drop_mode": "off"})  # back online, syncs

    _, attempts = get_order(seeded_app, order_id)
    assert [p.status for p in attempts] == [Payment.STATUS_VOIDED]
    with seeded_app.app_context():
        assert OfflineQueueItem.query.one().status == OfflineQueueItem.STATUS_VOIDED
    assert charges(seeded_app) == []


# ---------------------------------------------------------------------------
# Session ends after a purchase (FR-01–03, NFR-14)
# ---------------------------------------------------------------------------


def test_session_ends_after_successful_purchase(seeded_app, seeded_client, login):
    """
    Purpose: After paying and dispensing, the user is logged out but still
        sees the result page.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    order_id = new_order(seeded_client, seeded_app)

    response = pay(seeded_client, order_id, follow_redirects=True)

    assert logged_in_user_id(seeded_client) is None
    assert b"You have been logged out after your purchase." in response.data
    assert b"Enjoy your Latte!" in response.data
    assert seeded_client.get(f"/order/{order_id}").status_code == 200


def test_session_stays_after_declined_payment(seeded_app, seeded_client, login):
    """
    Purpose: A declined payment does not log the user out.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    order_id = new_order(seeded_client, seeded_app)

    pay(seeded_client, order_id, "card_chip", "visa-9995")

    assert logged_in_user_id(seeded_client) is not None


def test_logged_in_order_keeps_user_and_payment_in_history(seeded_app, seeded_client, login):
    """
    Purpose: The paid order belongs to the user and appears in the history.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    order_id = new_order(seeded_client, seeded_app, "Espresso")
    pay(seeded_client, order_id, "prepaid", "PP-1000-0001")
    login()

    order, _ = get_order(seeded_app, order_id)
    assert order.user_id is not None
    assert b"Espresso" in seeded_client.get("/profile").data


# ---------------------------------------------------------------------------
# NFR-08: no full card numbers stored
# ---------------------------------------------------------------------------


def test_no_full_card_number_is_stored_anywhere(seeded_app, seeded_client):
    """
    Purpose: After online, offline, declined and wallet payments, no table
        contains a full demo card number (NFR-08).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    for method, instrument in (("card_chip", "visa-4242"), ("card_chip", "visa-9995"),
                               ("apple_pay", "apple-blocked"), ("google_pay", "google-bob")):
        pay(seeded_client, new_order(seeded_client, seeded_app), method, instrument)
    seeded_client.post("/simulation/connectivity", data={"network_down": "on", "drop_mode": "off"})
    pay(seeded_client, new_order(seeded_client, seeded_app), "card_contactless", "mastercard-4444")

    numbers = [c["number"] for c in card_reader.DEMO_CARDS.values()]
    numbers += [w["number"] for w in card_reader.DEMO_WALLETS.values()]
    with seeded_app.app_context():
        dump = []
        for table in db.metadata.sorted_tables:
            for row in db.session.execute(select(table)):
                dump.append(" ".join(str(value) for value in row))
    text = "\n".join(dump)
    for number in numbers:
        assert number not in text
