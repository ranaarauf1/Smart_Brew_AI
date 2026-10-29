"""Tests for feature 9: QR and email receipts, also after logout and while
offline (FR-36, FR-37, FR-60)."""

import base64
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path

from app import DATA_DIR, db
from app.models import Drink, Ingredient, OfflineQueueItem, Transaction
from app.services import card_reader, receipts
from tests.conftest import ALICE_EMAIL, pay


def new_order(client, app, drink="Latte"):
    """
    Purpose: Places a pending order with default options.

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


def bought(client, app, method="card_chip", instrument="visa-4242"):
    """
    Purpose: Places and pays a Latte order.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        method (str): Payment method.
        instrument (str): Demo card or prepaid card.

    Returns:
        int: The completed order's ID.
    """
    order_id = new_order(client, app)
    pay(client, order_id, method, instrument)
    return order_id


def network(client, up):
    """
    Purpose: Switches the simulated network on or off.

    Args:
        client (FlaskClient): The test client.
        up (bool): True for online.

    Returns:
        TestResponse: The panel response (redirects followed).
    """
    data = {"drop_mode": "off"}
    if not up:
        data["network_down"] = "on"
    return client.post("/simulation/connectivity", data=data, follow_redirects=True)


def outbox_files(app):
    """
    Purpose: Lists the .eml files in the test outbox.

    Args:
        app (Flask): The test application.

    Returns:
        list[Path]: The files.
    """
    folder = Path(app.config["OUTBOX_DIR"])
    return sorted(folder.glob("*.eml")) if folder.exists() else []


def email_items(app):
    """
    Purpose: Lists the email queue items as (to, status) pairs.

    Args:
        app (Flask): The test application.

    Returns:
        list[tuple[str, str]]: One pair per email, oldest first.
    """
    with app.app_context():
        items = OfflineQueueItem.query.filter_by(kind=OfflineQueueItem.KIND_EMAIL).order_by(OfflineQueueItem.id)
        return [(i.payload["to"], i.status) for i in items]


def token_of(app, order_id):
    """
    Purpose: Reads an order's receipt token.

    Args:
        app (Flask): The test application.
        order_id (int): Transaction ID.

    Returns:
        str | None: The token.
    """
    with app.app_context():
        return db.session.get(Transaction, order_id).receipt_token


# ---------------------------------------------------------------------------
# Where receipts are offered
# ---------------------------------------------------------------------------


def test_receipt_buttons_after_purchase_only(seeded_app, seeded_client):
    """
    Purpose: The result page offers QR and email receipts; a pending order
        has no receipt.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    pending = new_order(seeded_client, seeded_app)
    assert b"QR receipt" not in seeded_client.get(f"/order/{pending}").data
    assert seeded_client.get(f"/order/{pending}/receipt").status_code == 404
    assert seeded_client.post(f"/order/{pending}/receipt/email", data={"email": "a@b.co"}).status_code == 404

    pay(seeded_client, pending)
    page = seeded_client.get(f"/order/{pending}").get_data(as_text=True)
    assert "QR receipt" in page and "Email receipt" in page


def test_receipt_works_after_logout(seeded_app, seeded_client, login):
    """
    Purpose: After a purchase logs the user out, the QR and email receipts
        still work for that order.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    order_id = bought(seeded_client, seeded_app)

    with seeded_client.session_transaction() as sess:
        assert "user_id" not in sess
    assert seeded_client.get(f"/order/{order_id}/receipt").status_code == 200
    sent = seeded_client.post(f"/order/{order_id}/receipt/email", data={"email": ALICE_EMAIL},
                              follow_redirects=True)
    assert b"Receipt sent to alice@demo.com." in sent.data


def test_other_people_cannot_get_a_receipt(seeded_app, seeded_client):
    """
    Purpose: Another browser gets 404 for someone else's receipt screens.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)
    stranger = seeded_app.test_client()

    assert stranger.get(f"/order/{order_id}/receipt").status_code == 404
    assert stranger.post(f"/order/{order_id}/receipt/email", data={"email": "x@y.zz"}).status_code == 404


# ---------------------------------------------------------------------------
# QR receipt (FR-36)
# ---------------------------------------------------------------------------


