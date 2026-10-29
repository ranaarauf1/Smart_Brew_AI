"""Tests for feature 5: saved recipes and one-touch ordering
(FR-07–10, FR-15, NFR-24)."""

import re

import pytest

from app import db
from app.models import CustomizationOption, Drink, Ingredient, SavedRecipe, Transaction, User
from app.services import recipes

RECIPE_CHOICES = {
    "milk": "Oat milk",
    "strength": "Extra shot",
    "temperature": "Hot",
    "syrup": "Vanilla",
}


def option_id(app, group, name):
    """
    Purpose: Looks up a customization option's ID.

    Args:
        app (Flask): The test application.
        group (str): Option group.
        name (str): Option name.

    Returns:
        int: The option ID.
    """
    with app.app_context():
        return CustomizationOption.query.filter_by(group=group, name=name).one().id


def drink_id(app, name):
    """
    Purpose: Looks up a drink's ID by name.

    Args:
        app (Flask): The test application.
        name (str): Drink name.

    Returns:
        int: The drink ID.
    """
    with app.app_context():
        return Drink.query.filter_by(name=name).one().id


def form_for(app, choices, shots=0, name=None):
    """
    Purpose: Builds the customization form data from option names.

    Args:
        app (Flask): The test application.
        choices (dict): Group -> option name.
        shots (int): Number of syrup shots.
        name (str | None): Recipe name to include, if any.

    Returns:
        dict: Form data with option IDs.
    """
    data = {group: option_id(app, group, value) for group, value in choices.items()}
    data["syrup_shots"] = shots
    if name is not None:
        data["recipe_name"] = name
    return data


def set_stock(app, name, level):
    """
    Purpose: Sets an ingredient's stock level directly.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.
        level (float): New stock level.

    Returns:
        None
    """
    with app.app_context():
        Ingredient.query.filter_by(name=name).one().stock_level = level
        db.session.commit()


def create_recipe(app, email, drink, name, choices, shots=0):
    """
    Purpose: Creates a recipe through the service, bypassing the UI.

    Args:
        app (Flask): The test application.
        email (str): Owner's email.
        drink (str): Drink name.
        name (str): Recipe name.
        choices (dict): Group -> option name.
        shots (int): Number of syrup shots.

    Returns:
        int: The new recipe's ID.
    """
    data = form_for(app, choices, shots)
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        drink_obj = Drink.query.filter_by(name=drink).one()
        return recipes.save_recipe(user, drink_obj, name, data).id


def recipe_names(app, email):
    """
    Purpose: Lists the names of a user's recipes.

    Args:
        app (Flask): The test application.
        email (str): Owner's email.

    Returns:
        list[str]: Recipe names.
    """
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        return [r.name for r in recipes.get_user_recipes(user)]


@pytest.fixture
def alice(seeded_client, login):
    """
    Purpose: Logs Alice in on the seeded client.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        FlaskClient: The logged-in client.
    """
    login()
    return seeded_client


@pytest.fixture
def bob(seeded_app):
    """
    Purpose: Provides a second client logged in as Bob.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        FlaskClient: A client logged in as Bob.
    """
    client = seeded_app.test_client()
    client.post("/login", data={"email": "bob@demo.com", "password": "Bob2026!"})
    return client


# ---------------------------------------------------------------------------
# Saving (FR-07)
# ---------------------------------------------------------------------------


