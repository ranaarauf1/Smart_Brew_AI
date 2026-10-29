"""Tests for feature 8: offline mode, single-service failures, the offline
queue and sync, and the dropped connection during payment
(FR-53–55, FR-58, FR-59, FR-61, FR-62, NFR-29, NFR-30)."""

import uuid

import pytest

from app import db
from app.models import (
    Drink,
    ErrorEvent,
    Ingredient,
    OfflineQueueItem,
    Payment,
    ProcessorCharge,
    SystemState,
    Transaction,
)
from app.services import offline, processor
from tests.conftest import ALICE_EMAIL, ALICE_PASSWORD, pay


def switches(client, network=True, card=True, wallet=True, drop="off"):
    """
    Purpose: Posts the Simulation panel connectivity form.

    Args:
        client (FlaskClient): The test client.
        network (bool): False for "Network down".
        card (bool): False for "Card processor down".
        wallet (bool): False for "Wallet processor down".
        drop (str): Drop mode for the next payment.

    Returns:
        TestResponse: The response (redirects followed).
    """
    data = {"drop_mode": drop}
    for field, up in (("network_down", network), ("card_down", card), ("wallet_down", wallet)):
        if not up:
            data[field] = "on"
    return client.post("/simulation/connectivity", data=data, follow_redirects=True)


def new_order(client, app, drink="Latte"):
    """
    Purpose: Places a pending order with default options and returns its ID.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        drink (str): Drink name.

    Returns:
        int: The order ID.
    """
    with app.app_context():
        drink_id = Drink.query.filter_by(name=drink).one().id
    response = client.post(f"/drink/{drink_id}/order", data={})
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def order_status(app, order_id):
    """
    Purpose: Reads an order's status.

    Args:
        app (Flask): The test application.
        order_id (int): Transaction ID.

    Returns:
        str: The status.
    """
    with app.app_context():
        return db.session.get(Transaction, order_id).status


def payment_statuses(app, order_id):
    """
    Purpose: Lists the statuses of an order's payment attempts.

    Args:
        app (Flask): The test application.
        order_id (int): Transaction ID.

    Returns:
        list[str]: Payment statuses, oldest first.
    """
    with app.app_context():
        return [p.status for p in db.session.get(Transaction, order_id).payments]


def bank_charges(app):
    """
    Purpose: Counts the simulated bank's charges.

    Args:
        app (Flask): The test application.

    Returns:
        int: Number of charges.
    """
    with app.app_context():
        return ProcessorCharge.query.count()


def queue_statuses(app):
    """
    Purpose: Lists the statuses of all payment queue items.

    Args:
        app (Flask): The test application.

    Returns:
        list[str]: Item statuses, oldest first.
    """
    with app.app_context():
        return [i.status for i in OfflineQueueItem.query.order_by(OfflineQueueItem.id)]


def cups(app):
    """
    Purpose: Reads the cup stock (one cup per dispensed drink).

    Args:
        app (Flask): The test application.

    Returns:
        float: Cups left.
    """
    with app.app_context():
        return Ingredient.query.filter_by(name="Cups").one().stock_level


def events(app, code):
    """
    Purpose: Lists error events with a code.

    Args:
        app (Flask): The test application.
        code (str): Event code.

    Returns:
        list[ErrorEvent]: Matching events, oldest first.
    """
    with app.app_context():
        found = ErrorEvent.query.filter_by(code=code).order_by(ErrorEvent.id).all()
        for event in found:
            db.session.expunge(event)
        return found


def options(client, order_id):
    """
    Purpose: Returns the order page HTML (shows the payment buttons).

    Args:
        client (FlaskClient): The test client.
        order_id (int): Transaction ID.

    Returns:
        str: Page HTML.
    """
    return client.get(f"/order/{order_id}").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Offline mode and single-service failures (FR-53, FR-58)
# ---------------------------------------------------------------------------


