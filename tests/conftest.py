"""Shared pytest fixtures for SmartBrew AI."""

import pytest

from app import create_app
from seed import seed_database

TEST_CONFIG = {
    "TESTING": True,
    "SECRET_KEY": "test-secret",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "BCRYPT_ROUNDS": 4,  # minimum cost: keeps hashing fast in tests
}

ALICE_EMAIL = "alice@demo.com"
ALICE_PASSWORD = "Alice2026!"


def pay(client, order_id, method="card_chip", instrument="visa-4242", **kwargs):
    """
    Purpose: Pays for an order through the payment form (feature 7); with
        the defaults this inserts the always-approved demo Visa card, which
        also dispenses the drink.

    Args:
        client (FlaskClient): The test client.
        order_id (int): ID of the pending order.
        method (str): Payment method, e.g. "card_chip" or "prepaid".
        instrument (str): Demo card/phone key or prepaid card number.
        **kwargs: Extra arguments for client.post, e.g. follow_redirects=True.

    Returns:
        TestResponse: The response to the payment POST.
    """
    return client.post(
        f"/order/{order_id}/pay", data={"method": method, "instrument": instrument}, **kwargs
    )


@pytest.fixture
def app():
    """
    Purpose: Creates an app instance backed by a fresh, empty in-memory database.

    Args:
        None

    Returns:
        Flask: The test application.
    """
    return create_app(dict(TEST_CONFIG))


@pytest.fixture
def client(app):
    """
    Purpose: Provides a Flask test client for the empty-database app.

    Args:
        app (Flask): The test application fixture.

    Returns:
        FlaskClient: Client for the test application.
    """
    return app.test_client()


@pytest.fixture
def seeded_app(app):
    """
    Purpose: Fills the test database with the demo data from seed.py.

    Args:
        app (Flask): The test application fixture.

    Returns:
        Flask: The same application, now with demo data.
    """
    with app.app_context():
        seed_database()
    return app


@pytest.fixture
def seeded_client(seeded_app):
    """
    Purpose: Provides a Flask test client for the app with demo data.

    Args:
        seeded_app (Flask): The seeded test application fixture.

    Returns:
        FlaskClient: Client for the seeded application.
    """
    return seeded_app.test_client()


@pytest.fixture
def login(seeded_client):
    """
    Purpose: Provides a helper that logs in through the login form.

    Args:
        seeded_client (FlaskClient): Client for the seeded application.

    Returns:
        callable: login(email, password) -> Response of the login POST.
    """

    def do_login(email=ALICE_EMAIL, password=ALICE_PASSWORD):
        """
        Purpose: Posts the login form with the given details.

        Args:
            email (str): Email to log in with (Alice by default).
            password (str): Password to log in with.

        Returns:
            TestResponse: The response to the login POST.
        """
        return seeded_client.post("/login", data={"email": email, "password": password})

    return do_login
