"""Tests for feature 6: personalized recommendations from time of day,
weather and order history, the preference profile and the Simulation panel
weather/time controls (FR-16–18, FR-22–25, FR-56).

All tests use fixed times and weather so results are predictable. Expected
scores follow the rules in app/services/recommendations.py.
"""

import re
import uuid
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import CustomizationOption, Drink, Ingredient, SystemState, Transaction, User, utcnow
from app.services import clock, customization, orders, preferences, recommendations, weather
from tests.conftest import pay

MORNING = datetime(2026, 10, 29, 8, 0)
AFTERNOON = datetime(2026, 10, 29, 14, 0)
EVENING = datetime(2026, 10, 29, 20, 0)
NIGHT = datetime(2026, 10, 29, 23, 0)
COLD = {"condition": "cloudy", "temperature_c": 4}
HOT = {"condition": "sunny", "temperature_c": 28}


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


def get_user(email):
    """
    Purpose: Loads a user by email (inside an app context).

    Args:
        email (str): The user's email.

    Returns:
        User: The user.
    """
    return User.query.filter_by(email=email).one()


def ranked(user, now, current_weather):
    """
    Purpose: Ranks all active drinks (inside an app context).

    Args:
        user (User | None): The user, or None for a guest.
        now (datetime): Fixed time.
        current_weather (dict | None): Fixed weather, or None.

    Returns:
        list[Recommendation]: Ranked recommendations.
    """
    drinks = Drink.query.filter_by(is_active=True).all()
    return recommendations.rank_drinks(drinks, user, now, current_weather)


def scores(recs):
    """
    Purpose: Maps drink names to scores.

    Args:
        recs (list[Recommendation]): Ranked recommendations.

    Returns:
        dict[str, float]: Score per drink name.
    """
    return {rec.drink.name: rec.score for rec in recs}


def set_conditions(app, time=None, condition="cloudy", temp=4, online=True):
    """
    Purpose: Sets the simulated time and manual weather in SystemState.

    Args:
        app (Flask): The test application.
        time (str | None): "HH:MM" override, or None for real time.
        condition (str): Weather condition.
        temp (float): Temperature in °C.
        online (bool): False simulates the weather API being down.

    Returns:
        None
    """
    with app.app_context():
        state = SystemState.get()
        state.time_override = time
        state.weather_mode = SystemState.WEATHER_MANUAL
        state.weather_condition = condition
        state.weather_temp_c = temp
        state.weather_online = online
        db.session.commit()


def add_completed_order(app, email, drink, choices, days_ago):
    """
    Purpose: Inserts a completed order into a user's history.

    Args:
        app (Flask): The test application.
        email (str): The user's email.
        drink (str): Drink name.
        choices (dict): Group -> option name.
        days_ago (float): How long ago the order was made.

    Returns:
        None
    """
    with app.app_context():
        drink_obj = Drink.query.filter_by(name=drink).one()
        data = {g: option_id(app, g, n) for g, n in choices.items()}
        selection, errors = customization.parse_selection(drink_obj, data)
        assert not errors
        needs = customization.compute_ingredients(drink_obj, selection)
        created = utcnow() - timedelta(days=days_ago)
        db.session.add(Transaction(
            user=get_user(email), drink=drink_obj,
            customizations=orders.build_order_details(drink_obj, selection, needs),
            amount_cents=customization.compute_price(drink_obj, selection),
            status=Transaction.STATUS_COMPLETED, idempotency_key=uuid.uuid4().hex,
            created_at=created, completed_at=created,
        ))
        db.session.commit()


def menu_drink_names(html):
    """
    Purpose: Lists drink names in the order they appear on a menu page.

    Args:
        html (str): Menu page HTML.

    Returns:
        list[str]: Drink names.
    """
    return re.findall(r'class="drink-name">([^<]+)<', html)