def test_offline_banner_only_when_network_down(seeded_client):
    """
    Purpose: The "Offline mode – basic ordering" banner shows only while
        the network is down.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    online = seeded_client.get("/").get_data(as_text=True)
    offline_page = switches(seeded_client, network=False).get_data(as_text=True)

    assert "Offline mode – basic ordering" not in online
    assert "Offline mode – basic ordering" in offline_page


def test_network_down_disables_wallets_but_keeps_cards_and_prepaid(seeded_app, seeded_client):
    """
    Purpose: Offline, wallets are disabled with PAY-401, cards show the
        offline note and prepaid still works.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    switches(seeded_client, network=False)

    html = options(seeded_client, order_id)
    wallet_screen = seeded_client.get(f"/order/{order_id}/pay/apple_pay", follow_redirects=True)

    assert html.count("pay-option pay-disabled") == 2
    assert "the machine is offline" in html and "(Code PAY-401)" in html
    assert html.count("Offline: accepted up to €10") == 2
    assert f"/order/{order_id}/pay/prepaid" in html
    assert b"(Code PAY-401)" in wallet_screen.data
    assert pay(seeded_client, order_id, "apple_pay", "apple-alice").status_code == 302
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PENDING


def test_card_accepted_offline_is_queued_and_dispensed(seeded_app, seeded_client):
    """
    Purpose: Offline, a card payment is stored in the queue (with a token,
        not a card number) and the drink is dispensed (FR-54).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    order_id = new_order(seeded_client, seeded_app)

    pay(seeded_client, order_id, "card_contactless", "mastercard-4444")

    assert order_status(seeded_app, order_id) == Transaction.STATUS_COMPLETED
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_OFFLINE_QUEUED]
    assert bank_charges(seeded_app) == 0
    with seeded_app.app_context():
        item = OfflineQueueItem.query.one()
        assert item.payload["token"] == "tok_mastercard_4444"
        assert item.payload["card"] == "Mastercard •••• 4444"


def test_prepaid_works_offline(seeded_app, seeded_client):
    """
    Purpose: The prepaid balance is local, so it keeps working offline.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    order_id = new_order(seeded_client, seeded_app)

    pay(seeded_client, order_id, "prepaid", "PP-1000-0001")

    assert order_status(seeded_app, order_id) == Transaction.STATUS_COMPLETED
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_APPROVED]


def test_only_wallet_processor_down(seeded_app, seeded_client):
    """
    Purpose: With only the wallet processor down, wallets are disabled
        ("wallet service is down") while cards are charged online and there
        is no offline banner (FR-58).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    page = switches(seeded_client, wallet=False).get_data(as_text=True)

    html = options(seeded_client, order_id)
    pay(seeded_client, order_id)

    assert "Offline mode" not in page
    assert html.count("pay-option pay-disabled") == 2
    assert "the wallet service is down" in html
    assert "Offline: accepted" not in html
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_APPROVED]
    assert bank_charges(seeded_app) == 1


def test_only_card_processor_down(seeded_app, seeded_client):
    """
    Purpose: With only the card processor down, cards are accepted offline
        and queued while wallets still work online (FR-58).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, card=False)
    # A kiosk session handles one order at a time, so pay each before the next
    card_order = new_order(seeded_client, seeded_app)
    html = options(seeded_client, card_order)
    pay(seeded_client, card_order)
    wallet_order = new_order(seeded_client, seeded_app)
    pay(seeded_client, wallet_order, "google_pay", "google-bob")

    assert "pay-disabled" not in html
    assert "Offline: accepted up to €10" in html
    assert payment_statuses(seeded_app, card_order) == [Payment.STATUS_OFFLINE_QUEUED]
    assert payment_statuses(seeded_app, wallet_order) == [Payment.STATUS_APPROVED]


