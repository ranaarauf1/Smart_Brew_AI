"""Tests for feature 4: inventory-aware ordering, stock re-check before
dispensing, deduction, low-threshold events and the Simulation panel stock
controls (FR-14, FR-26–28, FR-66, FR-67; history FR-05, FR-06)."""

import re

import pytest

from app import db
from app.models import CustomizationOption, Drink, ErrorEvent, Ingredient, Transaction
from app.services import inventory
from tests.conftest import ALICE_EMAIL, ALICE_PASSWORD


def option_id(app, group, name):
    """
    Purpose: Looks up a customization option's ID.

    Args:
        app (Flask): The test application.
        group (str): Option group, e.g. "milk".
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


def ingredient_id(app, name):
    """
    Purpose: Looks up an ingredient's ID by name.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.

    Returns:
        int: The ingredient ID.
    """
    with app.app_context():
        return Ingredient.query.filter_by(name=name).one().id


def set_stock(app, name, level, minimum=None):
    """
    Purpose: Sets an ingredient's stock (and optionally its minimum) directly.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.
        level (float): New stock level.
        minimum (float | None): New minimum threshold, or None to keep it.

    Returns:
        None
    """
    with app.app_context():
        ingredient = Ingredient.query.filter_by(name=name).one()
        ingredient.stock_level = level
        if minimum is not None:
            ingredient.min_threshold = minimum
        db.session.commit()


def stock(app, name):
    """
    Purpose: Reads an ingredient's current stock level.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.

    Returns:
        float: The stock level.
    """
    with app.app_context():
        return Ingredient.query.filter_by(name=name).one().stock_level


def stock_levels(app):
    """
    Purpose: Reads every ingredient's stock level.

    Args:
        app (Flask): The test application.

    Returns:
        dict[str, float]: Stock per ingredient name.
    """
    with app.app_context():
        return {i.name: i.stock_level for i in Ingredient.query.all()}


def low_stock_events(app, name):
    """
    Purpose: Lists the low-threshold error events for one ingredient.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.

    Returns:
        list[ErrorEvent]: The events, oldest first.
    """
    with app.app_context():
        return (
            ErrorEvent.query.filter_by(
                code=ErrorEvent.CODE_LOW_INGREDIENT, ingredient_id=ingredient_id(app, name)
            )
            .order_by(ErrorEvent.id)
            .all()
        )


def place_order(client, app, drink, **choices):
    """
    Purpose: Submits the customization form for a drink using option names.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        drink (str): Drink name.
        **choices: Group -> option name, or syrup_shots -> int.

    Returns:
        TestResponse: Response of the order POST.
    """
    form = {}
    for group, value in choices.items():
        form[group] = value if group == "syrup_shots" else option_id(app, group, value)
    return client.post(f"/drink/{drink_id(app, drink)}/order", data=form)


def order_id_from(response):
    """
    Purpose: Extracts the order ID from the redirect after placing an order.

    Args:
        response (TestResponse): Redirect response to /order/<id>.

    Returns:
        int: The order ID.
    """
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def get_transaction(app, order_id):
    """
    Purpose: Loads a transaction and its details for assertions.

    Args:
        app (Flask): The test application.
        order_id (int): Transaction ID.

    Returns:
        Transaction: The transaction (detached from the session).
    """
    with app.app_context():
        order = db.session.get(Transaction, order_id)
        db.session.expunge(order)
        return order


# ---------------------------------------------------------------------------
# Menu availability (FR-14, FR-26–28)
# ---------------------------------------------------------------------------


def test_menu_greys_out_drinks_with_low_base_ingredient(seeded_app, seeded_client):
    """
    Purpose: With chocolate powder below its minimum, Hot Chocolate shows
        "Not available" and has no link; other drinks stay tappable.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Chocolate powder", 50)
    with seeded_app.app_context():
        category = Drink.query.filter_by(name="Hot Chocolate").one().category_id

    html = seeded_client.get(f"/menu?category={category}").get_data(as_text=True)
    espresso_tab = seeded_client.get("/menu").get_data(as_text=True)

    assert "Not available" in html
    assert f'href="/drink/{drink_id(seeded_app, "Hot Chocolate")}"' not in html
    assert f'href="/drink/{drink_id(seeded_app, "Chai Latte")}"' in html
    assert "Not available" not in espresso_tab  # drinks without chocolate unaffected


