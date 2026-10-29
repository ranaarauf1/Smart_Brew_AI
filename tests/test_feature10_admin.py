"""Tests for feature 10: admin mode access (PIN with lockout, NFC master
card, service key), audit log, ingredients, waste bins, faults, errors,
event log and remote diagnostics (FR-43–52, NFR-12, NFR-27, NFR-31)."""

import time
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (
    AdminAuditLog,
    AuditLogImmutableError,
    CustomizationOption,
    Drink,
    ErrorEvent,
    Ingredient,
    ProcessorCharge,
    SystemState,
    Transaction,
    WasteBin,
)
from app.services import admin, inventory, maintenance, recommendations
from tests.conftest import pay

PIN = "1234"


def enter_admin(client, pin=PIN):
    """
    Purpose: Submits the admin PIN keypad.

    Args:
        client (FlaskClient): The test client.
        pin (str): PIN to type.

    Returns:
        TestResponse: The response (not followed).
    """
    return client.post("/admin/login", data={"pin": pin})


def audit_entries(app):
    """
    Purpose: Lists the audit log as (action, actor, details), oldest first.

    Args:
        app (Flask): The test application.

    Returns:
        list[tuple[str, str, str]]: Audit entries.
    """
    with app.app_context():
        return [(e.action, e.actor, e.details) for e in AdminAuditLog.query.order_by(AdminAuditLog.id)]


def actions(app):
    """
    Purpose: Lists just the audit actions, oldest first.

    Args:
        app (Flask): The test application.

    Returns:
        list[str]: Actions.
    """
    return [a for a, _, _ in audit_entries(app)]


def ingredient(app, name):
    """
    Purpose: Loads an ingredient (detached) for assertions.

    Args:
        app (Flask): The test application.
        name (str): Ingredient name.

    Returns:
        Ingredient: The ingredient.
    """
    with app.app_context():
        found = Ingredient.query.filter_by(name=name).one()
        db.session.expunge(found)
        return found


def bin_by_kind(app, kind):
    """
    Purpose: Loads a waste bin (detached) for assertions.

    Args:
        app (Flask): The test application.
        kind (str): WasteBin.KIND_GROUNDS or KIND_LIQUID.

    Returns:
        WasteBin: The bin.
    """
    with app.app_context():
        found = WasteBin.query.filter_by(kind=kind).one()
        db.session.expunge(found)
        return found


def set_bin(app, kind, level, capacity=None):
    """
    Purpose: Sets a bin's level (and optionally capacity) directly.

    Args:
        app (Flask): The test application.
        kind (str): Bin kind.
        level (float): Level in percent.
        capacity (float | None): New capacity, or None to keep it.

    Returns:
        None
    """
    with app.app_context():
        found = WasteBin.query.filter_by(kind=kind).one()
        found.level_percent = level
        if capacity is not None:
            found.capacity = capacity
        db.session.commit()


def new_order(client, app, drink="Espresso", **form):
    """
    Purpose: Submits an order for a drink.

    Args:
        client (FlaskClient): The test client.
        app (Flask): The test application.
        drink (str): Drink name.
        **form: Extra form fields (option IDs).

    Returns:
        TestResponse: The order POST response (not followed).
    """
    with app.app_context():
        drink_id = Drink.query.filter_by(name=drink).one().id
    return client.post(f"/drink/{drink_id}/order", data=form)


def order_id_from(response):
    """
    Purpose: Reads the order ID from the redirect after ordering.

    Args:
        response (TestResponse): Redirect to /order/<id>.

    Returns:
        int: The order ID.
    """
    assert response.status_code == 302 and "/order/" in response.headers["Location"], response.data
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


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


def open_events(app, code):
    """
    Purpose: Counts unresolved events with a code.

    Args:
        app (Flask): The test application.
        code (str): Event code.

    Returns:
        int: Number of open events.
    """
    with app.app_context():
        return ErrorEvent.query.filter_by(code=code, resolved_at=None).count()