def test_offline_card_limit_is_10_euro(seeded_app, seeded_client):
    """
    Purpose: Offline card payments above €10 are refused (PAY-402); the
        prepaid card still works.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    with seeded_app.app_context():
        Drink.query.filter_by(name="Latte").one().base_price_cents = 1200
        db.session.commit()
    order_id = new_order(seeded_client, seeded_app)
    switches(seeded_client, network=False)

    html = options(seeded_client, order_id)
    refused = pay(seeded_client, order_id, follow_redirects=True)

    assert "card payments are accepted up to €10.00" in html
    assert b"(Code PAY-402)" in refused.data
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PENDING
    pay(seeded_client, order_id, "prepaid", "PP-1000-0001")
    assert order_status(seeded_app, order_id) == Transaction.STATUS_COMPLETED


def test_full_queue_refuses_offline_cards(seeded_app, seeded_client, monkeypatch):
    """
    Purpose: When the queue is full, offline card payments are refused
        (PAY-403) instead of being lost.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        monkeypatch (MonkeyPatch): Pytest fixture to lower the capacity.

    Returns:
        None
    """
    monkeypatch.setattr(offline, "QUEUE_CAPACITY", 1)
    switches(seeded_client, network=False)
    pay(seeded_client, new_order(seeded_client, seeded_app))
    order_id = new_order(seeded_client, seeded_app)

    refused = pay(seeded_client, order_id, follow_redirects=True)

    assert b"(Code PAY-403)" in refused.data
    assert queue_statuses(seeded_app) == [OfflineQueueItem.STATUS_QUEUED]


def test_basic_ordering_works_offline(seeded_app, seeded_client):
    """
    Purpose: Login, menu (with recommendations), customization and recipes
        keep working while offline.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    with seeded_app.app_context():
        latte = Drink.query.filter_by(name="Latte").one().id

    login = seeded_client.post("/login", data={"email": ALICE_EMAIL, "password": ALICE_PASSWORD})
    menu = seeded_client.get("/menu").get_data(as_text=True)

    assert login.status_code == 302
    assert 'class="top-badge"' in menu
    assert "Weather unavailable" in menu
    assert seeded_client.get(f"/drink/{latte}/customize").status_code == 200
    assert seeded_client.get("/recipes").status_code == 200
    assert seeded_client.post(f"/drink/{latte}/order", data={}).status_code == 302


# ---------------------------------------------------------------------------
# Sync when back online (FR-55, NFR-29, NFR-30)
# ---------------------------------------------------------------------------


def test_queue_is_sent_automatically_on_reconnect(seeded_app, seeded_client):
    """
    Purpose: Coming back online sends every queued payment once and the
        result appears on the Simulation panel.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    first = new_order(seeded_client, seeded_app)
    pay(seeded_client, first)
    second = new_order(seeded_client, seeded_app)
    pay(seeded_client, second, "card_contactless", "mastercard-4444")

    back = switches(seeded_client).get_data(as_text=True)
    panel = seeded_client.get("/simulation/").get_data(as_text=True)

    assert queue_statuses(seeded_app) == [OfflineQueueItem.STATUS_SYNCED] * 2
    assert payment_statuses(seeded_app, first) == [Payment.STATUS_SYNCED]
    assert bank_charges(seeded_app) == 2
    assert "Offline sync: Sent 2: 2 approved, 0 declined" in back
    assert "Sent 2: 2 approved" in panel


def test_offline_card_declined_on_sync_is_recorded_as_loss(seeded_app, seeded_client):
    """
    Purpose: An offline payment the bank declines later is marked
        sync_declined with an error event; the order stays completed.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    order_id = new_order(seeded_client, seeded_app)
    pay(seeded_client, order_id, "card_chip", "visa-9995")

    back = switches(seeded_client).get_data(as_text=True)

    assert order_status(seeded_app, order_id) == Transaction.STATUS_COMPLETED
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_SYNC_DECLINED]
    assert queue_statuses(seeded_app) == [OfflineQueueItem.STATUS_DECLINED]
    assert "1 declined (€3.50 lost)" in back
    loss = events(seeded_app, offline.CODE_OFFLINE_DECLINED)
    assert len(loss) == 1 and "Visa •••• 9995" in loss[0].message


