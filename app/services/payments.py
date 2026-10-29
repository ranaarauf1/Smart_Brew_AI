"""Payments (feature 7: FR-31–35, FR-62, NFR-08, NFR-27) with offline
fallback (feature 8: FR-53–55, FR-58).

Flow for one order::

    pending --claim--> paying --approved / accepted offline--> paid --> dispense
                             --declined / error--> pending (pay another way)
                             --connection dropped--> payment_unknown (checked on reconnect)

Never charging twice (FR-62):
  * only one request can claim an order (pending -> paying);
  * every attempt has its own idempotency key and the processor charges a
    key at most once;
  * the attempt is saved *before* the processor is contacted, so a lost
    answer can always be checked later.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import update

from app import db, format_euro
from app.models import Payment, PrepaidCard, ProcessorCharge, Transaction
from app.services import card_reader, connectivity, offline, orders, processor

# ---------------------------------------------------------------------------
# Plain-language error messages with codes (NFR-27)
# ---------------------------------------------------------------------------
ERRORS = {
    "PAY-101": "Your card was declined (insufficient funds). Please try another card "
               "or payment method.",
    "PAY-102": "Your wallet declined the payment. Please try another payment method.",
    "PAY-201": "The card processor had a problem, so you have not been charged. "
               "Please try again or use another payment method.",
    "PAY-301": "Your prepaid card balance ({balance}) is too low for this order ({amount}). "
               "Please use another payment method.",
    "PAY-302": "This prepaid card is not recognised. Please use another payment method.",
    "PAY-303": "The card could not be read. Please try again.",
    "PAY-401": "Apple Pay and Google Pay are unavailable right now ({reason}). "
               "Please pay by card or prepaid card.",
    "PAY-402": "While offline, card payments are accepted up to {limit}. "
               "Please use a prepaid card.",
    "PAY-403": "The offline payment store is full. Please use a prepaid card.",
    "PAY-501": "The connection dropped during payment. Nothing was dispensed. We will check "
               "with your bank as soon as the connection is back – you will be charged at "
               "most once.",
    "PAY-502": "We are still checking your last payment with the bank. Please wait until "
               "the connection is back.",
    "DSP-101": "{reason} Your payment of {amount} has been refunded.",
}

METHOD_INFO = {
    Payment.METHOD_CHIP: {"label": "Insert card", "icon": "💳", "prompt": "Insert your card"},
    Payment.METHOD_CONTACTLESS: {"label": "Tap card", "icon": "📶", "prompt": "Tap your card"},
    Payment.METHOD_APPLE_PAY: {"label": "Apple Pay", "icon": "📱", "prompt": "Hold your phone near the reader"},
    Payment.METHOD_GOOGLE_PAY: {"label": "Google Pay", "icon": "📱", "prompt": "Hold your phone near the reader"},
    Payment.METHOD_PREPAID: {"label": "Prepaid card", "icon": "🪪", "prompt": "Tap your employee card"},
}

OFFLINE_CARD_NOTE = "Offline: accepted up to {limit} and sent to the bank later."


def error_message(code, **values):
    """
    Purpose: Builds a plain-language error message with its code (NFR-27).

    Args:
        code (str): Key of ERRORS, e.g. "PAY-101".
        **values: Values for the placeholders in the message.

    Returns:
        str: E.g. "Your card was declined (insufficient funds). … (Code PAY-101)"
    """
    return f"{ERRORS[code].format(**values)} (Code {code})"


class PaymentError(Exception):
    """Raised when a payment is refused; str() is the message for the customer."""

    def __init__(self, code, **values):
        """
        Purpose: Creates the error with its code and formatted message.

        Args:
            code (str): Key of ERRORS.
            **values: Values for the message placeholders.

        Returns:
            None
        """
        self.code = code
        super().__init__(error_message(code, **values))


class ConnectionLostError(Exception):
    """Raised when the connection drops in the middle of an online payment."""


@dataclass
class PaymentOutcome:
    """What the payment screen shows after a payment request."""

    order: Transaction
    ok: bool = False
    message: str | None = None
    category: str = "error"  # flash category


# ---------------------------------------------------------------------------
# Which methods can be used right now (FR-53, FR-58)
# ---------------------------------------------------------------------------


def get_unavailable_error(order, method):
    """
    Purpose: Decides whether a payment method can be used for an order now,
        given the network and processors (FR-58: only what depends on a
        failed service is disabled).

    Args:
        order (Transaction): The order to pay.
        method (str): A Payment.METHOD_* value.

    Returns:
        PaymentError | None: The reason the method is unavailable, or None.
    """
    if method in Payment.CARD_METHODS and not connectivity.is_available(connectivity.CARD_PROCESSOR):
        # Store-and-forward: cards are accepted offline up to the limit (FR-54)
        if order.amount_cents > offline.OFFLINE_CARD_LIMIT_CENTS:
            return PaymentError("PAY-402", limit=format_euro(offline.OFFLINE_CARD_LIMIT_CENTS))
        if not offline.queue_has_room():
            return PaymentError("PAY-403")
    if method in Payment.WALLET_METHODS and not connectivity.is_available(connectivity.WALLET_PROCESSOR):
        reason = "the machine is offline" if connectivity.is_offline() else "the wallet service is down"
        return PaymentError("PAY-401", reason=reason)
    return None  # prepaid cards work locally, always


def get_payment_options(order):
    """
    Purpose: Lists the payment buttons for the order screen, each marked
        available or not with the reason.

    Args:
        order (Transaction): The order to pay.

    Returns:
        list[dict]: Entries with method, label, icon, available, reason and note.
    """
    card_offline = not connectivity.is_available(connectivity.CARD_PROCESSOR)
    options = []
    for method in Payment.ALL_METHODS:
        error = get_unavailable_error(order, method)
        note = None
        if error is None and card_offline and method in Payment.CARD_METHODS:
            note = OFFLINE_CARD_NOTE.format(limit=format_euro(offline.OFFLINE_CARD_LIMIT_CENTS))
        options.append({
            "method": method,
            **METHOD_INFO[method],
            "available": error is None,
            "reason": str(error) if error else None,
            "note": note,
        })
    return options


def list_instruments(method):
    """
    Purpose: Lists the demo cards, phones or prepaid cards to present for a
        method (the simulated reader).

    Args:
        method (str): A Payment.METHOD_* value.

    Returns:
        list[dict]: Entries with id, label and note.
    """
    if method in Payment.CARD_METHODS:
        return card_reader.list_cards()
    if method in Payment.WALLET_METHODS:
        return card_reader.list_wallets(method)
    return [
        {"id": card.card_number, "label": f"{card.card_number} ({card.holder_name})",
         "note": f"Balance {format_euro(card.balance_cents)}"}
        for card in PrepaidCard.query.filter_by(is_active=True).order_by(PrepaidCard.card_number)
    ]


def get_successful_payment(order):
    """
    Purpose: Finds the order's successful payment, if any.

    Args:
        order (Transaction): The order.

    Returns:
        Payment | None: The approved, offline-accepted or synced payment.
    """
    return Payment.query.filter(
        Payment.transaction_id == order.id, Payment.status.in_(Payment.SUCCESS_STATUSES)
    ).first()


# ---------------------------------------------------------------------------
# Taking a payment
# ---------------------------------------------------------------------------


def _new_payment(order, method, status, **fields):
    """
    Purpose: Creates a payment attempt with its own idempotency key (FR-62).

    Args:
        order (Transaction): The order.
        method (str): A Payment.METHOD_* value.
        status (str): Initial Payment status.
        **fields: Other Payment columns (brand, last 4, prepaid card).

    Returns:
        Payment: The new, unsaved payment.
    """
    payment = Payment(
        transaction_id=order.id, method=method, amount_cents=order.amount_cents,
        status=status, idempotency_key=uuid.uuid4().hex, **fields,
    )
    db.session.add(payment)
    return payment


def _charge_online(order, method, card, processor_kind):
    """
    Purpose: Charges a card or wallet through the processor. The attempt is
        saved first, so if the connection drops its result can be checked
        later (FR-62). Honours the "Drop connection during next payment"
        switch.

    Args:
        order (Transaction): The order.
        method (str): Card or wallet method.
        card (CardRead): What the reader passed on (no full number).
        processor_kind (str): ProcessorCharge.PROCESSOR_CARD or PROCESSOR_WALLET.

    Returns:
        Payment: The approved payment.

    Raises:
        PaymentError: If the payment is declined or the processor fails.
        ConnectionLostError: If the connection drops during the payment.
    """
    payment = _new_payment(order, method, Payment.STATUS_UNKNOWN,
                           card_brand=card.brand, card_last4=card.last4)
    db.session.commit()  # recorded before contacting the bank

    drop = connectivity.consume_drop_mode()
    if drop == connectivity.DROP_BEFORE_CHARGE:
        connectivity.record_connection_drop()
        raise ConnectionLostError()

    answer = processor.charge(processor_kind, payment.idempotency_key, card.token,
                              payment.amount_cents)
    db.session.commit()  # the bank's side is saved even if our answer is lost
    if drop == connectivity.DROP_AFTER_CHARGE:
        connectivity.record_connection_drop()
        raise ConnectionLostError()

    if answer.result == processor.RESULT_APPROVED:
        payment.status = Payment.STATUS_APPROVED
        payment.processor_reference = answer.reference
        db.session.commit()
        return payment

    if answer.result == processor.RESULT_ERROR:
        payment.status, code = Payment.STATUS_ERROR, "PAY-201"
    elif processor_kind == ProcessorCharge.PROCESSOR_WALLET:
        payment.status, code = Payment.STATUS_DECLINED, "PAY-102"
    else:
        payment.status, code = Payment.STATUS_DECLINED, "PAY-101"
    payment.error_code = code
    db.session.commit()
    raise PaymentError(code)


def _accept_offline(order, method, card):
    """
    Purpose: Accepts a card payment while the card processor cannot be
        reached and queues it to be sent later (FR-54, store-and-forward).

    Args:
        order (Transaction): The order (limit and queue room already checked).
        method (str): Card method.
        card (CardRead): What the reader passed on.

    Returns:
        Payment: The offline-queued payment.
    """
    payment = _new_payment(order, method, Payment.STATUS_OFFLINE_QUEUED,
                           card_brand=card.brand, card_last4=card.last4)
    db.session.flush()
    offline.enqueue_payment(payment, card.token)
    db.session.commit()
    return payment


def _charge_prepaid(order, card_number):
    """
    Purpose: Pays with a prepaid employee card from its local balance
        (FR-33). The balance check and deduction are one database update,
        so the balance can never go below zero.

    Args:
        order (Transaction): The order.
        card_number (str | None): The presented prepaid card number.

    Returns:
        Payment: The approved payment.

    Raises:
        PaymentError: If the card is unknown or its balance is too low.
    """
    card = PrepaidCard.query.filter_by(card_number=card_number or "", is_active=True).first()
    if card is None:
        raise PaymentError("PAY-302")

    result = db.session.execute(
        update(PrepaidCard)
        .where(PrepaidCard.id == card.id, PrepaidCard.balance_cents >= order.amount_cents)
        .values(balance_cents=PrepaidCard.balance_cents - order.amount_cents)
    )
    db.session.refresh(card)
    fields = {"card_brand": "Prepaid", "card_last4": card.card_number[-4:], "prepaid_card_id": card.id}
    if result.rowcount == 0:
        payment = _new_payment(order, Payment.METHOD_PREPAID, Payment.STATUS_DECLINED, **fields)
        payment.error_code = "PAY-301"
        db.session.commit()
        raise PaymentError("PAY-301", balance=format_euro(card.balance_cents),
                           amount=format_euro(order.amount_cents))

    payment = _new_payment(order, Payment.METHOD_PREPAID, Payment.STATUS_APPROVED, **fields)
    db.session.commit()
    return payment


def _take_payment(order, method, instrument_id):
    """
    Purpose: Takes the payment with the chosen method and presented card.

    Args:
        order (Transaction): The claimed order.
        method (str): A Payment.METHOD_* value.
        instrument_id (str | None): Demo card/phone key or prepaid card number.

    Returns:
        Payment: The successful payment.

    Raises:
        PaymentError: If the payment is refused.
        ConnectionLostError: If the connection drops during the payment.
    """
    unavailable = get_unavailable_error(order, method)
    if unavailable is not None:
        raise unavailable

    try:
        if method in Payment.CARD_METHODS:
            card = card_reader.read_card(instrument_id)
            if connectivity.is_available(connectivity.CARD_PROCESSOR):
                return _charge_online(order, method, card, ProcessorCharge.PROCESSOR_CARD)
            return _accept_offline(order, method, card)
        if method in Payment.WALLET_METHODS:
            card = card_reader.read_wallet(method, instrument_id)
            return _charge_online(order, method, card, ProcessorCharge.PROCESSOR_WALLET)
    except card_reader.CardReadError:
        raise PaymentError("PAY-303") from None
    return _charge_prepaid(order, instrument_id)


def pay(order, method, instrument_id):
    """
    Purpose: Pays for an order and, once paid, dispenses it (FR-31–35).
        Only one request can pay an order: a second press finds it already
        claimed and charges nothing (FR-62).

    Args:
        order (Transaction): The order to pay.
        method (str): A Payment.METHOD_* value.
        instrument_id (str | None): Demo card/phone key or prepaid card number.

    Returns:
        PaymentOutcome: The order after payment/dispensing and the message.
    """
    if method not in Payment.ALL_METHODS:
        return PaymentOutcome(order, message="Please choose a payment method.")
    if order.status == Transaction.STATUS_PAYMENT_UNKNOWN:
        return PaymentOutcome(order, message=error_message("PAY-502"), category="warning")

    claimed = db.session.execute(
        update(Transaction)
        .where(Transaction.id == order.id, Transaction.status == Transaction.STATUS_PENDING)
        .values(status=Transaction.STATUS_PAYING)
    )
    db.session.commit()
    db.session.refresh(order)
    if claimed.rowcount == 0:
        # Already paid, dispensed or being paid by another request: charge nothing
        return PaymentOutcome(order, ok=order.status == Transaction.STATUS_COMPLETED)

    try:
        payment = _take_payment(order, method, instrument_id)
    except PaymentError as error:
        order.status = Transaction.STATUS_PENDING  # the customer may pay another way
        db.session.commit()
        return PaymentOutcome(order, message=str(error))
    except ConnectionLostError:
        order.status = Transaction.STATUS_PAYMENT_UNKNOWN
        db.session.commit()
        return PaymentOutcome(order, message=error_message("PAY-501"), category="warning")
    except Exception:
        order.status = Transaction.STATUS_PENDING  # never leave an order stuck in "paying"
        db.session.commit()
        raise

    orders.record_successful_payment(order, payment)
    db.session.commit()
    return finish_dispense(order, payment)


def refund_payment(payment):
    """
    Purpose: Gives the money back when the drink could not be dispensed:
        prepaid balance restored, card/wallet charge refunded, offline
        payment voided before it is sent.

    Args:
        payment (Payment | None): The order's successful payment.

    Returns:
        None
    """
    if payment is None:
        return
    if payment.status == Payment.STATUS_OFFLINE_QUEUED:
        offline.void_payment(payment)
    elif payment.method == Payment.METHOD_PREPAID:
        db.session.execute(
            update(PrepaidCard)
            .where(PrepaidCard.id == payment.prepaid_card_id)
            .values(balance_cents=PrepaidCard.balance_cents + payment.amount_cents)
        )
        payment.status = Payment.STATUS_REFUNDED
    else:
        processor.refund(payment.idempotency_key)
        payment.status = Payment.STATUS_REFUNDED
    db.session.commit()


def finish_dispense(order, payment=None):
    """
    Purpose: Dispenses a paid order; if the stock re-check fails (FR-66),
        refunds the payment automatically.

    Args:
        order (Transaction): A paid order.
        payment (Payment | None): Its payment (looked up if None).

    Returns:
        PaymentOutcome: ok=True when the drink was dispensed.
    """
    orders.dispense_order(order)
    if order.status == Transaction.STATUS_CANCELLED:
        payment = payment or get_successful_payment(order)
        refund_payment(payment)
        order.failure_reason = error_message(
            "DSP-101", reason=order.failure_reason or "The drink could not be made.",
            amount=format_euro(order.amount_cents),
        )[:255]
        db.session.commit()
        return PaymentOutcome(order)  # the order page shows failure_reason itself
    if order.status == Transaction.STATUS_COMPLETED:
        return PaymentOutcome(order, ok=True, message="Payment approved – enjoy your drink!",
                              category="success")
    return PaymentOutcome(order)


def dispense_paid_order(order):
    """
    Purpose: Dispenses an order that is already paid, e.g. after a dropped
        connection was checked and the bank had charged (FR-62).

    Args:
        order (Transaction): The order.

    Returns:
        PaymentOutcome: The result; nothing happens if the order is not paid.
    """
    if order.status != Transaction.STATUS_PAID:
        return PaymentOutcome(order, ok=order.status == Transaction.STATUS_COMPLETED)
    return finish_dispense(order)


def get_prepaid_balance(order):
    """
    Purpose: Finds the remaining prepaid balance after paying an order.

    Args:
        order (Transaction): The order.

    Returns:
        int | None: Balance in cents, or None if not paid by prepaid card.
    """
    if order.payment_method != Payment.METHOD_PREPAID or order.prepaid_card is None:
        return None
    return order.prepaid_card.balance_cents
