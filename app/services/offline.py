"""Offline queue and sync (feature 8: FR-53–55, FR-59, FR-61, NFR-29, NFR-30).

Card payments accepted while the card processor cannot be reached are
stored here (store-and-forward) and sent when it is back. Queue items are
never deleted: each one ends as synced, declined or voided (NFR-29).
Payments whose result was lost when the connection dropped (FR-62) are
checked with the processor here too.
"""

from dataclasses import dataclass

from app import db
from app.models import ErrorEvent, OfflineQueueItem, Payment, ProcessorCharge, Transaction, utcnow
from app.services import connectivity, orders, processor

QUEUE_CAPACITY = 1000  # NFR-30 requires at least 500
OFFLINE_CARD_LIMIT_CENTS = 1000  # store-and-forward limit: €10 per payment

CODE_OFFLINE_SYNC = "OFFLINE_SYNC"
CODE_OFFLINE_DECLINED = "OFFLINE_PAYMENT_DECLINED"


@dataclass
class SyncResult:
    """Counts from one sync run."""

    sent: int = 0
    approved: int = 0
    declined: int = 0
    declined_cents: int = 0
    kept: int = 0  # still queued (processor error or unreachable)
    confirmed: int = 0  # dropped payments found charged
    not_charged: int = 0  # dropped payments the bank never charged

    def summary(self):
        """
        Purpose: Describes the sync in one line for the Simulation panel.

        Args:
            None

        Returns:
            str: E.g. "Sent 3: 2 approved, 1 declined (€3.50 lost); 0 still queued."
        """
        text = (
            f"Sent {self.sent}: {self.approved} approved, {self.declined} declined"
            f" (€{self.declined_cents / 100:.2f} lost); {self.kept} still queued."
        )
        if self.confirmed or self.not_charged:
            text += (
                f" Dropped payments checked: {self.confirmed} charged once,"
                f" {self.not_charged} not charged."
            )
        return text

    def anything_happened(self):
        """
        Purpose: Checks whether the sync did any work worth logging.

        Args:
            None

        Returns:
            bool: True if anything was sent, kept or checked.
        """
        return bool(self.sent or self.kept or self.confirmed or self.not_charged)


def count_queued():
    """
    Purpose: Counts payments waiting in the offline queue.

    Args:
        None

    Returns:
        int: Number of queued payments.
    """
    return OfflineQueueItem.query.filter_by(
        kind=OfflineQueueItem.KIND_TRANSACTION, status=OfflineQueueItem.STATUS_QUEUED
    ).count()


def queue_has_room():
    """
    Purpose: Checks that the queue can take another payment (NFR-30).

    Args:
        None

    Returns:
        bool: True if fewer than QUEUE_CAPACITY payments are queued.
    """
    return count_queued() < QUEUE_CAPACITY


def get_queue_counts():
    """
    Purpose: Counts queue items per status for the Simulation panel.

    Args:
        None

    Returns:
        dict[str, int]: Count per status (all statuses present).
    """
    counts = {s: 0 for s in (OfflineQueueItem.STATUS_QUEUED, OfflineQueueItem.STATUS_SYNCED,
                             OfflineQueueItem.STATUS_DECLINED, OfflineQueueItem.STATUS_VOIDED)}
    rows = (
        db.session.query(OfflineQueueItem.status, db.func.count())
        .filter(OfflineQueueItem.kind == OfflineQueueItem.KIND_TRANSACTION)
        .group_by(OfflineQueueItem.status)
    )
    for status, count in rows:
        counts[status] = count
    return counts


def enqueue_payment(payment, token):
    """
    Purpose: Stores an offline card payment to be sent later (FR-54). Only
        the card token is kept, never the card number (NFR-08). The caller
        commits.

    Args:
        payment (Payment): The payment accepted offline.
        token (str): Card token from the reader.

    Returns:
        OfflineQueueItem: The new queue item.
    """
    item = OfflineQueueItem(
        kind=OfflineQueueItem.KIND_TRANSACTION,
        transaction_id=payment.transaction_id,
        payment=payment,
        payload={
            "token": token,
            "amount_cents": payment.amount_cents,
            "idempotency_key": payment.idempotency_key,
            "card": f"{payment.card_brand} •••• {payment.card_last4}",
        },
    )
    db.session.add(item)
    return item


def void_payment(payment):
    """
    Purpose: Cancels a queued offline payment before it is sent, e.g. when
        the drink could not be dispensed. The caller commits.

    Args:
        payment (Payment): An offline-queued payment.

    Returns:
        bool: True if a queued item was voided.
    """
    items = OfflineQueueItem.query.filter_by(
        payment_id=payment.id, status=OfflineQueueItem.STATUS_QUEUED
    ).all()
    for item in items:
        item.status = OfflineQueueItem.STATUS_VOIDED
    payment.status = Payment.STATUS_VOIDED
    return bool(items)


