"""Smoke test: the app starts and the home page loads."""


def test_app_starts_and_home_page_loads(client):
    """
    Purpose: Checks that the app factory works and "/" renders the welcome
        screen, even with an empty database.

    Args:
        client (FlaskClient): Test client fixture.

    Returns:
        None
    """
    response = client.get("/")

    assert response.status_code == 200
    assert b"SmartBrew AI" in response.data
    assert b"Menu" in response.data
