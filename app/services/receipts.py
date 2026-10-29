"""Receipts: QR code and email (feature 9: FR-36, FR-37, FR-60).

Online, the QR code links to a receipt page with an unguessable token;
offline it holds the receipt text itself. Email receipts are written as
.eml files to data/outbox/ (no real email is sent); while offline they are
queued and written when the network comes back.
"""

import base64
import io
import secrets
import uuid
from datetime import timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path

import qrcode
from flask import current_app

from app import db, format_euro
from app.models import OfflineQueueItem, Payment, Transaction, utcnow
from app.services import auth, connectivity

SENDER = "SmartBrew AI <receipts@smartbrew.local>"

METHOD_LABELS = {
    Payment.METHOD_CHIP: "Insert card",
    Payment.METHOD_CONTACTLESS: "Tap card",
    Payment.METHOD_APPLE_PAY: "Apple Pay",
    Payment.METHOD_GOOGLE_PAY: "Google Pay",
    Payment.METHOD_PREPAID: "Prepaid card",
}

EMAIL_SENT = "sent"
EMAIL_QUEUED = "queued"


class ReceiptError(Exception):
    """Raised when a receipt cannot be made or sent; the message is shown."""


# ---------------------------------------------------------------------------
# Receipt content
# ---------------------------------------------------------------------------


def get_receipt_payment(order):
    """
    Purpose: Finds the payment a receipt describes: the successful one, or
        the refunded/voided one for a cancelled order.

    Args:
        order (Transaction): The order.

    Returns:
        Payment | None: The payment, or None.
    """
    shown = Payment.SUCCESS_STATUSES + (Payment.STATUS_REFUNDED, Payment.STATUS_VOIDED,
                                        Payment.STATUS_SYNC_DECLINED)
    return next((p for p in reversed(order.payments) if p.status in shown), None)


def can_have_receipt(order):
    """
    Purpose: Checks whether a receipt is offered: after a completed
        purchase, or a cancelled order whose payment was refunded.

    Args:
        order (Transaction): The order.

    Returns:
        bool: True if a receipt can be shown.
    """
    if order.status == Transaction.STATUS_COMPLETED:
        return True
    payment = get_receipt_payment(order)
    return (order.status == Transaction.STATUS_CANCELLED and payment is not None
            and payment.status in (Payment.STATUS_REFUNDED, Payment.STATUS_VOIDED))


def to_local(utc_naive):
    """
    Purpose: Converts a stored UTC time to the machine's local time.

    Args:
        utc_naive (datetime): Naive UTC datetime from the database.

    Returns:
        datetime: Local, timezone-aware datetime.
    """
    return utc_naive.replace(tzinfo=timezone.utc).astimezone()


def build_receipt(order):
    """
    Purpose: Collects everything a receipt shows (FR-36): date and time,
        order ID, drink, options, price, payment (brand and last 4 digits
        only, NFR-08) and refund status.

    Args:
        order (Transaction): The order.

    Returns:
        dict: Keys order_id, when, drink, options, price, payment, refund.
    """
    payment = get_receipt_payment(order)
    payment_text = "—"
    if payment is not None:
        payment_text = f"{payment.card_brand} •••• {payment.card_last4} ({METHOD_LABELS[payment.method]})"
        if payment.status in (Payment.STATUS_OFFLINE_QUEUED, Payment.STATUS_SYNCED):
            payment_text += ", accepted offline"

    refund = None
    if payment is not None and payment.status in (Payment.STATUS_REFUNDED, Payment.STATUS_VOIDED):
        refund = f"Refunded {format_euro(payment.amount_cents)} – drink could not be made"

    return {
        "order_id": order.id,
        "when": to_local(order.completed_at or order.created_at).strftime("%d.%m.%Y %H:%M"),
        "drink": order.drink.name,
        "options": (order.customizations or {}).get("summary", "Standard"),
        "price": format_euro(order.amount_cents),
        "payment": payment_text,
        "refund": refund,
    }


