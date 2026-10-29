"""Tests for feature 3: live drink customization with price and nutrition
updates, and depleted options blocked (FR-13, FR-65)."""

import re

import pytest

from app import db
from app.models import CustomizationOption, Drink, Ingredient, Transaction
from app.services import customization


def option_id(app, group, name):
    """
    Purpose: Looks up a customization option's ID.

    Args:
        app (Flask): The test application.
        group (str): Option group, e.g. "milk".
        name (str): Option name, e.g. "Oat milk".

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


def set_stock(app, name, level):
    """
    Purpose: Sets an ingredient's stock level directly in the database.

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


def preview(client, app, drink, **choices):
    """
    Purpose: Calls the preview endpoint for a drink with option names.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        drink (str): Drink name.
        **choices: Group name -> option name (e.g. milk="Oat milk"), or
            syrup_shots -> int.

    Returns:
        TestResponse: The JSON response.
    """
    body = {}
    for group, value in choices.items():
        if group == "syrup_shots":
            body[group] = value
        else:
            body[group] = option_id(app, group, value)
    return client.post(f"/api/drinks/{drink_id(app, drink)}/preview", json=body)


def nutrition_value(data, label):
    """
    Purpose: Reads one formatted nutrition value from a preview response.

    Args:
        data (dict): Preview JSON.
        label (str): Row label, e.g. "Calories".

    Returns:
        str: The formatted value, e.g. "150 kcal".
    """
    return dict(data["nutrition"])[label]


# ---------------------------------------------------------------------------
# Which options each drink offers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("drink, groups", [
    ("Latte", ["milk", "strength", "temperature", "syrup"]),
    ("Espresso", ["strength", "temperature", "syrup"]),
    ("Iced Latte", ["milk", "strength", "syrup"]),
    ("Hot Chocolate", ["milk", "temperature", "syrup"]),
    ("Green Tea", ["temperature", "syrup"]),
])
def test_drink_offers_only_applicable_groups(seeded_app, drink, groups):
    """
    Purpose: Milk only with milk in the recipe, strength only for coffee,
        temperature only for hot drinks, syrup for all.

    Args:
        seeded_app (Flask): Seeded test application.
        drink (str): Drink name.
        groups (list[str]): Expected group keys in order.

    Returns:
        None
    """
    with seeded_app.app_context():
        assert customization.get_applicable_groups(Drink.query.filter_by(name=drink).one()) == groups


def test_customize_page_shows_options_and_defaults(seeded_app, seeded_client):
    """
    Purpose: The screen lists every option of the drink's groups with the
        defaults checked and the server-calculated price shown.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    html = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Latte')}/customize").get_data(as_text=True)

    for name in ("None", "Whole milk", "Skimmed milk", "Oat milk", "Almond milk",
                 "Mild", "Regular", "Strong", "Extra shot",
                 "Warm", "Hot", "Extra hot", "Vanilla", "Caramel"):
        assert f'<span class="option-name">{name}</span>' in html
    whole_id = option_id(seeded_app, "milk", "Whole milk")
    assert re.search(rf'value="{whole_id}"\s+data-option-id="{whole_id}"\s+checked', html)
    assert 'id="preview-price">€3.50<' in html
    assert 'name="syrup_shots"' in html and 'max="3"' in html


def test_detail_page_links_to_customization(seeded_app, seeded_client):
    """
    Purpose: "Customize & order" on the detail page opens the customization screen.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    latte = drink_id(seeded_app, "Latte")

    html = seeded_client.get(f"/drink/{latte}").get_data(as_text=True)

    assert f'href="/drink/{latte}/customize"' in html


# ---------------------------------------------------------------------------
# Live price and nutrition (FR-13)
# ---------------------------------------------------------------------------