def send_queued_payments(result):
    """
    Purpose: Sends every queued payment to the card processor, oldest first
        (FR-55). Each item is committed on its own so nothing is lost if the
        connection fails half-way (NFR-29).

    Args:
        result (SyncResult): Counts to update.

    Returns:
        None
    """
    items = (
        OfflineQueueItem.query.filter_by(
            kind=OfflineQueueItem.KIND_TRANSACTION, status=OfflineQueueItem.STATUS_QUEUED
        )
        .order_by(OfflineQueueItem.id)
        .all()
    )
    for index, item in enumerate(items):
        if not connectivity.is_available(connectivity.CARD_PROCESSOR):
            result.kept += len(items) - index  # went down again: keep the rest
            return

        payment = item.payment
        item.attempts += 1
        try:
            answer = processor.charge(
                ProcessorCharge.PROCESSOR_CARD,
                item.payload["idempotency_key"],
                item.payload["token"],
                item.payload["amount_cents"],
            )
        except processor.ProcessorUnavailableError:
            item.last_error = "Processor unreachable"
            db.session.commit()
            result.kept += len(items) - index
            return

        if answer.result == processor.RESULT_APPROVED:
            item.status = OfflineQueueItem.STATUS_SYNCED
            item.synced_at = utcnow()
            payment.status = Payment.STATUS_SYNCED
            payment.processor_reference = answer.reference
            result.sent += 1
            result.approved += 1
        elif answer.result in (processor.RESULT_INSUFFICIENT_FUNDS, processor.RESULT_DECLINED):
            # Store-and-forward risk: the drink was already given out
            item.status = OfflineQueueItem.STATUS_DECLINED
            item.synced_at = utcnow()
            item.last_error = answer.result
            payment.status = Payment.STATUS_SYNC_DECLINED
            payment.error_code = "PAY-101"
            db.session.add(ErrorEvent(
                code=CODE_OFFLINE_DECLINED, component="Payments",
                severity=ErrorEvent.SEVERITY_WARNING,
                message=(
                    f"Offline payment for order #{item.transaction_id} "
                    f"({item.payload['card']}, €{payment.amount_cents / 100:.2f}) was declined "
                    "on sync; the drink was already dispensed (loss)."
                ),
            ))
            result.sent += 1
            result.declined += 1
            result.declined_cents += payment.amount_cents
        else:
            item.last_error = "Processor error"  # retried on the next sync
            result.kept += 1
        db.session.commit()


def reconcile_unknown_payments(result):
    """
    Purpose: Checks payments whose result was lost when the connection
        dropped (FR-62): if the bank charged, the order becomes paid (charged
        once); if not, the order can be paid again (not charged).

    Args:
        result (SyncResult): Counts to update.

    Returns:
        None
    """
    for payment in Payment.query.filter_by(status=Payment.STATUS_UNKNOWN).all():
        service = (connectivity.WALLET_PROCESSOR if payment.method in Payment.WALLET_METHODS
                   else connectivity.CARD_PROCESSOR)
        if not connectivity.is_available(service):
            continue  # try again on the next sync

        order = payment.transaction
        charge = processor.lookup(payment.idempotency_key)
        if charge is not None and charge.status == ProcessorCharge.STATUS_CHARGED:
            payment.status = Payment.STATUS_APPROVED
            payment.processor_reference = charge.reference
            if order.status == Transaction.STATUS_PAYMENT_UNKNOWN:
                orders.record_successful_payment(order, payment)
            result.confirmed += 1
        else:
            payment.status = Payment.STATUS_FAILED
            if order.status == Transaction.STATUS_PAYMENT_UNKNOWN:
                order.status = Transaction.STATUS_PENDING  # customer may pay again
            result.not_charged += 1
        db.session.commit()


def run_sync():
    """
    Purpose: Runs a full sync: checks dropped payments and sends the
        offline queue, then records the summary for the Simulation panel.

    Args:
        None

    Returns:
        str: One-line summary of the sync.
    """
    result = SyncResult()
    reconcile_unknown_payments(result)
    if connectivity.is_available(connectivity.CARD_PROCESSOR):
        send_queued_payments(result)
    else:
        result.kept = count_queued()

    summary = result.summary()
    state = connectivity.get_state()
    if state is not None:
        state.last_sync_at = utcnow()
        state.last_sync_summary = summary
    if result.anything_happened():
        db.session.add(ErrorEvent(
            code=CODE_OFFLINE_SYNC, component="Connectivity",
            severity=ErrorEvent.SEVERITY_INFO, message=f"Offline sync: {summary}",
            resolved_at=utcnow(),
        ))
    db.session.commit()
    return summary
