"""Admin mode pages (feature 10: FR-43–52): PIN keypad, dashboard with
ingredients, waste bins, errors, event log and audit log."""

from functools import wraps

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from app.models import WasteBin
from app.services import admin, inventory, maintenance

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

TABS = ("ingredients", "bins", "errors", "events", "audit")


@admin_bp.before_app_request
def report_events_and_check_admin_timeout():
    """
    Purpose: Before every request, sends queued events to remote
        diagnostics when online, and ends an admin session that has been
        inactive for more than 2 minutes (logged as a timeout exit).

    Args:
        None

    Returns:
        Response | None: 401 JSON for an expired admin keepalive, else None.
    """
    if request.endpoint == "static":
        return None
    maintenance.report_pending_events()

    g.admin_actor = None
    if admin.is_admin_expired():
        admin.exit_admin(reason="timeout")
        if request.endpoint == "admin.keepalive":
            return jsonify(admin=False), 401
        if (request.endpoint or "").startswith("admin."):
            flash("Admin mode ended after 2 minutes of inactivity.", "warning")
        return None

    g.admin_actor = admin.get_admin_actor()
    if g.admin_actor and (request.endpoint or "").startswith("admin."):
        admin.touch_admin()  # only admin screens count as admin activity
    return None


@admin_bp.app_context_processor
def inject_admin_info():
    """
    Purpose: Makes the admin state and timeout available in templates.

    Args:
        None

    Returns:
        dict: Template variables admin_actor, admin_timeout and
            out_of_service (message when a waste bin is full, else None).
    """
    return {
        "admin_actor": g.get("admin_actor"),
        "admin_timeout": admin.admin_timeout_seconds(),
        "out_of_service": maintenance.get_out_of_service_reason(),
    }


def admin_required(view):
    """
    Purpose: Decorator that sends anyone not in admin mode to the PIN screen.

    Args:
        view (callable): The admin view to protect.

    Returns:
        callable: The wrapped view.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        """
        Purpose: Runs the view only in admin mode.

        Args:
            *args: Positional URL arguments for the view.
            **kwargs: Keyword URL arguments for the view.

        Returns:
            Response: The view's response, or a redirect to the PIN screen.
        """
        if g.admin_actor is None:
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


def back_to(tab, message, category="success"):
    """
    Purpose: Shows a message and returns to a dashboard tab.

    Args:
        tab (str): One of TABS.
        message (str): Message to flash.
        category (str): Flash category.

    Returns:
        Response: Redirect to the dashboard tab.
    """
    flash(message, category)
    return redirect(url_for("admin.dashboard", tab=tab))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Purpose: Technician PIN keypad (NFR-12: 5 wrong tries lock PIN entry for
        15 minutes; the time left is shown).

    Args:
        None

    Returns:
        Response: The keypad (with an error, status 401 or 423 when locked),
            or a redirect to the dashboard.
    """
    if g.admin_actor is not None:
        return redirect(url_for("admin.dashboard"))
    error = None
    status = 200
    if request.method == "POST":
        try:
            admin.enter_with_pin(request.form.get("pin"))
        except admin.AdminAccessError as exc:
            error = str(exc)
            status = 423 if admin.get_lockout_remaining() else 401
        else:
            return redirect(url_for("admin.dashboard"))

    remaining = admin.get_lockout_remaining()
    return render_template(
        "admin_login.html",
        error=error,
        locked_for=admin.format_remaining(remaining) if remaining else None,
    ), status


@admin_bp.route("/exit", methods=["POST"])
def exit_admin():
    """
    Purpose: Leaves admin mode (Exit button, or the countdown reaching 0).

    Args:
        None

    Returns:
        Response: Redirect to the home screen.
    """
    reason = "timeout" if request.form.get("reason") == "timeout" else "manual"
    if admin.exit_admin(reason):
        flash("You have left admin mode.", "success")
    return redirect(url_for("customer.home"))


@admin_bp.route("/keepalive", methods=["POST"])
def keepalive():
    """
    Purpose: Keeps admin mode open while the technician taps the screen.

    Args:
        None

    Returns:
        Response: 204 while in admin mode, 401 JSON otherwise.
    """
    if g.admin_actor is None:
        return jsonify(admin=False), 401
    return "", 204