def test_unavailable_drink_cannot_be_opened_for_ordering(seeded_app, seeded_client):
    """
    Purpose: Detail page shows "Not available"; the customization screen and
        the order POST redirect to the menu with the reason.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Chocolate powder", 50)
    hot_chocolate = drink_id(seeded_app, "Hot Chocolate")

    detail = seeded_client.get(f"/drink/{hot_chocolate}").get_data(as_text=True)
    customize = seeded_client.get(f"/drink/{hot_chocolate}/customize", follow_redirects=True)
    order = seeded_client.post(f"/drink/{hot_chocolate}/order", data={})
    preview = seeded_client.post(f"/api/drinks/{hot_chocolate}/preview", json={})

    assert f'href="/drink/{hot_chocolate}/customize"' not in detail
    assert ">Not available</button>" in detail
    assert customize.request.path == "/menu"
    assert b"Hot Chocolate is not available right now (low on: Chocolate powder)." in customize.data
    assert order.status_code == 302 and "/menu" in order.headers["Location"]
    assert preview.status_code == 400
    with seeded_app.app_context():
        assert Transaction.query.filter_by(status=Transaction.STATUS_PENDING).count() == 0


def test_stock_exactly_at_threshold_is_available(seeded_app):
    """
    Purpose: Only stock strictly below the minimum blocks a drink.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    set_stock(seeded_app, "Chocolate powder", 100)  # minimum is 100

    with seeded_app.app_context():
        assert inventory.is_drink_available(Drink.query.filter_by(name="Hot Chocolate").one())


def test_milk_drink_blocked_only_when_every_milk_is_low(seeded_app):
    """
    Purpose: Milk is interchangeable: a Latte stays available while any milk
        is in stock and is blocked only when all are low.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    for milk in ("Whole milk", "Skimmed milk", "Almond milk"):
        set_stock(seeded_app, milk, 0)
    with seeded_app.app_context():
        latte = Drink.query.filter_by(name="Latte").one()
        assert inventory.is_drink_available(latte)  # oat milk still in stock

    set_stock(seeded_app, "Oat milk", 0)
    with seeded_app.app_context():
        latte = Drink.query.filter_by(name="Latte").one()
        assert not inventory.is_drink_available(latte)
        assert inventory.get_blocking_ingredients(latte) == ["Milk"]
        assert inventory.is_drink_available(Drink.query.filter_by(name="Espresso").one())


# ---------------------------------------------------------------------------
# Stock check at confirmation (FR-14)
# ---------------------------------------------------------------------------


def test_extra_shot_needs_more_beans_than_left(seeded_app, seeded_client):
    """
    Purpose: With 20 g of beans left (minimum lowered to 0), a regular Doppio
        (18 g) passes but an extra shot (27 g) is refused with a clear message.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Coffee beans", 20, minimum=0)

    preview = seeded_client.post(
        f"/api/drinks/{drink_id(seeded_app, 'Doppio')}/preview",
        json={"strength": option_id(seeded_app, "strength", "Extra shot")},
    ).get_json()
    refused = place_order(seeded_client, seeded_app, "Doppio", strength="Extra shot")
    accepted = place_order(seeded_client, seeded_app, "Doppio", strength="Regular")

    message = "Not enough Coffee beans (needs 27 g, 20 g left) for this order."
    assert preview["can_order"] is False
    assert preview["stock_problems"] == [message]
    assert refused.status_code == 400
    assert message.encode() in refused.data
    assert accepted.status_code == 302
    assert stock(seeded_app, "Coffee beans") == 20  # nothing deducted before dispensing


