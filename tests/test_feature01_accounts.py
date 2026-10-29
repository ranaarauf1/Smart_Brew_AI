"""Tests for feature 1: accounts, password/NFC login and session handling
(FR-01–03, NFR-09, NFR-14)."""

import time

from app.models import NfcCard, User
from app.services import auth
from tests.conftest import ALICE_EMAIL, ALICE_PASSWORD

NEW_USER = {"name": "Carol Tester", "email": "carol@example.com", "password": "Secret123"}


def get_user(app, email):
    """
    Purpose: Loads a user by email inside an app context.

    Args:
        app (Flask): The test application.
        email (str): Email of the user.

    Returns:
        User | None: The user, or None if not found.
    """
    with app.app_context():
        return User.query.filter_by(email=email).first()


def card_id(app, uid):
    """
    Purpose: Looks up the database ID of a demo NFC card.

    Args:
        app (Flask): The test application.
        uid (str): UID of the card.

    Returns:
        int: The card's ID.
    """
    with app.app_context():
        return NfcCard.query.filter_by(uid=uid).one().id


def card_owner_email(app, uid):
    """
    Purpose: Finds the email of the user a card is linked to.

    Args:
        app (Flask): The test application.
        uid (str): UID of the card.

    Returns:
        str | None: Owner's email, or None if the card is not linked.
    """
    with app.app_context():
        card = NfcCard.query.filter_by(uid=uid).one()
        return card.user.email if card.user else None


def logged_in_user_id(client):
    """
    Purpose: Reads the logged-in user's ID from the test client's session.

    Args:
        client (FlaskClient): The test client.

    Returns:
        int | None: The user ID, or None for a guest.
    """
    with client.session_transaction() as sess:
        return sess.get(auth.SESSION_USER_KEY)


def set_last_activity(client, seconds_ago):
    """
    Purpose: Moves the session's last activity time into the past.

    Args:
        client (FlaskClient): The test client.
        seconds_ago (float): How long ago the last activity should be.

    Returns:
        None
    """
    with client.session_transaction() as sess:
        sess[auth.SESSION_ACTIVITY_KEY] = time.time() - seconds_ago


# ---------------------------------------------------------------------------
# Registration (FR-01, NFR-09)
# ---------------------------------------------------------------------------


def test_register_creates_hashed_account_with_empty_preferences(seeded_app, seeded_client):
    """
    Purpose: A valid registration creates the user with a bcrypt hash, an
        empty preference profile, and logs them in.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post("/register", data=NEW_USER)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/menu")
    with seeded_app.app_context():
        user = User.query.filter_by(email="carol@example.com").one()
        assert user.password_hash != NEW_USER["password"]
        assert user.password_hash.startswith("$2b$")
        assert user.preference is not None
        assert user.preference.preferred_milk_option_id is None
        assert logged_in_user_id(seeded_client) == user.id


def test_register_normalizes_email(seeded_app, seeded_client):
    """
    Purpose: Spaces and upper case in the email are removed before saving.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/register", data={**NEW_USER, "email": "  Carol@Example.COM "})

    assert get_user(seeded_app, "carol@example.com") is not None


def test_register_rejects_invalid_email(seeded_app, seeded_client):
    """
    Purpose: An email without a valid shape is refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "email": "not-an-email"})

    assert response.status_code == 400
    assert b"valid email address" in response.data
    assert get_user(seeded_app, "not-an-email") is None


def test_register_rejects_short_password(seeded_app, seeded_client):
    """
    Purpose: Passwords under 8 characters are refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "password": "short7!"})

    assert response.status_code == 400
    assert b"at least 8 characters" in response.data
    assert get_user(seeded_app, NEW_USER["email"]) is None


def test_register_rejects_password_over_bcrypt_limit(seeded_client):
    """
    Purpose: Passwords over 72 bytes are refused instead of crashing bcrypt.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "password": "x" * 73})

    assert response.status_code == 400
    assert b"too long" in response.data


def test_register_rejects_missing_name(seeded_client):
    """
    Purpose: A blank name is refused.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "name": "   "})

    assert response.status_code == 400
    assert b"enter your name" in response.data