def test_preview_default_matches_base_drink(seeded_app, seeded_client):
    """
    Purpose: With default choices the preview equals the menu values.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte").get_json()

    assert data["ok"] is True
    assert data["price_cents"] == 350
    assert data["price"] == "€3.50"
    assert nutrition_value(data, "Calories") == "150 kcal"
    assert data["can_order"] is True


def test_preview_oat_milk_changes_price_and_nutrition(seeded_app, seeded_client):
    """
    Purpose: Oat milk adds €0.40 and swaps 220 ml of whole milk for oat milk
        (calories 150 + (46 − 64) × 2.2 ≈ 110).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", milk="Oat milk").get_json()

    assert data["price"] == "€3.90"
    assert nutrition_value(data, "Calories") == "110 kcal"
    assert nutrition_value(data, "Sugar") == "11.2 g"
    assert "Oat milk" in data["summary"]


def test_preview_no_milk_removes_milk_nutrition(seeded_app, seeded_client):
    """
    Purpose: Milk "None" removes the milk's calories (150 − 64 × 2.2 ≈ 9).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", milk="None").get_json()

    assert data["price"] == "€3.50"
    assert nutrition_value(data, "Calories") == "9 kcal"
    assert "No milk" in data["summary"]


def test_preview_extra_shot_adds_caffeine_and_price(seeded_app, seeded_client):
    """
    Purpose: An extra shot (+9 g beans) adds €0.60 and 63 mg caffeine.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", strength="Extra shot").get_json()

    assert data["price"] == "€4.10"
    assert nutrition_value(data, "Caffeine") == "189 mg"


def test_preview_mild_reduces_caffeine(seeded_app, seeded_client):
    """
    Purpose: Mild (−4 g beans) lowers caffeine by 28 mg at no extra cost.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Espresso", strength="Mild").get_json()

    assert data["price"] == "€2.20"
    assert nutrition_value(data, "Caffeine") == "35 mg"


def test_preview_three_syrup_shots(seeded_app, seeded_client):
    """
    Purpose: Three caramel shots add 3 × €0.50 and 3 × 8 g sugar.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", syrup="Caramel", syrup_shots=3).get_json()

    assert data["price"] == "€5.00"
    assert nutrition_value(data, "Sugar") == "37 g"
    assert "3× Caramel" in data["summary"]


def test_preview_combined_options(seeded_app, seeded_client):
    """
    Purpose: Several options add up: oat (+0.40), extra shot (+0.60),
        2 vanilla shots (+1.00).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", milk="Oat milk", strength="Extra shot",
                   temperature="Extra hot", syrup="Vanilla", syrup_shots=2).get_json()

    assert data["price"] == "€5.50"
    assert data["summary"] == "Oat milk · Extra shot · Extra hot · 2× Vanilla"


def test_zero_shots_ignores_syrup_type(seeded_app, seeded_client):
    """
    Purpose: With 0 shots the chosen syrup type has no effect.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", syrup="Vanilla", syrup_shots=0).get_json()

    assert data["ok"] is True
    assert data["price"] == "€3.50"
    assert "Vanilla" not in data["summary"]


def test_order_price_matches_preview(seeded_app, seeded_client):
    """
    Purpose: The confirmed order costs exactly what the preview showed.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    choices = {"milk": "Oat milk", "strength": "Strong", "syrup": "Vanilla"}
    shown = preview(seeded_client, seeded_app, "Latte", syrup_shots=1, **choices).get_json()
    form = {group: option_id(seeded_app, group, name) for group, name in choices.items()}
    form["syrup_shots"] = 1

    seeded_client.post(f"/drink/{drink_id(seeded_app, 'Latte')}/order", data=form)

    with seeded_app.app_context():
        new_order = Transaction.query.filter_by(status=Transaction.STATUS_PENDING).one()
        assert new_order.amount_cents == shown["price_cents"] == 470


# ---------------------------------------------------------------------------
# Depleted options (FR-65)
# ---------------------------------------------------------------------------


def test_out_of_stock_option_is_disabled_and_labelled(seeded_app, seeded_client):
    """
    Purpose: Almond milk (seeded below its threshold) is disabled with an
        "Out of stock" label; the other milks are not.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    html = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Latte')}/customize").get_data(as_text=True)
    almond = option_id(seeded_app, "milk", "Almond milk")
    oat = option_id(seeded_app, "milk", "Oat milk")

    almond_tile = re.search(rf'<label class="option option-disabled">\s*<input[^>]*value="{almond}"[^>]*disabled>.*?</label>', html, re.S)
    assert almond_tile is not None
    assert '<span class="out-of-stock-label">Out of stock</span>' in almond_tile.group(0)
    oat_tile = re.search(rf'<label class="option">\s*<input[^>]*value="{oat}".*?</label>', html, re.S)
    assert "disabled" not in oat_tile.group(0)
    assert "hidden>Out of stock" in oat_tile.group(0)


