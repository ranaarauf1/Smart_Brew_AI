"""Machine maintenance (feature 10, FR-43–52): waste bins, hardware faults,
the error catalogue shown in admin mode and remote diagnostics.

===========================================================================
WASTE RULES (named constants, see README)
===========================================================================
Every dispensed drink adds waste:
  * coffee grounds bin: WASTE_GROUNDS_PER_GRAM_BEANS × the beans used
    (wet spent grounds weigh about twice the dry beans), e.g. Espresso
    9 g beans -> 18 g grounds
  * liquid waste bin:   WASTE_LIQUID_PER_DRINK_ML per drink (rinse water
    and drips)
At WasteBin.ALERT_LEVEL (80 %) a maintenance alert is raised. At
WasteBin.HALT_LEVEL (95 %) no new drink starts (the whole machine is out of
service, admin mode stays available). A drink is also refused if its waste
would push a bin past 100 % ("would overfill"). A drink already being
dispensed always finishes.
===========================================================================
"""

from app import db
from app.models import Drink, ErrorEvent, Ingredient, WasteBin, utcnow
from app.services import connectivity

WASTE_GROUNDS_PER_GRAM_BEANS = 2.0  # g of wet grounds per g of beans
WASTE_LIQUID_PER_DRINK_ML = 30  # ml of rinse water and drips per drink

FAULT_GRINDER = "GRINDER_JAMMED"
FAULT_HEATER = "WATER_HEATER_FAULT"
CODE_BIN_ALERT = "WASTE_BIN_ALERT"
CODE_BIN_FULL = "WASTE_BIN_FULL"

# Faults: what they block and how they are described
FAULTS = {
    FAULT_GRINDER: {
        "label": "grinder jammed",
        "message": "Grinder Jammed: drinks with coffee beans are blocked.",
    },
    FAULT_HEATER: {
        "label": "water heater fault",
        "message": "Water Heater Fault: hot drinks are blocked.",
    },
}

# Event code -> display code, title, type (for the event log filter) and
# next action for the technician (NFR-27)
EVENT_CATALOG = {
    ErrorEvent.CODE_LOW_INGREDIENT: ("INV-101", "Low ingredient", "inventory",
                                     "Refill the ingredient under Ingredients."),
    FAULT_GRINDER: ("HW-201", "Grinder jammed", "hardware",
                    "Open the grinder, remove the blockage, then press Resolve."),
    FAULT_HEATER: ("HW-202", "Water heater fault", "hardware",
                   "Check and reset the heater fuse, then press Resolve."),
    CODE_BIN_ALERT: ("WST-301", "Waste bin almost full", "waste",
                     "Empty the bin soon under Waste bins."),
    CODE_BIN_FULL: ("WST-302", "Waste bin full – drinks stopped", "waste",
                    "Empty the bin under Waste bins to restart service."),
    "NETWORK_DOWN": ("NET-401", "Network down", "connectivity",
                     "Check the network cable or Wi-Fi; cards work offline up to €10."),
    "CARD_PROCESSOR_DOWN": ("NET-402", "Card processor unreachable", "connectivity",
                            "Wait or contact the payment provider; cards are queued."),
    "WALLET_PROCESSOR_DOWN": ("NET-403", "Wallet processor unreachable", "connectivity",
                              "Wait or contact the payment provider; wallets are disabled."),
    "WEATHER_DOWN": ("NET-404", "Weather service unavailable", "connectivity",
                     "No action needed; recommendations work without weather."),
    "CONNECTION_DROPPED": ("NET-405", "Connection dropped during payment", "connectivity",
                           "Restore the network; the payment is checked automatically."),
    "OFFLINE_SYNC": ("NET-410", "Offline queue sent", "connectivity", "No action needed."),
    "OFFLINE_PAYMENT_DECLINED": ("PAY-601", "Offline payment declined on sync (loss)", "payments",
                                 "Check the order; the drink was already given out."),
}
EVENT_KINDS = ("inventory", "hardware", "waste", "connectivity", "payments")

OUT_OF_SERVICE_MESSAGE = (
    "Out of service: the {name} bin is full. Please ask staff to empty it. (Code WST-302)"
)
OVERFILL_MESSAGE = (
    "This drink would overfill the {name} bin ({level:.0f}% full). Please ask staff to "
    "empty it or choose a smaller drink. (Code WST-303)"
)


class MaintenanceError(Exception):
    """Raised for an invalid maintenance action; the message is shown."""


class WasteBinError(Exception):
    """Raised when a drink cannot start because of the waste bins."""


# ---------------------------------------------------------------------------
# Event catalogue and remote diagnostics
# ---------------------------------------------------------------------------


def describe_event(event):
    """
    Purpose: Looks up how an event is shown in admin mode (NFR-27).

    Args:
        event (ErrorEvent): The event.

    Returns:
        dict: Keys code (display code), title, kind and next_action.
    """
    code = event.code
    if code.endswith("_RESTORED"):
        info = ("NET-400", "Service restored", "connectivity", "No action needed.")
    else:
        info = EVENT_CATALOG.get(code, ("GEN-999", code.replace("_", " ").capitalize(),
                                        "other", "Check the event details."))
    return {"code": info[0], "title": info[1], "kind": info[2], "next_action": info[3]}