def test_syrup_shots_need_enough_syrup(seeded_app, seeded_client):
    """
    Purpose: 3 vanilla shots (30 ml) are refused with 25 ml left; 2 shots pass.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Vanilla syrup", 25, minimum=0)

    three = place_order(seeded_client, seeded_app, "Latte", syrup="Vanilla", syrup_shots=3)
    two = place_order(seeded_client, seeded_app, "Latte", syrup="Vanilla", syrup_shots=2)

    assert three.status_code == 400
    assert b"Not enough Vanilla syrup (needs 30 ml, 25 ml left)" in three.data
    assert two.status_code == 302


def test_refused_order_keeps_customer_choices(seeded_app, seeded_client):
    """
    Purpose: After a refusal the screen still shows the customer's choices.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Coffee beans", 20, minimum=0)
    extra = option_id(seeded_app, "strength", "Extra shot")

    html = place_order(seeded_client, seeded_app, "Doppio", strength="Extra shot").get_data(as_text=True)

    assert re.search(rf'value="{extra}"\s+data-option-id="{extra}"\s+checked', html)


def test_pending_order_records_details_without_deducting(seeded_app, seeded_client):
    """
    Purpose: Confirming creates a pending transaction with all details but
        deducts nothing yet.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    before = stock_levels(seeded_app)

    order_id = order_id_from(place_order(seeded_client, seeded_app, "Latte", milk="Oat milk"))
    order = get_transaction(seeded_app, order_id)

    assert order.status == Transaction.STATUS_PENDING
    assert order.amount_cents == 390
    assert order.idempotency_key
    assert order.customizations["options"]["milk"] == "Oat milk"
    assert stock_levels(seeded_app) == before
    assert b"Confirm &amp; dispense" in seeded_client.get(f"/order/{order_id}").data


# ---------------------------------------------------------------------------
# Dispensing: re-check, deduct, record (FR-66, FR-27, FR-05, FR-06)
# ---------------------------------------------------------------------------


def test_dispense_deducts_exact_customized_quantities(seeded_app, seeded_client):
    """
    Purpose: A Latte with oat milk, extra shot and 2 vanilla shots deducts
        27 g beans, 60 ml water, 220 ml oat milk, 20 ml vanilla and 1 cup,
        and no whole milk.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    before = stock_levels(seeded_app)
    order_id = order_id_from(place_order(
        seeded_client, seeded_app, "Latte",
        milk="Oat milk", strength="Extra shot", syrup="Vanilla", syrup_shots=2,
    ))

    response = seeded_client.post(f"/order/{order_id}/dispense", follow_redirects=True)

    after = stock_levels(seeded_app)
    expected = {"Coffee beans": 27, "Water": 60, "Oat milk": 220, "Vanilla syrup": 20, "Cups": 1}
    for name, level in before.items():
        assert after[name] == level - expected.get(name, 0), name
    assert b"Enjoy your Latte!" in response.data
    order = get_transaction(seeded_app, order_id)
    assert order.status == Transaction.STATUS_COMPLETED
    assert order.completed_at is not None


def test_transaction_records_all_customization_details(seeded_app, seeded_client):
    """
    Purpose: The completed transaction stores every option, the ingredient
        quantities and the nutrition (FR-05, FR-06).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(
        seeded_client, seeded_app, "Cappuccino",
        milk="Skimmed milk", strength="Strong", temperature="Extra hot",
        syrup="Caramel", syrup_shots=1,
    ))
    seeded_client.post(f"/order/{order_id}/dispense")

    details = get_transaction(seeded_app, order_id).customizations

    assert details["options"] == {
        "milk": "Skimmed milk", "strength": "Strong", "temperature": "Extra hot",
        "syrup": "Caramel", "syrup_shots": 1,
    }
    assert details["summary"] == "Skimmed milk · Strong · Extra hot · 1× Caramel"
    assert {"id": ingredient_id(seeded_app, "Coffee beans"), "name": "Coffee beans",
            "quantity": 22, "unit": "g"} in details["ingredients"]
    assert details["selection"]["syrup_shots"] == 1
    assert set(details["nutrition"]) == {"calories", "sugar_g", "fat_g", "protein_g", "caffeine_mg"}


def test_completed_order_appears_in_profile_history(seeded_client, login):
    """
    Purpose: A logged-in user's completed order shows on their profile with
        its options, price and status.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    app = seeded_client.application
    login()
    order_id = order_id_from(place_order(seeded_client, app, "Latte", milk="Oat milk",
                                         strength="Extra shot"))
    seeded_client.post(f"/order/{order_id}/dispense")

    html = seeded_client.get("/profile").get_data(as_text=True)

    assert "No orders yet." not in html
    assert "Latte" in html
    assert "Oat milk · Extra shot · Hot" in html
    assert "€4.50" in html
    assert "Completed" in html