def checked_option(html, app, group, name):
    """
    Purpose: Checks whether an option's radio button is checked on the page.

    Args:
        html (str): Customization page HTML.
        app (Flask): The test application.
        group (str): Option group.
        name (str): Option name.

    Returns:
        bool: True if the option is preselected.
    """
    oid = option_id(app, group, name)
    return re.search(rf'value="{oid}"\s+data-option-id="{oid}"\s+checked', html) is not None


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour, minute, period", [
    (5, 59, "night"), (6, 0, "morning"), (10, 59, "morning"),
    (11, 0, "afternoon"), (16, 59, "afternoon"), (17, 0, "evening"),
    (21, 59, "evening"), (22, 0, "night"), (0, 30, "night"),
])
def test_time_periods(hour, minute, period):
    """
    Purpose: Periods are morning 06–11, afternoon 11–17, evening 17–22,
        night 22–06.

    Args:
        hour (int): Hour to test.
        minute (int): Minute to test.
        period (str): Expected period.

    Returns:
        None
    """
    assert recommendations.get_time_period(datetime(2026, 1, 1, hour, minute)) == period


@pytest.mark.parametrize("condition, temp, expected", [
    ("cloudy", 10, "cold"), ("cloudy", 10.5, "mild"), ("rainy", 15, "cold"),
    ("snowy", 22, "cold"), ("sunny", 22.9, "mild"), ("sunny", 23, "hot"), ("rainy", 25, "hot"),
])
def test_weather_classes(condition, temp, expected):
    """
    Purpose: ≤ 10 °C or rain/snow is cold, ≥ 23 °C is hot, otherwise mild.

    Args:
        condition (str): Weather condition.
        temp (float): Temperature in °C.
        expected (str): Expected class.

    Returns:
        None
    """
    assert recommendations.classify_weather({"condition": condition, "temperature_c": temp}) == expected


def test_auto_weather_follows_time_of_day():
    """
    Purpose: Auto mode is warmest (16 °C) at 15:00 and coldest (6 °C) at 03:00.

    Args:
        None

    Returns:
        None
    """
    assert weather.auto_temperature(datetime(2026, 1, 1, 15, 0)) == 16
    assert weather.auto_temperature(datetime(2026, 1, 1, 3, 0)) == 6


def test_most_frequent_breaks_ties_by_most_recent():
    """
    Purpose: The most common value wins; on a tie the newest one wins.

    Args:
        None

    Returns:
        None
    """
    assert preferences.most_frequent([2, 1, 1, None]) == 1
    assert preferences.most_frequent([3, 4, 4, 3]) == 3  # newest first
    assert preferences.most_frequent([None, None]) is None


# ---------------------------------------------------------------------------
# Guest scores: time of day + weather (FR-16, FR-17)
# ---------------------------------------------------------------------------


def test_guest_morning_cold_prefers_strong_hot_coffee(seeded_app):
    """
    Purpose: At 08:00 and 4 °C strong hot coffee scores 100 and Hot
        Chocolate 41.7; ties are broken by name.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        recs = ranked(None, MORNING, COLD)

    s = scores(recs)
    assert [r.drink.name for r in recs[:3]] == ["Americano", "Cappuccino", "Doppio"]
    assert s["Doppio"] == 100 and s["Hot Chocolate"] == 41.7 and s["Espresso"] == 70.8
    assert s["Iced Latte"] == 58.3
    assert recs[0].reason == "A strong start to your morning"
    assert set(recs[0].parts) == {"time_of_day", "weather"}  # no history for guests


def test_guest_evening_prefers_low_caffeine(seeded_app):
    """
    Purpose: At 20:00 low-caffeine drinks lead and Doppio drops.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        recs = ranked(None, EVENING, COLD)

    s = scores(recs)
    assert [r.drink.name for r in recs[:3]] == ["Chai Latte", "Green Tea", "Hot Chocolate"]
    assert s["Hot Chocolate"] > s["Doppio"]
    assert recs[0].reason == "Light choice for the evening"