def set_admin_idle(client, seconds):
    """
    Purpose: Moves the admin session's last activity into the past.

    Args:
        client (FlaskClient): The test client.
        seconds (float): Seconds of simulated inactivity.

    Returns:
        None
    """
    with client.session_transaction() as sess:
        data = sess[admin.SESSION_ADMIN_KEY]
        data["last_activity"] = time.time() - seconds
        sess[admin.SESSION_ADMIN_KEY] = data


@pytest.fixture
def technician(seeded_client):
    """
    Purpose: Provides a client that is in admin mode (entered by PIN).

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        FlaskClient: The client in admin mode.
    """
    assert enter_admin(seeded_client).status_code == 302
    return seeded_client


# ---------------------------------------------------------------------------
# Ways in
# ---------------------------------------------------------------------------


def test_correct_pin_opens_dashboard_and_is_audited(seeded_app, seeded_client):
    """
    Purpose: The right PIN opens the dashboard; the entry is logged with
        the access method.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = enter_admin(seeded_client)
    dashboard = seeded_client.get("/admin/").get_data(as_text=True)

    assert response.headers["Location"].endswith("/admin/")
    assert "Signed in by PIN" in dashboard
    assert 'id="admin-countdown" data-timeout="120"' in dashboard
    assert audit_entries(seeded_app) == [("LOGIN_OK", "PIN", "Entered admin mode.")]


def test_wrong_pin_counts_attempts(seeded_app, seeded_client):
    """
    Purpose: A wrong PIN is refused with the attempts left and logged.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = enter_admin(seeded_client, "0000")

    assert response.status_code == 401
    assert b"Wrong PIN. 4 attempt(s) left" in response.data and b"(Code ADM-101)" in response.data
    assert seeded_client.get("/admin/").status_code == 302
    assert audit_entries(seeded_app) == [("LOGIN_FAILED", "PIN", "Wrong PIN (attempt 1 of 5).")]


def test_correct_pin_resets_the_count(seeded_app, seeded_client):
    """
    Purpose: After a correct PIN, the next wrong one starts counting at 1.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    for _ in range(3):
        enter_admin(seeded_client, "9999")
    enter_admin(seeded_client)
    seeded_client.post("/admin/exit")

    assert b"4 attempt(s) left" in enter_admin(seeded_client, "9999").data


def test_pin_lockout_timing(seeded_app):
    """
    Purpose: 5 wrong PINs lock PIN entry for exactly 15 minutes: at 14:59
        even the right PIN is refused ("0:01" left), at 15:00 it works.

    Args:
        seeded_app (Flask): Seeded test application.

    Returns:
        None
    """
    t0 = datetime(2026, 10, 29, 9, 0, 0)
    with seeded_app.app_context():
        for attempt in range(1, 6):
            with pytest.raises(admin.AdminAccessError) as error:
                admin.check_pin("0000", now=t0)
        assert "Try again in 15:00" in str(error.value)
        assert SystemState.get().locked_until == t0 + timedelta(minutes=15)

        with pytest.raises(admin.AdminAccessError) as error:
            admin.check_pin(PIN, now=t0 + timedelta(minutes=14, seconds=59))
        assert "Try again in 0:01" in str(error.value)
        assert "(Code ADM-102)" in str(error.value)

        admin.check_pin(PIN, now=t0 + timedelta(minutes=15))  # no error: unlocked
        assert SystemState.get().locked_until is None
        assert SystemState.get().failed_pin_attempts == 0

    assert actions(seeded_app) == ["LOGIN_FAILED"] * 5 + ["PIN_LOCKOUT", "PIN_BLOCKED"]


def test_locked_keypad_shows_remaining_time_but_card_still_works(seeded_app, seeded_client):
    """
    Purpose: During a lockout the keypad shows the time left (status 423),
        and the NFC master card still opens admin mode.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    for _ in range(5):
        enter_admin(seeded_client, "1111")

    locked = enter_admin(seeded_client)  # right PIN, but locked
    page = seeded_client.get("/admin/login").get_data(as_text=True)
    tap = seeded_client.post("/simulation/nfc-tap", data={"uid": "04FFEE01"})

    assert locked.status_code == 423
    assert b"PIN entry is locked" in locked.data
    assert "Try again in <strong>14:5" in page or "Try again in <strong>15:00" in page
    assert 'id="pin" name="pin" inputmode="numeric" autocomplete="off"\n           maxlength="8" aria-label="PIN" disabled' in page
    assert tap.headers["Location"].endswith("/admin/")
    assert audit_entries(seeded_app)[-1] == ("LOGIN_OK", "NFC 04FFEE01", "Entered admin mode.")


