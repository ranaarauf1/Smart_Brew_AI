"""Personalized drink recommendations (feature 6: FR-16–18, FR-22–25, FR-56).

===========================================================================
SCORING RULES (all weights live here)
===========================================================================
Each available drink gets points from up to three parts:

  Part          Max points  Based on
  ------------  ----------  ---------------------------------------------
  history           40      the user's last 30 completed orders
  time_of_day       35      the drink's caffeine vs. the time of day
  weather           25      hot vs. iced drink vs. current weather

The final score is scaled to 0–100 using only the parts that are available:

    score = 100 × points earned / sum of max points of available parts

So a guest (no history) is scored from time (35) and weather (25) only, a
user whose weather API is down from history (40) and time (35) only, and
the missing part's weight is shared by the others in proportion.

History (40): 70 % from how often the user ordered *this drink* (relative to
their most-ordered drink) and 30 % from how often they order from its
*category* (relative to their most-ordered category).

Time of day (35): the drink's caffeine (mg) is matched to the period:
  morning   06–11  ≥ 120 mg full, 60–119 mg half, otherwise none
  afternoon 11–17  40–130 mg full, otherwise half
  evening   17–22  ≤ 50 mg full, 51–100 mg half, otherwise none
  night     22–06  ≤ 30 mg full, 31–60 mg half, otherwise none

Weather (25):
  cold  (≤ 10 °C, or rainy/snowy below 23 °C): hot drinks full, iced none
  mild  (11–22 °C):                            hot and iced half each
  hot   (≥ 23 °C):                             iced full, hot 20 %

Unavailable drinks are never scored (FR-25). The menu is sorted by score
(highest first), ties broken by drink name; the top 3 get a badge with the
reason of the part that gave them the most points.
===========================================================================
"""

from collections import Counter
from dataclasses import dataclass, field

from app.models import Drink
from app.services import clock, inventory, weather as weather_service
# The 30-order history window is shared with the preference profile
from app.services.preferences import HISTORY_ORDER_LIMIT, get_recent_completed_orders

WEIGHTS = {"history": 40, "time_of_day": 35, "weather": 25}
HISTORY_DRINK_SHARE = 0.7
HISTORY_CATEGORY_SHARE = 0.3
TOP_PICKS = 3

# (period, start hour inclusive, end hour exclusive); night wraps past midnight
TIME_PERIODS = (
    ("morning", 6, 11),
    ("afternoon", 11, 17),
    ("evening", 17, 22),
    ("night", 22, 6),
)

# Per period: caffeine bands (min mg, max mg or None, fraction of points),
# the fraction for drinks outside every band, and the badge reason.
TIME_RULES = {
    "morning": {
        "bands": ((120, None, 1.0), (60, 119, 0.5)),
        "other": 0.0,
        "reason": "A strong start to your morning",
    },
    "afternoon": {
        "bands": ((40, 130, 1.0),),
        "other": 0.5,
        "reason": "A good afternoon pick-me-up",
    },
    "evening": {
        "bands": ((0, 50, 1.0), (51, 100, 0.5)),
        "other": 0.0,
        "reason": "Light choice for the evening",
    },
    "night": {
        "bands": ((0, 30, 1.0), (31, 60, 0.5)),
        "other": 0.0,
        "reason": "Low in caffeine for late hours",
    },
}

COLD_MAX_C = 10
HOT_MIN_C = 23
WET_CONDITIONS = ("rainy", "snowy")

# Weather class -> fraction of weather points for hot and iced drinks
WEATHER_RULES = {
    "cold": {Drink.SERVING_HOT: 1.0, Drink.SERVING_ICED: 0.0},
    "mild": {Drink.SERVING_HOT: 0.5, Drink.SERVING_ICED: 0.5},
    "hot": {Drink.SERVING_HOT: 0.2, Drink.SERVING_ICED: 1.0},
}

WEATHER_DOWN_NOTE = "Weather unavailable – suggestions use the time of day and your history."
WEATHER_DOWN_NOTE_GUEST = "Weather unavailable – suggestions use the time of day."