def test_guest_hot_afternoon_prefers_iced_drinks(seeded_app):
    """
    Purpose: At 14:00 and 28 °C the iced drinks come first.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        recs = ranked(None, AFTERNOON, HOT)

    assert [r.drink.name for r in recs[:2]] == ["Iced Latte", "Cold Brew"]
    assert recs[0].score == 100
    assert recs[1].reason == "Cool down, it's 28°C outside"


def test_weather_reason_when_weather_gives_most_points(seeded_app):
    """
    Purpose: A Latte at 23:00 gets no time points, so the weather reason
        "Warm up, it's 4°C outside" is used (score 25/60 = 41.7).

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        latte = Drink.query.filter_by(name="Latte").one()
        rec = recommendations.score_drink(latte, NIGHT, COLD, None)

    assert rec.reason == "Warm up, it's 4°C outside"
    assert rec.score == 41.7


# ---------------------------------------------------------------------------
# History (FR-18) and weights
# ---------------------------------------------------------------------------


def test_history_lifts_users_favourite(seeded_app):
    """
    Purpose: Alice (3 Flat Whites, 1 Latte) gets Flat White first with
        "Popular with you"; weights 40/35/25 give exact scores.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        recs = ranked(get_user("alice@demo.com"), MORNING, COLD)

    s = scores(recs)
    assert [r.drink.name for r in recs[:3]] == ["Flat White", "Latte", "Cappuccino"]
    assert s["Flat White"] == 100 and s["Latte"] == 81.3 and s["Cappuccino"] == 72
    assert recs[0].reason == "Popular with you"


def test_history_keeps_favourite_on_top_in_the_evening(seeded_app):
    """
    Purpose: In the evening Alice's Flat White (65) still beats the
        low-caffeine drinks (60), which fill the other top spots.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        recs = ranked(get_user("alice@demo.com"), EVENING, COLD)

    assert [r.drink.name for r in recs[:3]] == ["Flat White", "Chai Latte", "Green Tea"]
    assert recs[0].score == 65


def test_history_reasons_for_category_and_single_order(seeded_app):
    """
    Purpose: A drink never ordered but from Alice's favourite category says
        "You often order Milk Coffees"; one past order says "You've enjoyed
        this before".

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        history = recommendations.load_history(get_user("alice@demo.com"))
        cappuccino = Drink.query.filter_by(name="Cappuccino").one()
        latte = Drink.query.filter_by(name="Latte").one()
        cap = recommendations.score_drink(cappuccino, EVENING, None, history)
        lat = recommendations.score_drink(latte, EVENING, None, history)

    assert cap.reason == "You often order Milk Coffees"
    assert lat.reason == "You've enjoyed this before"


def test_missing_weather_shares_its_weight(seeded_app):
    """
    Purpose: Without weather, scores are scaled over history + time (75
        points) for users and time only for guests.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        user_scores = scores(ranked(get_user("alice@demo.com"), MORNING, None))
        guest_scores = scores(ranked(None, MORNING, None))

    assert user_scores["Flat White"] == 100
    assert user_scores["Latte"] == 75.1  # (21.33 + 35) / 75
    assert guest_scores["Doppio"] == 100
    assert guest_scores["Espresso"] == 50


def test_scores_always_between_0_and_100(seeded_app):
    """
    Purpose: Every combination of user, time and weather stays within 0–100.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        for user in (None, get_user("alice@demo.com"), get_user("bob@demo.com")):
            for now in (MORNING, AFTERNOON, EVENING, NIGHT):
                for current in (COLD, HOT, {"condition": "sunny", "temperature_c": 15}, None):
                    for rec in ranked(user, now, current):
                        assert 0 <= rec.score <= 100


def test_calculate_score_matches_ranking(seeded_app):
    """
    Purpose: calculate_score(drink, user, now, weather) gives the same score
        as the ranking.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        alice = get_user("alice@demo.com")
        latte = Drink.query.filter_by(name="Latte").one()
        assert recommendations.calculate_score(latte, alice, MORNING, COLD) == 81.3
        assert recommendations.calculate_score(latte, None, MORNING, COLD) == 100