def test_service_key_opens_admin_mode(seeded_app, seeded_client):
    """
    Purpose: The simulated service key opens admin mode and is logged.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    response = seeded_client.post("/simulation/service-key")

    assert response.headers["Location"].endswith("/admin/")
    assert audit_entries(seeded_app) == [("LOGIN_OK", "SERVICE_KEY", "Entered admin mode.")]


def test_customer_card_does_not_open_admin_mode(seeded_app, seeded_client):
    """
    Purpose: A normal card logs a customer in, not a technician.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/nfc-tap", data={"uid": "04A1B2C3"})

    assert seeded_client.get("/admin/").status_code == 302
    assert audit_entries(seeded_app) == []


def test_admin_pages_and_actions_need_admin_mode(seeded_app, seeded_client):
    """
    Purpose: Without admin mode every admin page and action redirects to
        the keypad and changes nothing.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")
    grounds = bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS)

    for url, data in (("/admin/", None),
                      (f"/admin/ingredients/{oat.id}/refill", {"quantity": "100"}),
                      (f"/admin/ingredients/{oat.id}/reset", {}),
                      (f"/admin/ingredients/{oat.id}/limits", {"minimum": "1", "capacity": "9000"}),
                      (f"/admin/bins/{grounds.id}/empty", {}),
                      ("/admin/events/1/resolve", {})):
        response = seeded_client.get(url) if data is None else seeded_client.post(url, data=data)
        assert response.headers["Location"].endswith("/admin/login"), url

    assert ingredient(seeded_app, "Oat milk").stock_level == 2500
    assert bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS).level_percent == 40


# ---------------------------------------------------------------------------
# Leaving admin mode
# ---------------------------------------------------------------------------


def test_exit_button(seeded_app, technician):
    """
    Purpose: Exit leaves admin mode and is logged.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    technician.post("/admin/exit")

    assert technician.get("/admin/").status_code == 302
    assert audit_entries(seeded_app)[-1] == ("EXIT", "PIN", "Left admin mode (Exit button).")


def test_admin_mode_ends_after_2_minutes_inactivity(seeded_app, technician):
    """
    Purpose: 119 s idle keeps admin mode; 121 s ends it with a logged
        timeout exit and a message.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    set_admin_idle(technician, 119)
    assert technician.get("/admin/").status_code == 200

    set_admin_idle(technician, 121)
    response = technician.get("/admin/", follow_redirects=True)

    assert response.request.path == "/admin/login"
    assert b"Admin mode ended after 2 minutes of inactivity." in response.data
    assert audit_entries(seeded_app)[-1] == (
        "EXIT", "PIN", "Left admin mode after 2 minutes of inactivity.")


def test_admin_keepalive(technician):
    """
    Purpose: Taps in admin mode keep it open; after the timeout the
        keepalive returns 401.

    Args:
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    set_admin_idle(technician, 100)
    assert technician.post("/admin/keepalive").status_code == 204

    set_admin_idle(technician, 121)
    assert technician.post("/admin/keepalive").status_code == 401


# ---------------------------------------------------------------------------
# Audit log (NFR-31)
# ---------------------------------------------------------------------------


