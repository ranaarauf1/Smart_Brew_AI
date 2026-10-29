"""Mock weather service (no real API, no keys). In auto mode the temperature
follows the time of day; in manual mode it returns the values set on the
Simulation panel. The panel can also switch the "API" off (FR-56)."""

import math

from app import db
from app.models import SystemState

CONDITIONS = ("sunny", "cloudy", "rainy", "snowy")
CONDITION_ICONS = {"sunny": "☀️", "cloudy": "☁️", "rainy": "🌧️", "snowy": "❄️"}
MIN_TEMP_C = -20
MAX_TEMP_C = 45

# Auto mode: daily curve between these values, warmest at 15:00
AUTO_MIN_TEMP_C = 6
AUTO_MAX_TEMP_C = 16
AUTO_WARMEST_HOUR = 15
AUTO_CONDITION = "cloudy"


class WeatherUnavailableError(Exception):
    """Raised when the (simulated) weather API is down (FR-56)."""


class WeatherSettingsError(Exception):
    """Raised when weather settings entered on the Simulation panel are invalid."""


def auto_temperature(now):
    """
    Purpose: Calculates the auto-mode temperature for a time of day: a smooth
        daily curve, coldest at 03:00 and warmest at 15:00.

    Args:
        now (datetime): The time to calculate for.

    Returns:
        float: Temperature in °C, rounded to 1 decimal.
    """
    hours = now.hour + now.minute / 60
    middle = (AUTO_MIN_TEMP_C + AUTO_MAX_TEMP_C) / 2
    amplitude = (AUTO_MAX_TEMP_C - AUTO_MIN_TEMP_C) / 2
    angle = (hours - AUTO_WARMEST_HOUR) / 24 * 2 * math.pi
    return round(middle + amplitude * math.cos(angle), 1)


def get_weather(now):
    """
    Purpose: Returns the current weather from the mock service.

    Args:
        now (datetime): Current (or simulated) time, used in auto mode.

    Returns:
        dict: Keys condition (str), temperature_c (float) and source
            ("auto" or "manual").

    Raises:
        WeatherUnavailableError: If the API is switched off on the
            Simulation panel or the database is not seeded.
    """
    state = SystemState.get()
    if state is None or not state.weather_online:
        raise WeatherUnavailableError("Weather service unavailable.")
    if state.weather_mode == SystemState.WEATHER_MANUAL:
        return {
            "condition": state.weather_condition,
            "temperature_c": state.weather_temp_c,
            "source": SystemState.WEATHER_MANUAL,
        }
    return {
        "condition": AUTO_CONDITION,
        "temperature_c": auto_temperature(now),
        "source": SystemState.WEATHER_AUTO,
    }


def describe(weather):
    """
    Purpose: Formats weather for display, e.g. "☁️ Cloudy, 12°C".

    Args:
        weather (dict | None): Result of get_weather(), or None.

    Returns:
        str: Short description, or "Weather unavailable".
    """
    if weather is None:
        return "Weather unavailable"
    icon = CONDITION_ICONS.get(weather["condition"], "")
    return f"{icon} {weather['condition'].capitalize()}, {round(weather['temperature_c'])}°C".strip()


def update_settings(mode, condition, temperature, online):
    """
    Purpose: Saves the weather settings from the Simulation panel.

    Args:
        mode (str | None): "auto" or "manual".
        condition (str | None): One of CONDITIONS.
        temperature (str | float | None): Temperature in °C for manual mode.
        online (bool): False simulates the weather API being down (FR-56).

    Returns:
        SystemState: The updated state.

    Raises:
        WeatherSettingsError: If a value is invalid or the database is not seeded.
    """
    state = SystemState.get()
    if state is None:
        raise WeatherSettingsError("System state missing. Run python seed.py first.")
    if mode not in (SystemState.WEATHER_AUTO, SystemState.WEATHER_MANUAL):
        raise WeatherSettingsError("Please choose auto or manual weather.")
    if condition not in CONDITIONS:
        raise WeatherSettingsError("Please choose a valid weather condition.")
    try:
        temp = float(temperature)
    except (TypeError, ValueError):
        raise WeatherSettingsError("Please enter the temperature as a number.") from None
    if temp != temp or not MIN_TEMP_C <= temp <= MAX_TEMP_C:  # temp != temp: NaN
        raise WeatherSettingsError(
            f"Temperature must be between {MIN_TEMP_C} and {MAX_TEMP_C} °C."
        )

    state.weather_mode = mode
    state.weather_condition = condition
    state.weather_temp_c = temp
    state.weather_online = bool(online)
    db.session.commit()
    return state