def test_history_uses_last_30_completed_orders(seeded_app):
    """
    Purpose: Only the 30 most recent completed orders count; older and
        non-completed orders are ignored.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    add_completed_order(seeded_app, "bob@demo.com", "Mocha", {"milk": "Whole milk"}, days_ago=40)
    for i in range(30):
        add_completed_order(seeded_app, "bob@demo.com", "Espresso", {}, days_ago=i / 10)

    with seeded_app.app_context():
        history = recommendations.load_history(get_user("bob@demo.com"))

    assert dict(history.drink_counts) == {drink_id(seeded_app, "Espresso"): 30}


def test_unavailable_drinks_are_never_recommended(seeded_app):
    """
    Purpose: With no coffee beans, no coffee drink is scored at all (FR-25).

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    with seeded_app.app_context():
        Ingredient.query.filter_by(name="Coffee beans").one().stock_level = 0
        db.session.commit()
        names = [r.drink.name for r in ranked(get_user("alice@demo.com"), MORNING, COLD)]

    assert sorted(names) == ["Chai Latte", "Green Tea", "Hot Chocolate"]


# ---------------------------------------------------------------------------
# Menu (FR-22–25, FR-56)
# ---------------------------------------------------------------------------


def test_for_you_tab_sorted_with_three_badges(seeded_app, seeded_client):
    """
    Purpose: The default tab lists drinks by score with exactly 3 top-pick
        badges and their reasons.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_conditions(seeded_app, time="08:00", temp=4)

    html = seeded_client.get("/menu").get_data(as_text=True)

    assert menu_drink_names(html)[:3] == ["Americano", "Cappuccino", "Doppio"]
    assert html.count('class="top-badge"') == 3
    assert html.count("A strong start to your morning") == 3
    assert "Cloudy, 4°C · Morning" in html


def test_category_tab_sorted_by_score(seeded_app, seeded_client, login):
    """
    Purpose: Inside a category, Alice's drinks are sorted by her scores and
        the overall top 3 keep their badge.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    set_conditions(seeded_app, time="08:00", temp=4)
    login()
    with seeded_app.app_context():
        milk_coffees = Drink.query.filter_by(name="Latte").one().category_id

    html = seeded_client.get(f"/menu?category={milk_coffees}").get_data(as_text=True)

    assert menu_drink_names(html) == ["Flat White", "Latte", "Cappuccino", "Mocha"]
    assert html.count('class="top-badge"') == 3
    assert "Popular with you" in html


def test_unavailable_drinks_greyed_out_last_in_category(seeded_app, seeded_client):
    """
    Purpose: In a category tab unavailable drinks come last and get no badge.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_conditions(seeded_app, time="20:00", temp=4)
    with seeded_app.app_context():
        Ingredient.query.filter_by(name="Chocolate powder").one().stock_level = 0
        db.session.commit()
        category = Drink.query.filter_by(name="Hot Chocolate").one().category_id

    html = seeded_client.get(f"/menu?category={category}").get_data(as_text=True)
    for_you = seeded_client.get("/menu").get_data(as_text=True)

    assert menu_drink_names(html) == ["Chai Latte", "Green Tea", "Hot Chocolate"]
    assert html.rfind("Not available") > html.find("Green Tea")
    assert "Hot Chocolate" not in menu_drink_names(for_you)


def test_menu_works_when_weather_api_is_down(seeded_app, seeded_client, login):
    """
    Purpose: With the weather API down, the menu still shows top picks and
        a note that weather is unavailable (FR-56).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    set_conditions(seeded_app, time="08:00", online=False)

    guest = seeded_client.get("/menu").get_data(as_text=True)
    login()
    user = seeded_client.get("/menu").get_data(as_text=True)

    assert recommendations.WEATHER_DOWN_NOTE_GUEST in guest
    assert recommendations.WEATHER_DOWN_NOTE in user
    assert "Weather unavailable · Morning" in user
    assert user.count('class="top-badge"') == 3
    assert menu_drink_names(user)[0] == "Flat White"