def test_processor_error_keeps_item_for_retry(seeded_app, seeded_client):
    """
    Purpose: A processor error during sync keeps the payment queued, with
        the attempt counted.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    pay(seeded_client, new_order(seeded_client, seeded_app), "card_chip", "visa-0119")

    switches(seeded_client)

    with seeded_app.app_context():
        item = OfflineQueueItem.query.one()
        assert (item.status, item.attempts, item.last_error) == ("queued", 1, "Processor error")


def test_connection_lost_during_sync_loses_nothing(seeded_app, seeded_client, monkeypatch):
    """
    Purpose: If the processor becomes unreachable half-way through a sync,
        the rest stay queued and the next sync sends them (NFR-29).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        monkeypatch (MonkeyPatch): Pytest fixture to break the processor.

    Returns:
        None
    """
    switches(seeded_client, network=False)
    for _ in range(3):
        pay(seeded_client, new_order(seeded_client, seeded_app))

    real_charge = processor.charge
    calls = {"n": 0}

    def flaky_charge(*args, **kwargs):
        """
        Purpose: Lets the first charge through, then fails like a dropped line.

        Args:
            *args: Arguments for processor.charge.
            **kwargs: Keyword arguments for processor.charge.

        Returns:
            ChargeResult: The real result for the first call.
        """
        calls["n"] += 1
        if calls["n"] > 1:
            raise processor.ProcessorUnavailableError()
        return real_charge(*args, **kwargs)

    monkeypatch.setattr(processor, "charge", flaky_charge)
    switches(seeded_client)
    assert queue_statuses(seeded_app) == ["synced", "queued", "queued"]

    monkeypatch.setattr(processor, "charge", real_charge)
    seeded_client.post("/simulation/sync")
    assert queue_statuses(seeded_app) == ["synced"] * 3
    assert bank_charges(seeded_app) == 3


def test_queue_holds_and_syncs_500_payments(seeded_app):
    """
    Purpose: 500 queued payments are all kept and all sent (NFR-29, NFR-30).

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        latte = Drink.query.filter_by(name="Latte").one()
        for _ in range(500):
            order = Transaction(drink=latte, amount_cents=350, status=Transaction.STATUS_COMPLETED,
                                idempotency_key=uuid.uuid4().hex, customizations={})
            db.session.add(order)
            db.session.flush()
            payment = Payment(transaction_id=order.id, method=Payment.METHOD_CHIP, amount_cents=350,
                              status=Payment.STATUS_OFFLINE_QUEUED, card_brand="Visa",
                              card_last4="4242", idempotency_key=uuid.uuid4().hex)
            db.session.add(payment)
            db.session.flush()
            offline.enqueue_payment(payment, "tok_visa_4242")
        db.session.commit()
        assert offline.count_queued() == 500
        assert offline.queue_has_room()

        summary = offline.run_sync()

        assert offline.get_queue_counts()["synced"] == 500
        assert offline.count_queued() == 0
        assert ProcessorCharge.query.count() == 500
    assert summary.startswith("Sent 500: 500 approved")


# ---------------------------------------------------------------------------
# Dropped connection during payment (FR-62)
# ---------------------------------------------------------------------------


def test_drop_before_charge_charges_nothing(seeded_app, seeded_client):
    """
    Purpose: If the line drops before the bank gets the payment, nothing is
        charged or dispensed; paying again is blocked until the result is
        checked; after reconnecting the order can be paid normally, once.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    switches(seeded_client, drop="before_charge")

    dropped = pay(seeded_client, order_id, follow_redirects=True)
    blocked = pay(seeded_client, order_id, "prepaid", "PP-1000-0001", follow_redirects=True)

    assert b"(Code PAY-501)" in dropped.data and b"Offline mode" in dropped.data
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PAYMENT_UNKNOWN
    assert b"(Code PAY-502)" in blocked.data
    assert bank_charges(seeded_app) == 0 and cups(seeded_app) == 180

    back = switches(seeded_client).get_data(as_text=True)
    assert "0 charged once, 1 not charged" in back
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PENDING
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_FAILED]

    pay(seeded_client, order_id)
    assert order_status(seeded_app, order_id) == Transaction.STATUS_COMPLETED
    assert bank_charges(seeded_app) == 1


def test_drop_after_charge_charges_once_and_dispenses_after_check(seeded_app, seeded_client):
    """
    Purpose: If the bank charged but the answer was lost, nothing is
        dispensed until reconnecting confirms the charge; then the order is
        paid and can be dispensed, and the customer was charged once.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    switches(seeded_client, drop="after_charge")

    pay(seeded_client, order_id)
    pay(seeded_client, order_id)  # impatient second press

    assert order_status(seeded_app, order_id) == Transaction.STATUS_PAYMENT_UNKNOWN
    assert bank_charges(seeded_app) == 1
    assert cups(seeded_app) == 180  # nothing dispensed without a confirmed payment

    switches(seeded_client)
    page = seeded_client.get(f"/order/{order_id}").get_data(as_text=True)
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PAID
    assert payment_statuses(seeded_app, order_id) == [Payment.STATUS_APPROVED]
    assert "Dispense my drink" in page

    done = seeded_client.post(f"/order/{order_id}/dispense", follow_redirects=True)
    seeded_client.post(f"/order/{order_id}/dispense")  # second press does nothing
    assert b"Enjoy your Latte!" in done.data
    assert cups(seeded_app) == 179
    assert bank_charges(seeded_app) == 1