def get_active_errors():
    """
    Purpose: Lists the unresolved events, newest first, for the Errors tab.

    Args:
        None

    Returns:
        list[ErrorEvent]: Active events.
    """
    return (
        ErrorEvent.query.filter_by(resolved_at=None)
        .order_by(ErrorEvent.created_at.desc(), ErrorEvent.id.desc())
        .all()
    )


def get_event_log(kind=None):
    """
    Purpose: Lists every event, newest first, optionally of one type.

    Args:
        kind (str | None): One of EVENT_KINDS, or None for all.

    Returns:
        list[ErrorEvent]: Matching events.
    """
    events = ErrorEvent.query.order_by(ErrorEvent.created_at.desc(), ErrorEvent.id.desc()).all()
    if kind:
        events = [e for e in events if describe_event(e)["kind"] == kind]
    return events


def resolve_event(event_id):
    """
    Purpose: Marks an active event as resolved (e.g. a fault was fixed).
        The caller commits.

    Args:
        event_id (int | None): ID of the event.

    Returns:
        ErrorEvent: The resolved event.

    Raises:
        MaintenanceError: If the event does not exist or is already resolved.
    """
    event = db.session.get(ErrorEvent, event_id) if event_id else None
    if event is None or event.resolved_at is not None:
        raise MaintenanceError("That event is not active.")
    event.resolved_at = utcnow()
    return event


def report_pending_events():
    """
    Purpose: Sends queued events to remote diagnostics while the network is
        up (simulated: marks them as sent). Offline they stay queued.

    Args:
        None

    Returns:
        int: Number of events sent now.
    """
    if not connectivity.is_available(connectivity.NETWORK):
        return 0
    count = ErrorEvent.query.filter_by(reported_at=None).update(
        {"reported_at": utcnow()}, synchronize_session=False
    )
    if count:
        db.session.commit()
    return count


# ---------------------------------------------------------------------------
# Hardware faults (simulated)
# ---------------------------------------------------------------------------


def get_active_fault(code):
    """
    Purpose: Finds the active event for a hardware fault.

    Args:
        code (str): FAULT_GRINDER or FAULT_HEATER.

    Returns:
        ErrorEvent | None: The active fault, or None.
    """
    return ErrorEvent.query.filter_by(code=code, resolved_at=None).first()


def trigger_fault(code):
    """
    Purpose: Simulates a hardware fault. It stays active until a technician
        resolves it in admin mode.

    Args:
        code (str): FAULT_GRINDER or FAULT_HEATER.

    Returns:
        ErrorEvent | None: The new fault event, or None if already active.

    Raises:
        MaintenanceError: If the fault is unknown.
    """
    if code not in FAULTS:
        raise MaintenanceError("Unknown fault.")
    if get_active_fault(code) is not None:
        return None
    event = ErrorEvent(code=code, component="Hardware", message=FAULTS[code]["message"],
                       severity=ErrorEvent.SEVERITY_CRITICAL)
    db.session.add(event)
    db.session.commit()
    return event


def get_fault_blockers(drink):
    """
    Purpose: Lists active faults that stop a drink: Grinder Jammed blocks
        drinks with coffee beans, Water Heater Fault blocks hot drinks.

    Args:
        drink (Drink): The drink.

    Returns:
        list[str]: Fault labels, e.g. ["grinder jammed"]; empty if none.
    """
    blockers = []
    uses_beans = any(i.ingredient.kind == Ingredient.KIND_COFFEE for i in drink.recipe_items)
    if uses_beans and get_active_fault(FAULT_GRINDER) is not None:
        blockers.append(FAULTS[FAULT_GRINDER]["label"])
    if drink.serving == Drink.SERVING_HOT and get_active_fault(FAULT_HEATER) is not None:
        blockers.append(FAULTS[FAULT_HEATER]["label"])
    return blockers


# ---------------------------------------------------------------------------
# Waste bins
# ---------------------------------------------------------------------------


def get_bins():
    """
    Purpose: Lists the waste bins.

    Args:
        None

    Returns:
        list[WasteBin]: Bins sorted by name.
    """
    return WasteBin.query.order_by(WasteBin.name).all()


def calculate_waste(needs):
    """
    Purpose: Calculates the waste one drink adds to each bin (see the
        WASTE RULES above).

    Args:
        needs (dict[Ingredient, float]): Ingredient quantities of the drink.

    Returns:
        dict[str, float]: Amount per bin kind (g of grounds, ml of liquid).
    """
    beans = sum(qty for ing, qty in needs.items() if ing.kind == Ingredient.KIND_COFFEE)
    return {
        WasteBin.KIND_GROUNDS: beans * WASTE_GROUNDS_PER_GRAM_BEANS,
        WasteBin.KIND_LIQUID: WASTE_LIQUID_PER_DRINK_ML,
    }