def test_audit_log_cannot_be_changed_or_deleted(seeded_app, technician):
    """
    Purpose: Audit entries cannot be edited or deleted, even in code, and
        admin mode has no route for it.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    with seeded_app.app_context():
        entry = AdminAuditLog.query.first()
        entry.details = "nothing happened"
        with pytest.raises(AuditLogImmutableError):
            db.session.commit()
        db.session.rollback()

        entry = AdminAuditLog.query.first()
        db.session.delete(entry)
        with pytest.raises(AuditLogImmutableError):
            db.session.commit()
        db.session.rollback()

    assert technician.post("/admin/audit/1/delete").status_code == 404
    assert technician.post("/admin/audit/1/edit").status_code == 404
    assert audit_entries(seeded_app)[0] == ("LOGIN_OK", "PIN", "Entered admin mode.")


def test_every_admin_action_is_audited(seeded_app, seeded_client):
    """
    Purpose: Entry, failed PIN, every dashboard action and exit each leave
        an audit entry with time and access method.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")
    grounds = bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS)
    seeded_client.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})
    with seeded_app.app_context():
        fault_id = maintenance.get_active_fault(maintenance.FAULT_GRINDER).id

    enter_admin(seeded_client, "0000")
    enter_admin(seeded_client)
    seeded_client.post(f"/admin/ingredients/{oat.id}/refill", data={"quantity": "100"})
    seeded_client.post(f"/admin/ingredients/{oat.id}/reset")
    seeded_client.post(f"/admin/ingredients/{oat.id}/limits", data={"minimum": "400", "capacity": "3000"})
    seeded_client.post(f"/admin/bins/{grounds.id}/empty")
    seeded_client.post(f"/admin/events/{fault_id}/resolve")
    seeded_client.post("/admin/exit")

    assert actions(seeded_app) == ["LOGIN_FAILED", "LOGIN_OK", "REFILL", "RESET_TO_FULL",
                                   "EDIT_LIMITS", "EMPTY_BIN", "RESOLVE_EVENT", "EXIT"]
    with seeded_app.app_context():
        assert all(e.created_at is not None and e.actor for e in AdminAuditLog.query)
    enter_admin(seeded_client)
    audit_page = seeded_client.get("/admin/?tab=audit")
    assert b"RESOLVE_EVENT" in audit_page.data and b"HW-201 Grinder jammed" in audit_page.data


# ---------------------------------------------------------------------------
# Ingredients
# ---------------------------------------------------------------------------


def test_refill_by_quantity_and_cap(seeded_app, technician):
    """
    Purpose: Refill adds the quantity; above capacity it is capped.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")

    first = technician.post(f"/admin/ingredients/{oat.id}/refill", data={"quantity": "200"},
                            follow_redirects=True)
    technician.post(f"/admin/ingredients/{oat.id}/refill", data={"quantity": "1000"})

    assert b"Refilled Oat milk +200 ml (2500 \xe2\x86\x92 2700)." in first.data
    assert ingredient(seeded_app, "Oat milk").stock_level == 3000
    assert audit_entries(seeded_app)[-1] == (
        "REFILL", "PIN", "Oat milk +300 ml (2700 → 3000) (capped at capacity).")


@pytest.mark.parametrize("quantity, message", [
    ("0", b"must be more than 0"), ("-5", b"must be more than 0"),
    ("abc", b"as a number"), ("", b"as a number"),
])
def test_refill_rejects_invalid_quantity(seeded_app, technician, quantity, message):
    """
    Purpose: Zero, negative or non-numeric refills are refused and not logged.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.
        quantity (str): Value typed.
        message (bytes): Expected error.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")

    response = technician.post(f"/admin/ingredients/{oat.id}/refill", data={"quantity": quantity},
                               follow_redirects=True)

    assert message in response.data
    assert ingredient(seeded_app, "Oat milk").stock_level == 2500
    assert "REFILL" not in actions(seeded_app)