def test_drop_mode_applies_to_one_payment_only(seeded_app, seeded_client):
    """
    Purpose: The drop switch is used up by one online payment; prepaid
        payments do not use it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, drop="before_charge")

    pay(seeded_client, new_order(seeded_client, seeded_app), "prepaid", "PP-1000-0001")
    with seeded_app.app_context():
        assert SystemState.get().drop_next_payment == "before_charge"

    pay(seeded_client, new_order(seeded_client, seeded_app))
    with seeded_app.app_context():
        assert SystemState.get().drop_next_payment == "off"


def test_unpaid_order_is_never_dispensed(seeded_app, seeded_client):
    """
    Purpose: The dispense endpoint refuses pending orders (FR-62).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)

    seeded_client.post(f"/order/{order_id}/dispense")

    assert order_status(seeded_app, order_id) == Transaction.STATUS_PENDING
    assert cups(seeded_app) == 180


def test_unknown_payment_waits_for_its_processor(seeded_app, seeded_client):
    """
    Purpose: A dropped card payment is only checked when the card processor
        can be reached, not just the network.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    switches(seeded_client, drop="after_charge")
    pay(seeded_client, order_id)

    switches(seeded_client, card=False)  # network back, card processor still down
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PAYMENT_UNKNOWN

    switches(seeded_client)
    assert order_status(seeded_app, order_id) == Transaction.STATUS_PAID


# ---------------------------------------------------------------------------
# Events and Simulation panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field, code", [
    ("network", "NETWORK"), ("card", "CARD_PROCESSOR"), ("wallet", "WALLET_PROCESSOR"),
])
def test_connectivity_changes_are_logged(seeded_app, seeded_client, field, code):
    """
    Purpose: Going down opens a warning event; coming back resolves it and
        logs an info event. Saving without a change logs nothing.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        field (str): Switch to flip.
        code (str): Event code prefix.

    Returns:
        None
    """
    switches(seeded_client, **{field: False})
    switches(seeded_client, **{field: False})  # unchanged: no extra event
    down = events(seeded_app, f"{code}_DOWN")
    assert len(down) == 1 and down[0].resolved_at is None

    switches(seeded_client)
    assert events(seeded_app, f"{code}_DOWN")[0].resolved_at is not None
    assert len(events(seeded_app, f"{code}_RESTORED")) == 1


def test_weather_switch_is_logged(seeded_app, seeded_client):
    """
    Purpose: The weather API switch also logs its changes.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/weather", data={
        "mode": "auto", "condition": "cloudy", "temperature": "12", "api_down": "on",
    })

    assert len(events(seeded_app, "WEATHER_DOWN")) == 1


def test_drop_is_logged(seeded_app, seeded_client):
    """
    Purpose: A dropped connection is logged and takes the network down.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    switches(seeded_client, drop="before_charge")

    pay(seeded_client, new_order(seeded_client, seeded_app))

    assert len(events(seeded_app, "CONNECTION_DROPPED")) == 1
    assert len(events(seeded_app, "NETWORK_DOWN")) == 1


def test_simulation_panel_shows_connectivity_and_queue(seeded_client):
    """
    Purpose: The panel has the switches, the drop option and the queue
        counts with a "Sync now" button.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    html = seeded_client.get("/simulation/").get_data(as_text=True)

    for text in ('id="connectivity"', 'name="network_down"', 'name="card_down"',
                 'name="wallet_down"', 'name="drop_mode"', 'id="queue"', "Sync now",
                 "After the bank charges (answer lost)"):
        assert text in html


def test_invalid_drop_mode_is_refused(seeded_app, seeded_client):
    """
    Purpose: An unknown drop mode is refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = switches(seeded_client, drop="sometimes")

    assert b"Unknown drop mode." in response.data
    with seeded_app.app_context():
        assert SystemState.get().drop_next_payment == "off"
