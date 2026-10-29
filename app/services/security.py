"""Password and PIN hashing helpers (NFR-09)."""

import bcrypt
from flask import current_app, has_app_context

DEFAULT_BCRYPT_ROUNDS = 12
# bcrypt only accepts up to 72 bytes; bcrypt 5 raises ValueError above that
MAX_SECRET_BYTES = 72


def _bcrypt_rounds():
    """
    Purpose: Reads the bcrypt cost factor from the app config (tests use a
        low value to stay fast).

    Args:
        None

    Returns:
        int: Number of bcrypt rounds to use.
    """
    if has_app_context():
        return current_app.config.get("BCRYPT_ROUNDS", DEFAULT_BCRYPT_ROUNDS)
    return DEFAULT_BCRYPT_ROUNDS


def hash_secret(plain_text):
    """
    Purpose: Hashes a password or PIN with bcrypt so it is never stored in
        plain text (NFR-09).

    Args:
        plain_text (str): The password or PIN entered by the user
            (at most 72 bytes in UTF-8).

    Returns:
        str: The bcrypt hash, salt included, as a UTF-8 string.
    """
    salt = bcrypt.gensalt(rounds=_bcrypt_rounds())
    return bcrypt.hashpw(plain_text.encode("utf-8"), salt).decode("utf-8")


def verify_secret(plain_text, hashed):
    """
    Purpose: Checks a password or PIN against its stored bcrypt hash.

    Args:
        plain_text (str): The password or PIN entered by the user.
        hashed (str): The hash stored in the database.

    Returns:
        bool: True if the secret matches the hash, otherwise False.
    """
    secret = plain_text.encode("utf-8")
    if len(secret) > MAX_SECRET_BYTES:
        return False  # could never have been stored, and bcrypt would raise
    return bcrypt.checkpw(secret, hashed.encode("utf-8"))