def test_out_of_stock_option_rejected_by_preview_and_order(seeded_app, seeded_client):
    """
    Purpose: Choosing almond milk anyway is refused by the preview and by
        the order, while the drink stays orderable with another milk.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    latte = drink_id(seeded_app, "Latte")
    almond = option_id(seeded_app, "milk", "Almond milk")

    response = preview(seeded_client, seeded_app, "Latte", milk="Almond milk")
    order = seeded_client.post(f"/drink/{latte}/order", data={"milk": almond})
    with_oat = seeded_client.post(
        f"/drink/{latte}/order", data={"milk": option_id(seeded_app, "milk", "Oat milk")}
    )

    assert response.status_code == 400
    assert "Almond milk is out of stock." in response.get_json()["errors"]
    assert almond in response.get_json()["out_of_stock_option_ids"]
    assert order.status_code == 400
    assert b"Almond milk is out of stock." in order.data
    assert with_oat.status_code == 302


def test_preview_reports_options_that_ran_out(seeded_app, seeded_client):
    """
    Purpose: If oat milk runs low while the screen is open, the next
        preview tells the screen to disable it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Oat milk", 100)

    data = preview(seeded_client, seeded_app, "Latte").get_json()

    assert option_id(seeded_app, "milk", "Oat milk") in data["out_of_stock_option_ids"]
    assert data["ok"] is True  # the default whole milk is still fine


def test_default_milk_out_of_stock_preselects_next_milk(seeded_app, seeded_client):
    """
    Purpose: With whole milk low, the drink stays available and the next
        milk in stock (skimmed) is preselected.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Whole milk", 100)
    skimmed = option_id(seeded_app, "milk", "Skimmed milk")

    response = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Latte')}/customize")

    assert response.status_code == 200
    assert re.search(rf'value="{skimmed}"\s+data-option-id="{skimmed}"\s+checked', response.get_data(as_text=True))


def test_out_of_stock_syrup_cannot_be_chosen(seeded_app, seeded_client):
    """
    Purpose: A syrup below its threshold is refused when shots are added.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Vanilla syrup", 10)

    response = preview(seeded_client, seeded_app, "Latte", syrup="Vanilla", syrup_shots=1)

    assert response.status_code == 400
    assert "Vanilla is out of stock." in response.get_json()["errors"]


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body, message", [
    ({"syrup_shots": 4}, "Syrup shots must be between 0 and 3."),
    ({"syrup_shots": -1}, "Syrup shots must be between 0 and 3."),
    ({"syrup_shots": "lots"}, "Syrup shots must be between 0 and 3."),
    ({"syrup_shots": 2}, "Please choose a syrup for the syrup shots."),
    ({"milk": 9999}, "Please choose a valid milk option."),
    ({"strength": "abc"}, "Please choose a valid strength option."),
    ({"temperature": True}, "Please choose a valid temperature option."),
])
def test_preview_rejects_invalid_values(seeded_app, seeded_client, body, message):
    """
    Purpose: Out-of-range shots, shots without syrup and unknown or
        malformed option IDs are refused with a clear message.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        body (dict): JSON body to send.
        message (str): Expected error message.

    Returns:
        None
    """
    response = seeded_client.post(f"/api/drinks/{drink_id(seeded_app, 'Latte')}/preview", json=body)

    assert response.status_code == 400
    assert message in response.get_json()["errors"]