def test_online_qr_is_a_real_png_linking_to_unguessable_receipt(seeded_app, seeded_client):
    """
    Purpose: Online, the QR code is a PNG image whose link contains a long
        random token; the link opens the receipt; a wrong token gives 404.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)

    html = seeded_client.get(f"/order/{order_id}/receipt").get_data(as_text=True)
    image = re.search(r'src="data:image/png;base64,([^"]+)"', html).group(1)
    token = token_of(seeded_app, order_id)

    assert base64.b64decode(image).startswith(b"\x89PNG")
    assert len(token) >= 32
    assert f"http://localhost/receipt/{token}" in html
    seeded_client.get(f"/order/{order_id}/receipt")
    assert token_of(seeded_app, order_id) == token  # created once

    public = seeded_app.test_client().get(f"/receipt/{token}")  # anyone with the link
    assert public.status_code == 200
    assert b"Latte" in public.data
    assert seeded_client.get("/receipt/not-the-token").status_code == 404


def test_offline_qr_contains_receipt_text(seeded_app, seeded_client):
    """
    Purpose: Offline, the QR code holds the receipt text itself (no link).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)
    network(seeded_client, up=False)

    page = seeded_client.get(f"/order/{order_id}/receipt").get_data(as_text=True)
    with seeded_app.app_context():
        qr = receipts.build_qr_receipt(db.session.get(Transaction, order_id), lambda t: f"/receipt/{t}")

    assert qr["mode"] == "text"
    assert f"Order #{order_id}" in qr["payload"]
    assert "Latte" in qr["payload"] and "€3.50" in qr["payload"]
    assert "Visa •••• 4242" in qr["payload"]
    assert "/receipt/" not in qr["payload"]
    assert "Offline: the QR code contains the full receipt." in page
    assert token_of(seeded_app, order_id) is None


def test_receipt_shows_every_field(seeded_app, seeded_client):
    """
    Purpose: The receipt shows date and time, order ID, drink, options,
        price and payment (brand and last 4 digits only).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)

    html = seeded_client.get(f"/order/{order_id}/receipt").get_data(as_text=True)

    assert f"<dd>#{order_id}</dd>" in html
    assert re.search(r"<dt>Date</dt><dd>\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}</dd>", html)
    assert "<dd>Latte</dd>" in html
    assert "<dd>Whole milk · Regular · Hot</dd>" in html
    assert "<dd>€3.50</dd>" in html
    assert "<dd>Visa •••• 4242 (Insert card)</dd>" in html
    assert "Refund" not in html
    for card in card_reader.DEMO_CARDS.values():
        assert card["number"] not in html


def test_receipt_for_prepaid_and_offline_payments(seeded_app, seeded_client):
    """
    Purpose: Prepaid and offline card payments are described correctly.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    prepaid = bought(seeded_client, seeded_app, "prepaid", "PP-1000-0001")
    assert b"Prepaid \xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2 0001 (Prepaid card)" in \
        seeded_client.get(f"/order/{prepaid}/receipt").data

    network(seeded_client, up=False)
    offline_order = bought(seeded_client, seeded_app, "card_contactless", "mastercard-4444")
    assert "Mastercard •••• 4444 (Tap card), accepted offline" in \
        seeded_client.get(f"/order/{offline_order}/receipt").get_data(as_text=True)