def test_save_recipe_from_customize_screen(seeded_app, alice):
    """
    Purpose: "Save as recipe" stores the drink and options and shows the
        recipe in "My recipes" with its summary and current price.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    latte = drink_id(seeded_app, "Latte")

    response = alice.post(f"/drink/{latte}/recipes",
                          data=form_for(seeded_app, RECIPE_CHOICES, shots=2, name=" Morning boost "))
    page = alice.get("/recipes").get_data(as_text=True)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/recipes")
    with seeded_app.app_context():
        recipe = SavedRecipe.query.one()
        assert recipe.name == "Morning boost"
        assert recipe.drink_id == latte
        assert recipe.customizations["syrup_shots"] == 2
        assert recipe.customizations["milk"] == option_id(seeded_app, "milk", "Oat milk")
    assert "Morning boost" in page
    assert "Latte · Oat milk · Extra shot · Hot · 2× Vanilla" in page
    assert "€5.50" in page


def test_customize_screen_offers_saving_only_when_logged_in(seeded_app, seeded_client, login):
    """
    Purpose: Guests see a login hint instead of the save form.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    url = f"/drink/{drink_id(seeded_app, 'Latte')}/customize"

    guest = seeded_client.get(url).get_data(as_text=True)
    login()
    user = seeded_client.get(url).get_data(as_text=True)

    assert "to save your recipes" in guest and 'name="recipe_name"' not in guest
    assert 'name="recipe_name"' in user and "Save as recipe" in user


def test_guest_cannot_save_recipe(seeded_app, seeded_client):
    """
    Purpose: Saving requires login.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post(f"/drink/{drink_id(seeded_app, 'Latte')}/recipes",
                                  data=form_for(seeded_app, RECIPE_CHOICES, name="Mine"))

    assert response.headers["Location"].endswith("/login")
    with seeded_app.app_context():
        assert SavedRecipe.query.count() == 0


@pytest.mark.parametrize("name, message", [
    ("", "Please give your recipe a name."),
    ("   ", "Please give your recipe a name."),
    ("x" * 41, "Recipe names can be at most 40 characters."),
])
def test_save_rejects_invalid_names(seeded_app, alice, name, message):
    """
    Purpose: Empty names and names over 40 characters are refused and the
        screen keeps the choices.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.
        name (str): Name to try.
        message (str): Expected error.

    Returns:
        None
    """
    response = alice.post(f"/drink/{drink_id(seeded_app, 'Latte')}/recipes",
                          data=form_for(seeded_app, RECIPE_CHOICES, shots=1, name=name))

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert recipe_names(seeded_app, "alice@demo.com") == []


def test_name_of_exactly_40_characters_is_allowed(seeded_app, alice):
    """
    Purpose: 40 characters is the maximum, not over it.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    alice.post(f"/drink/{drink_id(seeded_app, 'Latte')}/recipes",
               data=form_for(seeded_app, RECIPE_CHOICES, name="y" * 40))

    assert recipe_names(seeded_app, "alice@demo.com") == ["y" * 40]


def test_duplicate_name_is_refused_ignoring_case(seeded_app, alice):
    """
    Purpose: Names are unique per user, ignoring upper/lower case.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)

    response = alice.post(f"/drink/{drink_id(seeded_app, 'Espresso')}/recipes",
                          data={"recipe_name": "morning BOOST"})

    assert response.status_code == 400
    assert "You already have a recipe called" in response.get_data(as_text=True)
    assert recipe_names(seeded_app, "alice@demo.com") == ["Morning boost"]


def test_other_user_may_use_the_same_name(seeded_app, bob):
    """
    Purpose: Uniqueness is per user, so Bob can reuse Alice's recipe name.

    Args:
        seeded_app (Flask): Seeded test application.
        bob (FlaskClient): Client logged in as Bob.

    Returns:
        None
    """
    create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)

    response = bob.post(f"/drink/{drink_id(seeded_app, 'Espresso')}/recipes",
                        data={"recipe_name": "Morning boost"})

    assert response.status_code == 302
    assert recipe_names(seeded_app, "bob@demo.com") == ["Morning boost"]


def test_out_of_stock_option_cannot_be_saved(seeded_app, alice):
    """
    Purpose: Almond milk (below threshold) cannot be saved into a recipe.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    response = alice.post(f"/drink/{drink_id(seeded_app, 'Latte')}/recipes",
                          data=form_for(seeded_app, {"milk": "Almond milk"}, name="Nutty"))

    assert response.status_code == 400
    assert "Almond milk is out of stock." in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# My recipes: list, rename, edit, delete (FR-08–10)
# ---------------------------------------------------------------------------