def test_register_rejects_duplicate_email_any_case(seeded_client):
    """
    Purpose: An email that already exists is refused, ignoring case.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "email": "ALICE@demo.com"})

    assert response.status_code == 400
    assert b"already exists" in response.data


def test_register_keeps_entered_values_but_not_password(seeded_client):
    """
    Purpose: After an error the form keeps name and email but never echoes
        the password back.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post("/register", data={**NEW_USER, "password": "short"})

    assert b"Carol Tester" in response.data
    assert b'value="short"' not in response.data


# ---------------------------------------------------------------------------
# Password login and logout (FR-02, FR-03)
# ---------------------------------------------------------------------------


def test_login_with_correct_details(seeded_app, seeded_client, login):
    """
    Purpose: Correct email and password log the user in and record the time.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    response = login()

    assert response.status_code == 302
    alice = get_user(seeded_app, ALICE_EMAIL)
    assert logged_in_user_id(seeded_client) == alice.id
    assert alice.last_login_at is not None


def test_login_email_is_case_insensitive(seeded_client, login):
    """
    Purpose: Logging in works with the email in different case.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    assert login(email="Alice@Demo.com").status_code == 302


def test_login_wrong_password_and_unknown_email_get_same_error(seeded_client, login):
    """
    Purpose: Wrong password and unknown email show the same message, so the
        page does not reveal which emails exist.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    wrong_password = login(password="WrongPass1")
    unknown_email = login(email="nobody@demo.com")

    for response in (wrong_password, unknown_email):
        assert response.status_code == 401
        assert auth.LOGIN_FAILED_MESSAGE.encode() in response.data
    assert logged_in_user_id(seeded_client) is None


def test_logout_ends_session(seeded_client, login):
    """
    Purpose: The Log out button clears the session.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post("/logout", data={"reason": "manual"}, follow_redirects=True)

    assert logged_in_user_id(seeded_client) is None
    assert b"You have been logged out." in response.data


def test_logout_requires_post(seeded_client, login):
    """
    Purpose: A GET request (e.g. a plain link) cannot log the user out.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    assert seeded_client.get("/logout").status_code == 405
    assert logged_in_user_id(seeded_client) is not None


def test_logged_in_pages_show_countdown_and_logout(seeded_client, login):
    """
    Purpose: Logged-in pages show the 60 s countdown and the Log out button;
        guest pages do not.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    guest_page = seeded_client.get("/").data
    login()
    user_page = seeded_client.get("/").data

    assert b'id="countdown"' not in guest_page
    assert b'id="countdown"' in user_page
    assert b'data-timeout="60"' in user_page
    assert b"Log out" in user_page


# ---------------------------------------------------------------------------
# Session timeout (NFR-14)
# ---------------------------------------------------------------------------


def test_session_expires_after_60_seconds_inactivity(seeded_client, login):
    """
    Purpose: A request more than 60 s after the last activity logs the user
        out and shows the timeout message.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 61)

    response = seeded_client.get("/")

    assert logged_in_user_id(seeded_client) is None
    assert auth.TIMEOUT_MESSAGE.encode() in response.data


def test_session_stays_alive_with_activity(seeded_client, login):
    """
    Purpose: Activity within 60 s keeps the session and restarts the timer.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 50)

    assert seeded_client.get("/profile").status_code == 200
    with seeded_client.session_transaction() as sess:
        assert time.time() - sess[auth.SESSION_ACTIVITY_KEY] < 5


def test_keepalive_restarts_timer(seeded_client, login):
    """
    Purpose: The keepalive call from the countdown script counts as activity.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 55)

    assert seeded_client.post("/auth/keepalive").status_code == 204
    with seeded_client.session_transaction() as sess:
        assert time.time() - sess[auth.SESSION_ACTIVITY_KEY] < 5


def test_keepalive_after_timeout_returns_401(seeded_client, login):
    """
    Purpose: A keepalive after the timeout ends the session and returns 401.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 61)

    assert seeded_client.post("/auth/keepalive").status_code == 401
    assert logged_in_user_id(seeded_client) is None