def test_refunded_order_receipt_shows_refund(seeded_app, seeded_client):
    """
    Purpose: When the drink could not be made after payment, the receipt
        is still offered and shows the refund.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = new_order(seeded_client, seeded_app)
    with seeded_app.app_context():
        cups = Ingredient.query.filter_by(name="Cups").one()
        cups.stock_level, cups.min_threshold = 0, 0
        db.session.commit()
    pay(seeded_client, order_id)

    page = seeded_client.get(f"/order/{order_id}").get_data(as_text=True)
    receipt = seeded_client.get(f"/order/{order_id}/receipt").get_data(as_text=True)

    assert "QR receipt" in page
    assert "Refunded €3.50 – drink could not be made" in receipt


# ---------------------------------------------------------------------------
# Email receipt (FR-37, FR-60)
# ---------------------------------------------------------------------------


def test_email_receipt_online_writes_eml_file(seeded_app, seeded_client):
    """
    Purpose: Online, the email is written as an .eml file to the outbox with
        the right headers and the receipt in the body.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)

    response = seeded_client.post(f"/order/{order_id}/receipt/email",
                                  data={"email": "  Carol@Example.COM "}, follow_redirects=True)

    files = outbox_files(seeded_app)
    assert len(files) == 1
    message = BytesParser(policy=policy.default).parsebytes(files[0].read_bytes())
    assert message["To"] == "carol@example.com"
    assert message["Subject"] == f"Your SmartBrew AI receipt – order #{order_id}"
    assert "Visa •••• 4242 (Insert card)" in message.get_content()
    assert b"Receipt sent to carol@example.com." in response.data
    assert email_items(seeded_app) == [("carol@example.com", OfflineQueueItem.STATUS_SYNCED)]
    with seeded_app.app_context():
        assert db.session.get(Transaction, order_id).receipt_email == "carol@example.com"


def test_invalid_email_is_refused(seeded_app, seeded_client):
    """
    Purpose: An invalid address is refused and nothing is written or queued.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)

    for address in ("", "not-an-email", "a@b", "x" * 120 + "@example.com"):
        response = seeded_client.post(f"/order/{order_id}/receipt/email", data={"email": address},
                                      follow_redirects=True)
        assert b"Please enter a valid email address." in response.data

    assert outbox_files(seeded_app) == []
    assert email_items(seeded_app) == []


def test_offline_email_is_queued_and_sent_on_reconnect(seeded_app, seeded_client):
    """
    Purpose: Offline, the email is queued (no file yet); when the network
        returns it is written automatically (FR-60).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)
    network(seeded_client, up=False)

    queued = seeded_client.post(f"/order/{order_id}/receipt/email", data={"email": "dan@example.com"},
                                follow_redirects=True)
    assert b"will be sent when the connection is back" in queued.data
    assert outbox_files(seeded_app) == []
    assert email_items(seeded_app) == [("dan@example.com", OfflineQueueItem.STATUS_QUEUED)]

    back = network(seeded_client, up=True)

    assert len(outbox_files(seeded_app)) == 1
    assert email_items(seeded_app) == [("dan@example.com", OfflineQueueItem.STATUS_SYNCED)]
    assert b"1 queued receipt email(s) sent." in back.data


def test_outbox_lists_and_opens_emails(seeded_app, seeded_client):
    """
    Purpose: The Simulation panel lists sent and queued emails and can open
        each one; other IDs give 404.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = bought(seeded_client, seeded_app)
    seeded_client.post(f"/order/{order_id}/receipt/email", data={"email": "sent@example.com"})
    network(seeded_client, up=False)
    seeded_client.post(f"/order/{order_id}/receipt/email", data={"email": "later@example.com"})
    pay(seeded_client, new_order(seeded_client, seeded_app))  # a queued *payment*, not an email

    panel = seeded_client.get("/simulation/").get_data(as_text=True)
    with seeded_app.app_context():
        items = {i.payload.get("to"): i.id for i in OfflineQueueItem.query}
        payment_item = OfflineQueueItem.query.filter_by(kind=OfflineQueueItem.KIND_TRANSACTION).one().id

    assert 'id="outbox"' in panel
    assert "sent@example.com" in panel and "later@example.com" in panel
    assert "Queued (offline)" in panel
    sent = seeded_client.get(f"/simulation/outbox/{items['sent@example.com']}").get_data(as_text=True)
    later = seeded_client.get(f"/simulation/outbox/{items['later@example.com']}").get_data(as_text=True)
    assert "To: sent@example.com" in sent and "Visa •••• 4242" in sent
    assert "(queued – not sent yet)" in later
    assert seeded_client.get(f"/simulation/outbox/{payment_item}").status_code == 404
    assert seeded_client.get("/simulation/outbox/9999").status_code == 404


def test_tests_never_write_into_real_outbox(seeded_app):
    """
    Purpose: Tests use a temporary outbox, so data/outbox stays clean.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    assert Path(seeded_app.config["OUTBOX_DIR"]).resolve() != (DATA_DIR / "outbox").resolve()