# ---------------------------------------------------------------------------
# Preference profile
# ---------------------------------------------------------------------------


def test_seeded_profile_preselects_oat_and_strong(seeded_app, seeded_client, login):
    """
    Purpose: Alice's history (mostly oat milk, strong) preselects those
        options; guests get the normal defaults.

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
    alice = seeded_client.get(url).get_data(as_text=True)

    assert checked_option(guest, seeded_app, "milk", "Whole milk")
    assert checked_option(guest, seeded_app, "strength", "Regular")
    assert checked_option(alice, seeded_app, "milk", "Oat milk")
    assert checked_option(alice, seeded_app, "strength", "Strong")
    assert checked_option(alice, seeded_app, "temperature", "Hot")


def test_preference_skipped_when_out_of_stock_or_not_offered(seeded_app, seeded_client, login):
    """
    Purpose: If oat milk is low the default milk is used, but strong is still
        preselected; for an Espresso only the strength preference applies.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    with seeded_app.app_context():
        Ingredient.query.filter_by(name="Oat milk").one().stock_level = 100
        db.session.commit()
    login()

    latte = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Latte')}/customize").get_data(as_text=True)
    espresso = seeded_client.get(f"/drink/{drink_id(seeded_app, 'Espresso')}/customize").get_data(as_text=True)

    assert checked_option(latte, seeded_app, "milk", "Whole milk")
    assert checked_option(latte, seeded_app, "strength", "Strong")
    assert checked_option(espresso, seeded_app, "strength", "Strong")
    assert 'data-group="milk"' not in espresso


def test_user_without_orders_keeps_defaults_until_first_order(seeded_app):
    """
    Purpose: Bob's empty profile keeps the defaults; after he dispenses a
        Latte with skimmed milk and mild strength, those become his
        preferences and are preselected.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    bob = seeded_app.test_client()
    bob.post("/login", data={"email": "bob@demo.com", "password": "Bob2026!"})
    latte = drink_id(seeded_app, "Latte")
    before = bob.get(f"/drink/{latte}/customize").get_data(as_text=True)

    response = bob.post(f"/drink/{latte}/order", data={
        "milk": option_id(seeded_app, "milk", "Skimmed milk"),
        "strength": option_id(seeded_app, "strength", "Mild"),
    })
    pay(bob, int(response.headers["Location"].rstrip("/").split("/")[-1]))
    bob.post("/login", data={"email": "bob@demo.com", "password": "Bob2026!"})  # purchase logged him out
    after = bob.get(f"/drink/{latte}/customize").get_data(as_text=True)

    assert checked_option(before, seeded_app, "milk", "Whole milk")
    assert checked_option(after, seeded_app, "milk", "Skimmed milk")
    assert checked_option(after, seeded_app, "strength", "Mild")
    with seeded_app.app_context():
        profile = get_user("bob@demo.com").preference
        assert profile.preferred_milk_option_id == option_id(seeded_app, "milk", "Skimmed milk")


def test_preference_tie_goes_to_most_recent(seeded_app):
    """
    Purpose: With one skimmed and one (newer) oat order, oat is preferred.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    add_completed_order(seeded_app, "bob@demo.com", "Latte", {"milk": "Skimmed milk"}, days_ago=2)
    add_completed_order(seeded_app, "bob@demo.com", "Latte", {"milk": "Oat milk"}, days_ago=1)

    with seeded_app.app_context():
        profile = preferences.update_preferences(get_user("bob@demo.com"))
        assert profile.preferred_milk_option_id == option_id(seeded_app, "milk", "Oat milk")


def test_preselected_options_can_be_changed(seeded_app, seeded_client, login):
    """
    Purpose: Preselection is only a default: Alice can still order whole milk.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        login (callable): Login helper fixture.

    Returns:
        None
    """
    login()

    response = seeded_client.post(f"/drink/{drink_id(seeded_app, 'Latte')}/order",
                                  data={"milk": option_id(seeded_app, "milk", "Whole milk")})

    assert response.status_code == 302
    with seeded_app.app_context():
        order = Transaction.query.filter_by(status=Transaction.STATUS_PENDING).one()
        assert order.customizations["options"]["milk"] == "Whole milk"