@admin_bp.route("/")
@admin_required
def dashboard():
    """
    Purpose: Admin dashboard with tabs for ingredients, waste bins, errors,
        the event log (filterable by type) and the audit log (read only).

    Args:
        None

    Returns:
        str: The rendered dashboard.
    """
    tab = request.args.get("tab", "ingredients")
    if tab not in TABS:
        tab = "ingredients"
    kind = request.args.get("kind") or None
    if kind not in maintenance.EVENT_KINDS:
        kind = None
    return render_template(
        "admin.html",
        tab=tab,
        ingredients=inventory.get_all_ingredients(),
        is_low=inventory.is_below_threshold,
        bins=maintenance.get_bins(),
        alert_level=WasteBin.ALERT_LEVEL,
        halt_level=WasteBin.HALT_LEVEL,
        active_errors=maintenance.get_active_errors(),
        events=maintenance.get_event_log(kind) if tab == "events" else [],
        kinds=maintenance.EVENT_KINDS,
        kind=kind,
        describe=maintenance.describe_event,
        audit_log=admin.get_audit_log() if tab == "audit" else [],
        out_of_service=maintenance.get_out_of_service_reason(),
    )


@admin_bp.route("/ingredients/<int:ingredient_id>/refill", methods=["POST"])
@admin_required
def refill(ingredient_id):
    """
    Purpose: Refills an ingredient by a quantity, capped at capacity.

    Args:
        ingredient_id (int): Ingredient ID from the URL.

    Returns:
        Response: Redirect to the Ingredients tab with a message.
    """
    try:
        message = admin.refill(g.admin_actor, ingredient_id, request.form.get("quantity"))
    except inventory.StockLevelError as error:
        return back_to("ingredients", str(error), "error")
    return back_to("ingredients", message)


@admin_bp.route("/ingredients/<int:ingredient_id>/reset", methods=["POST"])
@admin_required
def reset(ingredient_id):
    """
    Purpose: Resets an ingredient counter to full capacity.

    Args:
        ingredient_id (int): Ingredient ID from the URL.

    Returns:
        Response: Redirect to the Ingredients tab with a message.
    """
    try:
        message = admin.reset_to_full(g.admin_actor, ingredient_id)
    except inventory.StockLevelError as error:
        return back_to("ingredients", str(error), "error")
    return back_to("ingredients", message)


@admin_bp.route("/ingredients/<int:ingredient_id>/limits", methods=["POST"])
@admin_required
def limits(ingredient_id):
    """
    Purpose: Changes an ingredient's minimum and capacity.

    Args:
        ingredient_id (int): Ingredient ID from the URL.

    Returns:
        Response: Redirect to the Ingredients tab with a message.
    """
    try:
        message = admin.edit_limits(g.admin_actor, ingredient_id,
                                    request.form.get("minimum"), request.form.get("capacity"))
    except inventory.StockLevelError as error:
        return back_to("ingredients", str(error), "error")
    return back_to("ingredients", message)


@admin_bp.route("/bins/<int:bin_id>/empty", methods=["POST"])
@admin_required
def empty_bin(bin_id):
    """
    Purpose: Empties a waste bin.

    Args:
        bin_id (int): Waste bin ID from the URL.

    Returns:
        Response: Redirect to the Waste bins tab with a message.
    """
    try:
        message = admin.empty_bin(g.admin_actor, bin_id)
    except maintenance.MaintenanceError as error:
        return back_to("bins", str(error), "error")
    return back_to("bins", message)


@admin_bp.route("/events/<int:event_id>/resolve", methods=["POST"])
@admin_required
def resolve_event(event_id):
    """
    Purpose: Resolves an active error event (e.g. a fixed fault).

    Args:
        event_id (int): Event ID from the URL.

    Returns:
        Response: Redirect to the Errors tab with a message.
    """
    try:
        message = admin.resolve(g.admin_actor, event_id)
    except maintenance.MaintenanceError as error:
        return back_to("errors", str(error), "error")
    return back_to("errors", message)
