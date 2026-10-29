"""Accounts, login (password and NFC) and session handling (feature 1).

Covers FR-01–03 (accounts, login, session end), NFR-09 (hashed secrets)
and NFR-14 (60 s inactivity timeout).
"""

import re
import time

from flask import current_app, session

from app import db
from app.models import NfcCard, Transaction, User, UserPreference, utcnow
from app.services.security import MAX_SECRET_BYTES, hash_secret, verify_secret

MIN_PASSWORD_LENGTH = 8
MAX_NAME_LENGTH = 80
MAX_EMAIL_LENGTH = 120
# Simple shape check: something@something.tld, no spaces
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LOGIN_FAILED_MESSAGE = "Email or password is incorrect."
TIMEOUT_MESSAGE = "You were logged out after 60 seconds of inactivity."

SESSION_USER_KEY = "user_id"
SESSION_ACTIVITY_KEY = "last_activity"


class NfcLoginError(Exception):
    """Raised when a tapped NFC card cannot be used to log in."""


class CardLinkError(Exception):
    """Raised when an NFC card cannot be linked to an account."""


# ---------------------------------------------------------------------------
# Registration and password login
# ---------------------------------------------------------------------------


def normalize_email(email):
    """
    Purpose: Trims and lowercases an email so lookups ignore case and spaces.

    Args:
        email (str | None): Email as typed by the user.

    Returns:
        str: The normalized email ("" if None was given).
    """
    return (email or "").strip().lower()


def validate_registration(name, email, password):
    """
    Purpose: Checks the create-account form fields (FR-01).

    Args:
        name (str): Display name (already trimmed).
        email (str): Normalized email.
        password (str): Password in plain text.

    Returns:
        dict[str, str]: Error message per field name; empty if all valid.
    """
    errors = {}

    if not name:
        errors["name"] = "Please enter your name."
    elif len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"Name can be at most {MAX_NAME_LENGTH} characters."

    if not EMAIL_PATTERN.match(email) or len(email) > MAX_EMAIL_LENGTH:
        errors["email"] = "Please enter a valid email address."
    elif User.query.filter_by(email=email).first():
        errors["email"] = "An account with this email already exists."

    if len(password) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    elif len(password.encode("utf-8")) > MAX_SECRET_BYTES:
        errors["password"] = "Password is too long (maximum 72 bytes)."

    return errors


def register_user(name, email, password):
    """
    Purpose: Creates a new account with a hashed password and an empty
        preference profile (FR-01, NFR-09).

    Args:
        name (str): Display name.
        email (str): Email as typed by the user.
        password (str): Password in plain text.

    Returns:
        tuple[User | None, dict[str, str]]: The new user and no errors, or
            None and the error message per field.
    """
    name = (name or "").strip()
    email = normalize_email(email)
    password = password or ""

    errors = validate_registration(name, email, password)
    if errors:
        return None, errors

    user = User(name=name, email=email, password_hash=hash_secret(password))
    user.preference = UserPreference()
    db.session.add(user)
    db.session.commit()
    return user, {}


def authenticate(email, password):
    """
    Purpose: Checks email and password for login (FR-02).

    Args:
        email (str): Email as typed by the user.
        password (str): Password in plain text.

    Returns:
        User | None: The matching user, or None if the details are wrong.
            The caller shows one generic message for every failure so it
            does not reveal which emails have accounts.
    """
    user = User.query.filter_by(email=normalize_email(email)).first()
    if user is None or not verify_secret(password or "", user.password_hash):
        return None
    return user


# ---------------------------------------------------------------------------
# NFC login and card linking
# ---------------------------------------------------------------------------


def login_with_nfc(uid):
    """
    Purpose: Finds the user for a tapped NFC card (FR-02).

    Args:
        uid (str): UID of the tapped card.

    Returns:
        User: The user the card is linked to.

    Raises:
        NfcLoginError: If the card is unknown, inactive, a master card or not
            linked to an account. The message is shown to the user.
    """
    card = NfcCard.query.filter_by(uid=uid).first()
    if card is None or not card.is_active:
        raise NfcLoginError("This card is not recognised. Please log in with your password.")
    if card.is_master:
        # Admin mode by master card is built with feature 10
        raise NfcLoginError("The technician card opens admin mode, which is not available yet.")
    if card.user is None:
        raise NfcLoginError(
            "This card is not linked to an account. Log in with your password "
            "and link the card on your profile page."
        )
    return card.user