def test_preview_rejects_option_from_wrong_group(seeded_app, seeded_client):
    """
    Purpose: A milk option ID sent as the strength is refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    body = {"strength": option_id(seeded_app, "milk", "Oat milk")}

    response = seeded_client.post(f"/api/drinks/{drink_id(seeded_app, 'Latte')}/preview", json=body)

    assert response.status_code == 400
    assert "Please choose a valid strength option." in response.get_json()["errors"]


def test_preview_rejects_group_the_drink_does_not_offer(seeded_app, seeded_client):
    """
    Purpose: Milk for an Espresso, or a temperature for an iced drink, is refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    milk = preview(seeded_client, seeded_app, "Espresso", milk="Oat milk")
    temperature = preview(seeded_client, seeded_app, "Iced Latte", temperature="Hot")

    assert milk.status_code == 400
    assert "Espresso has no milk option." in milk.get_json()["errors"]
    assert "Iced Latte has no temperature option." in temperature.get_json()["errors"]


@pytest.mark.parametrize("kwargs", [
    {"data": "not json", "content_type": "text/plain"},
    {"data": "{broken", "content_type": "application/json"},
    {"json": [1, 2, 3]},
])
def test_preview_rejects_non_object_body(seeded_app, seeded_client, kwargs):
    """
    Purpose: A body that is not a JSON object gets 400, not a crash.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        kwargs (dict): Request body arguments for the test client.

    Returns:
        None
    """
    response = seeded_client.post(f"/api/drinks/{drink_id(seeded_app, 'Latte')}/preview", **kwargs)

    assert response.status_code == 400
    assert response.get_json() == {"ok": False, "errors": ["Invalid request."]}


def test_preview_unknown_drink_returns_404(seeded_client):
    """
    Purpose: Previewing a drink that does not exist returns 404 JSON.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    response = seeded_client.post("/api/drinks/9999/preview", json={})

    assert response.status_code == 404
    assert response.get_json()["ok"] is False


def test_preview_only_with_post(seeded_app, seeded_client):
    """
    Purpose: The preview endpoint does not accept GET.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    assert seeded_client.get(f"/api/drinks/{drink_id(seeded_app, 'Latte')}/preview").status_code == 405


# ---------------------------------------------------------------------------
# Cup picture data (presentation of FR-13)
# ---------------------------------------------------------------------------


def test_preview_describes_cup_layers(seeded_app, seeded_client):
    """
    Purpose: The preview includes the cup picture: a Latte with oat milk,
        an extra shot and 2 vanilla shots has 20 ml syrup, 60 ml espresso,
        and 220 ml oat milk split into 187 ml milk and 33 ml foam (15 %).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Latte", milk="Oat milk", strength="Extra shot",
                   syrup="Vanilla", syrup_shots=2).get_json()

    cup_data = data["cup"]
    assert cup_data["vessel"] == "cup" and cup_data["capacity_ml"] == 400
    assert [(l["kind"], l["label"], l["ml"]) for l in cup_data["layers"]] == [
        ("syrup", "Vanilla syrup", 20), ("espresso", "Espresso", 60),
        ("milk", "Oat milk", 187), ("foam", "Foam", 33),
    ]
    assert cup_data["steam"] == 2 and cup_data["ice"] is False
    assert cup_data["description"] == "Cup: 20 ml vanilla syrup, 60 ml espresso, 187 ml oat milk, 33 ml foam"


def test_iced_drink_is_a_glass_with_ice_and_no_steam(seeded_app, seeded_client):
    """
    Purpose: An iced drink is drawn as a glass with ice, without foam or steam;
        the customize page draws it on first load (works without JavaScript).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    data = preview(seeded_client, seeded_app, "Iced Latte").get_json()
    page = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Iced Latte')}/customize").get_data(as_text=True)

    assert data["cup"]["vessel"] == "glass" and data["cup"]["ice"] is True
    assert data["cup"]["steam"] == 0
    assert [l["kind"] for l in data["cup"]["layers"]] == ["espresso", "milk"]
    assert 'id="cup-picture" class="cup-picture is-glass"' in page
    assert 'aria-label="Glass: 60 ml espresso, 180 ml whole milk, with ice"' in page
