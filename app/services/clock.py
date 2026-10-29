"""Machine clock with an optional simulated time of day (Simulation panel),
so time-based recommendations (FR-16–18) can be tested at any time."""

import re
from datetime import datetime

from app import db
from app.models import SystemState

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ClockSettingsError(Exception):
    """Raised when a simulated time entered on the Simulation panel is invalid."""


def parse_time(text):
    """
    Purpose: Parses a 24-hour "HH:MM" time.

    Args:
        text (str | None): Time as typed, e.g. "07:30".

    Returns:
        tuple[int, int]: Hour and minute.

    Raises:
        ClockSettingsError: If the text is not a valid HH:MM time.
    """
    match = TIME_PATTERN.match((text or "").strip())
    if not match:
        raise ClockSettingsError("Please enter a time as HH:MM, e.g. 07:30.")
    return int(match.group(1)), int(match.group(2))


def get_time_override():
    """
    Purpose: Reads the simulated time set on the Simulation panel.

    Args:
        None

    Returns:
        str | None: "HH:MM", or None when the real time is used.
    """
    state = SystemState.get()
    return state.time_override if state else None


def now():
    """
    Purpose: Returns the machine's current local time, using the simulated
        time of day when one is set (date stays today).

    Args:
        None

    Returns:
        datetime: Current (or simulated) local date and time.
    """
    real = datetime.now()
    override = get_time_override()
    if override is None:
        return real
    hour, minute = parse_time(override)
    return real.replace(hour=hour, minute=minute, second=0, microsecond=0)


def set_time_override(text):
    """
    Purpose: Sets or clears the simulated time of day.

    Args:
        text (str | None): "HH:MM" to simulate, or None/"" for real time.

    Returns:
        str | None: The stored override.

    Raises:
        ClockSettingsError: If the time is invalid or the database is not seeded.
    """
    state = SystemState.get()
    if state is None:
        raise ClockSettingsError("System state missing. Run python seed.py first.")
    if text is None or not text.strip():
        state.time_override = None
    else:
        hour, minute = parse_time(text)
        state.time_override = f"{hour:02d}:{minute:02d}"
    db.session.commit()
    return state.time_override