def test_refill_resolves_low_ingredient_event(seeded_app, technician):
    """
    Purpose: Refilling a low ingredient above its minimum resolves its
        Low Ingredient Threshold event.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")
    technician.post("/simulation/stock", data={"ingredient_id": oat.id, "level": "400"})
    assert open_events(seeded_app, ErrorEvent.CODE_LOW_INGREDIENT) == 1

    technician.post(f"/admin/ingredients/{oat.id}/refill", data={"quantity": "500"})

    assert open_events(seeded_app, ErrorEvent.CODE_LOW_INGREDIENT) == 0


def test_reset_to_full(seeded_app, technician):
    """
    Purpose: Reset sets the counter to capacity and is logged.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    almond = ingredient(seeded_app, "Almond milk")

    technician.post(f"/admin/ingredients/{almond.id}/reset")

    assert ingredient(seeded_app, "Almond milk").stock_level == 3000
    assert audit_entries(seeded_app)[-1] == (
        "RESET_TO_FULL", "PIN", "Almond milk reset to full (300 → 3000 ml).")


def test_edit_limits(seeded_app, technician):
    """
    Purpose: Minimum and capacity can be changed; raising the minimum above
        the stock opens a low-ingredient event.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")

    technician.post(f"/admin/ingredients/{oat.id}/limits", data={"minimum": "2600", "capacity": "3500"})

    updated = ingredient(seeded_app, "Oat milk")
    assert (updated.min_threshold, updated.capacity) == (2600, 3500)
    assert open_events(seeded_app, ErrorEvent.CODE_LOW_INGREDIENT) == 1
    assert audit_entries(seeded_app)[-1] == (
        "EDIT_LIMITS", "PIN", "Oat milk: minimum 500 → 2600, capacity 3000 → 3500 ml.")


@pytest.mark.parametrize("minimum, capacity, message", [
    ("4000", "3000", b"minimum must be between 0 and the capacity"),
    ("-1", "3000", b"minimum must be between 0 and the capacity"),
    ("100", "2000", b"cannot be below the current stock (2500 ml)"),
    ("100", "0", b"capacity must be more than 0"),
    ("x", "3000", b"minimum as a number"),
])
def test_edit_limits_rejects_invalid_values(seeded_app, technician, minimum, capacity, message):
    """
    Purpose: Impossible limits are refused and nothing changes.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.
        minimum (str): Minimum typed.
        capacity (str): Capacity typed.
        message (bytes): Expected error.

    Returns:
        None
    """
    oat = ingredient(seeded_app, "Oat milk")

    response = technician.post(f"/admin/ingredients/{oat.id}/limits",
                               data={"minimum": minimum, "capacity": capacity}, follow_redirects=True)

    assert message in response.data
    unchanged = ingredient(seeded_app, "Oat milk")
    assert (unchanged.min_threshold, unchanged.capacity) == (500, 3000)


# ---------------------------------------------------------------------------
# Waste bins
# ---------------------------------------------------------------------------


def test_each_drink_adds_documented_waste(seeded_app, seeded_client):
    """
    Purpose: An Espresso (9 g beans) adds 18 g grounds (1.2 % of 1500 g)
        and 30 ml liquid (1.5 % of 2000 ml).

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    pay(seeded_client, order_id_from(new_order(seeded_client, seeded_app)))

    assert maintenance.WASTE_GROUNDS_PER_GRAM_BEANS == 2.0
    assert maintenance.WASTE_LIQUID_PER_DRINK_ML == 30
    assert bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS).level_percent == pytest.approx(41.2)
    assert bin_by_kind(seeded_app, WasteBin.KIND_LIQUID).level_percent == pytest.approx(21.5)