def receipt_text(order):
    """
    Purpose: Writes the receipt as plain text (for email and offline QR).

    Args:
        order (Transaction): The order.

    Returns:
        str: Multi-line receipt.
    """
    r = build_receipt(order)
    lines = [
        "SmartBrew AI – Receipt",
        f"Order #{r['order_id']}  {r['when']}",
        f"{r['drink']}: {r['options']}",
        f"Price: {r['price']}",
        f"Payment: {r['payment']}",
    ]
    if r["refund"]:
        lines.append(r["refund"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# QR receipt (FR-36)
# ---------------------------------------------------------------------------


def ensure_receipt_token(order):
    """
    Purpose: Gives the order an unguessable receipt token (created once).

    Args:
        order (Transaction): The order.

    Returns:
        str: The token (32 URL-safe characters).
    """
    if not order.receipt_token:
        order.receipt_token = secrets.token_urlsafe(24)
        db.session.commit()
    return order.receipt_token


def get_order_by_token(token):
    """
    Purpose: Finds the order for a public receipt link.

    Args:
        token (str): Token from the URL.

    Returns:
        Transaction | None: The order, or None for an unknown token or one
            that has no receipt.
    """
    order = Transaction.query.filter_by(receipt_token=token).first() if token else None
    return order if order is not None and can_have_receipt(order) else None


def make_qr_data_uri(payload):
    """
    Purpose: Draws a real QR code and returns it as an embedded PNG image.

    Args:
        payload (str): Text or link to encode.

    Returns:
        str: "data:image/png;base64,…" for an <img> tag.
    """
    image = qrcode.make(payload, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def build_qr_receipt(order, link_for_token):
    """
    Purpose: Builds the QR receipt: online it encodes the receipt link,
        offline the receipt text itself.

    Args:
        order (Transaction): The order.
        link_for_token (callable): Turns a token into the full receipt URL.

    Returns:
        dict: Keys mode ("link" or "text"), payload and image (data URI).
    """
    if connectivity.is_available(connectivity.NETWORK):
        mode, payload = "link", link_for_token(ensure_receipt_token(order))
    else:
        mode, payload = "text", receipt_text(order)
    return {"mode": mode, "payload": payload, "image": make_qr_data_uri(payload)}


# ---------------------------------------------------------------------------
# Email receipt (FR-37, FR-60)
# ---------------------------------------------------------------------------


def validate_email(address):
    """
    Purpose: Checks an email address typed for a receipt.

    Args:
        address (str | None): Address as typed.

    Returns:
        str: The trimmed, lowercased address.

    Raises:
        ReceiptError: If it is not a valid email address.
    """
    address = auth.normalize_email(address)
    # Same rule as account registration (feature 1)
    if not auth.EMAIL_PATTERN.match(address) or len(address) > auth.MAX_EMAIL_LENGTH:
        raise ReceiptError("Please enter a valid email address.")
    return address


def outbox_dir():
    """
    Purpose: Returns the email outbox folder, creating it if needed.

    Args:
        None

    Returns:
        Path: The data/outbox folder (a temporary folder in tests).
    """
    path = Path(current_app.config["OUTBOX_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_email_file(item):
    """
    Purpose: "Sends" a queued email by writing it as an .eml file to the
        outbox. The caller commits.

    Args:
        item (OfflineQueueItem): An email queue item.

    Returns:
        str: The file name written.
    """
    message = EmailMessage()
    message["From"] = SENDER
    message["To"] = item.payload["to"]
    message["Subject"] = item.payload["subject"]
    message["Date"] = format_datetime(to_local(utcnow()))
    message.set_content(item.payload["body"], cte="8bit")  # readable UTF-8 in the file

    name = f"{utcnow():%Y%m%d-%H%M%S}-order{item.transaction_id}-{uuid.uuid4().hex[:8]}.eml"
    (outbox_dir() / name).write_bytes(bytes(message))
    item.payload = {**item.payload, "file_name": name}  # new dict so the JSON change is saved
    item.status = OfflineQueueItem.STATUS_SYNCED
    item.synced_at = utcnow()
    item.attempts += 1
    return name


def send_email_receipt(order, address):
    """
    Purpose: Emails the receipt: written to the outbox now when online,
        or queued and written when the network returns (FR-60).

    Args:
        order (Transaction): The order.
        address (str | None): Email address as typed.

    Returns:
        str: EMAIL_SENT or EMAIL_QUEUED.

    Raises:
        ReceiptError: If the address is invalid or there is no receipt.
    """
    if not can_have_receipt(order):
        raise ReceiptError("There is no receipt for this order yet.")
    address = validate_email(address)
    order.receipt_email = address
    item = OfflineQueueItem(
        kind=OfflineQueueItem.KIND_EMAIL,
        transaction_id=order.id,
        payload={
            "to": address,
            "subject": f"Your SmartBrew AI receipt – order #{order.id}",
            "body": receipt_text(order) + "\n\nThank you for your order!",
        },
    )
    db.session.add(item)
    status = EMAIL_QUEUED
    if connectivity.is_available(connectivity.NETWORK):
        write_email_file(item)
        status = EMAIL_SENT
    db.session.commit()
    return status


def send_queued_emails():
    """
    Purpose: Writes every queued email once the network is back (FR-60).
        Each one is committed on its own, so none are lost.

    Args:
        None

    Returns:
        int: Number of emails sent.
    """
    if not connectivity.is_available(connectivity.NETWORK):
        return 0
    sent = 0
    for item in (OfflineQueueItem.query.filter_by(kind=OfflineQueueItem.KIND_EMAIL,
                                                  status=OfflineQueueItem.STATUS_QUEUED)
                 .order_by(OfflineQueueItem.id)):
        write_email_file(item)
        db.session.commit()
        sent += 1
    return sent


def list_outbox():
    """
    Purpose: Lists sent and queued receipt emails, newest first.

    Args:
        None

    Returns:
        list[OfflineQueueItem]: Email queue items.
    """
    return (
        OfflineQueueItem.query.filter_by(kind=OfflineQueueItem.KIND_EMAIL)
        .order_by(OfflineQueueItem.id.desc())
        .all()
    )


def read_email(item_id):
    """
    Purpose: Loads one outbox email to show it: the .eml file if sent, the
        stored text if still queued.

    Args:
        item_id (int): Queue item ID.

    Returns:
        dict | None: Keys item and content, or None if not an email.
    """
    item = db.session.get(OfflineQueueItem, item_id)
    if item is None or item.kind != OfflineQueueItem.KIND_EMAIL:
        return None
    file_name = item.payload.get("file_name")
    if file_name:
        path = outbox_dir() / Path(file_name).name  # name only: never leave the outbox
        if path.exists():
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            headers = "\n".join(f"{h}: {message[h]}" for h in ("From", "To", "Subject", "Date"))
            content = f"{headers}\n\n{message.get_content()}"
        else:
            content = "(file missing)"
    else:
        content = (f"To: {item.payload['to']}\nSubject: {item.payload['subject']}\n"
                   f"(queued – not sent yet)\n\n{item.payload['body']}")
    return {"item": item, "content": content}