# ---------------------------------------------------------------------------
# Simulation panel: weather and time of day
# ---------------------------------------------------------------------------


def test_simulation_weather_settings_change_menu(seeded_app, seeded_client):
    """
    Purpose: Setting manual sunny 28 °C on the panel makes iced drinks the
        top picks on the menu.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/time", data={"time": "14:00", "action": "set"})
    response = seeded_client.post("/simulation/weather", data={
        "mode": "manual", "condition": "sunny", "temperature": "28",
    }, follow_redirects=True)

    html = seeded_client.get("/menu").get_data(as_text=True)

    assert b"Weather settings saved." in response.data
    assert "Sunny, 28°C · Afternoon" in html
    assert menu_drink_names(html)[:2] == ["Iced Latte", "Cold Brew"]


def test_simulation_weather_api_down_switch(seeded_app, seeded_client):
    """
    Purpose: The "Weather API down" switch makes the service fail and turning
        it off restores it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    form = {"mode": "auto", "condition": "cloudy", "temperature": "12"}
    seeded_client.post("/simulation/weather", data={**form, "api_down": "on"})
    with seeded_app.app_context():
        with pytest.raises(weather.WeatherUnavailableError):
            weather.get_weather(MORNING)

    seeded_client.post("/simulation/weather", data=form)
    with seeded_app.app_context():
        assert weather.get_weather(MORNING)["source"] == "auto"


@pytest.mark.parametrize("form, message", [
    ({"mode": "manual", "condition": "sunny", "temperature": "100"}, b"between -20 and 45"),
    ({"mode": "manual", "condition": "sunny", "temperature": "warm"}, b"as a number"),
    ({"mode": "manual", "condition": "foggy", "temperature": "10"}, b"valid weather condition"),
    ({"mode": "sometimes", "condition": "sunny", "temperature": "10"}, b"auto or manual"),
])
def test_simulation_rejects_invalid_weather(seeded_app, seeded_client, form, message):
    """
    Purpose: Invalid weather settings are refused and nothing changes.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        form (dict): Form data to send.
        message (bytes): Expected error.

    Returns:
        None
    """
    response = seeded_client.post("/simulation/weather", data=form, follow_redirects=True)

    assert message in response.data
    with seeded_app.app_context():
        assert SystemState.get().weather_mode == SystemState.WEATHER_AUTO


def test_simulation_time_override(seeded_app, seeded_client):
    """
    Purpose: Setting a time makes the machine clock and status bar use it;
        "Use real time" removes it.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/time", data={"time": "20:15", "action": "set"})
    page = seeded_client.get("/").get_data(as_text=True)
    with seeded_app.app_context():
        assert clock.now().strftime("%H:%M") == "20:15"
    assert "20:15 <small>SIM</small>" in page

    seeded_client.post("/simulation/time", data={"action": "real"})
    with seeded_app.app_context():
        assert clock.get_time_override() is None


@pytest.mark.parametrize("value", ["25:00", "7:3", "", "noon"])
def test_simulation_rejects_invalid_time(seeded_app, seeded_client, value):
    """
    Purpose: Invalid or empty times are refused and the clock is unchanged.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        value (str): Time to try.

    Returns:
        None
    """
    response = seeded_client.post("/simulation/time", data={"time": value, "action": "set"},
                                  follow_redirects=True)

    assert b"Please enter a time as HH:MM" in response.data
    with seeded_app.app_context():
        assert clock.get_time_override() is None


def test_simulation_panel_shows_weather_and_time_sections(seeded_client):
    """
    Purpose: The panel has the weather and time-of-day sections.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    html = seeded_client.get("/simulation/").get_data(as_text=True)

    assert 'id="weather"' in html and 'id="time"' in html
    assert 'name="api_down"' in html
    assert "Service returns:" in html