def test_recipe_list_requires_login(seeded_client):
    """
    Purpose: Guests are sent to the login page.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    assert seeded_client.get("/recipes").headers["Location"].endswith("/login")


def test_empty_recipe_list(bob):
    """
    Purpose: A user without recipes sees a helpful empty state.

    Args:
        bob (FlaskClient): Client logged in as Bob.

    Returns:
        None
    """
    assert b"No saved recipes yet" in bob.get("/recipes").data


def test_rename_recipe(seeded_app, alice):
    """
    Purpose: Renaming works, keeps the 40-character and uniqueness rules,
        and allows changing only the case of the same recipe's name.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    first = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)
    create_recipe(seeded_app, "alice@demo.com", "Espresso", "Quick one", {})

    ok = alice.post(f"/recipes/{first}/rename", data={"name": "Big latte"}, follow_redirects=True)
    taken = alice.post(f"/recipes/{first}/rename", data={"name": "QUICK ONE"}, follow_redirects=True)
    long = alice.post(f"/recipes/{first}/rename", data={"name": "z" * 41}, follow_redirects=True)
    case = alice.post(f"/recipes/{first}/rename", data={"name": "BIG LATTE"}, follow_redirects=True)

    assert b"Renamed to" in ok.data
    assert b"You already have a recipe called" in taken.data
    assert b"at most 40 characters" in long.data
    assert b"Renamed to" in case.data
    assert recipe_names(seeded_app, "alice@demo.com") == ["BIG LATTE", "Quick one"]


def test_edit_options_opens_prefilled_screen(seeded_app, alice):
    """
    Purpose: "Edit options" opens the customization screen with the recipe's
        options checked and an "Update recipe" button.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost",
                           RECIPE_CHOICES, shots=2)

    html = alice.get(f"/recipes/{recipe}/edit").get_data(as_text=True)

    for group, name in RECIPE_CHOICES.items():
        oid = option_id(seeded_app, group, name)
        assert re.search(rf'value="{oid}"\s+data-option-id="{oid}"\s+checked', html), name
    assert re.search(r'name="syrup_shots"[^>]*value="2"', html)
    assert "Update recipe" in html
    assert f'formaction="/recipes/{recipe}/update"' in html


def test_update_recipe_options(seeded_app, alice):
    """
    Purpose: Saving the edit screen replaces the recipe's options; invalid
        options are refused.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)

    ok = alice.post(f"/recipes/{recipe}/update",
                    data=form_for(seeded_app, {"milk": "Skimmed milk", "strength": "Mild"}))
    bad = alice.post(f"/recipes/{recipe}/update", data={"syrup_shots": 5})

    assert ok.status_code == 302
    assert bad.status_code == 400
    with seeded_app.app_context():
        stored = db.session.get(SavedRecipe, recipe).customizations
        assert stored["milk"] == option_id(seeded_app, "milk", "Skimmed milk")
        assert stored["strength"] == option_id(seeded_app, "strength", "Mild")
        assert stored["syrup"] is None and stored["syrup_shots"] == 0


def test_delete_requires_confirmation(seeded_app, alice):
    """
    Purpose: Delete only happens with confirm=yes (the "Yes, delete" button).

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)
    page = alice.get("/recipes").get_data(as_text=True)

    unconfirmed = alice.post(f"/recipes/{recipe}/delete", follow_redirects=True)
    assert b"Please confirm" in unconfirmed.data
    assert recipe_names(seeded_app, "alice@demo.com") == ["Morning boost"]

    confirmed = alice.post(f"/recipes/{recipe}/delete", data={"confirm": "yes"}, follow_redirects=True)
    assert b"Deleted" in confirmed.data
    assert recipe_names(seeded_app, "alice@demo.com") == []
    assert 'Really delete "Morning boost"?' in page


# ---------------------------------------------------------------------------
# One-touch ordering and disabled recipes (FR-15, NFR-24)
# ---------------------------------------------------------------------------


def test_one_touch_order_goes_to_dispense_step(seeded_app, alice):
    """
    Purpose: One tap on "Order" creates the order with the saved options and
        opens the confirm/dispense step, at the price shown in the list.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost",
                           RECIPE_CHOICES, shots=2)

    response = alice.post(f"/recipes/{recipe}/order")
    order_page = alice.get(response.headers["Location"]).get_data(as_text=True)

    assert response.status_code == 302
    assert "/order/" in response.headers["Location"]
    assert "Confirm &amp; dispense" in order_page
    assert "€5.50" in order_page
    with seeded_app.app_context():
        order = Transaction.query.filter_by(status=Transaction.STATUS_PENDING).one()
        assert order.customizations["summary"] == "Oat milk · Extra shot · Hot · 2× Vanilla"
        assert order.amount_cents == 550

    dispensed = alice.post(response.headers["Location"] + "/dispense", follow_redirects=True)
    assert b"Enjoy your Latte!" in dispensed.data


