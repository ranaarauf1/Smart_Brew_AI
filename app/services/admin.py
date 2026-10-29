"""Admin mode access and audit log (feature 10: FR-43–52, NFR-12, NFR-31).

Three ways in: technician PIN (5 wrong tries lock PIN entry for 15 minutes),
NFC master card, or the simulated service key. The admin session is kept
apart from the customer login and ends after 2 minutes of inactivity.
Every entry, exit and admin action is written to the audit log, which can
only be added to.
"""

import math
import time
from datetime import timedelta

from flask import current_app, session

from app import db
from app.models import AdminAuditLog, NfcCard, SystemState, WasteBin, utcnow
from app.services import inventory, maintenance
from app.services.security import verify_secret

PIN_MAX_ATTEMPTS = 5
PIN_LOCKOUT = timedelta(minutes=15)

METHOD_PIN = "PIN"
METHOD_NFC = "NFC"
METHOD_SERVICE_KEY = "SERVICE_KEY"

SESSION_ADMIN_KEY = "admin"

# Audit log actions
ACTION_LOGIN_OK = "LOGIN_OK"
ACTION_LOGIN_FAILED = "LOGIN_FAILED"
ACTION_LOCKOUT = "PIN_LOCKOUT"
ACTION_PIN_BLOCKED = "PIN_BLOCKED"
ACTION_EXIT = "EXIT"
ACTION_REFILL = "REFILL"
ACTION_RESET = "RESET_TO_FULL"
ACTION_LIMITS = "EDIT_LIMITS"
ACTION_EMPTY_BIN = "EMPTY_BIN"
ACTION_RESOLVE = "RESOLVE_EVENT"

LOCKED_MESSAGE = "PIN entry is locked after {max} wrong attempts. Try again in {remaining}. (Code ADM-102)"
WRONG_PIN_MESSAGE = "Wrong PIN. {left} attempt(s) left before PIN entry is locked for 15 minutes. (Code ADM-101)"


class AdminAccessError(Exception):
    """Raised when admin mode cannot be entered; the message is shown."""


# ---------------------------------------------------------------------------
# Audit log (append only)
# ---------------------------------------------------------------------------


def audit(action, actor, details=""):
    """
    Purpose: Adds an entry to the audit log and saves it straight away, so
        nothing is lost even if a later step fails.

    Args:
        action (str): One of the ACTION_* values.
        actor (str): Access method, e.g. "PIN", "NFC 04FFEE01", "SERVICE_KEY".
        details (str): What happened.

    Returns:
        AdminAuditLog: The new entry.
    """
    entry = AdminAuditLog(action=action, actor=actor, details=details[:255])
    db.session.add(entry)
    db.session.commit()
    return entry


def get_audit_log():
    """
    Purpose: Lists the audit log, newest first (read only).

    Args:
        None

    Returns:
        list[AdminAuditLog]: All entries.
    """
    return AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()).all()


# ---------------------------------------------------------------------------
# PIN with lockout (NFR-12)
# ---------------------------------------------------------------------------


def format_remaining(remaining):
    """
    Purpose: Formats the lockout time left as minutes:seconds, rounded up.

    Args:
        remaining (timedelta): Time left.

    Returns:
        str: E.g. "14:32" or "0:01".
    """
    seconds = max(0, math.ceil(remaining.total_seconds()))
    return f"{seconds // 60}:{seconds % 60:02d}"


def get_lockout_remaining(now=None):
    """
    Purpose: Checks whether PIN entry is locked and for how long.

    Args:
        now (datetime | None): Current UTC time (for tests); defaults to now.

    Returns:
        timedelta | None: Time left, or None if not locked.
    """
    state = SystemState.get()
    now = now or utcnow()
    if state is None or state.locked_until is None or now >= state.locked_until:
        return None
    return state.locked_until - now