def test_stock_drop_between_confirm_and_dispense_cancels_order(seeded_app, seeded_client):
    """
    Purpose: If beans drop after the order is confirmed, the re-check before
        dispensing cancels it (FR-66) and nothing is deducted.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Doppio",
                                         strength="Extra shot"))
    set_stock(seeded_app, "Coffee beans", 20, minimum=0)  # e.g. another order used them
    before = stock_levels(seeded_app)

    response = seeded_client.post(f"/order/{order_id}/dispense", follow_redirects=True)

    order = get_transaction(seeded_app, order_id)
    assert order.status == Transaction.STATUS_CANCELLED
    assert "Not enough Coffee beans (needs 27 g, 20 g left)" in order.failure_reason
    assert stock_levels(seeded_app) == before
    assert b"Sorry, we couldn't make your drink" in response.data
    assert b"Not enough Coffee beans" in response.data


def test_option_running_out_before_dispense_cancels_order(seeded_app, seeded_client):
    """
    Purpose: If the chosen milk drops below its minimum after confirmation,
        the order is cancelled even though the drink is still available.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Latte", milk="Oat milk"))
    set_stock(seeded_app, "Oat milk", 400)  # minimum 500

    seeded_client.post(f"/order/{order_id}/dispense")

    order = get_transaction(seeded_app, order_id)
    assert order.status == Transaction.STATUS_CANCELLED
    assert "Oat milk is out of stock." in order.failure_reason
    assert stock(seeded_app, "Oat milk") == 400


def test_drink_becoming_unavailable_before_dispense_cancels_order(seeded_app, seeded_client):
    """
    Purpose: If a base ingredient drops below its minimum after confirmation,
        the order is cancelled with the reason.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Espresso"))
    set_stock(seeded_app, "Cups", 5)  # minimum 20

    seeded_client.post(f"/order/{order_id}/dispense")

    order = get_transaction(seeded_app, order_id)
    assert order.status == Transaction.STATUS_CANCELLED
    assert "low on: Cups" in order.failure_reason


def test_double_dispense_deducts_only_once(seeded_app, seeded_client):
    """
    Purpose: Pressing "Confirm & dispense" twice dispenses one drink.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Espresso"))

    seeded_client.post(f"/order/{order_id}/dispense")
    second = seeded_client.post(f"/order/{order_id}/dispense", follow_redirects=True)

    assert stock(seeded_app, "Coffee beans") == 1800 - 9
    assert stock(seeded_app, "Cups") == 180 - 1
    assert b"Enjoy your Espresso!" in second.data


def test_cancelled_order_cannot_be_dispensed_later(seeded_app, seeded_client):
    """
    Purpose: After a cancellation, refilling and pressing dispense again
        does nothing; a new order is needed.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Espresso"))
    set_stock(seeded_app, "Cups", 5)
    seeded_client.post(f"/order/{order_id}/dispense")
    set_stock(seeded_app, "Cups", 200)

    seeded_client.post(f"/order/{order_id}/dispense")

    assert get_transaction(seeded_app, order_id).status == Transaction.STATUS_CANCELLED
    assert stock(seeded_app, "Cups") == 200


def test_guest_can_order_and_dispense(seeded_app, seeded_client):
    """
    Purpose: Guests can order; the transaction has no user.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Green Tea"))
    seeded_client.post(f"/order/{order_id}/dispense")

    order = get_transaction(seeded_app, order_id)
    assert order.user_id is None
    assert order.status == Transaction.STATUS_COMPLETED