def test_alert_event_at_80_percent(seeded_app, seeded_client):
    """
    Purpose: Crossing 80 % raises one "almost full" alert; drinks still work.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_bin(seeded_app, WasteBin.KIND_GROUNDS, 79.5)

    pay(seeded_client, order_id_from(new_order(seeded_client, seeded_app)))
    pay(seeded_client, order_id_from(new_order(seeded_client, seeded_app)))

    assert open_events(seeded_app, maintenance.CODE_BIN_ALERT) == 1
    assert open_events(seeded_app, maintenance.CODE_BIN_FULL) == 0


def test_bin_at_95_percent_stops_new_drinks(seeded_app, seeded_client):
    """
    Purpose: At 95 % customers see the out-of-service message, ordering and
        paying are refused without charging, and admin mode still works.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    pending = order_id_from(new_order(seeded_client, seeded_app))
    grounds = bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS)
    seeded_client.post("/simulation/bins", data={"bin_id": grounds.id, "level": "95"})

    menu = seeded_client.get("/menu").get_data(as_text=True)
    refused = new_order(seeded_client, seeded_app)
    paid = pay(seeded_client, pending, follow_redirects=True)

    assert "Out of service: the coffee grounds bin is full" in menu
    assert "(Code WST-302)" in menu
    assert 'class="drink-name"' not in menu
    assert refused.headers["Location"].startswith("/menu")
    assert b"Out of service" in paid.data
    with seeded_app.app_context():
        assert db.session.get(Transaction, pending).status == Transaction.STATUS_PENDING
        assert ProcessorCharge.query.count() == 0
    assert open_events(seeded_app, maintenance.CODE_BIN_FULL) == 1
    assert enter_admin(seeded_client).status_code == 302
    assert seeded_client.get("/admin/?tab=bins").status_code == 200


def test_drink_in_progress_finishes_then_machine_stops(seeded_app, seeded_client):
    """
    Purpose: A drink that pushes a bin past 95 % still finishes; the next
        one is refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_bin(seeded_app, WasteBin.KIND_GROUNDS, 94.5)

    first = order_id_from(new_order(seeded_client, seeded_app))
    done = pay(seeded_client, first, follow_redirects=True)

    assert b"Enjoy your Espresso!" in done.data
    assert bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS).level_percent == pytest.approx(95.7)
    assert new_order(seeded_client, seeded_app).headers["Location"].startswith("/menu")


def test_overfill_is_checked_before_starting(seeded_app, seeded_client):
    """
    Purpose: With a 300 g bin at 94 %, a Doppio (36 g grounds = 12 %) would
        overfill it and is refused at confirmation; an Espresso (6 %) fits.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_bin(seeded_app, WasteBin.KIND_GROUNDS, 94, capacity=300)

    doppio = new_order(seeded_client, seeded_app, "Doppio")
    espresso = new_order(seeded_client, seeded_app, "Espresso")

    assert doppio.status_code == 400
    assert b"This drink would overfill the coffee grounds bin (94% full)" in doppio.data
    assert b"(Code WST-303)" in doppio.data
    assert espresso.status_code == 302


def test_overfill_found_at_dispense_is_refunded(seeded_app, seeded_client):
    """
    Purpose: If the bin fills between confirming and dispensing, the
        re-check refuses the drink and the payment is refunded.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    set_bin(seeded_app, WasteBin.KIND_GROUNDS, 80, capacity=300)
    order_id = order_id_from(new_order(seeded_client, seeded_app, "Doppio"))
    set_bin(seeded_app, WasteBin.KIND_GROUNDS, 90)  # 90 + 12 > 100

    response = pay(seeded_client, order_id, follow_redirects=True)

    with seeded_app.app_context():
        assert db.session.get(Transaction, order_id).status == Transaction.STATUS_CANCELLED
        assert ProcessorCharge.query.one().status == ProcessorCharge.STATUS_REFUNDED
    assert b"would overfill" in response.data and b"has been refunded" in response.data


def test_empty_bin_restarts_service(seeded_app, technician):
    """
    Purpose: Emptying a full bin sets it to 0 %, resolves its events, is
        logged and lets customers order again.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    grounds = bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS)
    technician.post("/simulation/bins", data={"bin_id": grounds.id, "preset": "full"})
    assert open_events(seeded_app, maintenance.CODE_BIN_FULL) == 1

    response = technician.post(f"/admin/bins/{grounds.id}/empty", follow_redirects=True)

    assert bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS).level_percent == 0
    assert open_events(seeded_app, maintenance.CODE_BIN_FULL) == 0
    assert open_events(seeded_app, maintenance.CODE_BIN_ALERT) == 0
    assert b"Coffee grounds bin emptied (100% \xe2\x86\x92 0%)." in response.data
    assert audit_entries(seeded_app)[-1][0] == "EMPTY_BIN"
    assert new_order(technician, seeded_app).status_code == 302