def check_pin(pin, now=None):
    """
    Purpose: Checks the technician PIN (hashed, NFR-09). Every wrong attempt
        is logged; the 5th wrong attempt locks PIN entry for 15 minutes.

    Args:
        pin (str | None): PIN typed on the keypad.
        now (datetime | None): Current UTC time (for tests); defaults to now.

    Returns:
        None: The PIN was correct.

    Raises:
        AdminAccessError: If the PIN is wrong or PIN entry is locked; the
            message includes attempts left or the time remaining.
    """
    state = SystemState.get()
    if state is None:
        raise AdminAccessError("System state missing. Run python seed.py first.")
    now = now or utcnow()

    remaining = get_lockout_remaining(now)
    if remaining is not None:
        audit(ACTION_PIN_BLOCKED, METHOD_PIN, f"PIN entry while locked ({format_remaining(remaining)} left).")
        raise AdminAccessError(LOCKED_MESSAGE.format(max=PIN_MAX_ATTEMPTS,
                                                     remaining=format_remaining(remaining)))
    if state.locked_until is not None:  # lockout has run out
        state.locked_until = None
        state.failed_pin_attempts = 0

    if verify_secret(pin or "", state.admin_pin_hash):
        state.failed_pin_attempts = 0
        db.session.commit()
        return

    state.failed_pin_attempts += 1
    attempts = state.failed_pin_attempts
    db.session.commit()
    audit(ACTION_LOGIN_FAILED, METHOD_PIN, f"Wrong PIN (attempt {attempts} of {PIN_MAX_ATTEMPTS}).")
    if attempts >= PIN_MAX_ATTEMPTS:
        state.locked_until = now + PIN_LOCKOUT
        state.failed_pin_attempts = 0
        db.session.commit()
        audit(ACTION_LOCKOUT, METHOD_PIN, "PIN entry locked for 15 minutes.")
        raise AdminAccessError(LOCKED_MESSAGE.format(max=PIN_MAX_ATTEMPTS,
                                                     remaining=format_remaining(PIN_LOCKOUT)))
    raise AdminAccessError(WRONG_PIN_MESSAGE.format(left=PIN_MAX_ATTEMPTS - attempts))


# ---------------------------------------------------------------------------
# Admin session
# ---------------------------------------------------------------------------


def admin_timeout_seconds():
    """
    Purpose: Reads the admin inactivity timeout from the app config.

    Args:
        None

    Returns:
        int: Seconds of inactivity before admin mode exits (default 120).
    """
    return current_app.config.get("ADMIN_TIMEOUT_SECONDS", 120)


def start_admin_session(actor):
    """
    Purpose: Enters admin mode and logs it.

    Args:
        actor (str): Access method, e.g. "PIN" or "NFC 04FFEE01".

    Returns:
        None
    """
    session[SESSION_ADMIN_KEY] = {"actor": actor, "last_activity": time.time()}
    audit(ACTION_LOGIN_OK, actor, "Entered admin mode.")


def enter_with_pin(pin, now=None):
    """
    Purpose: Enters admin mode with the technician PIN.

    Args:
        pin (str | None): PIN typed on the keypad.
        now (datetime | None): Current UTC time (for tests).

    Returns:
        None

    Raises:
        AdminAccessError: If the PIN is wrong or locked.
    """
    check_pin(pin, now)
    start_admin_session(METHOD_PIN)


def enter_with_nfc(uid):
    """
    Purpose: Enters admin mode with the NFC master card.

    Args:
        uid (str): UID of the tapped card.

    Returns:
        None

    Raises:
        AdminAccessError: If the card is not an active master card.
    """
    card = NfcCard.query.filter_by(uid=uid, is_master=True, is_active=True).first()
    if card is None:
        raise AdminAccessError("This card cannot open admin mode.")
    start_admin_session(f"{METHOD_NFC} {card.uid}")


def enter_with_service_key():
    """
    Purpose: Enters admin mode with the (simulated) service key.

    Args:
        None

    Returns:
        None
    """
    start_admin_session(METHOD_SERVICE_KEY)


def is_master_card(uid):
    """
    Purpose: Checks whether a tapped card is an active NFC master card.

    Args:
        uid (str): UID of the tapped card.

    Returns:
        bool: True for a master card.
    """
    return NfcCard.query.filter_by(uid=uid, is_master=True, is_active=True).first() is not None


def get_admin_actor():
    """
    Purpose: Returns how the current admin session was opened.

    Args:
        None

    Returns:
        str | None: The access method, or None when not in admin mode.
    """
    data = session.get(SESSION_ADMIN_KEY)
    return data["actor"] if data else None


def is_admin_expired(now=None):
    """
    Purpose: Checks whether the admin session has been inactive too long.

    Args:
        now (float | None): Current Unix time; defaults to time.time().

    Returns:
        bool: True if in admin mode and inactive for more than 2 minutes.
    """
    data = session.get(SESSION_ADMIN_KEY)
    if not data:
        return False
    now = time.time() if now is None else now
    return now - data["last_activity"] > admin_timeout_seconds()