def percent_of(bin_, amount):
    """
    Purpose: Converts a waste amount into percent of a bin's capacity.

    Args:
        bin_ (WasteBin): The bin.
        amount (float): Waste in the bin's unit.

    Returns:
        float: Percent of capacity.
    """
    return amount / bin_.capacity * 100


def get_out_of_service_reason():
    """
    Purpose: Checks whether the whole machine is out of service because a
        waste bin reached the halt level (95 %).

    Args:
        None

    Returns:
        str | None: Message for customers, or None if in service.
    """
    for bin_ in get_bins():
        if bin_.level_percent >= WasteBin.HALT_LEVEL:
            return OUT_OF_SERVICE_MESSAGE.format(name=bin_.name.lower())
    return None


def check_waste_capacity(needs):
    """
    Purpose: Checks before a drink starts that no bin is at the halt level
        and that the drink's waste will not overfill a bin.

    Args:
        needs (dict[Ingredient, float]): Ingredient quantities of the drink.

    Returns:
        None

    Raises:
        WasteBinError: With the message for the customer.
    """
    reason = get_out_of_service_reason()
    if reason:
        raise WasteBinError(reason)
    waste = calculate_waste(needs)
    for bin_ in get_bins():
        if bin_.level_percent + percent_of(bin_, waste.get(bin_.kind, 0)) > 100:
            raise WasteBinError(OVERFILL_MESSAGE.format(name=bin_.name.lower(),
                                                        level=bin_.level_percent))


def get_open_bin_event(bin_, code):
    """
    Purpose: Finds a bin's unresolved alert or full event.

    Args:
        bin_ (WasteBin): The bin.
        code (str): CODE_BIN_ALERT or CODE_BIN_FULL.

    Returns:
        ErrorEvent | None: The open event, or None.
    """
    return ErrorEvent.query.filter_by(code=code, waste_bin_id=bin_.id, resolved_at=None).first()


def update_bin_events(bin_):
    """
    Purpose: Keeps a bin's events in line with its level: opens an alert at
        80 % and a "full" event at 95 %, and resolves them when the level is
        below again. The caller commits.

    Args:
        bin_ (WasteBin): The bin after its level changed.

    Returns:
        None
    """
    for code, limit, severity in ((CODE_BIN_ALERT, WasteBin.ALERT_LEVEL, ErrorEvent.SEVERITY_WARNING),
                                  (CODE_BIN_FULL, WasteBin.HALT_LEVEL, ErrorEvent.SEVERITY_CRITICAL)):
        open_event = get_open_bin_event(bin_, code)
        if bin_.level_percent >= limit and open_event is None:
            db.session.add(ErrorEvent(
                code=code, component="Waste", severity=severity, waste_bin_id=bin_.id,
                message=f"{bin_.name} bin at {bin_.level_percent:.0f}% (limit {limit}%).",
            ))
        elif bin_.level_percent < limit and open_event is not None:
            open_event.resolved_at = utcnow()


def add_waste(needs):
    """
    Purpose: Adds a dispensed drink's waste to the bins and raises alerts.
        Called after the drink is made, so a drink in progress always
        finishes. The caller commits.

    Args:
        needs (dict[Ingredient, float]): Ingredient quantities of the drink.

    Returns:
        None
    """
    waste = calculate_waste(needs)
    for bin_ in get_bins():
        bin_.level_percent = min(100.0, bin_.level_percent + percent_of(bin_, waste.get(bin_.kind, 0)))
        update_bin_events(bin_)


def set_bin_level(bin_id, raw_level):
    """
    Purpose: Sets a bin's level from the Simulation panel (0–100 %).

    Args:
        bin_id (int | None): ID of the bin.
        raw_level (str | float | None): Level as entered.

    Returns:
        WasteBin: The updated bin (committed).

    Raises:
        MaintenanceError: If the bin is unknown or the level invalid.
    """
    bin_ = db.session.get(WasteBin, bin_id) if bin_id else None
    if bin_ is None:
        raise MaintenanceError("Unknown waste bin.")
    try:
        level = float(raw_level)
    except (TypeError, ValueError):
        raise MaintenanceError("Please enter the level as a number.") from None
    if level != level or not 0 <= level <= 100:  # level != level: NaN
        raise MaintenanceError("The level must be between 0 and 100%.")
    bin_.level_percent = level
    update_bin_events(bin_)
    db.session.commit()
    return bin_


def empty_bin(bin_id):
    """
    Purpose: Empties a waste bin (admin action) and resolves its events.
        The caller commits.

    Args:
        bin_id (int | None): ID of the bin.

    Returns:
        WasteBin: The emptied bin.

    Raises:
        MaintenanceError: If the bin is unknown.
    """
    bin_ = db.session.get(WasteBin, bin_id) if bin_id else None
    if bin_ is None:
        raise MaintenanceError("Unknown waste bin.")
    bin_.level_percent = 0
    bin_.emptied_at = utcnow()
    update_bin_events(bin_)
    return bin_