def test_recipe_with_out_of_stock_option_is_disabled(seeded_app, alice):
    """
    Purpose: When the recipe's oat milk runs low, the recipe is shown
        disabled with the reason and cannot be ordered.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)
    set_stock(seeded_app, "Oat milk", 100)

    page = alice.get("/recipes").get_data(as_text=True)
    refused = alice.post(f"/recipes/{recipe}/order", follow_redirects=True)

    assert "recipe-card panel recipe-disabled" in page
    assert '<p class="unavailable-label">Oat milk is out of stock.</p>' in page
    assert re.search(r'class="btn btn-primary recipe-order"\s+disabled', page)
    assert b"Oat milk is out of stock." in refused.data
    with seeded_app.app_context():
        assert Transaction.query.filter_by(status=Transaction.STATUS_PENDING).count() == 0


def test_recipe_with_unavailable_drink_is_disabled(seeded_app, alice):
    """
    Purpose: A recipe whose drink is blocked by a low base ingredient is
        disabled with that reason.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    create_recipe(seeded_app, "alice@demo.com", "Mocha", "Choc fix", {"milk": "Whole milk"})
    set_stock(seeded_app, "Chocolate powder", 10)

    page = alice.get("/recipes").get_data(as_text=True)

    assert "Mocha is not available right now (low on: Chocolate powder)." in page


def test_available_recipe_is_enabled(seeded_app, alice):
    """
    Purpose: A recipe that can be made has an enabled Order button.

    Args:
        seeded_app (Flask): Seeded test application.
        alice (FlaskClient): Client logged in as Alice.

    Returns:
        None
    """
    create_recipe(seeded_app, "alice@demo.com", "Espresso", "Quick one", {})

    page = alice.get("/recipes").get_data(as_text=True)

    assert "recipe-disabled" not in page
    assert not re.search(r'recipe-order"\s+disabled', page)


# ---------------------------------------------------------------------------
# Ownership and shortcuts
# ---------------------------------------------------------------------------


def test_other_users_recipes_are_hidden(seeded_app, bob):
    """
    Purpose: Bob gets 404 for every action on Alice's recipe and does not
        see it in his list.

    Args:
        seeded_app (Flask): Seeded test application.
        bob (FlaskClient): Client logged in as Bob.

    Returns:
        None
    """
    recipe = create_recipe(seeded_app, "alice@demo.com", "Latte", "Morning boost", RECIPE_CHOICES)

    assert bob.get(f"/recipes/{recipe}/edit").status_code == 404
    for action, data in (("update", {}), ("rename", {"name": "Mine now"}),
                         ("delete", {"confirm": "yes"}), ("order", {})):
        assert bob.post(f"/recipes/{recipe}/{action}", data=data).status_code == 404, action
    assert b"Morning boost" not in bob.get("/recipes").data
    assert recipe_names(seeded_app, "alice@demo.com") == ["Morning boost"]


def test_my_recipes_shortcut_on_home_and_menu(seeded_client, login):
    """
    Purpose: Logged-in users see "My recipes" on the home and menu screens;
        guests do not.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    guest_home = seeded_client.get("/").data
    guest_menu = seeded_client.get("/menu").data
    login()

    assert b"My recipes" not in guest_home and b"My recipes" not in guest_menu
    assert b'href="/recipes"' in seeded_client.get("/").data
    assert b'href="/recipes"' in seeded_client.get("/menu").data