@pytest.mark.parametrize("level, message", [
    ("101", b"between 0 and 100%"), ("-1", b"between 0 and 100%"), ("full-ish", b"as a number"),
])
def test_simulation_rejects_invalid_bin_level(seeded_app, seeded_client, level, message):
    """
    Purpose: Invalid bin levels on the Simulation panel are refused.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.
        level (str): Value typed.
        message (bytes): Expected error.

    Returns:
        None
    """
    grounds = bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS)

    response = seeded_client.post("/simulation/bins", data={"bin_id": grounds.id, "level": level},
                                  follow_redirects=True)

    assert message in response.data
    assert bin_by_kind(seeded_app, WasteBin.KIND_GROUNDS).level_percent == 40


# ---------------------------------------------------------------------------
# Faults and the Errors tab
# ---------------------------------------------------------------------------


def available(app, name):
    """
    Purpose: Checks whether a drink can be ordered right now.

    Args:
        app (Flask): The test application.
        name (str): Drink name.

    Returns:
        bool: True if available.
    """
    with app.app_context():
        return inventory.is_drink_available(Drink.query.filter_by(name=name).one())


def test_grinder_jammed_blocks_bean_drinks(seeded_app, seeded_client):
    """
    Purpose: Grinder Jammed blocks every drink with coffee beans, not tea or
        chocolate, and recommendations skip them.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})

    blocked = new_order(seeded_client, seeded_app, "Espresso")
    message = seeded_client.get(blocked.headers["Location"]).get_data(as_text=True)

    assert not available(seeded_app, "Espresso") and not available(seeded_app, "Cold Brew")
    assert available(seeded_app, "Hot Chocolate") and available(seeded_app, "Green Tea")
    assert "Espresso is not available right now (grinder jammed)." in message
    with seeded_app.app_context():
        names = [r.drink.name for r in recommendations.get_menu_recommendations(None).ranked]
    assert sorted(names) == ["Chai Latte", "Green Tea", "Hot Chocolate"]


def test_water_heater_fault_blocks_hot_drinks(seeded_app, seeded_client):
    """
    Purpose: Water Heater Fault blocks hot drinks; iced drinks still work.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/faults", data={"fault": maintenance.FAULT_HEATER})

    assert not available(seeded_app, "Latte") and not available(seeded_app, "Green Tea")
    assert available(seeded_app, "Iced Latte") and available(seeded_app, "Cold Brew")


def test_fault_stays_until_resolved_in_admin(seeded_app, seeded_client):
    """
    Purpose: Triggering an active fault again does nothing; only Resolve in
        admin mode clears it and unblocks the drinks.

    Args:
        seeded_app (Flask): Seeded test application.
        seeded_client (FlaskClient): Client for it.

    Returns:
        None
    """
    seeded_client.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})
    again = seeded_client.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER},
                               follow_redirects=True)
    unknown = seeded_client.post("/simulation/faults", data={"fault": "COFFEE_EXPLODED"},
                                 follow_redirects=True)
    assert b"That fault is already active." in again.data
    assert b"Unknown fault." in unknown.data
    assert open_events(seeded_app, maintenance.FAULT_GRINDER) == 1

    enter_admin(seeded_client)
    with seeded_app.app_context():
        fault_id = maintenance.get_active_fault(maintenance.FAULT_GRINDER).id
    seeded_client.post(f"/admin/events/{fault_id}/resolve")

    assert available(seeded_app, "Espresso")
    assert seeded_client.post(f"/admin/events/{fault_id}/resolve", follow_redirects=True).data.count(
        b"That event is not active.") == 1