def test_other_people_cannot_view_or_dispense_an_order(seeded_app, seeded_client):
    """
    Purpose: Another browser (guest or logged-in) gets 404 for someone
        else's order and cannot dispense it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/login", data={"email": ALICE_EMAIL, "password": ALICE_PASSWORD})
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Espresso"))

    stranger = seeded_app.test_client()
    bob = seeded_app.test_client()
    bob.post("/login", data={"email": "bob@demo.com", "password": "Bob2026!"})

    for other in (stranger, bob):
        assert other.get(f"/order/{order_id}").status_code == 404
        assert other.post(f"/order/{order_id}/dispense").status_code == 404
    assert get_transaction(seeded_app, order_id).status == Transaction.STATUS_PENDING


def test_unknown_order_returns_404(seeded_client):
    """
    Purpose: A non-existent order ID shows the 404 page.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    assert seeded_client.get("/order/9999").status_code == 404
    assert seeded_client.post("/order/9999/dispense").status_code == 404


def test_deduct_stock_never_goes_negative(seeded_app):
    """
    Purpose: The deduction itself refuses and rolls back if stock no longer
        covers a quantity, even without the earlier checks.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    set_stock(seeded_app, "Cups", 0, minimum=0)

    with seeded_app.app_context():
        beans = Ingredient.query.filter_by(name="Coffee beans").one()
        cups = Ingredient.query.filter_by(name="Cups").one()
        with pytest.raises(inventory.InsufficientStockError):
            inventory.deduct_stock({beans: 9, cups: 1})

    assert stock(seeded_app, "Coffee beans") == 1800  # rolled back
    assert stock(seeded_app, "Cups") == 0


# ---------------------------------------------------------------------------
# Low Ingredient Threshold events (FR-67)
# ---------------------------------------------------------------------------


def test_dispense_crossing_threshold_creates_one_event(seeded_app, seeded_client):
    """
    Purpose: Oat milk at 600 ml (minimum 500) drops to 380 ml after a Latte:
        exactly one "Low Ingredient Threshold" event is created.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_stock(seeded_app, "Oat milk", 600)
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Latte", milk="Oat milk"))

    seeded_client.post(f"/order/{order_id}/dispense")

    events = low_stock_events(seeded_app, "Oat milk")
    assert len(events) == 1
    assert events[0].resolved_at is None
    assert events[0].severity == ErrorEvent.SEVERITY_WARNING
    assert "Low Ingredient Threshold: Oat milk at 380 ml (minimum 500 ml)" in events[0].message
    assert low_stock_events(seeded_app, "Coffee beans") == []


def test_no_event_when_staying_above_threshold(seeded_app, seeded_client):
    """
    Purpose: A normal order that keeps every ingredient above its minimum
        creates no events.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    order_id = order_id_from(place_order(seeded_client, seeded_app, "Latte"))
    seeded_client.post(f"/order/{order_id}/dispense")

    with seeded_app.app_context():
        assert ErrorEvent.query.count() == 0


def test_no_duplicate_event_while_still_low(seeded_app):
    """
    Purpose: Dropping further while an event is open adds no duplicate;
        refilling resolves it, and a new drop creates a new event.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    oat = ingredient_id(seeded_app, "Oat milk")
    with seeded_app.app_context():
        inventory.set_stock_level(oat, 400)
        inventory.set_stock_level(oat, 100)
    assert len(low_stock_events(seeded_app, "Oat milk")) == 1

    with seeded_app.app_context():
        inventory.set_stock_level(oat, 3000)
    assert low_stock_events(seeded_app, "Oat milk")[0].resolved_at is not None

    with seeded_app.app_context():
        inventory.set_stock_level(oat, 50)
    events = low_stock_events(seeded_app, "Oat milk")
    assert len(events) == 2
    assert [e.resolved_at is None for e in events] == [False, True]