def get_linkable_cards():
    """
    Purpose: Lists the demo cards that a user may link to their account.

    Args:
        None

    Returns:
        list[NfcCard]: Active, non-master cards not linked to anyone.
    """
    return (
        NfcCard.query.filter_by(user_id=None, is_master=False, is_active=True)
        .order_by(NfcCard.uid)
        .all()
    )


def link_card(user, card_id):
    """
    Purpose: Links an NFC card to the user, replacing their previous card
        (one card per user).

    Args:
        user (User): The logged-in user.
        card_id (int): ID of the card to link.

    Returns:
        NfcCard: The newly linked card.

    Raises:
        CardLinkError: If the card does not exist, is inactive, is a master
            card or belongs to another user.
    """
    card = db.session.get(NfcCard, card_id)
    if card is None or not card.is_active:
        raise CardLinkError("That card could not be found.")
    if card.is_master:
        raise CardLinkError("The technician master card cannot be linked to an account.")
    if card.user_id is not None and card.user_id != user.id:
        raise CardLinkError("That card is already linked to another account.")

    for old_card in user.nfc_cards:
        if old_card.id != card.id:
            old_card.user = None
    card.user = user
    db.session.commit()
    return card


def unlink_card(user):
    """
    Purpose: Removes the link between the user and their NFC card.

    Args:
        user (User): The logged-in user.

    Returns:
        bool: True if a card was unlinked, False if none was linked.
    """
    cards = list(user.nfc_cards)
    for card in cards:
        card.user = None
    db.session.commit()
    return bool(cards)


def get_linked_card(user):
    """
    Purpose: Returns the NFC card linked to the user, if any.

    Args:
        user (User): The user.

    Returns:
        NfcCard | None: The linked card, or None.
    """
    return user.nfc_cards[0] if user.nfc_cards else None


def get_order_history(user):
    """
    Purpose: Returns the user's past orders, newest first (FR-05, FR-06).

    Args:
        user (User): The user.

    Returns:
        list[Transaction]: The user's transactions (empty until payments exist).
    """
    return (
        Transaction.query.filter_by(user_id=user.id)
        .order_by(Transaction.created_at.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Session handling (FR-03, NFR-14)
# ---------------------------------------------------------------------------


def session_timeout_seconds():
    """
    Purpose: Reads the inactivity timeout from the app config.

    Args:
        None

    Returns:
        int: Seconds of inactivity before automatic logout.
    """
    return current_app.config["SESSION_TIMEOUT_SECONDS"]


def start_session(user):
    """
    Purpose: Logs the user in by storing their ID in a fresh session.

    Args:
        user (User): The user who has just authenticated.

    Returns:
        None
    """
    session.clear()  # drop any old session data so it cannot be reused
    session[SESSION_USER_KEY] = user.id
    session[SESSION_ACTIVITY_KEY] = time.time()
    user.last_login_at = utcnow()
    db.session.commit()


def end_session():
    """
    Purpose: Logs the current user out by clearing the session (FR-03).

    Args:
        None

    Returns:
        None
    """
    # Also called after every successful purchase (FR-01–03), see
    # routes/customer.py finish_payment_request()
    session.clear()


def is_session_expired(now=None):
    """
    Purpose: Checks whether the logged-in session has been inactive too long
        (NFR-14).

    Args:
        now (float | None): Current Unix time; defaults to time.time().

    Returns:
        bool: True if a user is logged in and the timeout has passed.
    """
    if SESSION_USER_KEY not in session:
        return False
    now = time.time() if now is None else now
    last = session.get(SESSION_ACTIVITY_KEY, 0)
    return now - last > session_timeout_seconds()


def touch_session():
    """
    Purpose: Records user activity so the inactivity timer restarts.

    Args:
        None

    Returns:
        None
    """
    if SESSION_USER_KEY in session:
        session[SESSION_ACTIVITY_KEY] = time.time()


def get_session_user():
    """
    Purpose: Loads the user stored in the current session.

    Args:
        None

    Returns:
        User | None: The logged-in user, or None for a guest. A session for
            a deleted user is cleared.
    """
    user_id = session.get(SESSION_USER_KEY)
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None:
        session.clear()
    return user