@dataclass
class History:
    """Order counts from a user's recent completed orders."""

    drink_counts: Counter
    category_counts: Counter


@dataclass
class Recommendation:
    """The score of one drink with its points per part and badge reason."""

    drink: Drink
    score: float
    parts: dict = field(default_factory=dict)  # part -> points earned
    reason: str | None = None


@dataclass
class MenuRecommendations:
    """Everything the menu needs: ranked drinks, top picks and weather info."""

    ranked: list  # list[Recommendation], best first, available drinks only
    top_ids: set
    weather: dict | None
    note: str | None
    period: str


# ---------------------------------------------------------------------------
# Parts of the score
# ---------------------------------------------------------------------------


def get_time_period(now):
    """
    Purpose: Finds the period of the day for a time (FR-16).

    Args:
        now (datetime): The time.

    Returns:
        str: "morning", "afternoon", "evening" or "night".
    """
    for period, start, end in TIME_PERIODS:
        if start < end and start <= now.hour < end:
            return period
        if start > end and (now.hour >= start or now.hour < end):  # wraps midnight
            return period
    return "night"


def caffeine_fraction(caffeine_mg, rule):
    """
    Purpose: Looks up which share of the time points a caffeine level earns.

    Args:
        caffeine_mg (int): The drink's caffeine.
        rule (dict): One entry of TIME_RULES.

    Returns:
        float: Fraction between 0 and 1.
    """
    for low, high, fraction in rule["bands"]:
        if caffeine_mg >= low and (high is None or caffeine_mg <= high):
            return fraction
    return rule["other"]


def time_points(drink, now):
    """
    Purpose: Scores a drink for the time of day (FR-16).

    Args:
        drink (Drink): The drink.
        now (datetime): Current (or simulated) time.

    Returns:
        tuple[float, str]: Points (0–35) and the reason text.
    """
    rule = TIME_RULES[get_time_period(now)]
    return WEIGHTS["time_of_day"] * caffeine_fraction(drink.caffeine_mg, rule), rule["reason"]


def classify_weather(weather):
    """
    Purpose: Puts the weather into the cold, mild or hot class.

    Args:
        weather (dict): Keys condition and temperature_c.

    Returns:
        str: "cold", "mild" or "hot".
    """
    temp = weather["temperature_c"]
    if temp >= HOT_MIN_C:
        return "hot"
    if temp <= COLD_MAX_C or weather["condition"] in WET_CONDITIONS:
        return "cold"
    return "mild"


def weather_points(drink, weather):
    """
    Purpose: Scores a drink for the current weather (FR-17).

    Args:
        drink (Drink): The drink.
        weather (dict): Keys condition and temperature_c.

    Returns:
        tuple[float, str]: Points (0–25) and the reason text.
    """
    weather_class = classify_weather(weather)
    fraction = WEATHER_RULES[weather_class][drink.serving]
    temp = round(weather["temperature_c"])
    if weather_class == "cold":
        reason = f"Warm up, it's {temp}°C outside"
    elif weather_class == "hot":
        reason = f"Cool down, it's {temp}°C outside"
    else:
        reason = f"Nice for a mild {temp}°C day"
    return WEIGHTS["weather"] * fraction, reason


def load_history(user, limit=HISTORY_ORDER_LIMIT):
    """
    Purpose: Counts the drinks and categories in a user's last completed
        orders (FR-18).

    Args:
        user (User | None): The user, or None for a guest.
        limit (int): How many recent orders to use.

    Returns:
        History | None: The counts, or None for guests and users without
            completed orders (the history part is then left out).
    """
    if user is None:
        return None
    orders = get_recent_completed_orders(user, limit)
    if not orders:
        return None
    return History(
        drink_counts=Counter(order.drink_id for order in orders),
        category_counts=Counter(order.drink.category_id for order in orders),
    )