def test_keepalive_for_guest_returns_401(seeded_client):
    """
    Purpose: A guest has no session to keep alive.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    assert seeded_client.post("/auth/keepalive").status_code == 401


def test_countdown_logout_shows_timeout_message_once(seeded_client, login):
    """
    Purpose: When the countdown reaches 0 the script posts reason=timeout;
        the user sees the timeout message exactly once.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 61)

    response = seeded_client.post("/logout", data={"reason": "timeout"}, follow_redirects=True)

    assert logged_in_user_id(seeded_client) is None
    assert response.data.count(auth.TIMEOUT_MESSAGE.encode()) == 1


def test_countdown_logout_just_before_server_timeout(seeded_client, login):
    """
    Purpose: If the browser countdown reaches 0 slightly before the server's
        timer, the user is still logged out with the timeout message once.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    set_last_activity(seeded_client, 59)

    response = seeded_client.post("/logout", data={"reason": "timeout"}, follow_redirects=True)

    assert logged_in_user_id(seeded_client) is None
    assert response.data.count(auth.TIMEOUT_MESSAGE.encode()) == 1


# ---------------------------------------------------------------------------
# NFC login via the Simulation panel (FR-02)
# ---------------------------------------------------------------------------


def test_simulation_panel_lists_demo_cards(seeded_client):
    """
    Purpose: The Simulation panel shows every demo card with a Tap button.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.get("/simulation/")

    assert response.status_code == 200
    for uid in (b"04A1B2C3", b"04B2C3D4", b"04FFEE01", b"04DEAD99"):
        assert uid in response.data
    assert b"Linked to Alice Example" in response.data


def test_nfc_tap_with_linked_card_logs_in(seeded_app, seeded_client):
    """
    Purpose: Tapping a linked card logs in its owner.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post("/simulation/nfc-tap", data={"uid": "04B2C3D4"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/menu")
    assert logged_in_user_id(seeded_client) == get_user(seeded_app, "bob@demo.com").id


def test_nfc_tap_with_unlinked_card_offers_password_login(seeded_client):
    """
    Purpose: An unlinked card is refused and the user is sent to password login.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post(
        "/simulation/nfc-tap", data={"uid": "04DEAD99"}, follow_redirects=True
    )

    assert logged_in_user_id(seeded_client) is None
    assert b"not linked to an account" in response.data
    assert b"Log in with your password" in response.data
    assert response.request.path == "/login"


def test_nfc_tap_with_unknown_card_is_refused(seeded_client):
    """
    Purpose: A UID that is not in the database is refused.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post(
        "/simulation/nfc-tap", data={"uid": "FFFFFFFF"}, follow_redirects=True
    )

    assert logged_in_user_id(seeded_client) is None
    assert b"not recognised" in response.data


def test_nfc_tap_with_inactive_card_is_refused(seeded_app, seeded_client):
    """
    Purpose: A deactivated card cannot log in, even if linked.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    from app import db

    with seeded_app.app_context():
        NfcCard.query.filter_by(uid="04A1B2C3").one().is_active = False
        db.session.commit()

    seeded_client.post("/simulation/nfc-tap", data={"uid": "04A1B2C3"})

    assert logged_in_user_id(seeded_client) is None


