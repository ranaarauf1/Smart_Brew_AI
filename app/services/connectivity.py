"""Connectivity: the simulated network and outside services (feature 8:
FR-53–55, FR-58). Every outside service needs the network; each one can
also fail on its own. Every change is logged as an event (feature 10
dashboard)."""

from app import db
from app.models import ErrorEvent, SystemState, utcnow

NETWORK = "network"
WEATHER = "weather"
CARD_PROCESSOR = "card_processor"
WALLET_PROCESSOR = "wallet_processor"

SERVICE_FIELDS = {
    NETWORK: "network_online",
    WEATHER: "weather_online",
    CARD_PROCESSOR: "card_processor_online",
    WALLET_PROCESSOR: "wallet_processor_online",
}
SERVICE_LABELS = {
    NETWORK: "Network",
    WEATHER: "Weather API",
    CARD_PROCESSOR: "Card processor",
    WALLET_PROCESSOR: "Wallet processor",
}

DROP_OFF = "off"
DROP_BEFORE_CHARGE = "before_charge"  # the bank never receives the payment
DROP_AFTER_CHARGE = "after_charge"  # the bank charges, the answer is lost
DROP_MODES = (DROP_OFF, DROP_BEFORE_CHARGE, DROP_AFTER_CHARGE)

CODE_CONNECTION_DROPPED = "CONNECTION_DROPPED"


class ConnectivityError(Exception):
    """Raised for an invalid switch or drop mode on the Simulation panel."""


def get_state():
    """
    Purpose: Loads the system state with the simulation switches.

    Args:
        None

    Returns:
        SystemState | None: The state row, or None before seeding.
    """
    return SystemState.get()


def is_switch_on(service):
    """
    Purpose: Reads one service's own switch, ignoring the network.

    Args:
        service (str): One of SERVICE_FIELDS.

    Returns:
        bool: True if the service itself is up (True before seeding).
    """
    state = get_state()
    return True if state is None else getattr(state, SERVICE_FIELDS[service])


def is_available(service):
    """
    Purpose: Checks whether a service can be reached right now: the network
        must be up and, for outside services, the service itself too.

    Args:
        service (str): One of SERVICE_FIELDS.

    Returns:
        bool: True if the service is reachable.
    """
    if not is_switch_on(NETWORK):
        return False
    return service == NETWORK or is_switch_on(service)


def is_offline():
    """
    Purpose: Checks whether the machine is in offline mode (network down).

    Args:
        None

    Returns:
        bool: True if the network is down.
    """
    return not is_available(NETWORK)


def get_status():
    """
    Purpose: Collects the status of every service for the screens.

    Args:
        None

    Returns:
        dict: Keys offline (bool) and services (service -> available bool).
    """
    return {
        "offline": is_offline(),
        "services": {service: is_available(service) for service in SERVICE_FIELDS},
    }


def log_change(service, online, details=""):
    """
    Purpose: Logs a connectivity change as an event. Going down opens a
        warning; coming back resolves it and adds an info event.

    Args:
        service (str): The service that changed.
        online (bool): Its new state.
        details (str): Extra text for the message.

    Returns:
        ErrorEvent: The event created (caller commits).
    """
    name = service.upper()
    label = SERVICE_LABELS[service]
    now = utcnow()
    if online:
        for open_event in ErrorEvent.query.filter_by(code=f"{name}_DOWN", resolved_at=None):
            open_event.resolved_at = now
        event = ErrorEvent(
            code=f"{name}_RESTORED", component="Connectivity",
            message=f"{label} is back online. {details}".strip(),
            severity=ErrorEvent.SEVERITY_INFO, resolved_at=now,
        )
    else:
        event = ErrorEvent(
            code=f"{name}_DOWN", component="Connectivity",
            message=f"{label} is unavailable. {details}".strip(),
            severity=ErrorEvent.SEVERITY_WARNING,
        )
    db.session.add(event)
    return event


def set_service(service, online, sync=True):
    """
    Purpose: Switches a simulated service on or off, logs the change and,
        when payments become reachable again, sends the offline queue
        automatically (FR-55).

    Args:
        service (str): One of SERVICE_FIELDS.
        online (bool): New state.
        sync (bool): False to skip the automatic sync.

    Returns:
        str | None: Summary of the automatic sync, or None if none ran.

    Raises:
        ConnectivityError: If the service is unknown or the database is not seeded.
    """
    state = get_state()
    if service not in SERVICE_FIELDS or state is None:
        raise ConnectivityError("Unknown service.")
    if getattr(state, SERVICE_FIELDS[service]) == bool(online):
        return None  # no change, nothing to log

    setattr(state, SERVICE_FIELDS[service], bool(online))
    log_change(service, bool(online))
    db.session.commit()

    payment_paths = (NETWORK, CARD_PROCESSOR, WALLET_PROCESSOR)
    if online and sync and service in payment_paths and is_available(service):
        from app.services import offline  # local import: offline uses this module

        return offline.run_sync()
    return None


def set_drop_mode(mode):
    """
    Purpose: Arms "Drop connection during next payment" (FR-62 testing).

    Args:
        mode (str): One of DROP_MODES.

    Returns:
        None

    Raises:
        ConnectivityError: If the mode is unknown or the database is not seeded.
    """
    state = get_state()
    if mode not in DROP_MODES or state is None:
        raise ConnectivityError("Unknown drop mode.")
    state.drop_next_payment = mode
    db.session.commit()


def consume_drop_mode():
    """
    Purpose: Reads and clears the armed drop mode (it applies to one payment).

    Args:
        None

    Returns:
        str: The mode that was armed (DROP_OFF if none).
    """
    state = get_state()
    if state is None or state.drop_next_payment == DROP_OFF:
        return DROP_OFF
    mode = state.drop_next_payment
    state.drop_next_payment = DROP_OFF
    db.session.commit()
    return mode


def record_connection_drop():
    """
    Purpose: Simulates the connection dropping in the middle of a payment:
        the network goes down and the drop is logged.

    Args:
        None

    Returns:
        None
    """
    state = get_state()
    db.session.add(ErrorEvent(
        code=CODE_CONNECTION_DROPPED, component="Connectivity",
        message="Connection dropped during a payment; the result will be checked on reconnect.",
        severity=ErrorEvent.SEVERITY_WARNING, resolved_at=utcnow(),
    ))
    if state is not None and state.network_online:
        state.network_online = False
        log_change(NETWORK, False, "Dropped during a payment.")
    db.session.commit()
