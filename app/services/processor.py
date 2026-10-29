"""SIMULATED external card and wallet processor (the bank's side).

It never calls a real API. Its records live in the ProcessorCharge table,
which the machine treats as external. Charges are idempotent: sending the
same idempotency key again returns the existing charge instead of charging
twice (FR-62). Outcomes are fixed per demo token so tests are predictable.
"""

import uuid
from dataclasses import dataclass

from app import db
from app.models import ProcessorCharge, utcnow

RESULT_APPROVED = "approved"
RESULT_INSUFFICIENT_FUNDS = "insufficient_funds"
RESULT_DECLINED = "declined"
RESULT_ERROR = "error"

# Demo token -> fixed outcome; every other token is approved
TOKEN_OUTCOMES = {
    "tok_visa_9995": RESULT_INSUFFICIENT_FUNDS,
    "tok_visa_0002": RESULT_DECLINED,  # the blocked Apple Pay wallet
    "tok_visa_0119": RESULT_ERROR,
}


class ProcessorUnavailableError(Exception):
    """Raised when the processor cannot be reached in the middle of a call
    (used by tests to simulate the connection failing during a sync)."""


@dataclass
class ChargeResult:
    """The processor's answer to a charge request."""

    result: str
    reference: str | None = None


def charge(processor, idempotency_key, token, amount_cents):
    """
    Purpose: Charges a card or wallet once per idempotency key (FR-62). The
        caller commits, which saves the bank-side record.

    Args:
        processor (str): ProcessorCharge.PROCESSOR_CARD or PROCESSOR_WALLET.
        idempotency_key (str): Unique key of this payment attempt.
        token (str): Card token from the reader (not a card number).
        amount_cents (int): Amount to charge.

    Returns:
        ChargeResult: approved (with reference), insufficient_funds,
            declined or error.
    """
    existing = ProcessorCharge.query.filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        # Same request sent again: return the original charge, never a second one
        return ChargeResult(RESULT_APPROVED, existing.reference)

    outcome = TOKEN_OUTCOMES.get(token, RESULT_APPROVED)
    if outcome != RESULT_APPROVED:
        return ChargeResult(outcome)

    record = ProcessorCharge(
        processor=processor,
        idempotency_key=idempotency_key,
        token=token,
        amount_cents=amount_cents,
        reference=f"ch_{uuid.uuid4().hex[:16]}",
    )
    db.session.add(record)
    return ChargeResult(RESULT_APPROVED, record.reference)


def lookup(idempotency_key):
    """
    Purpose: Asks the processor whether a payment attempt was charged, used
        after a dropped connection.

    Args:
        idempotency_key (str): Key of the payment attempt.

    Returns:
        ProcessorCharge | None: The charge, or None if it never happened.
    """
    return ProcessorCharge.query.filter_by(idempotency_key=idempotency_key).first()


def refund(idempotency_key):
    """
    Purpose: Refunds a charge (e.g. the drink could not be dispensed). The
        caller commits.

    Args:
        idempotency_key (str): Key of the charged payment attempt.

    Returns:
        bool: True if a charge was refunded, False if none was found or it
            was already refunded.
    """
    record = lookup(idempotency_key)
    if record is None or record.status == ProcessorCharge.STATUS_REFUNDED:
        return False
    record.status = ProcessorCharge.STATUS_REFUNDED
    record.refunded_at = utcnow()
    return True
