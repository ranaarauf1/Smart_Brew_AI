"""Tests for feature 2: menu browsing by category with nutrition (FR-11, FR-12)."""

import re

from app import db
from app.models import Category, Drink


def category_id(app, name):
    """
    Purpose: Looks up a category's database ID by name.

    Args:
        app (Flask): The test application.
        name (str): Category name.

    Returns:
        int: The category ID.
    """
    with app.app_context():
        return Category.query.filter_by(name=name).one().id


def drink_id(app, name):
    """
    Purpose: Looks up a drink's database ID by name.

    Args:
        app (Flask): The test application.
        name (str): Drink name.

    Returns:
        int: The drink ID.
    """
    with app.app_context():
        return Drink.query.filter_by(name=name).one().id


def test_menu_shows_all_category_tabs_in_order(seeded_client):
    """
    Purpose: The menu shows the 4 category tabs in their sort order (FR-11).

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    html = seeded_client.get("/menu").get_data(as_text=True)

    positions = [html.index(name) for name in
                 ("Espresso Classics", "Milk Coffees", "Tea &amp; Chocolate", "Iced Drinks")]
    assert positions == sorted(positions)


def test_menu_defaults_to_first_category(seeded_client):
    """
    Purpose: Without a category parameter, the first category's drinks show.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.get("/menu")

    assert response.status_code == 200
    for name in (b"Espresso", b"Doppio", b"Americano"):
        assert name in response.data
    assert b"Cappuccino" not in response.data


def test_menu_selected_category_shows_its_drinks_with_prices(seeded_app, seeded_client):
    """
    Purpose: Choosing a tab shows that category's drinks with euro prices.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.get(f"/menu?category={category_id(seeded_app, 'Milk Coffees')}")
    html = response.get_data(as_text=True)

    for name, price in (("Cappuccino", "€3.30"), ("Latte", "€3.50"),
                        ("Flat White", "€3.40"), ("Mocha", "€3.90")):
        assert name in html
        assert price in html
    assert "Espresso<" not in html
    assert 'tab tab-active' in html


def test_menu_shows_drink_icons(seeded_app, seeded_client):
    """
    Purpose: Each drink button shows its icon.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    html = seeded_client.get(
        f"/menu?category={category_id(seeded_app, 'Iced Drinks')}"
    ).get_data(as_text=True)

    assert html.count('class="drink-icon">🧊') == 2


def test_menu_unknown_category_returns_404(seeded_client):
    """
    Purpose: An unknown category ID shows the 404 page.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.get("/menu?category=9999")

    assert response.status_code == 404
    assert b"isn't here" in response.data


def test_menu_hides_inactive_drinks(seeded_app, seeded_client):
    """
    Purpose: Drinks marked inactive are not listed.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    with seeded_app.app_context():
        Drink.query.filter_by(name="Doppio").one().is_active = False
        db.session.commit()

    response = seeded_client.get("/menu")

    assert b"Doppio" not in response.data
    assert b"Espresso" in response.data


def test_menu_with_empty_database_shows_hint(client):
    """
    Purpose: Without seeded data the menu still loads and explains what to do.

    Args:
        client (FlaskClient): Client for the empty-database app.

    Returns:
        None
    """
    response = client.get("/menu")

    assert response.status_code == 200
    assert b"seed.py" in response.data


def test_drink_detail_reachable_in_two_touches(seeded_app, seeded_client):
    """
    Purpose: From the menu, one tab touch and one drink touch reach the
        detail page (FR-12: within 2 touches).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    # Touch 1: the tab link on the menu page
    menu_html = seeded_client.get("/menu").get_data(as_text=True)
    tab_href = re.search(r'href="(/menu\?category=\d+)"[^>]*>Tea &amp; Chocolate', menu_html).group(1)
    tab_html = seeded_client.get(tab_href).get_data(as_text=True)

    # Touch 2: the drink button on that tab
    detail_href = f"/drink/{drink_id(seeded_app, 'Chai Latte')}"
    assert f'href="{detail_href}"' in tab_html
    assert seeded_client.get(detail_href).status_code == 200


def test_drink_detail_shows_nutrition(seeded_app, seeded_client):
    """
    Purpose: The detail page shows calories, sugar, fat, protein and caffeine (FR-12).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Mocha')}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Mocha" in html
    assert "€3.90" in html
    for label, value in (("Calories", "250 kcal"), ("Sugar", "28 g"), ("Fat", "10 g"),
                         ("Protein", "9 g"), ("Caffeine", "130 mg")):
        assert f"<dt>{label}</dt>" in html
        assert f"<dd>{value}</dd>" in html


def test_drink_detail_formats_decimal_nutrition(seeded_app, seeded_client):
    """
    Purpose: Decimal values such as 6.5 g fat are shown without rounding.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    html = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Flat White')}").get_data(as_text=True)

    assert "<dd>6.5 g</dd>" in html


def test_drink_detail_unknown_drink_returns_404(seeded_client):
    """
    Purpose: An unknown drink ID shows the 404 page.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    assert seeded_client.get("/drink/9999").status_code == 404


def test_drink_detail_inactive_drink_returns_404(seeded_app, seeded_client):
    """
    Purpose: An inactive drink cannot be opened directly by URL.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    doppio_id = drink_id(seeded_app, "Doppio")
    with seeded_app.app_context():
        db.session.get(Drink, doppio_id).is_active = False
        db.session.commit()

    assert seeded_client.get(f"/drink/{doppio_id}").status_code == 404


def test_menu_works_for_logged_in_user(seeded_app, seeded_client, login):
    """
    Purpose: Logged-in users see the same menu and detail pages as guests.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    menu = seeded_client.get("/menu")
    detail = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Latte')}")

    assert menu.status_code == 200
    assert b"Espresso" in menu.data
    assert b"Alice Example" in menu.data  # status bar shows the user
    assert detail.status_code == 200