def test_errors_tab_shows_code_description_and_next_action(seeded_app, technician):
    """
    Purpose: Active errors show code, title, description and next action,
        newest first.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    technician.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})
    technician.post("/simulation/faults", data={"fault": maintenance.FAULT_HEATER})

    html = technician.get("/admin/?tab=errors").get_data(as_text=True)

    assert html.index("HW-202") < html.index("HW-201")  # newest first
    assert "Water Heater Fault: hot drinks are blocked." in html
    assert "Next: Open the grinder, remove the blockage, then press Resolve." in html
    assert html.count(">Resolve</button>") == 2


# ---------------------------------------------------------------------------
# Event log and remote diagnostics
# ---------------------------------------------------------------------------


def test_event_log_filter_by_type(seeded_app, technician):
    """
    Purpose: The event log can be filtered by type.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    technician.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})
    technician.post("/simulation/connectivity", data={"wallet_down": "on", "drop_mode": "off"})

    hardware = technician.get("/admin/?tab=events&kind=hardware").get_data(as_text=True)
    connectivity = technician.get("/admin/?tab=events&kind=connectivity").get_data(as_text=True)
    everything = technician.get("/admin/?tab=events").get_data(as_text=True)

    assert "HW-201" in hardware and "NET-403" not in hardware
    assert "NET-403" in connectivity and "HW-201" not in connectivity
    assert "HW-201" in everything and "NET-403" in everything


def test_events_sent_to_remote_diagnostics_when_online(seeded_app, technician):
    """
    Purpose: Online, new events are marked as sent to remote diagnostics;
        offline they stay queued and are sent when the network is back.

    Args:
        seeded_app (Flask): Seeded test application.
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    technician.post("/simulation/faults", data={"fault": maintenance.FAULT_GRINDER})
    technician.get("/admin/?tab=events")
    with seeded_app.app_context():
        assert maintenance.get_active_fault(maintenance.FAULT_GRINDER).reported_at is not None

    technician.post("/simulation/connectivity", data={"network_down": "on", "drop_mode": "off"})
    technician.post("/simulation/faults", data={"fault": maintenance.FAULT_HEATER})
    offline_log = technician.get("/admin/?tab=events").get_data(as_text=True)
    with seeded_app.app_context():
        assert maintenance.get_active_fault(maintenance.FAULT_HEATER).reported_at is None
    assert "Queued (offline)" in offline_log

    technician.post("/simulation/connectivity", data={"drop_mode": "off"})
    with seeded_app.app_context():
        assert ErrorEvent.query.filter_by(reported_at=None).count() == 0


def test_simulation_panel_has_feature10_sections(seeded_client):
    """
    Purpose: The panel has admin access, faults, waste bins and outbox.

    Args:
        seeded_client (FlaskClient): Client for the seeded app.

    Returns:
        None
    """
    html = seeded_client.get("/simulation/").get_data(as_text=True)

    for text in ('id="admin-access"', "Turn service key", 'id="faults"', "Grinder Jammed",
                 "Water Heater Fault", 'id="bins"', "Coffee grounds", "Liquid waste", 'id="outbox"'):
        assert text in html


def test_admin_dashboard_tabs_render(technician):
    """
    Purpose: Every dashboard tab renders, and an unknown tab falls back to
        Ingredients.

    Args:
        technician (FlaskClient): Client in admin mode.

    Returns:
        None
    """
    for tab, text in (("ingredients", b"Reset to full"), ("bins", b"Empty bin"),
                      ("errors", b"No active errors"), ("events", b"Remote diagnostics"),
                      ("audit", b"LOGIN_OK"), ("nonsense", b"Reset to full")):
        response = technician.get(f"/admin/?tab={tab}")
        assert response.status_code == 200 and text in response.data, tab
