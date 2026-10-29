"""Simulated card reader and phone (NFC) reader for payments (feature 7).

This is the ONLY place where full demo card numbers exist. Reading a card
returns its brand, last 4 digits and a stand-in token that the simulated
processor understands; the full number never leaves this module and is
never stored (NFR-08).
"""

from dataclasses import dataclass

from app.models import Payment

# Demo payment cards (fixed, so tests are predictable)
DEMO_CARDS = {
    "visa-4242": {"number": "4242424242424242", "brand": "Visa", "note": "Always approved"},
    "mastercard-4444": {"number": "5555555555554444", "brand": "Mastercard", "note": "Always approved"},
    "visa-9995": {"number": "4000000000009995", "brand": "Visa", "note": "Declined: insufficient funds"},
    "visa-0119": {"number": "4000000000000119", "brand": "Visa", "note": "Processor error"},
}

# Demo phones with Apple Pay / Google Pay (the card stored in the wallet)
DEMO_WALLETS = {
    "apple-alice": {
        "method": Payment.METHOD_APPLE_PAY, "device": "Alice's iPhone",
        "number": "4242424242424242", "brand": "Visa", "note": "Approved",
    },
    "apple-blocked": {
        "method": Payment.METHOD_APPLE_PAY, "device": "Blocked iPhone",
        "number": "4000000000000002", "brand": "Visa", "note": "Declined by the wallet",
    },
    "google-bob": {
        "method": Payment.METHOD_GOOGLE_PAY, "device": "Bob's Pixel",
        "number": "5555555555554444", "brand": "Mastercard", "note": "Approved",
    },
}


class CardReadError(Exception):
    """Raised when the presented card or phone cannot be read."""


@dataclass
class CardRead:
    """What the reader passes on: no full card number (NFR-08)."""

    brand: str
    last4: str
    token: str  # stand-in for the encrypted card data a real reader sends
    label: str  # e.g. "Visa •••• 4242"


def _to_read(number, brand, label_prefix=""):
    """
    Purpose: Turns a demo card number into the data the reader passes on,
        dropping the full number.

    Args:
        number (str): Full demo card number (stays in this module).
        brand (str): Card brand, e.g. "Visa".
        label_prefix (str): Text before the masked number, e.g. "Apple Pay · ".

    Returns:
        CardRead: Brand, last 4 digits, token and display label.
    """
    last4 = number[-4:]
    return CardRead(
        brand=brand,
        last4=last4,
        token=f"tok_{brand.lower()}_{last4}",
        label=f"{label_prefix}{brand} •••• {last4}",
    )


def read_card(instrument_id):
    """
    Purpose: Simulates inserting or tapping a demo payment card.

    Args:
        instrument_id (str): Key of DEMO_CARDS, e.g. "visa-4242".

    Returns:
        CardRead: The card data without the full number.

    Raises:
        CardReadError: If the card is unknown.
    """
    card = DEMO_CARDS.get(instrument_id)
    if card is None:
        raise CardReadError("The card could not be read.")
    return _to_read(card["number"], card["brand"])


def read_wallet(method, instrument_id):
    """
    Purpose: Simulates holding a phone with Apple Pay or Google Pay near
        the reader.

    Args:
        method (str): Payment.METHOD_APPLE_PAY or METHOD_GOOGLE_PAY.
        instrument_id (str): Key of DEMO_WALLETS.

    Returns:
        CardRead: The wallet card data without the full number.

    Raises:
        CardReadError: If the phone is unknown or uses the other wallet.
    """
    wallet = DEMO_WALLETS.get(instrument_id)
    if wallet is None or wallet["method"] != method:
        raise CardReadError("The phone could not be read.")
    prefix = "Apple Pay · " if method == Payment.METHOD_APPLE_PAY else "Google Pay · "
    return _to_read(wallet["number"], wallet["brand"], prefix)


def list_cards():
    """
    Purpose: Lists the demo cards for the "Insert card" / "Tap card" screen.

    Args:
        None

    Returns:
        list[dict]: Entries with id, label and note (no full numbers).
    """
    return [
        {"id": key, "label": _to_read(c["number"], c["brand"]).label, "note": c["note"]}
        for key, c in DEMO_CARDS.items()
    ]


def list_wallets(method):
    """
    Purpose: Lists the demo phones for one wallet type.

    Args:
        method (str): Payment.METHOD_APPLE_PAY or METHOD_GOOGLE_PAY.

    Returns:
        list[dict]: Entries with id, label and note (no full numbers).
    """
    return [
        {"id": key, "label": f"{w['device']} ({w['brand']} •••• {w['number'][-4:]})", "note": w["note"]}
        for key, w in DEMO_WALLETS.items()
        if w["method"] == method
    ]