def history_points(drink, history):
    """
    Purpose: Scores a drink by how often the user orders it and its category.

    Args:
        drink (Drink): The drink.
        history (History): The user's order counts.

    Returns:
        tuple[float, str]: Points (0–40) and the reason text.
    """
    drink_count = history.drink_counts.get(drink.id, 0)
    category_count = history.category_counts.get(drink.category_id, 0)
    drink_share = drink_count / max(history.drink_counts.values())
    category_share = category_count / max(history.category_counts.values())
    points = WEIGHTS["history"] * (
        HISTORY_DRINK_SHARE * drink_share + HISTORY_CATEGORY_SHARE * category_share
    )

    if drink_count >= 2:
        reason = "Popular with you"
    elif drink_count == 1:
        reason = "You've enjoyed this before"
    else:
        reason = f"You often order {drink.category.name}"
    return points, reason


# ---------------------------------------------------------------------------
# Combined score
# ---------------------------------------------------------------------------


def score_drink(drink, now, weather, history):
    """
    Purpose: Combines the available parts into a 0–100 score and picks the
        badge reason from the part that gave the most points.

    Args:
        drink (Drink): The drink (assumed available).
        now (datetime): Current (or simulated) time.
        weather (dict | None): Current weather, or None if unavailable.
        history (History | None): The user's order counts, or None.

    Returns:
        Recommendation: Score, points per part and reason.
    """
    parts = {}
    reasons = {}
    if history is not None:
        parts["history"], reasons["history"] = history_points(drink, history)
    parts["time_of_day"], reasons["time_of_day"] = time_points(drink, now)
    if weather is not None:
        parts["weather"], reasons["weather"] = weather_points(drink, weather)

    # Scale to 0–100 over the parts that are available (missing weight is shared)
    possible = sum(WEIGHTS[part] for part in parts)
    score = round(100 * sum(parts.values()) / possible, 1)

    best_part = max(parts, key=lambda part: parts[part])  # dict order breaks ties
    reason = reasons[best_part] if parts[best_part] > 0 else None
    return Recommendation(drink=drink, score=score, parts=parts, reason=reason)


def calculate_score(drink, user, now, weather):
    """
    Purpose: Calculates the recommendation score of a drink for a user.

    Args:
        drink (Drink): The drink to score.
        user (User | None): The logged-in user, or None for a guest.
        now (datetime): Current date and time.
        weather (dict | None): Current weather, or None if unavailable.

    Returns:
        float: Score between 0 and 100; higher means more recommended.
    """
    return score_drink(drink, now, weather, load_history(user)).score


def rank_drinks(drinks, user, now, weather):
    """
    Purpose: Scores the available drinks and sorts them best first, ties by
        name (FR-22). Unavailable drinks are left out (FR-25).

    Args:
        drinks (Iterable[Drink]): Candidate drinks.
        user (User | None): The logged-in user, or None for a guest.
        now (datetime): Current (or simulated) time.
        weather (dict | None): Current weather, or None if unavailable.

    Returns:
        list[Recommendation]: Ranked recommendations.
    """
    history = load_history(user)
    ranked = [
        score_drink(drink, now, weather, history)
        for drink in drinks
        if drink.is_active and inventory.is_drink_available(drink)
    ]
    ranked.sort(key=lambda rec: (-rec.score, rec.drink.name))
    return ranked


def get_menu_recommendations(user, now=None):
    """
    Purpose: Builds the recommendations for the menu: all available drinks
        ranked for this user, the top 3, the weather and a note when the
        weather service is down (FR-56).

    Args:
        user (User | None): The logged-in user, or None for a guest.
        now (datetime | None): Time to use; defaults to the machine clock.

    Returns:
        MenuRecommendations: Ranked drinks and display information.
    """
    now = now or clock.now()
    note = None
    try:
        weather = weather_service.get_weather(now)
    except weather_service.WeatherUnavailableError:
        weather = None
        note = WEATHER_DOWN_NOTE if user is not None else WEATHER_DOWN_NOTE_GUEST

    ranked = rank_drinks(Drink.query.filter_by(is_active=True).all(), user, now, weather)
    return MenuRecommendations(
        ranked=ranked,
        top_ids={rec.drink.id for rec in ranked[:TOP_PICKS]},
        weather=weather,
        note=note,
        period=get_time_period(now),
    )