# ---------------------------------------------------------------------------
# Simulation panel: ingredient stock
# ---------------------------------------------------------------------------


def test_simulation_panel_lists_ingredient_stock(seeded_client):
    """
    Purpose: The panel shows every ingredient and marks low ones.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    html = seeded_client.get("/simulation/").get_data(as_text=True)

    assert 'id="stock"' in html
    for name in ("Coffee beans", "Whole milk", "Skimmed milk", "Almond milk", "Cups"):
        assert name in html
    assert html.count('class="badge-low"') == 1  # almond milk only


def test_simulation_set_level_blocks_and_unblocks_drink(seeded_app, seeded_client):
    """
    Purpose: Setting chocolate powder low blocks Hot Chocolate; setting it
        back unblocks it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    chocolate = ingredient_id(seeded_app, "Chocolate powder")
    hot_chocolate = drink_id(seeded_app, "Hot Chocolate")

    response = seeded_client.post("/simulation/stock", data={"ingredient_id": chocolate, "level": "40"},
                                  follow_redirects=True)
    blocked = seeded_client.get(f"/drink/{hot_chocolate}/customize")
    seeded_client.post("/simulation/stock", data={"ingredient_id": chocolate, "level": "500"})
    unblocked = seeded_client.get(f"/drink/{hot_chocolate}/customize")

    assert b"Chocolate powder set to 40 g (below minimum)." in response.data
    assert blocked.status_code == 302
    assert unblocked.status_code == 200
    assert stock(seeded_app, "Chocolate powder") == 500


def test_simulation_empty_and_full_presets(seeded_app, seeded_client):
    """
    Purpose: "Empty" sets the level to 0 and "Full" to the capacity.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    beans = ingredient_id(seeded_app, "Coffee beans")

    seeded_client.post("/simulation/stock", data={"ingredient_id": beans, "level": "", "preset": "empty"})
    assert stock(seeded_app, "Coffee beans") == 0
    assert len(low_stock_events(seeded_app, "Coffee beans")) == 1

    seeded_client.post("/simulation/stock", data={"ingredient_id": beans, "level": "", "preset": "full"})
    assert stock(seeded_app, "Coffee beans") == 2000
    assert low_stock_events(seeded_app, "Coffee beans")[0].resolved_at is not None


@pytest.mark.parametrize("level, message", [
    ("abc", b"Please enter a number."),
    ("", b"Please enter a number."),
    ("-5", b"Coffee beans must be between 0 and 2000 g."),
    ("2500", b"Coffee beans must be between 0 and 2000 g."),
    ("nan", b"Coffee beans must be between 0 and 2000 g."),
])
def test_simulation_rejects_invalid_level(seeded_app, seeded_client, level, message):
    """
    Purpose: Non-numeric, negative, too-large or NaN levels are refused and
        the stock stays unchanged.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        level (str): Value typed into the form.
        message (bytes): Expected error message.

    Returns:
        None
    """
    response = seeded_client.post(
        "/simulation/stock",
        data={"ingredient_id": ingredient_id(seeded_app, "Coffee beans"), "level": level},
        follow_redirects=True,
    )

    assert message in response.data
    assert stock(seeded_app, "Coffee beans") == 1800


def test_simulation_rejects_unknown_ingredient(seeded_client):
    """
    Purpose: An unknown or missing ingredient ID shows an error.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    unknown = seeded_client.post("/simulation/stock", data={"ingredient_id": 9999, "level": "5"},
                                 follow_redirects=True)
    missing = seeded_client.post("/simulation/stock", data={"level": "5"}, follow_redirects=True)

    assert b"Unknown ingredient." in unknown.data
    assert b"Unknown ingredient." in missing.data