def touch_admin():
    """
    Purpose: Records admin activity so the inactivity timer restarts.

    Args:
        None

    Returns:
        None
    """
    data = session.get(SESSION_ADMIN_KEY)
    if data:
        data["last_activity"] = time.time()
        session[SESSION_ADMIN_KEY] = data  # reassign so Flask saves the change


# ---------------------------------------------------------------------------
# Admin actions (each one is audited)
# ---------------------------------------------------------------------------


def refill(actor, ingredient_id, quantity):
    """
    Purpose: Refills an ingredient by a quantity (capped at capacity) and
        logs it.

    Args:
        actor (str): Current admin access method.
        ingredient_id (int | None): ID of the ingredient.
        quantity (str | None): Quantity typed in.

    Returns:
        str: Confirmation message.

    Raises:
        StockLevelError: If the ingredient or quantity is invalid.
    """
    ingredient, before, added = inventory.refill_ingredient(ingredient_id, quantity)
    db.session.commit()
    capped = " (capped at capacity)" if added < float(quantity) else ""
    details = (f"{ingredient.name} +{added:g} {ingredient.unit} "
               f"({before:g} → {ingredient.stock_level:g}){capped}.")
    audit(ACTION_REFILL, actor, details)
    return f"Refilled {details}"


def reset_to_full(actor, ingredient_id):
    """
    Purpose: Resets an ingredient counter to full capacity and logs it.

    Args:
        actor (str): Current admin access method.
        ingredient_id (int | None): ID of the ingredient.

    Returns:
        str: Confirmation message.

    Raises:
        StockLevelError: If the ingredient is unknown.
    """
    ingredient, before = inventory.reset_ingredient(ingredient_id)
    db.session.commit()
    details = f"{ingredient.name} reset to full ({before:g} → {ingredient.capacity:g} {ingredient.unit})."
    audit(ACTION_RESET, actor, details)
    return details


def edit_limits(actor, ingredient_id, minimum, capacity):
    """
    Purpose: Changes an ingredient's minimum and capacity and logs it.

    Args:
        actor (str): Current admin access method.
        ingredient_id (int | None): ID of the ingredient.
        minimum (str | None): New minimum.
        capacity (str | None): New capacity.

    Returns:
        str: Confirmation message.

    Raises:
        StockLevelError: If a value is invalid.
    """
    ingredient, old_min, old_cap = inventory.set_limits(ingredient_id, minimum, capacity)
    db.session.commit()
    details = (f"{ingredient.name}: minimum {old_min:g} → {ingredient.min_threshold:g}, "
               f"capacity {old_cap:g} → {ingredient.capacity:g} {ingredient.unit}.")
    audit(ACTION_LIMITS, actor, details)
    return details


def empty_bin(actor, bin_id):
    """
    Purpose: Empties a waste bin and logs it.

    Args:
        actor (str): Current admin access method.
        bin_id (int | None): ID of the bin.

    Returns:
        str: Confirmation message.

    Raises:
        MaintenanceError: If the bin is unknown.
    """
    current = db.session.get(WasteBin, bin_id) if bin_id else None
    before = current.level_percent if current is not None else 0
    bin_ = maintenance.empty_bin(bin_id)  # raises for an unknown bin
    db.session.commit()
    details = f"{bin_.name} bin emptied ({before:.0f}% → 0%)."
    audit(ACTION_EMPTY_BIN, actor, details)
    return details


def resolve(actor, event_id):
    """
    Purpose: Resolves an active error event (e.g. a fixed fault) and logs it.

    Args:
        actor (str): Current admin access method.
        event_id (int | None): ID of the event.

    Returns:
        str: Confirmation message.

    Raises:
        MaintenanceError: If the event is not active.
    """
    event = maintenance.resolve_event(event_id)
    db.session.commit()
    info = maintenance.describe_event(event)
    details = f"Resolved {info['code']} {info['title']} (event #{event.id})."
    audit(ACTION_RESOLVE, actor, details)
    return details


def exit_admin(reason="manual"):
    """
    Purpose: Leaves admin mode and logs the exit.

    Args:
        reason (str): "manual" (Exit button) or "timeout" (inactivity).

    Returns:
        bool: True if admin mode was active.
    """
    actor = get_admin_actor()
    if actor is None:
        return False
    session.pop(SESSION_ADMIN_KEY, None)
    audit(ACTION_EXIT, actor, "Left admin mode (Exit button)." if reason == "manual"
          else "Left admin mode after 2 minutes of inactivity.")
    return True