def test_nfc_tap_with_master_card_does_not_log_in_customer(seeded_client):
    """
    Purpose: The technician master card is not a customer login.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post(
        "/simulation/nfc-tap", data={"uid": "04FFEE01"}, follow_redirects=True
    )

    assert logged_in_user_id(seeded_client) is None
    assert b"admin mode" in response.data


# ---------------------------------------------------------------------------
# Profile and card linking
# ---------------------------------------------------------------------------


def test_profile_requires_login(seeded_client):
    """
    Purpose: Guests are redirected from the profile to the login page.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.get("/profile")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_profile_shows_name_card_and_empty_history(seeded_client, login):
    """
    Purpose: The profile shows the user's name, linked card and, for a user
        without orders (Bob), an empty history.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login(email="bob@demo.com", password="Bob2026!")

    response = seeded_client.get("/profile")

    assert response.status_code == 200
    assert b"Bob Example" in response.data
    assert b"04B2C3D4" in response.data
    assert b"No orders yet." in response.data


def test_profile_shows_seeded_order_history(seeded_client, login):
    """
    Purpose: Alice's seeded past orders appear on her profile.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    html = seeded_client.get("/profile").get_data(as_text=True)

    assert "No orders yet." not in html
    assert html.count("Flat White") == 3
    assert "Oat milk · Strong · Hot" in html


def test_link_card_replaces_previous_card(seeded_app, seeded_client, login):
    """
    Purpose: Linking a free card links it and unlinks the user's old card.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post(
        "/profile/link-card",
        data={"card_id": card_id(seeded_app, "04DEAD99")},
        follow_redirects=True,
    )

    assert b"now linked" in response.data
    assert card_owner_email(seeded_app, "04DEAD99") == ALICE_EMAIL
    assert card_owner_email(seeded_app, "04A1B2C3") is None


def test_newly_linked_card_logs_in_by_tap(seeded_app, seeded_client, login):
    """
    Purpose: After linking, tapping the card logs in that user.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()
    seeded_client.post("/profile/link-card", data={"card_id": card_id(seeded_app, "04DEAD99")})
    seeded_client.post("/logout")

    seeded_client.post("/simulation/nfc-tap", data={"uid": "04DEAD99"})

    assert logged_in_user_id(seeded_client) == get_user(seeded_app, ALICE_EMAIL).id


def test_link_card_owned_by_another_user_is_refused(seeded_app, seeded_client, login):
    """
    Purpose: A card linked to someone else cannot be taken over.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post(
        "/profile/link-card",
        data={"card_id": card_id(seeded_app, "04B2C3D4")},
        follow_redirects=True,
    )

    assert b"already linked to another account" in response.data
    assert card_owner_email(seeded_app, "04B2C3D4") == "bob@demo.com"


def test_link_master_card_is_refused(seeded_app, seeded_client, login):
    """
    Purpose: The technician master card cannot be linked to a customer.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post(
        "/profile/link-card",
        data={"card_id": card_id(seeded_app, "04FFEE01")},
        follow_redirects=True,
    )

    assert b"cannot be linked" in response.data
    assert card_owner_email(seeded_app, "04FFEE01") is None


def test_link_card_with_missing_or_unknown_id(seeded_client, login):
    """
    Purpose: A missing or unknown card ID shows an error instead of crashing.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    missing = seeded_client.post("/profile/link-card", data={}, follow_redirects=True)
    unknown = seeded_client.post(
        "/profile/link-card", data={"card_id": 9999}, follow_redirects=True
    )

    assert b"Please choose a card." in missing.data
    assert b"could not be found" in unknown.data


def test_unlink_card(seeded_app, seeded_client, login):
    """
    Purpose: Unlinking removes the user's card link.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post("/profile/unlink-card", follow_redirects=True)

    assert b"has been unlinked" in response.data
    assert card_owner_email(seeded_app, "04A1B2C3") is None


def test_card_linking_requires_login(seeded_app, seeded_client):
    """
    Purpose: Guests cannot link cards.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post(
        "/profile/link-card", data={"card_id": card_id(seeded_app, "04DEAD99")}
    )

    assert response.headers["Location"].endswith("/login")
    assert card_owner_email(seeded_app, "04DEAD99") is None


def test_password_is_verified_against_hash(seeded_app):
    """
    Purpose: The service accepts the right password and rejects others (NFR-09).

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        assert auth.authenticate(ALICE_EMAIL, ALICE_PASSWORD) is not None
        assert auth.authenticate(ALICE_EMAIL, "alice2026!") is None
        assert auth.authenticate(ALICE_EMAIL, "x" * 100) is None
